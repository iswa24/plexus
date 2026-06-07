"""Named connections registry + Trino connection-picker."""
from fastapi.testclient import TestClient
from plexus.main import app

client = TestClient(app)


def test_demo_seeds_three_trino_clients():
    conns = client.get("/api/connections").json()
    labels = [c["label"] for c in conns]
    assert any("Threat Intel" in l for l in labels)
    assert sum(c["kind"] == "trino" for c in conns) >= 3
    assert all("password" not in c and "jwtSecret" not in c for c in conns)  # secrets stripped


def test_connection_crud_and_test():
    created = client.post("/api/connections", json={
        "label": "Test Cluster", "kind": "trino", "host": "demo.trino.local",
        "port": 8443, "catalog": "c", "schema": "s", "authType": "obo"}).json()
    cid = created["id"]
    assert client.get(f"/api/connections/{cid}").json()["label"] == "Test Cluster"
    # test endpoint returns synthetic OK for *.local / demo
    t = client.post(f"/api/connections/{cid}/test").json()
    assert t["ok"] is True and "demo" in t["version"].lower()
    client.put(f"/api/connections/{cid}", json={"label": "Renamed"})
    assert client.get(f"/api/connections/{cid}").json()["label"] == "Renamed"
    client.delete(f"/api/connections/{cid}")
    assert client.get(f"/api/connections/{cid}").status_code == 404


def test_trino_node_tags_selected_connection():
    """A source.trino node with a connectionId tags its output with the cluster label."""
    import asyncio
    from plexus.executor import execute
    from plexus.models import AppDef
    from plexus.config import get_settings
    from plexus.auth import Principal
    conns = client.get("/api/connections").json()
    cid = next(c["id"] for c in conns if "Threat Intel" in c["label"])
    app_def = AppDef(name="t", nodes=[
        {"id": "q", "type": "input.text", "config": {"value": "x"}},
        {"id": "t", "type": "source.trino", "config": {"connectionId": cid, "sql": "SELECT 1"}},
    ], edges=[{"id": "e", "source": "q", "target": "t"}])

    async def _noop(f): pass
    loop = asyncio.new_event_loop()
    results = loop.run_until_complete(execute(app_def, {}, get_settings(), Principal(username="u"), _noop)); loop.close()
    assert results["t"]["kind"] == "rows"
    assert "Threat Intel" in results["t"].get("connection", "")
