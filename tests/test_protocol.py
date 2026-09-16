"""MCP / JSON-RPC behaviour, exercised through a real stdio subprocess (no network)."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from devtools.mcp_client import StdioMcpClient
from tests.helpers import CRM_SERVER, python, subprocess_env

EXPECTED_TOOLS = {
    "crm_buscar",
    "crm_hoy",
    "crm_pipeline",
    "crm_consulta",
    "crm_siguiente_llamada",
    "crm_actualizar_estado",
    "crm_programar_siguiente_paso",
    "crm_apuntar_nota",
    "crm_cambios",
    "crm_salud",
}
WRITE_TOOLS = {"crm_actualizar_estado", "crm_programar_siguiente_paso", "crm_apuntar_nota"}


@pytest.fixture
def client(tmp_path: Path) -> Iterator[StdioMcpClient]:
    c = StdioMcpClient([python(), str(CRM_SERVER)], subprocess_env(tmp_path), timeout=10)
    try:
        yield c
    finally:
        c.close()


def test_initialize_echoes_a_supported_version(client: StdioMcpClient) -> None:
    response = client.initialize("2025-06-18")
    result = response["result"]
    assert response["id"] == 1
    assert result["protocolVersion"] == "2025-06-18"
    assert result["serverInfo"]["name"] == "agent-crm-mcp"
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    assert "untrusted" in result["instructions"]


def test_initialize_with_unknown_version_answers_latest(client: StdioMcpClient) -> None:
    result = client.initialize("1999-01-01")["result"]
    assert result["protocolVersion"] == "2025-11-25"


def test_notifications_get_no_response(client: StdioMcpClient) -> None:
    client.initialize()
    client.notify("notifications/cancelled", {"requestId": 99})
    response = client.request("ping")
    assert response == {"jsonrpc": "2.0", "id": 2, "result": {}}


def test_tools_list_contract(client: StdioMcpClient) -> None:
    client.initialize()
    tools = client.request("tools/list")["result"]["tools"]
    assert {t["name"] for t in tools} == EXPECTED_TOOLS
    for tool in tools:
        assert tool["inputSchema"]["type"] == "object"
        assert tool["inputSchema"]["additionalProperties"] is False
        assert tool["annotations"]["destructiveHint"] is False
        assert tool["annotations"]["readOnlyHint"] is (tool["name"] not in WRITE_TOOLS)
    write_descriptions = [t["description"] for t in tools if t["name"] in WRITE_TOOLS]
    assert all("CRM_WRITE_MODE: off" in d for d in write_descriptions)


def test_unknown_method(client: StdioMcpClient) -> None:
    response = client.request("resources/list")
    assert response["error"]["code"] == -32601


def test_unknown_tool(client: StdioMcpClient) -> None:
    response = client.request("tools/call", {"name": "crm_borrar_todo", "arguments": {}})
    assert response["error"]["code"] == -32602


def test_arguments_must_be_an_object(client: StdioMcpClient) -> None:
    response = client.request("tools/call", {"name": "crm_hoy", "arguments": ["x"]})
    assert response["error"]["code"] == -32602


def test_invalid_json_returns_parse_error_and_server_survives(client: StdioMcpClient) -> None:
    client.send_raw("{this is not json")
    error = client.read_message()
    assert error == {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": -32700, "message": "Parse error"},
    }
    assert client.request("ping")["result"] == {}


def test_non_object_message_is_invalid_request(client: StdioMcpClient) -> None:
    client.send_raw("[1, 2, 3]")
    error = client.read_message()
    assert error is not None and error["error"]["code"] == -32600


def test_tool_call_without_configuration_is_a_controlled_tool_error(
    client: StdioMcpClient,
) -> None:
    client.initialize()
    result = client.call("crm_hoy")
    text = StdioMcpClient.text(result)
    assert result["isError"] is True
    assert "CRM_SUPABASE_URL" in text and "CRM_AGENT_TOKEN" in text
    assert "Traceback" not in text


def test_stderr_is_structured_json(client: StdioMcpClient) -> None:
    client.initialize()
    client.call("crm_hoy")
    client.close()
    lines = [json.loads(line) for line in client.stderr_lines if line.strip()]
    events = [line["event"] for line in lines]
    assert events[0] == "startup"
    assert "tool_call" in events and events[-1] == "shutdown"


def test_version_flag() -> None:
    out = subprocess.run(
        [python(), str(CRM_SERVER), "--version"],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    assert out.stdout.strip().startswith("agent-crm-mcp ")
