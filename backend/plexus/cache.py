"""Response cache + usage/cost meter for model calls.

Caching keyed by (provider, model, system, prompt) so an identical model call is
never paid for twice. Also tracks tokens and cost (and the $ saved by cache hits).
In-process for now — swap the dict for Redis in production; the interface stays.
"""
from __future__ import annotations

import hashlib
import re
import threading
from difflib import SequenceMatcher
from typing import Awaitable, Callable

_LOCK = threading.Lock()
_CACHE: dict[str, dict] = {}          # exact model-call cache

STATS = {"hits": 0, "misses": 0, "input_tokens": 0, "output_tokens": 0,
         "cost_usd": 0.0, "saved_usd": 0.0, "sem_hits": 0, "sem_saved": 0.0}

# approx USD per 1M tokens (input, output) — adjust to your Bedrock/Anthropic rates
_PRICE = {"haiku": (0.80, 4.0), "sonnet": (3.0, 15.0), "opus": (15.0, 75.0)}


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


# ---- semantic answer cache (FREE lookup — no model call) ----
# Deterministic, zero-cost matching. A question becomes a normalized token set:
# lowercase, expand abbreviations, drop stopwords/quantifier words (word-order
# invariant). Two questions match when EVERY content token lines up one-to-one,
# allowing fuzzy equality per token so typos/plurals still hit ("incidnets"≈
# "incidents", "BU"→"business unit"). Meaningfully different words do NOT match
# (open vs closed, mfa vs vpn) so filters stay correct. A hit just renders the
# stored answer with NO model spend → always $0.
# (For sharper matching later, add an optional embedding pre-pass; this stays the
#  free fast path so a hit is always $0.)
_ANSWERS: dict[str, dict] = {}          # cheap_key -> {payload, cost, tokens}
_TOK_SIM = 0.80                          # per-token fuzzy threshold (typo-tolerant)

_ABBR = {"bu": "business unit", "mfa": "mfa", "ir": "incident response",
         "vpn": "vpn", "soc": "soc", "iam": "iam", "cve": "cve"}
_STOP = {"how", "many", "much", "show", "all", "list", "are", "is", "there", "the",
         "a", "an", "of", "by", "for", "to", "in", "on", "what", "which", "give",
         "me", "please", "do", "we", "with", "and", "get", "find", "that", "this",
         "our", "any", "return", "display", "tell", "about", "per", "across",
         "from", "each"}  # NB: keep filter words like 'open'/'closed'


def _tokens(question: str) -> list[str]:
    s = re.sub(r"[^a-z0-9 ]", " ", (question or "").lower())
    out: list[str] = []
    for t in s.split():
        out.extend(_ABBR.get(t, t).split())
    return [t for t in out if t not in _STOP]


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


def answer_get(key: str):
    want = set(key.split())
    with _LOCK:
        e = _ANSWERS.get(key)                      # fast path: exact normalized key
        if e is None:                              # fuzzy path: typo/plural tolerant
            for e2 in _ANSWERS.values():
                if _match(want, e2["tokens"]):
                    e = e2
                    break
        if e:
            STATS["sem_hits"] += 1
            STATS["sem_saved"] += e["cost"]
            return e
    return None


def answer_put(key: str, payload: dict, cost: float):
    with _LOCK:
        _ANSWERS[key] = {"payload": payload, "cost": cost, "tokens": set(key.split())}
