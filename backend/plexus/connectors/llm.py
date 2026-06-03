"""Shared provider-aware model access, used by all agent connectors.

One place that knows how to talk to claudecode (Max sub) / bedrock (IAM) /
anthropic (API). Each agent type calls `complete()` and stays provider-agnostic.
"""
from __future__ import annotations

import asyncio

from . import claudecli

_DEFAULTS = {
    "claudecode": "claude-sonnet-4-5",
    "bedrock": None,        # filled from settings
    "anthropic": None,      # filled from settings
}


def provider_name(config, ctx) -> str:
    return (config.get("provider") or ctx.settings.nl2sql_provider or "claudecode").lower()


def model_name(config, ctx) -> str:
    p = provider_name(config, ctx)
    m = config.get("modelId")
    if m and m != "auto":
        return m
    return {
        "claudecode": "claude-sonnet-4-5",
        "bedrock": ctx.settings.bedrock_default_model,
        "anthropic": ctx.settings.anthropic_model,
    }.get(p, "claude-sonnet-4-5")


def available(config, ctx) -> bool:
    p = provider_name(config, ctx)
    if p == "claudecode":
        return claudecli.available()
    if p == "bedrock":
        return True  # assume IAM is present; the call errors clearly if not
    return bool(ctx.settings.anthropic_api_key)


async def _bedrock_complete(prompt, system, model, settings) -> str:
    try:
        import boto3  # noqa: WPS433
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("boto3 not installed. `pip install boto3`.") from e
    client = boto3.client("bedrock-runtime", region_name=settings.aws_region)
    kw = {
        "modelId": model,
        "messages": [{"role": "user", "content": [{"text": prompt}]}],
        "inferenceConfig": {"temperature": 0.2, "maxTokens": 1500},
    }
    if system:
        kw["system"] = [{"text": system}]
    resp = await asyncio.to_thread(client.converse, **kw)
    return "".join(b.get("text", "") for b in resp["output"]["message"]["content"]).strip()


async def complete(prompt: str, system: str, config: dict, ctx) -> str | None:
    """Provider-agnostic single completion. Returns None if no provider available."""
    p = provider_name(config, ctx)
    m = config.get("modelId")
    if p == "claudecode" and claudecli.available():
        return await claudecli.claude_run(prompt, system=system, model=claudecli.cli_model(m))
    if p == "bedrock":
        return await _bedrock_complete(prompt, system, m if (m and m != "auto") else ctx.settings.bedrock_default_model, ctx.settings)
    if ctx.settings.anthropic_api_key:
        import anthropic  # noqa: WPS433
        client = anthropic.Anthropic(api_key=ctx.settings.anthropic_api_key)
        resp = await asyncio.to_thread(
            client.messages.create,
            model=(m if (m and m != "auto") else ctx.settings.anthropic_model),
            max_tokens=1500, system=system, messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in resp.content if b.type == "text")
    return None
