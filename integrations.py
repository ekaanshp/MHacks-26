"""Sponsor integration setup and health checks: Neon, ElevenLabs and Fetch.ai.

Run through manage.py. Status checks are read-only, except that Neon's idempotent
schema is applied. Setup commands create or update provider resources only when
explicitly invoked, and never place a phone call.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import httpx

import calls
from config import get_settings

ROOT = Path(__file__).resolve().parent
AGENT_NAME = "Lastly Estate Caller"
# Values shown in the ElevenLabs dashboard when testing; real calls replace them per account.
DYNAMIC_VARIABLE_PLACEHOLDERS = {
    "person_name": "Margaret Hale", "date_of_death": "2026-09-12", "institution": "Planet Fitness",
    "action": "cancel", "category": "subscription", "executor_name": "Daniel",
}


class SetupError(RuntimeError):
    """A provider rejected a setup request or is not configured."""


def _short(exc: Exception) -> str:
    # Database and HTTP errors can be long; never include connection strings or keys.
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
    return re.sub(r"postgres(?:ql)?://\S+", "<database url>", text)[:200]


# Neon -----------------------------------------------------------------------

def neon_init() -> str:
    import db

    if not get_settings().database_url:
        raise SetupError("DATABASE_URL is not set.")
    try:
        with db._connection() as connection:
            connection.execute((ROOT / "schema.sql").read_text(encoding="utf-8"))
            estates = connection.execute("SELECT count(*) AS n FROM estates").fetchone()["n"]
    except db._database_errors() as exc:
        raise SetupError(f"Neon connection failed: {_short(exc)}") from None
    return f"Schema ready over verified TLS; {estates} estate revision(s) stored."


def neon_status() -> tuple[bool | None, str]:
    if not get_settings().database_url:
        return None, "Not configured (DATABASE_URL empty); the local JSON Family Hub is in use."
    try:
        return True, neon_init()
    except SetupError as exc:
        return False, str(exc)


# ElevenLabs -----------------------------------------------------------------

def _eleven(method: str, path: str, *, payload: dict[str, Any] | None = None) -> Any:
    key = get_settings().elevenlabs_api_key
    if not key:
        raise SetupError("ELEVENLABS_API_KEY is not set.")
    try:
        with httpx.Client(timeout=httpx.Timeout(25.0, connect=8.0), trust_env=False, follow_redirects=False) as client:
            response = client.request(method, f"{calls.API_BASE}{path}", headers={"xi-api-key": key}, json=payload)
    except httpx.HTTPError as exc:
        raise SetupError(f"ElevenLabs could not be reached ({type(exc).__name__}).") from None
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail")
            detail = detail.get("message") if isinstance(detail, dict) else detail
        except (ValueError, AttributeError):
            detail = None
        raise SetupError(f"ElevenLabs returned HTTP {response.status_code}" + (f": {str(detail)[:200]}" if detail else "."))
    return response.json() if response.content else {}


def agent_config(voice_id: str | None = None) -> dict[str, Any]:
    config: dict[str, Any] = {
        "turn": {"turn_timeout": 20.0, "silence_end_call_timeout": -1.0},
        "agent": {
            "first_message": calls.FIRST_MESSAGE,
            "language": "en",
            "prompt": {"prompt": calls.AGENT_PROMPT, "built_in_tools": {"end_call": {
                "type": "system", "name": "end_call", "params": {"system_tool_type": "end_call"},
                "description": "End the call after you have repeated any reference number, summarized what the company "
                               "confirmed and the remaining steps, said goodbye, and the representative has nothing further.",
            }}},
            "dynamic_variables": {"dynamic_variable_placeholders": dict(DYNAMIC_VARIABLE_PLACEHOLDERS)},
        },
    }
    if voice_id:
        config["tts"] = {"voice_id": voice_id}
    return config


def elevenlabs_setup(*, voice_id: str | None = None, twilio_number: str | None = None) -> list[str]:
    """Create or update the calling agent and optionally import/assign the Twilio number.

    Returns the .env lines the operator should save. No call is placed.
    """
    settings = get_settings()
    body = {"name": AGENT_NAME, "conversation_config": agent_config(voice_id)}
    if settings.elevenlabs_agent_id:
        _eleven("PATCH", f"/agents/{settings.elevenlabs_agent_id}", payload=body)
        agent_id = settings.elevenlabs_agent_id
    else:
        agent_id = _eleven("POST", "/agents/create", payload=body).get("agent_id")
        if not isinstance(agent_id, str) or not agent_id:
            raise SetupError("ElevenLabs did not return an agent ID.")
    lines = [f"ELEVENLABS_AGENT_ID={agent_id}"]
    phone_id = settings.elevenlabs_phone_number_id
    if twilio_number:
        if not re.fullmatch(r"\+[1-9]\d{7,14}", twilio_number):
            raise SetupError("Use E.164 format for the Twilio number, such as +17345551234.")
        existing = next((item for item in _eleven("GET", "/phone-numbers") if item.get("phone_number") == twilio_number), None)
        if existing:
            phone_id = existing["phone_number_id"]
        else:
            sid, token = os.getenv("TWILIO_ACCOUNT_SID", ""), os.getenv("TWILIO_AUTH_TOKEN", "")
            if not sid or not token:
                raise SetupError("Importing a Twilio number needs TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN in .env.")
            phone_id = _eleven("POST", "/phone-numbers", payload={
                "provider": "twilio", "phone_number": twilio_number, "label": "Lastly", "sid": sid, "token": token,
            }).get("phone_number_id")
            if not phone_id:
                raise SetupError("ElevenLabs did not return a phone number ID.")
    if phone_id:
        _eleven("PATCH", f"/phone-numbers/{phone_id}", payload={"agent_id": agent_id})
        lines.append(f"ELEVENLABS_PHONE_NUMBER_ID={phone_id}")
    return lines


def elevenlabs_status() -> tuple[bool | None, str]:
    settings = get_settings()
    if not settings.elevenlabs_api_key:
        return None, "Not configured (ELEVENLABS_API_KEY empty)."
    problems = []
    try:
        if not settings.elevenlabs_agent_id:
            problems.append("ELEVENLABS_AGENT_ID is empty; run `python manage.py elevenlabs-setup`.")
        else:
            agent = _eleven("GET", f"/agents/{settings.elevenlabs_agent_id}")
            prompt = (((agent.get("conversation_config") or {}).get("agent") or {}).get("prompt") or {}).get("prompt", "")
            if "AI" not in prompt:
                problems.append("The agent prompt is missing the AI disclosure; re-run elevenlabs-setup.")
        phone_mode = bool(settings.elevenlabs_phone_number_id)
        numbers = _eleven("GET", "/phone-numbers") if phone_mode else []
        number = next((item for item in numbers if item.get("phone_number_id") == settings.elevenlabs_phone_number_id), None)
        if not phone_mode:
            pass  # Browser conversations need no phone number; Twilio is optional.
        elif number is None:
            problems.append("ELEVENLABS_PHONE_NUMBER_ID does not match a number in this ElevenLabs account.")
        elif ((number.get("assigned_agent") or {}).get("agent_id")) != settings.elevenlabs_agent_id:
            problems.append(f"Phone number {number.get('phone_number')} is not assigned to the configured agent.")
    except SetupError as exc:
        return False, str(exc)
    if settings.offline:
        problems.append("LASTLY_OFFLINE=true blocks calls; set it to false for the live demo.")
    if phone_mode and not os.getenv("LASTLY_ALLOWED_CALL_NUMBERS", "").strip():
        problems.append("LASTLY_ALLOWED_CALL_NUMBERS is empty, so every call destination is blocked.")
    if not settings.access_token:
        problems.append("LASTLY_ACCESS_TOKEN is required before live calls are enabled.")
    if problems:
        return False, " ".join(problems)
    if not phone_mode:
        return True, "Agent ready for in-browser conversations (Talk to them here). Phone calls are off until a Twilio number is connected."
    return True, "Agent and phone number are connected; browser conversations and calls to approved numbers are enabled."


# Fetch.ai -------------------------------------------------------------------

def fetch_status() -> tuple[bool | None, str]:
    settings = get_settings()
    if not settings.agent_seed:
        return None, "Not configured (AGENT_SEED empty)."
    if not settings.agent_token:
        return False, "LASTLY_AGENT_TOKEN is required for the bridge; set the same value for the server and agent."
    try:
        from uagents.crypto import Identity
    except ImportError:
        return False, "Install the agent packages: pip install -r requirements-agent.txt."
    address = Identity.from_seed(settings.agent_seed, 0).address
    try:
        import fetch_agent

        base = fetch_agent._api_url()
        with httpx.Client(timeout=5.0, trust_env=False, follow_redirects=False) as client:
            # Any HTTP answer (including 401 before unlocking) means the server is up.
            healthy = client.get(f"{base}/api/health").status_code < 500
    except (ValueError, httpx.HTTPError):
        healthy = False
    reach = "reachable" if healthy else "not reachable yet (start uvicorn first)"
    insurer = os.getenv("CLAIMS_AGENT_ADDRESS", "").strip()
    claims_note = (f" Agent-to-agent claims go to the insurer agent {insurer}; run `python insurer_agent.py` too."
                   if insurer else " Agent-to-agent claims are off (CLAIMS_AGENT_ADDRESS empty).")
    return True, f"Agent address {address}; Lastly API at {settings.api_base_url} is {reach}. Run `python fetch_agent.py` and connect Mailbox.{claims_note}"


def status_report() -> list[tuple[str, bool | None, str]]:
    return [("Neon", *neon_status()), ("ElevenLabs", *elevenlabs_status()), ("Fetch.ai", *fetch_status())]
