"""Errors are explained, never leak credentials, and logs carry no arguments."""

from __future__ import annotations

import io
import json
import socket

from servers.crm.server import Config, CrmServer, JsonLog, load_env_file, redact
from tests.helpers import FIXED_TODAY, TOKEN, call

WRONG_TOKEN = "wrong-token-abcdef-123456"


def test_missing_configuration_names_variables() -> None:
    server = CrmServer(Config.from_env({}))
    text, is_error = call(server, "crm_hoy")
    assert is_error
    assert "missing CRM_SUPABASE_URL, CRM_AGENT_TOKEN" in text


def test_rejected_token_is_not_echoed(fake) -> None:
    server = CrmServer(
        Config.from_env({"CRM_SUPABASE_URL": fake.url, "CRM_AGENT_TOKEN": WRONG_TOKEN})
    )
    text, is_error = call(server, "crm_pipeline")
    assert is_error
    assert "HTTP 401" in text and "token was rejected" in text
    assert WRONG_TOKEN not in text


def test_jwt_and_bearer_values_in_backend_messages_are_redacted(make_server, fake) -> None:
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJyb2xlIjoiY3JtX2FnZW50In0.c2lnbmF0dXJl"
    fake.fail_next = (500, {"code": "XX000", "message": f"boom {jwt} Bearer {TOKEN}"})
    text, is_error = call(make_server(), "crm_hoy")
    assert is_error and "HTTP 500" in text
    assert jwt not in text and TOKEN not in text
    assert "[redacted" in text


def test_non_json_error_body(make_server, fake) -> None:
    fake.fail_next = (502, "<html>bad gateway</html>")
    text, is_error = call(make_server(), "crm_hoy")
    assert is_error and "HTTP 502" in text and "<html>" not in text


def test_unreachable_backend() -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = CrmServer(
        Config.from_env(
            {
                "CRM_SUPABASE_URL": f"http://127.0.0.1:{port}",
                "CRM_AGENT_TOKEN": TOKEN,
                "CRM_TIMEOUT_SECONDS": "2",
            }
        )
    )
    text, is_error = call(server, "crm_hoy")
    assert is_error and "Could not reach the CRM backend" in text


def test_missing_table_hint(make_server) -> None:
    text, is_error = call(make_server(CRM_TABLE_PROSPECTOS="leads_old"), "crm_hoy")
    assert is_error and "table not found" in text


def test_bad_url_scheme_and_table_names_are_rejected() -> None:
    config = Config.from_env(
        {
            "CRM_SUPABASE_URL": "file:///etc/passwd",
            "CRM_TABLE_NOTAS": "notas; drop table x",
            "CRM_AGENT_TOKEN": "t",
        }
    )
    assert config.base_url == ""
    assert config.table_notas == "prospecto_notas"
    assert len(config.warnings) == 2


def test_unexpected_exception_is_generic(make_server, monkeypatch) -> None:
    def boom(self, args):
        raise RuntimeError(f"internal detail with {TOKEN}")

    monkeypatch.setattr(CrmServer, "t_hoy", boom)
    text, is_error = call(make_server(), "crm_hoy")
    assert is_error
    assert text.startswith("Internal error")
    assert TOKEN not in text and "internal detail" not in text


def test_logs_contain_no_arguments_results_or_token(fake) -> None:
    stream = io.StringIO()
    config = Config.from_env({"CRM_SUPABASE_URL": fake.url, "CRM_AGENT_TOKEN": TOKEN})
    server = CrmServer(
        config, JsonLog(stream, "debug", config.secrets()), today=lambda: FIXED_TODAY
    )
    call(server, "crm_buscar", {"nombre": "Asesoría Demo Norte"})
    call(server, "crm_consulta", {"estado": "no-such-estado"})
    records = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert {r["event"] for r in records} >= {"tool_call", "backend_request"}
    raw = stream.getvalue()
    for secret_or_pii in (TOKEN, "Demo Norte", "+44 7700", "no-such-estado"):
        assert secret_or_pii not in raw
    tool_calls = [r for r in records if r["event"] == "tool_call"]
    assert [r["ok"] for r in tool_calls] == [True, False]
    assert tool_calls[1]["error_kind"] == "tool_error"


def test_env_file_reads_only_crm_keys_and_environment_wins(tmp_path) -> None:
    env_file = tmp_path / "agent.env"
    env_file.write_text(
        "# comment\nexport CRM_WRITE_MODE=on\nCRM_AGENT_TOKEN='from-file'\n"
        "OPENAI_API_KEY=should-be-ignored\n",
        encoding="utf-8",
    )
    merged = load_env_file(str(env_file), {"CRM_AGENT_TOKEN": "from-env"})
    assert merged["CRM_WRITE_MODE"] == "on"
    assert merged["CRM_AGENT_TOKEN"] == "from-env"
    assert "OPENAI_API_KEY" not in merged


def test_redact_helper() -> None:
    assert redact("key=abcdefgh", ["abcdefgh"]) == "key=[redacted]"
    assert redact("Authorization: Bearer xyz") == "Authorization: Bearer [redacted]"
