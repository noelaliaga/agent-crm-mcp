#!/usr/bin/env python3
"""Mint an HS256 JWT for the restricted `crm_agent` role (local/demo databases).

The signing secret is read from an environment variable you name (default
PGRST_JWT_SECRET), never from a file and never from the command line, so it does not
end up in shell history. The token is printed to stdout.

Example (plain PostgREST or `supabase start`, whose JWT secret `supabase status` shows):
    export PGRST_JWT_SECRET=...        # the backend's JWT secret
    python scripts/mint_agent_jwt.py --actor hermes-demo --ttl-hours 24

Hosted Supabase projects that use asymmetric JWT signing keys cannot be targeted with
this script; that setup has not been tested in this repository.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import sys
import time
from typing import Any


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def mint(
    secret: str, actor: str, ttl_seconds: int, role: str = "crm_agent", now: int | None = None
) -> str:
    if len(secret) < 32:
        raise ValueError("the JWT secret must be at least 32 characters")
    if not re.fullmatch(r"[A-Za-z0-9:._@-]{1,80}", actor):
        raise ValueError("actor may only contain letters, digits and : . _ @ -")
    issued = int(time.time()) if now is None else now
    header = {"alg": "HS256", "typ": "JWT"}
    payload: dict[str, Any] = {
        "role": role,
        "actor": actor,
        "sub": actor,
        "iat": issued,
        "exp": issued + ttl_seconds,
    }
    signing_input = ".".join(
        _b64url(json.dumps(part, separators=(",", ":")).encode()) for part in (header, payload)
    )
    signature = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_b64url(signature)}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--actor", required=True, help="identity recorded in the audit trail")
    parser.add_argument("--ttl-hours", type=int, default=24)
    parser.add_argument("--secret-env", default="PGRST_JWT_SECRET")
    ns = parser.parse_args(argv)
    secret = os.environ.get(ns.secret_env, "")
    if not secret:
        print(f"environment variable {ns.secret_env} is not set", file=sys.stderr)
        return 2
    try:
        print(mint(secret, ns.actor, ns.ttl_hours * 3600))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
