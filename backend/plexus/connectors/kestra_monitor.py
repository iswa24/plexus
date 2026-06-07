"""Kestra Execution Monitor + Alert agent.

Lists recent Kestra executions (via the Kestra MCP server), then an LLM summarizes
pipeline health, highlights failures, and recommends actions (rerun / triage /
escalate). Emits an agent-style trace.

Demo mode simulates a recent-execution set (with failures) + an alert summary. Live
mode calls the real `list_executions` tool and a real provider.
"""
from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from . import llm
from . import mcp as mcpmod

Emit = Callable[[dict], Awaitable[None]]

_FAIL_STATES = {"FAILED", "KILLED", "WARNING"}

_DEMO_EXECS = [
    {"executionId": "exec_2026060706", "flow": "daily_incident_brief", "state": "SUCCESS"},
    {"executionId": "exec_2026060705", "flow": "ioc_sweep", "state": "FAILED"},
    {"executionId": "exec_2026060704", "flow": "ioc_sweep", "state": "FAILED"},
    {"executionId": "exec_2026060703", "flow": "remediation", "state": "SUCCESS"},
    {"executionId": "exec_2026060702", "flow": "daily_incident_brief", "state": "SUCCESS"},
    {"executionId": "exec_2026060701", "flow": "ioc_sweep", "state": "RUNNING"},
]


async def run_kestra_monitor(config: dict, ctx, emit: Emit) -> dict[str, Any]:
    namespace = config.get("namespace", "") or "company.team"
    limit = int(config.get("limit", 25) or 25)
    server = config.get("serverId", "kestra")
    provider, model_id = llm.provider_name(config, ctx), llm.model_name(config, ctx)
    steps: list[dict] = []

    async def push(value="", **extra):
        await emit({"output": {"kind": "agent", "steps": list(steps), "value": value,
                               "provider": provider, "model": model_id, **extra}})

    steps.append({"type": "think", "text": f"Checking recent Kestra executions in '{namespace}'…"})
    await push()
    steps.append({"type": "tool_call", "tool": "list_executions", "input": namespace})
    await push()

    if ctx.settings.demo_mode:
        execs = _DEMO_EXECS
    else:
        out = await mcpmod.agent_call(server, "list_executions",
                                      {"namespace": namespace, "limit": limit}, ctx)
        try:
            execs = out.get("rows") if isinstance(out, dict) and out.get("rows") else json.loads(out.get("value") or "[]")
        except (json.JSONDecodeError, AttributeError):
            execs = []

    failures = [e for e in execs if str(e.get("state", "")).upper() in _FAIL_STATES]
    steps.append({"type": "tool_result", "tool": "list_executions",
                  "summary": f"{len(execs)} executions · {len(failures)} need attention"})
    await push()

    if not failures:
        v = f"✅ All {len(execs)} recent executions in '{namespace}' are healthy — no action needed."
        await push(v)
        return {"kind": "agent", "steps": steps, "value": v, "provider": provider,
                "model": model_id, "alert": False}

    if ctx.settings.demo_mode or llm.use_demo(config, ctx):
        flows = ", ".join(sorted({f.get("flow", "?") for f in failures}))
        v = (f"🚨 **Kestra alert — {len(failures)} failed execution(s) in `{namespace}`**\n\n"
             f"Affected flow(s): **{flows}**\n\n"
             "- `ioc_sweep` failed twice in the last hour — likely a transient upstream/source error "
             "or a bad input. Recommend: inspect the latest execution logs, then **replay** the failed "
             "runs; if it fails a third time, escalate to the data-platform on-call.\n"
             "- Downstream impact: IOC sweeps feed threat-intel enrichment — gaps until this recovers.\n\n"
             "Suggested action: trigger a re-run of `ioc_sweep` (approval-gated).")
    else:
        prompt = (f"Recent Kestra executions in namespace '{namespace}':\n{json.dumps(execs, indent=2)}\n\n"
                  "Summarize pipeline health, call out the failures (flow + how many), infer the likely "
                  "cause and downstream impact, and recommend specific actions (replay / triage / escalate). "
                  "Start with a one-line alert headline.")
        try:
            v = await llm.complete(prompt, "You are an SRE monitoring data orchestration. Be concise "
                                           "and actionable.", config, ctx)
        except Exception as exc:
            v = f"⚠ monitor model error: {exc}"
    await push(v, alert=True)
    return {"kind": "agent", "steps": steps, "value": v, "provider": provider,
            "model": model_id, "alert": True}
