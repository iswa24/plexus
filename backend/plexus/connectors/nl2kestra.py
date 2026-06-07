"""NL → Kestra flow generator.

Turns a plain-English request into a deployable Kestra flow (YAML): id, namespace,
inputs, tasks, and a schedule trigger. Optionally grounds on the existing flows in the
namespace (via the Kestra MCP server) for naming/consistency.

Demo mode returns a realistic security-orchestration flow (no model call).
"""
from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from . import llm
from . import mcp as mcpmod

Emit = Callable[[dict], Awaitable[None]]

_DEMO = """```yaml
id: ioc_sweep
namespace: company.team
description: Sweep new IOCs every 30 min, score via a Plexus agent, alert on hits.

inputs:
  - id: lookback_minutes
    type: INT
    defaults: 30

tasks:
  - id: pull_new_iocs
    type: io.kestra.plugin.jdbc.trino.Query
    url: jdbc:trino://trino.company.internal:8443/threat_intel
    sql: >
      SELECT indicator, type, score
      FROM iocs
      WHERE first_seen > now() - interval '{{ inputs.lookback_minutes }}' minute
    store: true

  - id: score_with_plexus
    type: io.kestra.plugin.core.http.Request
    uri: "https://plexus.company.internal/api/apps/{{ vars.agent_id }}/run"
    method: POST
    contentType: application/json
    headers:
      X-User: "{{ flow.namespace }}"          # OBO identity
    body: "{{ outputs.pull_new_iocs.uri }}"

  - id: alert_secops
    type: io.kestra.plugin.notifications.slack.SlackIncomingWebhook
    url: "{{ secret('SLACK_SECOPS_WEBHOOK') }}"
    payload: |
      { "text": "IOC sweep — {{ outputs.score_with_plexus.body }}" }

triggers:
  - id: every_30m
    type: io.kestra.plugin.core.trigger.Schedule
    cron: "*/30 * * * *"
```

**Deploy:** `kestra flow update company.team ioc_sweep.yml` (or paste in the UI), \
set the `agent_id` var and the `SLACK_SECOPS_WEBHOOK` secret. Trigger a test run with the \
**Kestra · Trigger Flow** node (approval-gated)."""


async def run_nl2kestra(config: dict, ctx, emit: Emit) -> dict[str, Any]:
    goal = ctx.resolve(config.get("goal") or config.get("prompt") or "", for_prompt=True)
    namespace = config.get("namespace") or "company.team"
    flow_id = config.get("flowId") or "new_flow"
    server = config.get("serverId", "kestra")
    provider, model_id = llm.provider_name(config, ctx), llm.model_name(config, ctx)
    steps: list[dict] = [{"type": "think", "text": f"Designing Kestra flow `{flow_id}` in `{namespace}`…"}]

    async def push(value="", **extra):
        await emit({"output": {"kind": "agent", "steps": list(steps), "value": value,
                               "provider": provider, "model": model_id, **extra}})

    await push()

    if ctx.settings.demo_mode or llm.use_demo(config, ctx):
        await push(_DEMO)
        return {"kind": "agent", "steps": steps, "value": _DEMO, "provider": "demo", "model": "(demo)"}

    # ground on existing flows (best-effort) for naming/consistency
    existing = ""
    try:
        out = await mcpmod.agent_call(server, "list_flows", {"namespace": namespace}, ctx)
        existing = out.get("value") or json.dumps(out.get("rows", []))
        if existing:
            steps.append({"type": "think", "text": f"Reviewed existing flows in '{namespace}'."})
            await push()
    except Exception:
        pass

    sys = ("You are a Kestra platform engineer. Generate ONE deployable Kestra flow as a fenced "
           "```yaml block: id, namespace, optional inputs, tasks (use real io.kestra.plugin.* types), "
           "and a Schedule trigger if the request implies a cadence. Reference secrets via "
           "{{ secret('NAME') }}. Keep it valid and minimal.")
    prompt = (f"Request: {goal}\n\nFlow id: {flow_id}\nNamespace: {namespace}\n"
              + (f"\nExisting flows for context:\n{existing}\n" if existing else "")
              + "\nGenerate the Kestra flow YAML.")
    try:
        v = await llm.complete(prompt, sys, config, ctx)
    except Exception as exc:
        v = f"⚠ model provider error: {exc}"
    steps.append({"type": "tool_result", "tool": "generate", "summary": f"Kestra flow `{flow_id}`"})
    await push(v)
    return {"kind": "agent", "steps": steps, "value": v or "⚠ no output", "provider": provider, "model": model_id}
