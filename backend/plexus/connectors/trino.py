"""Trino connector. Client imported lazily; demo mode needs no cluster.

Identity / OBO: the caller's bearer token (Principal.token) is used as the
downstream JWT so Trino enforces row-level security as the app user. Wire your
real OAuth2/OBO token-exchange in auth.py and it flows through here unchanged.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any, Awaitable, Callable

from .demo_data import SAMPLE_INCIDENTS

Emit = Callable[[dict], Awaitable[None]]
_REF = re.compile(r"@\{([^}]+)\}")


def _parameterize(template: str, ctx) -> tuple[str, list]:
    """Replace each @{ref} in the SQL with a bind placeholder (?) and collect its
    resolved value as a parameter — so upstream values are never string-interpolated
    into SQL (prevents injection). Trino's client uses qmark paramstyle."""
    params: list = []

    def repl(m: re.Match) -> str:
        params.append(ctx.resolve(m.group(0), for_prompt=False))
        return "?"

    return _REF.sub(repl, template or ""), params


async def run_trino(config: dict, ctx, emit: Emit) -> dict[str, Any]:
    max_rows = int(config.get("maxRows", 500))

    if ctx.settings.demo_mode:
        rows = SAMPLE_INCIDENTS[:max_rows]
        return {"kind": "rows", "columns": ["id", "severity", "owner", "ts"], "rows": rows}

    sql, params = _parameterize(config.get("sql", ""), ctx)

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
        cur.execute(sql, params)  # bound params, not string-interpolated
        cols = [d[0] for d in cur.description] if cur.description else []
        data = cur.fetchmany(max_rows)
        return cols, [dict(zip(cols, row)) for row in data]

    cols, rows = await asyncio.to_thread(_run)
    return {"kind": "rows", "columns": cols, "rows": rows}
