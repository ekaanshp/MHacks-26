"""Agent-to-agent claims: queued by the family, relayed by Lastly's agent, answered only by the insurer agent."""
from __future__ import annotations

import secrets

import pytest
from test_security import http_client, login, secured, security_environment  # noqa: F401  (fixtures)

import claims
import insurer_agent

INSURER = "agent1q" + "a" * 58
OTHER = "agent1q" + "b" * 58


@pytest.fixture
def relay(secured, monkeypatch):  # noqa: F811
    client, token = secured
    agent_token = secrets.token_urlsafe(32)
    monkeypatch.setenv("LASTLY_AGENT_TOKEN", agent_token)
    monkeypatch.setenv("CLAIMS_AGENT_ADDRESS", INSURER)
    csrf = login(client, token)
    metlife = next(a["id"] for a in client.get("/api/estate").json()["accounts"] if a["institution"] == "MetLife")
    agent = {"Authorization": f"Bearer {agent_token}", "X-Lastly-Agent-Mode": "synthetic", "X-Lastly-Agent-Sender": "self"}
    return client, csrf, metlife, agent


def test_family_queues_one_claim_and_agent_receives_minimal_request(relay):
    client, csrf, metlife, agent = relay
    first = client.post(f"/api/claim/{metlife}", headers={"X-CSRF-Token": csrf}).json()
    again = client.post(f"/api/claim/{metlife}", headers={"X-CSRF-Token": csrf}).json()
    assert first["status"] == "queued" and again["id"] == first["id"]
    client.cookies.clear()
    pending = client.get("/api/agent/claims", headers=agent).json()["claims"]
    assert len(pending) == 1
    item = pending[0]
    assert item["insurer"] == INSURER and item["request_id"] == first["id"]
    assert item["institution"] == "MetLife" and item["policy_type"] == "insurance" and item["claimant_name"]
    assert set(item) == {"insurer", "request_id", "policyholder_name", "date_of_death", "institution", "policy_type", "claimant_name"}


def test_only_the_configured_insurer_can_open_the_claim(relay):
    client, csrf, metlife, agent = relay
    claim = client.post(f"/api/claim/{metlife}", headers={"X-CSRF-Token": csrf}).json()
    client.cookies.clear()
    assert client.post(f"/api/agent/claims/{claim['id']}", json={"status": "sent"}, headers=agent).status_code == 200
    forged = {"status": "opened", "responder": OTHER, "claim_number": "CLM-000001", "required_documents": ["x"], "message": "ok"}
    assert client.post(f"/api/agent/claims/{claim['id']}", json=forged, headers=agent).status_code == 403
    genuine = dict(forged, responder=INSURER)
    opened = client.post(f"/api/agent/claims/{claim['id']}", json=genuine, headers=agent)
    assert opened.status_code == 200 and opened.json()["claim_number"] == "CLM-000001"
    # A resolved claim cannot be reopened or changed.
    assert client.post(f"/api/agent/claims/{claim['id']}", json=dict(genuine, status="rejected"), headers=agent).status_code == 409


def test_agent_cannot_open_claims_or_touch_accounts(relay):
    client, csrf, metlife, agent = relay
    client.cookies.clear()
    assert client.post(f"/api/claim/{metlife}", headers=agent).status_code == 403
    assert client.patch(f"/api/account/{metlife}", json={"status": "done"}, headers=agent).status_code == 403
    assert client.get("/api/agent/claims/../estate", headers=agent).status_code in {403, 404}


def test_family_session_cannot_use_agent_relay(relay):
    client, csrf, metlife, agent = relay
    assert client.get("/api/agent/claims").status_code == 403


def test_claims_need_configuration(relay, monkeypatch):
    client, csrf, metlife, agent = relay
    monkeypatch.delenv("CLAIMS_AGENT_ADDRESS")
    assert client.post(f"/api/claim/{metlife}", headers={"X-CSRF-Token": csrf}).status_code == 503


def test_only_claimable_accounts_can_be_sent(relay):
    client, csrf, metlife, agent = relay
    netflix = next(a["id"] for a in client.get("/api/estate").json()["accounts"] if a["institution"] == "Netflix")
    assert client.post(f"/api/claim/{netflix}", headers={"X-CSRF-Token": csrf}).status_code == 400


def test_no_claim_yet_is_not_an_error(relay):
    client, csrf, metlife, agent = relay
    assert client.get(f"/api/claim/{metlife}").json() == {"status": None}


class Request:
    def __init__(self, **values):
        defaults = {"request_id": "a" * 32, "policyholder_name": "Margaret Ellis", "date_of_death": "2026-09-12",
                    "institution": "MetLife", "policy_type": "insurance", "claimant_name": "Daniel"}
        defaults.update(values)
        self.__dict__.update(defaults)


def test_insurer_opens_valid_claims_with_stable_numbers_and_rejects_invalid_ones():
    status, number, documents, message = insurer_agent.review(Request())
    assert status == "opened" and number == insurer_agent.claim_number("a" * 32) and number.startswith("CLM-")
    assert "death certificate" in documents[0].lower() and "Simulated insurer" in message
    assert insurer_agent.review(Request(date_of_death="soon"))[0] == "rejected"
    assert insurer_agent.review(Request(date_of_death="2999-01-01"))[0] == "rejected"
    assert insurer_agent.review(Request(policy_type="subscription"))[0] == "rejected"


def test_claim_records_persist_and_evict_only_finished(monkeypatch, tmp_path):
    monkeypatch.setenv("LASTLY_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CLAIMS_AGENT_ADDRESS", INSURER)
    monkeypatch.setenv("LASTLY_AGENT_TOKEN", "x" * 40)
    persona = {"name": "Margaret Ellis", "email": "m@example.com", "date_of_death": "2026-09-12"}
    record = claims.create({"id": "acct_15", "institution": "MetLife", "category": "insurance"}, persona, "Daniel")
    assert claims.latest("acct_15", persona)["id"] == record["id"]
    with pytest.raises(PermissionError):
        claims.update(record["id"], "opened", responder=OTHER, claim_number="CLM-1")


def test_opened_claim_appears_in_session_progress(relay):
    client, csrf, metlife, agent = relay
    claim = client.post(f"/api/claim/{metlife}", headers={"X-CSRF-Token": csrf}).json()
    family_cookies = dict(client.cookies)
    client.cookies.clear()
    opened = {"status": "opened", "responder": INSURER, "claim_number": "CLM-000002", "required_documents": [], "message": "ok"}
    assert client.post(f"/api/agent/claims/{claim['id']}", json=opened, headers=agent).status_code == 200
    client.cookies.update(family_cookies)
    claims_now = client.get("/api/progress").json()["claims_in_progress"]
    assert claims_now == [{"institution": "MetLife", "amount": 50000.0}]
