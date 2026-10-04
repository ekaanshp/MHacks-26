"""Small Anthropic adapter. Offline operation never contacts the provider."""
from __future__ import annotations

import json
import time

import httpx

from config import get_settings


class LLMError(RuntimeError):
    pass


def enabled() -> bool:
    settings = get_settings()
    return bool(settings.anthropic_api_key) and not settings.offline


def complete(system: str, user: str, *, json_mode: bool = False, model: str | None = None, max_tokens: int = 4096) -> str:
    settings = get_settings()
    if not enabled():
        raise LLMError("Set ANTHROPIC_API_KEY and LASTLY_OFFLINE=false to enable AI requests.")
    payload = {
        "model": model or settings.llm_model,
        "max_tokens": max_tokens,
        "system": system + ("\nReturn a single valid JSON object, without markdown fences." if json_mode else ""),
        "messages": [{"role": "user", "content": user}],
    }
    headers = {"x-api-key": settings.anthropic_api_key, "anthropic-version": "2023-06-01"}
    if settings.anthropic_workspace_id:
        # Organization-level keys must name the workspace that is billed.
        headers["anthropic-workspace-id"] = settings.anthropic_workspace_id
    for attempt in range(3):
        try:
            response = httpx.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json=payload,
                timeout=httpx.Timeout(90, connect=10),
                trust_env=False,
                follow_redirects=False,
            )
        except httpx.HTTPError as exc:
            raise LLMError("The AI service could not be reached. The saved estate is still available.") from exc
        if response.status_code in {429, 500, 502, 503, 529} and attempt < 2:
            time.sleep(0.5 * (attempt + 1))
            continue
        if response.is_error:
            raise LLMError(f"The AI service returned HTTP {response.status_code}. Check the model and credentials.")
        if response.is_redirect or len(response.content) > 2 * 1024 * 1024:
            raise LLMError("The AI service returned an unexpected response.")
        try:
            data = response.json()
        except ValueError as exc:
            raise LLMError("The AI service returned invalid JSON.") from exc
        if not isinstance(data, dict) or not isinstance(data.get("content"), list):
            raise LLMError("The AI service returned an unexpected response.")
        if data.get("stop_reason") == "max_tokens":
            raise LLMError("The AI response was truncated; reduce the sender batch size.")
        output = "".join(part["text"] for part in data["content"] if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str))
        if not output:
            raise LLMError("The AI service returned an empty response.")
        return output
    raise LLMError("The AI service is temporarily unavailable.")


def _json_object(raw: str) -> dict | None:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        # Models occasionally add a sentence around the object; read the first complete one.
        start = raw.find("{")
        if start < 0:
            return None
        try:
            result, _ = json.JSONDecoder().raw_decode(raw[start:])
        except json.JSONDecodeError:
            return None
    return result if isinstance(result, dict) else None


def complete_json(system: str, user: str, *, model: str | None = None, max_tokens: int = 4096) -> dict:
    # One unreadable reply should not end a long mailbox analysis, so ask once more.
    for _ in range(2):
        result = _json_object(complete(system, user, json_mode=True, model=model, max_tokens=max_tokens))
        if result is not None:
            return result
    raise LLMError("The AI service returned invalid JSON.")
