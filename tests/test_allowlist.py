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


def test_migration_never_grants_delete_or_audit_writes_to_the_agent() -> None:
    lowered = SQL.lower()
    assert "grant delete" not in lowered
    assert "for delete" not in lowered
    assert not re.search(r"grant (insert|update|all)[^;]*prospecto_cambios", lowered)
    assert "service_role" not in re.sub(r"--[^\n]*", "", lowered)


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
