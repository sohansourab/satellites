"""SQLite persistence layer: satellite TLE cache and app state.

Designed so a future PostgreSQL migration only needs to swap this module
(the rest of the code talks to these functions, not raw SQL).
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .config import settings
from .logging_conf import get_logger

log = get_logger("db")

_lock = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS satellites (
    norad_id   TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    object_id  TEXT,
    group_name TEXT NOT NULL,
    tle_line1  TEXT NOT NULL,
    tle_line2  TEXT NOT NULL,
    source     TEXT NOT NULL DEFAULT 'celestrak',
    fetched_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS app_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_satellites_group ON satellites(group_name);
"""


def _connect() -> sqlite3.Connection:
    Path(settings.database_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.database_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _lock, _connect() as conn:
        conn.executescript(_SCHEMA)
    log.info("Database ready at %s", settings.database_path)


# ---------- app state ----------

def get_state(key: str) -> Optional[str]:
    with _lock, _connect() as conn:
        row = conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None


def set_state(key: str, value: str) -> None:
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO app_state(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


# ---------- satellites ----------

def replace_group(group: str, rows: list[dict[str, Any]], source: str, fetched_at: datetime) -> int:
    """Atomically replace all cached satellites for one group."""
    ts = fetched_at.astimezone(timezone.utc).isoformat()
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM satellites WHERE group_name = ?", (group,))
        conn.executemany(
            "INSERT INTO satellites(norad_id, name, object_id, group_name, tle_line1, tle_line2, source, fetched_at)"
            " VALUES(:norad_id, :name, :object_id, :group_name, :tle_line1, :tle_line2, :source, :fetched_at)",
            [
                {
                    "norad_id": r["norad_id"],
                    "name": r["name"],
                    "object_id": r.get("object_id"),
                    "group_name": group,
                    "tle_line1": r["tle_line1"],
                    "tle_line2": r["tle_line2"],
                    "source": source,
                    "fetched_at": ts,
                }
                for r in rows
            ],
        )
    return len(rows)


def list_satellites(group: Optional[str] = None, search: Optional[str] = None) -> list[dict[str, Any]]:
    query = "SELECT * FROM satellites"
    params: list[Any] = []
    conditions: list[str] = []
    if group:
        conditions.append("group_name = ?")
        params.append(group)
    if search:
        conditions.append("(name LIKE ? OR norad_id LIKE ?)")
        like = f"%{search}%"
        params.extend([like, like])
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY name COLLATE NOCASE"
    with _lock, _connect() as conn:
        return [dict(r) for r in conn.execute(query, params).fetchall()]


def get_satellite(norad_id: str) -> Optional[dict[str, Any]]:
    with _lock, _connect() as conn:
        row = conn.execute("SELECT * FROM satellites WHERE norad_id = ?", (norad_id,)).fetchone()
        return dict(row) if row else None


def groups_summary() -> list[dict[str, Any]]:
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT group_name, COUNT(*) AS count, MAX(fetched_at) AS last_updated FROM satellites"
            " GROUP BY group_name ORDER BY group_name"
        ).fetchall()
        return [dict(r) for r in rows]
