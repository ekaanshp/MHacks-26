"""Encrypted CLI metadata, bank import and backup flows."""
import base64
import secrets
import sys

import pytest

import bank
import manage
import secure_storage


@pytest.fixture
def encrypted_key(monkeypatch):
    monkeypatch.setenv("LASTLY_DATA_KEY", base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())


def test_persona_command_preserves_private_encryption(dataset, monkeypatch, encrypted_key):
    path = dataset / "inbox.json"
    inbox = secure_storage.read_json(path)
    inbox["synthetic"] = False
    secure_storage.write_json(path, inbox)
    monkeypatch.setattr(sys, "argv", ["manage.py", "persona", "--name", "Private Person", "--date-of-death", "2026-09-01"])
    manage.main()
    assert secure_storage.is_encrypted(path)
    saved = secure_storage.read_json(path)
    assert saved["persona"]["name"] == "Private Person"
    assert saved["persona"]["date_of_death"] == "2026-09-01"
    assert "Private Person" not in path.read_text()


def test_bank_import_validates_and_encrypts_without_overwriting(dataset, tmp_path, monkeypatch, encrypted_key):
    source = tmp_path / "statement.csv"
    source.write_text("id,date,description,amount\nbank_new,2026-10-01,Merchant,-10\n")
    monkeypatch.setattr(sys, "argv", ["manage.py", "import-bank", "--input", str(source)])
    with pytest.raises(FileExistsError):
        manage.main()
    monkeypatch.setattr(sys, "argv", ["manage.py", "import-bank", "--input", str(source), "--replace"])
    manage.main()
    destination = dataset / "bank.csv"
    assert secure_storage.is_encrypted(destination)
    assert bank.read_rows(destination) == [{"id": "bank_new", "date": "2026-10-01", "description": "Merchant", "amount": -10.0}]


def test_bank_import_refuses_unencrypted_storage(dataset, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["manage.py", "import-bank", "--input", str(dataset / "bank.csv")])
    with pytest.raises(SystemExit):
        manage.main()


def test_private_export_is_encrypted_and_refuses_overwrite(dataset, estate_data, monkeypatch, encrypted_key):
    estate_data["analysis"]["synthetic"] = False
    monkeypatch.setattr(manage.db, "load_estate", lambda: estate_data)
    backup = dataset / "estate-backup.json"
    monkeypatch.setattr(sys, "argv", ["manage.py", "export", "--output", str(backup)])
    manage.main()
    assert secure_storage.is_encrypted(backup)
    assert secure_storage.read_json(backup)["accounts"] == estate_data["accounts"]
    with pytest.raises(SystemExit):
        manage.main()
