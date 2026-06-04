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


# ---------------------------------------------------------------- model.agent <-> MCP
def test_agent_tool_specs_built_for_allowed_servers():
    specs = mcp.agent_tool_specs(["demo"], settings)
    names = [s["toolSpec"]["name"] for s in specs]
    assert "mcp_demo_search_iocs" in names
    # least-privilege: an un-listed server contributes nothing
    assert mcp.agent_tool_specs([], settings) == []


def test_parse_agent_tool_roundtrips_with_underscores():
    assert mcp.parse_agent_tool("mcp_demo_search_iocs", settings) == ("demo", "search_iocs")
    assert mcp.parse_agent_tool("query_warehouse", settings) is None


def test_agent_call_read_returns_rows_in_demo():
    out = _await(mcp.agent_call("demo", "search_iocs", {"query": "ip"}, _ctx()))
    assert out["kind"] == "rows" and out["rows"]


def test_agent_loop_uses_mcp_tool_in_demo():
    """The demo agent loop, given an allowed MCP server, shows an MCP tool_call step."""
    from plexus.connectors import agent
    app = AppDef(name="x", nodes=[{"id": "i", "type": "input.text", "config": {"value": "185.23.41.9"}}], edges=[])
    ctx = RunContext(app, {}, settings, PRIN)
    ctx.results["i"] = {"kind": "text", "value": "185.23.41.9"}
    out = _await(agent.run_agent(
        {"goal": "@{i}", "tools": [], "mcpServers": ["demo"]}, ctx, _noop))
    tool_calls = [s for s in out["steps"] if s.get("type") == "tool_call"]
    assert any(s["tool"].startswith("mcp_demo_") for s in tool_calls)


def test_multi_server_least_privilege_scoping():
    """An agent only sees tools from the servers it was granted (hermetic: inject
    a two-server registry rather than relying on .env)."""
    import types as _t
    stub = _t.SimpleNamespace(mcp_servers={
        "secintel": {"tools": ["search_iocs", "block_ip"]},
        "geoip": {"tools": ["geolocate_ip", "whois_domain"]},
    })
    only_geo = [s["toolSpec"]["name"] for s in mcp.agent_tool_specs(["geoip"], stub)]
    assert only_geo == ["mcp_geoip_geolocate_ip", "mcp_geoip_whois_domain"]
    assert "mcp_secintel_search_iocs" not in only_geo          # not granted -> not visible
    both = [s["toolSpec"]["name"] for s in mcp.agent_tool_specs(["secintel", "geoip"], stub)]
    assert len(both) == 4 and "mcp_secintel_block_ip" in both and "mcp_geoip_whois_domain" in both
