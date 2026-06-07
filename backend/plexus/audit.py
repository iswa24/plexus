"""Immutable-ish audit log of every node execution.

At a bank this is a compliance requirement: who ran what query / prompt,
against which app, when. Persisted to SQLite here; point it at an append-only
store (or your SIEM) in production.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AuditLog:
    def __init__(self, path: str):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS audit(
                    id TEXT PRIMARY KEY,
                    ts TEXT,
                    principal TEXT,
                    app_id TEXT,
                    run_id TEXT,
                    node_id TEXT,
                    node_type TEXT,
                    detail TEXT
                )"""
            )
            self._conn.commit()

    def log(
        self,
        *,
        principal: str,
        app_id: str,
        run_id: str,
        node_id: str,
        node_type: str,
        detail: dict,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO audit VALUES(?,?,?,?,?,?,?,?)",
                (
                    uuid.uuid4().hex,
                    _now(),
                    principal,
                    app_id,
                    run_id,
                    node_id,
                    node_type,
                    json.dumps(detail)[:10000],
                ),
            )
            self._conn.commit()

    def runs(self, limit: int = 50, app_id: str | None = None) -> list[dict]:
        """Recent executions, grouped by run_id (for the Executions history UI)."""
        where, params = ("WHERE app_id=?", [app_id]) if app_id else ("", [])
        params.append(limit)
        with self._lock:
            cur = self._conn.execute(
                f"""SELECT run_id, app_id, principal, MIN(ts) started, MAX(ts) ended,
                          COUNT(*) nodes, GROUP_CONCAT(node_type) types,
                          SUM(CASE WHEN detail LIKE '%\"status\": \"error\"%' THEN 1 ELSE 0 END) errors
                    FROM audit {where} GROUP BY run_id ORDER BY started DESC LIMIT ?""",
                params,
            )
            cols = ["run_id", "app_id", "principal", "started", "ended", "nodes", "types", "errors"]
            out = []
            for r in cur.fetchall():
                row = dict(zip(cols, r))
                row["types"] = (row["types"] or "").split(",")
                row["status"] = "error" if (row.get("errors") or 0) else "success"
                out.append(row)
            return out

    def run(self, run_id: str) -> list[dict]:
        """Per-node detail for one execution."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT ts, principal, app_id, run_id, node_id, node_type, detail "
                "FROM audit WHERE run_id=? ORDER BY ts ASC", (run_id,))
            cols = ["ts", "principal", "app_id", "run_id", "node_id", "node_type", "detail"]
            out = []
            for r in cur.fetchall():
                row = dict(zip(cols, r))
                row["detail"] = json.loads(row["detail"]) if row["detail"] else {}
                out.append(row)
            return out

    def recent(self, limit: int = 100) -> list[dict]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT ts, principal, app_id, run_id, node_id, node_type, detail "
                "FROM audit ORDER BY ts DESC LIMIT ?",
                (limit,),
            )
            cols = ["ts", "principal", "app_id", "run_id", "node_id", "node_type", "detail"]
            out = []
            for r in cur.fetchall():
                row = dict(zip(cols, r))
                row["detail"] = json.loads(row["detail"]) if row["detail"] else {}
                out.append(row)
            return out
