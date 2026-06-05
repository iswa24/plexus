# Plexus × Kestra × DBT × Iceberg — the synergy loop

This is the "Act 2" of the summit demo: Plexus (the reasoning layer) wired into
the data platform you already run. Integration is **bidirectional**, and MCP +
REST are the contracts.

```
            ┌──────────────── Plexus (agent builder) ────────────────┐
  Kestra ──▶│  REST /api/apps/{id}/run   (reuse-first router)         │──▶ Azure OpenAI
  (HTTP)    │  orchestration · cost cache · OBO · approval gates       │    Bedrock
            └───▲────────────────────────────┬───────────────────────-┘
                │ calls back via MCP          │ queries via Trino (OBO)
        dbt / kestra MCP servers              ▼
        (dbt_run, dbt_test,           DBT ─▶ Iceberg lakehouse ◀─ Trino
         trigger_flow, ...)         (transform+test)  (versioned truth)
```

## Direction 1 — Platform drives Plexus (this flow)
`daily_incident_brief.yml`: Kestra (6am) → `dbt run`+`dbt test` (refresh Iceberg)
→ quality gate → HTTP `POST /api/apps/{id}/run` to Plexus (X-User = OBO identity)
→ write the brief back to `iceberg.security.exec_briefs` → Slack.

Deploy it: `kestra flow update security daily_incident_brief.yml` (or paste in the
UI). Set `plexus_url`, `agent_id`, and the `SLACK_SECOPS_WEBHOOK` secret.

## Direction 2 — Plexus drives the platform (MCP)
Inside Plexus, an agent can call the platform as **tools** via the MCP servers in
`backend/mcp_servers/`:
- **dbt** — `list_models`, `model_lineage`, `dbt_test`, `dbt_run` *(write, gated)*
- **kestra** — `list_flows`, `flow_status`, `trigger_flow` *(write, gated)*

Register them in `backend/.env` (`PLEXUS_MCP_SERVERS`, see
`backend/mcp_servers/README.md`). Write tools are approval-gated: an autonomous
agent proposes `dbt_run` / `trigger_flow`; a human approves before they execute.

## Why both
- **Kestra** = deterministic, scheduled, governed data DAGs.
- **Plexus** = agentic reasoning (branch on content, compose sub-agents, NL tasks,
  human-in-loop) — and a callable service.
They nest: Kestra orchestrates the pipeline; Plexus orchestrates the reasoning in a
step and can call back. Same identity, audit, and review across both.
