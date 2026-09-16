from __future__ import annotations

import sys
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from tests.helpers import FIXED_TODAY, ROOT, TOKEN

sys.path.insert(0, str(ROOT))

from devtools.fake_postgrest import FakePostgrest  # noqa: E402
from devtools.seed import load_seed  # noqa: E402
from servers.crm.server import Config, CrmServer, JsonLog  # noqa: E402


@pytest.fixture
def fake() -> Iterator[FakePostgrest]:
    server = FakePostgrest(token=TOKEN, actor="hermes-test")
    load_seed(server, today=FIXED_TODAY)
    server.start()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture
def make_server(fake: FakePostgrest) -> Callable[..., CrmServer]:
    def _make(log: Any = None, **env: str) -> CrmServer:
        values = {"CRM_SUPABASE_URL": fake.url, "CRM_AGENT_TOKEN": TOKEN, **env}
        config = Config.from_env(values)
        return CrmServer(config, log or JsonLog(None), today=lambda: FIXED_TODAY)

    return _make


def prospect(fake: FakePostgrest, nombre: str) -> dict[str, Any]:
    return next(p for p in fake.tables["prospectos"] if p["nombre"] == nombre)


@pytest.fixture
def get_prospect(fake: FakePostgrest) -> Callable[[str], dict[str, Any]]:
    return lambda nombre: prospect(fake, nombre)
