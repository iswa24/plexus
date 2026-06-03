"""DAG executor: topological order, @-ref resolution, per-node streaming."""
from __future__ import annotations

import re
import time
import uuid
from typing import Any, Awaitable, Callable, Optional

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


async def execute(
    app: AppDef,
    inputs: dict,
    settings: Settings,
    principal: Principal,
    emit: Emit,
    audit=None,
) -> dict[str, dict]:
    run_id = "run_" + uuid.uuid4().hex[:8]
    ctx = RunContext(app, inputs, settings, principal)
    order = topo_sort(app)
    await emit({"event": "run_start", "runId": run_id})

    for nid in order:
        node = ctx.nodes[nid]
        await emit({"event": "node", "runId": run_id, "nodeId": nid, "status": "running"})
        t0 = time.perf_counter()

        async def node_emit(partial: dict, _nid=nid, _run=run_id) -> None:
            # Connectors may stream a simple text delta ({"partial": "..."}) or a
            # rich output object ({"output": {...}}, used by the agent trace).
            out = partial["output"] if "output" in partial else {
                "kind": "text",
                "value": partial.get("partial", ""),
            }
            await emit(
                {
                    "event": "node",
                    "runId": _run,
                    "nodeId": _nid,
                    "status": "running",
                    "output": out,
                }
            )

        try:
            out = await run_node(node, ctx, node_emit)
            ctx.results[nid] = out
            ms = int((time.perf_counter() - t0) * 1000)
            await emit(
                {
                    "event": "node",
                    "runId": run_id,
                    "nodeId": nid,
                    "status": "done",
                    "output": out,
                    "ms": ms,
                    "tokens": out.get("tokens"),
                }
            )
            if audit:
                audit.log(
                    principal=principal.username,
                    app_id=app.id or "",
                    run_id=run_id,
                    node_id=nid,
                    node_type=node.type,
                    detail={"config": node.config},
                )
        except Exception as exc:
            await emit(
                {
                    "event": "node",
                    "runId": run_id,
                    "nodeId": nid,
                    "status": "error",
                    "error": str(exc),
                }
            )

    await emit({"event": "run_complete", "runId": run_id})
    return ctx.results
