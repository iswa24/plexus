"""dbt as an MCP server (stdio) — lets a Plexus agent inspect and trigger the
data layer (dbt models).

TWO MODES (auto-selected):
  • LIVE  — set DBT_PROJECT_DIR to your dbt project. `list_models`/`model_lineage`
            read target/manifest.json; `dbt_test`/`dbt_run` shell out to the `dbt`
            CLI in that project. This connects Plexus to YOUR local dbt.
  • DEMO  — DBT_PROJECT_DIR unset → returns a simulated security-warehouse project
            so the node works with no dbt (tests and `PLEXUS_DEMO_MODE=true`).

Environment (LIVE mode):
  DBT_PROJECT_DIR    path to your dbt project (the dir with dbt_project.yml)  (required for live)
  DBT_PROFILES_DIR   path to profiles.yml if not the default ~/.dbt           (optional)
  DBT_TARGET         dbt target/profile to use, e.g. dev/prod                  (optional)
  DBT_BIN            dbt executable (default: "dbt" on PATH)                    (optional)

manifest.json: produced by `dbt compile`/`dbt parse`/any dbt run. If it's missing,
LIVE mode runs `dbt parse` once to generate it.

`dbt_run` is a WRITE (rebuilds tables) — register it under `write_tools` in
PLEXUS_MCP_SERVERS so Plexus keeps it approval-gated.

Run standalone:  python mcp_servers/dbt_server.py
"""
from __future__ import annotations

import json
import os
import subprocess

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("dbt")

PROJECT_DIR = os.environ.get("DBT_PROJECT_DIR") or ""
PROFILES_DIR = os.environ.get("DBT_PROFILES_DIR") or ""
TARGET = os.environ.get("DBT_TARGET") or ""
DBT_BIN = os.environ.get("DBT_BIN") or "dbt"
RUNLOG = os.environ.get("PLEXUS_DBT_RUNLOG", "/tmp/plexus_dbt_runs.jsonl")
LIVE = bool(PROJECT_DIR)

# ---- simulated project (DEMO mode only) ----
_MODELS = {
    "stg_incidents":     {"materialized": "view", "rows": None, "tests": 4, "fresh_min": 12},
    "stg_assets":        {"materialized": "view", "rows": None, "tests": 3, "fresh_min": 12},
    "dim_business_unit": {"materialized": "table", "rows": 8, "tests": 2, "fresh_min": 35},
    "fct_open_p1":       {"materialized": "incremental", "rows": 7, "tests": 5, "fresh_min": 9},
}
_LINEAGE = {
    "fct_open_p1": {"upstream": ["stg_incidents", "stg_assets", "dim_business_unit"], "downstream": ["mart_exec_brief"]},
    "mart_exec_brief": {"upstream": ["fct_open_p1"], "downstream": []},
}


def _dbt(*args: str) -> dict:
    """Run the dbt CLI in the project dir; return {returncode, stdout, stderr}."""
    cmd = [DBT_BIN, *args, "--project-dir", PROJECT_DIR]
    if PROFILES_DIR:
        cmd += ["--profiles-dir", PROFILES_DIR]
    if TARGET:
        cmd += ["--target", TARGET]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=600)  # noqa: S603
    return {"returncode": p.returncode, "stdout": p.stdout[-4000:], "stderr": p.stderr[-2000:]}


def _manifest() -> dict:
    path = os.path.join(PROJECT_DIR, "target", "manifest.json")
    if not os.path.exists(path):
        _dbt("parse")  # generate it once
    with open(path) as f:
        return json.load(f)


def _short(uid: str) -> str:
    """model.my_project.stg_incidents -> stg_incidents"""
    return uid.split(".")[-1]


@mcp.tool()
def list_models() -> str:
    """List dbt models with materialization. JSON rows: [{model, materialized, path}]."""
    if not LIVE:
        return json.dumps([{"model": m, **v} for m, v in _MODELS.items()])
    man = _manifest()
    rows = []
    for uid, n in man.get("nodes", {}).items():
        if n.get("resource_type") != "model":
            continue
        rows.append({"model": n.get("name"),
                     "materialized": (n.get("config") or {}).get("materialized", "view"),
                     "schema": n.get("schema"), "path": n.get("original_file_path")})
    return json.dumps(rows)


@mcp.tool()
def model_lineage(model: str) -> str:
    """Return upstream/downstream dependencies for a model. JSON {upstream, downstream}."""
    if not LIVE:
        return json.dumps(_LINEAGE.get(model, {"upstream": [], "downstream": [], "note": "unknown model"}))
    man = _manifest()
    uid = next((u for u in man.get("nodes", {}) if _short(u) == model and u.startswith("model.")), None)
    if not uid:
        return json.dumps({"upstream": [], "downstream": [], "note": f"model '{model}' not found"})
    parents = man.get("parent_map", {}).get(uid, [])
    children = man.get("child_map", {}).get(uid, [])
    return json.dumps({"upstream": [_short(p) for p in parents if p.startswith("model.")],
                       "downstream": [_short(c) for c in children if c.startswith("model.")]})


@mcp.tool()
def dbt_test(model: str = "") -> str:
    """Run dbt tests (read-only checks) for a model or all. Returns pass/fail counts."""
    if not LIVE:
        target = [model] if model else list(_MODELS)
        total = sum(_MODELS.get(m, {}).get("tests", 0) for m in target)
        return json.dumps({"models": target, "tests_run": total, "passed": total, "failed": 0, "status": "pass"})
    res = _dbt("test", *(["--select", model] if model else []))
    rr = os.path.join(PROJECT_DIR, "target", "run_results.json")
    passed = failed = 0
    if os.path.exists(rr):
        with open(rr) as f:
            for r in json.load(f).get("results", []):
                if r.get("status") == "pass":
                    passed += 1
                elif r.get("status") in ("fail", "error"):
                    failed += 1
    return json.dumps({"select": model or "all", "passed": passed, "failed": failed,
                       "status": "pass" if res["returncode"] == 0 else "fail", "log": res["stdout"][-800:]})


@mcp.tool()
def dbt_run(model: str = "") -> str:
    """Build dbt model(s) (WRITE — refreshes tables). Runs `dbt run [--select model]`."""
    if not LIVE:
        from datetime import datetime, timezone
        with open(RUNLOG, "a") as f:
            f.write(json.dumps({"select": model or "all", "at": datetime.now(timezone.utc).isoformat(),
                                "by": "plexus-mcp", "status": "success"}) + "\n")
        built = [model] if model else list(_MODELS)
        return json.dumps({"status": "success", "models_built": built, "target": "demo", "logged": RUNLOG})
    res = _dbt("run", *(["--select", model] if model else []))
    return json.dumps({"select": model or "all", "status": "success" if res["returncode"] == 0 else "fail",
                       "log": res["stdout"][-1500:], "stderr": res["stderr"][-500:] if res["returncode"] else ""})


if __name__ == "__main__":
    mcp.run()
