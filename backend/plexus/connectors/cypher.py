"""Graph agent (NL→Cypher) — relationship / blast-radius questions over Neo4j.
A different agent *type*: graph traversal, not tabular SQL.

Generates Cypher from the question + a graph model, runs it via the Neo4j
connector (demo mode returns sample graph data), then answers from the result.
"""
from __future__ import annotations

import re
from typing import Any, Awaitable, Callable

from . import llm
from .neo4j import run_neo4j

Emit = Callable[[dict], Awaitable[None]]

DEFAULT_GRAPH_MODEL = (
    "(:Incident {id, severity, status})-[:AFFECTS]->(:Asset {name, business_unit, criticality})\n"
    "(:User {email, mfa_enabled})-[:OWNS]->(:Asset)\n"
    "(:Incident)-[:INDICATES]->(:Indicator {name, type})"
)


def _clean(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"^```(?:cypher)?", "", s).strip()
    s = re.sub(r"```$", "", s).strip()
    return s.strip()


async def _noop(_):
    return None


async def run_cypher(config: dict, ctx, emit: Emit) -> dict[str, Any]:
    question = ctx.resolve(config.get("goal") or config.get("prompt") or "", for_prompt=True)
    model_text = config.get("graphModel") or DEFAULT_GRAPH_MODEL
    provider, model = llm.provider_name(config, ctx), llm.model_name(config, ctx)
    steps: list[dict] = []

    async def push(value=""):
        await emit({"output": {"kind": "agent", "steps": list(steps), "value": value,
                               "provider": provider, "model": model}})

    if llm.use_demo(config, ctx):
        cy = "MATCH (i:Incident)-[r]-(e) RETURN e LIMIT 25"
        steps.append({"type": "think", "text": "No model provider — demo Cypher."})
        steps.append({"type": "tool_call", "tool": "run_cypher", "input": cy})
        await push()
        res = await run_neo4j({"query": cy}, ctx, _noop)
        steps.append({"type": "tool_result", "tool": "run_cypher", "summary": f"{len(res['rows'])} rows"})
        v = f"(demo) {len(res['rows'])} related entities. Configure a model provider for real NL→Cypher."
        await push(v)
        return {"kind": "agent", "steps": steps, "value": v, "provider": "demo", "model": "(none)"}

    steps.append({"type": "think", "text": f"Generating Cypher with {model}…"})
    await push()
    sys = "You translate questions into a single Cypher query. Output ONLY Cypher — no commentary, no fences."
    gen = f"Graph model:\n{model_text}\n\nQuestion: {question}\n\nReturn one Cypher query."
    cy = _clean(await llm.complete(gen, sys, config, ctx))
    steps.append({"type": "tool_call", "tool": "run_cypher", "input": cy})
    await push()
    res = await run_neo4j({"query": cy}, ctx, emit)
    steps.append({"type": "tool_result", "tool": "run_cypher", "summary": f"{len(res.get('rows', []))} rows"})
    await push()

    import json
    ans = (f"Question: {question}\n\nCypher:\n{cy}\n\nResult rows:\n{json.dumps(res.get('rows', [])[:50])}\n\n"
           "Give a concise, factual answer for a security analyst.")
    value = await llm.complete(ans, "You are a security graph analyst. Be concise and factual.", config, ctx)
    await push(value)
    return {"kind": "agent", "steps": steps, "value": value, "provider": provider, "model": model}
