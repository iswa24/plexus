# Plexus

> *Weave data, models & tools into agents.*

A drag-and-drop agent builder (Amazon Q Apps–style) over **Trino** (one or many
remote clusters), **Neo4j**, **dbt + Kestra** (via MCP), and a pluggable **AI
provider** (AWS Bedrock · Azure OpenAI · Anthropic · local Claude CLI). Drag
input / source / AI-agent / output cards onto a canvas, wire them, write
`@`-annotated prompts, pick a provider, and run — each node streams its result live.

> **Bringing this into a firm and wiring real Trino / dbt / Kestra?** Follow
> **[`docs/GO-LIVE.md`](docs/GO-LIVE.md)** — the complete connection-setup runbook.

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
│   │   ├── generator.py     generate an App Definition from a natural-language prompt
│   │   ├── connections.py   named remote Trino clients (registry + /api/connections)
│   │   └── connectors/      bedrock/llm · nl2sql · trino · neo4j · mcp · sources (+ demo_data)
│   ├── mcp_servers/         real MCP stdio servers: dbt · kestra · secintel · geoip
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
# edit .env: set PLEXUS_DEMO_MODE=false and fill the provider / Trino sections
pip install -r requirements.txt   # pulls boto3, trino, mcp, …
./run.sh
```

Connector libs are imported lazily, so demo mode runs on the core deps alone.

**The full setup — AI providers, multi-client Trino connections, OBO identity, and
connecting to your real local dbt + Kestra — is in [`docs/GO-LIVE.md`](docs/GO-LIVE.md).**
Two things worth knowing up front:

- **Multiple Trino clusters**: add each remote client in the UI
  (**⋯ More → Connections & Settings**) or via `POST /api/connections`; a node then
  references it by `connectionId`. The `PLEXUS_TRINO_*` env vars are just the default
  fallback cluster. (`backend/plexus/connections.py`)
- **dbt + Kestra**: reached through MCP servers in `backend/mcp_servers/`. They call
  your real local dbt project / Kestra REST API when `DBT_PROJECT_DIR` / `KESTRA_BASE_URL`
  are set (registered in `PLEXUS_MCP_SERVERS`), and simulate otherwise. See
  [`backend/mcp_servers/README.md`](backend/mcp_servers/README.md).

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
- **Generate from a prompt**: `POST /api/generate {prompt}` (`generator.py`) returns
  an App Definition built from a plain-English description — provider-generated when
  configured, a deterministic keyword heuristic in demo mode — loaded onto the canvas,
  fully editable.

## Real-time NL2SQL test (security databases)

A working natural-language → SQL agent over two related SQLite databases, powered
by the direct Anthropic API. SQLite stands in for Trino now (same federated-query
shape via `ATTACH`); swapping in real Trino later is a connector change.

```bash
cd backend
.venv/bin/pip install anthropic          # one-time
python scripts/seed_security.py          # creates data/incidents.db + data/assets.db
echo 'PLEXUS_ANTHROPIC_API_KEY=sk-ant-...' >> .env   # your key (gitignored)

# CLI: ask 3 questions; pass a model to compare
.venv/bin/python scripts/test_nl2sql.py
.venv/bin/python scripts/test_nl2sql.py claude-3-5-haiku-latest
```

Or in the UI: drag the **🧮 NL→SQL Agent** card, set the question, pick a model,
Run (or ▷ Preview). The card streams its trace: reasoning → the SQL it wrote →
rows → answer.

- **Databases** (`scripts/seed_security.py`): `incidents`/`alerts` (main) +
  `assets`/`identities` (attached as `assets.`). They relate, so cross-database
  questions work (e.g. *"which critical assets have open incidents, and do their
  owners have MFA?"*).
- **Without a key** the agent runs a representative query against the real data
  (demo fallback) so the UI still works.
- **Multiple models**: the card's Model dropdown / the CLI's model arg switch
  Claude tiers (Opus / Sonnet / Haiku) to compare quality and latency.
- **Model provider**: `claudecode` (local `claude` CLI on a Max subscription, no
  API key) or `anthropic` (direct API key). Set on the card or `PLEXUS_NL2SQL_PROVIDER`.
- **Trino backend**: set the card's *Data source* to `trino` (+ catalog/schema).
  The agent introspects `information_schema` and runs Trino SQL via the Trino
  client — same agent, real cluster. Spin up a local test stack from
  [`infra/`](infra/README.md) (`docker compose up`). The SQLite path needs no install.
- **Schema scoping** (for large estates — you can't put 1000s of tables in a prompt):
  set a **Tables allow-list** on the card, or leave it blank and the agent
  **auto-scopes** — above the *threshold* it lists table names, the model picks the
  relevant ones, and only those are introspected. The chosen tables show in the trace.

## Configuration (environment variables)

All settings use the `PLEXUS_` prefix (loaded from the environment or a `.env`
file in `backend/`). See `backend/.env.example`.

| Variable | Default | Purpose |
|---|---|---|
| `PLEXUS_DEMO_MODE` | `true` | Connectors return sample data; no credentials needed. Set `false` to use real connectors. |
| `PLEXUS_DB_PATH` | `studio.db` | SQLite file for the app registry + audit log + connections. |
| `PLEXUS_CORS_ORIGINS` | `*` | Comma-separated allowed origins. Lock down in prod. |
| `PLEXUS_NL2SQL_PROVIDER` | `claudecode` | Default AI provider for NL→SQL: `claudecode` / `anthropic` / `bedrock` / `azure`. |
| `PLEXUS_AWS_REGION` / `PLEXUS_BEDROCK_DEFAULT_MODEL` | `us-east-1`, `…sonnet…` | Bedrock region + default model. |
| `PLEXUS_ANTHROPIC_API_KEY` / `_MODEL` | – , `claude-sonnet-4-5` | Anthropic direct API. |
| `PLEXUS_AZURE_ENDPOINT` / `_API_KEY` / `_DEPLOYMENT` / `_USE_ENTRA` | – | Azure OpenAI (key or Entra/managed identity). |
| `PLEXUS_NEO4J_URI` / `_USER` / `_PASSWORD` / `_DATABASE` | `bolt://localhost:7687`, `neo4j`, ``, `neo4j` | Neo4j connection. |
| `PLEXUS_ELASTIC_URL` | `http://localhost:9200` | Elasticsearch (SIEM logs source). |
| `PLEXUS_TRINO_HOST` / `_PORT` / `_SCHEME` / `_CATALOG` / `_USER` | `localhost`, `8080`, `https`, `hive`, `studio` | **Default/fallback** Trino cluster. Multiple remote clients are added at runtime via Connections (see below). |
| `PLEXUS_MCP_SERVERS` | `{}` | Admin allow-list of MCP servers (dbt, Kestra, …) as JSON. See [`backend/mcp_servers/README.md`](backend/mcp_servers/README.md). |

**Runtime (not env):** remote Trino clients are managed as **Connections** — add them in
the UI (**⋯ More → Connections & Settings**) or `POST /api/connections`; stored in
`PLEXUS_DB_PATH`. Full reference: [`docs/GO-LIVE.md`](docs/GO-LIVE.md).

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
