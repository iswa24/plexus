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


# Lineage-aware incident impact — a security finding fans out to the dbt model
# catalog + the affected model's lineage + the affected Trino records, then an AI
# analyst produces a blast-radius brief (downstream marts/owners + containment).
# Composite skill: Trino + dbt lineage + AI.
PACK.append(app(
    "Incident Impact · Lineage-Aware",
    "A security finding → affected Trino data → dbt lineage (downstream marts) → AI blast-radius brief.",
    [node("q", "input.text", "Security Finding", 0, 1,
          {"value": "Unauthorized access suspected on the incidents source (host finance-vpn-gw "
                    "compromised). Assess the blast radius across our data models, downstream marts, and owners."}),
     node("models", "dbt.list", "dbt · Models", 1, 0, {"maxRows": 200}),
     node("lin", "dbt.lineage", "dbt · Lineage (fct_open_p1)", 1, 1, {"model": "fct_open_p1"}),
     node("assets", "source.trino", "Affected Records (Trino)", 1, 2,
          {"connectionId": "conn_incidentdb",
           "sql": "SELECT id, severity, owner, asset FROM incidents WHERE status='open'"}),
     node("impact", "model.bedrock", "Impact Analyst", 2, 1,
          {"provider": "bedrock", "modelId": "auto",
           "system": "You are a data-platform incident analyst. Given a security finding, the dbt model "
                     "catalog, the lineage of the affected model, and the affected records, produce a "
                     "BLAST-RADIUS assessment: (1) directly affected data assets, (2) downstream dbt "
                     "models / marts / dashboards at risk via lineage, (3) likely impacted owners/teams, "
                     "(4) severity, (5) prioritized containment + verification steps. Be concise.",
           "prompt": "Security finding:\n@{q}\n\ndbt model catalog:\n@{models}\n\nLineage of the affected "
                     "model (fct_open_p1):\n@{lin}\n\nAffected records:\n@{assets}\n\nProduce the blast-radius assessment."}),
     node("doc", "output.document", "Incident Impact Brief", 3, 1,
          {"title": "Lineage-Aware Incident Impact", "template": "# @{title}\n\n@{impact}"})],
    [("q", "impact"), ("models", "impact"), ("lin", "impact"), ("assets", "impact"), ("impact", "doc")]))


# --- Data-Ops skill demos (one flow per new node, so they appear in My Apps) ---
PACK.append(app(
    "dbt · Test-Failure Triage",
    "Runs dbt tests; on failure an AI agent root-causes each and proposes a fix.",
    [node("t", "dbt.triage", "dbt · Test Triage", 0, 0, {"model": "fct_open_p1", "provider": "bedrock", "modelId": "auto"}),
     node("doc", "output.document", "Triage Report", 1, 0, {"title": "dbt Test Triage", "template": "# @{title}\n\n@{t}"})],
    [("t", "doc")]))

PACK.append(app(
    "Kestra · Execution Monitor",
    "Scans recent Kestra executions, flags failures, and raises an AI alert with recommended actions.",
    [node("m", "kestra.monitor", "Kestra · Monitor + Alert", 0, 0,
          {"namespace": "company.team", "limit": 25, "provider": "bedrock", "modelId": "auto"}),
     node("doc", "output.document", "Alert", 1, 0, {"title": "Kestra Health Alert", "template": "# @{title}\n\n@{m}"})],
    [("m", "doc")]))

PACK.append(app(
    "Trino · Cost Guard",
    "Estimates scan cost via EXPLAIN and blocks expensive queries unless approved.",
    [node("g", "source.trino_guard", "Trino (cost-guarded)", 0, 0,
          {"connectionId": "conn_cloudlogs", "sql": "SELECT * FROM events", "maxScanRows": 1000000, "maxRows": 500, "approved": []}),
     node("doc", "output.document", "Result", 1, 0, {"title": "Cost-Guarded Query", "template": "# @{title}\n\n@{g}"})],
    [("g", "doc")]))

# Self-healing pipeline — detect (dbt test) → diagnose (triage) → remediate (Kestra
# rerun, approval-gated) → report. Composes the dbt-triage + Kestra-trigger skills.
PACK.append(app(
    "Self-Healing Pipeline",
    "dbt test fails → AI triage diagnoses → proposes a gated Kestra rerun → report. Agentic ops, human-in-the-loop.",
    [node("q", "input.text", "Trigger", 0, 1,
          {"value": "Nightly dbt build failed quality checks — diagnose and remediate."}),
     node("triage", "dbt.triage", "Diagnose (dbt triage)", 1, 1,
          {"model": "fct_open_p1", "provider": "bedrock", "modelId": "auto"}),
     node("heal", "kestra.trigger", "Remediate (Kestra rerun)", 2, 1,
          {"namespace": "company.team", "flow": "daily_incident_brief", "inputs": "", "approved": []}),
     node("doc", "output.document", "Self-Healing Report", 3, 1,
          {"title": "Self-Healing Pipeline",
           "template": "# @{title}\n\n## Diagnosis\n@{triage}\n\n## Remediation (approval-gated)\n@{heal}"})],
    [("q", "triage"), ("triage", "heal"), ("triage", "doc"), ("heal", "doc")]))


# Detection-as-Data — Detection Rule Agent writes a rule → AI materializes it as a
# dbt model → a gated Kestra sweep schedules it. Closes the detection→data loop.
PACK.append(app(
    "Detection-as-Data",
    "Detection Rule Agent writes a rule → AI materializes it as a dbt model → gated Kestra sweep.",
    [node("q", "input.text", "Behavior to Detect", 0, 1,
          {"value": "Repeated failed logins followed by a successful login from a new geo for the "
                    "same user (credential stuffing)."}),
     node("rule", "model.detection", "Detection Rule Agent", 1, 0,
          {"provider": "bedrock", "modelId": "auto", "behavior": "@{q}", "target": "SQL"}),
     node("dbtmodel", "model.bedrock", "Materialize as dbt model", 2, 0,
          {"provider": "bedrock", "modelId": "auto",
           "system": "You are an analytics engineer. Turn the given detection SQL into a dbt model: "
                     "output (1) models/security/det_*.sql with {{ config(materialized='view') }} and "
                     "the SELECT, and (2) the schema.yml entry with a not_null test on the key column.",
           "prompt": "Detection rule:\n@{rule}\n\nProduce the dbt model file + schema.yml entry."}),
     node("sweep", "kestra.trigger", "Schedule Sweep (Kestra)", 2, 2,
          {"namespace": "company.team", "flow": "ioc_sweep", "inputs": "", "approved": []}),
     node("doc", "output.document", "Detection-as-Code Package", 3, 1,
          {"title": "Detection-as-Data",
           "template": "# @{title}\n\n## Detection rule\n@{rule}\n\n## dbt model (detection-as-code)\n"
                       "@{dbtmodel}\n\n## Scheduled sweep (approval-gated)\n@{sweep}"})],
    [("q", "rule"), ("rule", "dbtmodel"), ("rule", "sweep"), ("rule", "doc"),
     ("dbtmodel", "doc"), ("sweep", "doc")]))


# NL → dbt model generator (the dbt.generate node) — plain English to dbt-as-code.
PACK.append(app(
    "dbt · NL→Model Generator",
    "Plain English → a runnable dbt model SQL + schema.yml tests, grounded on the warehouse schema.",
    [node("q", "input.text", "Request", 0, 0,
          {"value": "Flag users with >=5 failed logins then a success from a new geo (credential stuffing)."}),
     node("g", "dbt.generate", "dbt · Generate Model (NL)", 1, 0,
          {"goal": "@{q}", "modelName": "fct_credential_stuffing", "materialized": "view",
           "source": "trino", "connectionId": "conn_cloudlogs", "provider": "bedrock", "modelId": "auto"}),
     node("doc", "output.document", "dbt Model", 2, 0,
          {"title": "Generated dbt Model", "template": "# @{title}\n\n@{g}"})],
    [("q", "g"), ("g", "doc")]))


# NL → Kestra flow generator (the kestra.generate node) — plain English to flow YAML.
PACK.append(app(
    "Kestra · NL→Flow Generator",
    "Plain English → a deployable Kestra flow (YAML) with tasks + schedule.",
    [node("q", "input.text", "Request", 0, 0,
          {"value": "Sweep new IOCs across the estate every 30 minutes, score them with a Plexus "
                    "agent, and alert SecOps on Slack."}),
     node("g", "kestra.generate", "Kestra · Generate Flow (NL)", 1, 0,
          {"goal": "@{q}", "flowId": "ioc_sweep", "namespace": "company.team", "provider": "bedrock", "modelId": "auto"}),
     node("doc", "output.document", "Kestra Flow", 2, 0,
          {"title": "Generated Kestra Flow", "template": "# @{title}\n\n@{g}"})],
    [("q", "g"), ("g", "doc")]))


# Compliance Evidence Collector — assembles an audit-ready pack from dbt lineage +
# Trino records + Kestra run history. Composite: Trino + dbt + Kestra + AI.
PACK.append(app(
    "Compliance Evidence Collector",
    "Audit-ready evidence pack: dbt lineage + Trino records + Kestra run history → AI compliance report.",
    [node("q", "input.text", "Audit Scope / Control", 0, 1,
          {"value": "Quarterly evidence pack for the incidents data pipeline: prove data lineage, "
                    "access controls, and orchestration run history are governed."}),
     node("models", "dbt.list", "dbt · Model Inventory", 1, 0, {"maxRows": 200}),
     node("lin", "dbt.lineage", "dbt · Lineage (fct_open_p1)", 1, 1, {"model": "fct_open_p1"}),
     node("access", "source.trino", "Records under Control (Trino)", 1, 2,
          {"connectionId": "conn_incidentdb",
           "sql": "SELECT id, severity, owner, asset FROM incidents WHERE status='open'"}),
     node("runs", "kestra.monitor", "Orchestration Run History", 1, 3,
          {"namespace": "company.team", "limit": 25, "provider": "bedrock", "modelId": "auto"}),
     node("audit", "model.bedrock", "Compliance Officer", 2, 1,
          {"provider": "bedrock", "modelId": "auto",
           "system": "You are a compliance officer assembling an audit-ready evidence pack. Given the "
                     "scope, dbt model inventory, data lineage, records under control, and orchestration "
                     "run history, produce: (1) controls in scope, (2) evidence gathered with what each "
                     "proves, (3) gaps/exceptions, (4) an attestation paragraph. Cite the artifacts.",
           "prompt": "Audit scope:\n@{q}\n\ndbt model inventory:\n@{models}\n\nData lineage "
                     "(fct_open_p1):\n@{lin}\n\nRecords under control:\n@{access}\n\nOrchestration run "
                     "history:\n@{runs}\n\nAssemble the evidence pack."}),
     node("doc", "output.document", "Compliance Evidence Pack", 3, 1,
          {"title": "Compliance Evidence Pack", "template": "# @{title}\n\n@{audit}"})],
    [("q", "audit"), ("models", "audit"), ("lin", "audit"), ("access", "audit"),
     ("runs", "audit"), ("audit", "doc")]))


# Auto-Landscape — inventory Trino sources + dbt models + Kestra flows, suggest gaps.
PACK.append(app(
    "Map Data Landscape",
    "Inventories Trino sources + dbt models + Kestra flows and suggests what to build next.",
    [node("m", "landscape.map", "Map Data Landscape", 0, 0,
          {"namespace": "company.team", "provider": "bedrock", "modelId": "auto"}),
     node("doc", "output.document", "Landscape Brief", 1, 0,
          {"title": "Data Landscape", "template": "# @{title}\n\n@{m}"})],
    [("m", "doc")]))


if __name__ == "__main__":
    for a in PACK:
        print("registered:", post(a))
