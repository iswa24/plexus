"""DBT as a real MCP server (stdio) — lets a Plexus agent inspect and trigger the
data layer (DBT models on Iceberg). Reads/triggers are real side effects (a run
log file); in a real deployment, swap the simulated bodies for `dbt` CLI / dbt
Cloud API calls. `dbt_run` is a WRITE (register under write_tools -> gated).

Run standalone:  python mcp_servers/dbt_server.py
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("dbt")
RUNLOG = os.environ.get("PLEXUS_DBT_RUNLOG", "/tmp/plexus_dbt_runs.jsonl")

# A small slice of a security-warehouse dbt project (models materialized on Iceberg).
_MODELS = {
    "stg_incidents":   {"materialized": "view",  "rows": None, "tests": 4, "fresh_min": 12},
    "stg_assets":      {"materialized": "view",  "rows": None, "tests": 3, "fresh_min": 12},
    "dim_business_unit": {"materialized": "table", "rows": 8,   "tests": 2, "fresh_min": 35},
    "fct_open_p1":     {"materialized": "incremental", "rows": 7, "tests": 5, "fresh_min": 9},
}
_LINEAGE = {
    "fct_open_p1": {"upstream": ["stg_incidents", "stg_assets", "dim_business_unit"], "downstream": ["mart_exec_brief"]},
    "mart_exec_brief": {"upstream": ["fct_open_p1"], "downstream": []},
}


@mcp.tool()
def list_models() -> str:
    """List dbt models with materialization, row counts and freshness (minutes). JSON rows."""
    rows = [{"model": m, **v} for m, v in _MODELS.items()]
    return json.dumps(rows)


@mcp.tool()
def model_lineage(model: str) -> str:
    """Return upstream/downstream dependencies for a model (Iceberg lineage). JSON."""
    return json.dumps(_LINEAGE.get(model, {"upstream": [], "downstream": [], "note": "unknown model"}))


@mcp.tool()
def dbt_test(model: str = "") -> str:
    """Run dbt tests (read-only checks) for a model or all. Returns pass/fail counts."""
    target = [model] if model else list(_MODELS)
    total = sum(_MODELS.get(m, {}).get("tests", 0) for m in target)
    return json.dumps({"models": target, "tests_run": total, "passed": total, "failed": 0, "status": "pass"})


@mcp.tool()
def dbt_run(model: str = "") -> str:
    """Build dbt model(s) on Iceberg (WRITE — refreshes tables). Logs the run and
    returns a status. Swap for the real `dbt run --select <model>` in production."""
    entry = {"select": model or "all", "command": f"dbt run --select {model or '+'}",
             "at": datetime.now(timezone.utc).isoformat(), "by": "plexus-mcp", "status": "success"}
    with open(RUNLOG, "a") as f:
        f.write(json.dumps(entry) + "\n")
    built = [model] if model else list(_MODELS)
    return json.dumps({"status": "success", "models_built": built, "target": "iceberg", "logged": RUNLOG})


if __name__ == "__main__":
    mcp.run()
