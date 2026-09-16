"""Minimal MCP client over stdio, for tests, the smoke script and the offline demo.

It launches a server as a subprocess and speaks newline-delimited JSON-RPC 2.0, exactly
like Hermes Agent or Claude Code do with a stdio server. Standard library only.
"""

from __future__ import annotations

import json
import queue
import subprocess
import threading
from collections.abc import Mapping, Sequence
from typing import Any


class StdioMcpClient:
    def __init__(self, argv: Sequence[str], env: Mapping[str, str], timeout: float = 15.0):
        self.timeout = timeout
        self.proc = subprocess.Popen(  # noqa: S603 - argv is built by our own tests/scripts
            list(argv),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env=dict(env),
            bufsize=1,
        )
        self._out: queue.Queue[str | None] = queue.Queue()
        self._err: list[str] = []
        self._next_id = 0
        threading.Thread(target=self._pump_stdout, daemon=True).start()
        threading.Thread(target=self._pump_stderr, daemon=True).start()

    def _pump_stdout(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self._out.put(line)
        self._out.put(None)

    def _pump_stderr(self) -> None:
        assert self.proc.stderr is not None
        for line in self.proc.stderr:
            self._err.append(line)

    @property
    def stderr_lines(self) -> list[str]:
        return list(self._err)

    def send_raw(self, line: str) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(line.rstrip("\n") + "\n")
        self.proc.stdin.flush()

    def read_message(self, timeout: float | None = None) -> dict[str, Any] | None:
        try:
            line = self._out.get(timeout=self.timeout if timeout is None else timeout)
        except queue.Empty:
            return None
        if line is None:
            return None
        message: dict[str, Any] = json.loads(line)
        return message

    def request(self, method: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        self._next_id += 1
        msg: dict[str, Any] = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        if params is not None:
            msg["params"] = dict(params)
        self.send_raw(json.dumps(msg))
        response = self.read_message()
        if response is None:
            raise TimeoutError(f"no response to {method}")
        return response

    def notify(self, method: str, params: Mapping[str, Any] | None = None) -> None:
        msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = dict(params)
        self.send_raw(json.dumps(msg))

    def initialize(self, version: str = "2025-11-25") -> dict[str, Any]:
        response = self.request(
            "initialize",
            {
                "protocolVersion": version,
                "capabilities": {},
                "clientInfo": {"name": "agent-crm-mcp-devtools", "version": "0"},
            },
        )
        self.notify("notifications/initialized")
        return response

    def call(self, name: str, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
        response = self.request("tools/call", {"name": name, "arguments": dict(arguments or {})})
        if "error" in response:
            raise RuntimeError(f"JSON-RPC error: {response['error']}")
        result: dict[str, Any] = response["result"]
        return result

    @staticmethod
    def text(result: Mapping[str, Any]) -> str:
        return "\n".join(c.get("text", "") for c in result.get("content", []))

    def close(self) -> int:
        if self.proc.stdin is not None and not self.proc.stdin.closed:
            self.proc.stdin.close()
        try:
            return self.proc.wait(timeout=self.timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            return self.proc.wait()

    def __enter__(self) -> StdioMcpClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
