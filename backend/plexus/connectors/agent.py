"""Agentic node — a Bedrock Converse tool-use loop.

Unlike the fixed source->model wiring, here the connectors are registered as
*tools* and the model decides, each turn, whether to query the warehouse, the
graph, both, or to stop and answer. This is what turns a flow into an agent:
model-driven control flow + tool use + a loop + a self-determined stop.
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from .demo_data import demo_bedrock_text
from .neo4j import run_neo4j
from .trino import run_trino

Emit = Callable[[dict], Awaitable[None]]

TOOL_SPECS = {
    "trino": {
        "toolSpec": {
            "name": "query_warehouse",
            "description": "Run a read-only Trino SQL query against the security "
            "data warehouse to retrieve incidents and related records.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {"sql": {"type": "string", "description": "A Trino SQL query"}},
                    "required": ["sql"],
                }
            },
        }
    },
    "neo4j": {
        "toolSpec": {
            "name": "query_graph",
            "description": "Run a Cypher query against the Neo4j graph to expand "
            "entities related to incidents (users, assets, indicators, IPs).",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {"cypher": {"type": "string", "description": "A Cypher query"}},
                    "required": ["cypher"],
                }
            },
        }
    },
}

# tool name (as the model sees it) -> enabled key
_TOOL_BY_NAME = {"query_warehouse": "trino", "query_graph": "neo4j"}


def _enabled_tools(config: dict) -> list[str]:
    t = config.get("tools")
    if isinstance(t, list):
        return t
    if isinstance(t, str):
        return [x.strip() for x in t.split(",") if x.strip()]
    return ["trino", "neo4j"]


async def run_agent(config: dict, ctx, emit: Emit) -> dict[str, Any]:
    goal = ctx.resolve(config.get("goal") or config.get("prompt") or "", for_prompt=True)
    tools = _enabled_tools(config)
    max_steps = int(config.get("maxSteps", 5))
    steps: list[dict] = []
    answer = {"value": ""}

    async def push():
        await emit({"output": {"kind": "agent", "steps": list(steps), "value": answer["value"]}})

    if ctx.settings.demo_mode:
        return await _demo_agent(ctx, tools, steps, answer, push, emit)

    return await _live_agent(config, ctx, tools, max_steps, steps, answer, push, emit, goal)


# --------------------------------------------------------------- demo
async def _demo_agent(ctx, tools, steps, answer, push, emit) -> dict[str, Any]:
    async def think(text: str):
        steps.append({"type": "think", "text": text})
        await push()
        await asyncio.sleep(0.25)

    async def call(name: str, query: str, runner):
        steps.append({"type": "tool_call", "tool": name, "input": query})
        await push()
        await asyncio.sleep(0.3)
        res = await runner()
        n = len(res.get("rows", [])) if res.get("kind") == "rows" else 0
        steps.append({"type": "tool_result", "tool": name, "summary": f"{n} rows returned"})
        await push()
        await asyncio.sleep(0.2)
        return res

    incidents = None
    if "trino" in tools:
        await think("I need recent P1 incidents — I'll query the warehouse.")
        incidents = await call(
            "query_warehouse",
            "SELECT id, severity, owner, ts FROM incidents\nWHERE severity = 'P1'\nORDER BY ts DESC LIMIT 50",
            lambda: run_trino({"sql": "", "maxRows": 50}, ctx, emit),
        )
    if "neo4j" in tools:
        await think("Now I'll expand the related entities for those incidents in the graph.")
        await call(
            "query_graph",
            "MATCH (i:Incident)-[r]-(e)\nWHERE i.severity = 'P1'\nRETURN e.name, labels(e)[0], type(r) LIMIT 50",
            lambda: run_neo4j({"query": ""}, ctx, emit),
        )

    await think("I have enough evidence. Writing the risk summary.")
    text = demo_bedrock_text(ctx.first_input_text(), len(incidents["rows"]) if incidents else 5)
    for i in range(0, len(text), 5):
        answer["value"] = text[: i + 5]
        await push()
        await asyncio.sleep(0.012)
    answer["value"] = text
    await push()
    return {"kind": "agent", "steps": steps, "value": text, "tokens": max(1, len(text) // 4)}


# --------------------------------------------------------------- live (Bedrock)
async def _live_agent(config, ctx, tools, max_steps, steps, answer, push, emit, goal) -> dict[str, Any]:
    try:
        import boto3  # noqa: WPS433 (lazy import by design)
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(
            "boto3 not installed. `pip install boto3` or set PLEXUS_DEMO_MODE=true."
        ) from e

    client = boto3.client("bedrock-runtime", region_name=ctx.settings.aws_region)
    model_id = config.get("modelId") or ctx.settings.bedrock_default_model
    tool_config = {"tools": [TOOL_SPECS[t] for t in tools if t in TOOL_SPECS]}
    system = [{"text": config.get("system") or
               "You are a security analyst agent. Use the tools to gather evidence, "
               "then give a concise risk summary with recommended actions."}]
    messages: list[dict] = [{"role": "user", "content": [{"text": goal}]}]
    inference = {
        "temperature": float(config.get("temperature", 0.2)),
        "maxTokens": int(config.get("maxTokens", 1500)),
    }
    tokens = None

    for _ in range(max_steps):
        resp = await asyncio.to_thread(
            client.converse,
            modelId=model_id,
            messages=messages,
            system=system,
            toolConfig=tool_config,
            inferenceConfig=inference,
        )
        tokens = (resp.get("usage") or {}).get("outputTokens", tokens)
        msg = resp["output"]["message"]
        messages.append(msg)
        texts = [b["text"] for b in msg.get("content", []) if b.get("text", "").strip()]

        if resp.get("stopReason") == "tool_use":
            for tb in texts:
                steps.append({"type": "think", "text": tb.strip()})
            await push()
            results = []
            for block in msg.get("content", []):
                tu = block.get("toolUse")
                if not tu:
                    continue
                name, inp, tuid = tu["name"], tu.get("input", {}), tu["toolUseId"]
                if name == "query_warehouse":
                    steps.append({"type": "tool_call", "tool": name, "input": inp.get("sql", "")})
                    await push()
                    res = await run_trino(
                        {"sql": inp.get("sql", ""), "catalog": config.get("catalog"),
                         "schema": config.get("schema"), "maxRows": config.get("maxRows", 100)},
                        ctx, emit,
                    )
                elif name == "query_graph":
                    steps.append({"type": "tool_call", "tool": name, "input": inp.get("cypher", "")})
                    await push()
                    res = await run_neo4j({"query": inp.get("cypher", "")}, ctx, emit)
                else:
                    res = {"kind": "text", "value": "unknown tool"}
                n = len(res.get("rows", [])) if res.get("kind") == "rows" else 0
                steps.append({"type": "tool_result", "tool": name,
                              "summary": f"{n} rows returned" if res.get("kind") == "rows" else "ok"})
                await push()
                content = ({"json": {"columns": res.get("columns", []), "rows": res.get("rows", [])[:50]}}
                           if res.get("kind") == "rows" else {"text": res.get("value", "")})
                results.append({"toolResult": {"toolUseId": tuid, "content": [content]}})
            messages.append({"role": "user", "content": results})
            continue

        answer["value"] = "".join(texts)
        await push()
        break

    return {"kind": "agent", "steps": steps, "value": answer["value"], "tokens": tokens}
