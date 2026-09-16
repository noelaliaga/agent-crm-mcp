-- Roles that Supabase already provides and a plain Postgres (CI) does not.
-- CI-only credentials: this database is created and destroyed with the CI job.
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'authenticator') then
    create role authenticator login password 'ci-only-authenticator' noinherit;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'anon') then
    create role anon nologin;
  end if;
end
$$;

grant anon to authenticator;
