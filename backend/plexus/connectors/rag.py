"""RAG knowledge agent — retrieve relevant passages from the knowledge base,
then answer grounded in them with citations."""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from . import llm, ragstore

Emit = Callable[[dict], Awaitable[None]]


async def run_rag(config: dict, ctx, emit: Emit) -> dict[str, Any]:
    question = ctx.resolve(config.get("goal") or config.get("prompt") or "", for_prompt=True)
    k = int(config.get("topK", 4))
    provider, model = llm.provider_name(config, ctx), llm.model_name(config, ctx)
    steps: list[dict] = []
    chunks = ragstore.search(question, k)
    cites = [c["id"] for c in chunks]

    async def push(value=""):
        await emit({"output": {"kind": "agent", "steps": list(steps), "value": value,
                               "provider": provider, "model": model, "tables": cites}})

    steps.append({"type": "think", "text": f"Retrieved {len(chunks)} passages from the knowledge base."})
    for c in chunks:
        steps.append({"type": "tool_result", "tool": "retrieve", "summary": "📄 " + c["id"]})
    await push()

    if not chunks:
        v = "I couldn't find anything relevant in the knowledge base for that question."
        await push(v)
        return {"kind": "agent", "steps": steps, "value": v, "provider": provider, "model": model, "tables": cites}
    if not llm.available(config, ctx):
        v = "(demo) Retrieved: " + ", ".join(cites) + ". Configure a model provider for grounded answers."
        await push(v)
        return {"kind": "agent", "steps": steps, "value": v, "provider": "demo", "model": "(none)", "tables": cites}

    context = "\n\n".join(f"[{c['id']}]\n{c['text']}" for c in chunks)
    system = ("You are a security knowledge assistant. Answer ONLY from the provided context. "
              "Cite sources inline like [doc#n]. If the answer is not in the context, say so.")
    prompt = f"Context:\n{context}\n\nQuestion: {question}\n\nAnswer concisely, with citations."
    v = await llm.complete(prompt, system, config, ctx)
    await push(v)
    return {"kind": "agent", "steps": steps, "value": v, "provider": provider, "model": model, "tables": cites}
