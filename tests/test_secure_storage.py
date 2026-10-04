"""Security boundaries: file access, encryption, durability, and call replay safety."""
from __future__ import annotations

import base64
import json
import os
import stat
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

import calls
import db
import secure_storage as storage


@pytest.fixture(autouse=True)
def isolate_storage_environment(monkeypatch, tmp_path):
    monkeypatch.delenv("LASTLY_DATA_KEY", raising=False)
    monkeypatch.delenv("LASTLY_ALLOW_INSECURE_DB", raising=False)
    monkeypatch.delenv("LASTLY_CALL_DAILY_LIMIT", raising=False)
    monkeypatch.setenv("LASTLY_DATA_DIR", str(tmp_path))


def encryption_key(monkeypatch, key=b"x" * 32):
    monkeypatch.setenv("LASTLY_DATA_KEY", base64.urlsafe_b64encode(key).decode("ascii"))


def test_atomic_write_restricts_existing_directory_and_file_permissions(tmp_path):
    directory = tmp_path / "private"
    directory.mkdir(mode=0o755)
    path = directory / "estate.json"
    path.write_text("old private information")
    path.chmod(0o644)
    storage.write_json(path, {"name": "Private Person"})
    assert storage.read_json(path) == {"name": "Private Person"}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert list(directory.glob(".*.tmp")) == []


def test_new_nested_directories_and_lock_are_private(tmp_path):
    path = tmp_path / "first" / "second" / ".lock"
    with storage.private_file_lock(path):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.parent.parent.stat().st_mode) == 0o700


def test_symlink_file_cannot_be_read_or_overwritten(tmp_path):
    target = tmp_path / "outside.txt"
    target.write_text("keep this secret")
    link = tmp_path / "estate.json"
    link.symlink_to(target)
    with pytest.raises(storage.StorageError):
        storage.read_text(link)
    with pytest.raises(storage.StorageError):
        storage.write_json(link, {"overwrite": True})
    with pytest.raises(storage.StorageError):
        with storage.private_file_lock(link):
            pytest.fail("A symlink was opened as a lock.")
    assert target.read_text() == "keep this secret"


def test_symlink_directory_cannot_redirect_reads_or_writes(tmp_path):
    target = tmp_path / "outside"
    target.mkdir()
    (target / "estate.json").write_text("{}")
    link = tmp_path / "private"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(storage.StorageError):
        storage.read_json(link / "estate.json")
    with pytest.raises(storage.StorageError):
        storage.write_json(link / "new.json", {})
    assert not (target / "new.json").exists()


def test_hard_links_are_rejected_without_changing_the_target(tmp_path):
    target = tmp_path / "outside.txt"
    target.write_text("unchanged")
    path = tmp_path / "estate.json"
    os.link(target, path)
    with pytest.raises(storage.StorageError):
        storage.write_json(path, {})
    with pytest.raises(storage.StorageError):
        storage.read_text(path)
    assert target.read_text() == "unchanged"


def test_fifo_is_rejected_without_a_blocking_read(tmp_path):
    path = tmp_path / "estate.json"
    os.mkfifo(path, mode=0o600)
    with pytest.raises(storage.StorageError):
        storage.read_json(path)


def test_no_overwrite_is_atomic_between_competing_writers(tmp_path):
    path = tmp_path / "inbox.json"

    def writer(number):
        try:
            storage.write_json(path, {"writer": number}, overwrite=False)
            return True
        except FileExistsError:
            return False

    with ThreadPoolExecutor(max_workers=8) as executor:
        successes = list(executor.map(writer, range(20)))
    assert successes.count(True) == 1
    assert storage.read_json(path)["writer"] in range(20)
    assert list(tmp_path.glob(".*.tmp")) == []


def test_failed_atomic_publish_preserves_old_contents(monkeypatch, tmp_path):
    path = tmp_path / "estate.json"
    storage.write_json(path, {"version": 1})

    def failed_replace(*args, **kwargs):
        raise OSError("Simulated disk failure.")

    monkeypatch.setattr(storage.os, "replace", failed_replace)
    with pytest.raises(OSError):
        storage.write_json(path, {"version": 2})
    assert storage.read_json(path) == {"version": 1}
    assert list(tmp_path.glob(".*.tmp")) == []


def test_encrypted_json_and_csv_are_transparent_and_use_fresh_nonces(monkeypatch, tmp_path):
    encryption_key(monkeypatch)
    path = tmp_path / "inbox.json"
    content = {"name": "Private Person", "email": "private@example.com"}
    storage.write_json(path, content)
    first = path.read_bytes()
    assert b"Private Person" not in first and b"private@example.com" not in first
    assert storage.is_encrypted(path)
    assert storage.read_json(path) == content
    storage.write_json(path, content)
    assert first != path.read_bytes()
    bank = tmp_path / "bank.csv"
    storage.write_text(bank, "date,description,amount\n2026-10-03,Sensitive Company,50\n")
    assert b"Sensitive Company" not in bank.read_bytes()
    assert "Sensitive Company" in storage.read_text(bank)


def test_encrypted_file_fails_closed_when_key_missing_or_wrong(monkeypatch, tmp_path):
    encryption_key(monkeypatch)
    path = tmp_path / "inbox.json"
    storage.write_json(path, {"name": "Private Person"})
    monkeypatch.delenv("LASTLY_DATA_KEY")
    with pytest.raises(storage.StorageError, match="required"):
        storage.read_json(path)
    encryption_key(monkeypatch, b"y" * 32)
    with pytest.raises(storage.StorageError, match="authenticated"):
        storage.read_json(path)


def test_ciphertext_tampering_and_filename_substitution_are_rejected(monkeypatch, tmp_path):
    encryption_key(monkeypatch)
    path = tmp_path / "inbox.json"
    storage.write_json(path, {"name": "Private Person"})
    other = tmp_path / "estate.json"
    other.write_bytes(path.read_bytes())
    with pytest.raises(storage.StorageError, match="authenticated"):
        storage.read_json(other)
    envelope = json.loads(path.read_text())
    ciphertext = bytearray(base64.urlsafe_b64decode(envelope["ciphertext"]))
    ciphertext[0] ^= 1
    envelope["ciphertext"] = base64.urlsafe_b64encode(ciphertext).decode("ascii")
    path.write_text(json.dumps(envelope))
    with pytest.raises(storage.StorageError, match="authenticated"):
        storage.read_json(path)


@pytest.mark.parametrize("key", ["secret", "A" * 44, "!" * 43, "A" * 42, "A" * 45])
def test_invalid_key_does_not_publish_a_plaintext_file(monkeypatch, tmp_path, key):
    monkeypatch.setenv("LASTLY_DATA_KEY", key)
    with pytest.raises(storage.StorageError):
        storage.write_json(tmp_path / "inbox.json", {"name": "Private Person"})
    assert not (tmp_path / "inbox.json").exists()


def test_plaintext_can_be_read_and_explicitly_migrated(monkeypatch, tmp_path):
    path = tmp_path / "inbox.json"
    path.write_text(json.dumps({"synthetic": True}))
    encryption_key(monkeypatch)
    assert storage.read_json(path) == {"synthetic": True}
    assert not storage.is_encrypted(path)
    storage.write_json(path, storage.read_json(path))
    assert storage.is_encrypted(path)


@pytest.mark.parametrize("field,value", [("__lastly_encrypted__", True), ("__lastly_encrypted__", 2), ("algorithm", "AES-128-GCM"), ("nonce", None), ("ciphertext", "not base64!")])
def test_malformed_encryption_envelope_fails_closed(monkeypatch, tmp_path, field, value):
    encryption_key(monkeypatch)
    path = tmp_path / "inbox.json"
    storage.write_json(path, {"name": "Private Person"})
    envelope = json.loads(path.read_text())
    envelope[field] = value
    path.write_text(json.dumps(envelope))
    with pytest.raises(storage.StorageError):
        storage.read_json(path)


def test_bounded_reads_reject_oversized_plaintext_and_decrypted_data(monkeypatch, tmp_path):
    path = tmp_path / "inbox.json"
    storage.write_text(path, "x" * 100)
    with pytest.raises(storage.StorageError, match="size"):
        storage.read_text(path, max_bytes=20)
    encryption_key(monkeypatch)
    storage.write_text(path, "x" * 100)
    with pytest.raises(storage.StorageError, match="size"):
        storage.read_text(path, max_bytes=20)


def test_family_store_round_trip_is_encrypted(monkeypatch, tmp_path):
    encryption_key(monkeypatch)
    monkeypatch.setattr(db, "_data_dir", lambda: tmp_path)
    monkeypatch.setattr(db, "_settings", lambda: SimpleNamespace(database_url=""))
    estate = {"persona": {"name": "Private Person", "email": "private@example.com"}, "accounts": [], "today": "2026-10-03"}
    estate_id = db.save_estate(estate)
    assert db.load_estate(estate_id)["persona"] == estate["persona"]
    for name in ("estate.json", "family_store.json"):
        assert storage.is_encrypted(tmp_path / name)
        assert b"Private Person" not in (tmp_path / name).read_bytes()


def test_durable_call_reservation_serializes_retries_and_binds_payload(tmp_path):
    key, fingerprint = str(uuid4()), "a" * 64
    with ThreadPoolExecutor(max_workers=8) as executor:
        responses = list(executor.map(lambda _: calls.reserve_call(key, fingerprint), range(20)))
    assert sum(response["state"] == "new" for response in responses) == 1
    assert sum(response["state"] == "pending" for response in responses) == 19
    with pytest.raises(ValueError, match="different"):
        calls.reserve_call(key, "b" * 64)
    calls.fail_call(key)
    assert calls.reserve_call(key, fingerprint) == {"state": "pending"}
    # No in-memory reservation state is needed: this receipt survives reloads.
    calls.complete_call(key, {"success": True, "conversation_id": "conv_123"})
    receipt = calls.reserve_call(key, fingerprint)
    assert receipt == {"state": "complete", "response": {"success": True, "conversation_id": "conv_123"}}
    receipt["response"]["success"] = False
    assert calls.reserve_call(key, fingerprint)["response"]["success"] is True
    with pytest.raises(ValueError, match="cannot be changed"):
        calls.complete_call(key, {"success": False})


def test_proven_no_call_can_retry_same_payload_only():
    key, fingerprint = str(uuid4()), "a" * 64
    assert calls.reserve_call(key, fingerprint)["state"] == "new"
    calls.fail_call(key, safe_no_call=True)
    with pytest.raises(ValueError, match="different"):
        calls.reserve_call(key, "b" * 64)
    assert calls.reserve_call(key, fingerprint)["state"] == "new"


def test_call_reservation_limit_never_evicts_uncertain_calls(monkeypatch):
    monkeypatch.setattr(calls, "_MAX_RESERVATIONS", 1)
    key = str(uuid4())
    calls.reserve_call(key, "a" * 64)
    calls.fail_call(key)
    with pytest.raises(calls.IntegrationError, match="limit"):
        calls.reserve_call(str(uuid4()), "b" * 64)
    assert calls.reserve_call(key, "a" * 64)["state"] == "pending"


def test_new_key_cannot_replay_an_equivalent_uncertain_call():
    key = str(uuid4())
    calls.reserve_call(key, "a" * 64)
    with pytest.raises(ValueError, match="pending or uncertain"):
        calls.reserve_call(str(uuid4()), "a" * 64)
    calls.fail_call(key)
    with pytest.raises(ValueError, match="pending or uncertain"):
        calls.reserve_call(str(uuid4()), "a" * 64)


def test_new_key_cannot_immediately_repeat_a_completed_call(tmp_path):
    key = str(uuid4())
    calls.reserve_call(key, "a" * 64)
    calls.complete_call(key, {"success": True, "conversation_id": "conv_123"})
    with pytest.raises(ValueError, match="just completed"):
        calls.reserve_call(str(uuid4()), "a" * 64)
    path = tmp_path / "runtime_calls.json"
    records = storage.read_json(path)
    records[calls._RESERVATIONS][key]["updated_at"] = (datetime.now(UTC) - timedelta(seconds=121)).isoformat()
    storage.write_json(path, records)
    assert calls.reserve_call(str(uuid4()), "a" * 64)["state"] == "new"


def test_daily_call_budget_is_durable_and_counts_uncertain_outcomes(monkeypatch, tmp_path):
    monkeypatch.setenv("LASTLY_CALL_DAILY_LIMIT", "2")
    first, second = str(uuid4()), str(uuid4())
    calls.reserve_call(first, "a" * 64)
    calls.fail_call(first)
    calls.reserve_call(second, "b" * 64)
    with pytest.raises(calls.CallLimitError, match="today"):
        calls.reserve_call(str(uuid4()), "c" * 64)
    assert calls.reserve_call(first, "a" * 64)["state"] == "pending"
    calls.fail_call(second, safe_no_call=True)
    assert calls.reserve_call(str(uuid4()), "c" * 64)["state"] == "new"
    assert len(storage.read_json(tmp_path / "runtime_calls.json")[calls._RESERVATIONS]) == 3


def test_daily_budget_resets_by_utc_date_without_releasing_uncertainty(monkeypatch, tmp_path):
    monkeypatch.setenv("LASTLY_CALL_DAILY_LIMIT", "1")
    key = str(uuid4())
    calls.reserve_call(key, "a" * 64)
    path = tmp_path / "runtime_calls.json"
    records = storage.read_json(path)
    records[calls._RESERVATIONS][key]["created_at"] = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    storage.write_json(path, records)
    assert calls.reserve_call(str(uuid4()), "b" * 64)["state"] == "new"
    with pytest.raises(ValueError, match="pending or uncertain"):
        calls.reserve_call(str(uuid4()), "a" * 64)


@pytest.mark.parametrize("limit", ["0", "101", "infinite"])
def test_invalid_daily_call_budget_fails_before_a_reservation(monkeypatch, tmp_path, limit):
    monkeypatch.setenv("LASTLY_CALL_DAILY_LIMIT", limit)
    with pytest.raises(ValueError, match="1 through 100"):
        calls.reserve_call(str(uuid4()), "a" * 64)
    assert not (tmp_path / "runtime_calls.json").exists()


def test_call_reservation_metadata_uses_authenticated_encryption(monkeypatch, tmp_path):
    encryption_key(monkeypatch)
    key = str(uuid4())
    calls.reserve_call(key, "a" * 64)
    assert storage.is_encrypted(tmp_path / "runtime_calls.json")
    assert key.encode("ascii") not in (tmp_path / "runtime_calls.json").read_bytes()
    assert calls.reserve_call(key, "a" * 64)["state"] == "pending"


def test_neon_url_cannot_disable_certificate_or_hostname_verification(monkeypatch):
    import psycopg

    observed = {}
    monkeypatch.setattr(db, "_settings", lambda: SimpleNamespace(database_url="postgresql://user:secret@database.neon.tech/estate?sslmode=disable"))
    monkeypatch.setattr(psycopg, "connect", lambda dsn, **kwargs: observed.update(dsn=dsn, **kwargs))
    db._connection()
    assert observed["sslmode"] == "verify-full"
    assert observed["sslrootcert"] == "system"
    assert observed["ssl_min_protocol_version"] == "TLSv1.2"
    assert observed["gssencmode"] == "disable"


@pytest.mark.parametrize("dsn", ["postgresql://remote.example/db", "host=localhost hostaddr=198.51.100.2 dbname=test", "host=localhost,remote.example dbname=test", "dbname=test"])
def test_insecure_database_exception_cannot_reach_remote_or_implicit_hosts(monkeypatch, dsn):
    import psycopg

    monkeypatch.setenv("LASTLY_ALLOW_INSECURE_DB", "true")
    monkeypatch.setattr(db, "_settings", lambda: SimpleNamespace(database_url=dsn))
    monkeypatch.setattr(psycopg, "connect", lambda *args, **kwargs: pytest.fail("An unsafe database reached the network."))
    with pytest.raises(ValueError, match="local"):
        db._connection()


def test_explicit_loopback_database_exception_is_allowed(monkeypatch):
    import psycopg

    monkeypatch.setenv("LASTLY_ALLOW_INSECURE_DB", "true")
    monkeypatch.setattr(db, "_settings", lambda: SimpleNamespace(database_url="postgresql://127.0.0.1/db?sslmode=disable"))
    observed = {}
    monkeypatch.setattr(psycopg, "connect", lambda dsn, **kwargs: observed.update(dsn=dsn, **kwargs))
    db._connection()
    assert observed["dsn"].startswith("postgresql://127.0.0.1/")


def test_takeout_cli_requires_encryption_before_reading_private_mail(monkeypatch, tmp_path):
    import import_takeout

    monkeypatch.setattr("sys.argv", ["import_takeout.py", str(tmp_path / "private.mbox"), "--email", "private@example.com"])
    monkeypatch.setattr(import_takeout, "import_mailbox", lambda *args, **kwargs: pytest.fail("Private mail read before encryption was configured."))
    with pytest.raises(SystemExit) as exc:
        import_takeout.main()
    assert exc.value.code == 2


def test_takeout_cli_writes_encrypted_mail_and_preserves_existing_data(monkeypatch, tmp_path):
    import import_takeout

    encryption_key(monkeypatch)
    path = tmp_path / "private" / "inbox.json"
    monkeypatch.setattr("sys.argv", ["import_takeout.py", str(tmp_path / "mail.mbox"), "--email", "private@example.com", "--output", str(path)])
    result = {"synthetic": False, "emails": [{"subject": "Private bill"}], "import_stats": {"total": 1, "kept": 1, "senders": 1, "invalid_dates": 0}}
    monkeypatch.setattr(import_takeout, "import_mailbox", lambda *args, **kwargs: result)
    import_takeout.main()
    assert storage.read_json(path) == result
    assert storage.is_encrypted(path)
    assert b"Private bill" not in path.read_bytes()
    first_contents = path.read_bytes()
    with pytest.raises(SystemExit) as exc:
        import_takeout.main()
    assert exc.value.code == 2
    assert path.read_bytes() == first_contents
