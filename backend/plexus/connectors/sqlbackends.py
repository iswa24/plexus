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

    def __init__(self, settings, principal, catalog, schema, conn=None):
        try:
            import trino  # noqa: WPS433 (lazy import by design)
            from trino.auth import JWTAuthentication
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("trino client not installed. `pip install trino`.") from e

        conn = conn or {}                       # selected named connection (or {} -> .env)
        self.catalog = catalog
        self.schema = schema
        self.label = conn.get("label") or conn.get("host") or settings.trino_host
        self.notes = (
            f"All tables are in catalog `{catalog}`, schema `{schema}`. "
            f"Use fully-qualified names like {catalog}.{schema}.<table>."
        )
        token = getattr(principal, "token", None)
        self.conn = trino.dbapi.connect(
            host=conn.get("host") or settings.trino_host,
            port=int(conn.get("port") or settings.trino_port),
            user=getattr(principal, "username", "plexus"),  # identity propagation (OBO)
            catalog=catalog,
            schema=schema,
            http_scheme=conn.get("scheme") or settings.trino_scheme,
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


# ----------------------------------------------------------------- demo Trino backend
# Synthetic schemas + rows keyed by the connection's catalog/schema, so the NL→SQL
# agent loop (introspect -> write SQL -> run -> refine -> answer) runs end-to-end in
# demo mode with no cluster. Each plan also carries a believable "bad" first query so
# the demo visibly shows the agent self-correcting.
_DEMO_PLANS = {
    "iocs": {
        "tables": {
            "iocs": ["indicator varchar", "type varchar", "score double", "first_seen timestamp", "campaign varchar"],
            "campaigns": ["id varchar", "name varchar", "actor varchar"],
        },
        "bad_sql": "SELECT indicator, type, risk_score\nFROM {cat}.{sch}.iocs ORDER BY risk_score DESC",
        "error": "column 'risk_score' cannot be resolved",
        "sql": "SELECT indicator, type, score, campaign\nFROM {cat}.{sch}.iocs WHERE score >= 80 ORDER BY score DESC",
        "rows": [
            {"indicator": "phish-kit-3f", "type": "url", "score": 94, "campaign": "HarborToad"},
            {"indicator": "185.23.41.77", "type": "ip", "score": 91, "campaign": "HarborToad"},
            {"indicator": "secure-reset[.]co", "type": "domain", "score": 88, "campaign": "HarborToad"},
            {"indicator": "a1f9c2e0…", "type": "sha256", "score": 83, "campaign": "DriftKit"},
        ],
        "answer": ("(demo) Found {n} high-confidence IOCs (score ≥ 80), led by a credential-phishing "
                   "kit (phish-kit-3f, 94) and infra (185.23.41.77, secure-reset[.]co) tied to campaign "
                   "**HarborToad**. Recommend blocking these indicators and hunting the phishing domains."),
    },
    "incidents": {
        "tables": {
            "incidents": ["id varchar", "severity varchar", "status varchar", "owner varchar", "asset varchar", "ts timestamp"],
            "assets": ["id varchar", "name varchar", "business_unit varchar"],
        },
        "bad_sql": "SELECT id, severity, owner\nFROM {cat}.{sch}.incidents WHERE state = 'open'",
        "error": "column 'state' cannot be resolved",
        "sql": ("SELECT id, severity, owner, asset\nFROM {cat}.{sch}.incidents\n"
                "WHERE status = 'open' AND severity = 'P1' ORDER BY ts DESC"),
        "rows": [
            {"id": "INC-4821", "severity": "P1", "owner": "a.kumar", "asset": "finance-vpn-gw"},
            {"id": "INC-4855", "severity": "P1", "owner": "a.kumar", "asset": "hr-db-01"},
            {"id": "INC-4830", "severity": "P1", "owner": "m.patel", "asset": "finance-vpn-gw"},
        ],
        "answer": ("(demo) {n} open P1 incidents, concentrated on **finance-vpn-gw** and the HR database. "
                   "Two share owner a.kumar — likely one campaign. Recommend a single incident bridge "
                   "and isolating the affected assets."),
    },
    "logs": {
        "tables": {
            "events": ["event_type varchar", "principal varchar", "src_ip varchar", "ts timestamp"],
        },
        "bad_sql": "SELECT event_type, count(*)\nFROM {cat}.{sch}.events GROUP BY type",
        "error": "column 'type' cannot be resolved",
        "sql": ("SELECT event_type, count(*) AS c\nFROM {cat}.{sch}.events "
                "GROUP BY event_type ORDER BY c DESC"),
        "rows": [
            {"event_type": "failed_login", "c": 1422},
            {"event_type": "mfa_challenge", "c": 318},
            {"event_type": "password_reset", "c": 64},
            {"event_type": "token_issued", "c": 51},
        ],
        "answer": ("(demo) Top signal is a **failed_login** spike (1,422) followed by mfa_challenge — a "
                   "credential-stuffing → phishing pattern. Recommend correlating the source /24 with the "
                   "threat-intel IOCs and forcing reauth for affected principals."),
    },
}


def _demo_plan(catalog: str, schema: str) -> dict:
    plan = _DEMO_PLANS.get((schema or "").lower()) or _DEMO_PLANS.get((catalog or "").lower())
    if not plan:
        plan = {
            "tables": {"records": ["id varchar", "label varchar", "value double", "ts timestamp"]},
            "bad_sql": "SELECT id, lable FROM {cat}.{sch}.records",
            "error": "column 'lable' cannot be resolved",
            "sql": "SELECT id, label, value FROM {cat}.{sch}.records ORDER BY value DESC",
            "rows": [{"id": "R-1", "label": "alpha", "value": 9.4}, {"id": "R-2", "label": "beta", "value": 7.1}],
            "answer": "(demo) Returned {n} representative rows from {cat}.{sch}.",
        }
    fmt = lambda s: s.format(cat=catalog, sch=schema)  # noqa: E731
    return {**plan, "bad_sql": fmt(plan["bad_sql"]), "sql": fmt(plan["sql"]),
            "answer": plan["answer"].replace("{cat}", catalog).replace("{sch}", schema)}


class DemoTrinoBackend:
    """No-cluster Trino stand-in for demo mode. Same surface as TrinoBackend, but
    schema + rows are synthetic (from _demo_plan) so the agent loop is fully exercised."""

    dialect = "Trino SQL"

    def __init__(self, catalog, schema, label=None):
        self.catalog = catalog
        self.schema = schema
        self.label = label or f"{catalog}.{schema}"
        self.notes = (f"All tables are in catalog `{catalog}`, schema `{schema}`. "
                      f"Use fully-qualified names like {catalog}.{schema}.<table>.")
        self._plan = _demo_plan(catalog, schema)

    def table_names(self):
        return list(self._plan["tables"].keys())

    def schema_text(self, only=None):
        tbls = self._plan["tables"]
        keep = [t for t in tbls if (not only or t in only)] or list(tbls)
        return "\n".join(f"{self.catalog}.{self.schema}.{t}({', '.join(cols)})"
                         for t, cols in tbls.items() if t in keep)

    def run_select(self, sql, max_rows=200):
        rows = self._plan["rows"]
        cols = list(rows[0].keys()) if rows else []
        return {"kind": "rows", "columns": cols, "rows": rows[:max_rows]}

    def close(self):
        pass


def _resolve_conn(config, settings):
    """Resolve the node's `connectionId` to a stored Trino connection (host/port/…)."""
    cid = config.get("connectionId")
    if not cid:
        return None
    try:
        from ..connections import Connections  # local import avoids a cycle
        return Connections(settings.db_path, settings.demo_mode).get(cid, with_secrets=True)
    except Exception:
        return None


def get_backend(config, settings, principal):
    """Pick a backend from the node config (`source`: 'sqlite' | 'trino').

    For Trino, the node's `connectionId` is resolved to a named remote client; in demo
    mode (or for a *.local / unreachable host) a synthetic DemoTrinoBackend is used so
    the NL→SQL agent runs with no cluster.
    """
    src = (config.get("source") or "sqlite").lower()
    if src == "trino":
        conn = _resolve_conn(config, settings) or {}
        catalog = config.get("catalog") or conn.get("catalog") or settings.trino_catalog
        schema = config.get("schema") or conn.get("schema") or "default"
        host = conn.get("host") or settings.trino_host
        if settings.demo_mode or not host or str(host).endswith(".local"):
            return DemoTrinoBackend(catalog, schema, conn.get("label") or host)
        return TrinoBackend(settings, principal, catalog, schema, conn)
    return SqliteBackend(settings)
