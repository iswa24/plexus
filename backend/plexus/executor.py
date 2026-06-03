"""DAG executor: dependency scheduler with concurrency, conditional branching,
sub-agent delegation, and map/loop — all author-controlled and audited.

The scheduler runs every node whose inputs are settled, as soon as they are
settled, in parallel. A `flow.branch` node marks its outgoing edges live/pruned;
nodes reachable only through pruned edges are skipped. `agent.call` runs another
registered agent as a step (agent-of-agents), and `flow.foreach` maps a chosen
agent over a list. @{ref} resolution and per-node streaming are unchanged.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from typing import Any, Awaitable, Callable, Optional

MAX_DEPTH = 5            # guard against an agent (in)directly calling itself forever
FOREACH_CONCURRENCY = 5  # bound on parallel item runs in flow.foreach
MAX_FOREACH = 25         # cap items mapped per run (logged when truncated)

from .auth import Principal
from .config import Settings
from .connectors.action import run_action
from .connectors.agent import run_agent
from .connectors.bedrock import run_bedrock
from .connectors.classify import run_classify
from .connectors.cypher import run_cypher
from .connectors.detection import run_detection
from .connectors.prompt_agent import run_prompt
from .connectors.rag import run_rag
from .connectors.neo4j import run_neo4j
from .connectors.nl2sql import run_nl2sql
from .connectors.sources import run_elastic, run_http, run_s3
from .connectors.trino import run_trino
from .models import AppDef, Node

Emit = Callable[[dict], Awaitable[None]]
REF_RE = re.compile(r"@\{([^}]+)\}")


def _slug(label: str, fallback: str) -> str:
    if not label:
        return fallback
    s = re.sub(r"[^\w]+", "_", label.lower()).strip("_")
    return s or fallback


def render_value(out: dict, for_prompt: bool) -> str:
    """Render a node output for substitution into a text field."""
    if out.get("kind") == "rows":
        rows = out.get("rows") or []
        cols = out.get("columns") or (list(rows[0].keys()) if rows else [])
        head = " | ".join(cols)
        limit = 8 if for_prompt else 5
        body = "\n".join(
            " | ".join(str(r.get(c, "")) for c in cols) for r in rows[:limit]
        )
        extra = f"\n…({len(rows)} rows total)" if len(rows) > limit else ""
        return f"{head}\n{body}{extra}"
    return str(out.get("value", ""))


class RunContext:
    def __init__(self, app: AppDef, inputs: dict, settings: Settings, principal: Principal):
        self.app = app
        self.inputs = inputs or {}
        self.settings = settings
        self.principal = principal
        self.depth = 0  # orchestration nesting depth (sub-agent calls)
        self.results: dict[str, dict] = {}
        self.nodes: dict[str, Node] = {n.id: n for n in app.nodes}
        # Map both the node id and the slugified label to the node id, so the
        # UI can write @{trino} (label) or @{trino_1} (id) interchangeably.
        self.ref_index: dict[str, str] = {}
        for n in app.nodes:
            self.ref_index[n.id] = n.id
            self.ref_index[_slug(n.label, n.id)] = n.id

    def resolve(self, text: str, for_prompt: bool) -> str:
        def repl(m: re.Match) -> str:
            key = m.group(1).strip()
            nid = self.ref_index.get(key)
            out = self.results.get(nid) if nid else None
            if not out:
                return f"[{key}]"
            return render_value(out, for_prompt)

        return REF_RE.sub(repl, text or "")

    def first_input_text(self) -> str:
        for n in self.app.nodes:
            if n.type.startswith("input"):
                r = self.results.get(n.id)
                if r and r.get("kind") == "text" and r.get("value"):
                    return r["value"]
        return ""

    def first_rows_count(self) -> int:
        for r in self.results.values():
            if r.get("kind") == "rows":
                return len(r.get("rows", []))
        return 0


def topo_sort(app: AppDef) -> list[str]:
    indeg = {n.id: 0 for n in app.nodes}
    adj: dict[str, list[str]] = {n.id: [] for n in app.nodes}
    for e in app.edges:
        if e.target in indeg and e.source in adj:
            indeg[e.target] += 1
            adj[e.source].append(e.target)
    queue = [nid for nid, d in indeg.items() if d == 0]
    order: list[str] = []
    while queue:
        nid = queue.pop(0)
        order.append(nid)
        for t in adj[nid]:
            indeg[t] -= 1
            if indeg[t] == 0:
                queue.append(t)
    # Any leftover (cycle) appended so they still execute deterministically.
    order += [n.id for n in app.nodes if n.id not in order]
    return order


async def run_node(node: Node, ctx: RunContext, emit: Emit) -> dict[str, Any]:
    t = node.type
    cfg = node.config

    if t.startswith("input"):
        val = ctx.inputs.get(node.id)
        if val is None or val == "":
            val = cfg.get("value") or cfg.get("default") or cfg.get("placeholder") or ""
        return {"kind": "text", "value": val}

    if t == "source.trino":
        return await run_trino(cfg, ctx, emit)
    if t == "source.neo4j":
        return await run_neo4j(cfg, ctx, emit)
    if t == "source.http":
        return await run_http(cfg, ctx, emit)
    if t == "source.s3":
        return await run_s3(cfg, ctx, emit)
    if t == "source.elastic":
        return await run_elastic(cfg, ctx, emit)
    if t == "model.bedrock":
        return await run_bedrock(cfg, ctx, emit)
    if t == "model.agent":
        return await run_agent(cfg, ctx, emit)
    if t == "model.nl2sql":
        return await run_nl2sql(cfg, ctx, emit)
    if t == "model.classify":
        return await run_classify(cfg, ctx, emit)
    if t == "model.detection":
        return await run_detection(cfg, ctx, emit)
    if t == "model.cypher":
        return await run_cypher(cfg, ctx, emit)
    if t == "model.rag":
        return await run_rag(cfg, ctx, emit)
    if t == "model.prompt":
        return await run_prompt(cfg, ctx, emit)
    if t == "action.webhook":
        return await run_action(cfg, ctx, emit)
    if t == "flow.branch":
        return await run_branch(cfg, ctx, emit)
    if t == "agent.call":
        return await run_call_agent(cfg, ctx, emit)
    if t == "flow.foreach":
        return await run_foreach(cfg, ctx, emit)
    if t == "output.text":
        return {"kind": "text", "value": ctx.resolve(cfg.get("template", ""), for_prompt=False)}
    if t == "output.document":
        title = cfg.get("title", "Document")
        tmpl = (cfg.get("template", "") or "").replace("@{title}", title)  # the doc's own title is available
        return {"kind": "document", "title": title, "value": ctx.resolve(tmpl, for_prompt=False)}
    if t == "output.table":
        m = REF_RE.search(cfg.get("source", "") or "")
        nid = ctx.ref_index.get(m.group(1).strip()) if m else None
        out = ctx.results.get(nid) if nid else None
        if out and out.get("kind") == "rows":
            return out
        return {"kind": "text", "value": "(point this at a rows output)"}
    if t == "output.json":
        m = REF_RE.search(cfg.get("source", "") or "")
        nid = ctx.ref_index.get(m.group(1).strip()) if m else None
        out = ctx.results.get(nid) if nid else None
        return {"kind": "json", "value": out}

    return {"kind": "text", "value": ""}


# ---------------------------------------------------------------- control flow
def _coerce(a: str, b: str):
    """Try numeric comparison; fall back to case-insensitive strings."""
    try:
        return float(a), float(b)
    except (TypeError, ValueError):
        return (a or "").strip().lower(), (b or "").strip().lower()


def _cmp(left: str, op: str, right: str) -> bool:
    lo, ro = _coerce(left, right)
    if op in ("==", "eq", "equals"):
        return lo == ro
    if op in ("!=", "ne"):
        return lo != ro
    if op in ("contains", "has"):
        return str(right).strip().lower() in str(left).strip().lower()
    if op in ("not_contains",):
        return str(right).strip().lower() not in str(left).strip().lower()
    try:
        if op in (">", "gt"):
            return lo > ro
        if op in ("<", "lt"):
            return lo < ro
        if op in (">=", "ge"):
            return lo >= ro
        if op in ("<=", "le"):
            return lo <= ro
    except TypeError:
        return False
    return False


async def run_branch(cfg: dict, ctx: "RunContext", emit: Emit) -> dict[str, Any]:
    """Evaluate a condition and choose which outgoing edges are live.
    Returns kind='branch' with an 'outcome' ('true'/'false') the scheduler reads."""
    left = ctx.resolve(cfg.get("left", ""), for_prompt=False)
    op = cfg.get("op", "==")
    right = cfg.get("right", "")
    outcome = "true" if _cmp(left, op, right) else "false"
    summary = f'{left!r} {op} {right!r} → {outcome}'
    out = {"kind": "branch", "outcome": outcome, "value": summary,
           "left": left, "op": op, "right": right,
           "steps": [{"type": "branch", "text": summary}]}
    await emit({"output": out})
    return out


def _terminal_output(sub_app: AppDef, results: dict) -> dict:
    """Pick the answer node of a sub-agent run (document > text > model/action)."""
    types = {n.id: n.type for n in sub_app.nodes}
    for pref in ("output.document", "output.text"):
        for nid, o in results.items():
            if types.get(nid) == pref and isinstance(o, dict) and (o.get("value") or o.get("rows")):
                return o
    for nid, o in results.items():
        if types.get(nid, "").startswith(("model", "action")) and isinstance(o, dict):
            return o
    for o in results.values():
        if isinstance(o, dict) and (o.get("value") or o.get("rows")):
            return o
    return {"kind": "text", "value": ""}


async def _run_subapp(ctx: "RunContext", app_id: str, input_value: str):
    """Run another registered agent as a step. Returns (terminal_output, name)."""
    if ctx.depth >= MAX_DEPTH:
        raise RuntimeError(f"orchestration depth limit ({MAX_DEPTH}) reached")
    if not app_id:
        raise RuntimeError("no agent selected for this node")
    from .registry import Registry  # local import avoids cycles

    stored = Registry(ctx.settings.db_path).get(app_id)
    if not stored:
        raise RuntimeError(f"agent '{app_id}' not found in registry")
    sub = AppDef(**stored)
    inp = next((n for n in sub.nodes if n.type.startswith("input")), None)
    inputs = {inp.id: input_value} if (inp and input_value is not None) else {}

    async def _silent(_frame: dict) -> None:  # sub-frames don't pollute the parent UI
        return None

    sub_results = await execute(sub, inputs, ctx.settings, ctx.principal,
                               _silent, audit=None, _depth=ctx.depth + 1)
    return _terminal_output(sub, sub_results), (sub.name or app_id)


def _input_for(cfg: dict, ctx: "RunContext") -> str:
    tmpl = cfg.get("input", "")
    if tmpl:
        return ctx.resolve(tmpl, for_prompt=True)
    return ctx.first_input_text() or ""


async def run_call_agent(cfg: dict, ctx: "RunContext", emit: Emit) -> dict[str, Any]:
    """Supervisor primitive: delegate a step to a registered agent (agent-of-agents)."""
    aid = cfg.get("agentId") or cfg.get("appId") or ""
    val = _input_for(cfg, ctx)
    out, name = await _run_subapp(ctx, aid, val)
    out = dict(out)
    out["delegated"] = name
    out["steps"] = [{"type": "delegate", "agent": name, "input": (val or "")[:160]}] + out.get("steps", [])
    await emit({"output": out})
    return out


def _ref_node_output(ctx: "RunContext", ref: str) -> Optional[dict]:
    m = REF_RE.search(ref or "")
    if not m:
        return None
    nid = ctx.ref_index.get(m.group(1).strip())
    return ctx.results.get(nid) if nid else None


def _item_text(it: Any) -> str:
    if isinstance(it, dict):
        return ", ".join(f"{k}={v}" for k, v in it.items())
    return str(it)


async def run_foreach(cfg: dict, ctx: "RunContext", emit: Emit) -> dict[str, Any]:
    """Map a chosen agent over a list/rows ref; run items concurrently; collect."""
    aid = cfg.get("agentId") or cfg.get("appId") or ""
    src = _ref_node_output(ctx, cfg.get("items", ""))
    items: list = []
    if src and src.get("kind") == "rows":
        field = cfg.get("itemField")
        rows = src.get("rows", [])
        items = [r.get(field) for r in rows] if field else rows
    elif src:
        items = [ln for ln in str(src.get("value", "")).splitlines() if ln.strip()]

    truncated = len(items) > MAX_FOREACH
    items = items[:MAX_FOREACH]
    sem = asyncio.Semaphore(FOREACH_CONCURRENCY)
    name_holder = {"name": aid}

    async def _one(it: Any) -> dict:
        async with sem:
            out, name = await _run_subapp(ctx, aid, _item_text(it))
            name_holder["name"] = name
            val = out.get("value") if out.get("value") is not None else render_value(out, False)
            return {"item": _item_text(it), "result": val}

    rows = await asyncio.gather(*[_one(it) for it in items]) if items else []
    step = {"type": "map", "agent": name_holder["name"], "count": len(rows)}
    if truncated:
        step["note"] = f"capped at {MAX_FOREACH} items"
    out = {"kind": "rows", "columns": ["item", "result"], "rows": list(rows),
           "steps": [step]}
    await emit({"output": out})
    return out


# ---------------------------------------------------------------- scheduler
async def execute(
    app: AppDef,
    inputs: dict,
    settings: Settings,
    principal: Principal,
    emit: Emit,
    audit=None,
    _depth: int = 0,
) -> dict[str, dict]:
    run_id = "run_" + uuid.uuid4().hex[:8]
    ctx = RunContext(app, inputs, settings, principal)
    ctx.depth = _depth
    await emit({"event": "run_start", "runId": run_id})

    in_edges: dict[str, list] = {n.id: [] for n in app.nodes}
    out_edges: dict[str, list] = {n.id: [] for n in app.nodes}
    for e in app.edges:
        if e.source in out_edges and e.target in in_edges:
            out_edges[e.source].append(e)
            in_edges[e.target].append(e)
    estate = {e.id: "pending" for e in app.edges}   # pending | live | pruned
    nstate = {n.id: "pending" for n in app.nodes}   # pending | running | done | skipped

    def settle_outgoing(nid: str, out: dict) -> None:
        is_branch = isinstance(out, dict) and out.get("kind") == "branch"
        outcome = out.get("outcome") if is_branch else None
        for e in out_edges[nid]:
            if is_branch and e.label:
                estate[e.id] = "live" if e.label == outcome else "pruned"
            else:
                estate[e.id] = "live"

    def prune_outgoing(nid: str) -> None:
        for e in out_edges[nid]:
            estate[e.id] = "pruned"

    async def exec_one(node: Node) -> dict:
        await emit({"event": "node", "runId": run_id, "nodeId": node.id, "status": "running"})
        t0 = time.perf_counter()

        async def node_emit(partial: dict, _nid=node.id) -> None:
            out = partial["output"] if "output" in partial else {
                "kind": "text", "value": partial.get("partial", "")}
            await emit({"event": "node", "runId": run_id, "nodeId": _nid,
                        "status": "running", "output": out})

        try:
            out = await run_node(node, ctx, node_emit)
            ms = int((time.perf_counter() - t0) * 1000)
            await emit({"event": "node", "runId": run_id, "nodeId": node.id,
                        "status": "done", "output": out, "ms": ms,
                        "tokens": out.get("tokens")})
            if audit:
                audit.log(principal=principal.username, app_id=app.id or "",
                          run_id=run_id, node_id=node.id, node_type=node.type,
                          detail={"config": node.config})
            return out
        except Exception as exc:
            await emit({"event": "node", "runId": run_id, "nodeId": node.id,
                        "status": "error", "error": str(exc)})
            return {"kind": "text", "value": "", "error": str(exc)}

    while True:
        # nodes whose every incoming edge has settled (or have none)
        ready = [n for n in app.nodes if nstate[n.id] == "pending"
                 and all(estate[e.id] != "pending" for e in in_edges[n.id])]
        if not ready:
            break
        run_batch, skipped = [], []
        for n in ready:
            ins = in_edges[n.id]
            live = (not ins) or any(estate[e.id] == "live" for e in ins)
            if live:
                nstate[n.id] = "running"
                run_batch.append(n)
            else:
                nstate[n.id] = "skipped"
                skipped.append(n)
        for n in skipped:  # all inputs pruned → this node never runs
            prune_outgoing(n.id)
            await emit({"event": "node", "runId": run_id, "nodeId": n.id, "status": "skipped"})
        if not run_batch:
            continue
        outs = await asyncio.gather(*[exec_one(n) for n in run_batch])  # parallel fan-out
        for n, out in zip(run_batch, outs):
            ctx.results[n.id] = out
            nstate[n.id] = "done"
            settle_outgoing(n.id, out)

    # leftover pending nodes => part of a cycle; settle deterministically
    leftover = [n for n in app.nodes if nstate[n.id] == "pending"]
    for n in leftover:
        out = await exec_one(n)
        ctx.results[n.id] = out
        nstate[n.id] = "done"

    await emit({"event": "run_complete", "runId": run_id})
    return ctx.results
