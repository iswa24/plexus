"""Trino Query Cost Guard — estimate-then-gate (demo path, no cluster)."""
import asyncio
from types import SimpleNamespace

from plexus.connectors.trino_guard import run_trino_guard


class _Ctx:
    def __init__(self):
        self.settings = SimpleNamespace(demo_mode=True, db_path=":memory:", trino_host="",
                                        trino_port=8080, trino_scheme="https", trino_catalog="hive")
        self.principal = SimpleNamespace(username="tester", token=None)

    def resolve(self, text, for_prompt=False):
        return text


def _run(cfg):
    async def emit(_):
        return None

    return asyncio.run(run_trino_guard(cfg, _Ctx(), emit))


def test_blocks_full_table_scan():
    out = _run({"sql": "SELECT * FROM events", "maxScanRows": 1_000_000})
    assert out.get("blocked") is True
    assert out["kind"] == "agent"
    assert "BLOCKED" in out["value"]


def test_approve_overrides_and_runs():
    out = _run({"sql": "SELECT * FROM events", "maxScanRows": 1_000_000, "approved": ["approved"]})
    assert out["kind"] == "rows"          # it ran
    assert out.get("guard") == "approved"


def test_filtered_query_passes_and_runs():
    out = _run({"sql": "SELECT id FROM events WHERE severity='P1'", "maxScanRows": 1_000_000})
    assert out["kind"] == "rows"
    assert out.get("guard") == "passed"
    assert out.get("estimate")            # estimate attached for the audit trail
