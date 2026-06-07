"""NL → Kestra flow generator (demo path — no model call)."""
import asyncio
from types import SimpleNamespace

from plexus.connectors.nl2kestra import run_nl2kestra


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

    return asyncio.run(run_nl2kestra(cfg, _Ctx(), emit))


def test_generates_flow_yaml():
    out = _run({"goal": "sweep new IOCs every 30 minutes and alert", "flowId": "ioc_sweep",
                "namespace": "company.team"})
    assert out["kind"] == "agent"
    v = out["value"]
    assert "namespace:" in v and "tasks:" in v   # valid Kestra flow shape
    assert "triggers:" in v                       # has a schedule
