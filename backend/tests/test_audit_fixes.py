"""Regressions for the builder audit fixes:
- model.* connectors honor demo_mode (no live/billed calls when DEMO_MODE=true)
- @{ref} resolution tolerates case/spacing (hand-typed/imported refs)
- source.trino parameterizes @{ref} values (no SQL string interpolation)
"""
import asyncio

from plexus.auth import Principal
from plexus.config import get_settings
from plexus.connectors import classify, detection, prompt_agent, rag, trino
from plexus.executor import RunContext, execute
from plexus.models import AppDef

settings = get_settings()
PRIN = Principal(username="tester")


def _await(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _ctx(value="hello"):
    app = AppDef(name="x", nodes=[{"id": "i", "type": "input.text", "config": {"value": value}}], edges=[])
    ctx = RunContext(app, {}, settings, PRIN)
    ctx.results["i"] = {"kind": "text", "value": value}
    return ctx


async def _noop(_f):
    return None


def test_demo_mode_short_circuits_model_connectors():
    """With DEMO_MODE on, agent connectors return canned output and never call a
    live provider (provider tag == 'demo'), even if a CLI/key happens to be present."""
    assert settings.demo_mode is True
    for run, cfg in [
        (prompt_agent.run_prompt, {"goal": "@{i}"}),
        (classify.run_classify, {"input": "@{i}"}),
        (detection.run_detection, {"behavior": "@{i}", "target": "Sigma"}),
        (rag.run_rag, {"goal": "ransomware playbook"}),
    ]:
        out = _await(run(cfg, _ctx(), _noop))
        assert out["provider"] == "demo", f"{run.__name__} made a live call in demo mode"


def test_ref_resolution_is_case_and_space_insensitive():
    app = AppDef(
        name="x",
        nodes=[
            {"id": "q", "type": "input.text", "label": "User Question", "config": {"value": "hi there"}},
            {"id": "o", "type": "output.text", "config": {"template": "@{User Question}"}},
        ],
        edges=[{"id": "e", "source": "q", "target": "o"}],
    )
    results = _await(execute(app, {}, settings, PRIN, _noop))
    assert results["o"]["value"] == "hi there"  # @{User Question} -> slug 'user_question'


def test_trino_parameterizes_refs_no_injection():
    """@{ref} values become bound params (?), never interpolated into the SQL string."""
    ctx = _ctx(value="P1' OR '1'='1")  # an injection attempt as the upstream value
    sql, params = trino._parameterize("SELECT * FROM incidents WHERE severity = @{i}", ctx)
    assert sql == "SELECT * FROM incidents WHERE severity = ?"
    assert params == ["P1' OR '1'='1"]  # carried as data, not concatenated into SQL
