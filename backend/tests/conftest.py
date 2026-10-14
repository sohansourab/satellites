"""Shared pytest fixtures: isolated temp DB, demo mode, no network."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


@pytest.fixture()
def app_env(tmp_path, monkeypatch):
    """Point the app at a throwaway SQLite DB and force demo data (no network)."""
    monkeypatch.setenv("ORBITDESK_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("ORBITDESK_DATA_DIR", str(tmp_path / "skyfield"))
    monkeypatch.setenv("ORBITDESK_FORCE_DEMO_MODE", "true")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    # Re-import config/db so they pick up the new env before app import.
    for mod in [m for m in list(sys.modules) if m.startswith(("backend.app", "app."))]:
        del sys.modules[mod]
    from backend.app import db as app_db
    app_db.init_db()
    yield
    for mod in [m for m in list(sys.modules) if m.startswith(("backend.app", "app."))]:
        del sys.modules[mod]


@pytest.fixture()
def client(app_env):
    from fastapi.testclient import TestClient
    from backend.app.main import app
    with TestClient(app) as c:
        yield c
