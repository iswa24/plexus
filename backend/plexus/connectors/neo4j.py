"""Neo4j (Cypher) connector. Driver imported lazily; demo mode needs no DB."""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from .demo_data import SAMPLE_GRAPH

Emit = Callable[[dict], Awaitable[None]]


async def run_neo4j(config: dict, ctx, emit: Emit) -> dict[str, Any]:
    query = ctx.resolve(config.get("query", ""), for_prompt=False)

    if ctx.settings.demo_mode:
        return {"kind": "rows", "columns": ["entity", "type", "rel"], "rows": SAMPLE_GRAPH}

    try:
        from neo4j import GraphDatabase  # noqa: WPS433 (lazy import by design)
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(
            "neo4j driver not installed. `pip install neo4j` or set PLEXUS_DEMO_MODE=true."
        ) from e

    s = ctx.settings

    def _run() -> list[dict]:
        # NOTE: for production, pass upstream values as Cypher parameters
        # (session.run(query, **params)) instead of inlining resolved refs,
        # to avoid injection. Kept inline here to mirror the visual @-refs.
        driver = GraphDatabase.driver(s.neo4j_uri, auth=(s.neo4j_user, s.neo4j_password))
        try:
            with driver.session(database=s.neo4j_database) as sess:
                result = sess.run(query)
                return [dict(record) for record in result]
        finally:
            driver.close()

    rows = await asyncio.to_thread(_run)
    cols = list(rows[0].keys()) if rows else []
    return {"kind": "rows", "columns": cols, "rows": rows}
