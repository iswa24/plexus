# AGENTS.md — guide for AI coding agents working on Plexus

Plexus is a drag-and-drop **agent builder** (Amazon Q Apps–style) over **Trino**,
**Neo4j**, and **AWS Bedrock**. Users wire input / source / model / output / agent
cards on a canvas, write `@`-annotated prompts, and run — each node streams its
result. This file tells you how to build, run, test, and safely extend it.

## TL;DR commands (run from `backend/`)

```bash
# setup
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
# run (demo mode — no credentials needed) → http://localhost:8000
./run.sh
# test
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

Python 3.12. Everything runs in **demo mode by default** (`PLEXUS_DEMO_MODE=true`):
connectors return realistic sample data, so the whole app works with zero AWS /
Neo4j / Trino setup. Flip `PLEXUS_DEMO_MODE=false` + set the env vars to go live.

## Repo layout

```
plexus/
├── backend/
│   ├── plexus/
│   │   ├── main.py          FastAPI: REST registry + /ws/run (WebSocket) + serves frontend
│   │   ├── executor.py      DAG topo-sort, @-ref resolution, per-node streaming
│   │   ├── models.py        App Definition Pydantic schemas (the core contract)
│   │   ├── registry.py      App persistence (SQLite)
│   │   ├── audit.py         per-node audit log
│   │   ├── auth.py          identity / Trino-OBO entry point
│   │   ├── config.py        env settings (PLEXUS_*)
│   │   ├── generator.py     build an App Definition from a NL prompt (POST /api/generate)
│   │   └── connectors/      agent · bedrock · neo4j · trino · nl2sql · sqldb · demo_data
│   ├── scripts/             seed_security.py (seed SQLite DBs) · test_nl2sql.py (live test)
│   ├── data/                seeded SQLite DBs (gitignored; regenerate via seed script)
│   ├── tests/               pytest suite (executor, demo e2e, API)
│   ├── requirements.txt · requirements-dev.txt · run.sh · pytest.ini · .env.example
├── frontend/index.html      the canvas UI (vanilla JS, served by the backend)
└── docs/DESIGN.md           architecture rationale + App Definition spec (§4)
```

## Architecture in one breath

1. The UI serializes the canvas to an **App Definition** (JSON: `nodes`, `edges`,
   `settings`). Same object is stored by the registry and run by the executor.
2. **Run**: the browser opens `ws://…/ws/run`, sends `{app, inputs}`. The executor
   topologically sorts nodes, resolves `@{ref}` placeholders against already-computed
   results, runs each node via its connector, and streams frames back.
3. **`@`-refs**: in any SQL / Cypher / prompt / template field, `@{label}` or
   `@{nodeId}` injects an upstream node's output (rows render as a compact table).
4. **Agent node** (`model.agent`): a Bedrock Converse **tool-use loop** — Trino and
   Neo4j are registered as tools and the *model* decides what to call and when to stop.

WebSocket frame contract (server → client):
- `{"event":"run_start","runId":…}`
- `{"event":"node","nodeId":…,"status":"running"|"done"|"error","output":{…},"ms":…,"tokens":…}`
  (model/agent nodes emit repeated `running` frames carrying streamed output)
- `{"event":"run_complete","runId":…}` / `{"event":"error","error":…}`

Node `output` shapes: `{"kind":"text","value":…}`, `{"kind":"rows","columns":[…],"rows":[…]}`,
or `{"kind":"agent","steps":[…],"value":…}`.

## How to add a new node type (3 touch points)

1. **Frontend** (`frontend/index.html`): add an entry to the `TYPES` registry
   (category, icon, color, `defaults`, inspector `fields`, `summary`). It appears in
   the palette automatically.
2. **Backend connector** (`backend/plexus/connectors/<name>.py`): write
   `async def run_<name>(config, ctx, emit) -> dict` returning a node output dict.
   **Lazy-import** any heavy SDK and fall back to demo data when
   `ctx.settings.demo_mode` is true.
3. **Executor dispatch** (`backend/plexus/executor.py`): add a branch in `run_node`
   mapping the node `type` string to your connector.

Use `ctx.resolve(text, for_prompt=...)` for any field that supports `@`-refs.

## Conventions & invariants (do not break)

- **Demo mode must always work with no credentials.** Connector SDK imports are
  lazy and gated behind `ctx.settings.demo_mode`.
- **Credentials live server-side only.** Never send secrets to the browser. Never
  commit a real `.env` (it is gitignored; `.env.example` is the template).
- **Identity flows through `auth.py` → `ctx.principal`** and into Trino (user + OBO
  token). Keep that path intact for row-level security.
- The frontend is intentionally **dependency-free vanilla JS** in a single file.
- Run tests before declaring done: `cd backend && .venv/bin/python -m pytest`.

## Open tasks / where to be careful

- **Trino OBO**: `auth.py` extracts the bearer token; the real IdP token exchange is
  a TODO. `trino.py` already passes it as a JWT.
- **Query guardrails**: agent/`@`-ref values are currently inlined into SQL/Cypher.
  Before pointing at real data, parameterize (bind params) and validate
  agent-generated queries.
- **Persistence**: SQLite (`registry.py`, `audit.py`) is single-node; move to
  Postgres / an append-only store for multi-user / production.
- **CORS**: lock `PLEXUS_CORS_ORIGINS` to your domain in production.

See `docs/DESIGN.md` for the full architecture and the App Definition spec.
