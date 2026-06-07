"""dbt Test-Failure Triage agent (demo path — no dbt, no model call)."""
import asyncio
from types import SimpleNamespace

from plexus.connectors.dbt_triage import run_dbt_triage


def _settings():
    return SimpleNamespace(demo_mode=True, anthropic_api_key="", bedrock_default_model="m",
                           anthropic_model="m", nl2sql_provider="claudecode", aws_region="us-east-1",
                           azure_endpoint="", azure_api_key="", azure_use_entra=False, azure_deployment="gpt-4o")


class _Ctx:
    def __init__(self):
        self.settings = _settings()

    def resolve(self, text, for_prompt=False):
        return text


def _run(cfg):
    async def emit(_):
        return None

    return asyncio.run(run_dbt_triage(cfg, _Ctx(), emit))


def test_triage_reports_failures_and_diagnosis():
    out = _run({"model": "fct_open_p1", "provider": "bedrock"})
    assert out["kind"] == "agent"
    types = [s["type"] for s in out["steps"]]
    assert "tool_call" in types  # ran dbt_test
    summaries = [s.get("summary", "") for s in out["steps"]]
    assert any("failed" in s for s in summaries)       # surfaced the failures
    assert "root cause" in out["value"].lower() or "fix" in out["value"].lower()  # produced triage
