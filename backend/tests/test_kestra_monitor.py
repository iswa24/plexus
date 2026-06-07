"""Kestra Execution Monitor + Alert agent (demo path — no Kestra, no model call)."""
import asyncio
from types import SimpleNamespace

from plexus.connectors.kestra_monitor import run_kestra_monitor


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

    return asyncio.run(run_kestra_monitor(cfg, _Ctx(), emit))


def test_monitor_flags_failures_and_alerts():
    out = _run({"namespace": "company.team", "provider": "bedrock"})
    assert out["kind"] == "agent"
    assert out["alert"] is True                              # demo set has failed runs
    summaries = [s.get("summary", "") for s in out["steps"]]
    assert any("need attention" in s for s in summaries)     # surfaced the count
    assert "ioc_sweep" in out["value"]                       # named the failing flow
