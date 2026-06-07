"""dbt Test-Failure Triage agent.

Runs `dbt test` (via the dbt MCP server), and when tests fail, an LLM reads the
failures and produces a root-cause + concrete fix for each. Emits an agent-style
trace (think → tool_call dbt_test → tool_result → diagnosis) so the UI shows the work.

Demo mode simulates a realistic failing run + triage (no dbt, no model call). Live
mode calls the real `dbt_test` tool and a real provider.
"""
from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from . import llm
from . import mcp as mcpmod

Emit = Callable[[dict], Awaitable[None]]

_DEMO_RESULT = {
    "select": "fct_open_p1", "passed": 12, "failed": 2,
    "failures": [
        {"test": "not_null_fct_open_p1_owner", "model": "fct_open_p1", "failures": 3,
         "message": "3 rows have a null owner"},
        {"test": "relationships_fct_open_p1_asset_id__id__ref_dim_assets_", "model": "fct_open_p1",
         "failures": 5, "message": "5 asset_id values are not present in dim_assets"},
    ],
}
_DEMO_TRIAGE = (
    "**2 dbt tests failing on `fct_open_p1`:**\n\n"
    "1. **not_null_fct_open_p1_owner** (3 null owners) — root cause: incidents land before "
    "the owner is assigned, so `stg_incidents` carries nulls. Fix: `COALESCE(owner, 'unassigned')` "
    "in `stg_incidents`, or add a `where owner is not null` to the incremental filter and backfill.\n\n"
    "2. **relationships … asset_id → dim_assets** (5 orphan asset_ids) — root cause: `dim_assets` "
    "is built after `fct_open_p1` in the schedule, so new assets are missing. Fix: add `dim_assets` "
    "as a `ref()` dependency (it already is) and reorder the Kestra run to build dims first; or make "
    "the test `warn` severity until the dim refresh lands.\n\n"
    "**Priority:** the relationships failure (orphan FKs) corrupts joins downstream in "
    "`mart_exec_brief` — fix first."
)


async def run_dbt_triage(config: dict, ctx, emit: Emit) -> dict[str, Any]:
    model = config.get("model", "")
    server = config.get("serverId", "dbt")
    provider, model_id = llm.provider_name(config, ctx), llm.model_name(config, ctx)
    steps: list[dict] = []

    async def push(value="", **extra):
        await emit({"output": {"kind": "agent", "steps": list(steps), "value": value,
                               "provider": provider, "model": model_id, **extra}})

    steps.append({"type": "think", "text": f"Running dbt tests for {model or 'all models'}…"})
    await push()

    if ctx.settings.demo_mode:
        result = dict(_DEMO_RESULT, select=model or "fct_open_p1")
    else:
        out = await mcpmod.agent_call(server, "dbt_test", {"model": model}, ctx)
        try:
            result = json.loads(out.get("value") or "{}")
        except (json.JSONDecodeError, AttributeError):
            result = {"failed": 0, "raw": out}

    failed = int(result.get("failed", 0) or 0)
    steps.append({"type": "tool_call", "tool": "dbt_test", "input": model or "all"})
    await push()
    steps.append({"type": "tool_result", "tool": "dbt_test",
                  "summary": f"{result.get('passed', '?')} passed, {failed} failed"})
    await push()

    if not failed:
        v = "✅ All dbt tests passed — nothing to triage."
        await push(v)
        return {"kind": "agent", "steps": steps, "value": v, "provider": provider, "model": model_id}

    if ctx.settings.demo_mode or llm.use_demo(config, ctx):
        v = _DEMO_TRIAGE
    else:
        prompt = (f"These dbt tests failed:\n{json.dumps(result.get('failures', result), indent=2)}\n\n"
                  "For each failure: state the likely ROOT CAUSE and a concrete FIX (SQL / model / "
                  "test / scheduling change). Then give a one-line priority order. Be specific.")
        try:
            v = await llm.complete(prompt, "You are a senior analytics engineer triaging dbt test "
                                           "failures. Be concise and actionable.", config, ctx)
        except Exception as exc:
            v = f"⚠ triage model error: {exc}"
    await push(v)
    return {"kind": "agent", "steps": steps, "value": v, "provider": provider, "model": model_id}
