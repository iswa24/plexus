"""Orchestration primitives: conditional branch, parallel fan-out + join,
sub-agent delegation (agent.call), and map (flow.foreach). Author-controlled —
the flow is explicit in the AppDef and the executor honors it deterministically."""
import asyncio

from plexus.auth import Principal
from plexus.config import get_settings
from plexus.executor import execute
from plexus.models import AppDef
from plexus.registry import Registry

settings = get_settings()
PRIN = Principal(username="tester")


def _await(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _run(app: AppDef, inputs=None):
    frames = []

    async def emit(f):
        frames.append(f)

    results = _await(execute(app, inputs or {}, settings, PRIN, emit))
    return results, frames


def _statuses(frames):
    """nodeId -> last status seen."""
    s = {}
    for f in frames:
        if f.get("event") == "node":
            s[f["nodeId"]] = f["status"]
    return s


# ---------------------------------------------------------------- branch
def test_branch_prunes_untaken_path():
    """severity == P1 → escalate runs, log is skipped."""
    app = AppDef(
        name="branch",
        nodes=[
            {"id": "q", "type": "input.text", "config": {"value": "P1"}},
            {"id": "b", "type": "flow.branch",
             "config": {"left": "@{q}", "op": "==", "right": "P1"}},
            {"id": "esc", "type": "output.text", "config": {"template": "ESCALATED"}},
            {"id": "log", "type": "output.text", "config": {"template": "logged"}},
        ],
        edges=[
            {"id": "e1", "source": "q", "target": "b"},
            {"id": "e2", "source": "b", "target": "esc", "label": "true"},
            {"id": "e3", "source": "b", "target": "log", "label": "false"},
        ],
    )
    results, frames = _run(app)
    st = _statuses(frames)
    assert results["b"]["outcome"] == "true"
    assert st["esc"] == "done"          # taken path ran
    assert st["log"] == "skipped"       # untaken path pruned
    assert results["esc"]["value"] == "ESCALATED"


def test_branch_false_path():
    app = AppDef(
        name="branch2",
        nodes=[
            {"id": "q", "type": "input.text", "config": {"value": "P3"}},
            {"id": "b", "type": "flow.branch",
             "config": {"left": "@{q}", "op": "==", "right": "P1"}},
            {"id": "esc", "type": "output.text", "config": {"template": "ESCALATED"}},
            {"id": "log", "type": "output.text", "config": {"template": "logged"}},
        ],
        edges=[
            {"id": "e1", "source": "q", "target": "b"},
            {"id": "e2", "source": "b", "target": "esc", "label": "true"},
            {"id": "e3", "source": "b", "target": "log", "label": "false"},
        ],
    )
    results, frames = _run(app)
    st = _statuses(frames)
    assert results["b"]["outcome"] == "false"
    assert st["log"] == "done"
    assert st["esc"] == "skipped"


# ---------------------------------------------------------------- parallel + join
def test_parallel_fanout_and_join():
    """Two independent branches run, a join node merges both via @refs."""
    app = AppDef(
        name="parallel",
        nodes=[
            {"id": "q", "type": "input.text", "config": {"value": "seed"}},
            {"id": "a", "type": "output.text", "config": {"template": "A:@{q}"}},
            {"id": "b", "type": "output.text", "config": {"template": "B:@{q}"}},
            {"id": "join", "type": "output.text", "config": {"template": "@{a} | @{b}"}},
        ],
        edges=[
            {"id": "e1", "source": "q", "target": "a"},
            {"id": "e2", "source": "q", "target": "b"},
            {"id": "e3", "source": "a", "target": "join"},
            {"id": "e4", "source": "b", "target": "join"},
        ],
    )
    results, _ = _run(app)
    assert results["join"]["value"] == "A:seed | B:seed"


# ---------------------------------------------------------------- agent.call
def _register_echo_agent() -> str:
    """A trivial registered agent: input → output that echoes it."""
    reg = Registry(settings.db_path)
    app = AppDef(
        name="Echo Agent",
        description="echoes its input",
        nodes=[
            {"id": "i", "type": "input.text", "config": {}},
            {"id": "o", "type": "output.text", "config": {"template": "echo:@{i}"}},
        ],
        edges=[{"id": "e", "source": "i", "target": "o"}],
    )
    return reg.upsert(app)["id"]


def test_agent_call_delegates_to_registered_agent():
    aid = _register_echo_agent()
    app = AppDef(
        name="supervisor",
        nodes=[
            {"id": "q", "type": "input.text", "config": {"value": "hello"}},
            {"id": "call", "type": "agent.call",
             "config": {"agentId": aid, "input": "@{q}"}},
            {"id": "o", "type": "output.text", "config": {"template": "@{call}"}},
        ],
        edges=[
            {"id": "e1", "source": "q", "target": "call"},
            {"id": "e2", "source": "call", "target": "o"},
        ],
    )
    results, _ = _run(app)
    assert results["call"]["value"] == "echo:hello"
    assert results["call"]["delegated"] == "Echo Agent"
    assert results["o"]["value"] == "echo:hello"


# ---------------------------------------------------------------- foreach (map)
def test_foreach_maps_agent_over_rows():
    aid = _register_echo_agent()
    app = AppDef(
        name="map",
        nodes=[
            {"id": "rows", "type": "output.json",
             "config": {}},  # placeholder; we inject rows via a model-less stub
            {"id": "loop", "type": "flow.foreach",
             "config": {"agentId": aid, "items": "@{rows}", "itemField": "name"}},
        ],
        edges=[{"id": "e1", "source": "rows", "target": "loop"}],
    )
    # seed the 'rows' node result directly by using an input that returns rows:
    # simplest path — replace 'rows' with an input and feed rows through ctx.
    # Instead, drive via a tiny custom run: register a rows-producing agent.
    # Here we just assert the foreach handles a rows input shape.
    from plexus.executor import RunContext, run_foreach

    ctx = RunContext(app, {}, settings, PRIN)
    ctx.results["rows"] = {"kind": "rows", "columns": ["name"],
                           "rows": [{"name": "alpha"}, {"name": "beta"}]}

    async def _noop(_f):
        return None

    out = _await(run_foreach(app.nodes[1].config, ctx, _noop))
    assert out["kind"] == "rows"
    assert len(out["rows"]) == 2
    assert out["rows"][0]["result"] == "echo:alpha"
    assert out["rows"][1]["result"] == "echo:beta"
