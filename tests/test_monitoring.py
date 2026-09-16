"""Monitoring scripts and examples: alert content, log watcher state, valid plist."""

from __future__ import annotations

import plistlib
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.helpers import ROOT, subprocess_env

BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="needs bash")

WATCH = ROOT / "scripts" / "watch-runtime-log.sh"
ALERT = ROOT / "scripts" / "healthcheck-alert.sh"
FAIL_LINE = "WARNING MCP server 'crm' unhandled errors in a TaskGroup (1 sub-exception)\n"


def _run(script: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    assert BASH is not None
    return subprocess.run(
        [BASH, str(script), *args], env=env, capture_output=True, text=True, timeout=60
    )


def test_launchd_example_is_a_valid_plist() -> None:
    path = ROOT / "examples" / "monitoring" / "com.example.agent-crm-mcp.healthcheck.plist"
    data = plistlib.loads(path.read_bytes())
    assert data["ProgramArguments"][1].endswith("/scripts/healthcheck-alert.sh")
    assert data["StartInterval"] == 900


def test_monitoring_examples_use_shell_safe_placeholders() -> None:
    for path in (ROOT / "examples" / "monitoring").iterdir():
        text = path.read_text(encoding="utf-8")
        body = text.split("-->", 1)[-1] if path.suffix == ".plist" else text
        assert "<ABSOLUTE" not in body and "<YOUR" not in body and "<HOME" not in body


def test_log_watcher_without_state_rereads_the_window(tmp_path: Path) -> None:
    log = tmp_path / "agent.log"
    log.write_text("start\n" + FAIL_LINE + "ok\n", encoding="utf-8")
    env = subprocess_env(tmp_path)
    assert _run(WATCH, env, str(log), "50").returncode == 1
    assert _run(WATCH, env, str(log), "50").returncode == 1  # same old line alerts again


def test_log_watcher_with_state_only_alerts_on_new_lines(tmp_path: Path) -> None:
    log = tmp_path / "agent.log"
    state = tmp_path / "state" / "watch.offset"
    env = subprocess_env(tmp_path, WATCH_STATE_FILE=str(state))
    log.write_text("start\n" + FAIL_LINE, encoding="utf-8")
    first = _run(WATCH, env, str(log))
    assert first.returncode == 1 and "1 MCP failure line(s) in new lines" in first.stderr
    assert _run(WATCH, env, str(log)).returncode == 0  # recovered: no repeat alert
    with log.open("a", encoding="utf-8") as fh:
        fh.write("healthy\n")
    assert _run(WATCH, env, str(log)).returncode == 0
    with log.open("a", encoding="utf-8") as fh:
        fh.write(FAIL_LINE * 2)
    again = _run(WATCH, env, str(log))
    assert again.returncode == 1 and "2 MCP failure line(s)" in again.stderr
    log.write_text(FAIL_LINE, encoding="utf-8")  # rotated: read from the start
    assert _run(WATCH, env, str(log)).returncode == 1


def test_log_watcher_usage_errors(tmp_path: Path) -> None:
    env = subprocess_env(tmp_path)
    assert _run(WATCH, env, str(tmp_path / "missing.log")).returncode == 2
    log = tmp_path / "agent.log"
    log.write_text("x\n", encoding="utf-8")
    assert _run(WATCH, env, str(log), "many").returncode == 2


def test_alert_reports_why_the_healthcheck_failed(tmp_path: Path) -> None:
    env = subprocess_env(tmp_path, CRM_ENV_FILE=str(tmp_path / "missing.env"))
    out = _run(ALERT, env)
    assert out.returncode == 2
    assert "healthcheck FAILED" in out.stderr
    assert "stderr: cannot read --env-file" in out.stderr


def test_alert_redacts_tokens_from_stderr(tmp_path: Path) -> None:
    fake_python = tmp_path / "python3"
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJl"
    fake_python.write_text(
        f"#!/bin/sh\necho 'boom Bearer abc123secret and {jwt}' >&2\nexit 1\n", encoding="utf-8"
    )
    fake_python.chmod(0o755)
    out = _run(ALERT, subprocess_env(tmp_path, PYTHON=str(fake_python)))
    assert out.returncode == 1
    assert "abc123secret" not in out.stderr and jwt not in out.stderr
    assert "Bearer [redacted]" in out.stderr and "[redacted-jwt]" in out.stderr
