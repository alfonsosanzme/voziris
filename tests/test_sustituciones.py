"""Tests de las sustituciones literales (VOZ-41)."""

from __future__ import annotations

import pytest

from voziris.proceso.sustituciones import Sustituciones
from voziris.tipos import Contexto, Modo, Nivel, Transcripcion

CTX = Contexto("app_activa", Modo.MANTENER, None, False, Nivel.LITERAL)


def _t(texto: str) -> Transcripcion:
    return Transcripcion(texto, "es", "local", 10, 1.0)


def _aplicar(reglas: dict[str, str], texto: str) -> str:
    return Sustituciones(reglas).aplicar(_t(texto), CTX).texto


REGLAS = {"punto y aparte": "\n\n", "nueva línea": "\n", "arroba": "@"}


def test_punto_y_aparte_produce_doble_salto() -> None:
    assert _aplicar(REGLAS, "Hola punto y aparte Adiós") == "Hola\n\nAdiós"


def test_absorbe_la_puntuacion_del_motor() -> None:
    assert _aplicar(REGLAS, "Hola punto y aparte. Adiós") == "Hola\n\nAdiós"
    assert _aplicar(REGLAS, "Hola, punto y aparte, adiós") == "Hola,\n\nadiós"
    assert _aplicar(REGLAS, "Una cosa nueva línea otra") == "Una cosa\notra"


def test_sin_distinguir_mayusculas() -> None:
    assert _aplicar(REGLAS, "Hola Punto Y Aparte adiós") == "Hola\n\nadiós"
    assert _aplicar(REGLAS, "correo ARROBA dominio") == "correo @ dominio"


def test_respeta_limites_de_palabra() -> None:
    assert _aplicar(REGLAS, "arrobado por la noticia") == "arrobado por la noticia"
    assert _aplicar({"punto": "."}, "un puntolimpio") == "un puntolimpio"
    assert _aplicar({"punto": "."}, "final punto") == "final ."


def test_frases_largas_ganan_a_las_cortas() -> None:
    reglas = {"punto": ".", "punto y aparte": "\n\n"}
    assert _aplicar(reglas, "Uno punto y aparte Dos punto") == "Uno\n\nDos ."


def test_una_regla_que_no_encaja_no_altera_nada() -> None:
    texto = "Un texto normal, con comas; y puntos."
    assert _aplicar(REGLAS, texto) == texto
    assert _aplicar({}, texto) == texto
    assert _aplicar({"": "x", "   ": "y"}, texto) == texto


def test_no_hay_regex_en_las_claves() -> None:
    """Los metacaracteres se toman al pie de la letra."""
    assert _aplicar({"a.b": "AB"}, "a.b y acb") == "AB y acb"
    assert _aplicar({"(paréntesis)": "()"}, "abre (paréntesis) cierra") == "abre () cierra"


def test_espacios_multiples_en_la_clave_y_en_el_texto() -> None:
    assert _aplicar({"punto  y aparte": "\n\n"}, "Uno punto y   aparte dos") == "Uno\n\ndos"


def test_reglas_en_caliente() -> None:
    s = Sustituciones({})
    assert s.aplicar(_t("hola arroba"), CTX).texto == "hola arroba"
    s.reglas = {"arroba": "@"}
    assert s.aplicar(_t("hola arroba"), CTX).texto == "hola @"
    assert s.reglas == {"arroba": "@"}


def test_un_fallo_interno_no_pierde_el_dictado(monkeypatch: pytest.MonkeyPatch) -> None:
    s = Sustituciones(REGLAS)

    class Rota:
        def sub(self, *_: object) -> str:
            raise RuntimeError("bum")

    s._patrones = [(Rota(), "x")]  # type: ignore[list-item]
    t = s.aplicar(_t("intacto"), CTX)
    assert t.texto == "intacto" and "Sustituciones no aplicadas" in t.avisos[0]
