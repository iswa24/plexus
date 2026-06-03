"""Tests for RAG retrieval and the approval-gated action node."""
import asyncio

from plexus.auth import Principal
from plexus.config import get_settings
from plexus.connectors import ragstore
from plexus.connectors.action import run_action

_settings = get_settings()


class _Ctx:
    def __init__(self):
        self.settings = _settings
        self.principal = Principal("tester")

    def resolve(self, text, for_prompt=False):
        return text


async def _noop(_):
    return None


def test_rag_retrieval_is_relevant():
    hits = ragstore.search("ransomware containment procedure", 3)
    ids = [h["id"] for h in hits]
    assert any(i.startswith("ransomware-playbook") for i in ids)
    # an unrelated query retrieves different docs
    mfa = [h["id"] for h in ragstore.search("MFA on critical assets", 3)]
    assert any(i.startswith("access-policy") for i in mfa)


def test_action_proposes_not_executes_by_default():
    out = asyncio.run(run_action(
        {"action": "block_ip", "payload": "{\"ip\":\"185.23.41.9\"}", "approved": []}, _Ctx(), _noop))
    assert out["kind"] == "agent"
    assert "PROPOSED" in out["value"] and "NOT executed" in out["value"]
    # never executes in demo mode even if 'approved'
    out2 = asyncio.run(run_action(
        {"action": "block_ip", "payload": "x", "approved": ["approved"]}, _Ctx(), _noop))
    assert "PROPOSED" in out2["value"]  # demo_mode guard still proposes
