"""NL2SQL agent — natural language → SQL over a pluggable backend.

The backend (sqlbackends.py) decides *where* the SQL runs: the local SQLite
security DBs (`source: sqlite`) or a real Trino cluster (`source: trino`). The
agent flow is identical either way:

  introspect schema -> model writes SQL -> run it read-only -> model answers

Model access comes from one of three providers:
  - claudecode : the local `claude` CLI (Max subscription, no API key)
  - anthropic  : the direct Anthropic API (tool-use loop)
  - demo       : no model — runs a representative query so the UI still works
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Awaitable, Callable

from . import claudecli
from .sqlbackends import get_backend

Emit = Callable[[dict], Awaitable[None]]

TOOLS = [{
    "name": "run_sql",
    "description": "Run one read-only SQL SELECT and get rows back.",
    "input_schema": {
        "type": "object",
        "properties": {"sql": {"type": "string", "description": "A SELECT query"}},
        "required": ["sql"],
    },
}]


async def _complete(prompt: str, system: str, config: dict, ctx) -> str | None:
    """Single provider-agnostic text completion (used for table selection)."""
    provider = (config.get("provider") or ctx.settings.nl2sql_provider or "claudecode").lower()
    if provider == "claudecode" and claudecli.available():
        return await claudecli.claude_run(prompt, system=system, model=claudecli.cli_model(config.get("modelId")))
    if ctx.settings.anthropic_api_key:
        import anthropic  # noqa: WPS433
        client = anthropic.Anthropic(api_key=ctx.settings.anthropic_api_key)
        resp = await asyncio.to_thread(
            client.messages.create,
            model=config.get("modelId") or ctx.settings.anthropic_model,
            max_tokens=300, system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in resp.content if b.type == "text")
    return None


async def _scope_tables(backend, question, config, ctx, steps, push) -> list[str] | None:
    """Schema scoping for large estates: allow-list, else full (if small), else
    two-phase discovery (list names -> model picks relevant -> introspect only those).
    Returns the chosen table list (None = use everything). Always traced, never silent.
    """
    allow = [t.strip() for t in (config.get("tables") or "").split(",") if t.strip()]
    if allow:
        steps.append({"type": "think", "text": f"Scoped to configured tables: {', '.join(allow)}"})
        await push()
        return allow

    try:
        names = backend.table_names()
    except Exception:
        return None  # backend can't list — fall back to full schema_text()

    threshold = int(config.get("maxSchemaTables", 25))
    if len(names) <= threshold:
        return None  # small estate — inject the whole schema

    steps.append({"type": "think",
                  "text": f"{len(names)} tables in scope — selecting the relevant ones for this question…"})
    await push()
    prompt = (f"Available tables:\n{', '.join(names)}\n\nQuestion: {question}\n\n"
              "Return ONLY a comma-separated list of the table names needed to answer (max 8).")
    out = await _complete(prompt, "You select the SQL tables relevant to a question. "
                                  "Output only a comma-separated list of table names.", config, ctx)
    picked = [t.strip() for t in re.split(r"[,\n]", out or "") if t.strip() in names][:8]
    if not picked:  # heuristic fallback — keyword match, then first N (logged, not silent)
        q = (question or "").lower()
        picked = [n for n in names if n.split(".")[-1].lower() in q][:8] or names[:threshold]
        steps.append({"type": "think", "text": f"Model selection unavailable; using {len(picked)} tables by heuristic."})
    else:
        steps.append({"type": "think", "text": f"Selected tables: {', '.join(picked)} (of {len(names)})"})
    await push()
    return picked


def _clean_sql(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"^```(?:sql)?", "", s).strip()
    s = re.sub(r"```$", "", s).strip()
    if ";" in s:
        s = s.split(";")[0]
    return s.strip()


async def run_nl2sql(config: dict, ctx, emit: Emit) -> dict[str, Any]:
    question = ctx.resolve(config.get("goal") or config.get("prompt") or "", for_prompt=True)
    max_steps = int(config.get("maxSteps", 6))
    steps: list[dict] = []
    answer = {"value": ""}

    async def push():
        await emit({"output": {"kind": "agent", "steps": list(steps), "value": answer["value"]}})

    backend = get_backend(config, ctx.settings, ctx.principal)
    try:
        scoped = await _scope_tables(backend, question, config, ctx, steps, push)
        schema = backend.schema_text(only=scoped)
        provider = (config.get("provider") or ctx.settings.nl2sql_provider or "claudecode").lower()
        if provider == "claudecode" and claudecli.available():
            return await _claudecode(backend, schema, question, config, steps, answer, push)
        if ctx.settings.anthropic_api_key:
            return await _live(backend, schema, question, config, ctx, max_steps, steps, answer, push)
        return await _demo(backend, steps, answer, push)
    finally:
        backend.close()


# ---------------------------------------------------------------- Claude Code (Max sub)
_SQL_SYS = ("You translate questions into a single {dialect} SELECT query. "
            "Output ONLY the SQL — no markdown fences, no commentary.")


async def _claudecode(backend, schema, question, config, steps, answer, push) -> dict[str, Any]:
    model = claudecli.cli_model(config.get("modelId"))
    sql_sys = _SQL_SYS.format(dialect=backend.dialect)
    steps.append({"type": "think", "text": f"Generating {backend.dialect} with Claude Code ({model})…"})
    await push()

    gen = (f"Schema:\n{schema}\n\n{backend.notes}\n\n"
           f"Question: {question}\n\nReturn only one {backend.dialect} SELECT query.")
    sql = _clean_sql(await claudecli.claude_run(gen, system=sql_sys, model=model))
    steps.append({"type": "tool_call", "tool": "run_sql", "input": sql})
    await push()

    try:
        res = backend.run_select(sql)
    except Exception as exc:
        steps.append({"type": "tool_result", "tool": "run_sql", "summary": f"error: {exc} — retrying"})
        await push()
        fix = (f"That SQL failed with: {exc}\n\nSchema:\n{schema}\n\n{backend.notes}\n\n"
               f"Question: {question}\n\nReturn a corrected single {backend.dialect} SELECT only.")
        sql = _clean_sql(await claudecli.claude_run(fix, system=sql_sys, model=model))
        steps.append({"type": "tool_call", "tool": "run_sql", "input": sql})
        await push()
        res = backend.run_select(sql)

    steps.append({"type": "tool_result", "tool": "run_sql", "summary": f"{len(res['rows'])} rows"})
    await push()

    ans = (f"Question: {question}\n\nSQL used:\n{sql}\n\n"
           f"Rows (JSON):\n{json.dumps(res['rows'][:50])}\n\n"
           "Give a concise, factual answer for a security analyst.")
    answer["value"] = await claudecli.claude_run(
        ans, system="You are a security data analyst. Be concise and factual.", model=model
    )
    await push()
    return {"kind": "agent", "steps": steps, "value": answer["value"], "tokens": None}


# ---------------------------------------------------------------- Anthropic API (tool-use loop)
async def _live(backend, schema, question, config, ctx, max_steps, steps, answer, push) -> dict[str, Any]:
    try:
        import anthropic  # noqa: WPS433 (lazy import by design)
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("anthropic SDK not installed. `pip install anthropic`.") from e

    client = anthropic.Anthropic(api_key=ctx.settings.anthropic_api_key)
    model = config.get("modelId") or ctx.settings.anthropic_model
    system = (f"You are a data analyst with a read-only SQL tool ({backend.dialect}).\n\n"
              f"Schema:\n{schema}\n\n{backend.notes}\n\n"
              "Write only SELECT queries. Call run_sql to fetch data, then answer concisely.")
    messages: list[dict] = [{"role": "user", "content": question}]
    tokens = 0

    for _ in range(max_steps):
        resp = await asyncio.to_thread(
            client.messages.create,
            model=model, max_tokens=int(config.get("maxTokens", 1500)),
            system=system, tools=TOOLS, messages=messages,
        )
        if getattr(resp, "usage", None):
            tokens += (resp.usage.output_tokens or 0)

        assistant, tool_uses = [], []
        for b in resp.content:
            if b.type == "text" and b.text.strip():
                assistant.append({"type": "text", "text": b.text})
            elif b.type == "tool_use":
                assistant.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
                tool_uses.append(b)
        messages.append({"role": "assistant", "content": assistant})

        if resp.stop_reason == "tool_use":
            for b in resp.content:
                if b.type == "text" and b.text.strip():
                    steps.append({"type": "think", "text": b.text.strip()})
            await push()
            results = []
            for tu in tool_uses:
                sql = (tu.input or {}).get("sql", "")
                steps.append({"type": "tool_call", "tool": "run_sql", "input": sql})
                await push()
                try:
                    res = backend.run_select(sql)
                    summary, content = f"{len(res['rows'])} rows", str({"columns": res["columns"], "rows": res["rows"][:50]})
                except Exception as exc:
                    summary, content = f"error: {exc}", f"ERROR: {exc}"
                steps.append({"type": "tool_result", "tool": "run_sql", "summary": summary})
                await push()
                results.append({"type": "tool_result", "tool_use_id": tu.id, "content": content})
            messages.append({"role": "user", "content": results})
            continue

        answer["value"] = "".join(b.text for b in resp.content if b.type == "text")
        await push()
        break

    return {"kind": "agent", "steps": steps, "value": answer["value"], "tokens": tokens or None}


# ---------------------------------------------------------------- demo (no model)
async def _demo(backend, steps, answer, push) -> dict[str, Any]:
    try:
        sql = ("SELECT i.id, i.severity, i.status, a.name AS asset, a.business_unit\n"
               "FROM incidents i JOIN assets.assets a ON i.asset_id = a.id\n"
               "WHERE i.severity='P1' ORDER BY i.ts DESC")
        steps.append({"type": "think", "text": "No model provider configured — running a representative demo query."})
        await push()
        steps.append({"type": "tool_call", "tool": "run_sql", "input": sql})
        await push()
        res = backend.run_select(sql)
        steps.append({"type": "tool_result", "tool": "run_sql", "summary": f"{len(res['rows'])} rows returned"})
        answer["value"] = (
            f"(demo) Found {len(res['rows'])} open P1 incidents. Configure Claude Code or an "
            "Anthropic key for real natural-language → SQL."
        )
    except Exception as exc:
        answer["value"] = f"(demo) Backend connected. Configure a model provider for NL→SQL. ({exc})"
    await push()
    return {"kind": "agent", "steps": steps, "value": answer["value"], "tokens": None}
