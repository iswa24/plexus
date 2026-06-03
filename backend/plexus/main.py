"""FastAPI app: app registry REST + run WebSocket + static frontend."""
from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .audit import AuditLog
from .auth import principal_from_headers
from .config import get_settings
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


# ---------------------------------------------------------------- run a SAVED app (reuse)
@app.post("/api/apps/{app_id}/run")
async def run_saved_app(app_id: str, body: dict, request: Request):
    """Run a saved agent again and again, by id, with inputs. Returns each node's
    output. This turns a built+validated agent into a callable service (script it,
    schedule it, embed it) — the same agent, no rebuild."""
    stored = registry.get(app_id)
    if not stored:
        raise HTTPException(status_code=404, detail="app not found")
    app_def = AppDef(**stored)
    principal = principal_from_headers({k.lower(): v for k, v in request.headers.items()})
    results: dict = {}

    async def emit(frame: dict):
        if frame.get("event") == "node" and frame.get("status") == "done":
            results[frame["nodeId"]] = frame.get("output")

    await execute(app_def, (body or {}).get("inputs", {}), settings, principal, emit, audit)
    # convenience: pick the "answer" — prefer output.text, then model nodes, never inputs
    types = {n.id: n.type for n in app_def.nodes}

    def _pick() -> str:
        for nid, o in results.items():
            if types.get(nid) == "output.text" and isinstance(o, dict) and o.get("value"):
                return o["value"]
        for nid, o in results.items():
            if types.get(nid, "").startswith("model") and isinstance(o, dict) and o.get("value"):
                return o["value"]
        return ""

    return {"appId": app_id, "name": app_def.name, "results": results, "answer": _pick()}


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
