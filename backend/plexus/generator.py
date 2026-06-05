"""Generate an App Definition from a natural-language description.

Two paths:
- demo mode (or Bedrock unavailable): a deterministic heuristic that assembles a
  sensible card graph from keywords in the prompt.
- live: Bedrock (Converse) is asked to emit an App Definition JSON, which is then
  validated and laid out. Falls back to the heuristic on any failure.
"""
from __future__ import annotations

import asyncio
import json
import re
from collections import deque

from .config import Settings
from .models import AppDef

VALID_TYPES = {
    "input.text", "input.dropdown",
    "source.trino", "source.neo4j", "source.http", "source.s3", "source.elastic",
    "model.bedrock", "model.agent", "model.nl2sql", "model.cypher",
    "model.classify", "model.detection", "model.rag", "model.prompt",
    "action.webhook", "source.mcp", "tool.mcp",
    "dbt.list", "dbt.lineage", "dbt.test", "dbt.run",
    "kestra.flows", "kestra.status", "kestra.trigger",
    "flow.branch", "agent.call", "flow.foreach",
    "output.text", "output.table", "output.document", "output.json",
}

SYSTEM = """You design "Plexus" apps. Output ONLY one JSON object, no prose:
{"name": str, "nodes": [...], "edges": [...]}

Node types and their config keys:
- input.text:     {"label","placeholder","value"}
- input.dropdown: {"label","options" (comma-separated string),"value"}
- source.trino:   {"catalog","schema","sql","maxRows"}     // SQL may embed @{ref}
- source.neo4j:   {"query"}                                 // Cypher may embed @{ref}
- model.bedrock:  {"system","prompt","temperature","maxTokens"} // prompt embeds @{ref}
- model.agent:    {"goal","tools" (subset of ["trino","neo4j"]),"system","maxSteps"} // autonomous tool-use loop
- output.text:    {"template"}                              // embeds @{ref}
- output.table:   {"source"}                                // a single @{ref} to a rows node

Each node: {"id": short unique string, "type": one of the above, "label": short string, "config": {...}}.
Each edge: {"id": string, "source": nodeId, "target": nodeId}. Edges define dependency order.
References: inside any text field, @{x} injects an upstream node's output, where x is the
target node's slugified label (lowercase, words joined by underscores), e.g. @{question}, @{trino}.

Rules:
- Always include at least one input.* and one output.*.
- Use model.agent when the task needs the model to DECIDE which data to fetch; otherwise use
  source nodes feeding a model.bedrock.
- Do NOT include positions. Output JSON only."""


async def generate_app(prompt: str, settings: Settings) -> dict:
    # Reuse-first at BUILD time: if existing registered agents cover the stages of
    # this goal, compose them into an orchestration instead of rebuilding from
    # scratch. Only fall back to single-agent generation when nothing fits.
    orch = await _orchestrate(prompt, settings)
    if orch is not None:
        return _normalize(orch, prompt)
    raw = None
    if not settings.demo_mode:
        raw = await _llm_generate(prompt, settings)
    if raw is None:
        raw = _heuristic(prompt)
    return _normalize(raw, prompt)


# ---------------------------------------------------------------- reuse-first orchestration
def _agent_capability(a: dict) -> str:
    if a.get("description"):
        return a["description"]
    types = [n.get("type", "") for n in a.get("nodes", [])]
    kinds = sorted({t.split(".")[-1] for t in types if t.startswith(("model", "action"))})
    return ("does: " + ", ".join(kinds)) if kinds else "agent"


_DECOMPOSE_SYS = (
    "You compose Plexus flows by REUSING existing agents. Given a user goal and a "
    "catalog of existing agents, break the goal into 1-5 ordered stages and pick, for "
    "each stage, the single existing agent that performs it (by id) — or \"\" if none "
    "fits. Mark a stage foreach:true when it runs once PER ROW/item of the previous "
    "stage's output (e.g. 'for each incident, ...'). Reuse agents wherever they fit.\n"
    'Output ONLY JSON: {"name":"<short flow name>","stages":[{"task":"<what it does>",'
    '"agentId":"<existing id or empty>","foreach":false}]}'
)


async def _orchestrate(prompt: str, settings: Settings) -> dict | None:
    """Decompose the goal and map each stage to an existing agent. Returns an
    orchestration AppDef if at least one stage reuses a registered agent; else None."""
    try:
        from .registry import Registry
        from .connectors import llm

        agents = Registry(settings.db_path).list()
        if not agents:
            return None
        by_id = {a["id"]: a for a in agents}
        catalog = "\n".join(
            f'- id="{a["id"]}" name="{a.get("name","")}" capability="{_agent_capability(a)}"'
            for a in agents)
        ask = (f'User goal: "{prompt}"\n\nExisting agents:\n{catalog}\n\n'
               "Decompose the goal into ordered stages, reusing these agents by id where they fit.")

        ctx = type("Ctx", (), {"settings": settings})()
        out = await llm.complete(ask, _DECOMPOSE_SYS, {}, ctx)
        if not out:
            return None
        plan = _extract_json(out) or {}
        stages = plan.get("stages") or []
        if not stages:
            return None
        # require at least one genuine reuse, else let the single-agent generator handle it
        if not any((s.get("agentId") in by_id) for s in stages):
            return None
        name = plan.get("name") or _title(prompt)
        return _build_orchestration(name, stages, prompt, by_id)
    except Exception:
        return None


def _build_orchestration(name: str, stages: list, prompt: str, by_id: dict) -> dict:
    nodes: list[dict] = []
    edges: list[dict] = []

    def add(t, label, cfg):
        nid = t.split(".")[-1] + "_" + str(len(nodes))
        c = dict(cfg)
        c.setdefault("label", label)
        nodes.append({"id": nid, "type": t, "label": label, "config": c})
        return nid

    def link(a, b, label=""):
        edges.append({"id": "e" + str(len(edges)), "source": a, "target": b, "label": label})

    prev = add("input.text", "Question", {"placeholder": "Ask…", "value": (prompt or "").strip()[:140]})
    for st in stages:
        aid = st.get("agentId") or ""
        agent = by_id.get(aid)
        task = (st.get("task") or "Process the input.")[:300]
        if st.get("foreach") and agent:
            nid = add("flow.foreach", f"For each → {agent.get('name','agent')}",
                      {"agentId": aid, "items": f"@{{{prev}}}", "itemField": ""})
        elif agent:
            nid = add("agent.call", agent.get("name", "Sub-agent"),
                      {"agentId": aid, "input": f"@{{{prev}}}"})
        else:  # gap: no existing agent fits → build a small prompt step inline
            nid = add("model.prompt", "Step",
                      {"provider": "claudecode", "modelId": "auto",
                       "system": "You are a senior security analyst. " + task,
                       "goal": f"@{{{prev}}}"})
        link(prev, nid)
        prev = nid

    out = add("output.document", "Report",
              {"title": name, "template": "# @{title}\n\n@{" + prev + "}"})
    link(prev, out)
    return {"name": name, "nodes": nodes, "edges": edges}


# ---------------------------------------------------------------- live (Bedrock)
async def _llm_generate(prompt: str, settings: Settings) -> dict | None:
    try:
        import boto3  # noqa: WPS433 (lazy import by design)
    except ImportError:
        return None
    try:
        client = boto3.client("bedrock-runtime", region_name=settings.aws_region)
        resp = await asyncio.to_thread(
            client.converse,
            modelId=settings.bedrock_default_model,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            system=[{"text": SYSTEM}],
            inferenceConfig={"temperature": 0.2, "maxTokens": 2000},
        )
        text = "".join(b.get("text", "") for b in resp["output"]["message"]["content"])
        return _extract_json(text)
    except Exception:
        return None


def _extract_json(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------- heuristic
def _heuristic(prompt: str) -> dict:
    p = (prompt or "").lower()
    nodes: list[dict] = []
    edges: list[dict] = []

    def add(t, label, config):
        nid = t.split(".")[-1] + "_" + str(len(nodes))
        cfg = dict(config)
        cfg.setdefault("label", label)  # persist label so the canvas shows it after round-trip
        nodes.append({"id": nid, "type": t, "label": label, "config": cfg})
        return nid

    def link(a, b):
        edges.append({"id": "e" + str(len(edges)), "source": a, "target": b})

    use_trino = any(k in p for k in
                    ["warehouse", "trino", "sql", "incident", "table", "record",
                     "data", "log", "query", "database", "report"])
    use_graph = any(k in p for k in
                    ["graph", "neo4j", "related", "entit", "connection", "cypher",
                     "relationship", "network", "lineage", "blast radius"])
    use_agent = any(k in p for k in
                    ["agent", "investigat", "autonomous", "decide", "figure out",
                     "explore", "hunt"])
    use_detect = any(k in p for k in
                     ["detection rule", "sigma", "yara", "write a rule", "spl", "kql",
                      "detection", "detect "])
    use_classify = any(k in p for k in
                       ["classify", "triage", "categorize", "what category", "label this",
                        "severity of"])
    if not (use_trino or use_graph or use_agent or use_detect or use_classify):
        use_trino = True  # sensible default source

    title = _title(prompt)
    q = add("input.text", "Question", {"placeholder": "Ask…", "value": (prompt or "").strip()[:140]})

    if use_agent:
        tools = ([t for t, on in (("trino", use_trino), ("neo4j", use_graph)) if on]) or ["trino", "neo4j"]
        m = add("model.agent", "Agent", {
            "goal": f"@{{{q}}}", "tools": tools,
            "system": "You are an analyst agent. Use the available tools to gather "
                      "evidence, then give a concise answer with recommended actions.",
            "maxSteps": 5, "temperature": 0.2, "maxTokens": 1500,
        })
        out = add("output.text", "Answer", {"template": f"@{{{m}}}"})
        link(q, m)
        link(m, out)
    elif use_detect:
        m = add("model.detection", "Detection", {"provider": "claudecode", "modelId": "auto",
                                                  "behavior": f"@{{{q}}}", "target": "Sigma"})
        out = add("output.text", "Answer", {"template": f"@{{{m}}}"})
        link(q, m)
        link(m, out)
    elif use_classify:
        m = add("model.classify", "Triage", {"provider": "claudecode", "modelId": "auto", "input": f"@{{{q}}}"})
        out = add("output.text", "Answer", {"template": f"@{{{m}}}"})
        link(q, m)
        link(m, out)
    elif use_trino and not use_graph:
        # "ask the data" → a real NL→SQL agent (writes its own SQL, runs it)
        m = add("model.nl2sql", "NL2SQL", {
            "provider": "claudecode", "source": "sqlite", "modelId": "auto", "goal": f"@{{{q}}}",
        })
        out = add("output.text", "Answer", {"template": f"@{{{m}}}"})
        link(q, m)
        link(m, out)
    elif use_graph and not use_trino:
        # relationship questions → a real NL→Cypher graph agent
        m = add("model.cypher", "Graph", {"provider": "claudecode", "modelId": "auto", "goal": f"@{{{q}}}"})
        out = add("output.text", "Answer", {"template": f"@{{{m}}}"})
        link(q, m)
        link(m, out)
    else:
        t = g = None
        if use_trino:
            t = add("source.trino", "Trino", {
                "catalog": "hive", "schema": "security", "maxRows": 500,
                "sql": "SELECT *\nFROM incidents\nORDER BY ts DESC\nLIMIT 50",
            })
        if use_graph:
            g = add("source.neo4j", "Neo4j", {
                "query": (f"MATCH (i:Incident)-[r]-(e)\nWHERE i.id IN @{{{t}}}\n"
                          "RETURN e.name AS entity, labels(e)[0] AS type, type(r) AS rel\nLIMIT 50")
                if t else "MATCH (n)-[r]-(m)\nRETURN n, r, m LIMIT 50",
            })
        prompt_text = f"User asked: @{{{q}}}"
        if t:
            prompt_text += f"\n\nWarehouse rows:\n@{{{t}}}"
        if g:
            prompt_text += f"\n\nGraph entities:\n@{{{g}}}"
        prompt_text += "\n\nAnswer the question using the information above."
        m = add("model.bedrock", "Bedrock", {
            "system": "You are a helpful analyst. Be concise and actionable.",
            "prompt": prompt_text, "temperature": 0.2, "maxTokens": 1500,
        })
        out = add("output.text", "Answer", {"template": f"@{{{m}}}"})
        if t and g:
            link(t, g)
        link(q, m)
        if t:
            link(t, m)
        if g:
            link(g, m)
        link(m, out)
        if t:
            tbl = add("output.table", "Records", {"source": f"@{{{t}}}"})
            link(t, tbl)

    return {"name": _title(prompt), "nodes": nodes, "edges": edges}


# ---------------------------------------------------------------- normalize + layout
def _normalize(raw: dict, prompt: str) -> dict:
    nodes = [n for n in raw.get("nodes", []) if n.get("type") in VALID_TYPES]
    seen: set[str] = set()
    for i, n in enumerate(nodes):
        nid = n.get("id") or (n["type"].split(".")[-1] + "_" + str(i))
        while nid in seen:
            nid = f"{nid}_{i}"
        n["id"] = nid
        seen.add(nid)
        n.setdefault("config", {})
        n["label"] = n.get("label") or n["type"]
    ids = {n["id"] for n in nodes}
    edges = []
    for i, e in enumerate(raw.get("edges", [])):
        if e.get("source") in ids and e.get("target") in ids:
            edges.append({"id": e.get("id") or f"e{i}", "source": e["source"],
                          "target": e["target"], "label": e.get("label", "")})

    pos = _layout(nodes, edges)
    for n in nodes:
        n["position"] = pos.get(n["id"], {"x": 60, "y": 60})

    app = AppDef(name=raw.get("name") or _title(prompt), nodes=nodes, edges=edges)
    return app.model_dump()


def _layout(nodes: list[dict], edges: list[dict]) -> dict:
    ids = [n["id"] for n in nodes]
    adj = {nid: [] for nid in ids}
    indeg = {nid: 0 for nid in ids}
    for e in edges:
        adj[e["source"]].append(e["target"])
        indeg[e["target"]] += 1
    layer = {nid: 0 for nid in ids}
    q = deque([nid for nid in ids if indeg[nid] == 0])
    remaining = dict(indeg)
    while q:
        nid = q.popleft()
        for t in adj[nid]:
            layer[t] = max(layer[t], layer[nid] + 1)
            remaining[t] -= 1
            if remaining[t] == 0:
                q.append(t)
    by_layer: dict[int, list[str]] = {}
    for nid in ids:
        by_layer.setdefault(layer[nid], []).append(nid)
    pos = {}
    for L, members in by_layer.items():
        for i, nid in enumerate(members):
            pos[nid] = {"x": 40 + L * 330, "y": 40 + i * 165}
    return pos


def _title(prompt: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", prompt or "")[:6]
    return " ".join(words).title() if words else "Generated App"
