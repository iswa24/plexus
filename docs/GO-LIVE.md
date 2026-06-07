# Plexus — Go-Live & Connection Setup

> **Audience:** an engineer (or an AI coding assistant such as GitHub Copilot /
> Claude) bringing Plexus into a firm and wiring it to **real** Trino, dbt, and
> Kestra. This is the single source of truth for non-demo configuration. Every
> setting is an environment variable with the `PLEXUS_` prefix, read from
> `backend/.env` (see `backend/.env.example`) by `backend/plexus/config.py`.

By default Plexus runs in **demo mode** (`PLEXUS_DEMO_MODE=true`): every connector
returns realistic sample data and needs no credentials. Going live = flip demo off
and configure the sections below. Connector SDKs are imported lazily, so you only
install what you actually use.

---

## 0. Mental model — what connects to what

```
 Browser (canvas UI)
   │  ws://…/ws/run  {app, inputs}
   ▼
 FastAPI backend  (backend/plexus/)
   ├── AI Agent nodes ─────▶ Bedrock | Azure OpenAI | Anthropic | claudecode CLI
   ├── NL→SQL / Trino nodes ▶ Trino cluster(s)   (per-connection, OBO/JWT identity)
   └── Data-Ops nodes ─────▶ dbt + Kestra  (via MCP stdio servers in backend/mcp_servers/)
```

There are **two ways** a node reaches Trino:
1. A **default cluster** from `.env` (`PLEXUS_TRINO_*`) — the fallback.
2. **Named connections** you add in the UI (**⋯ More → Connections & Settings**) or via
   the `/api/connections` REST API — the primary path for multiple remote clients.
   A node references one by its `connectionId`. Code: `backend/plexus/connections.py`,
   used by `connectors/trino.py` and `connectors/sqlbackends.py`.

dbt and Kestra are reached through **MCP servers** (real subprocesses, not HTTP from the
app): `backend/mcp_servers/dbt_server.py` and `kestra_server.py`. They auto-spawn when a
Data-Ops node runs and `PLEXUS_DEMO_MODE=false`.

---

## 1. Prerequisites

- Python **3.12**
- Network access to: your Trino cluster(s), your AI provider, your local dbt project,
  your Kestra (`http://localhost:8080` by default).
- `dbt` CLI on PATH (only if you use the dbt nodes against a real project).

---

## 2. Install

```bash
cd plexus/backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt      # FastAPI core
# install only the connector SDKs you'll use:
.venv/bin/pip install boto3                     # Bedrock
.venv/bin/pip install "openai>=1.0" azure-identity   # Azure OpenAI (+ Entra/managed identity)
.venv/bin/pip install anthropic                 # Anthropic direct API
.venv/bin/pip install trino                     # Trino
.venv/bin/pip install mcp                        # dbt / Kestra MCP servers
cp .env.example .env                            # then edit .env (next sections)
```

`mcp`, `trino`, and `boto3` are listed in `requirements.txt`; the line above is explicit
so an automated setup installs exactly what's needed.

---

## 3. Turn demo mode off

In `backend/.env`:

```ini
PLEXUS_DEMO_MODE=false
PLEXUS_DB_PATH=studio.db
PLEXUS_CORS_ORIGINS=https://plexus.yourfirm.internal   # lock this down in prod
```

> ⚠️ With demo off, **every** connector goes live. Configure at least one AI provider
> (§4) and your Trino connections (§5) before running real flows, or those nodes error.
> Note: a Trino host ending in `.local` (or an empty host) still resolves to a safe
> synthetic backend even with demo off — handy for staging.

---

## 4. AI provider (pick one or more)

AI Agent / NL→SQL / Classifier / Detection / Graph / RAG nodes each have a **provider**
field. The topbar provider picker sets all of them at once. Configure the providers you
allow:

### Bedrock (AWS)
```ini
PLEXUS_AWS_REGION=us-east-1
PLEXUS_BEDROCK_DEFAULT_MODEL=anthropic.claude-3-5-sonnet-20241022-v2:0
```
Auth uses the standard AWS chain (env vars, profile, or IAM role). No key in `.env`.

### Azure OpenAI
```ini
PLEXUS_AZURE_ENDPOINT=https://<resource>.openai.azure.com/
PLEXUS_AZURE_API_VERSION=2024-10-21
PLEXUS_AZURE_DEPLOYMENT=gpt-4o            # deployment name = model id
# Auth: either a key…
PLEXUS_AZURE_API_KEY=<key>
# …OR Entra ID / managed identity (enterprise; also enables OBO) — leave key blank:
PLEXUS_AZURE_USE_ENTRA=true
```

### Anthropic (direct API)
```ini
PLEXUS_ANTHROPIC_API_KEY=sk-ant-...
PLEXUS_ANTHROPIC_MODEL=claude-sonnet-4-5
```

### claudecode (local Claude CLI, no key)
Nothing to set — requires the `claude` CLI installed and logged in (Max subscription).

Verify readiness: `GET /api/config` returns `{providers:{bedrock:{ready:…}, azure:{…}, …}}`.

---

## 5. Trino

### 5a. Default cluster (fallback)
Used by any node that has **no** `connectionId`:
```ini
PLEXUS_TRINO_HOST=trino.yourfirm.internal
PLEXUS_TRINO_PORT=8443
PLEXUS_TRINO_SCHEME=https        # use http only for a local dev cluster
PLEXUS_TRINO_CATALOG=hive
PLEXUS_TRINO_USER=plexus
```

### 5b. Named connections (the main path — multiple remote clients)
Add each remote Trino client once; nodes then pick it from a dropdown.

**In the UI:** **⋯ More → Connections & Settings → + New connection** → fill
Label / Host / Port / Scheme / Catalog / Schema / Auth → **Create** → **Test ▷**
(expects `✓ Trino …`). Then on a **Trino Query** or **NL→SQL Agent** node set the
**Trino connection** field to it.

**Or via REST** (same registry, `backend/plexus/connections.py`):
```bash
curl -X POST http://localhost:8000/api/connections -H 'Content-Type: application/json' -d '{
  "label": "Threat Intel (Trino)",
  "kind": "trino",
  "host": "intel.trino.yourfirm.internal",
  "port": 8443,
  "scheme": "https",
  "catalog": "threat_intel",
  "schema": "iocs",
  "authType": "obo"          // none | basic | jwt | obo
}'
curl -X POST http://localhost:8000/api/connections/<id>/test   # probes the cluster
```
Secrets (`password`, `jwtSecret`, `token`) are stored but never returned by the API.
Connections persist in the SQLite registry (`PLEXUS_DB_PATH`).

### 5c. Identity / On-Behalf-Of (row-level security)
Plexus propagates the **caller's** identity to Trino so queries run as the app user.
- The caller's bearer token + `X-User` header become a `Principal`
  (`backend/plexus/auth.py`).
- For `authType: obo|jwt`, that token is sent to Trino as a JWT
  (`connectors/trino.py`, `connectors/sqlbackends.py::TrinoBackend`).
- **Production step:** in `auth.py::principal_from_headers`, validate the incoming
  OIDC/JWT against your IdP and exchange it for a downstream Trino token. The
  plumbing that carries the token through to Trino is already in place.

---

## 6. dbt + Kestra (Data-Ops nodes) — connect to your local instances

The Data-Ops palette nodes (`dbt · List/Lineage/Test/Run`, `Kestra · List/Status/Trigger`)
call the MCP servers in `backend/mcp_servers/`. Each server has **two modes**: it talks to
your **real** dbt/Kestra when its env vars are set, and falls back to simulated data
otherwise.

### Step 1 — register the servers in the allow-list
MCP servers are an **admin allow-list** (governance): only listed servers/tools are
reachable. Set `PLEXUS_MCP_SERVERS` in `backend/.env` as JSON (one line). Use **absolute
paths** to your venv python and the server scripts:

```ini
PLEXUS_MCP_SERVERS={"dbt":{"label":"dbt","transport":"stdio","command":"/abs/plexus/backend/.venv/bin/python","args":["/abs/plexus/backend/mcp_servers/dbt_server.py"],"tools":["list_models","model_lineage","dbt_test","dbt_run"],"write_tools":["dbt_run"],"env":{"DBT_PROJECT_DIR":"/abs/your/dbt_project","DBT_PROFILES_DIR":"/abs/your/.dbt","DBT_TARGET":"dev"}},"kestra":{"label":"Kestra","transport":"stdio","command":"/abs/plexus/backend/.venv/bin/python","args":["/abs/plexus/backend/mcp_servers/kestra_server.py"],"tools":["list_flows","flow_status","trigger_flow"],"write_tools":["trigger_flow"],"env":{"KESTRA_BASE_URL":"http://localhost:8080","KESTRA_NAMESPACE":"company.team"}}}
```

> The stdio transport passes the server's `env` block to the subprocess. If your MCP
> client build doesn't forward `env`, set `DBT_PROJECT_DIR` / `KESTRA_BASE_URL` in the
> same shell that launches the backend (`./run.sh`) instead — the servers read them
> from their own process environment either way.

### Step 2a — dbt (real local project)
The dbt server reads your project's `target/manifest.json` for `list_models`/`model_lineage`
and shells out to the `dbt` CLI for `dbt_test`/`dbt_run`.
```ini
DBT_PROJECT_DIR=/abs/your/dbt_project     # dir containing dbt_project.yml  (REQUIRED for live)
DBT_PROFILES_DIR=/abs/your/.dbt           # if profiles.yml isn't in ~/.dbt (optional)
DBT_TARGET=dev                            # dbt target/profile             (optional)
DBT_BIN=dbt                               # dbt executable                 (optional)
```
If `target/manifest.json` is missing, the server runs `dbt parse` once to create it.

### Step 2b — Kestra (real REST API)
The Kestra server calls your Kestra REST API.
```ini
KESTRA_BASE_URL=http://localhost:8080     # your Kestra            (REQUIRED for live)
KESTRA_NAMESPACE=company.team             # default namespace      (optional)
KESTRA_TENANT=                            # EE tenant id           (optional, EE only)
KESTRA_API_TOKEN=                         # bearer token           (optional)
KESTRA_USER= / KESTRA_PASSWORD=           # basic auth             (optional)
```
Endpoints used: `GET /api/v1/flows/{ns}`, `GET /api/v1/executions/{id}`,
`POST /api/v1/executions/{ns}/{flow}`.

### Step 3 — governance
- **Write tools are gated.** `dbt_run` and `trigger_flow` only execute when the node is
  **Approved** *and* `PLEXUS_DEMO_MODE=false`. Otherwise they return a `PROPOSED` result.
  An autonomous agent can *propose* a write but a human must approve it.
- **Demo gate is hard.** With `PLEXUS_DEMO_MODE=true`, Data-Ops nodes return simulated
  data even if a real server is registered — so a demo can never hit prod by accident.

The reverse direction (Kestra → Plexus via `POST /api/apps/{id}/run`) is documented in
[`infra/kestra/README.md`](../infra/kestra/README.md).

---

## 7. Verify the setup

```bash
cd backend && ./run.sh                                  # starts on :8000 with your .env
curl localhost:8000/api/config                          # provider readiness
curl localhost:8000/api/connections                     # your Trino connections (no secrets)
curl -X POST localhost:8000/api/connections/<id>/test   # probe a Trino cluster
```
Then in the UI (Builder): open a flow, click **▶ Run**, and watch each node. A live
NL→SQL agent shows: introspect schema → write SQL → run → refine → answer. A live
`dbt · List Models` returns your real models; `Kestra · List Flows` returns your real flows.

Smoke-test the Data-Ops MCP round-trip without the UI:
```bash
.venv/bin/python -m pytest tests/test_dataops_mcp.py -q
```

---

## 8. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `trino client not installed` | `.venv/bin/pip install trino` |
| `boto3 is not installed` | `.venv/bin/pip install boto3` (Bedrock) |
| `mcp SDK not installed` | `.venv/bin/pip install mcp` |
| Trino node returns demo rows with demo off | Host ends in `.local` / is empty → synthetic backend by design. Use a real host. |
| dbt/Kestra node returns *simulated* data | `DBT_PROJECT_DIR` / `KESTRA_BASE_URL` not visible to the server process. Put them in the server's `env` block in `PLEXUS_MCP_SERVERS`, or export them before `./run.sh`. |
| `dbt_run` / `trigger_flow` says **PROPOSED** | Working as designed — tick **Approve** on the node and set `PLEXUS_DEMO_MODE=false`. |
| Provider node errors | Check `GET /api/config` → `providers.<name>.ready`; install the SDK / set the key. |
| Trino 401/403 | OBO not wired — implement token validation + exchange in `auth.py`. |

---

## 9. Production hardening checklist

- [ ] `PLEXUS_DEMO_MODE=false`
- [ ] `PLEXUS_CORS_ORIGINS` locked to your domain (not `*`)
- [ ] Validate the bearer token + implement Trino OBO exchange in `auth.py`
- [ ] Trino over TLS (`scheme=https`) with auth; per-connection `authType=obo`
- [ ] Swap SQLite registry/audit (`PLEXUS_DB_PATH`) for Postgres / append-only store
- [ ] Secrets via a real secrets manager (don't keep keys in `.env` in prod)
- [ ] `PLEXUS_MCP_SERVERS` is an allow-list — only the servers/tools you intend; keep
      every side-effecting tool in `write_tools`

---

## 10. Where each thing is wired (code map)

| Concern | File |
|---|---|
| Env settings (all `PLEXUS_*`) | `backend/plexus/config.py` |
| Identity / OBO entry point | `backend/plexus/auth.py` |
| Named Trino connections (registry + `/api/connections`) | `backend/plexus/connections.py`, `main.py` |
| Trino query node | `backend/plexus/connectors/trino.py` |
| NL→SQL agent + Trino backend | `backend/plexus/connectors/nl2sql.py`, `sqlbackends.py` |
| AI provider routing | `backend/plexus/connectors/llm.py`, `bedrock.py` |
| MCP allow-list + live transport | `backend/plexus/connectors/mcp.py` |
| dbt / Kestra MCP servers | `backend/mcp_servers/dbt_server.py`, `kestra_server.py` |
| Node-type dispatch (incl. Data-Ops) | `backend/plexus/executor.py` |
| Local Trino+Postgres test stack | `infra/` (`docker compose up`) |
