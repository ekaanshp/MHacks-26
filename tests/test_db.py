import copy
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

import db


@pytest.fixture(autouse=True)
def local_database(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "_data_dir", lambda: tmp_path)
    monkeypatch.setattr(db, "_settings", lambda: SimpleNamespace(database_url=""))
    return tmp_path


def estate():
    return {
        "persona": {"name": "Margaret Ellis", "email": "margaret@example.com"},
        "today": "2026-10-03",
        "stats": {"emails": 790, "candidates": 10, "senders": 4, "accounts": 2},
        "totals": {"accounts": 2},
        "analysis": {"mode": "offline", "recall": "2/2"},
        "accounts": [
            {"id": "acct_00", "institution": "Netflix", "category": "subscription", "status": "open", "assigned_to": None},
            {"id": "acct_01", "institution": "Chase Checking", "category": "bank", "status": "open", "assigned_to": None},
        ],
    }


def test_estate_round_trip_export_and_historical_load(local_database):
    import json

    assert db.load_estate() is None
    first = db.save_estate(estate())
    assert first == 1
    stored = db.load_estate()
    assert stored["estate_id"] == first
    assert stored["analysis"] == {"mode": "offline", "recall": "2/2"}
    assert json.loads((local_database / "estate.json").read_text()) == stored
    newer = estate()
    newer["today"] = "2026-10-04"
    second = db.save_estate(newer)
    assert second != first and db.load_estate()["estate_id"] == second
    assert db.load_estate(first)["today"] == "2026-10-03"
    assert db.load_estate(9999) is None
    # Returned dictionaries are detached from the persistent store.
    stored["accounts"][0]["status"] = "done"
    assert db.load_estate(first)["accounts"][0]["status"] == "open"


def test_edits_survive_a_new_analysis_and_reordered_account_ids():
    first = db.save_estate(estate())
    db.update_account(first, "acct_00", status="done", assigned_to="Sarah")
    rerun = estate()
    rerun["accounts"].reverse()
    rerun["accounts"][1]["id"] = "acct_09"
    second = db.save_estate(rerun)
    netflix = next(account for account in db.load_estate(second)["accounts"] if account["institution"] == "Netflix")
    assert netflix["id"] == "acct_00"
    assert netflix["status"] == "done"
    assert netflix["assigned_to"] == "Sarah"
    assert db.load_estate(first)["accounts"][0]["status"] == "done"


def test_adding_an_alphabetically_earlier_account_does_not_change_ids():
    first = db.save_estate(estate())
    db.update_account(first, "acct_00", status="done", assigned_to="Sarah")
    rerun = estate()
    for index, account in enumerate(rerun["accounts"], start=1):
        account["id"] = f"acct_{index:02d}"
    rerun["accounts"].insert(0, {"id": "acct_00", "institution": "Adobe", "category": "subscription", "status": "open", "assigned_to": None})
    db.save_estate(rerun)
    by_name = {account["institution"]: account for account in db.load_estate()["accounts"]}
    assert by_name["Netflix"]["id"] == "acct_00"
    assert by_name["Netflix"]["status"] == "done"
    assert by_name["Chase Checking"]["id"] == "acct_01"
    assert by_name["Adobe"]["id"] == "acct_02"
    assert len({account["id"] for account in by_name.values()}) == 3


def test_omitted_assignment_is_retained_and_explicit_null_clears_it():
    estate_id = db.save_estate(estate())
    db.update_account(estate_id, "acct_00", assigned_to="Daniel")
    account = db.update_account(estate_id, "acct_00", status="in_progress")
    assert account["assigned_to"] == "Daniel"
    account = db.update_account(estate_id, "acct_00", assigned_to=None)
    assert account["assigned_to"] is None and account["status"] == "in_progress"
    with pytest.raises(ValueError):
        db.update_account(estate_id, "acct_00", status="cancelled")
    with pytest.raises(ValueError):
        db.update_account(estate_id, "acct_00", assigned_to="  ")
    with pytest.raises(KeyError):
        db.update_account(estate_id, "acct_missing", status="done")


def test_a_different_person_does_not_inherit_family_edits():
    first = db.save_estate(estate())
    db.update_account(first, "acct_00", status="done", assigned_to="Sarah")
    other = estate()
    other["persona"]["email"] = "different@example.com"
    db.save_estate(other)
    assert db.load_estate()["accounts"][0]["status"] == "open"
    assert db.load_estate()["accounts"][0]["assigned_to"] is None


def test_concurrent_family_edits_do_not_overwrite_other_accounts():
    sample = estate()
    sample["accounts"] = [dict(copy.deepcopy(sample["accounts"][0]), id=f"acct_{index:02d}", institution=f"Service {index}") for index in range(16)]
    estate_id = db.save_estate(sample)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda index: db.update_account(estate_id, f"acct_{index:02d}", status="done", assigned_to=f"Person {index}"), range(16)))
    accounts = db.load_estate()["accounts"]
    assert all(account["status"] == "done" for account in accounts)
    assert all(account["assigned_to"] == f"Person {index}" for index, account in enumerate(accounts))


def test_activity_limit_and_order():
    estate_id = db.save_estate(estate())
    for index in range(25):
        db.log_activity(estate_id, "acct_00", "Sarah", f"status:{index}")
    recent = db.get_activity()
    assert len(recent) == 20
    assert recent[0]["action"] == "status:24"
    assert recent[-1]["action"] == "status:5"
    assert all(row["estate_id"] == estate_id and row["actor"] == "Sarah" for row in recent)
    assert len(db.get_activity(limit=3)) == 3


def test_neon_outage_serves_local_export_without_logging_secrets(monkeypatch, caplog):
    estate_id = db.save_estate(estate())
    secret = "postgresql://person:extremely-secret-password@unreachable.example/database"
    monkeypatch.setattr(db, "_settings", lambda: SimpleNamespace(database_url=secret))

    def unavailable():
        raise OSError(secret)

    monkeypatch.setattr(db, "_connection", unavailable)
    assert db.load_estate()["estate_id"] == estate_id
    assert db.update_account(estate_id, "acct_00", status="done")["status"] == "done"
    assert db.get_activity() == []
    assert "Neon is unavailable" in caplog.text
    assert "extremely-secret-password" not in caplog.text


def test_loading_a_remote_historical_revision_does_not_replace_latest(local_database):
    import json

    latest = dict(estate(), estate_id=12, today="2026-10-04")
    historical = dict(estate(), estate_id=4, today="2026-10-03")
    db._mirror_remote(latest)
    db._mirror_remote(historical, export=False)
    assert db.load_estate()["estate_id"] == 12
    assert db.load_estate(4)["estate_id"] == 4
    assert json.loads((local_database / "estate.json").read_text())["estate_id"] == 12


def test_first_neon_save_preserves_edits_made_during_offline_development(monkeypatch):
    first = db.save_estate(estate())
    db.update_account(first, "acct_00", status="done", assigned_to="Sarah")
    persisted_accounts = []

    class Cursor:
        def __init__(self, row=None):
            self.row = row

        def fetchone(self):
            return self.row

    class EmptyNeon:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, query, params=None):
            if query.startswith("INSERT INTO estates"):
                return Cursor({"id": 8})
            if query.startswith("INSERT INTO accounts"):
                persisted_accounts.append(params[2].obj)
            return Cursor()

    monkeypatch.setattr(db, "_settings", lambda: SimpleNamespace(database_url="postgresql://test.invalid/db"))
    monkeypatch.setattr(db, "_connection", EmptyNeon)
    assert db.save_estate(estate()) == 8
    netflix = next(account for account in persisted_accounts if account["institution"] == "Netflix")
    assert netflix["status"] == "done" and netflix["assigned_to"] == "Sarah"


class MemoryNeon:
    """Transactional adapter used to exercise replay without a live account."""

    class Cursor:
        def __init__(self, rows=None):
            self.rows = rows or []

        def fetchone(self):
            return self.rows[0] if self.rows else None

        def fetchall(self):
            return self.rows

    def __init__(self, remote, history=None):
        self.estate = copy.deepcopy(remote)
        self.history = copy.deepcopy(history or [])
        self.activity = []
        self.fail_commit = False

    def __enter__(self):
        self.snapshot = (copy.deepcopy(self.estate), copy.deepcopy(self.activity), copy.deepcopy(self.history))
        return self

    def __exit__(self, exc_type, *_):
        if exc_type or self.fail_commit:
            self.estate, self.activity, self.history = self.snapshot
        if self.fail_commit:
            raise OSError("The connection was lost before commit.")
        return False

    def _header(self, estate=None):
        from datetime import date

        estate = estate or self.estate
        return {
            "id": estate["estate_id"], "persona": estate["persona"], "today": date.fromisoformat(estate["today"]),
            "stats": estate["stats"], "totals": estate["totals"],
            "metadata": {"analysis": estate.get("analysis"), "_account_order": [account["id"] for account in estate["accounts"]]},
        }

    def execute(self, query, params=None):
        from datetime import datetime

        if query.startswith("-- Lastly") or query.startswith("SELECT pg_advisory"):
            return self.Cursor()
        if query.startswith("INSERT INTO estates"):
            estate_id = self.estate["estate_id"] + 1
            self.history.append(copy.deepcopy(self.estate))
            self.estate = {"estate_id": estate_id, "persona": params[0].obj, "stats": params[1].obj, "totals": params[2].obj, "today": params[3], "analysis": params[4].obj.get("analysis"), "accounts": []}
            return self.Cursor([{"id": estate_id}])
        if query.startswith("INSERT INTO accounts"):
            self.estate["accounts"].append(copy.deepcopy(params[2].obj))
            return self.Cursor()
        if query.startswith("SELECT * FROM estates ORDER"):
            return self.Cursor([self._header()])
        if query.startswith("SELECT id FROM estates ORDER"):
            return self.Cursor([{"id": self.estate["estate_id"]}])
        if query.startswith("SELECT * FROM estates WHERE id"):
            matched = next((estate for estate in [self.estate, *self.history] if estate["estate_id"] == params[0]), None)
            return self.Cursor([self._header(matched)] if matched else [])
        if query.startswith("SELECT id FROM estates WHERE lower"):
            matched = next((estate for estate in sorted([self.estate, *self.history], key=lambda estate: -estate["estate_id"]) if estate["persona"].get("email", "").lower() == params[0]), None)
            return self.Cursor([{"id": matched["estate_id"]}] if matched else [])
        if query.startswith("SELECT data, status, assigned_to FROM accounts"):
            matched = next((estate for estate in [self.estate, *self.history] if estate["estate_id"] == params[0]), None)
            accounts = matched["accounts"] if matched else []
            if len(params) > 1:
                accounts = [account for account in accounts if account["id"] == params[1]]
            return self.Cursor([{"data": copy.deepcopy(account), "status": account["status"], "assigned_to": account.get("assigned_to")} for account in accounts])
        if query.startswith("UPDATE accounts"):
            for estate in [self.estate, *self.history]:
                for index, account in enumerate(estate["accounts"]):
                    if estate["estate_id"] == params[3] and account["id"] == params[4]:
                        estate["accounts"][index] = dict(params[0].obj, status=params[1], assigned_to=params[2])
            return self.Cursor()
        if query.startswith("INSERT INTO activity"):
            sync_key = params[-1]
            if any(row["sync_key"] == sync_key for row in self.activity):
                return self.Cursor()
            created_at = datetime.fromisoformat(params[4]) if len(params) == 6 else datetime.now().astimezone()
            row = {"id": len(self.activity) + 1, "estate_id": params[0], "account_id": params[1], "actor": params[2], "action": params[3], "created_at": created_at, "sync_key": sync_key}
            self.activity.append(row)
            return self.Cursor([copy.deepcopy(row)])
        if query.startswith("SELECT * FROM activity WHERE sync_key"):
            return self.Cursor([copy.deepcopy(row) for row in self.activity if row["sync_key"] == params[0]])
        if query.startswith("SELECT * FROM activity WHERE estate_id"):
            return self.Cursor(list(reversed([copy.deepcopy(row) for row in self.activity if row["estate_id"] == params[0]]))[:params[1]])
        raise AssertionError(f"Unexpected database query: {query}")


def configured_outage(monkeypatch):
    monkeypatch.setattr(db, "_settings", lambda: SimpleNamespace(database_url="postgresql://test.invalid/db", allow_private_cloud=False))

    def disconnected():
        raise OSError("Neon is temporarily unavailable.")

    monkeypatch.setattr(db, "_connection", disconnected)


def pending_store(directory):
    import json

    return json.loads((directory / "family_store.json").read_text())


def test_reconnect_replays_only_local_fields_and_preserves_other_family_edits(monkeypatch, local_database):
    estate_id = db.save_estate(estate())
    configured_outage(monkeypatch)
    db.update_account(estate_id, "acct_00", status="done")
    queued = pending_store(local_database)["pending_patches"]
    assert len(queued) == 1 and queued[0]["fields"] == {"status": "done"}
    remote = dict(estate(), estate_id=100)
    remote["accounts"][0].update(id="acct_70", assigned_to="Daniel", amount=99.00)
    remote["accounts"][1].update(status="in_progress", assigned_to="Sarah")
    neon = MemoryNeon(remote)
    monkeypatch.setattr(db, "_connection", lambda: neon)
    loaded = db.load_estate()
    netflix = loaded["accounts"][0]
    assert netflix["status"] == "done" and netflix["assigned_to"] == "Daniel"
    assert netflix["amount"] == 99.00
    assert loaded["accounts"][1]["assigned_to"] == "Sarah"
    assert pending_store(local_database)["pending_patches"] == []
    assert db.load_estate()["accounts"][0]["status"] == "done"


def test_outage_assignment_null_and_coalesced_patches_survive_restart(monkeypatch, local_database):
    estate_id = db.save_estate(estate())
    configured_outage(monkeypatch)
    db.update_account(estate_id, "acct_00", assigned_to="Sarah")
    db.update_account(estate_id, "acct_00", status="done")
    db.update_account(estate_id, "acct_00", assigned_to=None)
    queued = pending_store(local_database)["pending_patches"]
    assert len(queued) == 1 and queued[0]["fields"] == {"assigned_to": None, "status": "done"}
    remote = dict(estate(), estate_id=100)
    remote["accounts"][0]["assigned_to"] = "Daniel"
    neon = MemoryNeon(remote)
    monkeypatch.setattr(db, "_connection", lambda: neon)
    assert db.load_estate()["accounts"][0]["assigned_to"] is None
    assert neon.estate["accounts"][0]["status"] == "done"


def test_replay_commit_failure_keeps_queue_and_local_edits(monkeypatch, local_database):
    estate_id = db.save_estate(estate())
    configured_outage(monkeypatch)
    db.update_account(estate_id, "acct_00", status="done")
    neon = MemoryNeon(dict(estate(), estate_id=100))
    neon.fail_commit = True
    monkeypatch.setattr(db, "_connection", lambda: neon)
    assert db.load_estate()["accounts"][0]["status"] == "done"
    assert len(pending_store(local_database)["pending_patches"]) == 1
    assert neon.estate["accounts"][0]["status"] == "open"
    neon.fail_commit = False
    assert db.load_estate()["accounts"][0]["status"] == "done"
    assert pending_store(local_database)["pending_patches"] == []


def test_pending_patch_never_applies_to_a_different_person(monkeypatch, local_database):
    estate_id = db.save_estate(estate())
    configured_outage(monkeypatch)
    db.update_account(estate_id, "acct_00", status="done")
    different = dict(estate(), estate_id=100)
    different["persona"]["email"] = "another@example.com"
    neon = MemoryNeon(different)
    monkeypatch.setattr(db, "_connection", lambda: neon)
    assert db.load_estate()["accounts"][0]["status"] == "open"
    assert len(pending_store(local_database)["pending_patches"]) == 1


def test_outage_activity_reconciles_without_duplicate_rows(monkeypatch, local_database):
    estate_id = db.save_estate(estate())
    configured_outage(monkeypatch)
    db.log_activity(estate_id, "acct_00", "Sarah", "status:done")
    assert len(pending_store(local_database)["pending_activity"]) == 1
    neon = MemoryNeon(dict(estate(), estate_id=100))
    monkeypatch.setattr(db, "_connection", lambda: neon)
    db.load_estate()
    recent = db.get_activity()
    assert len(recent) == 1 and recent[0]["actor"] == "Sarah"
    assert recent[0]["estate_id"] == 100
    assert pending_store(local_database)["pending_activity"] == []
    assert len(neon.activity) == 1
    assert len(db.get_activity()) == 1


def test_fresh_cloud_analysis_reconciles_pending_patches_and_activity(monkeypatch, local_database):
    estate_id = db.save_estate(estate())
    configured_outage(monkeypatch)
    db.update_account(estate_id, "acct_00", status="done")
    db.log_activity(estate_id, "acct_00", "Sarah", "status:done")
    remote = dict(estate(), estate_id=100)
    remote["accounts"][0].update(id="acct_70", assigned_to="Daniel")
    neon = MemoryNeon(remote)
    monkeypatch.setattr(db, "_connection", lambda: neon)
    new_id = db.save_estate(estate())
    assert new_id == 101
    netflix = next(account for account in db.load_estate()["accounts"] if account["institution"] == "Netflix")
    assert netflix["id"] == "acct_70"
    assert netflix["status"] == "done" and netflix["assigned_to"] == "Daniel"
    assert db.get_activity()[0]["estate_id"] == 101
    assert pending_store(local_database)["pending_patches"] == []
    assert pending_store(local_database)["pending_activity"] == []


def test_cloud_revision_with_a_lower_serial_id_becomes_the_active_revision(local_database):
    local = dict(estate(), estate_id=50, today="2026-10-03")
    cloud = dict(estate(), estate_id=2, today="2026-10-04")
    db._mirror_remote(local)
    db._mirror_remote(cloud)
    assert db.load_estate()["estate_id"] == 2
    assert db.load_estate(50)["today"] == "2026-10-03"


def test_a_cloud_id_collision_archives_local_family_progress_and_activity(local_database):
    local_id = db.save_estate(estate())
    db.update_account(local_id, "acct_00", status="done", assigned_to="Sarah")
    db.log_activity(local_id, "acct_00", "Sarah", "status:done")
    other_person = dict(estate(), estate_id=local_id)
    other_person["persona"]["email"] = "another@example.com"
    db._mirror_remote(other_person)
    store = pending_store(local_database)
    archived = next(item for item in store["estates"] if item["persona"]["email"] == "margaret@example.com")
    assert archived["estate_id"] != local_id
    assert archived["accounts"][0]["status"] == "done"
    assert archived["accounts"][0]["assigned_to"] == "Sarah"
    assert store["activity"][0]["estate_id"] == archived["estate_id"]
    db.save_estate(estate())
    assert db.load_estate()["accounts"][0]["status"] == "done"
    assert db.load_estate()["accounts"][0]["assigned_to"] == "Sarah"


def test_latest_load_follows_connected_inbox_and_falls_back_to_same_person(monkeypatch, local_database):
    import json

    local_id = db.save_estate(estate())
    db.update_account(local_id, "acct_00", status="done")
    (local_database / "inbox.json").write_text(json.dumps({"synthetic": True, "persona": {"email": "margaret@example.com", "name": "Margaret Ellis"}}))
    different = dict(estate(), estate_id=100)
    different["persona"]["email"] = "another@example.com"
    neon = MemoryNeon(different)
    monkeypatch.setattr(db, "_settings", lambda: SimpleNamespace(database_url="postgresql://test.invalid/db", allow_private_cloud=False))
    monkeypatch.setattr(db, "_connection", lambda: neon)
    assert db.load_estate()["estate_id"] == local_id
    assert db.load_estate()["accounts"][0]["status"] == "done"
    # Explicit historical requests retain their original meaning.
    assert db.load_estate(100)["persona"]["email"] == "another@example.com"
    assert db.load_estate()["persona"]["email"] == "margaret@example.com"


def test_latest_load_never_returns_a_different_person_when_local_match_is_missing(monkeypatch, local_database):
    import json

    (local_database / "inbox.json").write_text(json.dumps({"synthetic": True, "persona": {"email": "missing@example.com"}}))
    neon = MemoryNeon(dict(estate(), estate_id=100))
    monkeypatch.setattr(db, "_settings", lambda: SimpleNamespace(database_url="postgresql://test.invalid/db", allow_private_cloud=False))
    monkeypatch.setattr(db, "_connection", lambda: neon)
    assert db.load_estate() is None


def test_connected_persona_loads_its_older_remote_estate_instead_of_global_latest(monkeypatch, local_database):
    import json

    margaret = dict(estate(), estate_id=50)
    margaret["accounts"][0].update(status="done", assigned_to="Sarah")
    global_latest = dict(estate(), estate_id=100)
    global_latest["persona"]["email"] = "another@example.com"
    (local_database / "inbox.json").write_text(json.dumps({"synthetic": True, "persona": margaret["persona"]}))
    neon = MemoryNeon(global_latest, history=[margaret])
    monkeypatch.setattr(db, "_settings", lambda: SimpleNamespace(database_url="postgresql://test.invalid/db", allow_private_cloud=False))
    monkeypatch.setattr(db, "_connection", lambda: neon)
    connected = db.load_estate()
    assert connected["estate_id"] == 50
    assert connected["accounts"][0]["assigned_to"] == "Sarah"
    assert connected["accounts"][0]["status"] == "done"


def test_first_neon_save_uses_same_person_local_history_when_another_person_is_latest(monkeypatch):
    first = db.save_estate(estate())
    db.update_account(first, "acct_00", status="done", assigned_to="Sarah")
    different = estate()
    different["persona"]["email"] = "another@example.com"
    db.save_estate(different)
    neon = MemoryNeon(dict(different, estate_id=100))
    monkeypatch.setattr(db, "_settings", lambda: SimpleNamespace(database_url="postgresql://test.invalid/db", allow_private_cloud=False))
    monkeypatch.setattr(db, "_connection", lambda: neon)
    new_id = db.save_estate(estate())
    restored = db.load_estate(new_id)
    assert restored["persona"]["email"] == "margaret@example.com"
    assert restored["accounts"][0]["status"] == "done"
    assert restored["accounts"][0]["assigned_to"] == "Sarah"


def test_revoked_private_cloud_permission_keeps_private_journal_local(monkeypatch, local_database):
    import json

    private = estate()
    private["analysis"] = {"synthetic": False}
    configured_outage(monkeypatch)
    monkeypatch.setattr(db, "_settings", lambda: SimpleNamespace(database_url="postgresql://test.invalid/db", allow_private_cloud=True))
    estate_id = db.save_estate(private)
    db.update_account(estate_id, "acct_00", status="done")
    db.log_activity(estate_id, "acct_00", "Sarah", "status:done")
    (local_database / "inbox.json").write_text(json.dumps({"synthetic": True, "persona": private["persona"]}))
    monkeypatch.setattr(db, "_settings", lambda: SimpleNamespace(database_url="postgresql://test.invalid/db", allow_private_cloud=False))
    neon = MemoryNeon(dict(estate(), estate_id=100))
    monkeypatch.setattr(db, "_connection", lambda: neon)
    db.load_estate()
    assert neon.estate["accounts"][0]["status"] == "open"
    assert neon.activity == []
    assert len(pending_store(local_database)["pending_patches"]) == 1
    assert len(pending_store(local_database)["pending_activity"]) == 1


@pytest.mark.parametrize("has_inbox", [False, True])
def test_private_dataset_never_attempts_neon_without_explicit_opt_in(monkeypatch, local_database, has_inbox):
    import json

    private = estate()
    private["analysis"] = {"synthetic": False}
    monkeypatch.setattr(db, "_settings", lambda: SimpleNamespace(database_url="postgresql://test.invalid/db", allow_private_cloud=False))

    def forbidden_connection():
        raise AssertionError("Private data must not attempt any Neon connection.")

    monkeypatch.setattr(db, "_connection", forbidden_connection)
    if has_inbox:
        (local_database / "inbox.json").write_text(json.dumps({"synthetic": False}))
    estate_id = db.save_estate(private)
    assert db.load_estate()["estate_id"] == estate_id
    assert db.update_account(estate_id, "acct_00", status="done")["status"] == "done"
    db.log_activity(estate_id, "acct_00", "Me", "status:done")
    assert len(db.get_activity()) == 1
    assert pending_store(local_database)["pending_patches"] == []
    assert pending_store(local_database)["pending_activity"] == []


def test_backend_only_member_ids_never_leave_the_database_layer(local_database):
    estate_id = db.save_estate(estate())
    db.log_activity(estate_id, "acct_00", "Sarah", "status:done")
    store_path = local_database / "family_store.json"
    store = json.loads(store_path.read_text())
    # Rows replayed from Neon carry the trigger-assigned member id; it must stay private.
    store["activity"][-1]["actor_member_id"] = "mem_0123456789abcdef"
    store_path.write_text(json.dumps(store))
    rows = db.get_activity(estate_id)
    assert rows and all("actor_member_id" not in row and "sync_key" not in row for row in rows)
    assert "mem_" not in json.dumps(rows)


def test_schema_links_members_and_offers_readable_views():
    schema = (Path(db.__file__).parent / "schema.sql").read_text()
    for statement in ("CREATE TABLE IF NOT EXISTS family_members", "accounts_link_assignee", "activity_link_actor",
                      "CREATE OR REPLACE VIEW account_overview", "CREATE OR REPLACE VIEW activity_overview"):
        assert statement in schema
