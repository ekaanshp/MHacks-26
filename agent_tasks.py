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
TERMINAL = {"completed", "pending", "rejected", "failed"}


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
    return {key: record.get(key) for key in (
        "id", "account_id", "institution", "action", "status", "reference_number",
        "required_documents", "message", "transcript", "created_at", "updated_at",
    )}


def create(account, persona, executor):
    address = company_address()
    if not address or not os.getenv("LASTLY_AGENT_TOKEN"):
        raise ClaimsNotConfigured("Fetch.ai conversations need COMPANY_AGENT_ADDRESS (or CLAIMS_AGENT_ADDRESS), "
                                 "LASTLY_AGENT_TOKEN, and both fetch_agent.py and insurer_agent.py running.")
    fingerprint = persona_fingerprint(persona)
    with _locked() as records:
        for record in records.values():
            if record["account_id"] == account["id"] and record["persona"] == fingerprint and record["status"] not in {"failed", "rejected"}:
                return public(record)
        if len(records) >= 200:
            raise ValueError("The agent task limit has been reached.")
        task_id = uuid4().hex
        now = datetime.now(UTC).isoformat()
        record = {
            "id": task_id, "account_id": account["id"], "institution": account["institution"],
            "action": account["action"], "persona": fingerprint, "company": address,
            "status": "queued", "transcript": [], "created_at": now, "updated_at": now,
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
        return public(max(matches, key=lambda r: r["created_at"])) if matches else None


def pending(persona):
    fingerprint = persona_fingerprint(persona)
    with _locked() as records:
        output = []
        for record in records.values():
            if record["persona"] != fingerprint or record["status"] not in {"queued", "awaiting_details"}:
                continue
            request = dict(record["request"], company=record["company"],
                           stage="intro" if record["status"] == "queued" else "details")
            if request["stage"] == "intro":
                request.update(person_name="", date_of_death="", executor_name="")
            output.append(request)
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
        write_json(_path(), records)
        return public(record)
