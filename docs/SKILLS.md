# Plexus — Skills Catalog (Trino · dbt · Kestra · AI)

A **skill** in Plexus is one of two things:
- a **node** — a reusable card in the left palette (one connector + executor dispatch +
  palette entry + test); or
- a **template** — a pre-wired flow (an App Definition) that composes nodes, shipped in
  the registry and seeded by `backend/scripts/seed_security_agents.py`.

Everything here runs in **demo mode** (synthetic data, no creds) and goes **live** when you
configure the backend per [`GO-LIVE.md`](GO-LIVE.md). Every write / expensive action is
**approval-gated**: it returns `PROPOSED` until a human ticks *Approve* and demo mode is off.

To add a new node type, see the 3-edit pattern in [`../AGENTS.md`](../AGENTS.md).

---

## Node skills (drag from the palette)

| Node (type) | Palette group | What it does | Key inputs | Live backend | Code |
|---|---|---|---|---|---|
| **NL→SQL Agent** (`model.nl2sql`) | AI Agents 🧮 | Question → writes SQL → runs it → **self-corrects on error** → answers | `goal`, `source` (sqlite/trino), `connectionId` | Trino / SQLite | `connectors/nl2sql.py` |
| **dbt · Test Triage** (`dbt.triage`) | Data Ops 🧪 | Runs `dbt test`; on failure, AI root-causes each + proposes a fix | `model`, provider | dbt (MCP) | `connectors/dbt_triage.py` |
| **Kestra · Monitor + Alert** (`kestra.monitor`) | Data Ops 🚨 | Scans recent executions, flags failures, raises an AI alert | `namespace`, `limit` | Kestra (MCP) | `connectors/kestra_monitor.py` |
| **Trino Query (cost-guarded)** (`source.trino_guard`) | Source 🛡 | `EXPLAIN` cost estimate **before** running; blocks runaway scans unless approved | `sql`, `connectionId`, `maxScanRows`, `approved` | Trino | `connectors/trino_guard.py` |
| **dbt · Generate Model (NL)** (`dbt.generate`) | Data Ops ✨ | Plain English → dbt model `.sql` + `schema.yml` tests, schema-grounded | `goal`, `modelName`, `materialized`, `source`/`connectionId` | Trino (schema) | `connectors/nl2dbt.py` |
| **Kestra · Generate Flow (NL)** (`kestra.generate`) | Data Ops ✨ | Plain English → deployable Kestra flow YAML (tasks + schedule) | `goal`, `flowId`, `namespace` | Kestra (context) | `connectors/nl2kestra.py` |
| **Map Data Landscape** (`landscape.map`) | Data Ops 🗺 | Inventories Trino connections + dbt models + Kestra flows → AI gap analysis | `namespace`, provider | Connections + dbt + Kestra | `connectors/landscape_map.py` |

Also already in the palette: **AI Agent** (`model.bedrock`), **Tool-Calling Agent**
(`model.agent`), **Classifier**, **Detection Rule**, **Graph (NL→Cypher)**, **RAG**;
sources (Trino, Neo4j, HTTP, S3, Elasticsearch, MCP Resource); the branded **dbt** /
**Kestra** read/write nodes; **Branch / Call Agent / For Each**; and the **MCP Tool (action)**.

---

## Template skills (open from ⋯ More → My Apps)

| Template | Composes | What it produces |
|---|---|---|
| **Incident Impact · Lineage-Aware** | Trino + `dbt.list` + `dbt.lineage` + AI | Blast-radius brief: affected data → downstream marts → owners → containment |
| **Self-Healing Pipeline** | `dbt.triage` + `kestra.trigger` (gated) | Diagnose a failed build → propose a gated rerun → report |
| **Detection-as-Data** | `model.detection` + AI + `kestra.trigger` (gated) | Detection rule → dbt model (as-code) → scheduled sweep |
| **Compliance Evidence Collector** | `dbt.list` + `dbt.lineage` + Trino + `kestra.monitor` + AI | Audit-ready evidence pack: lineage + access + run history + attestation |
| **SOC · Multi-Cluster Investigation** | 3× `model.nl2sql` (per cluster) + AI | Fan-out NL→SQL across Threat Intel / Incident DB / Cloud Logs → correlated brief |
| **NL→Model / NL→Flow generators** | `dbt.generate` / `kestra.generate` | One-node demos of the generators (+ Document output) |
| *(plus the security pack)* | prompt / classify / detection / RAG | MITRE Mapper, IR Playbook, Phishing Analyzer, Policy Q&A, Alert Response Pipeline |

Re-seed any fresh DB with: `cd backend && python scripts/seed_security_agents.py`
(server running). Connections (the Trino clients these reference) seed automatically in
demo mode with stable ids `conn_threatintel` / `conn_incidentdb` / `conn_cloudlogs`.

---

## How the pieces fit (the platform story)

- **Generate** — NL→SQL (`model.nl2sql`), NL→dbt (`dbt.generate`), NL→Kestra (`kestra.generate`)
- **Operate** — Test Triage, Execution Monitor, Self-Healing Pipeline
- **Govern** — Cost Guard, approval gates on every write, Compliance Evidence
- **Understand** — Lineage-Aware Incident Impact, Map Data Landscape

The three generators share one pattern (introspect/ground → generate → validate/refine);
the operate/govern skills share the MCP tool-call + approval-gate pattern. Templates are
just compositions — so the catalog grows by adding nodes, then wiring them.
