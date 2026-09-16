from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CRM_SERVER = ROOT / "servers" / "crm" / "server.py"
GHL_SERVER = ROOT / "servers" / "ghl_readonly" / "server.py"
MIGRATION = ROOT / "supabase" / "migrations" / "0001_crm_min.sql"

TOKEN = "test-agent-token-0123456789"
FIXED_TODAY = date(2026, 9, 14)


def call(server: Any, name: str, args: Mapping[str, Any] | None = None) -> tuple[str, bool]:
    spec = next(s for s in server.tools() if s.name == name)
    result = server.call_tool(spec, dict(args or {}))
    return result["content"][0]["text"], result["isError"]


def subprocess_env(home: Path, **extra: str) -> dict[str, str]:
    """Minimal environment for a server subprocess: no inherited secrets, empty HOME."""
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(home),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8",
    }
    env.update(extra)
    return env


def python() -> str:
    return sys.executable
