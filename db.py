"""Neon persistence with an atomic, durable local Family Hub fallback.

Every successful remote write is also mirrored to JSON. A lost connection can
therefore serve the most recently observed estate without discarding family
edits. No database connection string or database error details are logged.
"""

from __future__ import annotations

import copy
import logging
import os
import re
import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from secure_storage import private_file_lock, read_json, write_json

LOG = logging.getLogger(__name__)
UNSET = object()
_LOCK = threading.RLock()
_ESTATE_FIELDS = {"estate_id", "persona", "stats", "totals", "today", "accounts"}
FAMILY_FIELDS = ("review", "corrections", "notes", "followups", "documents", "outcome")
_VALID_STATUSES = {"open", "in_progress", "done"}
# Backend-only columns: sync bookkeeping and private family member ids.
_PRIVATE_ACTIVITY = {"sync_key", "actor_member_id"}


def _public_activity(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key not in _PRIVATE_ACTIVITY}


def _settings():
    from config import get_settings

    return get_settings()


def _data_dir() -> Path:
    # Read the environment at call time, including in tests and Takeout runs.
    from config import data_dir

    return data_dir()


def _empty_store() -> dict[str, Any]:
    return {"next_estate_id": 1, "next_activity_id": 1, "estates": [], "activity": [], "pending_patches": [], "pending_activity": [], "estate_origins": {}}


def _atomic_json(path: Path, value: Any) -> None:
    write_json(path, value)


class EstateRemoved(LookupError):
    """The estate's mailbox was deleted; its directory must not be re-created by a late request."""


@contextmanager
def _local_lock():
    from config import root_data_dir

    directory = _data_dir()
    # A deleted estate's directory is gone; a late request must not bring it back.
    if not Path(directory).exists() and Path(directory).resolve() != Path(root_data_dir()).resolve():
        raise EstateRemoved("This estate's mailbox was deleted.")
    with _LOCK, private_file_lock(directory / ".family-store.lock"):
        yield directory


def _read_store(directory: Path) -> dict[str, Any]:
    path = directory / "family_store.json"
    if path.exists():
        store = read_json(path)
        if not isinstance(store, dict) or not isinstance(store.get("estates"), list):
            raise ValueError("The local Family Hub store has an invalid format.")
        store.setdefault("activity", [])
        store.setdefault("pending_patches", [])
        store.setdefault("pending_activity", [])
        store.setdefault("estate_origins", {})
        for estate in store["estates"]:
            store["estate_origins"].setdefault(str(estate["estate_id"]), "local")
        store.setdefault("next_activity_id", max((item.get("id", 0) for item in store["activity"]), default=0) + 1)
        store.setdefault("next_estate_id", max((item.get("estate_id", 0) for item in store["estates"]), default=0) + 1)
        if store["estates"]:
            store.setdefault("latest_estate_id", store["estates"][-1]["estate_id"])
        return store
    store = _empty_store()
    cache = directory / "estate.json"
    if cache.exists():
        estate = read_json(cache)
        if isinstance(estate, dict) and "accounts" in estate:
            estate = copy.deepcopy(estate)
            estate["estate_id"] = estate.get("estate_id") or 1
            store["estates"].append(estate)
            store["latest_estate_id"] = estate["estate_id"]
            store["estate_origins"][str(estate["estate_id"])] = "local"
            store["next_estate_id"] = estate["estate_id"] + 1
    return store


def _write_store(directory: Path, store: dict[str, Any], latest: dict[str, Any] | None = None) -> None:
    _atomic_json(directory / "family_store.json", store)
    if latest is not None:
        _atomic_json(directory / "estate.json", latest)


def _latest(store: dict[str, Any], estate_id: int | None = None) -> dict[str, Any] | None:
    if estate_id is None:
        estate_id = store.get("latest_estate_id")
        if estate_id is None:
            return store["estates"][-1] if store["estates"] else None
    return next((item for item in reversed(store["estates"]) if item.get("estate_id") == estate_id), None)


def _identity(account: dict[str, Any]) -> tuple[str, str]:
    institution = re.sub(r"[^a-z0-9]+", "", account.get("institution", "").casefold())
    return institution, account.get("category", "")


def _same_person(left: dict[str, Any], right: dict[str, Any]) -> bool:
    # A separate real Takeout must not inherit Margaret's edits to Netflix.
    left_person = left.get("persona", {})
    right_person = right.get("persona", {})
    left_email = str(left_person.get("email", "")).casefold().strip()
    right_email = str(right_person.get("email", "")).casefold().strip()
    if left_email or right_email:
        return left_email == right_email
    return left_person.get("name") == right_person.get("name")


def _active_persona() -> dict[str, Any] | None:
    path = _data_dir() / "inbox.json"
    if not path.exists():
        return None
    persona = read_json(path).get("persona")
    return persona if isinstance(persona, dict) else None


def _local_persona_estate(store: dict[str, Any], persona: dict[str, Any]) -> dict[str, Any] | None:
    latest = _latest(store)
    if latest and _same_person(latest, {"persona": persona}):
        return latest
    return next((estate for estate in reversed(store["estates"]) if _same_person(estate, {"persona": persona})), None)


def _preserve_edits(estate: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    result = copy.deepcopy(estate)
    known = {}
    if previous and _same_person(result, previous):
        known = {_identity(account): account for account in previous.get("accounts", [])}
    reserved_ids = {account["id"] for account in known.values()}
    allocated_ids: set[str] = set()
    for account in result.get("accounts", []):
        old = known.get(_identity(account))
        if old:
            account["id"] = old["id"]
        elif known:
            index = 0
            while f"acct_{index:02d}" in reserved_ids | allocated_ids:
                index += 1
            account["id"] = f"acct_{index:02d}"
        if account["id"] in allocated_ids:
            raise ValueError("Every account must have a unique id within its estate.")
        allocated_ids.add(account["id"])
        account["status"] = old.get("status", "open") if old else account.get("status", "open")
        account["assigned_to"] = old.get("assigned_to") if old else account.get("assigned_to")
        for field in FAMILY_FIELDS:
            if old and old.get(field):
                account[field] = copy.deepcopy(old[field])
        if account["status"] not in _VALID_STATUSES:
            raise ValueError("Account status must be open, in_progress, or done.")
    # Accounts the family added are not in any inbox; they stay until the family removes them.
    present = {_identity(account) for account in result.get("accounts", [])}
    for old in known.values():
        if old.get("sources") == ["family"] and _identity(old) not in present and old["id"] not in allocated_ids:
            result["accounts"].append(copy.deepcopy(old))
            allocated_ids.add(old["id"])
    return result


def _connection():
    from psycopg import connect
    from psycopg.conninfo import conninfo_to_dict
    from psycopg.rows import dict_row

    connection_string = _settings().database_url
    parameters = conninfo_to_dict(connection_string)
    allow_local = os.getenv("LASTLY_ALLOW_INSECURE_DB", "").lower() in {"1", "true", "yes", "on"}
    if allow_local:
        hosts = str(parameters.get("host") or "").split(",")
        # hostaddr can override the host used for a connection. Both must be local.
        addresses = str(parameters.get("hostaddr") or "").split(",")
        import ipaddress

        def local(value: str) -> bool:
            if value == "localhost" or value.startswith("/"):
                return True
            try:
                return ipaddress.ip_address(value).is_loopback
            except ValueError:
                return False

        if not all(local(host) for host in hosts) or any(address and not local(address) for address in addresses):
            raise ValueError("LASTLY_ALLOW_INSECURE_DB is restricted to explicitly configured local databases.")
        return connect(connection_string, connect_timeout=3, row_factory=dict_row)
    # Caller URL options cannot weaken certificate or hostname verification.
    # The psycopg wheel's libpq cannot locate the macOS trust store, so verify
    # against Mozilla's CA bundle (certifi) on every platform.
    import certifi

    return connect(
        connection_string, connect_timeout=3, row_factory=dict_row,
        sslmode="verify-full", sslrootcert=certifi.where(), ssl_min_protocol_version="TLSv1.2", gssencmode="disable",
    )


def _database_configured(estate: dict[str, Any] | None = None) -> bool:
    settings = _settings()
    if not settings.database_url:
        return False
    if getattr(settings, "allow_private_cloud", False):
        return True
    if estate and (estate.get("analysis") or {}).get("synthetic") is False:
        return False
    # Takeout directories can share a .env with the synthetic demo. A configured
    # Neon URL must never implicitly upload an imported person's accounts.
    path = _data_dir() / "inbox.json"
    if path.exists():
        if read_json(path).get("synthetic") is False:
            return False
    elif (store_path := _data_dir() / "family_store.json").exists():
        active = _latest(read_json(store_path))
        if active and (active.get("analysis") or {}).get("synthetic") is False:
            return False
    return True


def _database_errors() -> tuple[type[Exception], ...]:
    """Only infrastructure failures trigger fallback; programming bugs propagate."""
    errors = (OSError, ImportError)
    try:
        from psycopg import Error
    except ImportError:
        return errors
    return (*errors, Error)


def _database_unavailable() -> None:
    LOG.warning("Neon is unavailable; using the local Family Hub store.")


def _remote_load(connection, estate_id: int | None = None) -> dict[str, Any] | None:
    if estate_id is None:
        row = connection.execute("SELECT * FROM estates ORDER BY created_at DESC, id DESC LIMIT 1").fetchone()
    else:
        row = connection.execute("SELECT * FROM estates WHERE id = %s", (estate_id,)).fetchone()
    if row is None:
        return None
    accounts = connection.execute(
        "SELECT data, status, assigned_to FROM accounts WHERE estate_id = %s ORDER BY id",
        (row["id"],),
    ).fetchall()
    result = dict(row.get("metadata") or {})
    result.update({
        "estate_id": row["id"],
        "persona": row["persona"],
        "stats": row["stats"],
        "totals": row["totals"],
        "today": row["today"].isoformat() if hasattr(row["today"], "isoformat") else row["today"],
        "accounts": [dict(item["data"], status=item["status"], assigned_to=item["assigned_to"]) for item in accounts],
    })
    # Preserve extraction order (urgent first), because JSONB accounts are queried
    # individually and their ids do not necessarily represent display order.
    order = result.pop("_account_order", [])
    positions = {account_id: index for index, account_id in enumerate(order)}
    result["accounts"].sort(key=lambda account: positions.get(account["id"], len(positions)))
    return result


def _persona_key(persona: dict[str, Any]) -> str:
    email = str(persona.get("email", "")).casefold().strip()
    return email or str(persona.get("name", "")).casefold().strip()


def _remote_persona_estate(connection, persona: dict[str, Any]) -> dict[str, Any] | None:
    latest = _remote_load(connection)
    if latest and _same_person(latest, {"persona": persona}):
        return latest
    email = str(persona.get("email", "")).casefold().strip()
    if email:
        row = connection.execute(
            "SELECT id FROM estates WHERE lower(persona->>'email') = %s ORDER BY created_at DESC, id DESC LIMIT 1",
            (email,),
        ).fetchone()
    else:
        row = connection.execute(
            "SELECT id FROM estates WHERE coalesce(persona->>'email', '') = '' AND lower(persona->>'name') = %s ORDER BY created_at DESC, id DESC LIMIT 1",
            (_persona_key(persona),),
        ).fetchone()
    return _remote_load(connection, row["id"]) if row else None


def _queue_patch(store: dict[str, Any], estate: dict[str, Any], account: dict[str, Any], fields: dict[str, Any]) -> None:
    identity = _identity(account)
    person = _persona_key(estate["persona"])
    previous = next((patch for patch in store["pending_patches"] if _persona_key(patch["persona"]) == person and _identity(patch) == identity), None)
    values = dict(previous["fields"]) if previous else {}
    values.update(fields)
    if previous:
        store["pending_patches"].remove(previous)
    store["pending_patches"].append({
        "id": uuid.uuid4().hex, "persona": {key: estate["persona"].get(key, "") for key in ("email", "name")},
        "institution": account["institution"], "category": account["category"], "fields": values,
        "synthetic": (estate.get("analysis") or {}).get("synthetic"),
    })


def _overlay_pending(estate: dict[str, Any], patches: list[dict[str, Any]]) -> dict[str, Any]:
    result = copy.deepcopy(estate)
    accounts = {_identity(account): account for account in result["accounts"]}
    for patch in patches:
        if patch.get("synthetic") is False and not getattr(_settings(), "allow_private_cloud", False):
            continue
        if _persona_key(patch["persona"]) == _persona_key(result["persona"]) and _identity(patch) in accounts:
            accounts[_identity(patch)].update(patch["fields"])
    return result


def _reconcile_pending(connection, *, include_activity: bool = True) -> dict[str, Any]:
    """Replay explicit fields only. Callers acknowledge only after COMMIT."""
    from psycopg.types.json import Jsonb

    with _local_lock() as directory:
        store = _read_store(directory)
        patches = copy.deepcopy(store["pending_patches"])
        activities = copy.deepcopy(store["pending_activity"]) if include_activity else []
    acknowledged: dict[str, Any] = {"patches": set(), "activities": set(), "rows": []}
    estates = {}

    def find_estate(persona):
        key = _persona_key(persona)
        if key not in estates:
            estates[key] = _remote_persona_estate(connection, persona)
        return estates[key]

    for patch in patches:
        if patch.get("synthetic") is False and not getattr(_settings(), "allow_private_cloud", False):
            continue
        estate = find_estate(patch["persona"])
        if not estate:
            continue
        account = next((item for item in estate["accounts"] if _identity(item) == _identity(patch)), None)
        if account is None:
            continue
        row = connection.execute(
            "SELECT data, status, assigned_to FROM accounts WHERE estate_id = %s AND id = %s FOR UPDATE",
            (estate["estate_id"], account["id"]),
        ).fetchone()
        if row is None:
            continue
        updated = dict(row["data"], status=row["status"], assigned_to=row["assigned_to"])
        updated.update(patch["fields"])
        connection.execute(
            "UPDATE accounts SET data = %s, status = %s, assigned_to = %s, updated_at = now() WHERE estate_id = %s AND id = %s",
            (Jsonb(updated), updated["status"], updated["assigned_to"], estate["estate_id"], account["id"]),
        )
        acknowledged["patches"].add(patch["id"])
    for pending in activities:
        if pending.get("synthetic") is False and not getattr(_settings(), "allow_private_cloud", False):
            continue
        estate = find_estate(pending["persona"])
        if not estate:
            continue
        account = next((item for item in estate["accounts"] if _identity(item) == _identity(pending)), None) if pending.get("institution") else None
        if pending.get("institution") and account is None:
            continue
        event = pending["activity"]
        row = connection.execute(
            "INSERT INTO activity (estate_id, account_id, actor, action, created_at, sync_key) VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (sync_key) DO NOTHING RETURNING *",
            (estate["estate_id"], account["id"] if account else None, event["actor"], event["action"], event["created_at"], pending["id"]),
        ).fetchone()
        if row is None:
            row = connection.execute("SELECT * FROM activity WHERE sync_key = %s", (pending["id"],)).fetchone()
        if row:
            row = dict(row)
            row["created_at"] = row["created_at"].isoformat() if hasattr(row["created_at"], "isoformat") else row["created_at"]
            acknowledged["rows"].append(row)
            acknowledged["activities"].add(pending["id"])
    return acknowledged


def _acknowledge_pending(acknowledged: dict[str, Any]) -> None:
    if not any(acknowledged.values()):
        return
    with _local_lock() as directory:
        store = _read_store(directory)
        store["pending_patches"] = [patch for patch in store["pending_patches"] if patch["id"] not in acknowledged["patches"]]
        store["pending_activity"] = [activity for activity in store["pending_activity"] if activity["id"] not in acknowledged["activities"]]
        for row in acknowledged["rows"]:
            existing = next((i for i, activity in enumerate(store["activity"]) if activity.get("sync_key") == row.get("sync_key")), None)
            if existing is None:
                store["activity"].append(row)
            else:
                store["activity"][existing] = row
            store["next_activity_id"] = max(store["next_activity_id"], row["id"] + 1)
        _write_store(directory, store)


def _mirror_remote(estate: dict[str, Any], *, export: bool = True) -> dict[str, Any]:
    with _local_lock() as directory:
        store = _read_store(directory)
        estate = _overlay_pending(estate, store["pending_patches"])
        index = next((i for i, item in enumerate(store["estates"]) if item.get("estate_id") == estate["estate_id"]), None)
        if index is None:
            store["estates"].append(copy.deepcopy(estate))
        else:
            existing = store["estates"][index]
            old_source = (existing.get("analysis") or {}).get("source_hash")
            new_source = (estate.get("analysis") or {}).get("source_hash")
            if store["estate_origins"].get(str(estate["estate_id"])) == "local" and (
                not _same_person(existing, estate) or old_source != new_source
            ):
                # A new Neon project's SERIAL ids start at one independently of
                # local revisions. Keep the local snapshot under an unused id.
                archived = copy.deepcopy(existing)
                archive_id = store["next_estate_id"]
                occupied = {item["estate_id"] for item in store["estates"]} | {estate["estate_id"]}
                while archive_id in occupied:
                    archive_id += 1
                archived["estate_id"] = archive_id
                store["next_estate_id"] = archive_id + 1
                store["estates"].append(archived)
                store["estate_origins"][str(archive_id)] = "local"
                for activity in store["activity"]:
                    if activity["estate_id"] == estate["estate_id"]:
                        activity["estate_id"] = archive_id
                for pending in store["pending_activity"]:
                    if pending["activity"]["estate_id"] == estate["estate_id"]:
                        pending["activity"]["estate_id"] = archive_id
                if not export and store.get("latest_estate_id") == estate["estate_id"]:
                    store["latest_estate_id"] = archive_id
            store["estates"][index] = copy.deepcopy(estate)
        store["estate_origins"][str(estate["estate_id"])] = "neon"
        # Local and Neon serial ids are independent. A freshly observed cloud
        # estate can have a smaller id than an older local development revision.
        if export:
            store["latest_estate_id"] = estate["estate_id"]
        store["next_estate_id"] = max(store["next_estate_id"], estate["estate_id"] + 1)
        _write_store(directory, store, _latest(store) if export else None)
        return copy.deepcopy(estate)


def save_estate(estate: dict[str, Any]) -> int:
    """Save a new analysis, preserving family edits, and return its estate id."""
    if hasattr(estate, "model_dump"):
        estate = estate.model_dump(mode="json")
    if _database_configured(estate):
        try:
            from psycopg.types.json import Jsonb

            with _connection() as connection:
                connection.execute((Path(__file__).parent / "schema.sql").read_text(encoding="utf-8"))
                # Serialize full analyses so two simultaneous re-runs read the
                # same most recent family state before inserting their revision.
                connection.execute("SELECT pg_advisory_xact_lock(841019)")
                acknowledged = _reconcile_pending(connection, include_activity=False)
                previous = _remote_persona_estate(connection, estate["persona"])
                if previous is None:
                    # Connecting Neon later must carry over edits made while
                    # the team was developing against the local JSON store.
                    with _local_lock() as directory:
                        previous = copy.deepcopy(_local_persona_estate(_read_store(directory), estate["persona"]))
                result = _preserve_edits(estate, previous)
                metadata = {key: value for key, value in result.items() if key not in _ESTATE_FIELDS}
                metadata["_account_order"] = [account["id"] for account in result["accounts"]]
                row = connection.execute(
                    "INSERT INTO estates (persona, stats, totals, today, metadata) VALUES (%s, %s, %s, %s, %s) RETURNING id",
                    (Jsonb(result["persona"]), Jsonb(result["stats"]), Jsonb(result["totals"]), result["today"], Jsonb(metadata)),
                ).fetchone()
                result["estate_id"] = row["id"]
                for account in result["accounts"]:
                    connection.execute(
                        "INSERT INTO accounts (estate_id, id, data, status, assigned_to) VALUES (%s, %s, %s, %s, %s)",
                        (result["estate_id"], account["id"], Jsonb(account), account["status"], account.get("assigned_to")),
                    )
                after_save = _reconcile_pending(connection)
                acknowledged["patches"].update(after_save["patches"])
                acknowledged["activities"].update(after_save["activities"])
                acknowledged["rows"].extend(after_save["rows"])
                if after_save["patches"]:
                    result = _remote_load(connection, result["estate_id"])
            _acknowledge_pending(acknowledged)
            _mirror_remote(result)
            return result["estate_id"]
        except _database_errors():
            _database_unavailable()
    with _local_lock() as directory:
        store = _read_store(directory)
        result = _preserve_edits(estate, _local_persona_estate(store, estate["persona"]))
        result["estate_id"] = store["next_estate_id"]
        store["next_estate_id"] += 1
        store["estates"].append(result)
        store["latest_estate_id"] = result["estate_id"]
        store["estate_origins"][str(result["estate_id"])] = "local"
        _write_store(directory, store, result)
        return result["estate_id"]


def load_estate(estate_id: int | None = None) -> dict[str, Any] | None:
    """Return the requested estate, or the latest one, without exposing internals."""
    persona = _active_persona() if estate_id is None else None
    if _database_configured():
        try:
            with _connection() as connection:
                acknowledged = _reconcile_pending(connection)
                result = _remote_persona_estate(connection, persona) if persona is not None else _remote_load(connection, estate_id)
            _acknowledge_pending(acknowledged)
            if result is not None:
                return _mirror_remote(result, export=estate_id is None)
        except _database_errors():
            _database_unavailable()
    with _local_lock() as directory:
        store = _read_store(directory)
        return copy.deepcopy(_local_persona_estate(store, persona) if persona is not None else _latest(store, estate_id))


def _validate_edits(status: str | None, assigned_to: Any) -> None:
    if status is not None and status not in _VALID_STATUSES:
        raise ValueError("Account status must be open, in_progress, or done.")
    if assigned_to is not UNSET and assigned_to is not None and (
        not isinstance(assigned_to, str) or not assigned_to.strip() or len(assigned_to) > 100
    ):
        raise ValueError("Assignee must be a nonempty name, up to 100 characters, or null.")


def update_account(estate_id: int | None, acct_id: str, status: str | None = None, assigned_to: Any = UNSET,
                   fields: dict[str, Any] | None = None) -> dict[str, Any]:
    """Patch an account. Omitted assignment is retained; explicit None clears it.

    ``fields`` sets family workspace data (review, corrections, notes, follow-ups, documents, outcome).
    """
    _validate_edits(status, assigned_to)
    fields = dict(fields or {})
    if set(fields) - set(FAMILY_FIELDS):
        raise ValueError("Only family workspace fields can be changed here.")
    if _database_configured():
        try:
            from psycopg.types.json import Jsonb

            with _connection() as connection:
                acknowledged = _reconcile_pending(connection)
                if estate_id is None:
                    latest = connection.execute("SELECT id FROM estates ORDER BY created_at DESC, id DESC LIMIT 1").fetchone()
                    if latest is None:
                        raise KeyError("No estate has been analyzed yet.")
                    estate_id = latest["id"]
                row = connection.execute(
                    "SELECT data, status, assigned_to FROM accounts WHERE estate_id = %s AND id = %s FOR UPDATE",
                    (estate_id, acct_id),
                ).fetchone()
                if row is None:
                    raise KeyError(acct_id)
                account = dict(row["data"], status=row["status"], assigned_to=row["assigned_to"])
                if status is not None:
                    account["status"] = status
                if assigned_to is not UNSET:
                    account["assigned_to"] = assigned_to.strip() if isinstance(assigned_to, str) else None
                account.update(copy.deepcopy(fields))
                connection.execute(
                    "UPDATE accounts SET data = %s, status = %s, assigned_to = %s, updated_at = now() WHERE estate_id = %s AND id = %s",
                    (Jsonb(account), account["status"], account["assigned_to"], estate_id, acct_id),
                )
                estate = _remote_load(connection, estate_id)
            _acknowledge_pending(acknowledged)
            _mirror_remote(estate)
            return copy.deepcopy(account)
        except KeyError:
            raise
        except _database_errors():
            _database_unavailable()
    with _local_lock() as directory:
        store = _read_store(directory)
        estate = _latest(store, estate_id)
        if estate is None:
            raise KeyError("No matching estate has been analyzed yet.")
        account = next((item for item in estate["accounts"] if item["id"] == acct_id), None)
        if account is None:
            raise KeyError(acct_id)
        if status is not None:
            account["status"] = status
        if assigned_to is not UNSET:
            account["assigned_to"] = assigned_to.strip() if isinstance(assigned_to, str) else None
        account.update(copy.deepcopy(fields))
        if _database_configured(estate):
            patch = copy.deepcopy(fields)
            if status is not None:
                patch["status"] = status
            if assigned_to is not UNSET:
                patch["assigned_to"] = account["assigned_to"]
            if patch:
                _queue_patch(store, estate, account, patch)
        _write_store(directory, store, _latest(store))
        return copy.deepcopy(account)


def add_account(estate_id: int | None, account: dict[str, Any]) -> dict[str, Any]:
    """Add an account the family knows about. It is saved where the estate lives (Neon or local)."""
    account = copy.deepcopy(account)
    if _database_configured():
        try:
            from psycopg.types.json import Jsonb

            with _connection() as connection:
                if estate_id is None:
                    latest = connection.execute("SELECT id FROM estates ORDER BY created_at DESC, id DESC LIMIT 1").fetchone()
                    if latest is None:
                        raise KeyError("No estate has been analyzed yet.")
                    estate_id = latest["id"]
                existing = connection.execute("SELECT id, data FROM accounts WHERE estate_id = %s FOR UPDATE", (estate_id,)).fetchall()
                if any(_identity(row["data"]) == _identity(account) for row in existing):
                    raise ValueError("This account is already listed.")
                account["id"] = _next_account_id({row["id"] for row in existing})
                connection.execute(
                    "INSERT INTO accounts (estate_id, id, data, status, assigned_to) VALUES (%s, %s, %s, %s, %s)",
                    (estate_id, account["id"], Jsonb(account), account["status"], account.get("assigned_to")),
                )
                row = connection.execute("SELECT metadata FROM estates WHERE id = %s", (estate_id,)).fetchone()
                metadata = dict(row["metadata"] or {})
                metadata["_account_order"] = list(metadata.get("_account_order", [])) + [account["id"]]
                connection.execute("UPDATE estates SET metadata = %s WHERE id = %s", (Jsonb(metadata), estate_id))
                estate = _remote_load(connection, estate_id)
            _mirror_remote(estate)
            return account
        except (KeyError, ValueError):
            raise
        except _database_errors():
            # A family-added account must not silently exist on one laptop only.
            raise RuntimeError("The shared family database is unavailable. Try adding the account again shortly.") from None
    with _local_lock() as directory:
        store = _read_store(directory)
        estate = _latest(store, estate_id)
        if estate is None:
            raise KeyError("No matching estate has been analyzed yet.")
        if any(_identity(item) == _identity(account) for item in estate["accounts"]):
            raise ValueError("This account is already listed.")
        account["id"] = _next_account_id({item["id"] for item in estate["accounts"]})
        estate["accounts"].append(account)
        _write_store(directory, store, _latest(store))
        return copy.deepcopy(account)


def _next_account_id(taken: set[str]) -> str:
    index = 0
    while f"acct_{index:02d}" in taken:
        index += 1
    return f"acct_{index:02d}"


def delete_person(persona: dict[str, Any]) -> None:
    """Remove every saved analysis, account and activity for one person (used when a family deletes an upload)."""
    key = _persona_key(persona)
    if _database_configured():
        try:
            with _connection() as connection:
                connection.execute(
                    "DELETE FROM estates WHERE lower(coalesce(nullif(persona->>'email', ''), persona->>'name')) = %s", (key,))
                connection.execute("DELETE FROM relatives WHERE deceased_email = %s", (str(persona.get("email", "")).casefold(),))
        except _database_errors():
            raise RuntimeError("The shared family database is unavailable, so the upload was not deleted. Try again shortly.") from None


def log_activity(estate_id: int | None, acct_id: str | None, actor: str, action: str) -> dict[str, Any]:
    """Record a family action locally and in Neon when configured."""
    if not actor.strip() or not action.strip():
        raise ValueError("An activity needs an actor and an action.")
    remote_row = None
    sync_key = uuid.uuid4().hex
    if _database_configured():
        try:
            with _connection() as connection:
                acknowledged = _reconcile_pending(connection)
                if estate_id is None:
                    latest = connection.execute("SELECT id FROM estates ORDER BY created_at DESC, id DESC LIMIT 1").fetchone()
                    if latest is None:
                        raise KeyError("No estate has been analyzed yet.")
                    estate_id = latest["id"]
                remote_row = connection.execute(
                    "INSERT INTO activity (estate_id, account_id, actor, action, sync_key) VALUES (%s, %s, %s, %s, %s) RETURNING *",
                    (estate_id, acct_id, actor, action, sync_key),
                ).fetchone()
                remote_row["created_at"] = remote_row["created_at"].isoformat()
            _acknowledge_pending(acknowledged)
        except KeyError:
            raise
        except _database_errors():
            _database_unavailable()
    with _local_lock() as directory:
        store = _read_store(directory)
        estate = _latest(store, estate_id)
        if estate is None and remote_row is None:
            raise KeyError("No matching estate has been analyzed yet.")
        row = remote_row or {
            "id": store["next_activity_id"], "estate_id": estate["estate_id"],
            "account_id": acct_id, "actor": actor, "action": action,
            "created_at": datetime.now(UTC).isoformat(),
            "sync_key": sync_key,
        }
        store["next_activity_id"] = max(store["next_activity_id"], row["id"] + 1)
        store["activity"].append(row)
        if remote_row is None and _database_configured(estate):
            account = next((item for item in estate["accounts"] if item["id"] == acct_id), None)
            store["pending_activity"].append({
                "id": sync_key, "persona": {key: estate["persona"].get(key, "") for key in ("email", "name")},
                "institution": account["institution"] if account else None, "category": account["category"] if account else None,
                "activity": row,
                "synthetic": (estate.get("analysis") or {}).get("synthetic"),
            })
        _write_store(directory, store)
        return _public_activity(copy.deepcopy(row))


def get_activity(estate_id: int | None = None, limit: int = 20) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 100))
    if _database_configured():
        try:
            with _connection() as connection:
                acknowledged = _reconcile_pending(connection)
                if estate_id is None:
                    latest = connection.execute("SELECT id FROM estates ORDER BY created_at DESC, id DESC LIMIT 1").fetchone()
                    if latest is None:
                        return []
                    estate_id = latest["id"]
                rows = connection.execute(
                    "SELECT * FROM activity WHERE estate_id = %s ORDER BY created_at DESC, id DESC LIMIT %s",
                    (estate_id, limit),
                ).fetchall()
            _acknowledge_pending(acknowledged)
            return [_public_activity(dict(row, created_at=row["created_at"].isoformat())) for row in rows]
        except _database_errors():
            _database_unavailable()
    with _local_lock() as directory:
        store = _read_store(directory)
        estate = _latest(store, estate_id)
        if estate is None:
            return []
        rows = [row for row in store["activity"] if row["estate_id"] == estate["estate_id"]]
        # Log insertion order remains reliable if the machine's wall clock is
        # adjusted. Replayed rows replace their original positions in the store.
        rows.reverse()
        return [_public_activity(copy.deepcopy(row)) for row in rows[:limit]]


def register_relatives(persona: dict[str, Any], relatives: list[dict[str, Any]]) -> None:
    """Record the deceased person's relatives in Neon (each linked to a private member id)."""
    if not relatives or not _database_configured():
        return
    try:
        with _connection() as connection:
            connection.execute((Path(__file__).parent / "schema.sql").read_text(encoding="utf-8"))
            for relative in relatives:
                connection.execute(
                    "INSERT INTO relatives (deceased_name, deceased_email, relative_name, relationship, is_executor, member_id) "
                    "VALUES (%s, %s, %s, %s, %s, lastly_member_id(%s)) ON CONFLICT (deceased_email, relative_name) "
                    "DO UPDATE SET relationship = EXCLUDED.relationship, is_executor = EXCLUDED.is_executor, member_id = EXCLUDED.member_id",
                    (persona.get("name", ""), str(persona.get("email", "")).casefold(), relative["name"], relative["relationship"],
                     relative["executor"], relative["name"]),
                )
    except _database_errors():
        _database_unavailable()


def clear_activity(persona: dict[str, Any]) -> None:
    """Remove this person's family activity (demo resets only), in Neon and the local store."""
    key = _persona_key(persona)
    if _database_configured():
        try:
            with _connection() as connection:
                connection.execute(
                    "DELETE FROM activity WHERE estate_id IN (SELECT id FROM estates WHERE lower(coalesce(nullif(persona->>'email', ''), persona->>'name')) = %s)",
                    (key,),
                )
        except _database_errors():
            _database_unavailable()
    with _local_lock() as directory:
        store = _read_store(directory)
        ids = {estate["estate_id"] for estate in store["estates"] if _persona_key(estate["persona"]) == key}
        store["activity"] = [row for row in store["activity"] if row.get("estate_id") not in ids]
        store["pending_activity"] = [row for row in store["pending_activity"] if _persona_key(row["persona"]) != key]
        store["pending_patches"] = [row for row in store["pending_patches"] if _persona_key(row["persona"]) != key]
        _write_store(directory, store)
