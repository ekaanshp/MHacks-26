"""Durable account requests exchanged by the two Fetch.ai agents."""
from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import uuid4

import config
from calls import persona_fingerprint
from claims import AGENT_ADDRESS, ClaimsNotConfigured
from secure_storage import private_file_lock, read_json, write_json

_LOCK = threading.RLock()
TERMINAL = {"completed", "pending", "rejected", "failed", "timed_out"}
RETRYABLE = {"failed", "rejected", "timed_out"}
# How long each step may wait before the family is told what stalled. Overridable for slow demos.
DEADLINES = {"queued": 60, "sent": 90, "awaiting_details": 90, "details_sent": 90}
STALLED = {
    "queued": "Lastly's Fetch.ai agent never picked up the request. Check that fetch_agent.py is running and connected.",
    "sent": "The company's agent did not answer. Check that insurer_agent.py is running and reachable.",
    "awaiting_details": "Lastly's agent did not send the account details the company asked for. Check fetch_agent.py.",
    "details_sent": "The company's agent did not confirm the request. Check insurer_agent.py.",
}


def _deadline(status: str) -> float:
    scale = float(os.getenv("LASTLY_AGENT_TIMEOUT_SCALE", "1") or 1)
    return DEADLINES.get(status, 0) * scale


def _expire(record) -> bool:
    """Mark a stalled request as timed out; return whether it changed."""
    limit = _deadline(record["status"])
    if not limit:
        return False
    try:
        waited = (datetime.now(UTC) - datetime.fromisoformat(record["updated_at"])).total_seconds()
    except (KeyError, ValueError):
        return False
    if waited < limit:
        return False
    record.update(status="timed_out", failure_reason=STALLED[record["status"]], updated_at=datetime.now(UTC).isoformat())
    return True


def company_address():
    value = os.getenv("COMPANY_AGENT_ADDRESS", "").strip() or os.getenv("CLAIMS_AGENT_ADDRESS", "").strip()
    return value if AGENT_ADDRESS.fullmatch(value) else ""


def _path():
    return config.data_dir() / "runtime_agent_tasks.json"


@contextmanager
def _locked():
    with _LOCK, private_file_lock(_path().with_name(".runtime-agent-tasks.lock")):
        records = read_json(_path()) if _path().exists() else {}
        if not isinstance(records, dict):
            raise ValueError("The agent task record is invalid.")
        yield records


def public(record):
    output = {key: record.get(key) for key in (
        "id", "account_id", "institution", "action", "status", "reference_number",
        "required_documents", "message", "transcript", "created_at", "updated_at", "failure_reason",
    )}
    output["attempts"] = list(record.get("attempts") or [])
    limit = _deadline(record.get("status", ""))
    output["deadline_seconds"] = round(limit) if limit else None
    return output


def create(account, persona, executor):
    address = company_address()
    if not address or not os.getenv("LASTLY_AGENT_TOKEN"):
        raise ClaimsNotConfigured("Fetch.ai conversations need COMPANY_AGENT_ADDRESS (or CLAIMS_AGENT_ADDRESS), "
                                 "LASTLY_AGENT_TOKEN, and both fetch_agent.py and insurer_agent.py running.")
    fingerprint = persona_fingerprint(persona)
    with _locked() as records:
        previous = []
        for record in sorted(records.values(), key=lambda item: item["created_at"]):
            if record["account_id"] != account["id"] or record["persona"] != fingerprint:
                continue
            if _expire(record):
                write_json(_path(), records)
            if record["status"] not in RETRYABLE:
                return public(record)
            previous.append({"id": record["id"], "status": record["status"], "failure_reason": record.get("failure_reason") or record.get("message"),
                             "transcript": record.get("transcript", []), "created_at": record["created_at"]})
        if len(records) >= 200:
            raise ValueError("The agent task limit has been reached.")
        task_id = uuid4().hex
        now = datetime.now(UTC).isoformat()
        record = {
            "id": task_id, "account_id": account["id"], "institution": account["institution"],
            "action": account["action"], "persona": fingerprint, "company": address,
            "status": "queued", "transcript": [], "created_at": now, "updated_at": now,
            # Earlier attempts stay visible, so a retry never hides what already happened.
            "attempts": previous[-5:],
            "request": {"request_id": task_id, "institution": account["institution"],
                        "action": account["action"], "category": account["category"],
                        "person_name": str(persona.get("name") or ""),
                        "date_of_death": str(persona.get("date_of_death") or ""), "executor_name": executor},
        }
        records[task_id] = record
        write_json(_path(), records)
        return public(record)


def latest(account_id, persona):
    fingerprint = persona_fingerprint(persona)
    with _locked() as records:
        matches = [r for r in records.values() if r["account_id"] == account_id and r["persona"] == fingerprint]
        if not matches:
            return None
        record = max(matches, key=lambda r: r["created_at"])
        if _expire(record):
            write_json(_path(), records)
        return public(record)


def pending(persona):
    fingerprint = persona_fingerprint(persona)
    with _locked() as records:
        output = []
        changed = False
        for record in records.values():
            changed = _expire(record) or changed
            if record["persona"] != fingerprint or record["status"] not in {"queued", "awaiting_details"}:
                continue
            request = dict(record["request"], company=record["company"],
                           stage="intro" if record["status"] == "queued" else "details")
            if request["stage"] == "intro":
                request.update(person_name="", date_of_death="", executor_name="")
            output.append(request)
        if changed:
            write_json(_path(), records)
        return output


def update(task_id, persona, status, *, responder="", message="", reference_number=None, required_documents=None):
    with _locked() as records:
        record = records.get(task_id)
        if record is None or record["persona"] != persona_fingerprint(persona):
            raise KeyError(task_id)
        company_reply = status in {"awaiting_details", "completed", "pending", "rejected"}
        if company_reply and responder != record["company"]:
            raise PermissionError("Only the requested company's agent can answer this request.")
        transitions = {"queued": {"sent", "awaiting_details", "failed"},
                       "sent": {"awaiting_details", "failed"},
                       "awaiting_details": {"details_sent", "completed", "pending", "rejected", "failed"},
                       "details_sent": {"completed", "pending", "rejected", "failed"}}
        # A provider response can beat the relay's acknowledgement.
        if status == record["status"]:
            return public(record)
        if record["status"] == "timed_out":
            raise ValueError("This request timed out and was closed. Start a new request from the account.")
        if (status == "sent" and record["status"] != "queued") or (status == "details_sent" and record["status"] in TERMINAL):
            line = {"speaker": "Lastly", "message": message}
            if message and line not in record["transcript"]:
                position = 0 if status == "sent" else max(0, len(record["transcript"]) - 1)
                record["transcript"].insert(position, line)
                write_json(_path(), records)
            return public(record)
        if status not in transitions.get(record["status"], set()):
            raise ValueError("This agent task cannot make that status transition.")
        if status == "completed" and required_documents:
            raise ValueError("A request with outstanding documents cannot be completed.")
        record.update(status=status, updated_at=datetime.now(UTC).isoformat())
        if message:
            record["transcript"].append({"speaker": record["institution"] if company_reply else "Lastly", "message": message})
        if status in TERMINAL:
            record.update(message=message, reference_number=reference_number, required_documents=list(required_documents or []))
        if status == "failed":
            record["failure_reason"] = message or "The agent reported that the request could not be delivered."
        write_json(_path(), records)
        return public(record)
