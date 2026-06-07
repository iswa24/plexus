"""Agentic NL→SQL over Trino — demo path (no cluster, no model key).

Locks in the corrected architecture: a question drives an agent that introspects
the Trino schema, writes SQL, runs it via a tool, sees an error, and *refines* —
the self-correcting loop — all against the synthetic DemoTrinoBackend.
"""
import asyncio
from types import SimpleNamespace

from plexus.auth import Principal
from plexus.connectors.nl2sql import run_nl2sql
from plexus.connectors.sqlbackends import DemoTrinoBackend, TrinoBackend, get_backend


class _Ctx:
    def __init__(self, settings):
        self.settings = settings
        self.principal = Principal("tester")

    def resolve(self, text, for_prompt=False):
        return text


def _settings(**over):
    base = dict(demo_mode=True, db_path=":memory:", trino_catalog="threat_intel",
                trino_host="", trino_port=8443, trino_scheme="https",
                nl2sql_provider="claudecode", aws_region="us-east-1",
                bedrock_default_model="m", anthropic_model="m", anthropic_api_key=None)
    base.update(over)
    return SimpleNamespace(**base)


async def _noop(_):
    return None


def test_demo_trino_backend_has_synthetic_schema_and_rows():
    b = DemoTrinoBackend("threat_intel", "iocs")
    assert "iocs(" in b.schema_text()
    res = b.run_select("SELECT * FROM threat_intel.iocs.iocs")
    assert res["kind"] == "rows" and len(res["rows"]) >= 1


def test_get_backend_demo_trino_never_connects():
    # source=trino in demo mode must resolve to the synthetic backend, not the real
    # TrinoBackend (which would import the trino client and open a socket).
    b = get_backend({"source": "trino", "schema": "iocs"}, _settings(), Principal("t"))
    assert isinstance(b, DemoTrinoBackend)
    assert not isinstance(b, TrinoBackend)
    b.close()


def test_local_host_connection_uses_demo_backend():
    # a *.local host (the seeded demo connections) is treated as unreachable → demo.
    b = get_backend({"source": "trino", "schema": "incidents"},
                    _settings(demo_mode=False, trino_host="warehouse.trino.local"), Principal("t"))
    assert isinstance(b, DemoTrinoBackend)
    b.close()


def test_nl2sql_trino_demo_shows_self_correcting_loop():
    out = asyncio.run(run_nl2sql(
        {"goal": "find recent phishing IOCs", "source": "trino", "schema": "iocs"},
        _Ctx(_settings()), _noop))
    assert out["kind"] == "agent"
    types = [s["type"] for s in out["steps"]]
    assert types.count("tool_call") == 2          # first (bad) query, then the refined one
    results = [s for s in out["steps"] if s["type"] == "tool_result"]
    assert any("error" in s["summary"] for s in results)   # the loop saw an error…
    assert any("rows" in s["summary"] for s in results)    # …then succeeded
    assert out["value"]                                     # produced a final answer
    assert "threat_intel.iocs.iocs" in out["tables"]       # lineage captured


def test_nl2sql_trino_demo_per_schema_plans_differ():
    inc = asyncio.run(run_nl2sql(
        {"goal": "open P1s", "source": "trino", "schema": "incidents"}, _Ctx(_settings()), _noop))
    logs = asyncio.run(run_nl2sql(
        {"goal": "auth signals", "source": "trino", "schema": "logs"}, _Ctx(_settings()), _noop))
    assert "incidents" in inc["tables"][0]
    assert "events" in logs["tables"][0]
