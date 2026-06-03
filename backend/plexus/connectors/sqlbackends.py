"""Pluggable SQL backends for the NL2SQL agent.

Each backend exposes the same surface — `schema_text()`, `run_select()`, plus a
`dialect` and join `notes` for the prompt — so the agent is identical whether it
runs over the local SQLite security DBs or a real Trino cluster. Only the backend
changes; the agent loop does not.
"""
from __future__ import annotations

from collections import OrderedDict

from . import sqldb


class SqliteBackend:
    dialect = "SQLite"
    notes = (
        "incidents tables are in main; asset/identity tables use the 'assets.' prefix. "
        "Joins: incidents.asset_id -> assets.assets.id; "
        "incidents.opened_by -> assets.identities.email; "
        "assets.assets.owner_email -> assets.identities.email."
    )

    def __init__(self, settings):
        self.conn = sqldb.open_db(settings)

    def table_names(self):
        return sqldb.list_tables(self.conn)

    def schema_text(self, only=None):
        return sqldb.schema_text(self.conn, only)

    def run_select(self, sql, max_rows=200):
        return sqldb.run_select(self.conn, sql, max_rows)

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass


class TrinoBackend:
    """NL2SQL over a Trino cluster, scoped to one catalog.schema.

    Schema is introspected from `<catalog>.information_schema.columns`, and the
    caller's identity (+ OBO token) is propagated for row-level security.
    """

    dialect = "Trino SQL"

    def __init__(self, settings, principal, catalog, schema):
        try:
            import trino  # noqa: WPS433 (lazy import by design)
            from trino.auth import JWTAuthentication
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("trino client not installed. `pip install trino`.") from e

        self.catalog = catalog
        self.schema = schema
        self.notes = (
            f"All tables are in catalog `{catalog}`, schema `{schema}`. "
            f"Use fully-qualified names like {catalog}.{schema}.<table>."
        )
        token = getattr(principal, "token", None)
        self.conn = trino.dbapi.connect(
            host=settings.trino_host,
            port=settings.trino_port,
            user=getattr(principal, "username", "plexus"),  # identity propagation
            catalog=catalog,
            schema=schema,
            http_scheme=settings.trino_scheme,
            auth=JWTAuthentication(token) if token else None,
        )

    def table_names(self):
        cur = self.conn.cursor()
        cur.execute(
            f"SELECT table_name FROM {self.catalog}.information_schema.tables "
            f"WHERE table_schema = ?",
            (self.schema,),
        )
        return [r[0] for r in cur.fetchall()]

    def schema_text(self, only=None):
        cur = self.conn.cursor()
        base = (f"SELECT table_name, column_name, data_type "
                f"FROM {self.catalog}.information_schema.columns WHERE table_schema = ?")
        params = [self.schema]
        if only:
            base += " AND table_name IN (" + ",".join(["?"] * len(only)) + ")"
            params += list(only)
        cur.execute(base + " ORDER BY table_name, ordinal_position", params)
        tables: "OrderedDict[str, list[str]]" = OrderedDict()
        for table, col, dtype in cur.fetchall():
            tables.setdefault(table, []).append(f"{col} {dtype}")
        if not tables:
            return f"(no tables found in {self.catalog}.{self.schema})"
        return "\n".join(
            f"{self.catalog}.{self.schema}.{t}({', '.join(cols)})" for t, cols in tables.items()
        )

    def run_select(self, sql, max_rows=200):
        s = (sql or "").strip().rstrip(";")
        low = s.lower()
        if not (low.startswith("select") or low.startswith("with")):
            raise ValueError("only SELECT/WITH queries are allowed")
        if sqldb._WRITE.search(s):
            raise ValueError("write/DDL statements are not allowed")
        cur = self.conn.cursor()
        cur.execute(s)
        cols = [d[0] for d in cur.description] if cur.description else []
        data = cur.fetchmany(max_rows)
        return {"kind": "rows", "columns": cols, "rows": [dict(zip(cols, r)) for r in data]}

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass


def get_backend(config, settings, principal):
    """Pick a backend from the node config (`source`: 'sqlite' | 'trino')."""
    src = (config.get("source") or "sqlite").lower()
    if src == "trino":
        return TrinoBackend(
            settings, principal,
            config.get("catalog") or settings.trino_catalog,
            config.get("schema") or "default",
        )
    return SqliteBackend(settings)
