"""Exercise actual startup migration and private-session access together."""
import base64
import secrets
import stat

import pytest
from fastapi.testclient import TestClient

import pipeline
import secure_storage
import server


def test_private_startup_encrypts_legacy_artifacts_without_invalidating_evidence(dataset, make_private, monkeypatch):
    expected = make_private()
    original_hash = pipeline.source_hash(dataset)
    token = secrets.token_urlsafe(32)
    monkeypatch.setenv("LASTLY_ACCESS_TOKEN", token)
    monkeypatch.setenv("LASTLY_DATA_KEY", base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
    with TestClient(server.app, base_url="http://localhost", headers={"X-Requested-With": "Lastly"}) as client:
        for name in ("inbox.json", "bank.csv", "estate.json", "family_store.json"):
            path = dataset / name
            assert secure_storage.is_encrypted(path)
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
            assert "Private Person" not in path.read_text()
        assert stat.S_IMODE(dataset.stat().st_mode) == 0o700
        assert pipeline.source_hash(dataset) == original_hash
        assert client.get("/api/estate").status_code == 401
        session = client.post("/api/session", json={"access_code": token})
        assert session.status_code == 200
        result = client.get("/api/estate").json()
        assert result["estate_id"] == expected["estate_id"]
        assert result["persona"]["name"] == "Private Person"
        evidence = next(eid for item in result["accounts"] for eid in item["evidence_ids"] if eid.startswith("msg_"))
        assert client.get(f"/api/email/{evidence}").status_code == 200


def test_wrong_key_refuses_startup_without_exposing_private_values(dataset, make_private, monkeypatch):
    make_private()
    monkeypatch.setenv("LASTLY_ACCESS_TOKEN", secrets.token_urlsafe(32))
    monkeypatch.setenv("LASTLY_DATA_KEY", base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
    with TestClient(server.app, base_url="http://localhost"):
        pass
    monkeypatch.setenv("LASTLY_DATA_KEY", base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
    with pytest.raises(RuntimeError) as failure:
        with TestClient(server.app, base_url="http://localhost"):
            pytest.fail("An incorrectly encrypted private inbox was accepted")
    assert "startup refused" in str(failure.value)
    assert "Private Person" not in str(failure.value) and "private@example.com" not in str(failure.value)
