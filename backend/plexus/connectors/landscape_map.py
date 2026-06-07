"""Auto-Landscape — map the data platform in one node.

Inventories the connected Trino clusters (from the Connections registry), the dbt
models + lineage (dbt MCP), and the Kestra flows (Kestra MCP), then an LLM summarizes
the landscape and suggests what to build next. Output is a markdown brief (and the raw
counts) — a fast "what do we have, what should we build" for a new analyst.

Demo mode uses the connection registry (real) + simulated dbt/Kestra inventories.
"""
from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from . import llm
from . import mcp as mcpmod

Emit = Callable[[dict], Awaitable[None]]

_DEMO_MODELS = [
    {"model": "stg_incidents", "materialized": "view"},
    {"model": "stg_assets", "materialized": "view"},
    {"model": "dim_business_unit", "materialized": "table"},
    {"model": "fct_open_p1", "materialized": "incremental"},
]
_DEMO_FLOWS = [
    {"flow": "daily_incident_brief"}, {"flow": "remediation"}, {"flow": "ioc_sweep"},
]
_DEMO_SUMMARY = (
    "**Coverage:** sources feed `stg_*` → `dim_*`/`fct_*` → `mart_exec_brief`; Kestra schedules "
    "the refresh and an IOC sweep. Solid incident path.\n\n"
    "**Gaps / suggested next builds:**\n"
    "1. No model for **auth/login events** — add `stg_auth_events` to power credential-stuffing "
    "detection (see the Detection-as-Data skill).\n"
    "2. `ioc_sweep` has no downstream dbt mart — materialize its hits so they're queryable + tested.\n"
    "3. Cloud Logs cluster isn't referenced by any dbt model yet — wire it in for full coverage.\n"
    "4. Add a freshness/SLA monitor flow for `fct_open_p1` (feeds the exec brief)."
)


async def run_landscape_map(config: dict, ctx, emit: Emit) -> dict[str, Any]:
    namespace = config.get("namespace") or "company.team"
    provider, model_id = llm.provider_name(config, ctx), llm.model_name(config, ctx)
    steps: list[dict] = []

    async def push(value="", **extra):
        await emit({"output": {"kind": "agent", "steps": list(steps), "value": value,
                               "provider": provider, "model": model_id, **extra}})

    # 1) Trino sources from the Connections registry (real in any mode)
    steps.append({"type": "think", "text": "Inventorying Trino connections, dbt models, and Kestra flows…"})
    await push()
    try:
        from ..connections import Connections
        conns = [c for c in Connections(ctx.settings.db_path, ctx.settings.demo_mode).list()
                 if (c.get("kind") or "trino") == "trino"]
    except Exception:
        conns = []

    # 2) dbt models + 3) Kestra flows
    if ctx.settings.demo_mode:
        models, flows = _DEMO_MODELS, _DEMO_FLOWS
    else:
        try:
            mo = await mcpmod.agent_call("dbt", "list_models", {}, ctx)
            models = mo.get("rows") or json.loads(mo.get("value") or "[]")
        except Exception:
            models = []
        try:
            fo = await mcpmod.agent_call("kestra", "list_flows", {"namespace": namespace}, ctx)
            flows = fo.get("rows") or json.loads(fo.get("value") or "[]")
        except Exception:
            flows = []

    steps.append({"type": "tool_result", "tool": "inventory",
                  "summary": f"{len(conns)} Trino sources · {len(models)} dbt models · {len(flows)} Kestra flows"})
    await push()

    inv = ("## Data Landscape\n\n"
           f"**Trino sources ({len(conns)}):**\n"
           + ("".join(f"- {c.get('label')} — `{c.get('catalog','')}.{c.get('schema','')}`\n" for c in conns) or "- (none)\n")
           + f"\n**dbt models ({len(models)}):**\n"
           + ("".join(f"- {m.get('model')} ({m.get('materialized','?')})\n" for m in models) or "- (none)\n")
           + f"\n**Kestra flows ({len(flows)}):**\n"
           + ("".join(f"- {f.get('flow')}\n" for f in flows) or "- (none)\n"))

    if ctx.settings.demo_mode or llm.use_demo(config, ctx):
        summary = _DEMO_SUMMARY
    else:
        prompt = (f"{inv}\n\nSummarize this data landscape: coverage, how the pieces connect, and the "
                  "top gaps / what to build next (be specific to the sources/models/flows above).")
        try:
            summary = await llm.complete(prompt, "You are a data-platform architect.", config, ctx)
        except Exception as exc:
            summary = f"⚠ model provider error: {exc}"

    value = inv + "\n---\n\n### Analysis\n" + (summary or "")
    await push(value, counts={"sources": len(conns), "models": len(models), "flows": len(flows)})
    return {"kind": "agent", "steps": steps, "value": value, "provider": provider, "model": model_id,
            "counts": {"sources": len(conns), "models": len(models), "flows": len(flows)}}
