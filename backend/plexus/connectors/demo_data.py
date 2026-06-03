"""Sample data returned by connectors when PLEXUS_DEMO_MODE=true."""
from __future__ import annotations

SAMPLE_INCIDENTS = [
    {"id": "INC-4821", "severity": "P1", "owner": "a.kumar", "ts": "2026-06-03 02:14"},
    {"id": "INC-4822", "severity": "P1", "owner": "s.lee", "ts": "2026-06-03 01:55"},
    {"id": "INC-4830", "severity": "P1", "owner": "m.patel", "ts": "2026-06-03 00:48"},
    {"id": "INC-4841", "severity": "P1", "owner": "j.diaz", "ts": "2026-06-02 23:30"},
    {"id": "INC-4855", "severity": "P1", "owner": "a.kumar", "ts": "2026-06-02 22:05"},
]

SAMPLE_GRAPH = [
    {"entity": "finance-vpn-gw", "type": "Asset", "rel": "TARGETED"},
    {"entity": "phish-kit-3f", "type": "Indicator", "rel": "USED_IN"},
    {"entity": "a.kumar", "type": "User", "rel": "AFFECTED"},
    {"entity": "185.23.x.x", "type": "IP", "rel": "SOURCE_OF"},
]


def demo_bedrock_text(question: str, incident_count: int) -> str:
    q = question or "the request"
    n = incident_count or len(SAMPLE_INCIDENTS)
    return (
        f'Risk summary for "{q}":\n\n'
        f"I reviewed {n} P1 incidents from the warehouse and the related graph "
        "context. They form a coordinated **credential-phishing cluster** "
        "targeting finance users via the VPN gateway (indicator phish-kit-3f, "
        "source 185.23.x.x).\n\n"
        "Recommended next steps:\n"
        "1. Force password reset + reauth for affected users (a.kumar, s.lee, m.patel).\n"
        "2. Block source IP 185.23.x.x at the edge and sinkhole the phishing domain.\n"
        "3. Raise a P1 bridge; notify the finance BISO and SOC lead.\n"
        "4. Hunt for lateral movement from finance-vpn-gw over the last 24h."
    )
