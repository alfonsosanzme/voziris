"""Tests del análisis de combinaciones de teclas. Sin Windows: corren en CI."""

from __future__ import annotations

import pytest

from voziris import teclas


def test_normaliza_orden_y_mayusculas() -> None:
    c = teclas.analizar("Space + Shift + CTRL")
    assert c.teclas == ("ctrl", "shift", "space")
    assert c.texto == "ctrl+shift+space"
    assert c.tecla == "space"
    assert c.modificadores == {"ctrl", "shift"}


def test_solo_modificadores_es_valido() -> None:
    """`ctrl+win` es el atajo «mantener» por defecto: no lleva tecla normal."""
    c = teclas.analizar("ctrl+win")
    assert c.tecla is None
    assert c.codigos == (teclas.VK["ctrl"], teclas.VK["win"])


def test_alias() -> None:
    assert teclas.analizar("control+espacio").texto == "ctrl+space"
    assert teclas.analizar("escape").texto == "esc"
    assert teclas.analizar("Windows+Intro").texto == "win+enter"


@pytest.mark.parametrize(
    ("texto", "fragmento"),
    [
        ("", "no es una combinación"),
        ("ctrl+", "no es una combinación"),
        ("ctrl+patata", "«patata» no es una tecla conocida"),
        ("ctrl+ctrl", "repite la tecla «ctrl»"),
        ("ctrl+a+b", "más de una tecla que no es modificador"),
    ],
)
def test_errores_dicen_que_esta_mal(texto: str, fragmento: str) -> None:
    with pytest.raises(ValueError, match=fragmento):
        teclas.analizar(texto)


def test_todos_los_nombres_tienen_codigo() -> None:
    for nombre in teclas.NOMBRES:
        assert teclas.VK[nombre], nombre
    assert teclas.VK["f12"] == {0x7B}
    assert teclas.VK["a"] == {0x41}
    assert teclas.VK["numpad5"] == {0x65}
