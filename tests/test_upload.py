"""Uploading a Google Takeout mailbox through the app, then analyzing it."""
from __future__ import annotations

import base64
import json
import mailbox
import secrets
from email.message import EmailMessage

import pytest
from fastapi.testclient import TestClient
from test_security import isolated_app, login

import llm
import pipeline

OWNER = "rosa.vance@example.com"
PARAMS = {"deceased": "Rosa Vance", "name": "Ines", "relationship": "daughter", "pronoun": "she"}


def takeout(path, extra: int = 0) -> bytes:
    box = mailbox.mbox(str(path))
    def add(sender, subject, day, body, labels="Inbox,Opened"):
        msg = EmailMessage()
        msg["X-GM-THRID"] = str(secrets.randbits(60))
        msg["X-Gmail-Labels"] = labels
        msg["Delivered-To"] = OWNER
        msg["From"], msg["To"], msg["Subject"], msg["Date"] = sender, OWNER, subject, day
        msg.set_content(body)
        box.add(msg)
    for month in ("Apr", "May", "Jun", "Jul", "Aug", "Sep"):
        add("Netflix <info@account.netflix.com>", "Your Netflix receipt", f"Fri, 01 {month} 2026 06:00:00 +0000",
            "We charged $15.49 for your Standard plan. Your next billing date is next month.")
    add("Prize <win@scam.example>", "You won", "Tue, 01 Sep 2026 06:00:00 +0000", "Send bank details.", labels="Spam")
    add(f"Rosa Vance <{OWNER}>", "Re: dinner", "Tue, 01 Sep 2026 07:00:00 +0000", "See you soon.", labels="Sent")
    for index in range(extra):
        add(f"news{index}@club.example", "Garden club news", "Wed, 02 Sep 2026 06:00:00 +0000", "See you Sunday.")
    box.flush()
    box.close()
    return path.read_bytes()


@pytest.fixture
def security_environment(dataset, monkeypatch):
    for name in ("LASTLY_ACCESS_TOKEN", "LASTLY_AGENT_TOKEN", "LASTLY_DATA_KEY", "LASTLY_PRODUCTION", "LASTLY_ALLOWED_HOSTS", "LASTLY_ALLOWED_ORIGINS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(llm, "enabled", lambda: False)
    return dataset


@pytest.fixture
def family(security_environment, estate_data, monkeypatch):
    token = secrets.token_urlsafe(32)
    monkeypatch.setenv("LASTLY_ACCESS_TOKEN", token)
    monkeypatch.setenv("LASTLY_DATA_KEY", base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
    with TestClient(isolated_app(), base_url="http://localhost", headers={"X-Requested-With": "Lastly"}) as client:
        client.headers["X-CSRF-Token"] = login(client, token)
        yield client


def upload(client, data: bytes, params=PARAMS, content_type="application/mbox"):
    return client.post("/api/import", params=params, content=data, headers={"Content-Type": content_type})


def test_upload_creates_encrypted_estate_that_signs_in_and_analyzes(family, tmp_path, security_environment):
    response = upload(family, takeout(tmp_path / "upload.mbox", extra=3))
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["estate"] == "rosa-vance" and result["ai"] is False
    # Spam and Rosa's own sent mail are skipped.
    assert result["stats"]["kept"] == 9 and result["stats"]["total"] == 11

    stored = security_environment / "estates" / "rosa-vance" / "inbox.json"
    assert b"Netflix" not in stored.read_bytes(), "Uploaded mail must be encrypted at rest"
    assert not any((security_environment / ".uploads").iterdir()), "The raw upload must be deleted"

    identity = family.post("/api/identify", json={"deceased": "Rosa Vance", "name": "Ines", "relationship": "daughter"}).json()
    assert identity["found"] and identity["estate"] == "rosa-vance" and identity["executor"] is True

    estate = family.post("/api/analyze", headers={"X-Lastly-Estate": "rosa-vance"}).json()
    assert estate["persona"]["name"] == "Rosa Vance" and estate["persona"]["email"] == OWNER
    assert estate["analysis"] == {**estate["analysis"], "synthetic": False, "method": "rules"}
    assert any(account["institution"] == "Netflix" for account in estate["accounts"])
    # The family's original demo estate is untouched.
    assert family.get("/api/estate").json()["persona"]["name"] == "Margaret Ellis"


def test_upload_uses_ai_only_with_private_cloud_consent(family, tmp_path, monkeypatch):
    calls = []

    def fake(system, user, **kwargs):
        calls.append(system == pipeline.TRIAGE_SYSTEM)
        if system == pipeline.TRIAGE_SYSTEM:
            return {"senders": [{"sender": item["sender"], "has_account": item["sender"].endswith("netflix.com")} for item in json.loads(user)["senders"]]}
        ids = [email["id"] for email in json.loads(user)["emails"]]
        return {"accounts": [{"institution": "Netflix", "category": "subscription", "frequency": "monthly", "amount": 15.49, "evidence_ids": ids}]}

    monkeypatch.setattr(llm, "enabled", lambda: True)
    monkeypatch.setattr(llm, "complete_json", fake)
    assert upload(family, takeout(tmp_path / "upload.mbox", extra=2)).json()["ai"] is False
    estate = family.post("/api/analyze", headers={"X-Lastly-Estate": "rosa-vance"}).json()
    assert estate["analysis"]["method"] == "rules" and not calls

    monkeypatch.setenv("ALLOW_PRIVATE_CLOUD", "true")
    estate = family.post("/api/analyze?fresh=true", headers={"X-Lastly-Estate": "rosa-vance"}).json()
    assert estate["analysis"]["method"] == "anthropic"
    assert calls.count(True) == 1 and calls.count(False) == 1, "One triage call, then extraction only for Netflix"
    netflix = next(account for account in estate["accounts"] if account["institution"] == "Netflix")
    assert netflix["amount"] == 15.49 and netflix["email_count"] == 6


def test_upload_requires_encryption_and_login(security_environment, estate_data, monkeypatch, tmp_path):
    data = takeout(tmp_path / "upload.mbox")
    token = secrets.token_urlsafe(32)
    monkeypatch.setenv("LASTLY_ACCESS_TOKEN", token)
    with TestClient(isolated_app(), base_url="http://localhost", headers={"X-Requested-With": "Lastly"}) as client:
        assert upload(client, data).status_code == 401
        client.headers["X-CSRF-Token"] = login(client, token)
        response = upload(client, data)
        assert response.status_code == 503 and "LASTLY_DATA_KEY" in response.json()["detail"]
    assert not (security_environment / "estates").exists()


def test_upload_rejects_bad_input(family, tmp_path):
    data = takeout(tmp_path / "upload.mbox")
    assert upload(family, b"<html>not a mailbox</html>").status_code == 415
    assert upload(family, data, content_type="application/json").status_code == 415
    assert upload(family, data, params={**PARAMS, "pronoun": "it"}).status_code == 422
    assert upload(family, data, params={**PARAMS, "date_of_death": "2099-01-01"}).status_code == 400
    # Uploads are limited to three attempts a minute.
    assert upload(family, data).status_code == 429


def test_upload_refuses_existing_estate_names(family, tmp_path):
    data = takeout(tmp_path / "upload.mbox")
    assert upload(family, data).status_code == 200
    assert upload(family, data).status_code == 409
    assert upload(family, data, params={**PARAMS, "deceased": "Margaret Ellis"}).status_code == 409


def test_demo_estates_switch_to_ai_analysis_when_available(family, monkeypatch):
    calls = []

    def fake(system, user, **kwargs):
        calls.append(system == pipeline.TRIAGE_SYSTEM)
        request = json.loads(user)
        if system == pipeline.TRIAGE_SYSTEM:
            return {"senders": [{"sender": item["sender"], "has_account": item["sender"] == "info@account.netflix.com"} for item in request["senders"]]}
        if isinstance(request, dict) and "descriptor" in request:  # Bank statement merchant names.
            return {"institution": request["descriptor"].title()}
        if not isinstance(request, dict) or "emails" not in request:  # Same-company checks while merging.
            return {"same": False}
        ids = [email["id"] for email in request["emails"]]
        return {"accounts": [{"institution": "Netflix", "category": "subscription", "frequency": "monthly", "amount": 15.49, "evidence_ids": ids}]}

    # Without AI the prepared rules analysis is returned as before.
    assert family.post("/api/analyze").json()["analysis"]["method"] == "rules"
    monkeypatch.setattr(llm, "enabled", lambda: True)
    monkeypatch.setattr(llm, "complete_json", fake)
    assert family.get("/api/health").json()["ai_analysis"] is True
    # Rehearsal mode stays offline and instant.
    assert family.post("/api/analyze?demo=true").json()["analysis"]["method"] == "rules" and not calls
    estate = family.post("/api/analyze").json()
    assert estate["analysis"]["method"] == "anthropic" and calls
    # Claude's analysis is saved and reused, not repeated on every visit.
    calls.clear()
    assert family.post("/api/analyze").json()["analysis"]["method"] == "anthropic" and not calls


def test_same_mailbox_under_another_name_points_to_the_existing_estate(family, tmp_path):
    data = takeout(tmp_path / "upload.mbox")
    assert upload(family, data).status_code == 200
    response = upload(family, data, params={**PARAMS, "deceased": "Rosa M Vance"})
    assert response.status_code == 409 and response.json()["existing"] == "Rosa Vance"
    assert not (tmp_path / "estates" / "rosa-m-vance").exists()


def test_a_deleted_upload_is_not_recreated_by_a_late_request(family, tmp_path):
    import db
    import server

    assert upload(family, takeout(tmp_path / "upload.mbox")).status_code == 200
    family.post("/api/analyze", headers={"X-Lastly-Estate": "rosa-vance"})
    directory = tmp_path / "estates" / "rosa-vance"
    assert family.delete("/api/import", headers={"X-Lastly-Estate": "rosa-vance"}).status_code == 200
    assert not directory.exists()
    # A request that was already looking at this estate (such as an agent poll) must not write it back.
    with server.estate_context(directory):
        with pytest.raises(db.EstateRemoved):
            db.load_estate()
    assert not directory.exists()
    assert family.post("/api/identify", json={"deceased": "Rosa Vance", "name": "Ines", "relationship": "daughter"}).json()["found"] is False
