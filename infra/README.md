# Local Trino + Postgres (test the NL→SQL Trino backend)

Stands up Trino federating a Postgres database seeded with the same security data
as the SQLite test, so you can exercise the NL→SQL agent's **Trino** backend
end-to-end.

## Run

```bash
# 1) generate the Postgres seed (once)
cd ../backend && python scripts/export_postgres_seed.py && cd ../infra

# 2) start the stack (needs Docker)
docker compose up -d
# wait ~30s for Trino to come up; check:  curl localhost:8080/v1/info

# 3) point Plexus at it — in backend/.env:
#    PLEXUS_TRINO_HOST=localhost
#    PLEXUS_TRINO_PORT=8080
#    PLEXUS_TRINO_SCHEME=http      # local Trino is http, no auth
```

## Use it

In the **🧮 NL→SQL Agent** card set:
- **Data source** = `trino`
- **Trino catalog** = `postgres`
- **Trino schema** = `public`

Then Run / Preview. The agent introspects `postgres.information_schema`, the model
writes **Trino SQL** (e.g. `postgres.public.incidents JOIN postgres.public.assets …`),
Plexus runs it via the Trino client, and the model answers from the rows.

## Notes
- Tables live in one Postgres schema (`public`): `incidents`, `alerts`, `assets`,
  `identities` — same rows as the SQLite seed.
- To add a second source (real federation across catalogs), drop another
  `*.properties` file in `trino/catalog/` (e.g. a MySQL catalog) and reference it
  by its filename.
- For production: enable TLS + auth on Trino, set `PLEXUS_TRINO_SCHEME=https`, and
  wire the OBO token exchange in `auth.py` so queries run as the app user.

```bash
docker compose down -v   # stop and wipe
```
