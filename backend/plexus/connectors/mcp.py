"""MCP (Model Context Protocol) connector.

Reach ANY MCP server's tools/resources through ONE generic node instead of a
bespoke connector per system. Two node types:

  source.mcp   read a resource or call a read-only tool  -> rows | text
  tool.mcp     invoke a named tool as a step; WRITE tools are APPROVAL-GATED

Governance (this is a bank): servers come from an admin ALLOW-LIST in settings
(`mcp_servers`), never an arbitrary user-supplied command in production. The
official `mcp` SDK is imported lazily; demo mode (or a missing SDK) falls back to
canned sample data so cards run end-to-end with no external server. All MCP
output is treated as untrusted data; write tools never execute unless approved
AND not in demo mode (same gate as action.webhook).
"""
from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

Emit = Callable[[dict], Awaitable[None]]

# Built-in demo server so MCP cards run with zero external dependency.
_DEMO_SERVERS = {
    "demo": {
        "label": "Demo MCP server (sample security tools)",
        "transport": "demo",
        "tools": ["search_iocs", "enrich_user", "lookup_cve"],
        "resources": ["kb://ir-runbook", "kb://access-policy"],
    },
}

_DEMO_TOOL_ROWS = {
    "search_iocs": {
        "columns": ["indicator", "type", "score", "verdict"],
        "rows": [
            {"indicator": "185.23.41.9", "type": "ip", "score": 92, "verdict": "malicious"},
            {"indicator": "finance-login[.]co", "type": "domain", "score": 76, "verdict": "suspicious"},
        ],
    },
    "enrich_user": {
        "columns": ["user", "dept", "mfa", "risk"],
        "rows": [{"user": "a.kumar", "dept": "Finance", "mfa": "disabled", "risk": "high"}],
    },
    "lookup_cve": {
        "columns": ["cve", "cvss", "exploited", "summary"],
        "rows": [{"cve": "CVE-2026-1337", "cvss": 9.8, "exploited": "yes",
                  "summary": "RCE in edge gateway"}],
    },
    # dbt (Iceberg models) — demo fidelity for the branded dbt.* nodes
    "list_models": {
        "columns": ["model", "materialized", "rows", "tests", "fresh_min"],
        "rows": [
            {"model": "stg_incidents", "materialized": "view", "rows": None, "tests": 4, "fresh_min": 12},
            {"model": "dim_business_unit", "materialized": "table", "rows": 8, "tests": 2, "fresh_min": 35},
            {"model": "fct_open_p1", "materialized": "incremental", "rows": 7, "tests": 5, "fresh_min": 9},
        ],
    },
    "model_lineage": {
        "columns": ["model", "upstream", "downstream"],
        "rows": [{"model": "fct_open_p1", "upstream": "stg_incidents, dim_business_unit",
                  "downstream": "mart_exec_brief"}],
    },
    # kestra (orchestration) — demo fidelity for the branded kestra.* nodes
    "list_flows": {
        "columns": ["flow", "description", "schedule"],
        "rows": [
            {"flow": "daily_incident_brief", "description": "DBT refresh → Plexus brief → Slack", "schedule": "0 6 * * *"},
            {"flow": "remediation", "description": "Quarantine host + open ticket (gated)", "schedule": None},
        ],
    },
    "flow_status": {
        "columns": ["executionId", "state", "duration_s"],
        "rows": [{"executionId": "exec_demo", "state": "SUCCESS", "duration_s": 42}],
    },
}


def servers(settings) -> dict:
    """The allow-list: built-in demo server + any admin-registered servers."""
    reg = dict(_DEMO_SERVERS)
    extra = getattr(settings, "mcp_servers", None) or {}
    if isinstance(extra, dict):
        reg.update(extra)
    return reg


def _resolve_server(config: dict, ctx) -> tuple[str, dict | None]:
    sid = config.get("serverId") or "demo"
    return sid, servers(ctx.settings).get(sid)


def _args(config: dict, ctx) -> dict:
    raw = ctx.resolve(config.get("args", "") or "", for_prompt=False)
    if not raw.strip():
        return {}
    try:
        v = json.loads(raw)
        return v if isinstance(v, dict) else {"input": v}
    except json.JSONDecodeError:
        return {"input": raw}


def _approved(config: dict) -> bool:
    a = config.get("approved")
    return ("approved" in a) if isinstance(a, list) else bool(a)


def _is_demo(ctx, srv: dict | None) -> bool:
    return bool(ctx.settings.demo_mode) or not srv or srv.get("transport") == "demo"


def _tool_payload(tool: str, max_rows: int) -> dict:
    d = _DEMO_TOOL_ROWS.get(tool)
    if d:
        return {"kind": "rows", "columns": d["columns"], "rows": d["rows"][:max_rows]}
    return {"kind": "text", "value": f"(demo) MCP tool '{tool}' returned: ok"}


# ---------------------------------------------------------------- source.mcp
async def run_mcp_resource(config: dict, ctx, emit: Emit) -> dict[str, Any]:
    """Read an MCP resource or call a read-only tool; return rows or text."""
    sid, srv = _resolve_server(config, ctx)
    mode = (config.get("mode") or "tool").lower()
    max_rows = int(config.get("maxRows", 200))

    if _is_demo(ctx, srv):
        if mode == "resource":
            uri = ctx.resolve(config.get("resourceUri", ""), for_prompt=False) or "kb://ir-runbook"
            return {"kind": "text",
                    "value": f"# {uri}\n(demo) Sample contents of MCP resource '{uri}' on server '{sid}'."}
        return _tool_payload(config.get("tool") or "search_iocs", max_rows)

    tool = config.get("tool") or ""
    uri = ctx.resolve(config.get("resourceUri", ""), for_prompt=False)
    out = await _live_call(srv, mode="resource" if mode == "resource" else "tool",
                           tool=tool, uri=uri, args=_args(config, ctx), ctx=ctx)
    if out.get("kind") == "rows":
        out["rows"] = out.get("rows", [])[:max_rows]
    return out


# ---------------------------------------------------------------- tool.mcp
async def run_mcp_tool(config: dict, ctx, emit: Emit) -> dict[str, Any]:
    """Invoke an MCP tool as a step. Write tools are proposed-only unless approved
    AND not in demo mode (governance gate, mirrors action.webhook)."""
    sid, srv = _resolve_server(config, ctx)
    tool = config.get("tool") or ""
    side = (config.get("sideEffect") or "read-only").lower()
    args = _args(config, ctx)
    steps: list[dict] = [{"type": "tool_call", "tool": f"mcp:{sid}:{tool}",
                          "input": json.dumps(args)[:300]}]

    async def push(value="", **extra):
        out = {"kind": "agent", "steps": list(steps), "value": value,
               "provider": "mcp", "model": sid, **extra}
        await emit({"output": out})
        return out

    # write gate
    if side == "write" and not (_approved(config) and not ctx.settings.demo_mode):
        steps.append({"type": "tool_result", "tool": "gate", "summary": "proposed — approval required"})
        v = (f"PROPOSED (not executed): write tool '{tool}' on MCP server '{sid}' "
             f"with args {json.dumps(args)}. Tick Approve and disable demo mode to run.")
        return await push(v, proposed=True)

    if _is_demo(ctx, srv):
        payload = _tool_payload(tool, int(config.get("maxRows", 200)))
        val = json.dumps(payload.get("rows")) if payload.get("kind") == "rows" else payload.get("value", "")
        steps.append({"type": "tool_result", "tool": tool, "summary": "ok (demo)"})
        return await push(val)

    out = await _live_call(srv, mode="tool", tool=tool, uri="", args=args, ctx=ctx)
    val = json.dumps(out.get("rows")) if out.get("kind") == "rows" else out.get("value", "")
    steps.append({"type": "tool_result", "tool": tool, "summary": "ok"})
    return await push(val)


# ---------------------------------------------------------------- model.agent integration
def agent_tool_specs(server_ids, settings) -> list[dict]:
    """Bedrock Converse toolSpecs for every tool on the agent's allowed MCP servers
    (least-privilege: only the servers the author ticked). Named mcp_<server>_<tool>."""
    reg = servers(settings)
    specs: list[dict] = []
    for sid in (server_ids or []):
        srv = reg.get(sid)
        if not srv:
            continue
        for tool in srv.get("tools", []):
            specs.append({"toolSpec": {
                "name": f"mcp_{sid}_{tool}",
                "description": f"MCP tool '{tool}' on server '{sid}'. Pass JSON key/value arguments.",
                "inputSchema": {"json": {"type": "object", "properties": {},
                                         "description": "Arguments for the MCP tool (key/value pairs)."}},
            }})
    return specs


def parse_agent_tool(name: str, settings) -> tuple[str, str] | None:
    """Map a Bedrock tool name (mcp_<server>_<tool>) back to (serverId, tool),
    disambiguating against the registry (both ids may contain underscores)."""
    if not name or not name.startswith("mcp_"):
        return None
    for sid, srv in servers(settings).items():
        for tool in srv.get("tools", []):
            if name == f"mcp_{sid}_{tool}":
                return sid, tool
    return None


async def agent_call(sid: str, tool: str, args: dict, ctx) -> dict[str, Any]:
    """Invoke an MCP tool on behalf of model.agent, returning rows/text to feed
    back to the model. WRITE tools (server's `write_tools`) are NOT auto-executed
    by an agent — they return a proposal requiring human approval (governance)."""
    srv = servers(ctx.settings).get(sid)
    if tool in (srv or {}).get("write_tools", []):
        return {"kind": "text",
                "value": f"PROPOSED: write tool '{tool}' on '{sid}' needs human approval — not executed by the agent."}
    if _is_demo(ctx, srv):
        return _tool_payload(tool, 50)
    return await _live_call(srv, mode="tool", tool=tool, uri="", args=args or {}, ctx=ctx)


# ---------------------------------------------------------------- live transport (lazy)
async def _live_call(srv: dict, *, mode: str, tool: str, uri: str, args: dict, ctx) -> dict[str, Any]:
    try:
        from mcp import ClientSession  # noqa: WPS433 (lazy import by design)
        from mcp.client.sse import sse_client
        from mcp.client.stdio import StdioServerParameters, stdio_client
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(
            "mcp SDK not installed. `pip install mcp`, register a server in "
            "PLEXUS_MCP_SERVERS, or use the built-in demo server / PLEXUS_DEMO_MODE=true."
        ) from e

    transport = (srv.get("transport") or "stdio").lower()

    async def _do(session) -> dict[str, Any]:
        await session.initialize()
        if mode == "resource":
            res = await session.read_resource(uri)
            text = "\n".join(getattr(c, "text", "") for c in getattr(res, "contents", []) or [])
            return {"kind": "text", "value": text}
        res = await session.call_tool(tool, args or {})
        text = "\n".join(getattr(c, "text", "") for c in getattr(res, "content", []) or [])
        try:  # tabular tool output -> rows
            data = json.loads(text)
            if isinstance(data, list) and data and isinstance(data[0], dict):
                return {"kind": "rows", "columns": list(data[0].keys()), "rows": data}
        except (json.JSONDecodeError, TypeError):
            pass
        return {"kind": "text", "value": text}

    if transport == "sse":
        async with sse_client(srv["url"]) as (read, write):
            async with ClientSession(read, write) as session:
                return await _do(session)
    # Forward the server's `env` block (e.g. DBT_PROJECT_DIR, KESTRA_BASE_URL) to the
    # subprocess, merged over the parent environment so PATH etc. survive (needed for
    # the dbt CLI). If no `env` is configured, inherit the full parent environment.
    import os as _os
    env = {**_os.environ, **(srv.get("env") or {})}
    params = StdioServerParameters(command=srv["command"], args=srv.get("args", []), env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            return await _do(session)
