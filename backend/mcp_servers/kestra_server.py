"""Kestra as a real MCP server (stdio) — lets a Plexus agent trigger and inspect
orchestration flows. `trigger_flow` is a WRITE (register under write_tools ->
gated). In production, point these at the Kestra REST API
(POST /api/v1/executions/{namespace}/{flow}); here they simulate with a log file.

Run standalone:  python mcp_servers/kestra_server.py
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("kestra")
EXECLOG = os.environ.get("PLEXUS_KESTRA_EXECLOG", "/tmp/plexus_kestra_execs.jsonl")

_FLOWS = {
    "security": [
        {"flow": "daily_incident_brief", "description": "DBT refresh -> Plexus brief -> Slack", "schedule": "0 6 * * *"},
        {"flow": "remediation", "description": "Quarantine host + open ticket (approval-gated)", "schedule": None},
        {"flow": "ioc_sweep", "description": "Sweep new IOCs across the estate", "schedule": "*/30 * * * *"},
    ],
}


@mcp.tool()
def list_flows(namespace: str = "security") -> str:
    """List Kestra flows in a namespace. JSON rows."""
    return json.dumps(_FLOWS.get(namespace, []))


@mcp.tool()
def flow_status(execution_id: str) -> str:
    """Return the status of a Kestra execution. JSON."""
    return json.dumps({"executionId": execution_id, "state": "SUCCESS",
                       "duration_s": 42, "namespace": "security"})


@mcp.tool()
def trigger_flow(namespace: str = "security", flow: str = "", inputs: str = "") -> str:
    """Trigger a Kestra flow execution (WRITE). Logs and returns an execution id.
    Swap for POST /api/v1/executions/{namespace}/{flow} in production."""
    try:
        parsed = json.loads(inputs) if inputs else {}
    except json.JSONDecodeError:
        parsed = {"raw": inputs}
    eid = "exec_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    entry = {"executionId": eid, "namespace": namespace, "flow": flow,
             "inputs": parsed, "at": datetime.now(timezone.utc).isoformat(), "by": "plexus-mcp"}
    with open(EXECLOG, "a") as f:
        f.write(json.dumps(entry) + "\n")
    return json.dumps({"status": "STARTED", "executionId": eid, "namespace": namespace, "flow": flow})


if __name__ == "__main__":
    mcp.run()
