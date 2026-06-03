"""Lightweight in-process retriever for RAG (BM25-lite over chunked docs).

Stdlib only — no embeddings/vector DB — so RAG works out of the box. Swap
`search()` for a real vector store (pgvector / OpenSearch-knn / FAISS) in prod;
the RAG agent doesn't change.
"""
from __future__ import annotations

import math
import re

from .kb_data import DOCS

_CHUNKS: list[dict] = []


def _tok(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (s or "").lower())


def _build():
    if _CHUNKS:
        return
    for title, text in DOCS.items():
        for i, para in enumerate(p.strip() for p in text.split("\n\n") if p.strip()):
            _CHUNKS.append({"id": f"{title}#{i}", "title": title, "text": para, "terms": _tok(para)})


def search(query: str, k: int = 4) -> list[dict]:
    _build()
    q = set(_tok(query))
    if not q:
        return []
    n = len(_CHUNKS)
    df: dict[str, int] = {}
    for c in _CHUNKS:
        for t in set(c["terms"]):
            df[t] = df.get(t, 0) + 1
    scored = []
    for c in _CHUNKS:
        tf: dict[str, int] = {}
        for t in c["terms"]:
            tf[t] = tf.get(t, 0) + 1
        score = 0.0
        for t in q:
            if t in tf:
                idf = math.log(1 + n / (1 + df.get(t, 0)))
                score += idf * (tf[t] / (tf[t] + 1.5))
        if score > 0:
            scored.append((score, c))
    scored.sort(key=lambda x: -x[0])
    return [c for _, c in scored[:k]]
