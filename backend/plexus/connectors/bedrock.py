"""Bedrock LLM connector — uses the Converse streaming API.

Real boto3 code, but boto3 is imported lazily so demo mode needs no AWS.
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from .demo_data import demo_bedrock_text

Emit = Callable[[dict], Awaitable[None]]


async def run_bedrock(config: dict, ctx, emit: Emit) -> dict[str, Any]:
    # `goal`/`input` are accepted as prompt fallbacks so "AI Agent" (model.bedrock) is a
    # drop-in superset of the retired "AI Agent · Prompt" (model.prompt) node.
    prompt = ctx.resolve(config.get("prompt") or config.get("goal") or config.get("input") or "", for_prompt=True)
    system = config.get("system", "")
    provider = (config.get("provider") or "bedrock").lower()
    model_id = config.get("modelId") or ctx.settings.bedrock_default_model

    if ctx.settings.demo_mode:
        text = demo_bedrock_text(ctx.first_input_text(), ctx.first_rows_count())
        acc = ""
        for i in range(0, len(text), 4):
            acc = text[: i + 4]
            await emit({"partial": acc, "provider": provider})
            await asyncio.sleep(0.01)
        from . import llm
        return {"kind": "text", "value": text, "tokens": max(1, len(text) // 4),
                "provider": provider, "model": llm.model_name(config, ctx)}

    # AI Agent is provider-agnostic: only the native "bedrock" path streams via
    # Converse below; azure / anthropic / claudecode go through the shared client.
    if provider != "bedrock":
        from . import llm
        try:
            v = await llm.complete(prompt, system, config, ctx)
        except Exception as exc:
            v = f"⚠ model provider error: {exc}"
        v = v or "⚠ no response from the model provider."
        await emit({"partial": v, "provider": provider})
        return {"kind": "text", "value": v, "provider": provider, "model": llm.model_name(config, ctx)}

    try:
        import boto3  # noqa: WPS433 (lazy import by design)
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(
            "boto3 is not installed. `pip install boto3` or set PLEXUS_DEMO_MODE=true."
        ) from e

    client = boto3.client("bedrock-runtime", region_name=ctx.settings.aws_region)
    kwargs: dict[str, Any] = {
        "modelId": model_id,
        "messages": [{"role": "user", "content": [{"text": prompt}]}],
        "inferenceConfig": {
            "temperature": float(config.get("temperature", 0.2)),
            "maxTokens": int(config.get("maxTokens", 1500)),
        },
    }
    if system:
        kwargs["system"] = [{"text": system}]

    resp = await asyncio.to_thread(client.converse_stream, **kwargs)

    # Bridge boto3's synchronous EventStream into our async emit loop.
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def _pump() -> None:
        try:
            for event in resp["stream"]:
                delta = (
                    event.get("contentBlockDelta", {})
                    .get("delta", {})
                    .get("text")
                )
                if delta:
                    loop.call_soon_threadsafe(queue.put_nowait, ("delta", delta))
                meta = event.get("metadata")
                if meta:
                    loop.call_soon_threadsafe(queue.put_nowait, ("meta", meta))
            loop.call_soon_threadsafe(queue.put_nowait, ("end", None))
        except Exception as exc:  # pragma: no cover
            loop.call_soon_threadsafe(queue.put_nowait, ("error", str(exc)))

    asyncio.create_task(asyncio.to_thread(_pump))

    acc = ""
    tokens = None
    while True:
        kind, val = await queue.get()
        if kind == "delta":
            acc += val
            await emit({"partial": acc})
        elif kind == "meta":
            tokens = (val or {}).get("usage", {}).get("outputTokens")
        elif kind == "error":
            raise RuntimeError(val)
        else:
            break

    return {"kind": "text", "value": acc, "tokens": tokens, "provider": "bedrock", "model": model_id}
