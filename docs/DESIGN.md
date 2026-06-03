> NOTE: This is the original design document, written during the design phase before the product was named **Plexus** and before the backend existed. References to "Agent Builder", "agent-builder.html", and "tracker.html" predate the current code. It is kept for architectural rationale and the App Definition spec (§4). For the *current* implementation, see [../README.md](../README.md) and [AGENTS.md](../AGENTS.md).

# Drag-and-Drop Agent Builder — Design Doc & App Definition Spec

> An Amazon Q Apps–style visual builder where you drag input cards, source
> connectors (Trino, Graph DB), Bedrock model cards, and output cards onto a
> canvas, wire them together, write annotated prompts (`@references`), pick a
> model, and run — watching each node stream its result.

**Status:** design / pre-build  ·  **Owner:** Iswarya  ·  **Date:** 2026-06-03

---

## 1. Goal & demo target

Build an internal **visual agent builder** that lets a non-engineer compose a
small data+AI app by dragging cards onto a canvas:

- **Input** cards collect runtime parameters from the app user.
- **Source** cards query **Trino** (SQL warehouse) and the **Graph DB**.
- **Model** cards invoke **Bedrock** LLMs with prompts that reference upstream
  card outputs via `@`-annotations (the Amazon Q mechanic).
- **Output** cards render text / tables / charts.

**Demo north-star:** a security-analyst app — type a question → query Trino for
matching records → expand related entities in the graph → Bedrock summarizes
risk and recommends next steps → output card shows the answer + the evidence
table. Each node lights up and streams live during the run.

### Canvas model: **node graph** (recommended for demo)

| Option | Demo impact | Build cost | Verdict |
|---|---|---|---|
| Stacked cards (true Q clone) | Clean, simple, "guided" | Low | Good for productized end-users |
| **Node graph (React Flow)** | **High wow — reads as "agent builder", branching, live node animation** | Medium | **Use for the demo** |
| Cards now → graph later | Hedge | Medium | Fallback |

Decision: **node graph** for the demo. The App Definition format (below) is
canvas-agnostic, so a stacked-card renderer can be added later without changing
the backend or executor.

---

## 2. Build vs. buy — Langflow vs. Flowise vs. from-scratch

You asked for a comparison before committing. Here it is, scored for *your*
specifics: existing **Python** Trino client + Graph connection + Bedrock, a
**JPMC governance** bar, and a **fast impressive demo**.

| Dimension | **Langflow** | **Flowise** | **From scratch** |
|---|---|---|---|
| Backend language | **Python (FastAPI)** | Node.js / TypeScript | Your choice (Python) |
| Frontend | React + React Flow (built-in) | React (built-in) | React Flow (you build) |
| Custom node = | **Python class** | TypeScript class | Anything |
| Reuse your Trino/Graph Python clients | **Direct — drop them in** | No (need JS clients or a Python sidecar) | Direct |
| Bedrock support | Built-in component | Via LangChain.js | You write (boto3 Converse) |
| Self-host (air-gapped/VPC) | Yes (Docker) | Yes (Docker) | Yes |
| License (verify w/ legal) | MIT | Apache-2.0 core (some paid) | N/A |
| UX polish out of the box | High | High | None initially |
| Governance/audit control | Limited (their stack) | Limited | **Full** |
| Time to impressive demo | **Days** | Days–weeks (JS rework) | Weeks |
| Long-term control at a bank | Medium | Medium | **High** |

### Recommendation: **bootstrap the demo on Langflow, productize from scratch if it gets traction**

Rationale:
- Langflow's components are **Python classes**, so your existing Trino client,
  graph connection, and a boto3 Bedrock call become ~3 small custom nodes —
  **your code is reused directly, nothing thrown away.**
- You get a polished React Flow canvas, drag/drop, wiring, and run-streaming for
  free → **fastest path to a credible demo.**
- Flowise is excellent but TypeScript/LangChain.js-centric; your Python data
  connectors don't port cleanly, so you'd build a Python sidecar anyway —
  losing Flowise's main advantage.
- If the demo lands and this becomes a real platform, the **from-scratch** path
  (Section 3+) gives the governance, audit, and exact Q-style annotation UX a
  bank will require. The **App Definition spec below is written so a Langflow
  flow can be exported/translated into it**, protecting the migration.

> Net: *Langflow now for speed, the spec below as the durable contract.*

---

## 3. Architecture (from-scratch target — what Langflow approximates)

```
FRONTEND (React + TS + React Flow + shadcn/ui + Tailwind)
  Palette → Canvas (drag/drop + wiring) → Per-node config panel
  Serializes canvas → App Definition JSON
        │  REST (save/load/run) + WebSocket (streaming node updates)
        ▼
BACKEND (FastAPI, Python)
  ├─ App Registry        save/load/version App Definitions
  ├─ DAG Executor        topo-sort, resolve @refs, run nodes, stream
  ├─ Connector layer     Trino · Graph · Bedrock (+ transform/code)
  ├─ Auth / identity      propagate user identity to data sources
  └─ Audit log           every query + prompt + output, immutable
```

Key principle: **all credentials and connectors live server-side.** The browser
only ever holds the App Definition and rendered results.

---

## 4. App Definition spec (the core deliverable)

The App Definition is a single JSON document. It is what the canvas serializes,
what the registry stores/versions, and what the executor runs. It is **canvas-
agnostic** (renders as graph or stack) and **engine-agnostic** (custom executor
or translated to Langflow).

### 4.1 Top-level shape

```jsonc
{
  "schemaVersion": "1.0",
  "id": "app_7f3a",                 // stable id
  "name": "Incident Triage Assistant",
  "description": "Summarize incidents from the warehouse + graph context",
  "canvas": "graph",                // "graph" | "stack"
  "metadata": {
    "owner": "iswarya",
    "createdAt": "2026-06-03T00:00:00Z",
    "updatedAt": "2026-06-03T00:00:00Z",
    "tags": ["security", "demo"],
    "version": 3
  },
  "settings": {
    "defaultModelId": "anthropic.claude-3-5-sonnet-20241022-v2:0",
    "region": "us-east-1",
    "maxRuntimeMs": 120000,
    "streaming": true
  },
  "nodes": [ /* see 4.2 */ ],
  "edges": [ /* see 4.3 */ ]
}
```

### 4.2 Node object

```jsonc
{
  "id": "trino_1",                  // unique within app; used in @refs
  "type": "source.trino",          // see node catalog (4.5)
  "label": "Recent incidents",     // friendly name; shown in @autocomplete
  "position": { "x": 320, "y": 80 },// canvas coords (graph mode)
  "config": { /* type-specific, see catalog */ },
  "outputSchema": {                 // optional hint for downstream @autocomplete
    "kind": "rows",                 // "text" | "rows" | "json" | "binary"
    "fields": ["id", "severity", "ts"]
  }
}
```

### 4.3 Edge object (graph mode)

```jsonc
{
  "id": "e1",
  "source": "input_1",
  "target": "trino_1",
  "sourceHandle": "out",   // optional named ports (e.g. condition true/false)
  "targetHandle": "in"
}
```

In **stack mode**, edges are implicit (each card depends on all cards above it),
so `edges` may be empty and order is given by node array order / `position.y`.

### 4.4 Annotation / reference syntax (the "@" mechanic)

Inside any text field of a `config` (a SQL body, a prompt, an output template),
you reference upstream node outputs:

| Syntax | Resolves to |
|---|---|
| `@{trino_1}` | the primary output of node `trino_1` |
| `@{trino_1.rows}` | a named field of the output |
| `@{trino_1.rows[0].id}` | path indexing into structured output |
| `@{input_1}` | a runtime input value |
| `@Recent incidents` | **UI sugar** — autocomplete maps the label → `@{trino_1}` on save |

Resolution rules:
- The executor builds a `results` map (`nodeId → output`) and substitutes refs
  **before** running each node.
- A node may only reference nodes that are **upstream** in the DAG (validated at
  save time; cycles rejected).
- Substitution is **type-aware**: into a prompt, `rows` render as a compact
  markdown/CSV table; into a `transform.code` node, refs are passed as native
  objects, not stringified.
- **Injection safety:** when a ref lands inside a Trino/Graph query, the executor
  parameterizes (bind variables) wherever possible rather than string-concat.

### 4.5 Node catalog & per-type config

#### Inputs (collect runtime params)

```jsonc
// input.text
{ "type": "input.text",
  "config": { "label": "Question", "placeholder": "Ask…",
              "default": "", "required": true } }

// input.dropdown
{ "type": "input.dropdown",
  "config": { "label": "Severity", "options": ["P1","P2","P3"],
              "default": "P1", "multi": false } }

// input.file
{ "type": "input.file",
  "config": { "label": "Upload CSV", "accept": [".csv"], "maxSizeMb": 10 } }

// input.number / input.date — analogous
```

#### Sources (connectors)

```jsonc
// source.trino
{ "type": "source.trino",
  "config": {
    "catalog": "hive",
    "schema": "security",
    "sql": "SELECT id, severity, ts FROM incidents \
             WHERE severity = @{input_severity} \
             AND description ILIKE '%' || @{input_q} || '%' \
             ORDER BY ts DESC",
    "maxRows": 500,
    "timeoutMs": 30000,
    "readOnly": true            // enforced server-side
  } }

// source.graph
{ "type": "source.graph",
  "config": {
    "engine": "gremlin",        // "gremlin" | "cypher"
    "query": "g.V().has('incident','id', within(@{trino_1.rows[*].id})) \
               .both().dedup().limit(50).valueMap(true)",
    "bindings": {},             // explicit param bindings (preferred over inline)
    "maxRows": 200,
    "timeoutMs": 30000
  } }
```

#### Model (Bedrock)

```jsonc
// model.bedrock
{ "type": "model.bedrock",
  "config": {
    "modelId": "anthropic.claude-3-5-sonnet-20241022-v2:0",
    "system": "You are a senior security analyst.",
    "prompt": "User asked: @{input_q}\n\nIncidents:\n@{trino_1}\n\nRelated graph entities:\n@{graph_1}\n\nSummarize the risk and recommend next steps.",
    "temperature": 0.2,
    "maxTokens": 1500,
    "topP": 0.9,
    "stream": true,
    "tools": []                 // optional: expose connectors as tools (agentic mode)
  } }
```

#### Transform / logic

```jsonc
// transform.code  (sandboxed Python; refs passed as native objects)
{ "type": "transform.code",
  "config": { "language": "python",
    "code": "rows = inputs['trino_1']['rows']\nreturn [r for r in rows if r['severity']=='P1']" } }

// logic.condition  (graph mode; named output handles "true"/"false")
{ "type": "logic.condition",
  "config": { "expression": "len(@{trino_1.rows}) > 0" } }

// logic.loop  (iterate a model/transform over each row)
{ "type": "logic.loop",
  "config": { "over": "@{trino_1.rows}", "as": "row", "body": "node:model_1" } }
```

#### Outputs

```jsonc
// output.text  (markdown template with refs)
{ "type": "output.text",
  "config": { "template": "## Analysis\n@{model_1}" } }

// output.table
{ "type": "output.table",
  "config": { "source": "@{trino_1.rows}",
              "columns": ["id","severity","ts"] } }

// output.chart
{ "type": "output.chart",
  "config": { "chartType": "bar", "source": "@{trino_1.rows}",
              "x": "severity", "y": "count" } }

// output.download — emits a file (CSV/JSON) for the user
```

### 4.6 Runtime result envelope (executor → frontend over WS)

```jsonc
{
  "runId": "run_91c",
  "nodeId": "trino_1",
  "status": "running",          // queued | running | done | error | skipped
  "type": "rows",               // text | rows | json | binary
  "preview": "500 rows · 38ms", // short status for the node badge
  "output": { "rows": [ /* … */ ], "columns": ["id","severity","ts"] },
  "metrics": { "ms": 38, "rows": 500, "tokens": null },
  "error": null,
  "ts": "2026-06-03T00:00:01Z"
}
```

For `model.bedrock` with `stream:true`, the executor emits incremental
`status:"running"` frames carrying token deltas in `preview`/`output.text`.

---

## 5. Execution semantics

1. **Validate**: parse App Definition, build DAG from edges (stack mode →
   implicit edges by order), reject cycles, check every `@ref` is upstream.
2. **Collect inputs**: gather `input.*` node values from the run request.
3. **Topo-sort**: order nodes; independent branches run concurrently
   (`asyncio.gather`).
4. **Per node**: resolve `@refs` against the `results` map → run via the type's
   connector → store output → stream a result envelope.
5. **Cache**: key outputs by hash(config + resolved-upstream) so unchanged
   branches skip on re-run.
6. **Audit**: append every resolved query + prompt + output + identity to the
   immutable audit log.

```python
async def execute(app, inputs, ws, principal):
    dag = build_dag(app)                      # validates, raises on cycle
    results = {n.id: inputs[n.id] for n in app.input_nodes}
    for layer in dag.layers():                # each layer runs concurrently
        await asyncio.gather(*[
            run_node(n, results, ws, principal) for n in layer
        ])
    return results

async def run_node(node, results, ws, principal):
    cfg = resolve_refs(node.config, results)  # @{…} substitution, type-aware
    await ws.send(envelope(node, "running"))
    out = await REGISTRY[node.type](cfg, principal)   # connector call
    results[node.id] = out
    audit.log(principal, node, cfg, out)
    await ws.send(envelope(node, "done", out))
```

---

## 6. Connector specs

| Connector | Library | Notes / guardrails |
|---|---|---|
| **Trino** | `trino` (trino-python-client) | Force `readOnly`; auto-inject `LIMIT maxRows`; per-query timeout; catalog/schema allow-list; **propagate app-user identity** for row-level security. |
| **Graph** | `gremlinpython` (Neptune/JanusGraph/TinkerPop) **or** `neo4j` (Cypher) | Use parameter bindings, not string-concat; node/edge limit; timeout. |
| **Bedrock** | `boto3` `bedrock-runtime` **Converse API** | Uniform across Claude/Titan/Llama/Nova; native streaming; tool-use ready for agentic mode. Model picker just sets `modelId`. |

Agentic upgrade (later): give a `model.bedrock` node `tools` that wrap the Trino
and Graph connectors, so the model *decides* when to query — turning a fixed
dataflow into a true agent.

---

## 7. Security & governance (JPMC bar)

- **Server-side only** credentials; browser never touches secrets.
- **Identity propagation** to Trino/Graph → row-level security holds per app user.
- **Immutable audit log**: identity + resolved query + prompt + output, per run.
- **Injection guardrails** on `@ref` substitution into queries (bind params).
- **Read-only** data access by default; writes require explicit, reviewed nodes.
- **App Definitions are JSON** → version-controlled, peer-reviewed, RBAC on who
  can publish vs. run.
- Bedrock keeps inference inside your AWS VPC — no data leaves the boundary.

---

## 8. Roadmap

| Phase | Scope |
|---|---|
| **0 — Demo (Langflow)** | Custom Python nodes: Trino, Graph, Bedrock. Wire the security-triage flow. Impressive node-graph demo in days. |
| **1 — MVP (scratch)** | FastAPI + executor + WS streaming; React Flow canvas; input→Trino→Bedrock→output; `@refs`; this App Definition. |
| **2 — Builder polish** | Palette, config panels, model picker, graph node, output cards (table/chart). |
| **3 — Connectors+** | Graph node, transform/code node, file inputs. |
| **4 — Branching** | conditional/loop nodes, parallel branches. |
| **5 — Platform** | App registry, save/load/version, auth, audit, sharing, agentic tool-use mode. |

---

## 9. Demo script (the story to show)

1. Drag **Input(text)** "Question" + **Input(dropdown)** "Severity" onto canvas.
2. Drag **Trino** node; SQL filters incidents by `@{Severity}` + `@{Question}`.
3. Drag **Graph** node; expands related entities from the Trino result IDs.
4. Drag **Bedrock** node; prompt annotates `@{Question}`, `@{Trino}`, `@{Graph}`;
   pick Claude from the model dropdown.
5. Drag **Output(text)** + **Output(table)**.
6. Wire them, hit **Run** → watch each node light up and stream → final answer +
   evidence table appear.

The "wow" beats: live wiring, the `@`-autocomplete pulling upstream node names,
the model dropdown, and the streaming node animation over **real** data.
