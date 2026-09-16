"""CRM_WRITE_MODE (off | dry_run | on), write budget and write-tool validation."""

from __future__ import annotations

import pytest

from servers.crm.server import Config
from tests.helpers import call

NORTE = "Asesoría Demo Norte"


def _writes(fake) -> list[dict]:
    return [r for r in fake.requests if r["method"] in ("PATCH", "POST", "DELETE")]


def test_default_and_unknown_modes_are_off() -> None:
    assert Config.from_env({}).write_mode == "off"
    config = Config.from_env({"CRM_WRITE_MODE": "yes-please"})
    assert config.write_mode == "off"
    assert config.warnings


@pytest.mark.parametrize(
    "tool,args",
    [
        ("crm_actualizar_estado", {"nombre": NORTE, "estado": "contactado"}),
        ("crm_programar_siguiente_paso", {"nombre": NORTE, "proximo_paso": "Llamar"}),
        ("crm_apuntar_nota", {"nombre": NORTE, "nota": "Hola"}),
    ],
)
def test_off_refuses_without_touching_the_backend(make_server, fake, tool, args) -> None:
    server = make_server()
    fake.requests.clear()
    text, is_error = call(server, tool, args)
    assert is_error
    assert "CRM_WRITE_MODE=off" in text and "Nothing was changed" in text
    assert fake.requests == []


def test_dry_run_returns_the_diff_and_writes_nothing(make_server, fake, get_prospect) -> None:
    server = make_server(CRM_WRITE_MODE="dry_run")
    audit_before = len(fake.tables["prospecto_cambios"])
    text, is_error = call(
        server, "crm_actualizar_estado", {"nombre": NORTE, "estado": "contactado"}
    )
    assert not is_error
    assert text.startswith("DRY RUN")
    assert "estado: 'nuevo' -> 'contactado'" in text
    assert _writes(fake) == []
    assert get_prospect(NORTE)["estado"] == "nuevo"
    assert len(fake.tables["prospecto_cambios"]) == audit_before
    assert server.writes_used == 0


def test_dry_run_note_shows_body_and_sends_nothing(make_server, fake) -> None:
    server = make_server(CRM_WRITE_MODE="dry_run")
    notes_before = len(fake.tables["prospecto_notas"])
    text, is_error = call(server, "crm_apuntar_nota", {"nombre": NORTE, "nota": "Prefiere email"})
    assert not is_error and "DRY RUN" in text and "Prefiere email" in text
    assert len(fake.tables["prospecto_notas"]) == notes_before
    assert _writes(fake) == []


def test_on_writes_only_the_diff_and_the_audit_names_the_agent(
    make_server, fake, get_prospect
) -> None:
    server = make_server(CRM_WRITE_MODE="on")
    text, is_error = call(
        server, "crm_actualizar_estado", {"nombre": NORTE, "estado": "contactado"}
    )
    assert not is_error, text
    assert "Updated «Asesoría Demo Norte»" in text
    [patch] = _writes(fake)
    assert patch["body"] == {"estado": "contactado"}
    assert get_prospect(NORTE)["estado"] == "contactado"
    last = fake.tables["prospecto_cambios"][-1]
    assert (last["campo"], last["rol"], last["actor"]) == ("estado", "crm_agent", "hermes-test")
    assert server.writes_used == 1


def test_on_note_never_sends_an_author(make_server, fake) -> None:
    server = make_server(CRM_WRITE_MODE="on")
    text, is_error = call(server, "crm_apuntar_nota", {"nombre": NORTE, "nota": "Llamar a las 10"})
    assert not is_error, text
    [post] = _writes(fake)
    assert set(post["body"]) == {"prospecto_id", "cuerpo"}
    assert fake.tables["prospecto_notas"][-1]["autor"] == "agente:hermes-test"
    assert "agente:hermes-test" in text


def test_write_budget_is_enforced_per_process(make_server, fake) -> None:
    server = make_server(CRM_WRITE_MODE="on", CRM_MAX_WRITES="1")
    assert not call(server, "crm_apuntar_nota", {"nombre": NORTE, "nota": "uno"})[1]
    text, is_error = call(server, "crm_apuntar_nota", {"nombre": NORTE, "nota": "dos"})
    assert is_error and "write budget" in text
    assert len(_writes(fake)) == 1


def test_zero_budget_means_no_writes(make_server, fake) -> None:
    server = make_server(CRM_WRITE_MODE="on", CRM_MAX_WRITES="0")
    text, is_error = call(
        server, "crm_programar_siguiente_paso", {"nombre": NORTE, "proximo_paso": "Llamar"}
    )
    assert is_error and "write budget" in text
    assert _writes(fake) == []


def test_closing_requires_resultado(make_server, fake) -> None:
    server = make_server(CRM_WRITE_MODE="on")
    text, is_error = call(server, "crm_actualizar_estado", {"nombre": NORTE, "estado": "cerrado"})
    assert is_error and "resultado" in text
    assert fake.requests == [] or _writes(fake) == []


def test_closing_with_resultado_and_reopening(make_server, fake, get_prospect) -> None:
    server = make_server(CRM_WRITE_MODE="on")
    _, is_error = call(
        server,
        "crm_actualizar_estado",
        {"nombre": NORTE, "estado": "cerrado", "resultado": "perdido", "motivo": "Sin interés"},
    )
    assert not is_error
    row = get_prospect(NORTE)
    assert (row["estado"], row["resultado"], row["motivo_cierre"]) == (
        "cerrado",
        "perdido",
        "Sin interés",
    )
    _, is_error = call(server, "crm_actualizar_estado", {"nombre": NORTE, "estado": "contactado"})
    assert not is_error
    assert _writes(fake)[-1]["body"] == {"estado": "contactado", "resultado": None}


def test_resultado_only_applies_to_cerrado(make_server) -> None:
    server = make_server(CRM_WRITE_MODE="on")
    text, is_error = call(
        server,
        "crm_actualizar_estado",
        {"nombre": NORTE, "estado": "agendado", "resultado": "ganado"},
    )
    assert is_error and "only apply" in text


def test_same_state_is_a_no_op(make_server, fake) -> None:
    server = make_server(CRM_WRITE_MODE="on")
    text, is_error = call(server, "crm_actualizar_estado", {"nombre": NORTE, "estado": "nuevo"})
    assert not is_error and "No change needed" in text
    assert _writes(fake) == [] and server.writes_used == 0


def test_ambiguous_name_never_writes(make_server, fake) -> None:
    server = make_server(CRM_WRITE_MODE="on")
    text, is_error = call(
        server, "crm_actualizar_estado", {"nombre": "Gestoría Ejemplo", "estado": "contactado"}
    )
    assert is_error and "will not pick" in text
    assert _writes(fake) == []


@pytest.mark.parametrize(
    "args,expected",
    [
        ({"nombre": NORTE, "estado": "ganado"}, "not valid"),
        ({"nombre": NORTE, "estado": "contactado", "score": 100}, "Unexpected argument"),
        ({"nombre": NORTE, "proximo_paso": "x", "fecha": "2026-02-30"}, "real calendar date"),
        ({"nombre": NORTE, "proximo_paso": "x", "fecha": "mañana"}, "YYYY-MM-DD"),
        ({"nombre": NORTE, "proximo_paso": "x" * 401}, "too long"),
        ({"nombre": NORTE, "nota": "x" * 4001}, "too long"),
        ({"nombre": NORTE, "nota": "   "}, "cannot be empty"),
        ({"nombre": "", "nota": "hola"}, "at least 2"),
    ],
)
def test_write_arguments_are_validated_server_side(make_server, fake, args, expected) -> None:
    server = make_server(CRM_WRITE_MODE="on")
    tool = (
        "crm_apuntar_nota"
        if "nota" in args
        else "crm_programar_siguiente_paso"
        if "proximo_paso" in args
        else "crm_actualizar_estado"
    )
    fake.requests.clear()
    text, is_error = call(server, tool, args)
    assert is_error and expected in text
    assert fake.requests == []


def test_zero_rows_updated_is_reported_as_failure(make_server, monkeypatch) -> None:
    server = make_server(CRM_WRITE_MODE="on")
    monkeypatch.setattr(server.db, "update", lambda *a, **k: [])
    text, is_error = call(server, "crm_actualizar_estado", {"nombre": NORTE, "estado": "agendado"})
    assert is_error and "updated 0 rows" in text
