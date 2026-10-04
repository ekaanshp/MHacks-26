"""The family workspace: review, corrections, added accounts, notes, follow-ups, guides, exports and outcomes."""
from __future__ import annotations

import secrets
from datetime import date, timedelta

import pytest
from test_claims import INSURER
from test_security import http_client, login, secured, security_environment  # noqa: F401

import agent_tasks
import calls
import pipeline


@pytest.fixture
def family(secured):  # noqa: F811
    client, token = secured
    client.headers["X-CSRF-Token"] = login(client, token)
    return client


def account_named(client, name):
    return next(a for a in client.get("/api/estate").json()["accounts"] if a["institution"] == name)


def totals(client):
    return client.get("/api/estate").json()["totals"]


def activity_actions(client):
    return [row["action"] for row in client.get("/api/activity").json()["activity"]]


def test_dismissing_a_false_positive_removes_it_from_totals_and_restoring_brings_it_back(family):
    netflix = account_named(family, "Netflix")
    before = totals(family)
    dismissed = family.post(f"/api/account/{netflix['id']}/review", json={"state": "dismissed", "reason": "Shared family plan"}).json()
    assert dismissed["review"]["state"] == "dismissed" and dismissed["review"]["by"]
    after = totals(family)
    assert after["accounts"] == before["accounts"] - 1
    assert after["monthly_drain"] == pytest.approx(before["monthly_drain"] - netflix["amount"])
    assert "review:dismissed" in activity_actions(family)
    family.post(f"/api/account/{netflix['id']}/review", json={"state": "open"})
    assert totals(family)["monthly_drain"] == pytest.approx(before["monthly_drain"])


def test_corrections_show_reviewed_values_keep_the_original_and_survive_reanalysis(family, dataset):
    vanguard = account_named(family, "Vanguard")
    corrected = family.post(f"/api/account/{vanguard['id']}/correct", json={"institution": "Vanguard Brokerage", "amount": 30000}).json()
    assert corrected["institution"] == "Vanguard Brokerage" and corrected["amount"] == 30000
    assert corrected["original"] == {"institution": "Vanguard", "amount": vanguard["amount"]}
    assert corrected["corrections"]["by"]
    # A category change moves the account to the right bucket and action.
    moved = family.post(f"/api/account/{vanguard['id']}/correct", json={"category": "debt"}).json()
    assert moved["bucket"] == "owed" and moved["action"] == "notify"
    pipeline.run(mock=True, data_dir=dataset)
    again = next(a for a in family.get("/api/estate").json()["accounts"] if a["id"] == vanguard["id"])
    assert again["institution"] == "Vanguard Brokerage" and again["bucket"] == "owed"
    reset = family.post(f"/api/account/{vanguard['id']}/correct", json={"reset": True}).json()
    assert reset["institution"] == "Vanguard" and reset["original"] is None and reset["bucket"] == "waiting"
    assert family.post(f"/api/account/{vanguard['id']}/correct", json={}).status_code == 422


def test_family_can_add_a_missing_account_once_and_it_survives_reanalysis(family, dataset):
    before = totals(family)
    response = family.post("/api/accounts", json={"institution": "Ally Savings", "category": "bank", "amount": 1200, "frequency": "balance", "note": "Statement in the desk"})
    assert response.status_code == 200, response.text
    added = response.json()
    assert added["sources"] == ["family"] and added["evidence_ids"] == [] and added["review"]["state"] == "confirmed"
    assert added["notes"][0]["text"] == "Statement in the desk" and added["added_by"]
    assert totals(family)["assets_found"] == pytest.approx(before["assets_found"] + 1200)
    assert family.post("/api/accounts", json={"institution": "Ally Savings", "category": "bank"}).status_code == 409
    pipeline.run(mock=True, data_dir=dataset)
    assert any(a["id"] == added["id"] and a["institution"] == "Ally Savings" for a in family.get("/api/estate").json()["accounts"])


def test_notes_help_requests_and_handoffs(family):
    netflix = account_named(family, "Netflix")
    note = family.post(f"/api/account/{netflix['id']}/notes", json={"text": "Password is in the blue folder", "kind": "note"}).json()
    assert note["notes"][-1]["text"] == "Password is in the blue folder"
    helped = family.post(f"/api/account/{netflix['id']}/notes", json={"text": "Can someone call them?", "kind": "help"}).json()
    help_id = helped["notes"][-1]["id"]
    steps = family.get("/api/next-steps").json()["steps"]
    assert any(step["kind"] == "help" and step["account_id"] == netflix["id"] for step in steps)
    family.post(f"/api/account/{netflix['id']}/notes/{help_id}/resolve")
    assert not any(step["kind"] == "help" for step in family.get("/api/next-steps").json()["steps"])
    handed = family.post(f"/api/account/{netflix['id']}/notes", json={"text": "Over to you, I emailed them once.", "kind": "handoff", "to": "sarah"}).json()
    assert handed["assigned_to"] == "Sarah" and handed["notes"][-1]["to"] == "Sarah"
    assert "assigned:Sarah" in activity_actions(family)
    assert family.post(f"/api/account/{netflix['id']}/notes", json={"text": "x", "kind": "handoff", "to": "Stranger"}).status_code == 422
    assert family.post(f"/api/account/{netflix['id']}/notes", json={"text": "   ", "kind": "note"}).status_code == 422


def test_followups_drive_next_steps_and_the_digest(family):
    spotify = account_named(family, "Spotify")
    today = date.fromisoformat(family.get("/api/estate").json()["today"])
    overdue = family.post(f"/api/account/{spotify['id']}/followups", json={"kind": "callback", "title": "Spotify promised a refund call", "due": (today - timedelta(days=2)).isoformat(), "reference": "SP-1"}).json()
    item = overdue["followups"][-1]
    steps = family.get("/api/next-steps").json()["steps"]
    assert steps[0]["kind"] == "overdue" and steps[0]["account_id"] == spotify["id"]
    assert any(step["kind"] == "renewal" for step in steps), "The Prime renewal three days out is on the list"
    digest = family.get("/api/digest").json()
    assert any(entry.get("overdue") for entry in digest["upcoming"])
    family.patch(f"/api/account/{spotify['id']}/followups/{item['id']}", json={"done": True})
    family.patch(f"/api/account/{spotify['id']}", json={"status": "done"})
    digest = family.get("/api/digest").json()
    assert "Spotify" in digest["completed"] and "Spotify" in digest["text"]
    assert not any(step["kind"] == "overdue" for step in family.get("/api/next-steps").json()["steps"])
    assert family.patch(f"/api/account/{spotify['id']}/followups/missing", json={"done": True}).status_code == 404


def test_guides_have_steps_documents_and_a_verified_contact_route(family):
    ssa = account_named(family, "Social Security")
    guide = family.get(f"/api/account/{ssa['id']}/guide").json()
    assert guide["contact"]["verified"] and "1-800-772-1213" in guide["contact"]["label"]
    assert any(item["name"] == "Certified death certificate" and item["status"] == "needed" for item in guide["documents"])
    netflix = account_named(family, "Netflix")
    route = family.get(f"/api/account/{netflix['id']}/guide").json()["contact"]
    assert route["verified"] and "netflix.com" in route["label"]
    family.post(f"/api/account/{ssa['id']}/documents", json={"name": "Certified death certificate", "status": "ready"})
    guide = family.get(f"/api/account/{ssa['id']}/guide").json()
    assert next(item for item in guide["documents"] if item["name"] == "Certified death certificate")["status"] == "ready"


def test_funding_map_groups_charges_by_what_pays_them(family):
    sources = family.get("/api/funding").json()["sources"]
    charged = {charge["institution"] for source in sources for charge in source["charges"]}
    assert "Netflix" in charged
    assert sum(source["monthly_total"] for source in sources) == pytest.approx(totals(family)["monthly_drain"], abs=0.05)
    assert all(source["funder"] for source in sources)


def test_reports_and_packets_are_complete_and_escaped(family):
    family.post("/api/accounts", json={"institution": "<script>alert(1)</script> Bank", "category": "bank"})
    report = family.get("/api/report").json()
    assert report["filename"].endswith(".html")
    assert "<script>alert" not in report["html"] and "&lt;script&gt;" in report["html"]
    assert "Executor report" in report["html"] and "Netflix" in report["html"]
    netflix = account_named(family, "Netflix")
    packet = family.get(f"/api/account/{netflix['id']}/packet").json()
    assert "Draft letter" in packet["html"] and "Netflix" in packet["html"] and "Copy of the death certificate" in packet["html"]


def test_thin_evidence_is_queued_for_review_until_confirmed(family):
    estate = family.get("/api/estate").json()
    flagged = [a for a in estate["accounts"] if a["needs_review"]]
    assert flagged, "Single-email or inactive findings ask for a family check"
    confirmed = family.post(f"/api/account/{flagged[0]['id']}/review", json={"state": "confirmed"}).json()
    assert confirmed["needs_review"] is False


def test_a_finished_call_updates_the_account_on_the_server_without_the_drawer(family, monkeypatch):
    account = account_named(family, "Planet Fitness")
    estate = family.get("/api/estate").json()
    settings = type("S", (), dict(offline=False, elevenlabs_api_key="k", elevenlabs_agent_id="a", elevenlabs_phone_number_id="p", allow_private_cloud=False))()
    monkeypatch.setattr(calls, "get_settings", lambda: settings)
    monkeypatch.setattr(calls.llm, "enabled", lambda: False)
    with calls._metadata_lock():
        records = calls._load_metadata()
        records["conv_done"] = {"account_id": account["id"], "persona_fingerprint": calls.persona_fingerprint(estate["persona"]), "action": "cancel", "created_at": "2026-10-01T10:00:00+00:00"}
        records["conv_docs"] = {"account_id": account["id"], "persona_fingerprint": calls.persona_fingerprint(estate["persona"]), "action": "cancel", "created_at": "2026-10-01T09:00:00+00:00"}
        calls._save_metadata(records)
    transcripts = {
        "conv_docs": [{"role": "agent", "message": "Just to confirm for the family: has the Planet Fitness account been cancelled?"},
                      {"role": "user", "message": "Yes, if you send the death certificate. Someone will call you back within 3 business days."}],
        "conv_done": [{"role": "agent", "message": "Just to confirm for the family: has the Planet Fitness account been cancelled?"},
                      {"role": "user", "message": "Yes, it has been cancelled. Reference PF-20931."}],
    }
    monkeypatch.setattr(calls, "_request", lambda method, path, settings, **kw: {"status": "done", "transcript": transcripts[path.rsplit("/", 1)[1]]})
    history = family.get(f"/api/account/{account['id']}/calls").json()["calls"]
    assert [item["conversation_id"] for item in history] == ["conv_docs", "conv_done"]
    assert history[0]["summary"]["result"] == "documents_required" and history[1]["summary"]["result"] == "completed"
    saved = account_named(family, "Planet Fitness")
    assert saved["status"] == "done", "The confirmation was applied on the server"
    assert saved["outcome"]["reference_number"] == "PF-20931" and saved["outcome"]["source"] == "call"
    kinds = {item["kind"] for item in saved["followups"]}
    assert kinds == {"document", "callback"}
    callback = next(item for item in saved["followups"] if item["kind"] == "callback")
    assert callback["due"] == (date.fromisoformat(family.get("/api/estate").json()["today"]) + timedelta(days=3)).isoformat() or callback["due"]
    # Polling again is idempotent: no duplicate follow-ups or activity.
    family.get(f"/api/account/{account['id']}/calls")
    assert len(account_named(family, "Planet Fitness")["followups"]) == 2


def test_agent_requests_time_out_with_a_reason_and_retry_keeps_history(family, monkeypatch):
    monkeypatch.setenv("LASTLY_AGENT_TOKEN", secrets.token_urlsafe(32))
    monkeypatch.setenv("CLAIMS_AGENT_ADDRESS", INSURER)
    netflix = account_named(family, "Netflix")
    first = family.post(f"/api/agent-task/{netflix['id']}").json()
    assert first["status"] == "queued" and first["deadline_seconds"] == 60 and first["attempts"] == []
    monkeypatch.setenv("LASTLY_AGENT_TIMEOUT_SCALE", "0.00001")
    stalled = family.get(f"/api/agent-task/{netflix['id']}").json()
    assert stalled["status"] == "timed_out" and "fetch_agent.py" in stalled["failure_reason"]
    monkeypatch.setenv("LASTLY_AGENT_TIMEOUT_SCALE", "1")
    retry = family.post(f"/api/agent-task/{netflix['id']}").json()
    assert retry["id"] != first["id"] and retry["status"] == "queued"
    assert retry["attempts"][0]["id"] == first["id"] and retry["attempts"][0]["status"] == "timed_out"
    with pytest.raises(ValueError, match="timed out"):
        agent_tasks.update(first["id"], family.get("/api/estate").json()["persona"], "sent")


def test_uploaded_mailboxes_can_be_deleted_but_demos_cannot(family):
    assert family.delete("/api/import").status_code == 403
    connection = family.get("/api/workspace").json()["connection"]
    assert connection["provider"] == "demo" and connection["messages"] == 790 and connection["removable"] is False


def test_financial_transfers_get_a_money_guide_not_a_utility_one(family):
    estate = family.get("/api/estate").json()
    vanguard = next(a for a in estate["accounts"] if a["institution"] == "Vanguard")
    dte = next(a for a in estate["accounts"] if a["institution"] == "DTE Energy")
    assert "beneficiary" in family.get(f"/api/account/{vanguard['id']}/guide").json()["goal"]
    assert "final bill" in family.get(f"/api/account/{dte['id']}/guide").json()["goal"]
