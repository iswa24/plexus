"""Generic Prompt agent — an LLM with a configurable system prompt + task.
Powers the prompt-only pack (MITRE mapper, IR playbook, phishing analyzer, …)."""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from . import llm

Emit = Callable[[dict], Awaitable[None]]


async def run_prompt(config: dict, ctx, emit: Emit) -> dict[str, Any]:
    task = ctx.resolve(config.get("goal") or config.get("input") or config.get("prompt") or "", for_prompt=True)
    system = config.get("system") or "You are a senior security analyst. Be concise and actionable."
    provider, model = llm.provider_name(config, ctx), llm.model_name(config, ctx)
    steps = [{"type": "think", "text": f"Reasoning with {model}…"}]

    async def push(value=""):
        await emit({"output": {"kind": "agent", "steps": list(steps), "value": value,
                               "provider": provider, "model": model}})

    await push()
    if llm.use_demo(config, ctx):
        v = "(demo) Configure a model provider (Bedrock / Claude Code / Anthropic)."
        await push(v)
        return {"kind": "agent", "steps": steps, "value": v, "provider": "demo", "model": "(none)"}
    try:
        v = await llm.complete(task, system, config, ctx)
    except Exception as exc:  # surface the failure instead of an empty answer
        v = f"⚠ model provider error: {exc}"
    if not v:
        v = "⚠ no response from the model provider."
    await push(v)
    return {"kind": "agent", "steps": steps, "value": v, "provider": provider, "model": model}
