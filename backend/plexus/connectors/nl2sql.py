"""NL2SQL agent — turns a natural-language question into SQL over the security
SQLite databases, using the direct Anthropic API (tool-use loop).

The model is given the schema and a single read-only `run_sql` tool; it decides
what SQL to write, sees the rows, and iterates until it can answer. Output uses
the same {kind:"agent", steps, value} shape as the Bedrock agent, so the UI
renders the same live trace.

Demo fallback (no API key): runs a representative cross-database query against the
REAL SQLite data so you still see real rows, with a note to set the key.
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from . import sqldb

Emit = Callable[[dict], Awaitable[None]]

SYSTEM_TMPL = """You are a security data analyst with a read-only SQL tool over a SQLite database.

Schema (incidents tables are in main; asset/identity tables use the `assets.` prefix):
{schema}

Notes:
- incidents.asset_id -> assets.assets.id
- incidents.opened_by -> assets.identities.email
- assets.assets.owner_email -> assets.identities.email
Write SQLite-compatible SELECT queries only. Call run_sql to fetch data, then give a
concise, factual answer. Prefer a single well-joined query when possible."""

TOOLS = [{
    "name": "run_sql",
    "description": "Run one read-only SQLite SELECT against the security database and get rows back.",
    "input_schema": {
        "type": "object",
        "properties": {"sql": {"type": "string", "description": "A SQLite SELECT query"}},
        "required": ["sql"],
    },
}]


async def run_nl2sql(config: dict, ctx, emit: Emit) -> dict[str, Any]:
    question = ctx.resolve(config.get("goal") or config.get("prompt") or "", for_prompt=True)
    max_steps = int(config.get("maxSteps", 6))
    steps: list[dict] = []
    answer = {"value": ""}

    async def push():
        await emit({"output": {"kind": "agent", "steps": list(steps), "value": answer["value"]}})

    conn = sqldb.open_db(ctx.settings)
    try:
        schema = sqldb.schema_text(conn)
        key = ctx.settings.anthropic_api_key
        if not key:
            return await _demo(conn, steps, answer, push)
        return await _live(conn, schema, question, config, ctx, max_steps, steps, answer, push)
    finally:
        conn.close()


async def _demo(conn, steps, answer, push) -> dict[str, Any]:
    sql = ("SELECT i.id, i.severity, i.status, a.name AS asset, a.business_unit\n"
           "FROM incidents i JOIN assets.assets a ON i.asset_id = a.id\n"
           "WHERE i.severity='P1' ORDER BY i.ts DESC")
    steps.append({"type": "think", "text": "No Anthropic key set — running a representative demo query."})
    await push()
    steps.append({"type": "tool_call", "tool": "run_sql", "input": sql})
    await push()
    res = sqldb.run_select(conn, sql)
    steps.append({"type": "tool_result", "tool": "run_sql", "summary": f"{len(res['rows'])} rows returned"})
    answer["value"] = (
        f"(demo) Found {len(res['rows'])} open P1 incidents across the estate. "
        "Set PLEXUS_ANTHROPIC_API_KEY in backend/.env for real natural-language → SQL."
    )
    await push()
    return {"kind": "agent", "steps": steps, "value": answer["value"], "tokens": None}


async def _live(conn, schema, question, config, ctx, max_steps, steps, answer, push) -> dict[str, Any]:
    try:
        import anthropic  # noqa: WPS433 (lazy import by design)
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("anthropic SDK not installed. `pip install anthropic`.") from e

    client = anthropic.Anthropic(api_key=ctx.settings.anthropic_api_key)
    model = config.get("modelId") or ctx.settings.anthropic_model
    system = SYSTEM_TMPL.format(schema=schema)
    messages: list[dict] = [{"role": "user", "content": question}]
    tokens = 0

    for _ in range(max_steps):
        resp = await asyncio.to_thread(
            client.messages.create,
            model=model,
            max_tokens=int(config.get("maxTokens", 1500)),
            system=system,
            tools=TOOLS,
            messages=messages,
        )
        if getattr(resp, "usage", None):
            tokens += (resp.usage.output_tokens or 0)

        assistant_content = []
        tool_uses = []
        for block in resp.content:
            if block.type == "text" and block.text.strip():
                assistant_content.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                assistant_content.append(
                    {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
                )
                tool_uses.append(block)
        messages.append({"role": "assistant", "content": assistant_content})

        if resp.stop_reason == "tool_use":
            for block in resp.content:
                if block.type == "text" and block.text.strip():
                    steps.append({"type": "think", "text": block.text.strip()})
            await push()
            results = []
            for tu in tool_uses:
                sql = (tu.input or {}).get("sql", "")
                steps.append({"type": "tool_call", "tool": "run_sql", "input": sql})
                await push()
                try:
                    res = sqldb.run_select(conn, sql)
                    summary = f"{len(res['rows'])} rows"
                    content = str({"columns": res["columns"], "rows": res["rows"][:50]})
                except Exception as exc:
                    summary = f"error: {exc}"
                    content = f"ERROR: {exc}"
                steps.append({"type": "tool_result", "tool": "run_sql", "summary": summary})
                await push()
                results.append({"type": "tool_result", "tool_use_id": tu.id, "content": content})
            messages.append({"role": "user", "content": results})
            continue

        answer["value"] = "".join(b.text for b in resp.content if b.type == "text")
        await push()
        break

    return {"kind": "agent", "steps": steps, "value": answer["value"], "tokens": tokens or None}
