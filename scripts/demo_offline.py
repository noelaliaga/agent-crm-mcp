#!/usr/bin/env python3
"""Scripted offline demo: the real MCP server over stdio, a fake PostgREST, synthetic data.

    python scripts/demo_offline.py

It replays the demo scenario (read -> write refused -> dry run -> write -> audit) without
Postgres, Docker, network or an LLM: the tool calls are scripted, so this shows the
server's behaviour, not a model's. Database-level guarantees (column grants, trigger)
are NOT shown here; they are covered by tests/integration/ against real Postgres.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from devtools.fake_postgrest import FakePostgrest  # noqa: E402
from devtools.mcp_client import StdioMcpClient  # noqa: E402
from devtools.seed import load_seed  # noqa: E402

SERVER = ROOT / "servers" / "crm" / "server.py"
TOKEN = "demo-agent-token-not-a-secret"  # noqa: S105 - fake backend only
MAX_LINES = 14


def show(title: str, text: str, is_error: bool) -> None:
    lines = text.splitlines()
    print(f"\n> {title}" + ("   [isError]" if is_error else ""))
    for line in lines[:MAX_LINES]:
        print("  " + line)
    if len(lines) > MAX_LINES:
        print(f"  … ({len(lines) - MAX_LINES} more lines)")


def session(fake: FakePostgrest, mode: str, steps: list[tuple[str, dict[str, Any]]]) -> None:
    print(f"\n=== MCP session with CRM_WRITE_MODE={mode} ===")
    with tempfile.TemporaryDirectory() as home:
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": home,
            "PYTHONIOENCODING": "utf-8",
            "CRM_SUPABASE_URL": fake.url,
            "CRM_AGENT_TOKEN": TOKEN,
            "CRM_WRITE_MODE": mode,
            "CRM_MAX_WRITES": "5",
            "CRM_LOG_LEVEL": "error",
        }
        with StdioMcpClient([sys.executable, str(SERVER)], env) as client:
            client.initialize()
            for tool, args in steps:
                result = client.call(tool, args)
                label = f"{tool} {args}" if args else tool
                show(label, StdioMcpClient.text(result), result["isError"])


def main() -> int:
    fake = FakePostgrest(token=TOKEN, actor="hermes-demo")
    load_seed(fake)
    fake.start()
    try:
        print("agent-crm-mcp offline demo · fake PostgREST · synthetic data · scripted calls")
        norte = "Asesoría Demo Norte"
        nota = "Prefiere que le llamemos por la tarde."
        session(
            fake,
            "off",
            [
                ("crm_hoy", {}),
                ("crm_buscar", {"nombre": "Despacho Ficticio Levante"}),
                ("crm_apuntar_nota", {"nombre": norte, "nota": nota}),
            ],
        )
        session(
            fake,
            "dry_run",
            [
                ("crm_actualizar_estado", {"nombre": norte, "estado": "contactado"}),
            ],
        )
        session(
            fake,
            "on",
            [
                ("crm_apuntar_nota", {"nombre": norte, "nota": nota}),
                ("crm_actualizar_estado", {"nombre": norte, "estado": "contactado"}),
                ("crm_actualizar_estado", {"nombre": "Gestoría Ejemplo", "estado": "contactado"}),
                ("crm_cambios", {"nombre": norte, "limite": 5}),
                ("crm_salud", {}),
            ],
        )
    finally:
        fake.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
