"""Configuration shared by the local app and optional cloud integrations."""
from __future__ import annotations

import os
import re
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env", override=False)
DATA_DIR = Path(os.getenv("LASTLY_DATA_DIR", str(ROOT / "data"))).expanduser().resolve()


# The estate a request works on. Each deceased person has their own data directory:
# the root data directory plus data/estates/<slug>/. Unset means the root estate.
_ACTIVE_DIR: ContextVar[Path | None] = ContextVar("lastly_active_data_dir", default=None)
_REGISTRY: dict[str, object] = {"key": None, "estates": {}}


def root_data_dir() -> Path:
    return Path(os.getenv("LASTLY_DATA_DIR", str(DATA_DIR))).expanduser()


def data_dir() -> Path:
    """The data directory for the estate this request or task is working on."""
    return _ACTIVE_DIR.get() or root_data_dir()


def use_data_dir(path: Path) -> Token:
    return _ACTIVE_DIR.set(Path(path))


def reset_data_dir(token: Token) -> None:
    _ACTIVE_DIR.reset(token)


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-") or "estate"


def estates() -> dict[str, Path]:
    """Slug -> data directory for every estate: the root, then each synthetic estate in estates/."""
    from secure_storage import read_json

    root = root_data_dir()
    candidates = [root] + sorted(path.parent for path in (root / "estates").glob("*/inbox.json"))
    key = tuple((str(path), (path / "inbox.json").stat().st_mtime_ns) for path in candidates if (path / "inbox.json").exists())
    if _REGISTRY["key"] == key:
        return dict(_REGISTRY["estates"])  # type: ignore[arg-type]
    found: dict[str, Path] = {}
    for path in candidates:
        try:
            inbox = read_json(path / "inbox.json")
        except (OSError, ValueError):
            continue
        # Synthetic estates are added from estates/. An uploaded mailbox is listed only while
        # it is protected by the family access code and encryption; the root keeps its own rules.
        if path != root and inbox.get("synthetic") is not True and not (inbox.get("imported") is True and private_imports_allowed()):
            continue
        slug = slugify(str((inbox.get("persona") or {}).get("name") or path.name))
        found.setdefault(slug, path)
    _REGISTRY.update(key=key, estates=found)
    return dict(found)


def private_imports_allowed() -> bool:
    """Real mail may be stored and opened only behind the access code and with encryption at rest."""
    from secure_storage import encryption_enabled

    return bool(os.getenv("LASTLY_ACCESS_TOKEN")) and encryption_enabled()


def env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).lower() in {"1", "true", "yes", "on"}


def now_date() -> date:
    return datetime.now(ZoneInfo("America/New_York")).date()


@dataclass
class Settings:
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", ""))
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    llm_model: str = field(default_factory=lambda: os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5"))
    llm_triage_model: str = field(default_factory=lambda: os.getenv("ANTHROPIC_TRIAGE_MODEL", "claude-haiku-4-5"))
    anthropic_workspace_id: str = field(default_factory=lambda: os.getenv("ANTHROPIC_WORKSPACE_ID", ""))
    offline: bool = field(default_factory=lambda: env_bool("LASTLY_OFFLINE", True))
    allow_private_cloud: bool = field(default_factory=lambda: env_bool("ALLOW_PRIVATE_CLOUD"))
    elevenlabs_api_key: str = field(default_factory=lambda: os.getenv("ELEVENLABS_API_KEY", ""))
    elevenlabs_agent_id: str = field(default_factory=lambda: os.getenv("ELEVENLABS_AGENT_ID", ""))
    elevenlabs_phone_number_id: str = field(default_factory=lambda: os.getenv("ELEVENLABS_PHONE_NUMBER_ID", ""))
    agent_seed: str = field(default_factory=lambda: os.getenv("AGENT_SEED", ""))
    agent_mailbox: bool = field(default_factory=lambda: env_bool("AGENT_MAILBOX", True))
    agent_port: int = field(default_factory=lambda: int(os.getenv("AGENT_PORT", "8001")))
    api_base_url: str = field(default_factory=lambda: os.getenv("LASTLY_API_BASE_URL", "http://127.0.0.1:8000"))
    family_executor: str = field(default_factory=lambda: os.getenv("FAMILY_EXECUTOR", "Daniel"))
    access_token: str = field(default_factory=lambda: os.getenv("LASTLY_ACCESS_TOKEN", ""))
    agent_token: str = field(default_factory=lambda: os.getenv("LASTLY_AGENT_TOKEN", ""))
    production: bool = field(default_factory=lambda: env_bool("LASTLY_PRODUCTION"))


def get_settings() -> Settings:
    return Settings()
