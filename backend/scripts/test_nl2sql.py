"""Live NL2SQL test against the seeded security databases.

Prereqs:
  1) pip install anthropic           (or: pip install -r requirements.txt)
  2) python scripts/seed_security.py
  3) put PLEXUS_ANTHROPIC_API_KEY=sk-ant-... in backend/.env  (or export it)

Run (optionally pass a model to compare):
  python scripts/test_nl2sql.py
  python scripts/test_nl2sql.py claude-3-5-haiku-latest
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from plexus.auth import Principal
from plexus.config import get_settings
from plexus.connectors.nl2sql import run_nl2sql

QUESTIONS = [
    "How many open P1 incidents are there, and which business units own the affected assets?",
    "Which critical assets have open incidents, who owns them, and do those owners have MFA enabled?",
    "What are the top alert signals seen on finance-owned assets?",
]


async def main():
    settings = get_settings()
    model = sys.argv[1] if len(sys.argv) > 1 else settings.anthropic_model
    if not settings.anthropic_api_key:
        print("⚠ PLEXUS_ANTHROPIC_API_KEY not set — will run the demo fallback (real SQL, canned answer).")
    print(f"model: {model}\n" + "=" * 70)

    for q in QUESTIONS:
        ctx = _ctx(settings)
        print(f"\nQ: {q}")
        out = await run_nl2sql({"goal": q, "modelId": model, "maxSteps": 6}, ctx, _noop)
        for s in out["steps"]:
            if s["type"] == "tool_call":
                print("  🔧 SQL:", " ".join(s["input"].split()))
            elif s["type"] == "tool_result":
                print("  ↳", s["summary"])
        print("  ➤", out["value"])
        print(f"  (tokens: {out.get('tokens')})")


def _ctx(settings):
    # minimal RunContext; nl2sql only uses ctx.settings + ctx.resolve
    class _C:
        def __init__(self, s):
            self.settings = s
            self.principal = Principal("tester")
        def resolve(self, text, for_prompt=False):
            return text
    return _C(settings)


async def _noop(_):
    return None


if __name__ == "__main__":
    asyncio.run(main())
