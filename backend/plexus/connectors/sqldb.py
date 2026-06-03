"""Read-only SQLite access for the NL2SQL agent.

Opens the incidents DB as `main` and attaches the assets DB as `assets`, so the
model can write federated cross-database SQL (e.g. `assets.assets`). This mirrors
the Trino catalog model — swapping in real Trino later is a connector change, not
an architecture change.
"""
from __future__ import annotations

import os
import re
import sqlite3

# backend/ dir, so default relative db paths resolve regardless of CWD
_BASE = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))

_WRITE = re.compile(r"\b(insert|update|delete|drop|alter|create|attach|pragma|replace|truncate)\b", re.I)


def _resolve(path: str) -> str:
    return path if os.path.isabs(path) else os.path.join(_BASE, path)


def open_db(settings) -> sqlite3.Connection:
    inc = _resolve(settings.incidents_db)
    ast = _resolve(settings.assets_db)
    if not os.path.exists(inc) or not os.path.exists(ast):
        raise RuntimeError(
            "Security databases not found. Seed them first: "
            "`python scripts/seed_security.py`."
        )
    conn = sqlite3.connect(f"file:{inc}?mode=ro", uri=True)
    conn.execute(f"ATTACH DATABASE 'file:{ast}?mode=ro' AS assets")
    conn.row_factory = sqlite3.Row
    return conn


def schema_text(conn: sqlite3.Connection) -> str:
    """Human/LLM-readable schema across both attached databases."""
    out = []
    for db, label in (("main", "incidents (main)"), ("assets", "assets")):
        tables = [
            r[0]
            for r in conn.execute(
                f"SELECT name FROM {db}.sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'"
            )
        ]
        for t in tables:
            cols = conn.execute(f"PRAGMA {db}.table_info({t})").fetchall()
            coldesc = ", ".join(f"{c[1]} {c[2]}" for c in cols)
            prefix = "" if db == "main" else "assets."
            out.append(f"{prefix}{t}({coldesc})")
    return "\n".join(out)


def run_select(conn: sqlite3.Connection, sql: str, max_rows: int = 200) -> dict:
    """Execute a single read-only SELECT and return {columns, rows}."""
    s = (sql or "").strip().rstrip(";")
    low = s.lower()
    if not (low.startswith("select") or low.startswith("with")):
        raise ValueError("only SELECT/WITH queries are allowed")
    if _WRITE.search(s):
        raise ValueError("write/DDL statements are not allowed")
    cur = conn.execute(s)
    cols = [d[0] for d in cur.description] if cur.description else []
    rows = [dict(r) for r in cur.fetchmany(max_rows)]
    return {"kind": "rows", "columns": cols, "rows": rows}
