from __future__ import annotations

import json
from datetime import date

import pytest

import pipeline
from generate_inbox import generate_inbox


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch):
    """A developer's .env (keys, Neon, encryption, cloud consent) must never change test results."""
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_WORKSPACE_ID", "DATABASE_URL", "LASTLY_DATA_KEY", "LASTLY_ACCESS_TOKEN",
                 "LASTLY_AGENT_TOKEN", "ELEVENLABS_API_KEY", "AGENT_SEED"):
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("LASTLY_OFFLINE", "true")
    monkeypatch.setenv("ALLOW_PRIVATE_CLOUD", "false")


@pytest.fixture
def dataset(monkeypatch, tmp_path):
    monkeypatch.setenv("LASTLY_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LASTLY_OFFLINE", "true")
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("LASTLY_ACCESS_TOKEN", "")
    monkeypatch.setenv("LASTLY_AGENT_TOKEN", "")
    monkeypatch.setenv("LASTLY_PRODUCTION", "false")
    monkeypatch.setenv("LASTLY_DATA_KEY", "")
    monkeypatch.setenv("LASTLY_ALLOWED_HOSTS", "localhost,127.0.0.1,::1")
    monkeypatch.setenv("LASTLY_ALLOWED_ORIGINS", "")
    monkeypatch.setenv("LASTLY_ALLOWED_CALL_NUMBERS", "")
    monkeypatch.setenv("ALLOW_PRIVATE_CLOUD", "false")
    # Most tests use one estate; tests for the five-person demo enable the others explicitly.
    monkeypatch.setenv("LASTLY_EXTRA_ESTATES", "false")
    monkeypatch.delenv("LASTLY_RESET_ON_START", raising=False)
    monkeypatch.setattr(pipeline, "DATA_DIR", tmp_path)
    generate_inbox(today=date(2026, 10, 3), output_dir=tmp_path)
    return tmp_path


@pytest.fixture
def estate_data(dataset):
    return pipeline.run(mock=True, data_dir=dataset)


@pytest.fixture
def make_private(dataset):
    def private():
        path = dataset / "inbox.json"
        inbox = json.loads(path.read_text())
        inbox["synthetic"] = False
        inbox["persona"]["name"] = "Private Person"
        inbox["persona"]["email"] = "private@example.com"
        path.write_text(json.dumps(inbox))
        return pipeline.run(mock=True, data_dir=dataset)
    return private
