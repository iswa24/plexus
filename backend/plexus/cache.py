"""Response cache + usage/cost meter for model calls.

Caching keyed by (provider, model, system, prompt) so an identical model call is
never paid for twice. Also tracks tokens and cost (and the $ saved by cache hits).
In-process for now — swap the dict for Redis in production; the interface stays.
"""
from __future__ import annotations

import hashlib
import os
import re
import threading
import time
from difflib import SequenceMatcher
from typing import Awaitable, Callable

_LOCK = threading.Lock()
_CACHE: dict[str, dict] = {}          # exact model-call cache

STATS = {"hits": 0, "misses": 0, "input_tokens": 0, "output_tokens": 0,
         "cost_usd": 0.0, "saved_usd": 0.0, "sem_hits": 0, "sem_saved": 0.0}

# approx USD per 1M tokens (input, output) — adjust to your Bedrock/Anthropic/Azure rates
_PRICE = {"haiku": (0.80, 4.0), "sonnet": (3.0, 15.0), "opus": (15.0, 75.0),
          "gpt-4o-mini": (0.15, 0.60), "gpt-4o": (2.50, 10.0),
          "gpt-4": (30.0, 60.0), "gpt-35": (0.50, 1.50), "gpt-3.5": (0.50, 1.50)}


def _price(model: str):
    m = (model or "").lower()
    for k, v in _PRICE.items():
        if k in m:
            return v
    return _PRICE["sonnet"]


def cost_of(model: str, input_t: int, output_t: int) -> float:
    pin, pout = _price(model)
    return (input_t * pin + output_t * pout) / 1_000_000


def key(provider: str, model: str, system: str, prompt: str) -> str:
    raw = "|".join([provider or "", model or "", system or "", prompt or ""])
    return hashlib.sha256(raw.encode()).hexdigest()


def stats() -> dict:
    with _LOCK:
        s = dict(STATS)
    s["total_tokens"] = s["input_tokens"] + s["output_tokens"]
    s["calls"] = s["hits"] + s["misses"]
    return s


async def cached_call(provider: str, model: str, system: str, prompt: str,
                      call: Callable[[], Awaitable[tuple]]) -> str:
    """call() must return (text, input_tokens, output_tokens, cost_usd)."""
    k = key(provider, model, system, prompt)
    with _LOCK:
        hit = _CACHE.get(k)
    if hit is not None:
        with _LOCK:
            STATS["hits"] += 1
            STATS["saved_usd"] += hit["cost"]
        return hit["value"]
    text, it, ot, cost = await call()
    with _LOCK:
        _CACHE[k] = {"value": text, "cost": cost}
        STATS["misses"] += 1
        STATS["input_tokens"] += it
        STATS["output_tokens"] += ot
        STATS["cost_usd"] += cost
    return text


# ---- semantic answer cache (FREE lookup — no model call, no downloads) ----
# Deterministic, zero-cost matching that is fully PORTABLE: pure stdlib, no model,
# no network, no HuggingFace/torch downloads — copy the code to any machine and it
# runs. A question is reduced to a canonical token set in three steps:
#   1. phrase canonicalization  ("business unit"/"line of business" -> bu,
#                                 "priority 1"/"sev 1" -> p1)
#   2. per-token synonym folding ("division"/"dept"/"lob"/"org" -> bu,
#                                 "ticket"/"case" -> incident, "resolved" -> closed)
#   3. drop filler/quantifier words ("give","all","available",...), sort, dedup.
# Two questions match when EVERY content token pairs one-to-one (fuzzy per token,
# so typos/plurals still hit: "incidnets" ~ "incident"). Crucially, open-family and
# closed-family fold to DIFFERENT canonical tokens, so a filter flip (open vs
# closed, P1 vs P2) NEVER produces a false hit. A hit just renders the stored
# answer with NO model spend -> always $0.
# Optional TTL (PLEXUS_CACHE_TTL seconds; 0 = never expire) keeps data-backed
# answers fresh. Storage is in-process; swap _ANSWERS for Redis later (same API)
# if you want the cache shared across machines/restarts.
_ANSWERS: dict[str, dict] = {}          # cheap_key -> {payload, cost, tokens, exp}
_TOK_SIM = 0.80                          # per-token fuzzy threshold (typo-tolerant)
_DEFAULT_TTL = float(os.getenv("PLEXUS_CACHE_TTL", "0") or 0)  # seconds; 0 = no expiry

# Multi-word forms -> one canonical token (applied to the raw lowercased string).
_PHRASES = [
    (r"\bbusiness\s+units?\b", "bu"),
    (r"\blines?\s+of\s+business\b", "bu"),
    (r"\bincident\s+response\b", "ir"),
    (r"\bpriority\s*1\b", "p1"), (r"\bseverity\s*1\b", "p1"), (r"\bsev\s*1\b", "p1"),
    (r"\bpriority\s*2\b", "p2"), (r"\bseverity\s*2\b", "p2"), (r"\bsev\s*2\b", "p2"),
    (r"\bpriority\s*3\b", "p3"), (r"\bseverity\s*3\b", "p3"), (r"\bsev\s*3\b", "p3"),
]

# Single tokens -> canonical token. Families are kept SEPARATE on purpose: the
# open-set and closed-set must never merge, or a filter flip would mis-hit.
_SYN = {
    # business-unit grouping
    "bu": "bu", "businessunit": "bu", "division": "bu", "divisions": "bu",
    "department": "bu", "departments": "bu", "dept": "bu", "lob": "bu",
    "org": "bu", "orgs": "bu", "organization": "bu", "organisation": "bu",
    "unit": "bu", "units": "bu",
    # incident
    "incident": "incident", "incidents": "incident", "inc": "incident",
    "incidnet": "incident", "incidnets": "incident",  # common typos
    "ticket": "incident", "tickets": "incident", "case": "incident", "cases": "incident",
    # alert
    "alert": "alert", "alerts": "alert",
    # OPEN family (do NOT add closed-ish words here)
    "open": "open", "unresolved": "open", "outstanding": "open", "ongoing": "open",
    # CLOSED family (kept distinct from open)
    "closed": "closed", "resolved": "closed",
}

_STOP = {"how", "many", "much", "show", "all", "list", "are", "is", "there", "the",
         "a", "an", "of", "by", "for", "to", "in", "on", "what", "which", "give",
         "me", "please", "do", "we", "with", "and", "get", "find", "that", "this",
         "our", "any", "return", "display", "tell", "about", "per", "across",
         "from", "each", "available", "given", "various", "certain", "respective",
         "possible", "relevant", "every"}  # NB: keep filter words like 'open'/'closed'


def _tokens(question: str) -> list[str]:
    s = (question or "").lower()
    for pat, rep in _PHRASES:
        s = re.sub(pat, rep, s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    out: list[str] = []
    for t in s.split():
        t = _SYN.get(t, t)
        if t not in _STOP:
            out.append(t)
    return out


def cheap_key(question: str) -> str:
    return " ".join(sorted(set(_tokens(question))))


def _match(a: set, b: set) -> bool:
    """True when every token in each set pairs one-to-one (fuzzy) with the other."""
    if not a or not b or len(a) != len(b):
        return False
    used: set = set()
    for x in a:
        hit = None
        for y in b:
            if y in used:
                continue
            if x == y or SequenceMatcher(None, x, y).ratio() >= _TOK_SIM:
                hit = y
                break
        if hit is None:
            return False
        used.add(hit)
    return True


def _alive(entry: dict, now: float) -> bool:
    exp = entry.get("exp")
    return exp is None or exp > now


def answer_get(key: str):
    want = set(key.split())
    now = time.time()
    with _LOCK:
        e = _ANSWERS.get(key)                      # fast path: exact normalized key
        if e is not None and not _alive(e, now):
            _ANSWERS.pop(key, None)
            e = None
        if e is None:                              # fuzzy path: typo/synonym tolerant
            for k2, e2 in list(_ANSWERS.items()):
                if not _alive(e2, now):
                    _ANSWERS.pop(k2, None)
                    continue
                if _match(want, e2["tokens"]):
                    e = e2
                    break
        if e:
            STATS["sem_hits"] += 1
            STATS["sem_saved"] += e["cost"]
            return e
    return None


def answer_put(key: str, payload: dict, cost: float, ttl: float | None = None):
    if ttl is None:
        ttl = _DEFAULT_TTL
    exp = (time.time() + ttl) if ttl and ttl > 0 else None
    with _LOCK:
        _ANSWERS[key] = {"payload": payload, "cost": cost,
                         "tokens": set(key.split()), "exp": exp}
