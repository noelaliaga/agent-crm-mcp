-- agent-crm-mcp · minimal CRM schema with a restricted agent identity
--
-- Derived from the schema of a real single-user prospecting CRM, reduced to what the
-- MCP server needs. No data here: synthetic rows live in supabase/seed.sql.
--
-- What this migration guarantees, independently of the Python server:
--   * The agent connects as role `crm_agent` (via PostgREST, JWT claim role=crm_agent),
--     never as service_role / a superuser.
--   * `crm_agent` can UPDATE only the allowlisted columns of `prospectos`, INSERT only
--     (prospecto_id, cuerpo) into `prospecto_notas`, and cannot DELETE anything or
--     write the audit table.
--   * Every INSERT/UPDATE/DELETE on `prospectos` and every note is written to
--     `prospecto_cambios` by a trigger, with the effective database role, the session
--     user and the `actor` claim of the caller's JWT.
--
-- Works on plain Postgres >= 13 with PostgREST, and on Supabase (which already has the
-- `authenticator`, `anon` and `authenticated` roles). Idempotent.

begin;

-- ------------------------------------------------------------------------ agent role

do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'crm_agent') then
    create role crm_agent nologin noinherit;
  end if;
  -- PostgREST logs in as `authenticator` and switches to the role in the JWT.
  if exists (select 1 from pg_roles where rolname = 'authenticator') then
    grant crm_agent to authenticator;
  end if;
end
$$;

-- ------------------------------------------------------------------------------ tables

create table if not exists public.prospectos (
  id                    uuid primary key default gen_random_uuid(),
  nombre                text not null check (length(nombre) between 1 and 200),
  ciudad                text not null default '',
  telefono              text not null default '',
  email                 text not null default '',
  web                   text not null default '',
  score                 integer not null default 0 check (score between 0 and 100),
  tamano                text not null default '',
  hallazgos             jsonb not null default '[]'::jsonb check (jsonb_typeof(hallazgos) = 'array'),
  propuesta             text not null default '',
  canal                 text not null default '' check (canal in ('', 'email', 'telefono', 'web')),
  origen                text not null default 'manual',
  estado                text not null default 'nuevo'
                        check (estado in ('nuevo', 'contactado', 'respondido', 'agendado',
                                          'auditoria', 'propuesta', 'cerrado')),
  resultado             text check (resultado in ('ganado', 'perdido')),
  motivo_cierre         text not null default '' check (length(motivo_cierre) <= 300),
  proximo_paso          text not null default '' check (length(proximo_paso) <= 400),
  proxima_fecha         date,
  fecha_ultimo_contacto date,
  valor_cents           bigint not null default 0 check (valor_cents >= 0),
  creado_en             timestamptz not null default now(),
  actualizado_en        timestamptz not null default now(),
  -- A closed prospect must say whether it was won or lost, and an open one must not.
  constraint prospectos_cierre_con_resultado check ((estado = 'cerrado') = (resultado is not null))
);

create index if not exists prospectos_estado_score_idx on public.prospectos (estado, score desc);
create index if not exists prospectos_proxima_fecha_idx on public.prospectos (proxima_fecha);

create table if not exists public.prospecto_notas (
  id           bigint generated always as identity primary key,
  prospecto_id uuid not null references public.prospectos (id) on delete cascade,
  cuerpo       text not null check (length(cuerpo) between 1 and 4000),
  autor        text not null default 'humano',
  creado_en    timestamptz not null default now()
);

create index if not exists prospecto_notas_prospecto_idx
  on public.prospecto_notas (prospecto_id, creado_en desc);

-- No foreign key on purpose: the audit trail must survive the deletion of a prospect.
create table if not exists public.prospecto_cambios (
  id             bigint generated always as identity primary key,
  prospecto_id   uuid not null,
  nombre         text not null default '',
  operacion      text not null check (operacion in ('INSERT', 'UPDATE', 'DELETE', 'NOTA')),
  campo          text,
  valor_antes    text,
  valor_despues  text,
  rol            text not null,
  usuario_sesion text not null,
  actor          text,
  creado_en      timestamptz not null default now()
);

create index if not exists prospecto_cambios_prospecto_idx
  on public.prospecto_cambios (prospecto_id, id desc);

-- ------------------------------------------------------------------- identity helpers

-- Effective role of the statement. PostgREST runs `SET LOCAL ROLE <jwt role>`; the `role`
-- setting keeps reporting that role even inside SECURITY DEFINER functions, where
-- current_user would report the function owner instead.
create or replace function public.crm_rol_efectivo()
returns text
language sql
stable
set search_path = pg_catalog
as $$
  select coalesce(nullif(current_setting('role', true), 'none'), session_user::text)
$$;

-- Identity of the caller as asserted by its (signed) JWT: `actor` claim, else `sub`.
-- NULL for SQL sessions and scripts that do not go through PostgREST.
create or replace function public.crm_actor()
returns text
language sql
stable
set search_path = pg_catalog
as $$
  select coalesce(
    nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'actor',
    nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub'
  )
$$;

-- --------------------------------------------------------------------------- triggers

create or replace function public.tocar_actualizado_en()
returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
  new.actualizado_en := now();
  return new;
end
$$;

drop trigger if exists trg_tocar_actualizado_en on public.prospectos;
create trigger trg_tocar_actualizado_en
  before update on public.prospectos
  for each row execute function public.tocar_actualizado_en();

create or replace function public.registrar_cambio_prospecto()
returns trigger
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
  v_rol    text := public.crm_rol_efectivo();
  v_sesion text := session_user::text;
  v_actor  text := public.crm_actor();
  v_old    jsonb;
  v_new    jsonb;
  v_campo  text;
begin
  if tg_op = 'INSERT' then
    insert into public.prospecto_cambios
      (prospecto_id, nombre, operacion, rol, usuario_sesion, actor)
    values (new.id, new.nombre, 'INSERT', v_rol, v_sesion, v_actor);
    return new;
  elsif tg_op = 'DELETE' then
    insert into public.prospecto_cambios
      (prospecto_id, nombre, operacion, rol, usuario_sesion, actor)
    values (old.id, old.nombre, 'DELETE', v_rol, v_sesion, v_actor);
    return old;
  end if;

  v_old := to_jsonb(old);
  v_new := to_jsonb(new);
  for v_campo in select jsonb_object_keys(v_new) loop
    continue when v_campo = 'actualizado_en';
    if (v_old -> v_campo) is distinct from (v_new -> v_campo) then
      insert into public.prospecto_cambios
        (prospecto_id, nombre, operacion, campo, valor_antes, valor_despues,
         rol, usuario_sesion, actor)
      values (new.id, new.nombre, 'UPDATE', v_campo, v_old ->> v_campo, v_new ->> v_campo,
              v_rol, v_sesion, v_actor);
    end if;
  end loop;
  return new;
end
$$;

drop trigger if exists trg_registrar_cambio_prospecto on public.prospectos;
create trigger trg_registrar_cambio_prospecto
  after insert or update or delete on public.prospectos
  for each row execute function public.registrar_cambio_prospecto();

-- Notes written by the agent are attributed by the database, not by the client: the
-- agent cannot even send `autor` (no INSERT privilege on that column).
create or replace function public.fijar_autor_nota()
returns trigger
language plpgsql
set search_path = pg_catalog, public
as $$
begin
  if public.crm_rol_efectivo() = 'crm_agent' then
    new.autor := 'agente:' || coalesce(public.crm_actor(), 'desconocido');
  end if;
  return new;
end
$$;

drop trigger if exists trg_fijar_autor_nota on public.prospecto_notas;
create trigger trg_fijar_autor_nota
  before insert on public.prospecto_notas
  for each row execute function public.fijar_autor_nota();

create or replace function public.registrar_nota()
returns trigger
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
begin
  insert into public.prospecto_cambios
    (prospecto_id, nombre, operacion, campo, valor_despues, rol, usuario_sesion, actor)
  select new.prospecto_id, p.nombre, 'NOTA', 'nota', left(new.cuerpo, 200),
         public.crm_rol_efectivo(), session_user::text, public.crm_actor()
  from public.prospectos p
  where p.id = new.prospecto_id;
  return new;
end
$$;

drop trigger if exists trg_registrar_nota on public.prospecto_notas;
create trigger trg_registrar_nota
  after insert on public.prospecto_notas
  for each row execute function public.registrar_nota();

-- ---------------------------------------------------------------------- privileges

revoke all on public.prospectos, public.prospecto_notas, public.prospecto_cambios from public;
revoke all on public.prospectos, public.prospecto_notas, public.prospecto_cambios from crm_agent;

do $$
begin
  -- Supabase grants table privileges to anon/authenticated by default. The human app
  -- that uses these tables (a separate repository) adds its own grants and policies.
  if exists (select 1 from pg_roles where rolname = 'anon') then
    revoke all on public.prospectos, public.prospecto_notas, public.prospecto_cambios from anon;
  end if;
  if exists (select 1 from pg_roles where rolname = 'authenticated') then
    revoke all on public.prospectos, public.prospecto_notas, public.prospecto_cambios
      from authenticated;
  end if;
end
$$;

grant usage on schema public to crm_agent;
grant execute on function public.crm_rol_efectivo(), public.crm_actor() to crm_agent;

-- Read: everything except valor_cents (commercial value is not needed by the agent).
grant select (id, nombre, ciudad, telefono, email, web, score, tamano, hallazgos, propuesta,
              canal, origen, estado, resultado, motivo_cierre, proximo_paso, proxima_fecha,
              fecha_ultimo_contacto, creado_en, actualizado_en)
  on public.prospectos to crm_agent;

-- Write allowlist. Must stay identical to CAMPOS_ESCRIBIBLES in servers/crm/server.py
-- (tests/test_allowlist.py compares both).
grant update (estado, proximo_paso, proxima_fecha, fecha_ultimo_contacto, resultado, motivo_cierre)
  on public.prospectos to crm_agent;

grant select (id, prospecto_id, cuerpo, autor, creado_en) on public.prospecto_notas to crm_agent;
grant insert (prospecto_id, cuerpo) on public.prospecto_notas to crm_agent;

grant select on public.prospecto_cambios to crm_agent;

-- ------------------------------------------------------------------ row level security

alter table public.prospectos enable row level security;
alter table public.prospecto_notas enable row level security;
alter table public.prospecto_cambios enable row level security;

drop policy if exists crm_agent_lee_prospectos on public.prospectos;
create policy crm_agent_lee_prospectos on public.prospectos
  for select to crm_agent using (true);

drop policy if exists crm_agent_actualiza_prospectos on public.prospectos;
create policy crm_agent_actualiza_prospectos on public.prospectos
  for update to crm_agent using (true) with check (true);

drop policy if exists crm_agent_lee_notas on public.prospecto_notas;
create policy crm_agent_lee_notas on public.prospecto_notas
  for select to crm_agent using (true);

drop policy if exists crm_agent_inserta_notas on public.prospecto_notas;
create policy crm_agent_inserta_notas on public.prospecto_notas
  for insert to crm_agent with check (autor like 'agente:%');

drop policy if exists crm_agent_lee_cambios on public.prospecto_cambios;
create policy crm_agent_lee_cambios on public.prospecto_cambios
  for select to crm_agent using (true);

-- No DELETE grant or policy for crm_agent anywhere. No INSERT/UPDATE on prospecto_cambios.

commit;

-- PostgREST caches the schema; ask it to reload (harmless without PostgREST).
notify pgrst, 'reload schema';
