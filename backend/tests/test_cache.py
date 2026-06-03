"""Free semantic cache guarantee: a repeated/reworded/typo'd question must be
served from cache with ZERO model calls and $0 cost (the consumer's promise:
"cache should only render the response, not use a model and cost")."""
import plexus.main as main
from fastapi.testclient import TestClient

from plexus import cache
from plexus.main import app, registry

client = TestClient(app)

_APP = {
    "name": "Incident Summarizer",
    "description": "Summarizes security incidents by business unit",
    "nodes": [
        {"id": "q", "type": "input.text", "config": {}},
        {"id": "m", "type": "model.prompt", "config": {"prompt": "Answer: @{q}"}},
        {"id": "o", "type": "output.text", "config": {"value": "@{m}"}},
    ],
    "edges": [
        {"id": "e1", "source": "q", "target": "m"},
        {"id": "e2", "source": "m", "target": "o"},
    ],
}


def _wire(monkeypatch):
    """Register an agent + stub router/run so no real model is ever called."""
    app_id = client.post("/api/apps", json=_APP).json()["id"]

    async def fake_match(prompt):
        return {"match": True, "appId": app_id, "name": "Incident Summarizer",
                "reason": "fits", "confidence": 0.9}

    async def fake_run(app_def, inputs, principal):
        cache.STATS["cost_usd"] += 0.0123  # pretend a paid model ran
        out = {"o": {"kind": "text", "value": "42 open P1 incidents across 3 BUs."}}
        return out, "42 open P1 incidents across 3 BUs."

    monkeypatch.setattr(main, "_match_agent", fake_match)
    monkeypatch.setattr(main, "_run_def", fake_run)
    return app_id


def _ask(q):
    return client.post("/api/ask", json={"prompt": q}).json()


def test_cheap_key_is_word_order_and_abbrev_invariant():
    assert cache.cheap_key("how many open P1 incidents by business unit") == \
        cache.cheap_key("list the open P1 incidents per business unit")
    # canonicalization: BU / business unit / division all fold to one token
    assert "bu" in cache.cheap_key("incidents by BU").split()
    assert cache.cheap_key("open incidents by BU") == \
        cache.cheap_key("open incidents by division")


def test_synonyms_and_filler_words_canonicalize_to_same_key():
    base = cache.cheap_key("how many open P1 incidents by business unit")
    # filler ("give me all available"), abbrev (BU), plural drop (incident)
    assert cache.cheap_key("give me all open P1 incident by all available BU") == base
    # synonyms: division == business unit, ticket == incident
    assert cache.cheap_key("show all open P1 tickets per division") == base


def test_open_closed_and_severity_never_collide():
    assert cache.cheap_key("open P1 incidents by BU") != \
        cache.cheap_key("closed P1 incidents by BU")
    assert cache.cheap_key("open P1 incidents by BU") != \
        cache.cheap_key("open P2 incidents by BU")


def test_reworded_and_typo_question_is_free_cache_hit(monkeypatch):
    cache.STATS["cost_usd"] = 0.0
    cache._ANSWERS.clear()
    _wire(monkeypatch)

    first = _ask("how many open P1 incidents by business unit")
    assert first["cached"] is False and first["run_cost"] > 0

    spent_before = cache.stats()["cost_usd"]
    # reworded + typo ("incidnets") + abbreviation (BU) — must hit cache for free
    again = _ask("show all open P1 incidnets by BU")
    assert again["cached"] == "semantic"
    assert again["run_cost"] == 0.0 and again["saved"] > 0
    # the decisive check: NO additional model spend on the hit
    assert cache.stats()["cost_usd"] == spent_before


def test_meaningfully_different_filter_is_not_a_hit(monkeypatch):
    cache.STATS["cost_usd"] = 0.0
    cache._ANSWERS.clear()
    _wire(monkeypatch)

    _ask("show all open P1 incidents by BU")
    closed = _ask("show all closed P1 incidents by BU")  # open != closed
    assert closed["cached"] is False  # must re-run, not reuse the open answer
