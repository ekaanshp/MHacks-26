"""In-browser ElevenLabs conversations: no key in the browser, only Lastly's agent, family access only."""
from __future__ import annotations

import pytest
from test_security import http_client, login, secured, security_environment  # noqa: F401  (fixtures)

import calls

SIGNED = "wss://api.elevenlabs.io/v1/convai/conversation?agent_id=agent_test&conversation_signature=sig"


@pytest.fixture
def account():
    return {"id": "acct_09", "institution": "Planet Fitness", "category": "subscription", "action": "cancel"}


@pytest.fixture
def persona():
    return {"name": "Margaret Wilson", "email": "margaret@example.com", "date_of_death": "2026-09-18"}


@pytest.fixture
def voice_settings(monkeypatch, tmp_path):
    from types import SimpleNamespace

    settings = SimpleNamespace(offline=False, elevenlabs_api_key="test-key", elevenlabs_agent_id="agent_test",
                               elevenlabs_phone_number_id="", family_executor="Daniel")
    monkeypatch.setattr(calls, "get_settings", lambda: settings)
    monkeypatch.setenv("LASTLY_DATA_DIR", str(tmp_path))
    return settings


def test_session_returns_signed_url_and_estate_variables(voice_settings, monkeypatch, account, persona):
    requested = []
    monkeypatch.setattr(calls, "_request", lambda method, path, settings, **kwargs: requested.append((method, path)) or {"signed_url": SIGNED})
    session = calls.start_browser_session(account, persona)
    assert requested == [("GET", "/conversation/get-signed-url?agent_id=agent_test")]
    assert session["signed_url"] == SIGNED
    assert session["dynamic_variables"] == {
        "person_name": "Margaret Wilson", "date_of_death": "2026-09-18", "institution": "Planet Fitness",
        "action": "cancel", "category": "subscription", "executor_name": "Daniel",
    }


@pytest.mark.parametrize("url", ["wss://evil.example/v1/convai", "https://api.elevenlabs.io/v1", None])
def test_unexpected_signed_url_host_is_refused(voice_settings, monkeypatch, account, persona, url):
    monkeypatch.setattr(calls, "_request", lambda *args, **kwargs: {"signed_url": url})
    with pytest.raises(calls.IntegrationError):
        calls.start_browser_session(account, persona)


def test_offline_or_unconfigured_never_contacts_provider(voice_settings, monkeypatch, account, persona):
    monkeypatch.setattr(calls, "_request", lambda *args, **kwargs: pytest.fail("Offline voice reached provider"))
    voice_settings.offline = True
    with pytest.raises(calls.IntegrationNotConfigured):
        calls.start_browser_session(account, persona)
    voice_settings.offline, voice_settings.elevenlabs_agent_id = False, ""
    with pytest.raises(calls.IntegrationNotConfigured):
        calls.start_browser_session(account, persona)


def test_only_lastly_agent_conversations_are_tracked(voice_settings, monkeypatch, account, persona):
    monkeypatch.setattr(calls, "_request", lambda *args, **kwargs: {"agent_id": "someone_else"})
    with pytest.raises(ValueError):
        calls.register_browser_conversation("conv_1", account, persona)
    assert calls.call_metadata("conv_1") is None
    monkeypatch.setattr(calls, "_request", lambda *args, **kwargs: {"agent_id": "agent_test"})
    assert calls.register_browser_conversation("conv_1", account, persona) == {"success": True, "conversation_id": "conv_1"}
    record = calls.call_metadata("conv_1")
    assert record["account_id"] == "acct_09" and record["mode"] == "browser" and record["action"] == "cancel"
    # Registering again is idempotent, but the conversation cannot be moved to another account.
    calls.register_browser_conversation("conv_1", account, persona)
    with pytest.raises(ValueError):
        calls.register_browser_conversation("conv_1", {**account, "id": "acct_01"}, persona)


def test_browser_conversation_outcome_uses_company_confirmation(voice_settings, monkeypatch, account, persona):
    monkeypatch.setattr(calls, "_request", lambda *args, **kwargs: {"agent_id": "agent_test"})
    calls.register_browser_conversation("conv_2", account, persona)
    transcript = [
        {"role": "agent", "message": "I am an AI calling on behalf of the family. Is the membership cancelled?"},
        {"role": "user", "message": "Yes, the membership has been cancelled. Your reference number is PF-20931."},
    ]
    monkeypatch.setattr(calls, "_request", lambda *args, **kwargs: {"status": "done", "transcript": transcript})
    monkeypatch.setattr(calls.llm, "enabled", lambda: False)
    result = calls.get_call("conv_2")
    assert result["summary"]["cancelled"] is True and result["summary"]["reference_number"] == "PF-20931"


@pytest.fixture
def live_voice(secured, monkeypatch):  # noqa: F811
    client, token = secured
    monkeypatch.setenv("LASTLY_OFFLINE", "false")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_secret_value_never_sent")
    monkeypatch.setenv("ELEVENLABS_AGENT_ID", "agent_test")
    monkeypatch.setattr(calls, "_request", lambda method, path, settings, **kwargs: {"signed_url": SIGNED} if "signed-url" in path else {"agent_id": "agent_test"})
    return client, token


def planet_fitness(client):
    return next(item["id"] for item in client.get("/api/estate").json()["accounts"] if item["institution"] == "Planet Fitness")


def test_voice_endpoint_requires_family_unlock_and_never_exposes_key(live_voice):
    client, token = live_voice
    assert client.post("/api/voice/acct_00").status_code == 401
    csrf = login(client, token)
    acct = planet_fitness(client)
    response = client.post(f"/api/voice/{acct}", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200, response.text
    assert response.json()["signed_url"] == SIGNED
    assert "sk_secret_value_never_sent" not in response.text
    finished = client.post(f"/api/voice/{acct}/conversation", json={"conversation_id": "conv_9"}, headers={"X-CSRF-Token": csrf})
    assert finished.status_code == 200 and calls.call_metadata("conv_9")["account_id"] == acct


def test_voice_endpoint_rejects_malformed_conversation_ids(live_voice):
    client, token = live_voice
    csrf = login(client, token)
    acct = planet_fitness(client)
    response = client.post(f"/api/voice/{acct}/conversation", json={"conversation_id": "../etc"}, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 422


def test_page_policy_allows_microphone_and_elevenlabs_socket_only(http_client):  # noqa: F811
    headers = http_client.get("/").headers
    assert "microphone=(self)" in headers["permissions-policy"] and "camera=()" in headers["permissions-policy"]
    assert "connect-src 'self' wss://api.elevenlabs.io;" in headers["content-security-policy"]
    assert "script-src 'self';" in headers["content-security-policy"]


@pytest.mark.parametrize("line", [
    "I don't need any documents from you now. I've canceled the membership, and there'll be no further charges. Your reference number is PF-20931.",
    "We\u2019ve cancelled the membership. Reference PF-20931.",
    "I cancelled it just now, your confirmation number is PF-20931.",
    "I've gone ahead and cancelled the membership. Reference number PF-20931.",
])
def test_contracted_company_confirmation_counts_as_cancelled(line):
    transcript = [{"role": "agent", "message": "Hello, I am Lastly, an AI assistant."},
                  {"role": "user", "message": line},
                  {"role": "user", "message": "That'll be it. So sorry for your loss, and thank you for calling Planet Fitness."}]
    summary = calls.summarize_transcript(transcript)
    assert summary["cancelled"] is True and summary["reference_number"] == "PF-20931"


@pytest.mark.parametrize("line", ["I haven't cancelled it yet.", "We can cancel once we receive the death certificate.", "I've not cancelled the membership."])
def test_negative_or_conditional_lines_never_count(line):
    assert calls.summarize_transcript([{"role": "user", "message": line}])["cancelled"] is False


def test_identity_questions_are_not_family_next_steps_but_documents_are():
    transcript = [
        {"role": "user", "message": "So, I just need the member's full name and their date of death."},
        {"role": "user", "message": "Please email a copy of the death certificate for our records."},
        {"role": "user", "message": "I've canceled the membership now. Your reference number is PF-20931."},
    ]
    summary = calls.summarize_transcript(transcript)
    assert summary["cancelled"] is True
    assert summary["next_steps"] == ["Please email a copy of the death certificate for our records."]
