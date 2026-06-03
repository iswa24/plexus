"""Unit tests for the DAG executor: ordering, ref resolution, rendering."""
from plexus.auth import Principal
from plexus.config import Settings
from plexus.executor import RunContext, render_value, topo_sort
from plexus.models import AppDef


def _sample_app() -> AppDef:
    return AppDef(
        name="t",
        nodes=[
            {"id": "q", "type": "input.text", "label": "Question", "config": {"value": "hello"}},
            {"id": "t", "type": "source.trino", "label": "Trino", "config": {"sql": "select 1"}},
            {"id": "b", "type": "model.bedrock", "label": "Bedrock",
             "config": {"prompt": "Q:@{question} rows:@{trino}"}},
            {"id": "o", "type": "output.text", "label": "Out", "config": {"template": "@{bedrock}"}},
        ],
        edges=[
            {"id": "e1", "source": "q", "target": "b"},
            {"id": "e2", "source": "t", "target": "b"},
            {"id": "e3", "source": "b", "target": "o"},
        ],
    )


def test_topo_sort_orders_dependencies_first():
    order = topo_sort(_sample_app())
    assert set(order) == {"q", "t", "b", "o"}
    assert order.index("b") > order.index("q")
    assert order.index("b") > order.index("t")
    assert order.index("o") > order.index("b")


def test_topo_sort_does_not_hang_on_cycle():
    app = AppDef(
        name="c",
        nodes=[{"id": "a", "type": "output.text"}, {"id": "b", "type": "output.text"}],
        edges=[{"id": "1", "source": "a", "target": "b"},
               {"id": "2", "source": "b", "target": "a"}],
    )
    order = topo_sort(app)
    assert set(order) == {"a", "b"}  # all present, terminated


def test_render_value_rows_as_markdown_table():
    out = {"kind": "rows", "columns": ["id", "sev"],
           "rows": [{"id": "INC-1", "sev": "P1"}, {"id": "INC-2", "sev": "P2"}]}
    s = render_value(out, for_prompt=True)
    assert "id | sev" in s
    assert "INC-1 | P1" in s


def test_ref_resolution_by_label_slug_and_id():
    app = _sample_app()
    ctx = RunContext(app, {}, Settings(), Principal("u"))
    ctx.results["q"] = {"kind": "text", "value": "hello"}
    ctx.results["t"] = {"kind": "rows", "columns": ["id"], "rows": [{"id": "INC-1"}]}

    # @{question} resolves via the slugified label; @{trino} too
    txt = ctx.resolve("ask:@{question} data:@{trino}", for_prompt=True)
    assert "ask:hello" in txt
    assert "INC-1" in txt

    # by raw node id as well
    assert "hello" in ctx.resolve("@{q}", for_prompt=False)

    # unknown reference is preserved, not crashed
    assert ctx.resolve("@{nope}", for_prompt=False) == "[nope]"
