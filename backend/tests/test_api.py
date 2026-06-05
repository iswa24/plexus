"""API tests via Starlette TestClient: REST registry + run WebSocket."""
from fastapi.testclient import TestClient

from plexus.main import app

client = TestClient(app)


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["demoMode"] is True


def test_app_registry_crud():
    payload = {
        "name": "CRUD App",
        "nodes": [{"id": "i", "type": "input.text", "label": "Q", "config": {"value": "x"}}],
        "edges": [],
    }
    r = client.post("/api/apps", json=payload)
    assert r.status_code == 200
    app_id = r.json()["id"]
    assert app_id and r.json()["updatedAt"]

    assert client.get(f"/api/apps/{app_id}").json()["name"] == "CRUD App"
    assert any(a["id"] == app_id for a in client.get("/api/apps").json())

    assert client.delete(f"/api/apps/{app_id}").json()["ok"] is True
    assert client.get(f"/api/apps/{app_id}").status_code == 404


def test_ws_run_streams_and_resolves():
    payload = {
        "app": {
            "name": "WS",
            "nodes": [
                {"id": "q", "type": "input.text", "label": "Question", "config": {"value": "hi there"}},
                {"id": "o", "type": "output.text", "label": "Out", "config": {"template": "@{question}"}},
            ],
            "edges": [{"id": "e", "source": "q", "target": "o"}],
        },
        "inputs": {},
    }
    events = []
    with client.websocket_connect("/ws/run") as ws:
        ws.send_json(payload)
        while True:
            f = ws.receive_json()
            events.append(f)
            if f.get("event") == "run_complete":
                break

    done = {f["nodeId"]: f for f in events if f.get("event") == "node" and f["status"] == "done"}
    assert done["o"]["output"]["value"] == "hi there"


def test_generate_returns_runnable_app():
    r = client.post("/api/generate", json={
        "prompt": "Summarize recent incidents from the warehouse and list related "
                  "entities from the graph"})
    assert r.status_code == 200
    app_def = r.json()
    types = {n["type"] for n in app_def["nodes"]}
    assert any(t.startswith("input") for t in types)
    assert any(t.startswith("output") for t in types)
    assert "source.trino" in types and "source.neo4j" in types
    # every node has a position assigned by the layout pass
    assert all("position" in n for n in app_def["nodes"])
    # edges only reference existing nodes
    ids = {n["id"] for n in app_def["nodes"]}
    assert all(e["source"] in ids and e["target"] in ids for e in app_def["edges"])


def test_generate_picks_agent_for_autonomous_phrasing():
    r = client.post("/api/generate", json={"prompt": "an agent that investigates phishing autonomously"})
    assert r.status_code == 200
    types = {n["type"] for n in r.json()["nodes"]}
    assert "model.agent" in types


def test_generate_requires_prompt():
    assert client.post("/api/generate", json={"prompt": "  "}).status_code == 400


def test_cache_answer_roundtrip_makes_next_ask_free():
    """A client-streamed run stored via /api/cache/answer must make the next
    (reworded) /api/ask a $0 cache hit — no router or model call."""
    app = {"name": "X", "nodes": [{"id": "o", "type": "output.text",
           "config": {"template": "hi"}}], "edges": []}
    r = client.post("/api/cache/answer", json={
        "prompt": "how many open P1 incidents by business unit",
        "app": app, "results": {"o": {"kind": "text", "value": "42"}},
        "answer": "42", "cost": 0.01})
    assert r.json()["ok"] is True
    # reworded ask, run deferred — still a free semantic hit before any routing
    r2 = client.post("/api/ask", json={
        "prompt": "show open P1 incidents per BU", "run": False}).json()
    assert r2["cached"] == "semantic"
    assert r2["run_cost"] == 0.0
    assert r2["answer"] == "42"


def test_runs_history_groups_executions():
    """Running an app records an execution visible via /api/runs + /api/runs/{id}."""
    app = {"name": "Runs Demo", "nodes": [
        {"id": "q", "type": "input.text", "config": {"value": "hi"}},
        {"id": "o", "type": "output.text", "config": {"template": "@{q}"}},
    ], "edges": [{"id": "e", "source": "q", "target": "o"}]}
    aid = client.post("/api/apps", json=app).json()["id"]
    client.post(f"/api/apps/{aid}/run", json={"inputs": {}})
    runs = client.get(f"/api/runs?app_id={aid}").json()
    assert runs and runs[0]["nodes"] >= 1 and runs[0]["status"] in ("success", "error")
    detail = client.get(f"/api/runs/{runs[0]['run_id']}").json()
    assert any(r["node_type"] == "output.text" for r in detail)
