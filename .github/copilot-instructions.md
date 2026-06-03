# Copilot instructions for Plexus

Plexus is a drag-and-drop **agent builder** over Trino, Neo4j, and AWS Bedrock.
FastAPI backend (Python 3.12) + a single-file vanilla-JS frontend it serves.

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

- `backend/plexus/main.py` — REST (`/api/apps`) + WebSocket (`/ws/run`) + static.
- `backend/plexus/executor.py` — DAG run loop, `@`-ref resolution.
- `backend/plexus/connectors/` — `agent`, `bedrock`, `neo4j`, `trino`, `demo_data`.
- `backend/plexus/models.py` — the App Definition schema.

## Adding a node type = 3 edits
1. `frontend/index.html` → add to the `TYPES` registry (palette is auto-built).
2. `backend/plexus/connectors/<name>.py` → `async def run_<name>(config, ctx, emit)`.
3. `backend/plexus/executor.py` → add a dispatch branch in `run_node`.

For full detail read **`AGENTS.md`** and **`docs/DESIGN.md`** (architecture +
App Definition spec).
