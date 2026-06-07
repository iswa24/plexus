"""Kestra as an MCP server (stdio) — lets a Plexus agent inspect and trigger
orchestration flows.

TWO MODES (auto-selected):
  • LIVE  — set KESTRA_BASE_URL (e.g. http://localhost:8080). Calls the real Kestra
            REST API. This is what you use to connect Plexus to YOUR running Kestra.
  • DEMO  — KESTRA_BASE_URL unset → returns simulated flows/executions so the node
            works with no Kestra (used by tests and `PLEXUS_DEMO_MODE=true`).

Environment (LIVE mode):
  KESTRA_BASE_URL    base URL of your Kestra, e.g. http://localhost:8080   (required for live)
  KESTRA_TENANT      EE tenant id; inserted into the path /api/v1/{tenant}/…  (optional, EE only)
  KESTRA_API_TOKEN   bearer token (EE/API)                                   (optional)
  KESTRA_USER /      basic-auth credentials (OSS with basic auth enabled)    (optional)
  KESTRA_PASSWORD
  KESTRA_NAMESPACE   default namespace when a node doesn't pass one (default: "company.team")

REST endpoints used (Kestra v0.16+):
  list_flows    GET  {base}/api/v1[/{tenant}]/flows/{namespace}
  flow_status   GET  {base}/api/v1[/{tenant}]/executions/{executionId}
  trigger_flow  POST {base}/api/v1[/{tenant}]/executions/{namespace}/{flow}   (inputs as form data)

`trigger_flow` is a WRITE — register it under `write_tools` in PLEXUS_MCP_SERVERS so
Plexus keeps it approval-gated.

Run standalone:  python mcp_servers/kestra_server.py
"""
from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timezone

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("kestra")

BASE = (os.environ.get("KESTRA_BASE_URL") or "").rstrip("/")
TENANT = os.environ.get("KESTRA_TENANT") or ""
TOKEN = os.environ.get("KESTRA_API_TOKEN") or ""
USER = os.environ.get("KESTRA_USER") or ""
PASSWORD = os.environ.get("KESTRA_PASSWORD") or ""
DEFAULT_NS = os.environ.get("KESTRA_NAMESPACE") or "company.team"
EXECLOG = os.environ.get("PLEXUS_KESTRA_EXECLOG", "/tmp/plexus_kestra_execs.jsonl")
LIVE = bool(BASE)

# ---- simulated data (DEMO mode only) ----
_FLOWS = {
    "security": [
        {"flow": "daily_incident_brief", "description": "DBT refresh -> Plexus brief -> Slack", "schedule": "0 6 * * *"},
        {"flow": "remediation", "description": "Quarantine host + open ticket (approval-gated)", "schedule": None},
        {"flow": "ioc_sweep", "description": "Sweep new IOCs across the estate", "schedule": "*/30 * * * *"},
    ],
}


def _api(path: str) -> str:
    """Build a full API URL, inserting the EE tenant segment when configured."""
    seg = f"/{TENANT}" if TENANT else ""
    return f"{BASE}/api/v1{seg}{path}"


def _headers() -> dict:
    h = {"Accept": "application/json"}
    if TOKEN:
        h["Authorization"] = f"Bearer {TOKEN}"
    elif USER:
        h["Authorization"] = "Basic " + base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
    return h


def _http(method: str, path: str, *, data: dict | None = None) -> object:
    """Minimal JSON HTTP via urllib (no extra deps). `data` is sent as form fields."""
    import urllib.parse
    import urllib.request

    url = _api(path)
    body = urllib.parse.urlencode(data).encode() if data else None
    req = urllib.request.Request(url, data=body, method=method, headers=_headers())
    if body:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 (admin-configured base URL)
        text = r.read().decode()
    return json.loads(text) if text else {}


@mcp.tool()
def list_flows(namespace: str = "") -> str:
    """List Kestra flows in a namespace. JSON rows: [{flow, namespace, description}]."""
    ns = namespace or DEFAULT_NS
    if not LIVE:
        return json.dumps(_FLOWS.get(namespace or "security", []))
    flows = _http("GET", f"/flows/{ns}")
    rows = [{"flow": f.get("id"), "namespace": f.get("namespace", ns),
             "description": (f.get("description") or "")} for f in (flows or [])]
    return json.dumps(rows)


@mcp.tool()
def flow_status(execution_id: str) -> str:
    """Return the status of a Kestra execution. JSON {executionId, state, namespace, flow}."""
    if not LIVE:
        return json.dumps({"executionId": execution_id, "state": "SUCCESS",
                           "duration_s": 42, "namespace": "security"})
    ex = _http("GET", f"/executions/{execution_id}")
    return json.dumps({"executionId": ex.get("id", execution_id),
                       "state": (ex.get("state") or {}).get("current", "UNKNOWN"),
                       "namespace": ex.get("namespace"), "flow": ex.get("flowId")})


@mcp.tool()
def trigger_flow(namespace: str = "", flow: str = "", inputs: str = "") -> str:
    """Trigger a Kestra flow execution (WRITE). Returns the execution id + state.
    `inputs` is a JSON object of flow inputs (optional)."""
    ns = namespace or DEFAULT_NS
    try:
        parsed = json.loads(inputs) if inputs else {}
    except json.JSONDecodeError:
        parsed = {}
    if not LIVE:
        eid = "exec_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        with open(EXECLOG, "a") as f:
            f.write(json.dumps({"executionId": eid, "namespace": ns, "flow": flow,
                                "inputs": parsed, "by": "plexus-mcp"}) + "\n")
        return json.dumps({"status": "STARTED", "executionId": eid, "namespace": ns, "flow": flow})
    ex = _http("POST", f"/executions/{ns}/{flow}", data=parsed or None)
    return json.dumps({"status": "STARTED", "executionId": ex.get("id"),
                       "state": (ex.get("state") or {}).get("current", "CREATED"),
                       "namespace": ns, "flow": flow})


if __name__ == "__main__":
    mcp.run()
