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
