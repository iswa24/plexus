"""Auto-Landscape mapper (demo path — registry + simulated dbt/Kestra inventory)."""
import asyncio
from types import SimpleNamespace

from plexus.connectors.landscape_map import run_landscape_map


def _settings():
    return SimpleNamespace(demo_mode=True, db_path=":memory:", anthropic_api_key="",
                           bedrock_default_model="m", anthropic_model="m", nl2sql_provider="claudecode",
                           aws_region="us-east-1", azure_endpoint="", azure_api_key="",
                           azure_use_entra=False, azure_deployment="gpt-4o")


class _Ctx:
    def __init__(self):
        self.settings = _settings()
        self.principal = SimpleNamespace(username="tester", token=None)

    def resolve(self, text, for_prompt=False):
        return text


def _run(cfg):
    async def emit(_):
        return None

    return asyncio.run(run_landscape_map(cfg, _Ctx(), emit))


def test_inventories_models_and_flows():
    out = _run({"provider": "bedrock"})
    assert out["kind"] == "agent"
    assert out["counts"]["models"] >= 4      # simulated dbt models
    assert out["counts"]["flows"] >= 3       # simulated Kestra flows
    assert "Data Landscape" in out["value"]
    assert "what to build" in out["value"].lower() or "next builds" in out["value"].lower()
