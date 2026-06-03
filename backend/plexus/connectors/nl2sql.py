"""NL2SQL agent — natural language → SQL over a pluggable backend (SQLite/Trino),
with a pluggable model provider and an optional model router.

Flow: introspect+scope schema -> [route model] -> model writes SQL -> run read-only
-> model answers. Emits {kind:"agent", steps, value, tables, provider, model, routed}
so the UI shows the trace, the tables traversed, AND which model ran (and why).

Providers: claudecode (Max sub) | bedrock (IAM, prod) | anthropic (API) | demo.
Model: a concrete id, or "auto" → a cheap router LLM picks the tier + explains.
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

# tier -> concrete model per provider
TIER_MODELS = {
    "claudecode": {"haiku": "haiku", "sonnet": "sonnet", "opus": "opus"},
    "anthropic": {"haiku": "claude-3-5-haiku-latest", "sonnet": "claude-sonnet-4-5", "opus": "claude-opus-4-1"},
    "bedrock": {
        "haiku": "anthropic.claude-3-5-haiku-20241022-v1:0",
        "sonnet": "anthropic.claude-3-5-sonnet-20241022-v2:0",
        "opus": "anthropic.claude-3-opus-20240229-v1:0",
    },
}

_SQL_SYS = ("You translate questions into a single {dialect} SELECT query. "
            "Output ONLY the SQL — no markdown fences, no commentary.")
_TBL_RE = re.compile(r"\b(?:from|join)\s+([A-Za-z_][\w.]*)", re.I)


def _tables_from_sql(sql: str) -> list[str]:
    return [m.group(1) for m in _TBL_RE.finditer(sql or "")]


def _dedup(xs):
    return list(dict.fromkeys(xs))


def _clean_sql(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"^```(?:sql)?", "", s).strip()
    s = re.sub(r"```$", "", s).strip()
    if ";" in s:
        s = s.split(";")[0]
    return s.strip()


def _default_model(provider: str, ctx) -> str:
    return {
        "claudecode": "claude-sonnet-4-5",
        "bedrock": ctx.settings.bedrock_default_model,
        "anthropic": ctx.settings.anthropic_model,
    }.get(provider, "claude-sonnet-4-5")


async def _bedrock_complete(prompt: str, system: str, model: str, settings) -> str:
    try:
        import boto3  # noqa: WPS433
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("boto3 not installed. `pip install boto3`.") from e
    client = boto3.client("bedrock-runtime", region_name=settings.aws_region)
    kwargs = {
        "modelId": model,
        "messages": [{"role": "user", "content": [{"text": prompt}]}],
        "inferenceConfig": {"temperature": 0.1, "maxTokens": 1500},
    }
    if system:
        kwargs["system"] = [{"text": system}]
    resp = await asyncio.to_thread(client.converse, **kwargs)
    return "".join(b.get("text", "") for b in resp["output"]["message"]["content"]).strip()


async def _complete(prompt: str, system: str, config: dict, ctx) -> str | None:
    """Single provider-agnostic completion (used by the router and table-scoping)."""
    provider = (config.get("provider") or ctx.settings.nl2sql_provider or "claudecode").lower()
    model = config.get("modelId")
    if provider == "claudecode" and claudecli.available():
        return await claudecli.claude_run(prompt, system=system, model=claudecli.cli_model(model))
    if provider == "bedrock":
        return await _bedrock_complete(prompt, system, model or ctx.settings.bedrock_default_model, ctx.settings)
    if ctx.settings.anthropic_api_key:
        import anthropic  # noqa: WPS433
        client = anthropic.Anthropic(api_key=ctx.settings.anthropic_api_key)
        resp = await asyncio.to_thread(
            client.messages.create, model=model or ctx.settings.anthropic_model,
            max_tokens=300, system=system, messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in resp.content if b.type == "text")
    return None


async def _route(question, provider, config, ctx, steps, push) -> str:
    """A cheap router LLM picks the model tier for this question and explains why.
    The decision is emitted as a 'router' step so it's visible in the trace."""
    router_model = TIER_MODELS.get(provider, TIER_MODELS["claudecode"])["haiku"]
    prompt = (f'Question: "{question}"\n\nPick the CHEAPEST capable model tier to answer it as SQL + analysis:\n'
              "- haiku: simple single-table lookups or counts\n"
              "- sonnet: multi-table joins, aggregations, or analysis\n"
              "- opus: only very complex or ambiguous reasoning\n"
              'Reply ONLY as JSON: {"tier":"haiku|sonnet|opus","reason":"<= 10 words"}')
    out = await _complete(prompt, "You are a model router. Output only compact JSON.",
                          {**config, "modelId": router_model}, ctx)
    tier, reason = "sonnet", "default (router unavailable)"
    try:
        d = json.loads(re.search(r"\{.*\}", out or "", re.S).group(0))
        if d.get("tier") in ("haiku", "sonnet", "opus"):
            tier = d["tier"]
        reason = str(d.get("reason") or reason)[:80]
    except Exception:
        pass
    model = TIER_MODELS.get(provider, TIER_MODELS["claudecode"]).get(tier, "sonnet")
    steps.append({"type": "router", "tier": tier, "reason": reason, "model": model})
    await push()
    return model


async def _scope_tables(backend, question, config, ctx, steps, push) -> list[str] | None:
    allow = [t.strip() for t in (config.get("tables") or "").split(",") if t.strip()]
    if allow:
        steps.append({"type": "think", "text": f"Scoped to configured tables: {', '.join(allow)}"})
        await push()
        return allow
    try:
        names = backend.table_names()
    except Exception:
        return None
    threshold = int(config.get("maxSchemaTables", 25))
    if len(names) <= threshold:
        return None
    steps.append({"type": "think",
                  "text": f"{len(names)} tables in scope — selecting the relevant ones for this question…"})
    await push()
    prompt = (f"Available tables:\n{', '.join(names)}\n\nQuestion: {question}\n\n"
              "Return ONLY a comma-separated list of the table names needed to answer (max 8).")
    out = await _complete(prompt, "You select the SQL tables relevant to a question. "
                                  "Output only a comma-separated list of table names.", config, ctx)
    picked = [t.strip() for t in re.split(r"[,\n]", out or "") if t.strip() in names][:8]
    if not picked:
        q = (question or "").lower()
        picked = [n for n in names if n.split(".")[-1].lower() in q][:8] or names[:threshold]
        steps.append({"type": "think", "text": f"Model selection unavailable; using {len(picked)} tables by heuristic."})
    else:
        steps.append({"type": "think", "text": f"Selected tables: {', '.join(picked)} (of {len(names)})"})
    await push()
    return picked


async def run_nl2sql(config: dict, ctx, emit: Emit) -> dict[str, Any]:
    question = ctx.resolve(config.get("goal") or config.get("prompt") or "", for_prompt=True)
    max_steps = int(config.get("maxSteps", 6))
    steps: list[dict] = []
    answer = {"value": ""}
    tables: list[str] = []
    provider = (config.get("provider") or ctx.settings.nl2sql_provider or "claudecode").lower()
    meta = {"provider": provider, "model": "", "routed": False}

    async def push():
        await emit({"output": {"kind": "agent", "steps": list(steps), "value": answer["value"],
                               "tables": _dedup(tables), "provider": provider,
                               "model": meta["model"], "routed": meta["routed"]}})

    backend = get_backend(config, ctx.settings, ctx.principal)
    try:
        scoped = await _scope_tables(backend, question, config, ctx, steps, push)
        schema = backend.schema_text(only=scoped)
        key = ctx.settings.anthropic_api_key

        use_claudecode = provider == "claudecode" and claudecli.available()
        use_bedrock = provider == "bedrock"
        use_anthropic = (not use_claudecode and not use_bedrock) and bool(key)

        if not (use_claudecode or use_bedrock or use_anthropic):
            return await _demo(backend, steps, answer, tables, push)

        # choose the model — fixed, or via the router
        requested = config.get("modelId")
        if requested == "auto" or config.get("route"):
            meta["model"] = await _route(question, provider, config, ctx, steps, push)
            meta["routed"] = True
        else:
            meta["model"] = requested or _default_model(provider, ctx)
        model_id = meta["model"]
        await push()

        if use_claudecode:
            comp = lambda p, sy: claudecli.claude_run(p, system=sy, model=claudecli.cli_model(model_id))  # noqa: E731
            return await _generate(backend, schema, question, steps, answer, tables, push, comp, "Claude Code", meta)
        if use_bedrock:
            comp = lambda p, sy: _bedrock_complete(p, sy, model_id, ctx.settings)  # noqa: E731
            return await _generate(backend, schema, question, steps, answer, tables, push, comp, "Bedrock", meta)
        return await _live(backend, schema, question, config, ctx, max_steps, steps, answer, tables, push, model_id, meta)
    finally:
        backend.close()


def _out(steps, answer, tables, meta, tokens=None):
    return {"kind": "agent", "steps": steps, "value": answer["value"], "tables": _dedup(tables),
            "provider": meta["provider"], "model": meta["model"], "routed": meta["routed"], "tokens": tokens}


# ---------------------------------------------------------------- generic gen->run->answer
async def _generate(backend, schema, question, steps, answer, tables, push, complete, label, meta) -> dict[str, Any]:
    sql_sys = _SQL_SYS.format(dialect=backend.dialect)
    steps.append({"type": "think", "text": f"Generating {backend.dialect} with {label} ({meta['model']})…"})
    await push()

    gen = (f"Schema:\n{schema}\n\n{backend.notes}\n\n"
           f"Question: {question}\n\nReturn only one {backend.dialect} SELECT query.")
    sql = _clean_sql(await complete(gen, sql_sys))
    steps.append({"type": "tool_call", "tool": "run_sql", "input": sql})
    tables[:] = _dedup(tables + _tables_from_sql(sql))
    await push()

    try:
        res = backend.run_select(sql)
    except Exception as exc:
        steps.append({"type": "tool_result", "tool": "run_sql", "summary": f"error: {exc} — retrying"})
        await push()
        fix = (f"That SQL failed with: {exc}\n\nSchema:\n{schema}\n\n{backend.notes}\n\n"
               f"Question: {question}\n\nReturn a corrected single {backend.dialect} SELECT only.")
        sql = _clean_sql(await complete(fix, sql_sys))
        steps.append({"type": "tool_call", "tool": "run_sql", "input": sql})
        tables[:] = _dedup(tables + _tables_from_sql(sql))
        await push()
        res = backend.run_select(sql)

    steps.append({"type": "tool_result", "tool": "run_sql", "summary": f"{len(res['rows'])} rows"})
    await push()

    ans = (f"Question: {question}\n\nSQL used:\n{sql}\n\n"
           f"Rows (JSON):\n{json.dumps(res['rows'][:50])}\n\n"
           "Give a concise, factual answer for a security analyst.")
    answer["value"] = await complete(ans, "You are a security data analyst. Be concise and factual.")
    await push()
    return _out(steps, answer, tables, meta)


# ---------------------------------------------------------------- Anthropic API (tool-use loop)
async def _live(backend, schema, question, config, ctx, max_steps, steps, answer, tables, push, model, meta) -> dict[str, Any]:
    try:
        import anthropic  # noqa: WPS433
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("anthropic SDK not installed.") from e

    client = anthropic.Anthropic(api_key=ctx.settings.anthropic_api_key)
    system = (f"You are a data analyst with a read-only SQL tool ({backend.dialect}).\n\n"
              f"Schema:\n{schema}\n\n{backend.notes}\n\n"
              "Write only SELECT queries. Call run_sql to fetch data, then answer concisely.")
    messages: list[dict] = [{"role": "user", "content": question}]
    tokens = 0
    for _ in range(max_steps):
        resp = await asyncio.to_thread(
            client.messages.create, model=model, max_tokens=int(config.get("maxTokens", 1500)),
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
                tables[:] = _dedup(tables + _tables_from_sql(sql))
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
    return _out(steps, answer, tables, meta, tokens or None)


# ---------------------------------------------------------------- demo (no model)
async def _demo(backend, steps, answer, tables, push) -> dict[str, Any]:
    sql = ("SELECT i.id, i.severity, i.status, a.name AS asset, a.business_unit\n"
           "FROM incidents i JOIN assets.assets a ON i.asset_id = a.id\n"
           "WHERE i.severity='P1' ORDER BY i.ts DESC")
    meta = {"provider": "demo", "model": "(none)", "routed": False}
    try:
        steps.append({"type": "think", "text": "No model provider configured — running a representative demo query."})
        await push()
        steps.append({"type": "tool_call", "tool": "run_sql", "input": sql})
        tables[:] = _dedup(tables + _tables_from_sql(sql))
        await push()
        res = backend.run_select(sql)
        steps.append({"type": "tool_result", "tool": "run_sql", "summary": f"{len(res['rows'])} rows returned"})
        answer["value"] = (f"(demo) Found {len(res['rows'])} open P1 incidents. Configure a model "
                           "provider (Bedrock / Claude Code / Anthropic) for real natural-language → SQL.")
    except Exception as exc:
        answer["value"] = f"(demo) Backend connected. Configure a model provider for NL→SQL. ({exc})"
    await push()
    return _out(steps, answer, tables, meta)
