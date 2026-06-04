"""A second REAL MCP server (stdio): network/geo enrichment. Distinct from
secintel so multi-server routing is visibly pulling from two different sources.

Run standalone:  python mcp_servers/geoip_server.py
Registered in Plexus via PLEXUS_MCP_SERVERS alongside secintel.
"""
from __future__ import annotations

import ipaddress
import json

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("geoip")

# A small local geo/ASN table (real lookup logic, no external API needed).
_GEO = {
    "185.23.41.9": {"country": "RU", "city": "Moscow", "asn": "AS49505", "org": "Selectel", "hosting": True},
    "45.137.21.8": {"country": "NL", "city": "Amsterdam", "asn": "AS209854", "org": "BlueVPS", "hosting": True},
    "10.2.4.7": {"country": "—", "city": "—", "asn": "private", "org": "internal", "hosting": False},
}


@mcp.tool()
def geolocate_ip(ip: str) -> str:
    """Return geo + ASN/org for an IP, plus whether it's hosting/datacenter
    infrastructure (a useful risk signal). Returns JSON."""
    ip = (ip or "").strip()
    rec = dict(_GEO.get(ip, {"country": "??", "city": "unknown", "asn": "unknown",
                             "org": "unknown", "hosting": None}))
    try:
        rec["private"] = ipaddress.ip_address(ip).is_private
    except ValueError:
        rec["private"] = None
    rec["ip"] = ip
    return json.dumps([rec])


@mcp.tool()
def whois_domain(domain: str) -> str:
    """Return registration details for a domain (registrar, age, country). JSON."""
    table = {
        "finance-login[.]co": {"registrar": "NameSilo", "created": "2026-05-28",
                               "age_days": 7, "country": "PA", "privacy": True},
    }
    rec = table.get((domain or "").strip().lower(),
                    {"registrar": "unknown", "created": "?", "age_days": None, "country": "??"})
    rec["domain"] = domain
    return json.dumps([rec])


if __name__ == "__main__":
    mcp.run()  # stdio transport
