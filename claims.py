"""Agent-to-agent insurance claims relayed through Lastly's Fetch.ai agent.

The server never talks to another agent itself. It queues a claim request; the
Lastly agent (which holds the agent identity) delivers it to the insurer's agent
and reports the insurer's answer back. Importing this module performs no I/O.
"""
from __future__ import annotations

import os
import re
import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import config
from calls import persona_fingerprint
from secure_storage import private_file_lock, read_json, write_json

_LOCK = threading.RLock()
_MAX_RECORDS = 200
ACTIVE = {"queued", "sent", "opened"}
AGENT_ADDRESS = re.compile(r"agent1[a-z0-9]{50,70}")


class ClaimsNotConfigured(RuntimeError):
    """The insurer agent's address or the Lastly agent credential is missing."""


def insurer_address() -> str:
    value = os.getenv("CLAIMS_AGENT_ADDRESS", "").strip()
    return value if AGENT_ADDRESS.fullmatch(value) else ""


def require_configured() -> str:
    address = insurer_address()
    if not address or not os.getenv("LASTLY_AGENT_TOKEN"):
        raise ClaimsNotConfigured(
            "Agent claims need CLAIMS_AGENT_ADDRESS and LASTLY_AGENT_TOKEN, with fetch_agent.py and "
            "insurer_agent.py running. No claim has been sent."
        )
    return address


def _path() -> Path:
    return config.data_dir() / "runtime_claims.json"


@contextmanager
def _locked():
    with _LOCK, private_file_lock(_path().with_name(".runtime-claims.lock")):
        path = _path()
        records = read_json(path) if path.exists() else {}
        if not isinstance(records, dict):
            raise ValueError("The local claim record is invalid.")
        yield records


def _now() -> str:
    return datetime.now(UTC).isoformat()


def public(record: dict[str, Any]) -> dict[str, Any]:
    keys = ("id", "account_id", "institution", "status", "claim_number", "required_documents", "message", "responder", "created_at", "updated_at")
    return {key: record.get(key) for key in keys}


def create(account: dict[str, Any], persona: dict[str, Any], executor: str) -> dict[str, Any]:
    """Queue one claim per account; an active claim is returned instead of duplicated."""
    address = require_configured()
    fingerprint = persona_fingerprint(persona)
    with _locked() as records:
        for record in records.values():
            if record.get("account_id") == account["id"] and record.get("persona") == fingerprint and record.get("status") in ACTIVE:
                return public(record)
        if len(records) >= _MAX_RECORDS:
            # Drop the oldest finished records; active claims are never evicted.
            for key in sorted((key for key, value in records.items() if value.get("status") not in ACTIVE), key=lambda key: records[key]["created_at"])[:20]:
                del records[key]
        claim_id = uuid.uuid4().hex
        records[claim_id] = {
            "id": claim_id, "account_id": account["id"], "persona": fingerprint, "institution": account["institution"],
            "insurer": address, "status": "queued", "created_at": _now(), "updated_at": _now(),
            "request": {
                "request_id": claim_id,
                "policyholder_name": str(persona.get("name") or ""),
                "date_of_death": str(persona.get("date_of_death") or ""),
                "institution": account["institution"],
                "policy_type": account["category"],
                "claimant_name": executor,
            },
        }
        write_json(_path(), records)
        return public(records[claim_id])


def latest(account_id: str, persona: dict[str, Any]) -> dict[str, Any] | None:
    fingerprint = persona_fingerprint(persona)
    with _locked() as records:
        matches = [record for record in records.values() if record.get("account_id") == account_id and record.get("persona") == fingerprint]
    return public(max(matches, key=lambda record: record["created_at"])) if matches else None


def pending() -> list[dict[str, Any]]:
    """Requests the Lastly agent should deliver, with the insurer it must deliver them to."""
    with _locked() as records:
        return [{"insurer": record["insurer"], **record["request"]} for record in records.values() if record.get("status") == "queued"]


def update(claim_id: str, status: str, *, responder: str = "", claim_number: str | None = None,
           required_documents: list[str] | None = None, message: str = "") -> dict[str, Any]:
    """Apply the agent's report. Only the configured insurer agent can open or reject a claim."""
    with _locked() as records:
        record = records.get(claim_id)
        if record is None:
            raise KeyError(claim_id)
        transitions = {"queued": {"sent", "failed", "opened", "rejected"}, "sent": {"opened", "rejected", "failed"}}
        if status not in transitions.get(record["status"], set()):
            if record["status"] == status:
                return public(record)
            raise ValueError("This claim has already been resolved.")
        if status in {"opened", "rejected"} and responder != record["insurer"]:
            raise PermissionError("Only the insurer's agent can answer this claim.")
        record.update(status=status, updated_at=_now())
        if status in {"opened", "rejected"}:
            record.update(responder=responder, claim_number=claim_number, required_documents=list(required_documents or []), message=message)
        elif status == "failed":
            record["message"] = message
        write_json(_path(), records)
        return public(record)
