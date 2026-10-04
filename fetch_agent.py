"""Optional Agentverse Chat Protocol bridge to Lastly's evidence-backed API.

Run explicitly after configuring AGENT_SEED. There is no separate LLM or hidden
access to inbox contents, and public agent queries are limited to synthetic data.
"""
from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import httpx

from config import get_settings

AGENT_DESCRIPTION = (
    "Lastly helps families understand estate accounts from email and bank statements. "
    "Answers cite the supporting email or bank row IDs. The public demo uses synthetic data."
)
EXAMPLE_QUESTIONS = [
    "Did Margaret have life insurance?",
    "Which accounts are still charging?",
    "What renews in the next 14 days?",
    "Which subscriptions were found only on a bank statement?",
]


def allowed_senders() -> set[str]:
    return {value.strip() for value in os.getenv("FETCH_ALLOWED_SENDERS", "").split(",") if value.strip()}


def _headers(sender: str, *, private: bool) -> dict[str, str]:
    settings = get_settings()
    agent_token = getattr(settings, "agent_token", "")
    if not agent_token:
        raise ValueError("Agent access requires a dedicated LASTLY_AGENT_TOKEN.")
    headers = {"X-Lastly-Agent-Mode": "authorized_private" if private else "synthetic",
               "X-Lastly-Agent-Sender": sender,
               "X-Requested-With": "Lastly"}
    # This credential only permits estate reads and evidence-backed questions.
    # The family credential must never be given to a remotely reachable agent.
    headers["Authorization"] = f"Bearer {agent_token}"
    return headers


def _api_url() -> str:
    value = get_settings().api_base_url.rstrip("/")
    parsed = urlparse(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise ValueError("LASTLY_API_BASE_URL must be a valid HTTP(S) URL without credentials.")
    if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Use HTTPS when the Lastly API is outside this computer.")
    return value


def _format_answer(payload: dict[str, Any]) -> str:
    answer = str(payload.get("answer") or "There is not enough evidence to answer that question.")
    evidence = payload.get("evidence_ids") or []
    if isinstance(evidence, list):
        # The backend owns citations. Never fabricate an ID in the agent bridge.
        evidence = [value for value in evidence if isinstance(value, str) and value.startswith(("msg_", "bank_"))]
    else:
        evidence = []
    # Answers that already cite every ID inline do not need a repeated footer.
    if evidence and not all(item in answer for item in evidence):
        answer += "\nEvidence: " + ", ".join(dict.fromkeys(evidence))
    return answer


async def answer_question(question: str, sender: str = "") -> str:
    """Proxy one question with access checks; useful independently of uAgents."""
    question = question.strip()
    if not question:
        return "Please send a question about the estate's accounts."
    if len(question) > 2000:
        return "Please keep the question under 2,000 characters."
    permitted = allowed_senders()
    if permitted and sender not in permitted:
        return "This Lastly agent is restricted to authorized family agents."
    settings = get_settings()
    private = bool(permitted and sender in permitted and settings.allow_private_cloud)
    try:
        base_url = _api_url()
        headers = _headers(sender, private=private)
        async with httpx.AsyncClient(timeout=httpx.Timeout(110.0, connect=8.0), trust_env=False, follow_redirects=False) as client:
            # Restriction is also enforced by the server for both endpoints, so an
            # import between the estate read and the question cannot expose real data.
            estate_response = await client.get(f"{base_url}/api/estate", headers=headers)
            estate_response.raise_for_status()
            estate = estate_response.json()
            analysis = estate.get("analysis") if isinstance(estate, dict) else None
            is_synthetic = isinstance(analysis, dict) and analysis.get("synthetic") is True
            if not is_synthetic and not private:
                return "This public Lastly agent only answers questions about the synthetic demo estate."
            response = await client.post(f"{base_url}/api/ask", headers=headers, json={"question": question})
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict):
            return "Lastly returned an unexpected answer. Please try again."
        return _format_answer(payload)
    except (httpx.HTTPError, ValueError, TypeError):
        return "Lastly's local service is unavailable or access is restricted. Start the server and check the agent configuration."


async def _claims_request(method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0), trust_env=False, follow_redirects=False) as client:
        response = await client.request(method, f"{_api_url()}{path}", headers=_headers("", private=False), json=payload)
        response.raise_for_status()
        return response.json()


async def relay_pending_claims(send) -> int:
    """Deliver queued claim requests to the insurer agent; returns how many were sent."""
    pending = await _claims_request("GET", "/api/agent/claims")
    sent = 0
    for item in pending.get("claims", []) if isinstance(pending, dict) else []:
        try:
            await send(item)
            await _claims_request("POST", f"/api/agent/claims/{item['request_id']}", {"status": "sent"})
            sent += 1
        except (httpx.HTTPError, KeyError, ValueError):
            continue
    return sent


async def report_claim_response(sender: str, response) -> None:
    """Pass the insurer agent's answer back to Lastly; the server checks the sender."""
    await _claims_request("POST", f"/api/agent/claims/{response.request_id}", {
        "status": response.status if response.status in {"opened", "rejected"} else "rejected",
        "responder": sender, "claim_number": response.claim_number,
        "required_documents": list(response.required_documents)[:8], "message": response.message[:600],
    })


def build_agent():
    """Construct the optional agent only when requested, without changing its identity."""
    settings = get_settings()
    if not settings.agent_seed or len(settings.agent_seed.strip()) < 24:
        raise ValueError("Set AGENT_SEED to a fixed, private random phrase of at least 24 characters. Reuse it to keep the same agent address.")
    _headers("", private=False)
    _api_url()
    try:
        from uagents import Agent, Context, Protocol
        from uagents_core.contrib.protocols.chat import (
            ChatAcknowledgement,
            ChatMessage,
            EndSessionContent,
            TextContent,
            chat_protocol_spec,
        )
    except ImportError as exc:
        raise RuntimeError("Install the optional agent packages with pip install -r requirements-agent.txt.") from exc
    # Python 3.14 no longer creates an implicit event loop, which uAgents expects.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    from agent_claims import ClaimRequest, ClaimResponse, LocalFirstResolver, claims_protocol

    insurer = os.getenv("CLAIMS_AGENT_ADDRESS", "").strip()
    CLAIM_FIELDS = ("request_id", "policyholder_name", "date_of_death", "institution", "policy_type", "claimant_name")
    agent = Agent(name="Lastly Estate Assistant", seed=settings.agent_seed,
                  port=settings.agent_port, mailbox=settings.agent_mailbox,
                  description=AGENT_DESCRIPTION, publish_agent_details=True, loop=loop,
                  resolve=LocalFirstResolver({insurer: os.getenv("CLAIMS_AGENT_ENDPOINT", "")}))
    protocol = Protocol(spec=chat_protocol_spec)
    claims = claims_protocol()

    @agent.on_interval(period=2.0)
    async def deliver_claims(ctx: Context):
        if not insurer:
            return

        async def send(item):
            await ctx.send(item["insurer"], ClaimRequest(**{key: item[key] for key in CLAIM_FIELDS}))
            ctx.logger.info(f"Sent claim {item['request_id'][:8]} to the insurer agent")

        try:
            await relay_pending_claims(send)
        except (httpx.HTTPError, ValueError):
            pass  # The Lastly API may not be running yet; retry on the next interval.

    @claims.on_message(ClaimResponse)
    async def handle_claim_response(ctx: Context, sender: str, response: ClaimResponse):
        if sender != insurer:
            ctx.logger.warning("Ignored a claim response from an unknown agent")
            return
        try:
            await report_claim_response(sender, response)
            ctx.logger.info(f"Insurer agent {response.status} claim {response.claim_number or ''}")
        except (httpx.HTTPError, ValueError):
            ctx.logger.warning("The claim answer could not be saved in Lastly")

    @protocol.on_message(ChatMessage)
    async def handle_message(ctx: Context, sender: str, message: ChatMessage):
        await ctx.send(sender, ChatAcknowledgement(timestamp=datetime.now(UTC), acknowledged_msg_id=message.msg_id))
        question = "\n".join(item.text for item in message.content if isinstance(item, TextContent))
        response = await answer_question(question, sender)
        await ctx.send(sender, ChatMessage(timestamp=datetime.now(UTC), msg_id=uuid4(), content=[
            TextContent(type="text", text=response), EndSessionContent(type="end-session"),
        ]))

    @protocol.on_message(ChatAcknowledgement)
    async def handle_ack(ctx: Context, sender: str, message: ChatAcknowledgement):
        # Each answer closes the session; no conversation history is persisted.
        return None

    agent.include(protocol, publish_manifest=True)
    agent.include(claims, publish_manifest=True)
    return agent


def main() -> None:
    try:
        agent = build_agent()
    except (ValueError, RuntimeError) as exc:
        raise SystemExit(str(exc)) from exc
    print("Starting Lastly's optional Fetch.ai agent. Use its Agent Inspector link to connect Mailbox.")
    print("Public replies use the synthetic demo only. Private replies require an explicit sender allowlist and cloud consent.")
    agent.run()


if __name__ == "__main__":
    main()
