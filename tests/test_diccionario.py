"""Tests del diccionario personal (VOZ-40).

Veinte frases: diez que deben corregirse y diez que NO deben tocarse. Las
diez negativas son la mitad importante: un umbral flojo destroza texto
correcto.
"""

from __future__ import annotations

import pytest

from voziris.proceso.diccionario import Diccionario, clave_fonetica, normalizar
from voziris.tipos import Contexto, Modo, Nivel, Transcripcion

CTX = Contexto("app_activa", Modo.MANTENER, None, False, Nivel.LITERAL)
PALABRAS = ["Creatics", "Kairis", "Voziris", "Obsidian", "Valladolid", "Mundo Educa"]


def _aplicar(texto: str, palabras: list[str] = PALABRAS) -> str:
    return Diccionario(palabras).aplicar(Transcripcion(texto, "es", "local", 10, 1.0), CTX).texto


def test_claves() -> None:
    assert normalizar("Creátics") == "creatics"
    assert clave_fonetica("Voziris") == clave_fonetica("bociris") == clave_fonetica("vosiris")
    assert clave_fonetica("Kairis") == clave_fonetica("cairis") == clave_fonetica("Kayris")
    assert clave_fonetica("Creatics") == clave_fonetica("kreatiks")


# Diez que deben corregirse: errores reales del motor sobre nombres propios.
POSITIVAS = [
    ("Lo hacemos en creatics mañana.", "Lo hacemos en Creatics mañana."),
    ("Hablé con la gente de Creátics ayer.", "Hablé con la gente de Creatics ayer."),
    ("Una sesión para Cairis sobre dictado.", "Una sesión para Kairis sobre dictado."),
    ("El proyecto Kayris arranca en octubre.", "El proyecto Kairis arranca en octubre."),
    ("Estoy probando bociris con el micro.", "Estoy probando Voziris con el micro."),
    ("Vosiris pega el texto donde está el cursor.", "Voziris pega el texto donde está el cursor."),
    ("Lo apunto en obsidian ahora mismo.", "Lo apunto en Obsidian ahora mismo."),
    ("Vivo en valladolid desde hace años.", "Vivo en Valladolid desde hace años."),
    ("La academia mundo educa abre en septiembre.", "La academia Mundo Educa abre en septiembre."),
    ("Prueba de voziris, kairis y creatix.", "Prueba de Voziris, Kairis y Creatics."),
]

# Diez que NO deben tocarse: palabras corrientes que se parecen.
NEGATIVAS = [
    "La creaticidad no existe, pero la creatividad sí.",
    "Tiene mucho carisma y poca paciencia.",
    "El visir del reino habló con calma.",
    "Es un cráter enorme, casi un valle.",
    "Una crítica constructiva y un cristal roto.",
    "Vamos a crear un vaso de cristal.",
    "Su obsesión con el orden es total.",
    "Kilos de arena y una valla de madera.",
    "El bacilo se ve al microscopio.",
    "Mundo y educación van juntos, pero no son lo mismo.",
]


@pytest.mark.parametrize(("entrada", "esperado"), POSITIVAS)
def test_corrige_nombres_propios(entrada: str, esperado: str) -> None:
    assert _aplicar(entrada) == esperado


@pytest.mark.parametrize("frase", NEGATIVAS)
def test_no_toca_lo_que_esta_bien(frase: str) -> None:
    assert _aplicar(frase) == frase


def test_no_convierte_creaticidad_en_creatics() -> None:
    assert _aplicar("la creaticidad") == "la creaticidad"
    assert _aplicar("creaticsidad") == "creaticsidad"


def test_solo_capitalizacion_cuando_la_palabra_ya_es_correcta() -> None:
    assert _aplicar("kairis") == "Kairis"
    assert _aplicar("Kairis") == "Kairis"
    assert _aplicar("KAIRIS") == "Kairis"


def test_palabras_cortas_no_se_tocan() -> None:
    assert _aplicar("un vaso", ["Vasa"]) == "un vaso"
    assert _aplicar("con", ["Cono"]) == "con"


def test_diccionario_vacio_y_en_caliente() -> None:
    d = Diccionario([])
    t = Transcripcion("creatics", "es", "local", 10, 1.0)
    assert d.aplicar(t, CTX).texto == "creatics"
    d.palabras = ["Creatics", "  ", ""]
    assert d.palabras == ["Creatics"]
    assert d.aplicar(t, CTX).texto == "Creatics"


def test_conserva_puntuacion_y_saltos() -> None:
    assert _aplicar("¿Creatix?\nSí: kairis.") == "¿Creatics?\nSí: Kairis."


def test_un_fallo_interno_no_pierde_el_dictado(monkeypatch: pytest.MonkeyPatch) -> None:
    d = Diccionario(PALABRAS)
    monkeypatch.setattr(d, "corregir_palabra", lambda _p: 1 / 0)
    t = d.aplicar(Transcripcion("intacto texto", "es", "local", 10, 1.0), CTX)
    assert t.texto == "intacto texto" and "Diccionario no aplicado" in t.avisos[0]
