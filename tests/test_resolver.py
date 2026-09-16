"""Name resolution for writes: zero, one or several matches, and filter escaping."""

from __future__ import annotations

import pytest

from servers.crm.server import ToolError, ilike_contains, search_term


def test_zero_matches(make_server) -> None:
    with pytest.raises(ToolError, match="No prospect matches"):
        make_server().resolve("Bufete Que No Existe")


def test_single_match(make_server, get_prospect) -> None:
    pid, name = make_server().resolve("ficticio levante")
    assert name == "Despacho Ficticio Levante"
    assert pid == get_prospect("Despacho Ficticio Levante")["id"]


def test_several_matches_are_refused_and_listed(make_server) -> None:
    with pytest.raises(ToolError) as info:
        make_server().resolve("Gestoría Ejemplo")
    message = str(info.value)
    assert "will not pick" in message
    assert "Gestoría Ejemplo Centro;" in message and "Gestoría Ejemplo Centro Sur" in message


def test_exact_name_is_not_a_guess(make_server, get_prospect) -> None:
    pid, name = make_server().resolve("  gestoría   ejemplo centro ")
    assert name == "Gestoría Ejemplo Centro"
    assert pid == get_prospect("Gestoría Ejemplo Centro")["id"]


def test_like_wildcards_in_user_text_are_literal(make_server) -> None:
    server = make_server()
    assert server.resolve("100%")[1] == "Asesores 100% Demo"
    assert server.resolve("mo_Fi")[1] == "Demo_Fiscal Consultores"
    with pytest.raises(ToolError, match="No prospect matches"):
        server.resolve("%%")  # would match every row if it were not escaped
    with pytest.raises(ToolError, match="No prospect matches"):
        server.resolve("__")


def test_postgrest_syntax_characters_are_stripped(make_server, fake) -> None:
    server = make_server()
    fake.requests.clear()
    assert server.resolve("Levante)*,(")[1] == "Despacho Ficticio Levante"
    sent = dict(fake.requests[0]["params"])
    assert sent["nombre"] == "ilike.*Levante*"


def test_too_short_search_does_not_reach_the_backend(make_server, fake) -> None:
    server = make_server()
    fake.requests.clear()
    for bad in ("a", "  ", "**", None, 42):
        with pytest.raises(ToolError):
            server.resolve(bad)
    assert fake.requests == []


def test_escape_helpers() -> None:
    assert ilike_contains(search_term("50%_off")) == "ilike.*50\\%\\_off*"
    with pytest.raises(ToolError):
        search_term("x" * 81)
