# Plexus MCP servers

Standalone **real** MCP servers (stdio) that Plexus reaches via the
`MCP Resource (read)` (`source.mcp`), `MCP Tool (action)` (`tool.mcp`), the branded
**Data-Ops** nodes (dbt / Kestra), and the **Tool-Calling Agent** (`model.agent`) loop.
They run as separate processes over the Model Context Protocol — not canned demo data.

| Server | Tools | Live mode (env) | Notes |
|---|---|---|---|
| `dbt_server.py` | `list_models`, `model_lineage`, `dbt_test`, `dbt_run` | `DBT_PROJECT_DIR` | Reads `target/manifest.json`; `dbt_test`/`dbt_run` shell out to the `dbt` CLI. `dbt_run` is a **write** → `write_tools`. |
| `kestra_server.py` | `list_flows`, `flow_status`, `trigger_flow` | `KESTRA_BASE_URL` | Calls the Kestra REST API. `trigger_flow` is a **write** → `write_tools`. |
| `secintel_server.py` | `search_iocs`, `enrich_user`, `block_ip` | — | `block_ip` is a real write (appends to a blocklist file) → `write_tools`. |
| `geoip_server.py` | `geolocate_ip`, `whois_domain` | — | geo / ASN / network enrichment |

**Each server has two modes:** it talks to the real backend when its env var(s) are set,
and returns simulated data otherwise — so demo mode and tests work with nothing installed.

## Register them (admin allow-list)

Servers come from an allow-list, never arbitrary user input. Set `PLEXUS_MCP_SERVERS`
in `backend/.env` (gitignored) as one-line JSON, and turn demo mode off so the live
transport is used. Use **absolute paths**. The optional per-server **`env`** block is
forwarded to the subprocess (merged over the parent environment).

```bash
PLEXUS_DEMO_MODE=false
PLEXUS_MCP_SERVERS={"dbt":{"label":"dbt","transport":"stdio","command":"/abs/plexus/backend/.venv/bin/python","args":["/abs/plexus/backend/mcp_servers/dbt_server.py"],"tools":["list_models","model_lineage","dbt_test","dbt_run"],"write_tools":["dbt_run"],"env":{"DBT_PROJECT_DIR":"/abs/your/dbt_project","DBT_TARGET":"dev"}},"kestra":{"label":"Kestra","transport":"stdio","command":"/abs/plexus/backend/.venv/bin/python","args":["/abs/plexus/backend/mcp_servers/kestra_server.py"],"tools":["list_flows","flow_status","trigger_flow"],"write_tools":["trigger_flow"],"env":{"KESTRA_BASE_URL":"http://localhost:8080","KESTRA_NAMESPACE":"company.team"}}}
```

### dbt env (live)
| Var | Required | Meaning |
|---|---|---|
| `DBT_PROJECT_DIR` | ✅ | dir containing `dbt_project.yml`. Missing `target/manifest.json` → server runs `dbt parse` once. |
| `DBT_PROFILES_DIR` | | path to `profiles.yml` if not `~/.dbt` |
| `DBT_TARGET` | | dbt target/profile (e.g. `dev`) |
| `DBT_BIN` | | dbt executable (default `dbt` on PATH) |

### Kestra env (live)
| Var | Required | Meaning |
|---|---|---|
| `KESTRA_BASE_URL` | ✅ | e.g. `http://localhost:8080` |
| `KESTRA_NAMESPACE` | | default namespace when a node omits one |
| `KESTRA_TENANT` | | EE tenant id (path becomes `/api/v1/{tenant}/…`) |
| `KESTRA_API_TOKEN` | | bearer token (EE/API) |
| `KESTRA_USER` / `KESTRA_PASSWORD` | | basic auth (OSS) |

## Governance

- **Write tools are gated.** `dbt_run`, `trigger_flow`, `block_ip` execute only when the
  node is **Approved** *and* `PLEXUS_DEMO_MODE=false`; otherwise they return `PROPOSED`.
- **Demo gate is hard.** `PLEXUS_DEMO_MODE=true` → simulated data even for registered
  real servers, so a demo can never hit prod.

`http`/`sse` transports are also supported (`{"transport":"sse","url":"https://..."}`).
A built-in `demo` server always exists so MCP cards run with no setup.

Requires the `mcp` SDK (in `requirements.txt`). Run the backend with `./run.sh`
(uses the venv + `.env`). Full walkthrough: [`docs/GO-LIVE.md`](../../docs/GO-LIVE.md) §6.
