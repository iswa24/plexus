# Copilot instructions for Plexus

Plexus is a drag-and-drop **agent builder** over Trino (one or many clusters), Neo4j,
dbt + Kestra (via MCP), and a pluggable AI provider (Bedrock · Azure OpenAI · Anthropic
· local Claude CLI). FastAPI backend (Python 3.12) + a single-file vanilla-JS frontend
it serves.

> **Setting up real connections (Trino / dbt / Kestra) in a firm?** The complete
> step-by-step runbook is **[`docs/GO-LIVE.md`](../docs/GO-LIVE.md)** — read it before
> changing connection config. It covers every `PLEXUS_*` env var, the named-connection
> registry (`backend/plexus/connections.py`), OBO identity (`auth.py`), and the
> dbt/Kestra MCP servers (`backend/mcp_servers/`).

## Build / run / test (from `backend/`)

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # setup
./run.sh                                                             # run → http://localhost:8000
.venv/bin/pip install -r requirements-dev.txt && .venv/bin/python -m pytest   # test
```

**Always run `pytest` before finishing a change.** It runs in demo mode and needs
no credentials.

## Key facts

- **Demo mode is the default** (`PLEXUS_DEMO_MODE=true`): connectors return sample
  data, so everything works without AWS/Neo4j/Trino. Keep it that way — connector
  SDK imports are lazy and gated on `ctx.settings.demo_mode`.
- **Never commit secrets.** `.env` is gitignored; use `.env.example` as the template.
  Credentials are server-side only — never expose them to the browser.
- The frontend (`frontend/index.html`) is **dependency-free vanilla JS** — no build step.

## Where things live

- `backend/plexus/main.py` — REST (`/api/apps`, `/api/connections`, `/api/config`) + WebSocket (`/ws/run`) + static.
- `backend/plexus/executor.py` — DAG run loop, `@`-ref resolution, node-type dispatch.
- `backend/plexus/connectors/` — `bedrock`/`llm`, `nl2sql`+`sqlbackends`, `trino`, `neo4j`, `mcp`, `sources`, `demo_data`.
- `backend/plexus/connections.py` — named remote Trino clients (the multi-client registry).
- `backend/plexus/auth.py` — identity / Trino-OBO entry point.
- `backend/mcp_servers/` — real MCP stdio servers: `dbt`, `kestra`, `secintel`, `geoip`.
- `backend/plexus/models.py` — the App Definition schema.

## Connection setup (firm go-live), in one breath

- **AI provider** per node: `bedrock` | `azure` | `anthropic` | `claudecode`. Readiness at `GET /api/config`.
- **Trino**: default cluster via `PLEXUS_TRINO_*`; **multiple remote clients** via the
  Connections registry (UI ⋯ More → Connections, or `POST /api/connections`), referenced
  by a node's `connectionId`. OBO/JWT identity flows from `auth.py` → `connectors/trino.py`.
- **dbt / Kestra**: registered in `PLEXUS_MCP_SERVERS` (allow-list). The bundled servers
  run **live** when `DBT_PROJECT_DIR` / `KESTRA_BASE_URL` are set (in the server's `env`
  block), else simulate. Write tools (`dbt_run`, `trigger_flow`) are approval-gated and
  blocked in demo mode.

## Adding a node type = 3 edits
1. `frontend/index.html` → add to the `TYPES` registry (palette is auto-built).
2. `backend/plexus/connectors/<name>.py` → `async def run_<name>(config, ctx, emit)`.
3. `backend/plexus/executor.py` → add a dispatch branch in `run_node`.

For full detail read **`AGENTS.md`** and **`docs/DESIGN.md`** (architecture +
App Definition spec). The built-in skills (palette nodes + templates over Trino/dbt/Kestra)
are cataloged in **`docs/SKILLS.md`**.
