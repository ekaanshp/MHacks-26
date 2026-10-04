"""Integration contract tests never reach an external provider or place a call."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

import calls
import fetch_agent
import letters


@pytest.fixture
def account():
    return {"id": "acct_09", "institution": "Planet Fitness", "category": "subscription", "action": "cancel"}


@pytest.fixture
def persona():
    return {"name": "Margaret Wilson", "email": "margaret@example.com", "date_of_death": "2026-09-18"}


@pytest.fixture
def call_settings(monkeypatch, tmp_path):
    settings = SimpleNamespace(offline=False, elevenlabs_api_key="test-key", elevenlabs_agent_id="test-agent",
                               elevenlabs_phone_number_id="test-phone", family_executor="Daniel")
    monkeypatch.setattr(calls, "get_settings", lambda: settings)
    monkeypatch.setattr(calls, "DATA_DIR", tmp_path)
    monkeypatch.setenv("LASTLY_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(calls.llm, "enabled", lambda: False)
    return settings


@pytest.mark.parametrize("action", ["cancel", "transfer", "claim", "notify", "memorialize"])
def test_letter_actions_have_correct_facts_and_placeholders(account, persona, action):
    text = letters.generate_letter({**account, "action": action}, persona)
    assert len(text.split()) < 180
    assert persona["name"] in text and persona["date_of_death"] in text
    assert account["institution"] in text
    for field in ("name", "address", "email", "phone"):
        assert f"[Executor {field}]" in text
    assert "attached" not in text.lower()
    assert letters._valid(text, {**account, "action": action}, persona)


def test_invalid_llm_letter_falls_back(monkeypatch, account, persona):
    monkeypatch.setattr(letters.llm, "enabled", lambda: True)
    monkeypatch.setattr(letters.llm, "complete", lambda *args, **kwargs: "Account number 99887766 has been cancelled.")
    text = letters.generate_letter(account, persona, use_llm=True)
    assert persona["name"] in text
    assert "99887766" not in text


def test_disabled_calls_do_not_contact_provider(call_settings, monkeypatch, account, persona):
    call_settings.offline = True
    monkeypatch.setattr(calls, "_request", lambda *args, **kwargs: pytest.fail("Offline call reached provider"))
    with pytest.raises(calls.IntegrationNotConfigured):
        calls.place_call(account, persona, "+17345551234")


@pytest.mark.parametrize("number", ["7345551234", "+0123456789", "+1734", "+1734 555 1234", "tel:+17345551234"])
def test_invalid_number_rejected_before_network(call_settings, monkeypatch, account, persona, number):
    monkeypatch.setattr(calls, "_request", lambda *args, **kwargs: pytest.fail("Invalid number reached provider"))
    with pytest.raises(ValueError):
        calls.place_call(account, persona, number)


def test_outbound_payload_and_tracking(call_settings, monkeypatch, account, persona):
    requests = []
    def request(method, path, settings, *, payload=None):
        requests.append((method, path, payload))
        return {"success": True, "conversation_id": "conv_test", "callSid": "CA_test"}
    monkeypatch.setattr(calls, "_request", request)
    response = calls.place_call(account, persona, "+17345551234")
    assert response == {"success": True, "conversation_id": "conv_test", "callSid": "CA_test"}
    method, path, payload = requests[0]
    assert method == "POST" and path == "/twilio/outbound-call"
    variables = payload["conversation_initiation_client_data"]["dynamic_variables"]
    assert variables["person_name"] == persona["name"]
    assert variables["date_of_death"] == persona["date_of_death"]
    assert variables["institution"] == account["institution"]
    assert "AI" in calls.FIRST_MESSAGE
    metadata = calls.call_metadata("conv_test")
    assert metadata["account_id"] == account["id"]
    assert metadata["persona_fingerprint"] == calls.persona_fingerprint(persona)


def test_unknown_call_cannot_fetch_other_transcripts(call_settings, monkeypatch):
    monkeypatch.setattr(calls, "_request", lambda *args, **kwargs: pytest.fail("Unknown call reached provider"))
    with pytest.raises(ValueError, match="not placed"):
        calls.get_call("conv_unknown")


def test_call_metadata_directory_follows_runtime_environment(call_settings, monkeypatch, tmp_path, account, persona):
    alternate = tmp_path / "second-estate"
    monkeypatch.setenv("LASTLY_DATA_DIR", str(alternate))
    monkeypatch.setattr(calls, "_request", lambda *args, **kwargs: {"success": True, "conversation_id": "conv_test", "callSid": "CA_test"})
    calls.place_call(account, persona, "+17345551234")
    assert (alternate / "runtime_calls.json").exists()
    monkeypatch.setenv("LASTLY_DATA_DIR", str(tmp_path / "other-estate"))
    assert calls.call_metadata("conv_test") is None


def test_transcript_not_sent_to_anthropic_without_cloud_consent(call_settings, monkeypatch):
    monkeypatch.setattr(calls.llm, "enabled", lambda: True)
    monkeypatch.setattr(calls.llm, "complete_json", lambda *args, **kwargs: pytest.fail("Transcript sent without cloud consent"))
    result = calls.summarize_transcript([{ "role": "user", "message": "Your membership is now cancelled."}])
    assert result["cancelled"] is True


@pytest.mark.parametrize("company_message", [
    "We cannot cancel until we receive the death certificate. Your reference is PF-20931.",
    "We will cancel once you email the certificate. Your reference number is PF-20931.",
    "Your membership is not yet cancelled. Your reference number is PF-20931.",
    "Your membership is cancelled, pending receipt of a death certificate. Your reference number is PF-20931.",
])
def test_document_or_future_condition_does_not_mark_done(call_settings, company_message):
    summary = calls.summarize_transcript([
        {"role": "agent", "message": "I have cancelled the membership. Reference PF-00000."},
        {"role": "user", "message": company_message},
    ])
    assert summary["cancelled"] is False
    assert summary["reference_number"] == "PF-20931"


def test_company_confirmation_completes_after_document_for_records(call_settings):
    transcript = [
        {"role": "user", "message": "Please send the certificate for our records."},
        {"role": "user", "message": "I have cancelled the membership now. Your reference number is PF-20931."},
    ]
    assert calls.summarize_transcript(transcript)["cancelled"] is True
    assert calls.summarize_transcript(transcript, completed=False)["cancelled"] is False


def test_completed_call_result_matches_company_confirmation(call_settings, monkeypatch, account, persona):
    monkeypatch.setattr(calls, "_request", lambda *args, **kwargs: {"success": True, "conversation_id": "conv_test", "callSid": "CA_test"})
    calls.place_call(account, persona, "+17345551234")
    transcript = [{"role": "user", "message": "Your membership is now cancelled. Your reference number is PF-20931."}]
    monkeypatch.setattr(calls, "_request", lambda *args, **kwargs: {"status": "done", "transcript": transcript})
    result = calls.get_call("conv_test")
    assert result["status"] == "done"
    assert result["summary"]["cancelled"] is True
    assert result["summary"]["reference_number"] == "PF-20931"
    assert "PF-20931" in result["transcript_summary"]
    assert result["account_id"] == account["id"]


def test_non_cancellation_action_cannot_auto_complete_as_cancelled(call_settings, monkeypatch, account, persona):
    monkeypatch.setattr(calls, "_request", lambda *args, **kwargs: {"success": True, "conversation_id": "conv_test", "callSid": "CA_test"})
    calls.place_call({**account, "action": "notify"}, persona, "+17345551234")
    monkeypatch.setattr(calls, "_request", lambda *args, **kwargs: {"status": "done", "transcript": [
        {"role": "user", "message": "Your membership is now cancelled."},
    ]})
    assert calls.get_call("conv_test")["summary"]["cancelled"] is False


def test_agent_alone_never_proves_cancellation(call_settings):
    summary = calls.summarize_transcript([{ "role": "agent", "message": "The membership is cancelled. Reference number PF-20931."}])
    assert summary == {"cancelled": False, "reference_number": None, "next_steps": []}


def test_fetch_seed_required_without_installing_uagents(monkeypatch):
    monkeypatch.setattr(fetch_agent, "get_settings", lambda: SimpleNamespace(agent_seed=""))
    with pytest.raises(ValueError, match="AGENT_SEED"):
        fetch_agent.build_agent()


@pytest.mark.parametrize("synthetic, expected_queries", [(True, 1), (False, 0)])
def test_public_agent_only_queries_synthetic_estate(monkeypatch, synthetic, expected_queries):
    monkeypatch.delenv("FETCH_ALLOWED_SENDERS", raising=False)
    settings = SimpleNamespace(allow_private_cloud=False, access_token="", agent_token="scoped-agent-secret", api_base_url="http://127.0.0.1:8000")
    monkeypatch.setattr(fetch_agent, "get_settings", lambda: settings)
    requests = []
    def handle(request):
        requests.append(request)
        if request.url.path == "/api/estate":
            return httpx.Response(200, json={"analysis": {"synthetic": synthetic}})
        return httpx.Response(200, json={"answer": "MetLife policy found.", "evidence_ids": ["msg_0100"]})
    real_client = httpx.AsyncClient
    monkeypatch.setattr(fetch_agent.httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs))
    answer = asyncio.run(fetch_agent.answer_question("Did Margaret have life insurance?", "agent_test"))
    assert sum(request.url.path == "/api/ask" for request in requests) == expected_queries
    assert all(request.headers["X-Lastly-Agent-Mode"] == "synthetic" for request in requests)
    assert all(request.headers["X-Requested-With"] == "Lastly" for request in requests)
    if synthetic:
        assert "MetLife" in answer and "msg_0100" in answer
    else:
        assert "synthetic demo" in answer


def test_unlisted_fetch_sender_rejected_before_api(monkeypatch):
    monkeypatch.setenv("FETCH_ALLOWED_SENDERS", "agent_family")
    monkeypatch.setattr(fetch_agent.httpx, "AsyncClient", lambda **kwargs: pytest.fail("Unlisted sender reached API"))
    answer = asyncio.run(fetch_agent.answer_question("What accounts?", "agent_unknown"))
    assert "restricted" in answer


def test_fetch_agent_uses_separate_service_credential(monkeypatch):
    settings = SimpleNamespace(access_token="family-secret", agent_token="scoped-agent-secret")
    monkeypatch.setattr(fetch_agent, "get_settings", lambda: settings)
    headers = fetch_agent._headers("agent_family", private=True)
    assert headers["Authorization"] == "Bearer scoped-agent-secret"
    assert "family-secret" not in str(headers)
    assert headers["X-Lastly-Agent-Mode"] == "authorized_private"


@pytest.mark.parametrize("private", [False, True])
def test_agent_refuses_family_credential_as_fallback(monkeypatch, private):
    monkeypatch.setattr(fetch_agent, "get_settings", lambda: SimpleNamespace(access_token="family-secret", agent_token=""))
    with pytest.raises(ValueError, match="LASTLY_AGENT_TOKEN"):
        fetch_agent._headers("agent_family", private=private)


def test_agent_never_follows_a_redirect_or_inherits_proxy_credentials(monkeypatch):
    settings = SimpleNamespace(allow_private_cloud=False, access_token="family-secret", agent_token="agent-secret", api_base_url="http://127.0.0.1:8000")
    monkeypatch.setattr(fetch_agent, "get_settings", lambda: settings)
    monkeypatch.delenv("FETCH_ALLOWED_SENDERS", raising=False)
    requests = []
    options = []
    def handle(request):
        requests.append(request)
        return httpx.Response(307, headers={"Location": "https://attacker.example/collect"})
    real_client = httpx.AsyncClient
    def client(**kwargs):
        options.append(kwargs)
        return real_client(transport=httpx.MockTransport(handle), **kwargs)
    monkeypatch.setattr(fetch_agent.httpx, "AsyncClient", client)
    result = asyncio.run(fetch_agent.answer_question("What accounts?", "agent_public"))
    assert "unavailable or access is restricted" in result
    assert len(requests) == 1
    assert requests[0].url.host == "127.0.0.1"
    assert options[0]["trust_env"] is False
    assert options[0]["follow_redirects"] is False


@pytest.mark.parametrize("url", ["http://estate.example", "https://user:password@estate.example", "https://estate.example?token=secret", "https://estate.example#fragment"])
def test_agent_api_url_rejects_unsafe_configuration(monkeypatch, url):
    monkeypatch.setattr(fetch_agent, "get_settings", lambda: SimpleNamespace(api_base_url=url))
    with pytest.raises(ValueError):
        fetch_agent._api_url()


def test_optional_real_chat_protocol_contract_without_network(monkeypatch, tmp_path):
    """Runs when optional packages are installed; never runs registration or an agent server."""
    agent_module = pytest.importorskip("uagents.agent")
    chat = pytest.importorskip("uagents_core.contrib.protocols.chat")
    settings = SimpleNamespace(agent_seed="temporary fixed Lastly compatibility seed not used for registration",
                               agent_token="scoped-agent-secret", agent_mailbox=False, agent_port=8001, api_base_url="http://127.0.0.1:8000")
    monkeypatch.setattr(fetch_agent, "get_settings", lambda: settings)
    monkeypatch.chdir(tmp_path)
    # The SDK otherwise queries a chain version in its constructor; this check
    # exercises local construction and Chat Protocol behavior without that probe.
    monkeypatch.setattr(agent_module, "get_almanac_contract", lambda network: None)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        agent = fetch_agent.build_agent()
        # include(publish_manifest=True) schedules a coroutine; cancel it before
        # letting the event loop run so no manifest is published by the test.
        pending = list(asyncio.all_tasks(loop))
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
    finally:
        loop.close()
        asyncio.set_event_loop(None)
    # Chat, acknowledgements, insurance receipts and subscription task replies.
    assert len(agent._signed_message_handlers) == 4
    sent = []
    async def answer(question, sender):
        assert question == "Did Margaret have life insurance?"
        return "MetLife policy found. Evidence: msg_0100."
    async def send(sender, message):
        sent.append((sender, message))
    monkeypatch.setattr(fetch_agent, "answer_question", answer)
    message = chat.ChatMessage(timestamp=datetime.now(UTC), msg_id=uuid4(), content=[
        chat.TextContent(type="text", text="Did Margaret have life insurance?"),
    ])
    handler = agent._signed_message_handlers[chat.ChatMessage.build_schema_digest(chat.ChatMessage)]
    asyncio.run(handler(SimpleNamespace(send=send), "agent_test", message))
    assert len(sent) == 2
    assert sent[0][1].acknowledged_msg_id == message.msg_id
    assert "msg_0100" in sent[1][1].content[0].text
    assert sent[1][1].content[1].type == "end-session"
