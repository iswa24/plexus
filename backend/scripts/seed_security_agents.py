"""Register a pack of advanced security agents into the running Plexus registry.

Run (server must be up):  python scripts/seed_security_agents.py
"""
from __future__ import annotations

import json
import urllib.request

API = "http://127.0.0.1:8000/api/apps"


def post(app):
    req = urllib.request.Request(API, data=json.dumps(app).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req) as r:
        return json.load(r)["name"]


def node(nid, typ, label, stage, row, cfg):
    return {"id": nid, "type": typ, "label": label,
            "position": {"x": 60 + stage * 330, "y": 90 + row * 150}, "config": cfg}


def app(name, desc, nodes, edges):
    return {"name": name, "description": desc, "canvas": "graph", "nodes": nodes,
            "edges": [{"id": f"e{i}", "source": s, "target": t} for i, (s, t) in enumerate(edges)]}


PACK = []

# 1) MITRE ATT&CK Mapper
PACK.append(app(
    "MITRE ATT&CK Mapper",
    "Maps described attacker behavior or TTPs to MITRE ATT&CK tactics and techniques.",
    [node("q", "input.text", "Behavior", 0, 0, {"value": "credential dumping via LSASS, then lateral movement over SMB"}),
     node("m", "model.bedrock", "Mapper", 1, 0, {"provider": "claudecode", "modelId": "auto",
          "system": "You map described behavior to MITRE ATT&CK. Output a markdown table: Tactic | Technique (ID) | Why.",
          "goal": "@{q}"}),
     node("o", "output.text", "Answer", 2, 0, {"template": "@{m}"})],
    [("q", "m"), ("m", "o")]))

# 2) IR Playbook Generator
PACK.append(app(
    "IR Playbook Generator",
    "Given an incident description, produces an incident-response playbook (containment, eradication, recovery, notifications).",
    [node("q", "input.text", "Incident", 0, 0, {"value": "ransomware indicator on an HR database server"}),
     node("m", "model.bedrock", "IC", 1, 0, {"provider": "claudecode", "modelId": "auto",
          "system": "You are an incident commander. Produce a concise IR playbook with sections: Containment, Eradication, Recovery, Notifications.",
          "goal": "@{q}"}),
     node("o", "output.text", "Playbook", 2, 0, {"template": "@{m}"})],
    [("q", "m"), ("m", "o")]))

# 3) Phishing Analyzer
PACK.append(app(
    "Phishing Analyzer",
    "Analyzes a suspected phishing email and gives a verdict, indicators, and recommended actions.",
    [node("q", "input.text", "Email", 0, 0, {"value": "From IT <it@secure-reset.co> asking to reset VPN creds via a link"}),
     node("m", "model.bedrock", "Analyst", 1, 0, {"provider": "claudecode", "modelId": "auto",
          "system": "You analyze suspected phishing. Give a verdict (malicious/suspicious/benign), the indicators, and recommended actions.",
          "goal": "@{q}"}),
     node("o", "output.text", "Verdict", 2, 0, {"template": "@{m}"})],
    [("q", "m"), ("m", "o")]))

# 4) Policy & Runbook Q&A (RAG)
PACK.append(app(
    "Policy & Runbook Q&A",
    "Answers questions about security policies, IR runbooks, and procedures from the knowledge base, with citations.",
    [node("q", "input.text", "Question", 0, 0, {"value": "What is our ransomware containment procedure?"}),
     node("m", "model.rag", "Knowledge", 1, 0, {"provider": "claudecode", "modelId": "auto", "goal": "@{q}", "topK": 4}),
     node("o", "output.text", "Answer", 2, 0, {"template": "@{m}"})],
    [("q", "m"), ("m", "o")]))

# 5) Multi-agent pipeline: Alert -> Triage + Detection + IR Playbook -> combined report
PACK.append(app(
    "Alert Response Pipeline",
    "End-to-end: classifies an alert, drafts a detection rule and an IR playbook, and proposes a containment action (approval-gated).",
    [node("a", "input.text", "Alert", 0, 0, {"value": "EDR: credential_dumping on finance-vpn-gw, owner a.kumar"}),
     node("tri", "model.classify", "Triage", 1, 0, {"provider": "claudecode", "modelId": "auto", "input": "@{a}"}),
     node("det", "model.detection", "Detection", 1, 1, {"provider": "claudecode", "modelId": "auto", "behavior": "@{a}", "target": "Sigma"}),
     node("pb", "model.bedrock", "Playbook", 1, 2, {"provider": "claudecode", "modelId": "auto",
          "system": "You are an incident commander. Give a short containment + eradication checklist.", "goal": "@{a}"}),
     node("act", "action.webhook", "Contain", 2, 1, {"action": "block_ip", "payload": "{\"reason\": \"@{a}\"}", "approved": []}),
     node("rep", "output.document", "Report", 3, 0, {"title": "Alert Response",
          "template": "# Alert Response\n\n## Triage\n@{tri}\n\n## Detection rule\n@{det}\n\n## Playbook\n@{pb}\n\n## Proposed action\n@{act}"})],
    [("a", "tri"), ("a", "det"), ("a", "pb"), ("a", "act"),
     ("tri", "rep"), ("det", "rep"), ("pb", "rep"), ("act", "rep")]))


# Multi-cluster investigation — each remote Trino client is queried by its OWN
# looping NL→SQL agent (question -> SQL -> run -> refine on error -> answer); a
# SOC-lead AI Agent then correlates. Showcases agentic NL→SQL + the multi-client
# connection picker. (NOT static SQL: the agent writes and self-corrects the query.)
def _nl(nid, label, conn, row, goal):
    return node(nid, "model.nl2sql", label, 1, row,
                {"source": "trino", "connectionId": conn, "provider": "bedrock",
                 "modelId": "auto", "goal": goal, "maxSteps": 6})


PACK.append(app(
    "SOC · Multi-Cluster Investigation",
    "Each Trino cluster is queried by its own looping NL→SQL agent, then a SOC-lead agent correlates.",
    [node("q", "input.text", "Question", 0, 1,
          {"value": "Investigate recent credential phishing across our environment"}),
     _nl("intel", "Threat Intel Agent", "conn_threatintel", 0,
         "@{q} — find the highest-confidence indicators and the campaign behind them"),
     _nl("inc", "Incident DB Agent", "conn_incidentdb", 1,
         "@{q} — list the open P1 incidents and the assets/owners involved"),
     _nl("logs", "Cloud Logs Agent", "conn_cloudlogs", 2,
         "@{q} — summarize the auth event signals that suggest phishing or credential stuffing"),
     node("ai", "model.bedrock", "SOC Lead (synthesis)", 2, 1,
          {"provider": "bedrock", "modelId": "auto",
           "system": "You are a SOC lead. Correlate the three agent findings into one risk summary + prioritized next steps.",
           "prompt": "Question: @{q}\n\nThreat Intel agent:\n@{intel}\n\nIncident DB agent:\n@{inc}\n\nCloud Logs agent:\n@{logs}\n\nCorrelate and recommend."}),
     node("doc", "output.document", "Investigation Brief", 3, 1,
          {"title": "Multi-Cluster Investigation", "template": "# @{title}\n\n@{ai}"})],
    [("q", "intel"), ("q", "inc"), ("q", "logs"), ("intel", "ai"), ("inc", "ai"), ("logs", "ai"), ("ai", "doc")]))


if __name__ == "__main__":
    for a in PACK:
        print("registered:", post(a))
