# Plexus

> *Weave data, models & tools into agents.*

A drag-and-drop agent builder (Amazon Q Apps–style) over **Trino**, **Neo4j**,
and **Bedrock**. Drag input / source / model / output cards onto a canvas, wire
them, write `@`-annotated prompts, pick a model, and run — each node streams its
result live.

```
plexus/
├── backend/                 FastAPI service
│   ├── plexus/
│   │   ├── main.py          REST (app registry) + /ws/run (streaming) + static
│   │   ├── executor.py      DAG topo-sort, @-ref resolution, per-node streaming
│   │   ├── registry.py      App Definition persistence (SQLite)
│   │   ├── audit.py         per-node audit log
│   │   ├── auth.py          identity / OBO entry point
│   │   ├── config.py        env-driven settings (PLEXUS_*)
│   │   ├── models.py        App Definition Pydantic schemas
│   │   └── connectors/      agent · bedrock · neo4j · trino (+ demo_data)
│   ├── requirements.txt
│   └── run.sh
└── frontend/
    └── index.html           the canvas UI (served by the backend)
```

## Run it (demo mode — no credentials needed)

```bash
cd plexus/backend
./run.sh
# open http://localhost:8000
```

In demo mode the connectors return realistic sample data, so the whole app
works end-to-end with zero AWS / Neo4j / Trino setup.

## Go live (real connectors)

```bash
cp .env.example .env
# edit .env: set PLEXUS_DEMO_MODE=false and fill the Bedrock / Neo4j / Trino sections
pip install -r requirements.txt   # pulls boto3, neo4j, trino
./run.sh
```

Connector libs are imported lazily, so demo mode runs on the core deps alone.

## How it works

- **App Definition** (`models.py`) is the canvas serialized to JSON — the same
  document the UI builds, the registry stores, and the executor runs.
- **`@`-references**: in any SQL / Cypher / prompt field, `@{label}` (or
  `@{nodeId}`) injects an upstream node's output. The executor resolves them in
  topological order before running each node.
- **Streaming**: `/ws/run` accepts `{app, inputs}` and emits per-node frames
  (`run_start` → `node` running/done/error → `run_complete`); LLM output streams
  token-by-token.
- **Identity & audit**: the caller's identity (`auth.py`) is propagated to Trino
  (user + OBO token) for row-level security, and every node execution is written
  to the audit log.

## Configuration (environment variables)

All settings use the `PLEXUS_` prefix (loaded from the environment or a `.env`
file in `backend/`). See `backend/.env.example`.

| Variable | Default | Purpose |
|---|---|---|
| `PLEXUS_DEMO_MODE` | `true` | Connectors return sample data; no credentials needed. Set `false` to use real connectors. |
| `PLEXUS_DB_PATH` | `studio.db` | SQLite file for the app registry + audit log. |
| `PLEXUS_CORS_ORIGINS` | `*` | Comma-separated allowed origins. Lock down in prod. |
| `PLEXUS_AWS_REGION` | `us-east-1` | Bedrock region. |
| `PLEXUS_BEDROCK_DEFAULT_MODEL` | `anthropic.claude-3-5-sonnet-…` | Default model id. |
| `PLEXUS_NEO4J_URI` / `_USER` / `_PASSWORD` / `_DATABASE` | `bolt://localhost:7687`, `neo4j`, ``, `neo4j` | Neo4j connection. |
| `PLEXUS_TRINO_HOST` / `_PORT` / `_SCHEME` / `_CATALOG` | `localhost`, `8080`, `https`, `hive` | Trino connection. |

## Testing

```bash
cd backend
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

Tests run in demo mode (no credentials, no external services) and cover the
executor (topo-sort, `@`-ref resolution), an end-to-end demo run (flow + agent),
and the REST + WebSocket API.

## Developing / extending

**The contract.** An *App Definition* (`models.py`) is the canvas as JSON:

```jsonc
{
  "name": "…", "canvas": "graph",
  "settings": { "defaultModelId": "…", "region": "…", "streaming": true },
  "nodes": [{ "id": "n1", "type": "source.trino", "label": "…",
              "position": {"x":0,"y":0}, "config": { … } }],
  "edges": [{ "id": "e1", "source": "n1", "target": "n2" }]
}
```

**WebSocket frames** (`/ws/run`, server → client):
`run_start` → repeated `node` (`running`/`done`/`error`, with `output`/`ms`/`tokens`)
→ `run_complete`. Node `output` is `{kind:"text"|"rows"|"agent", …}`.

**Add a node type — 3 edits:**
1. `frontend/index.html` → add an entry to the `TYPES` registry (palette auto-builds).
2. `backend/plexus/connectors/<name>.py` → `async def run_<name>(config, ctx, emit)`;
   lazy-import any SDK and fall back to demo data when `ctx.settings.demo_mode`.
3. `backend/plexus/executor.py` → add a dispatch branch in `run_node`.

For the full architecture and the complete App Definition spec, see
[`docs/DESIGN.md`](docs/DESIGN.md). Agent-specific guidance is in
[`AGENTS.md`](AGENTS.md).

## Roadmap / open tasks

- [ ] **Trino OBO** — implement the IdP token exchange in `auth.py` (token is
      already extracted and passed to Trino as a JWT).
- [ ] **Query guardrails** — parameterize / validate agent- and `@`-ref-generated
      SQL & Cypher before running against real data.
- [ ] **Postgres** — swap the SQLite `registry.py` / `audit.py` for a production store.
- [ ] **AuthN/Z** — real bearer-token validation + RBAC on who can publish vs. run.
- [ ] **More connectors** — S3 / HTTP source cards, transform/code node.

## Security notes for production

- Replace the SQLite registry/audit with Postgres / an append-only store.
- Validate the bearer token in `auth.py` and implement the Trino OBO token
  exchange there.
- Parameterize Cypher/SQL (pass upstream values as bind params) instead of
  inlining resolved `@`-refs.
- Lock `PLEXUS_CORS_ORIGINS` to your domain.
