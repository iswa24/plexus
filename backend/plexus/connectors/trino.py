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


def _connection(config: dict, ctx) -> dict | None:
    cid = config.get("connectionId")
    if not cid:
        return None
    from ..connections import Connections  # local import avoids cycles
    return Connections(ctx.settings.db_path, ctx.settings.demo_mode).get(cid, with_secrets=True)


async def run_trino(config: dict, ctx, emit: Emit) -> dict[str, Any]:
    max_rows = int(config.get("maxRows", 500))
    conn = _connection(config, ctx)          # named remote client, if selected

    if ctx.settings.demo_mode:
        rows = SAMPLE_INCIDENTS[:max_rows]
        out = {"kind": "rows", "columns": ["id", "severity", "owner", "ts"], "rows": rows}
        if conn:
            out["connection"] = conn.get("label")   # which cluster answered (shown in trace/audit)
        return out

    sql, params = _parameterize(config.get("sql", ""), ctx)

    try:
        import trino  # noqa: WPS433 (lazy import by design)
        from trino.auth import JWTAuthentication
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(
            "trino client not installed. `pip install trino` or set PLEXUS_DEMO_MODE=true."
        ) from e

    s = ctx.settings
    c = conn or {}                                  # selected connection (or {} -> .env defaults)
    auth = JWTAuthentication(ctx.principal.token) if ctx.principal.token else None

    def _run() -> tuple[list[str], list[dict]]:
        tconn = trino.dbapi.connect(
            host=c.get("host") or s.trino_host,
            port=int(c.get("port") or s.trino_port),
            user=ctx.principal.username,  # identity propagation (OBO)
            catalog=config.get("catalog") or c.get("catalog") or s.trino_catalog,
            schema=config.get("schema") or c.get("schema"),
            http_scheme=c.get("scheme") or s.trino_scheme,
            auth=auth,
        )
        cur = tconn.cursor()
        cur.execute(sql, params)  # bound params, not string-interpolated
        cols = [d[0] for d in cur.description] if cur.description else []
        data = cur.fetchmany(max_rows)
        return cols, [dict(zip(cols, row)) for row in data]

    cols, rows = await asyncio.to_thread(_run)
    return {"kind": "rows", "columns": cols, "rows": rows}
