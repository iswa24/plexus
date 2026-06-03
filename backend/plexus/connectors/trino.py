"""Trino connector. Client imported lazily; demo mode needs no cluster.

Identity / OBO: the caller's bearer token (Principal.token) is used as the
downstream JWT so Trino enforces row-level security as the app user. Wire your
real OAuth2/OBO token-exchange in auth.py and it flows through here unchanged.
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from .demo_data import SAMPLE_INCIDENTS

Emit = Callable[[dict], Awaitable[None]]


async def run_trino(config: dict, ctx, emit: Emit) -> dict[str, Any]:
    sql = ctx.resolve(config.get("sql", ""), for_prompt=False)
    max_rows = int(config.get("maxRows", 500))

    if ctx.settings.demo_mode:
        rows = SAMPLE_INCIDENTS[:max_rows]
        return {"kind": "rows", "columns": ["id", "severity", "owner", "ts"], "rows": rows}

    try:
        import trino  # noqa: WPS433 (lazy import by design)
        from trino.auth import JWTAuthentication
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(
            "trino client not installed. `pip install trino` or set PLEXUS_DEMO_MODE=true."
        ) from e

    s = ctx.settings
    auth = JWTAuthentication(ctx.principal.token) if ctx.principal.token else None

    def _run() -> tuple[list[str], list[dict]]:
        conn = trino.dbapi.connect(
            host=s.trino_host,
            port=s.trino_port,
            user=ctx.principal.username,  # identity propagation
            catalog=config.get("catalog") or s.trino_catalog,
            schema=config.get("schema"),
            http_scheme=s.trino_scheme,
            auth=auth,
        )
        cur = conn.cursor()
        cur.execute(sql)
        cols = [d[0] for d in cur.description] if cur.description else []
        data = cur.fetchmany(max_rows)
        return cols, [dict(zip(cols, row)) for row in data]

    cols, rows = await asyncio.to_thread(_run)
    return {"kind": "rows", "columns": cols, "rows": rows}
