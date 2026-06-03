"""Action / response node — approval-gated.

This is the leap from read-only analysis to *response* (open a ticket, block an
IP, disable an account). By default it ONLY PROPOSES the action; it executes via
webhook solely when `approved` is true AND demo mode is off. The approval gate is
the governance control: an agent never acts on production without a human yes.
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

Emit = Callable[[dict], Awaitable[None]]


async def run_action(config: dict, ctx, emit: Emit) -> dict[str, Any]:
    action = config.get("action") or "open_ticket"
    payload = ctx.resolve(config.get("payload") or "", for_prompt=False)
    url = config.get("url", "")
    approved = bool(config.get("approved"))
    steps: list[dict] = [{"type": "think", "text": f"Action: {action} (approval-gated)"}]

    async def push(value=""):
        await emit({"output": {"kind": "agent", "steps": list(steps), "value": value}})

    # propose-only unless explicitly approved AND running live
    if not approved or ctx.settings.demo_mode:
        steps.append({"type": "tool_call", "tool": action, "input": payload})
        steps.append({"type": "tool_result", "tool": action, "summary": "PROPOSED — awaiting human approval"})
        v = (f"⏸ PROPOSED action `{action}` (NOT executed — requires approval):\n\n{payload}\n\n"
             "Approve this step to execute.")
        await push(v)
        return {"kind": "agent", "steps": steps, "value": v}

    # execute for real
    import json  # noqa: F401
    import urllib.request
    steps.append({"type": "tool_call", "tool": action, "input": payload})
    await push()
    try:
        req = urllib.request.Request(url, data=(payload or "").encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        resp = await asyncio.to_thread(lambda: urllib.request.urlopen(req, timeout=15).read().decode())
        v = f"✅ EXECUTED `{action}` → {resp[:300]}"
        steps.append({"type": "tool_result", "tool": action, "summary": "executed"})
    except Exception as exc:
        v = f"⚠ action `{action}` failed: {exc}"
        steps.append({"type": "tool_result", "tool": action, "summary": f"error: {exc}"})
    await push(v)
    return {"kind": "agent", "steps": steps, "value": v}
