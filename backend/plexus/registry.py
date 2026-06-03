"""App registry — persists App Definitions to SQLite.

Swap this class for a Postgres-backed repository in production; the interface
(list / get / upsert / delete) is what the API depends on.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone

from .models import AppDef


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Registry:
    def __init__(self, path: str):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS apps(
                    id TEXT PRIMARY KEY,
                    name TEXT,
                    definition TEXT,
                    created_at TEXT,
                    updated_at TEXT
                )"""
            )
            self._conn.commit()

    def list(self) -> list[dict]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT definition FROM apps ORDER BY updated_at DESC"
            )
            return [json.loads(r[0]) for r in cur.fetchall()]

    def get(self, app_id: str) -> dict | None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT definition FROM apps WHERE id=?", (app_id,)
            )
            row = cur.fetchone()
            return json.loads(row[0]) if row else None

    def upsert(self, app: AppDef) -> dict:
        now = _now()
        if not app.id:
            app.id = "app_" + uuid.uuid4().hex[:8]
            app.createdAt = now
        else:
            existing = self.get(app.id)
            app.createdAt = (existing or {}).get("createdAt") or now
        app.updatedAt = now
        data = app.model_dump()
        with self._lock:
            self._conn.execute(
                """INSERT INTO apps(id, name, definition, created_at, updated_at)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     name=excluded.name,
                     definition=excluded.definition,
                     updated_at=excluded.updated_at""",
                (app.id, app.name, json.dumps(data), app.createdAt, app.updatedAt),
            )
            self._conn.commit()
        return data

    def delete(self, app_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM apps WHERE id=?", (app_id,))
            self._conn.commit()
