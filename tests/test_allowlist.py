"""The write allowlist, in Python and in the SQL migration, must be the same list."""

from __future__ import annotations

import re

import pytest

from devtools import fake_postgrest
from servers.crm.server import CAMPOS_ESCRIBIBLES, BackendError, ToolError
from tests.helpers import MIGRATION

SQL = MIGRATION.read_text(encoding="utf-8")


def _grant_columns(privilege: str, table: str) -> set[str]:
    match = re.search(
        rf"grant {privilege} \(([^)]*)\)\s+on public\.{table} to crm_agent", SQL, re.I
    )
    assert match, f"no column-level GRANT {privilege} on {table} in the migration"
    return {c.strip() for c in match.group(1).split(",")}


def test_python_allowlist_equals_database_update_grant() -> None:
    assert _grant_columns("update", "prospectos") == set(CAMPOS_ESCRIBIBLES)


def test_fake_backend_uses_the_same_allowlist() -> None:
    assert fake_postgrest.AGENT_UPDATE_COLUMNS == CAMPOS_ESCRIBIBLES


def test_agent_can_only_insert_note_body_and_prospect() -> None:
    assert _grant_columns("insert", "prospecto_notas") == {"prospecto_id", "cuerpo"}


def _statements() -> list[str]:
    code = re.sub(r"--[^\n]*", "", SQL.lower())
    return [" ".join(part.split()) for part in code.split(";")]


def _agent_grants() -> list[tuple[set[str], str]]:
    """(privileges, object) for every `GRANT ... ON ... TO crm_agent` in the migration."""
    grants = []
    for statement in _statements():
        match = re.fullmatch(r"grant (.+?) on (.+?) to crm_agent", statement)
        if match:
            privileges = re.sub(r"\([^)]*\)", "", match.group(1))
            grants.append(({p.strip() for p in privileges.split(",")}, match.group(2)))
    return grants


def test_migration_never_grants_delete_or_audit_writes_to_the_agent() -> None:
    grants = _agent_grants()
    assert len(grants) >= 6, grants  # the parser must actually find the grants
    for privileges, target in grants:
        assert not privileges & {"delete", "truncate", "all", "all privileges"}, target
        assert privileges <= {"select", "insert", "update", "usage", "execute"}, target
        if "prospecto_cambios" in target:
            assert privileges == {"select"}, target
    policies = [s for s in _statements() if s.startswith("create policy")]
    assert policies
    assert all(" for delete " not in p and " for all " not in p for p in policies)
    assert all(" for select " in p for p in policies if " on public.prospecto_cambios " in p)
    assert "service_role" not in " ".join(_statements())


def test_grant_parser_catches_combined_and_all_privileges() -> None:
    for bad in (
        "grant select, delete on public.prospectos to crm_agent",
        "grant all on public.prospectos to crm_agent",
    ):
        match = re.fullmatch(r"grant (.+?) on (.+?) to crm_agent", bad)
        assert match
        privileges = {p.strip() for p in match.group(1).split(",")}
        assert privileges & {"delete", "all"}


def test_audit_rows_about_unreadable_columns_are_hidden_from_the_agent() -> None:
    readable = _grant_columns("select", "prospectos")
    unreadable = set(fake_postgrest.SCHEMA["prospectos"]) - readable
    assert unreadable == {"valor_cents"}
    policy = re.search(
        r"create policy crm_agent_lee_cambios on public\.prospecto_cambios\s+for select to "
        r"crm_agent using \(campo is null or campo not in \(([^)]*)\)\)",
        SQL,
        re.I,
    )
    assert policy, "the audit read policy must filter by campo"
    hidden = {c.strip().strip("'") for c in policy.group(1).split(",")}
    assert hidden == unreadable == set(fake_postgrest.AGENT_HIDDEN_COLUMNS["prospectos"])


def test_patch_rejects_fields_outside_the_allowlist_before_any_request(make_server, fake):
    server = make_server(CRM_WRITE_MODE="on")
    fake.requests.clear()
    with pytest.raises(ToolError, match="score"):
        server._patch("00000000-0000-4000-8000-000000000001", {"score": 100})
    with pytest.raises(ToolError, match="email, nombre"):
        server._patch(
            "00000000-0000-4000-8000-000000000001",
            {"estado": "contactado", "nombre": "x", "email": "x@example.com"},
        )
    assert fake.requests == []


def test_write_tool_arguments_map_only_to_allowlisted_columns(make_server) -> None:
    arg_to_column = {
        "estado": "estado",
        "resultado": "resultado",
        "motivo": "motivo_cierre",
        "proximo_paso": "proximo_paso",
        "fecha": "proxima_fecha",
    }
    lookup_only = {"nombre", "nota"}
    for spec in make_server().tools():
        if spec.kind != "write":
            continue
        for arg in spec.input_schema["properties"]:
            if arg in lookup_only:
                continue
            assert arg_to_column[arg] in CAMPOS_ESCRIBIBLES, (spec.name, arg)


def test_backend_rejection_is_reported_as_permission_denied(make_server, fake) -> None:
    # Bypass the Python check on purpose: the (imitated) database must refuse on its own.
    server = make_server(CRM_WRITE_MODE="on")
    with pytest.raises(BackendError) as info:
        server.db.update(
            "prospectos",
            {"id": "eq.00000000-0000-4000-8000-000000000001"},
            {"score": 1},
            returning="id",
        )
    assert info.value.code == "42501"
    assert "permission denied" in str(info.value)
