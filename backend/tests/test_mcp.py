"""MCP connector: generic source.mcp (read) and tool.mcp (invoke), with a
built-in demo server (no external dependency) and write-tool approval gating."""
import asyncio

from plexus.auth import Principal
from plexus.config import get_settings
from plexus.connectors import mcp
from plexus.executor import RunContext, execute
from plexus.models import AppDef

settings = get_settings()
PRIN = Principal(username="tester")


def _await(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _noop(_f):
    return None


def _ctx():
    app = AppDef(name="x", nodes=[{"id": "i", "type": "input.text", "config": {"value": "phishing"}}], edges=[])
    ctx = RunContext(app, {}, settings, PRIN)
    ctx.results["i"] = {"kind": "text", "value": "phishing"}
    return ctx


def test_demo_server_is_always_available():
    reg = mcp.servers(settings)
    assert "demo" in reg and reg["demo"]["transport"] == "demo"


def test_source_mcp_tool_returns_rows():
    out = _await(mcp.run_mcp_resource(
        {"serverId": "demo", "mode": "tool", "tool": "search_iocs"}, _ctx(), _noop))
    assert out["kind"] == "rows"
    assert any(r.get("verdict") == "malicious" for r in out["rows"])


def test_source_mcp_resource_returns_text():
    out = _await(mcp.run_mcp_resource(
        {"serverId": "demo", "mode": "resource", "resourceUri": "kb://ir-runbook"}, _ctx(), _noop))
    assert out["kind"] == "text" and "kb://ir-runbook" in out["value"]


def test_args_resolve_refs_and_parse_json():
    ctx = _ctx()
    assert mcp._args({"args": '{"query":"@{i}"}'}, ctx) == {"query": "phishing"}
    assert mcp._args({"args": "not json"}, ctx) == {"input": "not json"}


def test_tool_mcp_read_only_executes_in_demo():
    out = _await(mcp.run_mcp_tool(
        {"serverId": "demo", "tool": "enrich_user", "sideEffect": "read-only"}, _ctx(), _noop))
    assert out["kind"] == "agent"
    assert not out.get("proposed")
    assert "a.kumar" in out["value"]


def test_tool_mcp_write_is_proposed_only_in_demo():
    """A write tool must NOT execute (proposed-only) unless approved AND not demo."""
    out = _await(mcp.run_mcp_tool(
        {"serverId": "demo", "tool": "block_ip", "sideEffect": "write", "approved": ["approved"]},
        _ctx(), _noop))
    assert out.get("proposed") is True          # demo_mode forces propose-only
    assert "PROPOSED" in out["value"]


def test_source_mcp_runs_through_executor():
    app = AppDef(
        name="mcp-flow",
        nodes=[
            {"id": "q", "type": "input.text", "config": {"value": "iocs"}},
            {"id": "m", "type": "source.mcp", "config": {"serverId": "demo", "mode": "tool", "tool": "search_iocs"}},
            {"id": "o", "type": "output.table", "config": {"source": "@{m}"}},
        ],
        edges=[{"id": "e1", "source": "q", "target": "m"}, {"id": "e2", "source": "m", "target": "o"}],
    )
    results = _await(execute(app, {}, settings, PRIN, _noop))
    assert results["m"]["kind"] == "rows"
    assert results["o"]["kind"] == "rows"
