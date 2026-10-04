"""Sponsor setup commands configure providers without placing calls or leaking secrets."""
import re
from types import SimpleNamespace

import pytest

import calls
import integrations


def settings(**overrides):
    values = {"elevenlabs_api_key": "key", "elevenlabs_agent_id": "", "elevenlabs_phone_number_id": "",
              "offline": False, "access_token": "x" * 32, "database_url": "", "agent_seed": "", "agent_token": ""}
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture
def provider(monkeypatch):
    requests = []
    responses = {}

    def fake(method, path, *, payload=None):
        requests.append((method, path, payload))
        return responses.get((method, path), {})

    monkeypatch.setattr(integrations, "_eleven", fake)
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC123")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "secret")
    return requests, responses


def test_setup_creates_agent_with_disclosure_and_every_dynamic_variable(monkeypatch, provider):
    requests, responses = provider
    monkeypatch.setattr(integrations, "get_settings", lambda: settings())
    responses[("POST", "/agents/create")] = {"agent_id": "agent_new"}
    assert integrations.elevenlabs_setup() == ["ELEVENLABS_AGENT_ID=agent_new"]
    method, path, body = requests[0]
    agent = body["conversation_config"]["agent"]
    assert (method, path) == ("POST", "/agents/create")
    assert agent["prompt"]["prompt"] == calls.AGENT_PROMPT and "AI" in agent["first_message"]
    assert agent["prompt"]["built_in_tools"]["end_call"]["params"] == {"system_tool_type": "end_call"}
    used = set(re.findall(r"{{(\w+)}}", calls.AGENT_PROMPT + calls.FIRST_MESSAGE))
    assert used <= set(agent["dynamic_variables"]["dynamic_variable_placeholders"])
    assert not any("call" in path and "outbound" in path for _, path, _ in requests)


def test_setup_updates_existing_agent_and_imports_then_assigns_twilio_number(monkeypatch, provider):
    requests, responses = provider
    monkeypatch.setattr(integrations, "get_settings", lambda: settings(elevenlabs_agent_id="agent_1"))
    responses[("GET", "/phone-numbers")] = []
    responses[("POST", "/phone-numbers")] = {"phone_number_id": "phone_1"}
    lines = integrations.elevenlabs_setup(voice_id="voice_1", twilio_number="+17345550100")
    assert lines == ["ELEVENLABS_AGENT_ID=agent_1", "ELEVENLABS_PHONE_NUMBER_ID=phone_1"]
    assert requests[0][:2] == ("PATCH", "/agents/agent_1")
    assert requests[0][2]["conversation_config"]["tts"] == {"voice_id": "voice_1"}
    imported = next(body for method, path, body in requests if (method, path) == ("POST", "/phone-numbers"))
    assert imported == {"provider": "twilio", "phone_number": "+17345550100", "label": "Lastly", "sid": "AC123", "token": "secret"}
    assert requests[-1] == ("PATCH", "/phone-numbers/phone_1", {"agent_id": "agent_1"})


def test_setup_reuses_an_already_imported_number(monkeypatch, provider):
    requests, responses = provider
    monkeypatch.setattr(integrations, "get_settings", lambda: settings(elevenlabs_agent_id="agent_1"))
    responses[("GET", "/phone-numbers")] = [{"phone_number": "+17345550100", "phone_number_id": "phone_9"}]
    integrations.elevenlabs_setup(twilio_number="+17345550100")
    assert ("POST", "/phone-numbers") not in {(method, path) for method, path, _ in requests}
    assert requests[-1] == ("PATCH", "/phone-numbers/phone_9", {"agent_id": "agent_1"})


def test_setup_rejects_invalid_number_before_contacting_twilio(monkeypatch, provider):
    requests, _ = provider
    monkeypatch.setattr(integrations, "get_settings", lambda: settings(elevenlabs_agent_id="agent_1"))
    with pytest.raises(integrations.SetupError):
        integrations.elevenlabs_setup(twilio_number="734-555-0100")
    assert all(path != "/phone-numbers" for _, path, _ in requests)


def test_status_flags_unassigned_number_and_blocked_destinations(monkeypatch, provider):
    _, responses = provider
    monkeypatch.delenv("LASTLY_ALLOWED_CALL_NUMBERS", raising=False)
    monkeypatch.setattr(integrations, "get_settings", lambda: settings(elevenlabs_agent_id="agent_1", elevenlabs_phone_number_id="phone_1"))
    responses[("GET", "/agents/agent_1")] = {"conversation_config": {"agent": {"prompt": {"prompt": calls.AGENT_PROMPT}}}}
    responses[("GET", "/phone-numbers")] = [{"phone_number_id": "phone_1", "phone_number": "+1734", "assigned_agent": {"agent_id": "other"}}]
    ok, message = integrations.elevenlabs_status()
    assert ok is False and "not assigned" in message and "LASTLY_ALLOWED_CALL_NUMBERS" in message
    monkeypatch.setenv("LASTLY_ALLOWED_CALL_NUMBERS", "+17345550100")
    responses[("GET", "/phone-numbers")][0]["assigned_agent"] = {"agent_id": "agent_1"}
    assert integrations.elevenlabs_status()[0] is True


def test_browser_only_mode_needs_no_phone_number(monkeypatch, provider):
    requests, responses = provider
    monkeypatch.delenv("LASTLY_ALLOWED_CALL_NUMBERS", raising=False)
    monkeypatch.setattr(integrations, "get_settings", lambda: settings(elevenlabs_agent_id="agent_1"))
    responses[("GET", "/agents/agent_1")] = {"conversation_config": {"agent": {"prompt": {"prompt": calls.AGENT_PROMPT}}}}
    ok, message = integrations.elevenlabs_status()
    assert ok is True and "in-browser" in message
    assert ("GET", "/phone-numbers") not in {(method, path) for method, path, _ in requests}


def test_unconfigured_integrations_report_off(monkeypatch):
    monkeypatch.setattr(integrations, "get_settings", lambda: settings(elevenlabs_api_key=""))
    assert integrations.neon_status()[0] is None
    assert integrations.elevenlabs_status()[0] is None
    assert integrations.fetch_status()[0] is None


def test_neon_errors_never_echo_the_connection_string():
    error = RuntimeError("bad postgresql://user:hunter2@host/db?sslmode=require failed")
    assert "hunter2" not in integrations._short(error)
