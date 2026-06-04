"""FastAPI app: app registry REST + run WebSocket + static frontend."""
from __future__ import annotations

import json
import os
import re

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .audit import AuditLog
from .auth import principal_from_headers
from .config import get_settings
from . import cache
from .connectors import llm
from .executor import execute
from .generator import generate_app
from .models import AppDef
from .registry import Registry

settings = get_settings()

app = FastAPI(title="Plexus", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
    allow_methods=["*"],
    allow_headers=["*"],
)

registry = Registry(settings.db_path)
audit = AuditLog(settings.db_path)


# ---------------------------------------------------------------- health
@app.get("/api/health")
def health():
    return {"ok": True, "demoMode": settings.demo_mode}


@app.get("/api/usage")
def usage():
    """Token usage, model cost, and cache savings (the response cache means an
    identical model call is never paid for twice)."""
    return cache.stats()


# ---------------------------------------------------------------- registry
@app.get("/api/apps")
def list_apps():
    return registry.list()


@app.get("/api/apps/{app_id}")
def get_app(app_id: str):
    a = registry.get(app_id)
    if not a:
        raise HTTPException(status_code=404, detail="app not found")
    return a


@app.post("/api/apps")
def create_app(app_def: AppDef):
    app_def.id = None
    return registry.upsert(app_def)


@app.put("/api/apps/{app_id}")
def save_app(app_id: str, app_def: AppDef):
    app_def.id = app_id
    return registry.upsert(app_def)


@app.delete("/api/apps/{app_id}")
def delete_app(app_id: str):
    registry.delete(app_id)
    return {"ok": True}


@app.get("/api/audit")
def get_audit(limit: int = 100):
    return audit.recent(limit)


# ---------------------------------------------------------------- run helpers + saved app
def _pick(results: dict, app_def) -> str:
    types = {n.id: n.type for n in app_def.nodes}
    for pref in ("output.document", "output.text"):
        for nid, o in results.items():
            if types.get(nid) == pref and isinstance(o, dict) and o.get("value"):
                return o["value"]
    for nid, o in results.items():
        if types.get(nid, "").startswith(("model", "action")) and isinstance(o, dict) and o.get("value"):
            return o["value"]
    return ""


async def _run_def(app_def, inputs, principal):
    results: dict = {}

    async def emit(frame: dict):
        if frame.get("event") == "node" and frame.get("status") == "done":
            results[frame["nodeId"]] = frame.get("output")

    await execute(app_def, inputs, settings, principal, emit, audit)
    return results, _pick(results, app_def)


@app.post("/api/apps/{app_id}/run")
async def run_saved_app(app_id: str, body: dict, request: Request):
    """Run a saved agent by id. FREE cache-first: identical/reworded questions
    (matched by a zero-cost deterministic key) reuse the prior answer with NO
    model call and NO cost."""
    stored = registry.get(app_id)
    if not stored:
        raise HTTPException(status_code=404, detail="app not found")
    app_def = AppDef(**stored)
    principal = principal_from_headers({k.lower(): v for k, v in request.headers.items()})
    inputs = (body or {}).get("inputs", {})
    inp = next((n for n in app_def.nodes if n.type.startswith("input")), None)
    question = (inputs.get(inp.id) if inp else "") or (inp.config.get("value") if inp else "") or ""

    key = cache.cheap_key(question)
    hit = cache.answer_get(key) if key else None
    if hit:  # no model call → $0
        p = hit["payload"]
        return {"appId": app_id, "name": app_def.name, "results": p["results"], "answer": p["answer"],
                "cached": "semantic", "saved": hit["cost"], "run_cost": 0.0}

    c0 = cache.stats()["cost_usd"]
    results, answer = await _run_def(app_def, inputs, principal)
    run_cost = cache.stats()["cost_usd"] - c0
    if key:
        cache.answer_put(key, {"app": stored, "results": results, "answer": answer}, run_cost)
    return {"appId": app_id, "name": app_def.name, "results": results, "answer": answer,
            "cached": False, "run_cost": run_cost}


# ---------------------------------------------------------------- route (reuse-first)
class _SvcCtx:
    def __init__(self):
        self.settings = settings


def _caps(a: dict) -> str:
    types = [n.get("type", "") for n in a.get("nodes", [])]
    grp = lambda p: [t.split(".")[-1] for t in types if t.startswith(p)]  # noqa: E731
    parts = []
    if grp("model"):
        parts.append("agents: " + ", ".join(grp("model")))
    if grp("source"):
        parts.append("sources: " + ", ".join(grp("source")))
    if grp("output"):
        parts.append("outputs: " + ", ".join(grp("output")))
    return "; ".join(parts)


async def _match_agent(prompt: str) -> dict:
    """LLM router: match a question to the best registered agent, or NONE."""
    apps = registry.list()
    if not apps:
        return {"match": False, "reason": "no agents registered yet"}
    listing = "\n".join(
        f'- id="{a["id"]}" name="{a.get("name","")}" capability="{a.get("description") or _caps(a)}"'
        for a in apps)
    ask = (f'User question: "{prompt}"\n\nExisting agents:\n{listing}\n\n'
           "Pick the single agent that can already answer this by just receiving the question as input. "
           "Only match if it genuinely fits the same data/capability; otherwise NONE.\n"
           'Reply ONLY JSON: {"id":"<agent id or NONE>","reason":"<short>","confidence":0.0-1.0}')
    try:
        out = await llm.complete(ask, "You route questions to the best existing agent. Output only JSON.", {}, _SvcCtx())
    except Exception as exc:
        return {"match": False, "reason": f"router error: {exc}"[:160]}
    if not out:
        return {"match": False, "reason": "router unavailable (no model provider)"}
    dec = {"id": "NONE", "reason": "", "confidence": 0}
    try:
        dec = json.loads(re.search(r"\{.*\}", out, re.S).group(0))
    except Exception:
        pass
    try:
        conf = float(dec.get("confidence", 0) or 0)
    except (TypeError, ValueError):
        conf = 0.0
    ids = {a["id"] for a in apps}
    if dec.get("id") in ids and conf >= 0.5:
        a = next(x for x in apps if x["id"] == dec["id"])
        return {"match": True, "appId": a["id"], "name": a.get("name"),
                "reason": dec.get("reason", ""), "confidence": conf}
    return {"match": False, "reason": dec.get("reason") or "no existing agent fits"}


@app.post("/api/route")
async def route_question(body: dict):
    """Match a question to an existing registered agent (or signal 'build new')."""
    prompt = (body or {}).get("prompt", "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt is required")
    return await _match_agent(prompt)


@app.post("/api/ask")
async def ask(body: dict, request: Request):
    """Consumer entry — FREE cache-first, then route (+ run). A cache hit renders the
    stored answer with ZERO model calls (so a repeated/reworded question costs $0).

    With body {"run": false} this returns the routed agent WITHOUT executing it, so
    the client can stream the run over /ws/run (right for long multi-agent
    orchestrations that would otherwise block one HTTP request) and then POST the
    result to /api/cache/answer. Default run=true keeps the blocking behavior."""
    prompt = (body or {}).get("prompt", "").strip()
    do_run = (body or {}).get("run", True)
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt is required")
    principal = principal_from_headers({k.lower(): v for k, v in request.headers.items()})

    key = cache.cheap_key(prompt)
    hit = cache.answer_get(key) if key else None
    if hit:  # zero model calls
        p = hit["payload"]
        return {"match": True, "cached": "semantic", "saved": hit["cost"], "run_cost": 0.0,
                "app": p["app"], "results": p["results"], "answer": p["answer"]}

    routed = await _match_agent(prompt)
    if not routed.get("match"):
        return {"match": False, "reason": routed.get("reason", "")}
    stored = registry.get(routed["appId"])
    if not do_run:  # defer execution to the streaming client
        return {"match": True, "cached": False, "deferred": True,
                "appId": routed["appId"], "app": stored}

    app_def = AppDef(**stored)
    inp = next((n for n in app_def.nodes if n.type.startswith("input")), None)
    inputs = {inp.id: prompt} if inp else {}
    c0 = cache.stats()["cost_usd"]
    results, answer = await _run_def(app_def, inputs, principal)
    run_cost = cache.stats()["cost_usd"] - c0
    cache.answer_put(key, {"app": stored, "results": results, "answer": answer}, run_cost)
    return {"match": True, "cached": False, "run_cost": run_cost,
            "app": stored, "results": results, "answer": answer}


@app.post("/api/cache/answer")
async def cache_answer(body: dict):
    """Store a client-streamed run in the free answer cache, so the next identical/
    reworded question is a $0 cache hit. Used by chat after a /ws/run stream."""
    prompt = (body or {}).get("prompt", "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt is required")
    key = cache.cheap_key(prompt)
    if not key:
        return {"ok": False}
    cache.answer_put(key, {"app": body.get("app"), "results": body.get("results") or {},
                           "answer": body.get("answer") or ""},
                     float(body.get("cost") or 0.0))
    return {"ok": True}


# ---------------------------------------------------------------- generate
@app.post("/api/generate")
async def generate(body: dict):
    prompt = (body or {}).get("prompt", "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt is required")
    return await generate_app(prompt, settings)


# ---------------------------------------------------------------- run (stream)
@app.websocket("/ws/run")
async def ws_run(ws: WebSocket):
    await ws.accept()
    principal = principal_from_headers({k.lower(): v for k, v in ws.headers.items()})
    try:
        payload = await ws.receive_json()
        app_def = AppDef(**payload["app"])
        inputs = payload.get("inputs", {})

        async def emit(frame: dict):
            await ws.send_json(frame)

        await execute(app_def, inputs, settings, principal, emit, audit)
    except WebSocketDisconnect:
        return
    except Exception as exc:  # surface errors to the UI
        try:
            await ws.send_json({"event": "error", "error": str(exc)})
        except Exception:
            pass
    finally:
        try:
            await ws.close()
        except Exception:
            pass


# ---------------------------------------------------------------- frontend
_FRONTEND = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "frontend")
)
if os.path.isdir(_FRONTEND):
    # Mounted last so /api and /ws routes take precedence. html=True serves
    # index.html at "/".
    app.mount("/", StaticFiles(directory=_FRONTEND, html=True), name="frontend")
