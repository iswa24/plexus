# Plexus MCP servers

Standalone **real** MCP servers (stdio) that Plexus reaches via the `source.mcp`,
`tool.mcp` nodes and the `model.agent` tool loop. They run as separate processes
over the Model Context Protocol — not canned demo data.

| Server | Tools | Notes |
|---|---|---|
| `secintel_server.py` | `search_iocs`, `enrich_user`, `block_ip` | `block_ip` is a real write (appends to a blocklist file) → register it as a `write_tools` so it stays approval-gated |
| `geoip_server.py` | `geolocate_ip`, `whois_domain` | geo / ASN / network enrichment |

## Register them (admin allow-list)

Servers come from an allow-list, never arbitrary user input. Set `PLEXUS_MCP_SERVERS`
in `backend/.env` (gitignored) as JSON, and turn demo mode off so the live transport
is used:

```bash
PLEXUS_DEMO_MODE=false
PLEXUS_MCP_SERVERS={"secintel":{"label":"SecIntel","transport":"stdio","command":"/abs/path/.venv/bin/python","args":["/abs/path/backend/mcp_servers/secintel_server.py"],"tools":["search_iocs","enrich_user","block_ip"],"write_tools":["block_ip"]},"geoip":{"label":"GeoIP / NetInfo","transport":"stdio","command":"/abs/path/.venv/bin/python","args":["/abs/path/backend/mcp_servers/geoip_server.py"],"tools":["geolocate_ip","whois_domain"]}}
```

`http`/`sse` transports are also supported (`{"transport":"sse","url":"https://..."}`).
A built-in `demo` server always exists so MCP cards run with no setup.

Requires the `mcp` SDK (in `requirements.txt`). Run the backend with `./run.sh`
(uses the venv + `.env`).
