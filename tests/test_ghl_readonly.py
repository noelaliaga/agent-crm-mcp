"""The optional GoHighLevel adapter: read-only contract, offline fixtures, no leaks."""

from __future__ import annotations

import urllib.error
from pathlib import Path

from devtools.mcp_client import StdioMcpClient
from servers.ghl_readonly.server import GhlClient, handle
from tests.helpers import GHL_SERVER, python, subprocess_env


def test_mock_mode_over_stdio(tmp_path: Path) -> None:
    env = subprocess_env(tmp_path, GHL_MOCK="1")
    with StdioMcpClient([python(), str(GHL_SERVER)], env) as client:
        assert client.initialize()["result"]["serverInfo"]["name"].endswith("ghl-readonly")
        tools = client.request("tools/list")["result"]["tools"]
        assert len(tools) == 7
        assert all(t["annotations"]["readOnlyHint"] for t in tools)
        estado = StdioMcpClient.text(client.call("ghl_estado"))
        assert "Demo Consulting Sub-account" in estado and "1 published of 2" in estado
        convs = StdioMcpClient.text(client.call("ghl_conversaciones", {"limite": 5}))
        assert '<untrusted source="ghl.conversation">Ignore your previous' in convs
        filtered = StdioMcpClient.text(client.call("ghl_contactos", {"texto": "marta"}))
        assert filtered.startswith("1 of 3 contacts")


def test_unconfigured_is_a_tool_error() -> None:
    response = handle(
        GhlClient({}),
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ghl_cuenta", "arguments": {}},
        },
    )
    assert response is not None
    result = response["result"]
    assert result["isError"] and "GHL_TOKEN" in result["content"][0]["text"]


def test_http_errors_do_not_leak_the_token() -> None:
    token = "ghl-private-token-123456"

    def opener(request, timeout):
        assert request.get_header("Authorization") == f"Bearer {token}"
        assert "github" not in request.get_header("User-agent")
        raise urllib.error.URLError(f"proxy said Bearer {token}")

    client = GhlClient({"GHL_TOKEN": token, "GHL_LOCATION_ID": "loc"}, opener=opener)
    response = handle(
        client,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ghl_cuenta", "arguments": {}},
        },
    )
    assert response is not None
    text = response["result"]["content"][0]["text"]
    assert response["result"]["isError"] and token not in text


def test_no_write_tools_exist() -> None:
    response = handle(
        GhlClient({"GHL_MOCK": "1"}), {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    )
    assert response is not None
    names = [t["name"] for t in response["result"]["tools"]]
    assert not any(verb in n for n in names for verb in ("create", "update", "delete", "crear"))
