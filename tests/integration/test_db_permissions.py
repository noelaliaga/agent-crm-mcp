"""Database-level guarantees, checked with psql against a real Postgres.

Runs only when CRM_IT_DATABASE_URL is set and psql is installed (the CI `integration`
job). Each check runs as role crm_agent with the JWT claims PostgREST would set, inside
a transaction that is rolled back.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

from tests.integration.gate import require

pytestmark = pytest.mark.integration

DB = os.environ.get("CRM_IT_DATABASE_URL", "")
PSQL = shutil.which("psql")
require(bool(DB and PSQL), "needs CRM_IT_DATABASE_URL and psql")

CLAIMS = json.dumps({"role": "crm_agent", "actor": "it-agent"})
AS_AGENT = (
    f"begin; set local role crm_agent; select set_config('request.jwt.claims', '{CLAIMS}', true); "
)
NORTE = "nombre = 'Asesoría Demo Norte'"


def psql(sql: str) -> subprocess.CompletedProcess[str]:
    assert PSQL is not None
    return subprocess.run(
        [PSQL, DB, "-X", "-q", "-t", "-A", "-v", "ON_ERROR_STOP=1", "-c", sql],
        capture_output=True,
        text=True,
        timeout=30,
    )


def rows(output: str) -> list[list[str]]:
    return [line.split("|") for line in output.splitlines() if "|" in line]


@pytest.mark.parametrize(
    "statement",
    [
        f"update public.prospectos set score = 1 where {NORTE};",
        f"update public.prospectos set nombre = 'Otro' where {NORTE};",
        f"update public.prospectos set email = 'x@example.com' where {NORTE};",
        "select valor_cents from public.prospectos limit 1;",
        f"delete from public.prospectos where {NORTE};",
        "insert into public.prospectos (nombre) values ('Nuevo desde el agente');",
        "insert into public.prospecto_cambios (prospecto_id, operacion, rol, usuario_sesion) "
        "values (gen_random_uuid(), 'UPDATE', 'forged', 'forged');",
        "update public.prospecto_cambios set actor = 'someone-else';",
        "delete from public.prospecto_cambios;",
        "insert into public.prospecto_notas (prospecto_id, cuerpo, autor) "
        "select id, 'nota', 'humano:forged' from public.prospectos limit 1;",
        "delete from public.prospecto_notas;",
    ],
)
def test_agent_role_is_denied_outside_the_allowlist(statement: str) -> None:
    result = psql(AS_AGENT + statement + " rollback;")
    assert result.returncode != 0, result.stdout
    assert "permission denied" in result.stderr


def test_allowlisted_update_is_audited_with_agent_role_and_actor() -> None:
    result = psql(
        AS_AGENT + f"update public.prospectos set estado = 'contactado', "
        f"fecha_ultimo_contacto = current_date where {NORTE}; "
        + "reset role; "
        + "select campo, coalesce(valor_antes, ''), valor_despues, rol, usuario_sesion, "
        "coalesce(actor, '') from public.prospecto_cambios "
        f"where {NORTE} and operacion = 'UPDATE' order by id; " + "rollback;"
    )
    assert result.returncode == 0, result.stderr
    audit = {r[0]: r for r in rows(result.stdout)}
    assert audit["estado"][1:4] == ["nuevo", "contactado", "crm_agent"]
    assert audit["estado"][5] == "it-agent"
    assert audit["fecha_ultimo_contacto"][3] == "crm_agent"
    assert "actualizado_en" not in audit


def test_note_author_is_set_by_the_database() -> None:
    result = psql(
        AS_AGENT + "insert into public.prospecto_notas (prospecto_id, cuerpo) "
        f"select id, 'Nota del agente' from public.prospectos where {NORTE}; "
        + "reset role; "
        + "select 'nota', autor from public.prospecto_notas order by id desc limit 1; "
        + "select operacion, rol, coalesce(actor, '') from public.prospecto_cambios "
        "order by id desc limit 1; " + "rollback;"
    )
    assert result.returncode == 0, result.stderr
    out = rows(result.stdout)
    assert ["nota", "agente:it-agent"] in out
    assert ["NOTA", "crm_agent", "it-agent"] in out


def test_closing_without_resultado_violates_check() -> None:
    result = psql(
        AS_AGENT + f"update public.prospectos set estado = 'cerrado' where {NORTE}; rollback;"
    )
    assert result.returncode != 0
    assert "prospectos_cierre_con_resultado" in result.stderr


def test_seed_history_has_agent_rows_and_no_service_role() -> None:
    result = psql(
        "select 'agent', count(*) from public.prospecto_cambios "
        "where rol = 'crm_agent' and actor = 'hermes-demo'; "
        "select 'service', count(*) from public.prospecto_cambios where rol = 'service_role';"
    )
    assert result.returncode == 0, result.stderr
    counts = dict(rows(result.stdout))
    assert int(counts["agent"]) >= 5  # 4 field updates + 1 note
    assert counts["service"] == "0"


def test_agent_cannot_read_valor_cents_through_the_audit_table() -> None:
    result = psql(
        f"begin; update public.prospectos set valor_cents = 987654 where {NORTE}; "
        "select 'owner', count(*) from public.prospecto_cambios where campo = 'valor_cents'; "
        + AS_AGENT.removeprefix("begin; ")
        + "select 'agent', count(*) from public.prospecto_cambios where campo = 'valor_cents'; "
        "select 'agent_value', count(*) from public.prospecto_cambios "
        "where valor_despues = '987654'; rollback;"
    )
    assert result.returncode == 0, result.stderr
    counts = dict(rows(result.stdout))
    assert int(counts["owner"]) >= 1
    assert counts["agent"] == "0"
    assert counts["agent_value"] == "0"
