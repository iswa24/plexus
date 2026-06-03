"""Tests for the NL2SQL stack against the seeded SQLite security databases.

These run for real against SQLite (no model key needed) and are skipped if the
databases haven't been seeded yet.
"""
import asyncio
import os

import pytest

from plexus.auth import Principal
from plexus.config import get_settings
from plexus.connectors import sqldb
from plexus.connectors.nl2sql import run_nl2sql

_settings = get_settings()
_seeded = os.path.exists(sqldb._resolve(_settings.incidents_db)) and \
    os.path.exists(sqldb._resolve(_settings.assets_db))
pytestmark = pytest.mark.skipif(not _seeded, reason="run scripts/seed_security.py first")


class _Ctx:
    def __init__(self, settings):
        self.settings = settings
        self.principal = Principal("tester")

    def resolve(self, text, for_prompt=False):
        return text


def test_schema_exposes_both_databases():
    conn = sqldb.open_db(_settings)
    try:
        schema = sqldb.schema_text(conn)
    finally:
        conn.close()
    assert "incidents(" in schema
    assert "assets.assets(" in schema
    assert "assets.identities(" in schema


def test_cross_database_select_works():
    conn = sqldb.open_db(_settings)
    try:
        res = sqldb.run_select(conn, (
            "SELECT i.id FROM incidents i "
            "JOIN assets.assets a ON i.asset_id=a.id "
            "WHERE i.severity='P1' AND a.business_unit='Finance'"))
    finally:
        conn.close()
    assert res["kind"] == "rows"
    assert len(res["rows"]) >= 1


def test_writes_are_rejected():
    conn = sqldb.open_db(_settings)
    try:
        for bad in ["DELETE FROM incidents", "DROP TABLE incidents", "UPDATE incidents SET severity='P4'"]:
            with pytest.raises(ValueError):
                sqldb.run_select(conn, bad)
    finally:
        conn.close()


def test_nl2sql_demo_fallback_runs_real_sql():
    # no anthropic key in test env → demo path, but it executes real SQL
    out = asyncio.run(run_nl2sql({"goal": "open P1 incidents", "maxSteps": 3}, _Ctx(_settings), _noop))
    assert out["kind"] == "agent"
    calls = [s for s in out["steps"] if s["type"] == "tool_call"]
    assert calls and "select" in calls[0]["input"].lower()


async def _noop(_):
    return None
