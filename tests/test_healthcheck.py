"""`server.py --healthcheck` exit codes, run as a real subprocess."""

from __future__ import annotations

import json
import socket
import subprocess
from pathlib import Path

from tests.helpers import CRM_SERVER, TOKEN, python, subprocess_env


def _run(env: dict[str, str], *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [python(), str(CRM_SERVER), "--healthcheck", *extra],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_healthcheck_ok(fake, tmp_path: Path) -> None:
    out = _run(subprocess_env(tmp_path, CRM_SUPABASE_URL=fake.url, CRM_AGENT_TOKEN=TOKEN))
    assert out.returncode == 0, out.stdout + out.stderr
    report = json.loads(out.stdout)
    assert report["ok"] is True and report["status"] == "ok"
    assert TOKEN not in out.stdout + out.stderr
    assert all(json.loads(line)["event"] for line in out.stderr.splitlines())
    assert [r["method"] for r in fake.requests] == ["GET", "GET"]


def test_healthcheck_backend_down_exits_1(tmp_path: Path) -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    out = _run(
        subprocess_env(
            tmp_path,
            CRM_SUPABASE_URL=f"http://127.0.0.1:{port}",
            CRM_AGENT_TOKEN=TOKEN,
            CRM_TIMEOUT_SECONDS="2",
        )
    )
    assert out.returncode == 1
    assert json.loads(out.stdout)["status"] == "backend_error"


def test_healthcheck_wrong_token_exits_1(fake, tmp_path: Path) -> None:
    out = _run(
        subprocess_env(tmp_path, CRM_SUPABASE_URL=fake.url, CRM_AGENT_TOKEN="expired-token-000000")
    )
    assert out.returncode == 1
    assert "expired-token-000000" not in out.stdout + out.stderr


def test_healthcheck_misconfigured_exits_2(tmp_path: Path) -> None:
    out = _run(subprocess_env(tmp_path))
    assert out.returncode == 2
    assert json.loads(out.stdout)["status"] == "misconfigured"


def test_env_file_flag(fake, tmp_path: Path) -> None:
    env_file = tmp_path / "crm.env"
    env_file.write_text(f"CRM_SUPABASE_URL={fake.url}\nCRM_AGENT_TOKEN={TOKEN}\n", encoding="utf-8")
    out = _run(subprocess_env(tmp_path), "--env-file", str(env_file))
    assert out.returncode == 0, out.stderr


def test_missing_env_file_exits_2(tmp_path: Path) -> None:
    out = _run(subprocess_env(tmp_path), "--env-file", str(tmp_path / "nope.env"))
    assert out.returncode == 2
