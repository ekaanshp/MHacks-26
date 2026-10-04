"""Configuration shared by the local app and optional cloud integrations."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env", override=False)
DATA_DIR = Path(os.getenv("LASTLY_DATA_DIR", str(ROOT / "data"))).expanduser().resolve()


def env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).lower() in {"1", "true", "yes", "on"}


def now_date() -> date:
    return datetime.now(ZoneInfo("America/New_York")).date()


@dataclass
class Settings:
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", ""))
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    llm_model: str = field(default_factory=lambda: os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6"))
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
