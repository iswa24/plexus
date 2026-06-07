"""Trino Query Cost Guard.

Estimates a query's scan cost via `EXPLAIN (TYPE IO, FORMAT JSON)` BEFORE running it.
If the estimated scanned rows exceed the guard threshold, the query is BLOCKED (returns
the estimate + reason, runs nothing) unless the node is Approved — a governance control
that stops a runaway full-table scan on a production cluster. Under the threshold (or
approved), it runs the query and returns rows (delegating to the Trino connector).

Demo mode estimates from the SQL shape (no WHERE/`SELECT *` → expensive) so the gate is
demonstrable with no cluster.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable

from .trino import _connection, run_trino

Emit = Callable[[dict], Awaitable[None]]


def _approved(config: dict) -> bool:
    a = config.get("approved")
    return ("approved" in a) if isinstance(a, (list, tuple)) else bool(a)


def _human(n) -> str:
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "?"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def _demo_estimate(sql: str) -> dict:
    s = (sql or "").lower()
    if "limit" in s:
        return {"rows": 500, "bytes": "64 KB", "note": "bounded by LIMIT"}
    if "where" in s:
        return {"rows": 120_000, "bytes": "15 MB", "note": "filtered scan"}
    return {"rows": 48_000_000, "bytes": "6.2 GB", "note": "full-table scan (no filter)"}


async def _live_estimate(config: dict, ctx, sql: str) -> dict:
    """Best-effort Trino EXPLAIN (TYPE IO). Returns rows=None if it can't estimate."""
    try:
        import trino  # noqa: WPS433
        from trino.auth import JWTAuthentication
        conn = _connection(config, ctx) or {}
        s = ctx.settings

        def _run() -> str:
            tconn = trino.dbapi.connect(
                host=conn.get("host") or s.trino_host, port=int(conn.get("port") or s.trino_port),
                user=ctx.principal.username, catalog=config.get("catalog") or conn.get("catalog") or s.trino_catalog,
                schema=config.get("schema") or conn.get("schema"), http_scheme=conn.get("scheme") or s.trino_scheme,
                auth=JWTAuthentication(ctx.principal.token) if ctx.principal.token else None)
            cur = tconn.cursor()
            cur.execute("EXPLAIN (TYPE IO, FORMAT JSON) " + sql)
            row = cur.fetchone()
            return row[0] if row else "{}"

        data = json.loads(await asyncio.to_thread(_run))
        est = data.get("estimate") or {}
        rows = est.get("outputRowCount")
        return {"rows": int(rows) if rows is not None else None,
                "bytes": _human(est.get("outputSizeInBytes")), "note": "Trino EXPLAIN (TYPE IO)"}
    except Exception as exc:  # pragma: no cover
        return {"rows": None, "bytes": "?", "note": f"estimate unavailable ({str(exc)[:60]})"}


async def run_trino_guard(config: dict, ctx, emit: Emit) -> dict[str, Any]:
    sql = ctx.resolve(config.get("sql", ""), for_prompt=False)
    threshold = int(config.get("maxScanRows", 1_000_000) or 1_000_000)
    steps: list[dict] = [{"type": "think", "text": "Estimating scan cost via EXPLAIN before running…"}]

    async def push(value="", **extra):
        await emit({"output": {"kind": "agent", "steps": list(steps), "value": value,
                               "provider": "trino", "model": "cost-guard", **extra}})

    await push()
    est = _demo_estimate(sql) if ctx.settings.demo_mode else await _live_estimate(config, ctx, sql)
    est_rows = est.get("rows")
    rows_str = f"{est_rows:,}" if isinstance(est_rows, int) else "unknown"
    steps.append({"type": "tool_result", "tool": "EXPLAIN",
                  "summary": f"~{rows_str} rows · {est.get('bytes', '?')} · {est.get('note', '')}"})
    await push()

    over = isinstance(est_rows, int) and est_rows > threshold
    if over and not _approved(config):
        v = (f"🛑 **BLOCKED by cost guard.** Estimated **{rows_str} rows / {est.get('bytes', '?')}** "
             f"scanned exceeds the {threshold:,}-row limit ({est.get('note', '')}).\n\n"
             "Add a filter (WHERE / partition predicate) or a LIMIT, or tick **Approve** on this "
             "node to override. Nothing was run.")
        steps.append({"type": "tool_result", "tool": "guard", "summary": "blocked — over threshold"})
        await push(v, blocked=True, estimate=est)
        return {"kind": "agent", "steps": steps, "value": v, "blocked": True, "estimate": est,
                "provider": "trino", "model": "cost-guard"}

    steps.append({"type": "tool_result", "tool": "guard",
                  "summary": "approved override — running" if over else "under threshold — running"})
    await push()
    out = await run_trino(config, ctx, emit)   # delegate the actual run to the Trino connector
    out["estimate"] = est
    out["guard"] = "approved" if over else "passed"
    return out
