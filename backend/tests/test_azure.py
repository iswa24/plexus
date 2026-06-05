"""Azure OpenAI provider plumbing (hermetic — no live Azure needed)."""
import types

from plexus.cache import cost_of
from plexus.connectors import llm


def _ctx(**azure):
    s = types.SimpleNamespace(
        demo_mode=False, nl2sql_provider="claudecode",
        azure_endpoint=azure.get("endpoint", ""),
        azure_api_key=azure.get("key", ""),
        azure_api_version="2024-10-21",
        azure_deployment=azure.get("deployment", ""),
        azure_use_entra=azure.get("entra", False),
        anthropic_api_key="", anthropic_model="claude-sonnet-4-5",
        bedrock_default_model="anthropic.claude-3-5-sonnet",
    )
    return types.SimpleNamespace(settings=s)


def test_azure_available_requires_endpoint_and_auth():
    cfg = {"provider": "azure"}
    assert llm.available(cfg, _ctx()) is False                                   # nothing set
    assert llm.available(cfg, _ctx(endpoint="https://x.openai.azure.com/")) is False  # no auth
    assert llm.available(cfg, _ctx(endpoint="https://x.openai.azure.com/", key="k")) is True
    # Entra ID (managed identity) counts as auth — no key needed
    assert llm.available(cfg, _ctx(endpoint="https://x.openai.azure.com/", entra=True)) is True


def test_azure_model_name_falls_back_to_deployment():
    ctx = _ctx(deployment="gpt-4o-prod")
    assert llm.model_name({"provider": "azure"}, ctx) == "gpt-4o-prod"
    assert llm.model_name({"provider": "azure", "modelId": "gpt-4o-mini"}, ctx) == "gpt-4o-mini"


def test_azure_demo_mode_short_circuits():
    ctx = _ctx(endpoint="https://x.openai.azure.com/", key="k")
    ctx.settings.demo_mode = True
    assert llm.use_demo({"provider": "azure"}, ctx) is True   # never call live in demo


def test_gpt_pricing_in_cost_meter():
    # gpt-4o-mini must match before gpt-4o / gpt-4 (insertion order)
    assert cost_of("gpt-4o-mini", 1_000_000, 0) == 0.15
    assert cost_of("gpt-4o-prod", 1_000_000, 0) == 2.50
    assert cost_of("gpt-4-turbo", 1_000_000, 0) == 30.0
