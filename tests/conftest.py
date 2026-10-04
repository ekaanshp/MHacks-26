from __future__ import annotations

import json
from datetime import date

import pytest

import pipeline
from generate_inbox import generate_inbox


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
