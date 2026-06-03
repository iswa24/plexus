"""Detection-Engineer agent — turn a described behavior into a detection rule
(Sigma / Splunk SPL / SQL / KQL). A different agent *type*: code/rule generation."""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from . import llm

Emit = Callable[[dict], Awaitable[None]]

_DEMO_RULE = """title: Credential Dumping via LSASS Access
logsource:
  category: process_access
detection:
  selection:
    TargetImage|endswith: '\\\\lsass.exe'
    GrantedAccess: '0x1410'
  condition: selection
level: high"""


async def run_detection(config: dict, ctx, emit: Emit) -> dict[str, Any]:
    behavior = ctx.resolve(config.get("behavior") or config.get("goal") or "", for_prompt=True)
    target = config.get("target") or "Sigma"
    provider, model = llm.provider_name(config, ctx), llm.model_name(config, ctx)
    steps: list[dict] = []

    async def push(value=""):
        await emit({"output": {"kind": "agent", "steps": list(steps), "value": value,
                               "provider": provider, "model": model}})

    if not llm.available(config, ctx):
        steps.append({"type": "think", "text": "No model provider — demo rule."})
        steps.append({"type": "tool_call", "tool": f"{target} rule", "input": _DEMO_RULE})
        await push(_DEMO_RULE)
        return {"kind": "agent", "steps": steps, "value": _DEMO_RULE, "provider": "demo", "model": "(none)"}

    steps.append({"type": "think", "text": f"Writing a {target} detection rule with {model}…"})
    await push()
    system = f"You are a detection engineer. Output ONLY a valid {target} rule — no commentary, no fences."
    prompt = f"Write a {target} detection rule for this behavior:\n\n{behavior}"
    rule = (await llm.complete(prompt, system, config, ctx) or "").strip()
    steps.append({"type": "tool_call", "tool": f"{target} rule", "input": rule})
    await push(f"Generated a {target} detection rule.")
    return {"kind": "agent", "steps": steps, "value": rule, "provider": provider, "model": model}
