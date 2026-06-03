"""End-to-end execution in demo mode (no server, no credentials)."""
import asyncio

from plexus.auth import Principal
from plexus.config import Settings
from plexus.executor import execute
from plexus.models import AppDef


def _run(app: AppDef, inputs=None) -> list[dict]:
    frames: list[dict] = []

    async def emit(frame):
        frames.append(frame)

    asyncio.run(execute(app, inputs or {}, Settings(), Principal("tester"), emit))
    return frames


def test_demo_flow_streams_and_resolves_refs():
    app = AppDef(
        name="flow",
        nodes=[
            {"id": "q", "type": "input.text", "label": "Question", "config": {"value": "P1 phishing"}},
            {"id": "t", "type": "source.trino", "label": "Trino", "config": {"sql": "select *", "maxRows": 5}},
            {"id": "b", "type": "model.bedrock", "label": "Bedrock", "config": {"prompt": "Q:@{question}\n@{trino}"}},
            {"id": "o", "type": "output.text", "label": "Out", "config": {"template": "@{bedrock}"}},
        ],
        edges=[
            {"id": "e1", "source": "q", "target": "b"},
            {"id": "e2", "source": "t", "target": "b"},
            {"id": "e3", "source": "b", "target": "o"},
        ],
    )
    frames = _run(app)

    assert frames[0]["event"] == "run_start"
    assert frames[-1]["event"] == "run_complete"

    done = {f["nodeId"]: f for f in frames if f.get("event") == "node" and f["status"] == "done"}
    assert set(done) == {"q", "t", "b", "o"}
    assert done["t"]["output"]["kind"] == "rows"
    assert len(done["t"]["output"]["rows"]) == 5

    # the model node streamed partial frames
    partials = [f for f in frames if f.get("nodeId") == "b" and f["status"] == "running" and f.get("output")]
    assert partials, "expected streamed partials from the model node"

    # the output node resolved the model's answer
    assert "Risk summary" in done["o"]["output"]["value"]


def test_demo_agent_decides_to_call_tools():
    app = AppDef(
        name="agent",
        nodes=[
            {"id": "q", "type": "input.text", "label": "Question", "config": {"value": "investigate phishing"}},
            {"id": "a", "type": "model.agent", "label": "Agent",
             "config": {"goal": "@{question}", "tools": ["trino", "neo4j"]}},
        ],
        edges=[{"id": "e1", "source": "q", "target": "a"}],
    )
    frames = _run(app)
    done = next(f for f in frames if f.get("nodeId") == "a" and f["status"] == "done")
    out = done["output"]
    assert out["kind"] == "agent"
    tool_calls = [s for s in out["steps"] if s["type"] == "tool_call"]
    assert len(tool_calls) >= 2  # queried the warehouse and the graph
    assert out["value"], "agent produced a final answer"
