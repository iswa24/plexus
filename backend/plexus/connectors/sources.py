"""Additional source connectors beyond Trino/Neo4j.

Each returns {kind:"rows", columns, rows} and falls back to realistic sample data
in demo mode, so flows work with no external credentials. Real SDKs are imported
lazily.

  source.http     REST/JSON endpoint (e.g. threat-intel API)
  source.s3       object storage export (CSV/JSON)
  source.elastic  Elasticsearch / OpenSearch (SIEM logs)
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

Emit = Callable[[dict], Awaitable[None]]

_DEMO_HTTP = {
    "columns": ["indicator", "type", "score", "verdict"],
    "rows": [
        {"indicator": "185.23.41.9", "type": "ip", "score": 92, "verdict": "malicious"},
        {"indicator": "phish-kit-3f", "type": "hash", "score": 88, "verdict": "malicious"},
        {"indicator": "finance-login[.]co", "type": "domain", "score": 76, "verdict": "suspicious"},
    ],
}
_DEMO_S3 = {
    "columns": ["user", "src_ip", "action", "ts"],
    "rows": [
        {"user": "a.kumar", "src_ip": "185.23.41.9", "action": "vpn_login", "ts": "2026-06-03 02:10"},
        {"user": "s.lee", "src_ip": "10.2.4.7", "action": "vpn_login", "ts": "2026-06-03 01:50"},
        {"user": "a.kumar", "src_ip": "185.23.41.9", "action": "mfa_denied", "ts": "2026-06-03 02:12"},
    ],
}
_DEMO_ELASTIC = {
    "columns": ["@timestamp", "user", "event", "count"],
    "rows": [
        {"@timestamp": "2026-06-03T02:00", "user": "s.lee", "event": "failed_login", "count": 14},
        {"@timestamp": "2026-06-03T01:30", "user": "j.diaz", "event": "failed_login", "count": 6},
        {"@timestamp": "2026-06-03T00:45", "user": "a.kumar", "event": "impossible_travel", "count": 1},
    ],
}


async def run_http(config: dict, ctx, emit: Emit) -> dict[str, Any]:
    if ctx.settings.demo_mode:
        return {"kind": "rows", **_DEMO_HTTP}
    import json
    import urllib.request
    url = ctx.resolve(config.get("url", ""), for_prompt=False)
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    import asyncio
    data = await asyncio.to_thread(lambda: json.loads(urllib.request.urlopen(req, timeout=20).read().decode()))
    rows = data if isinstance(data, list) else data.get("results") or data.get("data") or [data]
    rows = [r if isinstance(r, dict) else {"value": r} for r in rows]
    cols = list(rows[0].keys()) if rows else []
    return {"kind": "rows", "columns": cols, "rows": rows[: int(config.get("maxRows", 200))]}


async def run_s3(config: dict, ctx, emit: Emit) -> dict[str, Any]:
    if ctx.settings.demo_mode:
        return {"kind": "rows", **_DEMO_S3}
    import asyncio
    import csv
    import io
    try:
        import boto3  # noqa: WPS433
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("boto3 not installed. `pip install boto3`.") from e

    def _read():
        s3 = boto3.client("s3", region_name=ctx.settings.aws_region)
        obj = s3.get_object(Bucket=config.get("bucket"), Key=config.get("key"))
        text = obj["Body"].read().decode()
        return list(csv.DictReader(io.StringIO(text)))

    rows = await asyncio.to_thread(_read)
    cols = list(rows[0].keys()) if rows else []
    return {"kind": "rows", "columns": cols, "rows": rows[: int(config.get("maxRows", 200))]}


async def run_elastic(config: dict, ctx, emit: Emit) -> dict[str, Any]:
    if ctx.settings.demo_mode:
        return {"kind": "rows", **_DEMO_ELASTIC}
    import asyncio
    try:
        from elasticsearch import Elasticsearch  # noqa: WPS433
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("elasticsearch not installed. `pip install elasticsearch`.") from e

    def _query():
        es = Elasticsearch(ctx.settings.elastic_url)
        body = config.get("query") or {"query": {"match_all": {}}, "size": int(config.get("maxRows", 50))}
        if isinstance(body, str):
            import json
            body = json.loads(body)
        res = es.search(index=config.get("index", "*"), body=body)
        return [h.get("_source", {}) for h in res.get("hits", {}).get("hits", [])]

    rows = await asyncio.to_thread(_query)
    cols = list(rows[0].keys()) if rows else []
    return {"kind": "rows", "columns": cols, "rows": rows}
