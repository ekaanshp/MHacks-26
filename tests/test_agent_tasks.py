"""Fetch.ai account conversations use real relay messages and company receipts."""
from types import SimpleNamespace

import pytest
from test_claims import INSURER, OTHER, relay  # noqa: F401
from test_security import http_client, login, secured, security_environment  # noqa: F401

import agent_tasks
import fetch_agent
import insurer_agent


def paramount(client):
    return next(a for a in client.get("/api/estate").json()["accounts"] if "Paramount" in a["institution"])


def test_subscription_agents_exchange_details_and_complete_without_reload(relay):  # noqa: F811
    client, csrf, _, agent = relay
    account = paramount(client)
    first = client.post(f"/api/agent-task/{account['id']}", headers={"X-CSRF-Token": csrf})
    assert first.status_code == 200
    task = first.json()
    assert task["status"] == "queued" and task["transcript"] == []
    again = client.post(f"/api/agent-task/{account['id']}", headers={"X-CSRF-Token": csrf})
    assert again.json()["id"] == task["id"]
    path = f"/api/agent/tasks/{task['id']}"
    client.cookies.clear()
    intro = client.get("/api/agent/tasks", headers=agent).json()["tasks"][0]
    assert intro["stage"] == "intro" and not intro["person_name"]
    intro_message = fetch_agent.task_message(intro)
    assert client.post(path, json={"status": "sent", "message": intro_message}, headers=agent).status_code == 200
    status, _, _, message = insurer_agent.review_task(SimpleNamespace(**intro))
    assert status == "awaiting_details"
    assert client.post(path, json={"status": status, "responder": OTHER, "message": message}, headers=agent).status_code == 403
    assert client.post(path, json={"status": status, "responder": INSURER, "message": message}, headers=agent).status_code == 200
    details = client.get("/api/agent/tasks", headers=agent).json()["tasks"][0]
    assert details["stage"] == "details" and details["person_name"]
    assert client.post(path, json={"status": "details_sent", "message": fetch_agent.task_message(details)}, headers=agent).status_code == 200
    status, reference, documents, message = insurer_agent.review_task(SimpleNamespace(**details))
    assert status == "completed" and reference and not documents
    result = client.post(path, json={"status": status, "responder": INSURER, "reference_number": reference, "message": message}, headers=agent)
    assert result.status_code == 200
    assert len(result.json()["transcript"]) == 4
    estate = client.get("/api/estate", headers=agent).json()
    assert next(a for a in estate["accounts"] if a["id"] == account["id"])["status"] == "done"
    # Repeat delivery is idempotent and cannot replace the company receipt.
    duplicate = client.post(path, json={"status": status, "responder": INSURER, "reference_number": "REF-OTHER"}, headers=agent)
    assert duplicate.json()["reference_number"] == reference
    assert client.post(path, json={"status": "rejected", "responder": INSURER}, headers=agent).status_code == 409


def test_family_and_agent_permissions_are_separate(relay):  # noqa: F811
    client, csrf, _, agent = relay
    account = paramount(client)
    assert client.get("/api/agent/tasks").status_code == 403
    client.cookies.clear()
    assert client.post(f"/api/agent-task/{account['id']}", headers=agent).status_code == 403
    assert client.patch(f"/api/account/{account['id']}", json={"status": "done"}, headers=agent).status_code == 403


def test_no_task_and_missing_configuration_are_clear(relay, monkeypatch):  # noqa: F811
    client, csrf, _, _ = relay
    account = paramount(client)
    assert client.get(f"/api/agent-task/{account['id']}").json() == {"status": None}
    monkeypatch.delenv("CLAIMS_AGENT_ADDRESS")
    monkeypatch.delenv("COMPANY_AGENT_ADDRESS", raising=False)
    response = client.post(f"/api/agent-task/{account['id']}", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 503 and "Fetch.ai" in response.json()["detail"]


def test_company_keeps_documents_pending_and_rejects_invalid_details():
    request = SimpleNamespace(request_id="b" * 32, stage="details", person_name="Margaret Ellis",
                              date_of_death="2026-09-12", institution="DTE", action="transfer", category="utility")
    status, reference, documents, _ = insurer_agent.review_task(request)
    assert status == "pending" and reference and documents
    request.date_of_death = "bad"
    assert insurer_agent.review_task(request)[0] == "rejected"


def test_task_does_not_accept_completion_with_outstanding_documents(monkeypatch, tmp_path):
    monkeypatch.setenv("LASTLY_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CLAIMS_AGENT_ADDRESS", INSURER)
    monkeypatch.setenv("LASTLY_AGENT_TOKEN", "x" * 40)
    persona = {"name": "Margaret", "date_of_death": "2026-09-12"}
    task = agent_tasks.create({"id": "p", "institution": "Paramount", "action": "cancel", "category": "subscription"}, persona, "Daniel")
    agent_tasks.update(task["id"], persona, "awaiting_details", responder=INSURER)
    with pytest.raises(ValueError, match="outstanding documents"):
        agent_tasks.update(task["id"], persona, "completed", responder=INSURER, required_documents=["Certificate"])


def test_fast_company_reply_preserves_transcript_order(monkeypatch, tmp_path):
    monkeypatch.setenv("LASTLY_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CLAIMS_AGENT_ADDRESS", INSURER)
    monkeypatch.setenv("LASTLY_AGENT_TOKEN", "x" * 40)
    persona = {"name": "Margaret", "date_of_death": "2026-09-12"}
    task = agent_tasks.create({"id": "p", "institution": "Paramount", "action": "cancel", "category": "subscription"}, persona, "Daniel")
    agent_tasks.update(task["id"], persona, "awaiting_details", responder=INSURER, message="Please send details.")
    agent_tasks.update(task["id"], persona, "sent", message="Hello.")
    agent_tasks.update(task["id"], persona, "completed", responder=INSURER, message="Cancelled.", reference_number="REF-123")
    result = agent_tasks.update(task["id"], persona, "details_sent", message="Here are the details.")
    assert result["status"] == "completed"
    assert [line["message"] for line in result["transcript"]] == ["Hello.", "Please send details.", "Here are the details.", "Cancelled."]
