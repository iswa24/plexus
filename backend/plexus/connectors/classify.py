"""Classifier / Triage agent — classify input text into a category + severity +
owning team. A different agent *type*: decisioning/routing, not querying."""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from . import llm

Emit = Callable[[dict], Awaitable[None]]


async def run_classify(config: dict, ctx, emit: Emit) -> dict[str, Any]:
    text = ctx.resolve(config.get("input") or config.get("goal") or "", for_prompt=True)
    categories = config.get("categories") or "Phishing, Malware, Unauthorized Access, Data Loss, Policy Violation, Other"
    provider, model = llm.provider_name(config, ctx), llm.model_name(config, ctx)
    steps: list[dict] = []

    async def push(value=""):
        await emit({"output": {"kind": "agent", "steps": list(steps), "value": value,
                               "provider": provider, "model": model}})

    if llm.use_demo(config, ctx):
        steps.append({"type": "think", "text": "No model provider — demo classification."})
        v = f"(demo) Would classify into one of: {categories}. Configure a model provider for real triage."
        await push(v)
        return {"kind": "agent", "steps": steps, "value": v, "provider": "demo", "model": "(none)"}

    steps.append({"type": "think", "text": f"Classifying with {model}…"})
    await push()
    system = "You are a security triage classifier. Be decisive. Output compact markdown only."
    prompt = (f"Classify the INPUT into exactly one category from: {categories}.\n"
              "Also assign: severity (P1–P4), the owning team, a confidence (0–1), and a one-line rationale.\n\n"
              f"INPUT:\n{text}\n\n"
              "Return a markdown table with columns: Category | Severity | Team | Confidence | Rationale.")
    value = await llm.complete(prompt, system, config, ctx)
    await push(value)
    return {"kind": "agent", "steps": steps, "value": value, "provider": provider, "model": model}
