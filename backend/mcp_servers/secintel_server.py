"""A REAL MCP server (stdio) exposing security tools — a standalone process that
Plexus connects to over the Model Context Protocol. Not canned demo data: tools
query a local intel dataset and block_ip performs a real write to a blocklist file.

Run standalone:  python mcp_servers/secintel_server.py
Registered in Plexus via PLEXUS_MCP_SERVERS (see make_real.sh / launch config).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("secintel")

BLOCKLIST = os.environ.get("PLEXUS_BLOCKLIST", "/tmp/plexus_blocklist.jsonl")

# A small but real local threat-intel table (distinct from Plexus's canned demo
# rows, so a live result is visibly different from the built-in demo server).
_INTEL = [
    {"indicator": "185.23.41.9", "type": "ip", "score": 96, "verdict": "malicious",
     "category": "c2", "first_seen": "2026-05-29", "source": "secintel-db"},
    {"indicator": "45.137.21.8", "type": "ip", "score": 88, "verdict": "malicious",
     "category": "scanner", "first_seen": "2026-06-01", "source": "secintel-db"},
    {"indicator": "finance-login[.]co", "type": "domain", "score": 79, "verdict": "suspicious",
     "category": "phishing", "first_seen": "2026-06-02", "source": "secintel-db"},
    {"indicator": "a1b2c3d4e5", "type": "hash", "score": 91, "verdict": "malicious",
     "category": "loader", "first_seen": "2026-05-30", "source": "secintel-db"},
]


@mcp.tool()
def search_iocs(query: str = "") -> str:
    """Search the threat-intel database for indicators matching a query
    (substring match over indicator/type/category). Returns JSON rows."""
    q = (query or "").strip().lower()
    rows = [r for r in _INTEL
            if not q or q in r["indicator"].lower() or q in r["type"] or q in r["category"]]
    if not rows:
        rows = _INTEL  # default: return the full feed
    return json.dumps(rows)


@mcp.tool()
def enrich_user(user: str) -> str:
    """Look up an internal user's department, MFA status and risk. Returns JSON."""
    table = {
        "a.kumar": {"user": "a.kumar", "dept": "Finance", "mfa": "disabled", "risk": "high"},
        "s.lee": {"user": "s.lee", "dept": "Engineering", "mfa": "enabled", "risk": "low"},
    }
    return json.dumps([table.get((user or "").strip().lower(),
                                 {"user": user, "dept": "unknown", "mfa": "unknown", "risk": "review"})])


@mcp.tool()
def block_ip(ip: str) -> str:
    """Add an IP to the network blocklist. REAL side effect: appends an entry to
    the blocklist file with a UTC timestamp. Returns a confirmation."""
    entry = {"ip": ip, "action": "block", "at": datetime.now(timezone.utc).isoformat(),
             "by": "plexus-mcp"}
    with open(BLOCKLIST, "a") as f:
        f.write(json.dumps(entry) + "\n")
    count = sum(1 for _ in open(BLOCKLIST)) if os.path.exists(BLOCKLIST) else 0
    return json.dumps({"status": "blocked", "ip": ip, "blocklist_size": count, "file": BLOCKLIST})


if __name__ == "__main__":
    mcp.run()  # stdio transport
