"""dbt + Kestra Data-Ops nodes over a REAL MCP stdio round-trip.

Spawns the bundled stdio servers (mcp_servers/dbt_server.py, kestra_server.py) and
drives them through the same `_run_branded` path the canvas uses, with demo mode OFF.
Skipped if the `mcp` SDK isn't installed.
"""
import asyncio
import os
from types import SimpleNamespace

import pytest

pytest.importorskip("mcp")

from plexus.executor import _run_branded  # noqa: E402

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PY = os.path.join(_BASE, ".venv", "bin", "python")
_SERVERS = {
    "dbt": {"label": "dbt (local)", "transport": "stdio", "command": _PY,
            "args": [os.path.join(_BASE, "mcp_servers", "dbt_server.py")],
            "tools": ["list_models", "model_lineage", "dbt_test", "dbt_run"], "write_tools": ["dbt_run"]},
    "kestra": {"label": "Kestra (local)", "transport": "stdio", "command": _PY,
               "args": [os.path.join(_BASE, "mcp_servers", "kestra_server.py")],
               "tools": ["list_flows", "flow_status", "trigger_flow"], "write_tools": ["trigger_flow"]},
}

pytestmark = pytest.mark.skipif(not os.path.exists(_PY), reason="venv python not found")


class _Ctx:
    def __init__(self, demo=False):
        self.settings = SimpleNamespace(demo_mode=demo, mcp_servers=_SERVERS)

    def resolve(self, text, for_prompt=False):
        return text


def _run(node_type, cfg, demo=False):
    async def emit(_):  # the branded read path returns its output; emit is a no-op here
        return None

    return asyncio.run(_run_branded(node_type, cfg, _Ctx(demo), emit))


def test_dbt_list_models_live():
    out = _run("dbt.list", {})
    assert out["kind"] == "rows"
    models = {r["model"] for r in out["rows"]}
    assert {"stg_incidents", "fct_open_p1"} <= models


def test_dbt_lineage_live():
    out = _run("dbt.lineage", {"model": "fct_open_p1"})
    assert "stg_incidents" in out["value"]  # upstream dependency present


def test_kestra_list_flows_live():
    out = _run("kestra.flows", {"namespace": "security"})
    assert out["kind"] == "rows"
    assert any(r["flow"] == "ioc_sweep" for r in out["rows"])


def test_dbt_run_write_is_gated_then_executes():
    proposed = _run("dbt.run", {"models": "fct_open_p1"})          # not approved
    assert "PROPOSED" in proposed["value"]
    done = _run("dbt.run", {"models": "fct_open_p1", "approved": ["approved"]})  # approved + live
    assert '"status": "success"' in done["value"]


def test_write_blocked_in_demo_even_if_approved():
    # demo mode is a hard gate: approved write still must not execute live
    out = _run("kestra.trigger", {"flow": "ioc_sweep", "approved": ["approved"]}, demo=True)
    assert "PROPOSED" in out["value"]
