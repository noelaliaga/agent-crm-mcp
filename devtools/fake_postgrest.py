"""In-memory imitation of the small part of PostgREST that the CRM server uses.

Used by the unit tests, scripts/smoke.sh and scripts/demo_offline.py so that all of them
run without Postgres, Docker or network access.

It is NOT a security boundary and NOT a proof of the database guarantees. It imitates
the grants of supabase/migrations/0001_crm_min.sql so that tests can exercise the
server's error paths, but the real enforcement is only verified by the integration
tests against Postgres (tests/integration/).

Standard library only.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

SCHEMA: dict[str, tuple[str, ...]] = {
    "prospectos": (
        "id",
        "nombre",
        "ciudad",
        "telefono",
        "email",
        "web",
        "score",
        "tamano",
        "hallazgos",
        "propuesta",
        "canal",
        "origen",
        "estado",
        "resultado",
        "motivo_cierre",
        "proximo_paso",
        "proxima_fecha",
        "fecha_ultimo_contacto",
        "valor_cents",
        "creado_en",
        "actualizado_en",
    ),
    "prospecto_notas": ("id", "prospecto_id", "cuerpo", "autor", "creado_en"),
    "prospecto_cambios": (
        "id",
        "prospecto_id",
        "nombre",
        "operacion",
        "campo",
        "valor_antes",
        "valor_despues",
        "rol",
        "usuario_sesion",
        "actor",
        "creado_en",
    ),
}

# Independent copy of the migration's grants (tests compare all three lists).
AGENT_UPDATE_COLUMNS = frozenset(
    {
        "estado",
        "proximo_paso",
        "proxima_fecha",
        "fecha_ultimo_contacto",
        "resultado",
        "motivo_cierre",
    }
)
AGENT_NOTE_INSERT_COLUMNS = frozenset({"prospecto_id", "cuerpo"})
AGENT_HIDDEN_COLUMNS = {"prospectos": frozenset({"valor_cents"})}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _like_regex(pattern: str) -> re.Pattern[str]:
    out: list[str] = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\" and i + 1 < len(pattern):
            out.append(re.escape(pattern[i + 1]))
            i += 2
            continue
        if ch in "*%":
            out.append(".*")
        elif ch == "_":
            out.append(".")
        else:
            out.append(re.escape(ch))
        i += 1
    return re.compile("".join(out), re.S | re.I)


def _coerce(cell: Any, raw: str) -> Any:
    if isinstance(cell, bool):
        return raw == "true"
    if isinstance(cell, int):
        try:
            return int(raw)
        except ValueError:
            return raw
    return raw


def _matches(cell: Any, op: str, raw: str) -> bool:
    if op == "is":
        return cell is None if raw == "null" else str(cell).lower() == raw
    if cell is None:
        return False
    if op in ("like", "ilike"):
        return _like_regex(raw).fullmatch(str(cell)) is not None
    value = _coerce(cell, raw)
    left: Any = cell if isinstance(cell, int) and isinstance(value, int) else str(cell)
    right: Any = value if isinstance(left, int) else str(value)
    results: dict[str, bool] = {
        "eq": left == right,
        "neq": left != right,
        "gt": left > right,
        "gte": left >= right,
        "lt": left < right,
        "lte": left <= right,
    }
    return results.get(op, False)


class PgError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class FakePostgrest:
    def __init__(
        self,
        tables: Mapping[str, list[dict[str, Any]]] | None = None,
        token: str = "test-agent-token",
        actor: str = "fake-agent",
        prefix: str = "/rest/v1",
        enforce_grants: bool = True,
    ):
        self.tables: dict[str, list[dict[str, Any]]] = {name: [] for name in SCHEMA}
        for name, rows in (tables or {}).items():
            self.tables[name] = copy.deepcopy(list(rows))
        self.token = token
        self.actor = actor
        self.prefix = prefix.rstrip("/")
        self.enforce_grants = enforce_grants
        self.requests: list[dict[str, Any]] = []
        self.fail_next: tuple[int, Any] | None = None
        self.lock = threading.RLock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # -- lifecycle -------------------------------------------------------------------

    def start(self, host: str = "127.0.0.1", port: int = 0) -> str:
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def do_GET(self) -> None:  # noqa: N802
                fake._dispatch(self, "GET")

            def do_PATCH(self) -> None:  # noqa: N802
                fake._dispatch(self, "PATCH")

            def do_POST(self) -> None:  # noqa: N802
                fake._dispatch(self, "POST")

            def do_DELETE(self) -> None:  # noqa: N802
                fake._dispatch(self, "DELETE")

        self._server = ThreadingHTTPServer((host, port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self.url

    @property
    def url(self) -> str:
        assert self._server is not None, "server not started"
        host, port = self._server.server_address[:2]
        return f"http://{host!s}:{port}"

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def __enter__(self) -> FakePostgrest:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    # -- data helpers ----------------------------------------------------------------

    def _next_id(self, table: str) -> int:
        return max((int(r["id"]) for r in self.tables[table]), default=0) + 1

    def audit(
        self,
        prospecto_id: str,
        nombre: str,
        operacion: str,
        campo: str | None = None,
        antes: Any = None,
        despues: Any = None,
        rol: str = "crm_agent",
        actor: str | None = None,
    ) -> None:
        def text(v: Any) -> str | None:
            if v is None:
                return None
            return json.dumps(v, ensure_ascii=False) if isinstance(v, list | dict) else str(v)

        self.tables["prospecto_cambios"].append(
            {
                "id": self._next_id("prospecto_cambios"),
                "prospecto_id": prospecto_id,
                "nombre": nombre,
                "operacion": operacion,
                "campo": campo,
                "valor_antes": text(antes),
                "valor_despues": text(despues),
                "rol": rol,
                "usuario_sesion": "authenticator" if rol == "crm_agent" else rol,
                "actor": actor,
                "creado_en": _now(),
            }
        )

    def apply_update(
        self, row: dict[str, Any], changes: Mapping[str, Any], rol: str, actor: str | None
    ) -> None:
        new = {**row, **changes}
        if (new.get("estado") == "cerrado") != (new.get("resultado") is not None):
            raise PgError(
                400,
                "23514",
                'new row for relation "prospectos" violates check '
                'constraint "prospectos_cierre_con_resultado"',
            )
        for key, value in changes.items():
            if row.get(key) != value:
                self.audit(row["id"], row["nombre"], "UPDATE", key, row.get(key), value, rol, actor)
                row[key] = value
        row["actualizado_en"] = _now()

    def add_note(
        self, prospecto_id: str, cuerpo: str, autor: str, rol: str, actor: str | None
    ) -> dict[str, Any]:
        prospect = next((p for p in self.tables["prospectos"] if p["id"] == prospecto_id), None)
        if prospect is None:
            raise PgError(
                409,
                "23503",
                'insert or update on table "prospecto_notas" violates foreign key constraint',
            )
        if not 1 <= len(cuerpo) <= 4000:
            raise PgError(400, "23514", "prospecto_notas_cuerpo_check")
        note = {
            "id": self._next_id("prospecto_notas"),
            "prospecto_id": prospecto_id,
            "cuerpo": cuerpo,
            "autor": autor,
            "creado_en": _now(),
        }
        self.tables["prospecto_notas"].append(note)
        self.audit(prospecto_id, prospect["nombre"], "NOTA", "nota", None, cuerpo[:200], rol, actor)
        return note

    # -- HTTP ------------------------------------------------------------------------

    def _dispatch(self, handler: BaseHTTPRequestHandler, method: str) -> None:
        parts = urlsplit(handler.path)
        length = int(handler.headers.get("Content-Length") or 0)
        raw_body = handler.rfile.read(length) if length else b""
        headers = {k.lower(): v for k, v in handler.headers.items()}
        try:
            body = json.loads(raw_body) if raw_body else None
        except json.JSONDecodeError:
            body = None
        table = (
            parts.path[len(self.prefix) + 1 :] if parts.path.startswith(self.prefix + "/") else ""
        )
        params = parse_qsl(parts.query, keep_blank_values=True)
        with self.lock:
            self.requests.append(
                {
                    "method": method,
                    "table": table,
                    "params": params,
                    "headers": headers,
                    "body": body,
                }
            )
            try:
                if self.fail_next is not None:
                    status, payload = self.fail_next
                    self.fail_next = None
                    self._send(handler, status, payload)
                    return
                if headers.get("authorization") != f"Bearer {self.token}":
                    # Deliberately verbose, like a misconfigured proxy: echoes the credential,
                    # so tests can prove the server redacts it.
                    received = headers.get("authorization", "")
                    raise PgError(401, "PGRST301", f"JWT invalid. Received: {received}")
                if table not in SCHEMA:
                    raise PgError(404, "42P01", f'relation "public.{table}" does not exist')
                status, payload, extra = self._handle(method, table, params, headers, body)
                self._send(handler, status, payload, extra)
            except PgError as exc:
                self._send(
                    handler,
                    exc.status,
                    {"code": exc.code, "message": exc.message, "details": None, "hint": None},
                )

    @staticmethod
    def _send(
        handler: BaseHTTPRequestHandler,
        status: int,
        payload: Any,
        extra: Mapping[str, str] | None = None,
    ) -> None:
        data = (
            b""
            if payload is None
            else (payload.encode() if isinstance(payload, str) else json.dumps(payload).encode())
        )
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(data)))
        for key, value in (extra or {}).items():
            handler.send_header(key, value)
        handler.end_headers()
        if data:
            handler.wfile.write(data)

    def _columns(self, table: str, select: str) -> list[str]:
        cols = (
            list(SCHEMA[table]) if select in ("", "*") else [c.strip() for c in select.split(",")]
        )
        for col in cols:
            if col not in SCHEMA[table]:
                raise PgError(400, "42703", f"column {table}.{col} does not exist")
        hidden = AGENT_HIDDEN_COLUMNS.get(table, frozenset())
        if self.enforce_grants and (select in ("", "*") and hidden or set(cols) & hidden):
            raise PgError(403, "42501", f"permission denied for table {table}")
        return cols

    def _filter(self, table: str, params: list[tuple[str, str]]) -> list[dict[str, Any]]:
        rows = self.tables[table]
        for key, value in params:
            if key in ("select", "order", "limit", "offset"):
                continue
            if key not in SCHEMA[table]:
                raise PgError(400, "42703", f"column {table}.{key} does not exist")
            op, _, operand = value.partition(".")
            rows = [r for r in rows if _matches(r.get(key), op, operand)]
        return rows

    @staticmethod
    def _order(rows: list[dict[str, Any]], order: str) -> list[dict[str, Any]]:
        for spec in reversed([s for s in order.split(",") if s]):
            col, _, direction = spec.partition(".")
            desc = direction.startswith("desc")
            present = [r for r in rows if r.get(col) is not None]
            missing = [r for r in rows if r.get(col) is None]

            def sort_key(r: dict[str, Any], c: str = col) -> tuple[bool, Any]:
                return (isinstance(r[c], str), r[c])

            present.sort(key=sort_key, reverse=desc)
            rows = missing + present if desc else present + missing
        return rows

    def _handle(
        self,
        method: str,
        table: str,
        params: list[tuple[str, str]],
        headers: Mapping[str, str],
        body: Any,
    ) -> tuple[int, Any, dict[str, str]]:
        q = dict(params)
        prefer = headers.get("prefer", "")
        cols = self._columns(table, q.get("select", "*"))
        if method == "GET":
            rows = self._order(self._filter(table, params), q.get("order", ""))
            total = len(rows)
            if "limit" in q:
                rows = rows[: int(q["limit"])]
            extra = {}
            if "count=exact" in prefer:
                extra["Content-Range"] = f"0-{len(rows) - 1}/{total}" if rows else f"*/{total}"
            return 200, [{c: r.get(c) for c in cols} for r in rows], extra
        if method == "PATCH":
            if table != "prospectos" or not isinstance(body, dict):
                raise PgError(403, "42501", f"permission denied for table {table}")
            if self.enforce_grants and set(body) - AGENT_UPDATE_COLUMNS:
                raise PgError(403, "42501", f"permission denied for table {table}")
            rows = self._filter(table, params)
            for row in rows:
                self.apply_update(row, body, "crm_agent", self.actor)
            if "return=representation" in prefer:
                return 200, [{c: r.get(c) for c in cols} for r in rows], {}
            return 204, None, {}
        if method == "POST":
            if table != "prospecto_notas" or not isinstance(body, dict):
                raise PgError(403, "42501", f"permission denied for table {table}")
            if self.enforce_grants and set(body) - AGENT_NOTE_INSERT_COLUMNS:
                raise PgError(403, "42501", f"permission denied for table {table}")
            note = self.add_note(
                str(body.get("prospecto_id")),
                str(body.get("cuerpo") or ""),
                f"agente:{self.actor}",
                "crm_agent",
                self.actor,
            )
            if "return=representation" in prefer:
                return 201, [{c: note.get(c) for c in cols}], {}
            return 201, None, {}
        raise PgError(403, "42501", f"permission denied for table {table}")


def main(argv: list[str] | None = None) -> int:
    from devtools.seed import load_seed

    parser = argparse.ArgumentParser(description="Run the fake PostgREST with the synthetic seed")
    parser.add_argument(
        "--seed", default=str(Path(__file__).parents[1] / "supabase/seed_data.json")
    )
    parser.add_argument("--token", default="demo-agent-token")
    parser.add_argument("--actor", default="hermes-demo")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--port-file", help="write the base URL to this file once listening")
    ns = parser.parse_args(argv)
    fake = FakePostgrest(token=ns.token, actor=ns.actor)
    load_seed(fake, ns.seed)
    url = fake.start(port=ns.port)
    if ns.port_file:
        Path(ns.port_file).write_text(url, encoding="utf-8")
    print(
        f"fake PostgREST listening on {url} (synthetic data, no grants beyond imitation)",
        file=sys.stderr,
        flush=True,
    )
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        fake.stop()
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parents[1]))
    sys.exit(main())
