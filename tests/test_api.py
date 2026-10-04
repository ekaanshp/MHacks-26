from __future__ import annotations

import base64
import json
import secrets

import pytest
from fastapi.testclient import TestClient

import llm
import pipeline
import server


@pytest.fixture
def client(dataset):
    with TestClient(server.app, base_url="http://localhost", headers={"X-Requested-With": "Lastly"}) as client:
        yield client


def account(client, name):
    return next(item for item in client.get("/api/estate").json()["accounts"] if item["institution"] == name)


def test_cache_does_not_invoke_pipeline_even_when_live_requested(client, monkeypatch):
    monkeypatch.setattr(pipeline, "run", lambda *args, **kwargs: pytest.fail("Cached demo invoked extraction"))
    response = client.post("/api/analyze?mock=0")
    assert response.status_code == 200 and response.json()["stats"]["accounts"] == 28


def test_family_edits_and_explicit_unassign_survive_reanalysis(client):
    netflix = account(client, "Netflix")
    response = client.patch(f"/api/account/{netflix['id']}", json={"status": "done", "assigned_to": "Sarah"})
    assert response.status_code == 200
    updated = next(item for item in client.post("/api/analyze?mock=1&fresh=1").json()["accounts"] if item["institution"] == "Netflix")
    assert updated["id"] == netflix["id"] and updated["status"] == "done" and updated["assigned_to"] == "Sarah"
    assert client.patch(f"/api/account/{netflix['id']}", json={"assigned_to": None}).json()["assigned_to"] is None
    assert client.get("/api/activity").json()["activity"][0]["institution"] == "Netflix"


@pytest.mark.parametrize("patch", [{}, {"status": "invalid"}, {"status": None}, {"amount": 0}])
def test_invalid_family_edits_rejected(client, patch):
    netflix = account(client, "Netflix")
    assert client.patch(f"/api/account/{netflix['id']}", json=patch).status_code == 422


def test_email_bank_and_missing_evidence(client):
    insurance = account(client, "MetLife")
    email_id = next(eid for eid in insurance["evidence_ids"] if eid.startswith("msg_"))
    proof = client.get(f"/api/email/{email_id}")
    assert proof.status_code == 200 and "$50,000.00" in proof.json()["body"]
    assert set(proof.json()) == {"id", "from", "subject", "date", "body"}
    yoga = account(client, "Ypsi Yoga Collective")
    assert client.get(f"/api/bank/{yoga['evidence_ids'][0]}").json()["amount"] == -45
    assert client.get("/api/email/msg_missing").status_code == 404
    assert client.patch("/api/account/acct_missing", json={"status": "done"}).status_code == 404


def test_question_answers_have_real_evidence_and_filters(client):
    answer = client.post("/api/ask", json={"question": "Did Margaret have life insurance?"}).json()
    assert "MetLife" in answer["answer"] and "$50,000.00" in answer["answer"]
    assert answer["evidence_ids"]
    for eid in answer["evidence_ids"]:
        assert client.get("/api/email/" + eid).status_code == 200
    bank_answer = client.post("/api/ask", json={"question": "Which bank-only subscriptions were found?"}).json()
    assert "Ypsi Yoga Collective" in bank_answer["answer"] and "Netflix" not in bank_answer["answer"]
    assert client.post("/api/ask", json={"question": "What stopped charging?"}).json()["answer"].startswith("Hulu")
    assert client.post("/api/ask", json={"question": "   "}).status_code == 422


def test_offline_letters_work_and_call_is_honestly_unavailable(client):
    gym = account(client, "Planet Fitness")
    letter = client.post(f"/api/letter/{gym['id']}")
    assert letter.status_code == 200 and "Planet Fitness" in letter.json()["letter"]
    call = client.post(f"/api/call/{gym['id']}", json={"to_number": "+17345551234"})
    assert call.status_code == 503 and "No call has been placed" in call.json()["detail"]
    assert client.post(f"/api/call/{gym['id']}", json={"to_number": "7345551234"}).status_code == 422
    assert account(client, "Planet Fitness")["status"] == "open"


def test_changed_source_cannot_serve_old_evidence_or_actions(client, dataset):
    gym = account(client, "Planet Fitness")
    path = dataset / "inbox.json"
    inbox = json.loads(path.read_text())
    inbox["emails"][0]["body"] = "Different content from a new export"
    path.write_text(json.dumps(inbox))
    assert client.get("/api/estate").status_code == 409
    assert client.get(f"/api/email/{gym['evidence_ids'][0]}").status_code == 409
    assert client.patch(f"/api/account/{gym['id']}", json={"status": "done"}).status_code == 409
    assert client.post("/api/analyze?mock=1").status_code == 200
    assert client.get("/api/estate").status_code == 200


def test_private_estate_stays_local_and_agent_cannot_read_it(client, make_private, monkeypatch):
    make_private()
    token = secrets.token_urlsafe(32)
    monkeypatch.setenv("LASTLY_ACCESS_TOKEN", token)
    monkeypatch.setenv("LASTLY_DATA_KEY", base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
    client.headers["Authorization"] = f"Bearer {token}"
    monkeypatch.setattr(llm, "enabled", lambda: True)
    monkeypatch.setattr(llm, "complete_json", lambda *args, **kwargs: pytest.fail("Private request reached Anthropic"))
    monkeypatch.setattr(llm, "complete", lambda *args, **kwargs: pytest.fail("Private letter reached Anthropic"))
    headers = {"X-Lastly-Agent-Mode": "synthetic", "X-Lastly-Agent-Sender": "unknown"}
    assert client.get("/api/estate", headers=headers).status_code == 403
    assert client.post("/api/ask", json={"question": "Life insurance?"}, headers=headers).status_code == 403
    gym = account(client, "Planet Fitness")
    assert client.patch(f"/api/account/{gym['id']}", json={"status": "in_progress"}).status_code == 200
    assert client.post(f"/api/letter/{gym['id']}").status_code == 200
    assert client.post("/api/ask", json={"question": "Life insurance?"}).status_code == 200
    assert client.post(f"/api/call/{gym['id']}", json={"to_number": "+17345551234"}).status_code == 403


def test_optional_access_token(client, monkeypatch):
    code = secrets.token_urlsafe(32)
    monkeypatch.setenv("LASTLY_ACCESS_TOKEN", code)
    assert client.get("/api/estate").status_code == 401
    assert client.get("/api/estate", headers={"Authorization": f"Bearer {code}"}).status_code == 200
    assert client.get("/").status_code == 200


def test_demo_letters_and_questions_never_call_anthropic(client, monkeypatch):
    monkeypatch.setattr(llm, "enabled", lambda: True)
    monkeypatch.setattr(llm, "complete", lambda *args, **kwargs: pytest.fail("Demo letter invoked live AI"))
    monkeypatch.setattr(llm, "complete_json", lambda *args, **kwargs: pytest.fail("Demo question invoked live AI"))
    gym = account(client, "Planet Fitness")
    assert client.post(f"/api/letter/{gym['id']}?demo=1").status_code == 200
    assert "MetLife" in client.post("/api/ask?demo=1", json={"question": "Did Margaret have life insurance?"}).json()["answer"]


@pytest.mark.parametrize("question,expected,prefix", [
    ("What estate accounts were discovered from the emails?", "Lastly found", "msg_"),
    ("Which emails reference the executor or attorney handling the estate?", "executor", "msg_"),
    ("Show me the total balance across all estate bank accounts.", "Chase", "msg_"),
    ("Can you summarize all income and expenses?", "Social Security", "msg_"),
])
def test_offline_answers_cover_broad_questions_with_real_citations(client, question, expected, prefix):
    estate = client.get("/api/estate").json()
    response = client.post("/api/ask", json={"question": question})
    assert response.status_code == 200
    payload = response.json()
    assert expected in payload["answer"]
    assert payload["evidence_ids"] and all(eid.startswith(("msg_", "bank_")) for eid in payload["evidence_ids"])
    assert any(eid.startswith(prefix) for eid in payload["evidence_ids"])
    cited = {eid for account in estate["accounts"] for eid in account["evidence_ids"]}
    for eid in payload["evidence_ids"]:
        assert eid in cited or client.get(f"/api/{'email' if eid.startswith('msg_') else 'bank'}/{eid}").status_code == 200


def test_unusual_transactions_name_rows_without_exposing_non_account_proof(client):
    payload = client.post("/api/ask", json={"question": "Were there any unusual transactions, like large withdrawals?"}).json()
    assert "Statement period:" in payload["answer"] and "last 60 days" in payload["answer"] and "bank_" in payload["answer"]
    assert "no ATM, teller, transfer" in payload["answer"]
    assert payload["evidence_ids"] == []


def test_list_answers_name_everything_and_expand_on_request(client):
    estate = client.get("/api/estate").json()
    charging = [account["institution"] for account in estate["accounts"] if account["bucket"] == "leaving" and account["active"]]
    short = client.post("/api/ask", json={"question": "Which accounts are still charging?"}).json()["answer"]
    full = client.post("/api/ask", json={"question": "List all accounts that are still charging."}).json()["answer"]
    assert all(name in short for name in charging) and all(name in full for name in charging)
    assert "more:" not in full
    expenses = client.post("/api/ask", json={"question": "Summarize income and expenses."}).json()["answer"]
    assert all(name in expenses for name in charging)
