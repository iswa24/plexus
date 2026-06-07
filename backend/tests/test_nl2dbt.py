"""NL → dbt model generator (demo path — no model call)."""
import asyncio
from types import SimpleNamespace

from plexus.connectors.nl2dbt import run_nl2dbt


def _settings():
    return SimpleNamespace(demo_mode=True, db_path=":memory:", anthropic_api_key="",
                           bedrock_default_model="m", anthropic_model="m", nl2sql_provider="claudecode",
                           aws_region="us-east-1", azure_endpoint="", azure_api_key="",
                           azure_use_entra=False, azure_deployment="gpt-4o",
                           trino_host="", trino_port=8080, trino_scheme="https", trino_catalog="hive")


class _Ctx:
    def __init__(self):
        self.settings = _settings()
        self.principal = SimpleNamespace(username="tester", token=None)

    def resolve(self, text, for_prompt=False):
        return text


def _run(cfg):
    async def emit(_):
        return None

    return asyncio.run(run_nl2dbt(cfg, _Ctx(), emit))


def test_generates_model_and_tests():
    out = _run({"goal": "flag credential stuffing", "modelName": "fct_cred_stuffing",
                "materialized": "view", "source": "none"})
    assert out["kind"] == "agent"
    v = out["value"]
    assert "config(" in v          # dbt model config header
    assert "schema.yml" in v       # the tests file
    assert "tests" in v
