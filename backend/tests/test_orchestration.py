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


# ---------------------------------------------------------------- generator: reuse-first
def test_build_orchestration_reuses_agents_and_maps():
    """A decomposition plan that names existing agents must produce agent.call /
    flow.foreach nodes that reference them — not rebuilt model.* nodes."""
    from plexus.generator import _build_orchestration
    by_id = {"app_sql": {"name": "P1 by BU"}, "app_play": {"name": "IR Playbook"}}
    stages = [
        {"task": "look up open incidents by BU", "agentId": "app_sql", "foreach": False},
        {"task": "remediation per incident", "agentId": "app_play", "foreach": True},
    ]
    flow = _build_orchestration("Triage Flow", stages, "for each open incident...", by_id)
    types = [n["type"] for n in flow["nodes"]]
    assert "input.text" in types
    assert "agent.call" in types and "flow.foreach" in types
    assert "output.document" in types
    # the agent.call references the existing agent, not a freshly built model node
    call = next(n for n in flow["nodes"] if n["type"] == "agent.call")
    assert call["config"]["agentId"] == "app_sql"
    loop = next(n for n in flow["nodes"] if n["type"] == "flow.foreach")
    assert loop["config"]["agentId"] == "app_play"
    assert "model.nl2sql" not in types  # did NOT rebuild the data agent


def test_orchestrate_prefers_existing_agents_over_rebuilding(monkeypatch):
    """End-to-end of the reuse path with a mocked decomposition LLM."""
    import plexus.connectors.llm as llmmod
    from plexus import generator

    reg = Registry(settings.db_path)
    sql_id = reg.upsert(AppDef(name="P1 by BU", description="incidents by business unit",
                               nodes=[{"id": "i", "type": "input.text", "config": {}}], edges=[]))["id"]
    play_id = reg.upsert(AppDef(name="IR Playbook", description="remediation steps",
                                nodes=[{"id": "i", "type": "input.text", "config": {}}], edges=[]))["id"]

    plan = ('{"name":"Incident Triage","stages":['
            f'{{"task":"incidents by BU","agentId":"{sql_id}","foreach":false}},'
            f'{{"task":"remediation","agentId":"{play_id}","foreach":true}}]}}')

    async def fake_complete(prompt, system, config, ctx):
        return plan

    monkeypatch.setattr(llmmod, "complete", fake_complete)
    flow = _await(generator._orchestrate("for each open incident draft remediation", settings))
    assert flow is not None
    ids = [n["config"].get("agentId") for n in flow["nodes"] if n["type"] in ("agent.call", "flow.foreach")]
    assert sql_id in ids and play_id in ids   # both existing agents reused


def test_orchestrate_returns_none_when_no_agent_fits(monkeypatch):
    import plexus.connectors.llm as llmmod
    from plexus import generator

    async def fake_complete(prompt, system, config, ctx):
        return '{"name":"x","stages":[{"task":"do thing","agentId":"","foreach":false}]}'

    monkeypatch.setattr(llmmod, "complete", fake_complete)
    # no reuse → let the single-agent generator handle it
    assert _await(generator._orchestrate("totally novel request", settings)) is None


# ---------------------------------------------------------------- error policy (wave 3)
def _err_flow(onError=None, maxTries=None, err_edge=False):
    cfg = {"agentId": "does_not_exist", "input": "@{q}"}
    if onError: cfg["onError"] = onError
    if maxTries: cfg["maxTries"] = maxTries
    nodes = [
        {"id": "q", "type": "input.text", "config": {"value": "x"}},
        {"id": "b", "type": "agent.call", "config": cfg},          # bad id -> always errors
        {"id": "o", "type": "output.text", "config": {"template": "@{b}"}},
    ]
    edges = [{"id": "e1", "source": "q", "target": "b"},
             {"id": "e2", "source": "b", "target": "o"}]
    if err_edge:
        nodes.append({"id": "h", "type": "output.text", "config": {"template": "handled"}})
        edges.append({"id": "e3", "source": "b", "target": "h", "label": "error"})
    return AppDef(name="err", nodes=nodes, edges=edges)


def test_onerror_stop_prunes_downstream():
    results, frames = _run(_err_flow(onError="stop"))
    assert _statuses(frames)["o"] == "skipped"      # downstream halted on error


def test_onerror_continue_runs_downstream():
    results, frames = _run(_err_flow(onError="continue"))
    assert _statuses(frames)["o"] == "done"          # downstream still runs


def test_onerror_route_uses_error_edge():
    results, frames = _run(_err_flow(onError="route", err_edge=True))
    st = _statuses(frames)
    assert st["h"] == "done"        # error-output branch runs
    assert st["o"] == "skipped"     # normal branch pruned
    assert results["h"]["value"] == "handled"


def test_retry_on_fail_attempts_then_errors():
    results, frames = _run(_err_flow(maxTries=3))
    retries = [f for f in frames if f.get("nodeId") == "b" and f.get("status") == "running"
               and isinstance(f.get("output"), dict) and "retry" in str(f["output"].get("value", ""))]
    assert len(retries) == 2                          # 2 retries before the 3rd (final) attempt
    assert any(f.get("nodeId") == "b" and f.get("status") == "error" for f in frames)
