"""End to end: MCP server -> PostgREST -> Postgres, with a crm_agent JWT.

Runs only when CRM_IT_POSTGREST_URL, CRM_IT_JWT_SECRET and CRM_IT_DATABASE_URL are set
and psql is installed (the CI `integration` job starts PostgREST in Docker).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from devtools.mcp_client import StdioMcpClient
from scripts.mint_agent_jwt import mint
from tests.helpers import CRM_SERVER, subprocess_env

pytestmark = pytest.mark.integration

URL = os.environ.get("CRM_IT_POSTGREST_URL", "")
SECRET = os.environ.get("CRM_IT_JWT_SECRET", "")
DB = os.environ.get("CRM_IT_DATABASE_URL", "")
PSQL = shutil.which("psql")
if not (URL and SECRET and DB and PSQL):
    pytest.skip("needs PostgREST, Postgres and psql", allow_module_level=True)

TOKEN = mint(SECRET, "hermes-ci", 3600)
TARGET = "Gestoría Ejemplo Centro"


def _env(tmp_path: Path, mode: str) -> dict[str, str]:
    return subprocess_env(
        tmp_path, CRM_SUPABASE_URL=URL, CRM_REST_PATH="", CRM_AGENT_TOKEN=TOKEN, CRM_WRITE_MODE=mode
    )


def _sql(query: str) -> str:
    assert PSQL is not None
    out = subprocess.run(
        [PSQL, DB, "-X", "-q", "-t", "-A", "-v", "ON_ERROR_STOP=1", "-c", query],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return out.stdout.strip()


def test_healthcheck_against_real_postgrest(tmp_path: Path) -> None:
    out = subprocess.run(
        [sys.executable, str(CRM_SERVER), "--healthcheck"],
        env=_env(tmp_path, "off"),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert out.returncode == 0, out.stdout + out.stderr


def test_dry_run_changes_nothing(tmp_path: Path) -> None:
    before = _sql("select estado from public.prospectos where nombre = 'Asesoría Demo Costa'")
    with StdioMcpClient([sys.executable, str(CRM_SERVER)], _env(tmp_path, "dry_run")) as client:
        client.initialize()
        result = client.call(
            "crm_actualizar_estado", {"nombre": "Asesoría Demo Costa", "estado": "auditoria"}
        )
        assert not result["isError"] and "DRY RUN" in StdioMcpClient.text(result)
    assert (
        _sql("select estado from public.prospectos where nombre = 'Asesoría Demo Costa'") == before
    )


def test_agent_writes_through_mcp_are_attributed_by_the_database(tmp_path: Path) -> None:
    with StdioMcpClient([sys.executable, str(CRM_SERVER)], _env(tmp_path, "on")) as client:
        client.initialize()
        state = client.call("crm_actualizar_estado", {"nombre": TARGET, "estado": "contactado"})
        assert not state["isError"], StdioMcpClient.text(state)
        note = client.call("crm_apuntar_nota", {"nombre": TARGET, "nota": "Nota de integración"})
        assert not note["isError"], StdioMcpClient.text(note)
    assert (
        _sql(
            "select rol || '|' || actor from public.prospecto_cambios "
            f"where nombre = '{TARGET}' and campo = 'estado' order by id desc limit 1"
        )
        == "crm_agent|hermes-ci"
    )
    assert _sql("select autor from public.prospecto_notas order by id desc limit 1") == (
        "agente:hermes-ci"
    )


def _request(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(
        f"{URL}/{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, {}
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def test_database_denies_a_patch_outside_the_allowlist_even_without_the_server() -> None:
    target = urllib.parse.quote(TARGET)
    status, payload = _request("PATCH", f"prospectos?nombre=eq.{target}", {"score": 1})
    assert status in (401, 403) and payload.get("code") == "42501"


def test_database_hides_valor_cents_from_the_agent() -> None:
    status, payload = _request("GET", "prospectos?select=valor_cents&limit=1")
    assert status in (401, 403) and payload.get("code") == "42501"
