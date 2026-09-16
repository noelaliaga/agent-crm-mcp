# Running against a real Postgres

The unit tests and the demo use a fake PostgREST. The guarantees that only a database can give are checked by `tests/integration/`:
- column grants;
- no DELETE;
- the audit trigger with role and actor;
- the note author set by the database;
- audit rows about `valor_cents` hidden from the agent.

Those tests run in the CI `integration` job (`.github/workflows/ci.yml`).

> **Status:** these steps and the integration tests were written without access to a local Postgres, Docker or Supabase CLI. The first CI run is the first time they execute. If a step below fails, fix the step: do not assume it works.

## Option A: Supabase CLI (local stack)

```bash
supabase init            # once, if the project has no supabase/config.toml yet
supabase start
supabase db reset        # applies supabase/migrations/ and supabase/seed.sql
supabase status          # shows the API URL, anon key and JWT secret of the LOCAL stack

export PGRST_JWT_SECRET='<JWT secret from supabase status>'
export CRM_SUPABASE_URL='http://127.0.0.1:54321'
export CRM_API_KEY='<anon key from supabase status>'
export CRM_AGENT_TOKEN="$(python3 scripts/mint_agent_jwt.py --actor demo-agent)"
export CRM_WRITE_MODE=dry_run

python3 servers/crm/server.py --healthcheck
```

## Option B: plain Postgres + PostgREST (what CI does)

```bash
export CRM_IT_DATABASE_URL=postgresql://postgres:<password>@localhost:5432/postgres
psql "$CRM_IT_DATABASE_URL" -v ON_ERROR_STOP=1 -f tests/integration/bootstrap_plain_postgres.sql
psql "$CRM_IT_DATABASE_URL" -v ON_ERROR_STOP=1 -f supabase/migrations/0001_crm_min.sql
psql "$CRM_IT_DATABASE_URL" -v ON_ERROR_STOP=1 -f supabase/seed.sql

# PostgREST with PGRST_DB_ANON_ROLE=anon and a JWT secret of at least 32 characters
export CRM_IT_JWT_SECRET=<same secret as PostgREST>
export CRM_IT_POSTGREST_URL=http://localhost:3000
export CRM_IT_REQUIRED=1        # fail instead of skipping a module whose backend is missing
python -m pytest -m integration -v -rs
# The server itself needs CRM_REST_PATH=/ against plain PostgREST (no /rest/v1 prefix).
```

`bootstrap_plain_postgres.sql` creates the `authenticator` and `anon` roles, which Supabase already provides, using a password meant only for the throwaway CI database. Use your own password anywhere else.

## Checking the database guarantee by hand

```sql
begin;
set local role crm_agent;
select set_config('request.jwt.claims', '{"role":"crm_agent","actor":"demo-agent"}', true);
update public.prospectos set score = 1 where nombre = 'Asesoría Demo Norte';
-- expected: ERROR: permission denied for table prospectos
rollback;

select rol, actor, operacion, campo from public.prospecto_cambios order by id desc limit 10;
```
