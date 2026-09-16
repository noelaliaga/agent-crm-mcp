"""The synthetic seed stays synthetic and in sync; the JWT helper signs correctly."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re

import pytest

from devtools.seed import SEED_SQL, build_sql, read_seed
from scripts.mint_agent_jwt import mint
from servers.crm.server import CAMPOS_ESCRIBIBLES, ESTADOS


def test_seed_sql_is_generated_from_the_json() -> None:
    assert SEED_SQL.read_text(encoding="utf-8") == build_sql(read_seed()), (
        "supabase/seed.sql is stale: run `python -m devtools.seed`"
    )


def test_seed_is_synthetic_and_covers_every_stage() -> None:
    data = read_seed()
    rows = data["prospectos"]
    assert len(rows) >= 30
    assert {r["estado"] for r in rows} == set(ESTADOS)
    for r in rows:
        assert r["email"] == "" or r["email"].endswith(".example.com"), r["email"]
        assert r["web"] == "" or re.fullmatch(r"https://[a-z0-9-]+\.example\.com", r["web"])
        assert r["telefono"] == "" or r["telefono"].startswith("+44 7700 900"), r["telefono"]
        assert (r["estado"] == "cerrado") == bool(r.get("resultado"))
    names = [r["nombre"] for r in rows]
    assert len(names) == len(set(names))


def test_seed_agent_history_only_uses_allowlisted_fields() -> None:
    for event in read_seed()["historial"]:
        if event["como"] == "agente" and event["tipo"] == "update":
            fields = {
                k.removesuffix("_dias") if k != "proxima_fecha_dias" else "proxima_fecha"
                for k in event["cambios"]
            }
            assert fields <= CAMPOS_ESCRIBIBLES


def _b64decode(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def test_mint_agent_jwt() -> None:
    secret = "s" * 40
    token = mint(secret, "hermes-demo", 3600, now=1_700_000_000)
    header, payload, signature = token.split(".")
    assert json.loads(_b64decode(header)) == {"alg": "HS256", "typ": "JWT"}
    claims = json.loads(_b64decode(payload))
    assert claims["role"] == "crm_agent" and claims["actor"] == "hermes-demo"
    assert claims["exp"] - claims["iat"] == 3600
    expected = hmac.new(secret.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
    assert _b64decode(signature) == expected


@pytest.mark.parametrize("secret,actor", [("short", "ok"), ("s" * 40, "bad actor!")])
def test_mint_rejects_weak_input(secret: str, actor: str) -> None:
    with pytest.raises(ValueError):
        mint(secret, actor, 60)
