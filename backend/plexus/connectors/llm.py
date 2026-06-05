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
        "azure": ctx.settings.azure_deployment,
    }.get(p, "claude-sonnet-4-5")


def available(config, ctx) -> bool:
    p = provider_name(config, ctx)
    if p == "claudecode":
        return claudecli.available()
    if p == "bedrock":
        return True  # assume IAM is present; the call errors clearly if not
    if p == "azure":
        s = ctx.settings
        return bool(s.azure_endpoint and (s.azure_api_key or s.azure_use_entra))
    return bool(ctx.settings.anthropic_api_key)


def use_demo(config, ctx) -> bool:
    """True when a connector should return canned demo output instead of calling a
    live model. Demo mode is a hard contract — zero credentials, no external or
    billed calls — so it short-circuits even when a provider happens to be present.
    Also true when no provider is available at all."""
    return bool(getattr(ctx.settings, "demo_mode", False)) or not available(config, ctx)


async def _bedrock_complete(prompt, system, model, settings) -> str:
    from .. import cache  # local import to avoid cycles

    async def _call():
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
        text = "".join(b.get("text", "") for b in resp["output"]["message"]["content"]).strip()
        u = resp.get("usage") or {}
        it, ot = u.get("inputTokens", 0), u.get("outputTokens", 0)
        return (text, it, ot, cache.cost_of(model, it, ot))

    return await cache.cached_call("bedrock", model, system or "", prompt, _call)


async def _azure_complete(prompt, system, model, settings) -> str:
    from .. import cache  # local import to avoid cycles

    async def _call():
        try:
            from openai import AzureOpenAI  # noqa: WPS433 (lazy import by design)
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("openai SDK not installed. `pip install openai`.") from e
        kw = {"azure_endpoint": settings.azure_endpoint, "api_version": settings.azure_api_version}
        if settings.azure_use_entra and not settings.azure_api_key:
            try:
                from azure.identity import DefaultAzureCredential, get_bearer_token_provider
            except ImportError as e:  # pragma: no cover
                raise RuntimeError(
                    "azure-identity not installed for Entra ID auth. `pip install azure-identity`."
                ) from e
            kw["azure_ad_token_provider"] = get_bearer_token_provider(
                DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default")
        else:
            kw["api_key"] = settings.azure_api_key
        client = AzureOpenAI(**kw)
        messages = ([{"role": "system", "content": system}] if system else []) + \
                   [{"role": "user", "content": prompt}]
        resp = await asyncio.to_thread(
            client.chat.completions.create, model=model, messages=messages,
            temperature=0.2, max_tokens=1500)
        text = (resp.choices[0].message.content or "").strip()
        u = getattr(resp, "usage", None)
        it = getattr(u, "prompt_tokens", 0) or 0
        ot = getattr(u, "completion_tokens", 0) or 0
        return (text, it, ot, cache.cost_of(model, it, ot))

    return await cache.cached_call("azure", model, system or "", prompt, _call)


async def complete(prompt: str, system: str, config: dict, ctx) -> str | None:
    """Provider-agnostic single completion. Returns None if no provider available."""
    p = provider_name(config, ctx)
    m = config.get("modelId")
    if p == "claudecode" and claudecli.available():
        return await claudecli.claude_run(prompt, system=system, model=claudecli.cli_model(m))
    if p == "bedrock":
        return await _bedrock_complete(prompt, system, m if (m and m != "auto") else ctx.settings.bedrock_default_model, ctx.settings)
    if p == "azure":
        if not available(config, ctx):
            return None
        return await _azure_complete(prompt, system, m if (m and m != "auto") else ctx.settings.azure_deployment, ctx.settings)
    if ctx.settings.anthropic_api_key:
        from .. import cache  # local import to avoid cycles
        amodel = m if (m and m != "auto") else ctx.settings.anthropic_model

        async def _call():
            import anthropic  # noqa: WPS433
            client = anthropic.Anthropic(api_key=ctx.settings.anthropic_api_key)
            resp = await asyncio.to_thread(
                client.messages.create, model=amodel, max_tokens=1500,
                system=system, messages=[{"role": "user", "content": prompt}],
            )
            text = "".join(b.text for b in resp.content if b.type == "text")
            u = getattr(resp, "usage", None)
            it = getattr(u, "input_tokens", 0) or 0
            ot = getattr(u, "output_tokens", 0) or 0
            return (text, it, ot, cache.cost_of(amodel, it, ot))

        return await cache.cached_call("anthropic", amodel, system or "", prompt, _call)
    return None
