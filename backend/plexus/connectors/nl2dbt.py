"""NL → dbt model generator.

Turns a plain-English request into dbt-as-code: a model `.sql` (with config + refs)
and the matching `schema.yml` tests. Optionally grounds on the real warehouse schema
(via the same backend the NL→SQL agent uses) so column/table names are correct.

Demo mode returns a realistic security-mart model + tests (no model call).
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from . import llm

Emit = Callable[[dict], Awaitable[None]]

_DEMO = """```sql
-- models/security/fct_credential_stuffing.sql
{{ config(materialized='view', tags=['security']) }}

with attempts as (
    select user_id, event_type, src_ip, geo, event_ts
    from {{ ref('stg_auth_events') }}
),
failed as (
    select user_id, count(*) as fails, min(event_ts) as first_fail, max(event_ts) as last_fail
    from attempts where event_type = 'failed_login'
    group by user_id having count(*) >= 5
),
success_new_geo as (
    select a.user_id, a.geo, a.src_ip, a.event_ts
    from attempts a
    where a.event_type = 'successful_login'
      and a.geo not in (select geo from {{ ref('dim_user_known_geo') }} k where k.user_id = a.user_id)
)
select s.user_id, f.fails, s.geo as new_geo, s.src_ip, s.event_ts as success_ts
from success_new_geo s
join failed f on f.user_id = s.user_id and s.event_ts between f.first_fail and f.last_fail + interval '1' hour
```

```yaml
# models/security/schema.yml
version: 2
models:
  - name: fct_credential_stuffing
    description: "Users with >=5 failed logins then a success from a new geo (credential stuffing)."
    columns:
      - name: user_id
        tests: [not_null]
      - name: new_geo
        tests: [not_null]
    tests:
      - dbt_utils.expression_is_true:
          expression: "fails >= 5"
```

**Next:** `dbt run --select fct_credential_stuffing && dbt test --select fct_credential_stuffing`, \
then schedule a sweep in Kestra.
"""


async def run_nl2dbt(config: dict, ctx, emit: Emit) -> dict[str, Any]:
    goal = ctx.resolve(config.get("goal") or config.get("prompt") or "", for_prompt=True)
    model_name = config.get("modelName") or "new_model"
    materialized = config.get("materialized") or "view"
    provider, model_id = llm.provider_name(config, ctx), llm.model_name(config, ctx)
    steps: list[dict] = [{"type": "think", "text": f"Designing dbt model `{model_name}` ({materialized}) from the request…"}]

    async def push(value="", **extra):
        await emit({"output": {"kind": "agent", "steps": list(steps), "value": value,
                               "provider": provider, "model": model_id, **extra}})

    await push()

    # ground on the real schema when a warehouse source is configured
    schema = ""
    if (config.get("source") == "trino") or config.get("connectionId"):
        try:
            from .sqlbackends import get_backend
            b = get_backend(config, ctx.settings, ctx.principal)
            schema = b.schema_text()
            b.close()
            steps.append({"type": "think", "text": "Grounded on the warehouse schema (information_schema)."})
            await push()
        except Exception:
            pass

    if ctx.settings.demo_mode or llm.use_demo(config, ctx):
        v = _DEMO
        await push(v)
        return {"kind": "agent", "steps": steps, "value": v, "provider": "demo", "model": "(demo)"}

    sys = ("You are a senior analytics engineer. Generate production dbt-as-code. Output a fenced "
           "```sql block (the model file: a {{ config(...) }} header + a SELECT using {{ ref() }} / "
           "{{ source() }}), then a fenced ```yaml block (the schema.yml entry with column tests). "
           "Use only real tables/columns from the schema when provided.")
    prompt = (f"Request: {goal}\n\nModel name: {model_name}\nMaterialization: {materialized}\n"
              + (f"\nWarehouse schema:\n{schema}\n" if schema else "")
              + "\nGenerate the dbt model + schema.yml.")
    try:
        v = await llm.complete(prompt, sys, config, ctx)
    except Exception as exc:
        v = f"⚠ model provider error: {exc}"
    steps.append({"type": "tool_result", "tool": "generate", "summary": f"dbt model `{model_name}` + tests"})
    await push(v)
    return {"kind": "agent", "steps": steps, "value": v or "⚠ no output", "provider": provider, "model": model_id}
