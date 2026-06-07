"""Named connection registry — lets analysts add/manage multiple remote Trino
clients (and other sources) from the UI instead of a single .env cluster.

Backed by SQLite (same pattern as registry.py). Secrets (password/jwt) are kept
in the stored config but never returned by the API list/get. In demo mode the
store is seeded with three sample cyber clusters so the builder can connect to
"3 clients" with zero infrastructure. Swap in encryption-at-rest + a secrets
manager for production (see AGENTS.md).
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone

_SECRET_KEYS = {"password", "jwtSecret", "token"}

# Seeded in demo mode so the demo can "connect to 3 clients" out of the box.
_DEMO_SEED = [
    {"label": "Threat Intel (Trino)", "kind": "trino", "host": "intel.trino.local",
     "port": 8443, "scheme": "https", "catalog": "threat_intel", "schema": "iocs", "authType": "obo"},
    {"label": "Incident DB (Trino)", "kind": "trino", "host": "warehouse.trino.local",
     "port": 8443, "scheme": "https", "catalog": "security", "schema": "incidents", "authType": "obo"},
    {"label": "Cloud Logs (Trino)", "kind": "trino", "host": "logs.trino.local",
     "port": 8443, "scheme": "https", "catalog": "cloud", "schema": "logs", "authType": "obo"},
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _public(row: dict) -> dict:
    """Strip secrets for API responses."""
    return {k: v for k, v in row.items() if k not in _SECRET_KEYS}


class Connections:
    def __init__(self, path: str, demo_mode: bool = False):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        self.demo_mode = demo_mode
        with self._lock:
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS connections(
                    id TEXT PRIMARY KEY, label TEXT, kind TEXT,
                    config TEXT, created_at TEXT, updated_at TEXT)"""
            )
            self._conn.commit()
            n = self._conn.execute("SELECT COUNT(*) FROM connections").fetchone()[0]
        if demo_mode and not n:
            for s in _DEMO_SEED:
                self.create(s)

    def _row(self, r) -> dict:
        cfg = json.loads(r[3]) if r[3] else {}
        return {"id": r[0], "label": r[1], "kind": r[2],
                "createdAt": r[4], "updatedAt": r[5], **cfg}

    def list(self) -> list[dict]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT id,label,kind,config,created_at,updated_at FROM connections ORDER BY created_at")
            return [_public(self._row(r)) for r in cur.fetchall()]

    def get(self, cid: str, *, with_secrets: bool = False) -> dict | None:
        with self._lock:
            r = self._conn.execute(
                "SELECT id,label,kind,config,created_at,updated_at FROM connections WHERE id=?",
                (cid,)).fetchone()
        if not r:
            return None
        row = self._row(r)
        return row if with_secrets else _public(row)

    def create(self, body: dict) -> dict:
        cid = "conn_" + uuid.uuid4().hex[:8]
        label = body.get("label") or "Untitled connection"
        kind = body.get("kind") or "trino"
        cfg = {k: v for k, v in body.items() if k not in ("id", "label", "kind", "createdAt", "updatedAt")}
        ts = _now()
        with self._lock:
            self._conn.execute("INSERT INTO connections VALUES(?,?,?,?,?,?)",
                               (cid, label, kind, json.dumps(cfg), ts, ts))
            self._conn.commit()
        return self.get(cid)

    def update(self, cid: str, body: dict) -> dict | None:
        cur = self.get(cid, with_secrets=True)
        if not cur:
            return None
        label = body.get("label", cur["label"])
        kind = body.get("kind", cur.get("kind", "trino"))
        merged = {k: v for k, v in cur.items()
                  if k not in ("id", "label", "kind", "createdAt", "updatedAt")}
        for k, v in body.items():
            if k not in ("id", "label", "kind", "createdAt", "updatedAt"):
                merged[k] = v
        with self._lock:
            self._conn.execute("UPDATE connections SET label=?,kind=?,config=?,updated_at=? WHERE id=?",
                               (label, kind, json.dumps(merged), _now(), cid))
            self._conn.commit()
        return self.get(cid)

    def delete(self, cid: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM connections WHERE id=?", (cid,))
            self._conn.commit()

    async def test(self, cid: str, principal=None) -> dict:
        """Probe a connection. Demo mode (or a *.local host) returns a friendly
        synthetic OK so the demo works with no real cluster."""
        c = self.get(cid, with_secrets=True)
        if not c:
            return {"ok": False, "error": "connection not found"}
        host = c.get("host", "")
        if self.demo_mode or host.endswith(".local") or not host:
            return {"ok": True, "version": "Trino 443 (demo)",
                    "detail": f"{c.get('catalog','')}.{c.get('schema','')}"}
        try:
            import asyncio
            import trino  # noqa: WPS433
            from trino.auth import JWTAuthentication

            def _probe():
                auth = JWTAuthentication(getattr(principal, "token", None)) \
                    if (c.get("authType") in ("jwt", "obo") and getattr(principal, "token", None)) else None
                conn = trino.dbapi.connect(
                    host=host, port=int(c.get("port", 8443)),
                    user=(getattr(principal, "username", None) or "plexus"),
                    catalog=c.get("catalog"), schema=c.get("schema"),
                    http_scheme=c.get("scheme", "https"), auth=auth)
                cur = conn.cursor()
                cur.execute("SELECT 1")
                cur.fetchone()
                return "Trino (reachable)"
            ver = await asyncio.to_thread(_probe)
            return {"ok": True, "version": ver, "detail": f"{c.get('catalog','')}.{c.get('schema','')}"}
        except Exception as exc:  # pragma: no cover
            return {"ok": False, "error": str(exc)[:200]}
