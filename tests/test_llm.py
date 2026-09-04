"""Tests de la limpieza con LLM (VOZ-42).

Con `httpx.MockTransport`: qué se manda, qué se acepta, qué se rechaza por
sospechoso, y que ningún fallo pierde el dictado. La verificación en
castellano con dictados reales es `tools/probar_llm.py`, con la clave del
cliente.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from voziris.proceso import llm as mod
from voziris.proceso.llm import LimpiezaLLM, es_sospechosa
from voziris.tipos import Contexto, Modo, Nivel, Transcripcion

CLAVE = "gsk_secreta_1234567890abcdef"


def _ctx(nivel: Nivel = Nivel.LIMPIO, hay_red: bool = True) -> Contexto:
    return Contexto("app_activa", Modo.MANTENER, None, hay_red, nivel)


def _t(texto: str) -> Transcripcion:
    return Transcripcion(texto, "es", "local", 10, 5.0)


def _llm(handler: Any, modelo: str = "llama-3.3-70b-versatile", clave: str = CLAVE) -> LimpiezaLLM:
    return LimpiezaLLM(
        "https://api.groq.com/openai/v1", modelo, clave, transporte=httpx.MockTransport(handler)
    )


def _respuesta(texto: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": texto}}]})


# --- llamada -------------------------------------------------------------------


def test_literal_no_llama_a_nada() -> None:
    llamado = []
    llm = _llm(lambda r: llamado.append(r) or _respuesta("x"))
    t = llm.aplicar(_t("eh, hola"), _ctx(Nivel.LITERAL))
    assert t.texto == "eh, hola" and llamado == [] and t.avisos == []


def test_limpio_manda_el_prompt_y_devuelve_el_texto_limpio() -> None:
    visto: dict[str, Any] = {}

    def handler(peticion: httpx.Request) -> httpx.Response:
        visto["url"] = str(peticion.url)
        visto["cuerpo"] = json.loads(peticion.read())
        visto["auth"] = peticion.headers["authorization"]
        return _respuesta("Nos vemos el jueves.")

    t = _llm(handler).aplicar(_t("eh, nos vemos el martes, no, el jueves"), _ctx())
    assert t.texto == "Nos vemos el jueves." and t.avisos == []
    assert visto["url"].endswith("/chat/completions")
    assert visto["auth"] == f"Bearer {CLAVE}"
    cuerpo = visto["cuerpo"]
    assert cuerpo["model"] == "llama-3.3-70b-versatile" and cuerpo["temperature"] == 0
    assert cuerpo["messages"][0]["role"] == "system"
    assert "muletillas" in cuerpo["messages"][0]["content"].lower()
    assert cuerpo["messages"][1] == {
        "role": "user",
        "content": "eh, nos vemos el martes, no, el jueves",
    }
    assert cuerpo["max_tokens"] >= 64


def test_reescritura_usa_el_otro_prompt() -> None:
    visto: dict[str, Any] = {}

    def handler(peticion: httpx.Request) -> httpx.Response:
        visto["sistema"] = json.loads(peticion.read())["messages"][0]["content"]
        return _respuesta("- uno\n- dos")

    t = _llm(handler).aplicar(_t("primero uno segundo dos"), _ctx(Nivel.REESCRITURA))
    assert t.texto == "- uno\n- dos"
    assert "formato" in visto["sistema"].lower() and "no añadas" in visto["sistema"].lower()


def test_quita_comillas_y_prefijos_que_el_modelo_anade() -> None:
    llm = _llm(lambda _: _respuesta('"Hola, mundo."'))
    assert llm.aplicar(_t("hola mundo"), _ctx()).texto == "Hola, mundo."
    llm = _llm(lambda _: _respuesta("Texto corregido: Hola, mundo."))
    assert llm.aplicar(_t("hola mundo"), _ctx()).texto == "Hola, mundo."


# --- degradación --------------------------------------------------------------------


@pytest.mark.parametrize(
    "handler",
    [
        lambda _: (_ for _ in ()).throw(httpx.ReadTimeout("tarde")),
        lambda _: (_ for _ in ()).throw(httpx.ConnectError("sin red")),
        lambda _: httpx.Response(429, json={"error": {"message": "rate limit"}}),
        lambda _: httpx.Response(200, content=b"no json"),
        lambda _: httpx.Response(200, json={"choices": []}),
        lambda _: _respuesta("   "),
    ],
)
def test_cualquier_fallo_devuelve_el_texto_de_entrada_con_aviso(handler: Any) -> None:
    t = _llm(handler).aplicar(_t("texto original"), _ctx())
    assert t.texto == "texto original"
    assert len(t.avisos) == 1 and "sin limpiar" in t.avisos[0]
    assert CLAVE not in t.avisos[0]


def test_sin_modelo_o_sin_clave_no_hace_nada() -> None:
    t = _llm(lambda _: _respuesta("x"), modelo="").aplicar(_t("hola"), _ctx())
    assert t.texto == "hola" and t.avisos == []
    t = _llm(lambda _: _respuesta("x"), clave="").aplicar(_t("hola"), _ctx())
    assert t.texto == "hola" and any("clave" in a for a in t.avisos)


def test_sin_red_no_llama() -> None:
    llamado = []
    llm = _llm(lambda r: llamado.append(r) or _respuesta("x"))
    t = llm.aplicar(_t("hola"), _ctx(hay_red=False))
    assert t.texto == "hola" and llamado == [] and any("Sin red" in a for a in t.avisos)


def test_el_timeout_es_corto() -> None:
    assert mod.TIMEOUT_S <= 8


# --- no inventa contenido -------------------------------------------------------------


def test_rechaza_una_salida_que_anade_contenido() -> None:
    entrada = "recuérdame llamar a la gestoría mañana"
    inventada = (
        "Recuérdame llamar a la gestoría mañana a las diez para preguntar por el modelo 303 "
        "y pedir cita con el asesor fiscal."
    )
    assert es_sospechosa(entrada, inventada)
    t = _llm(lambda _: _respuesta(inventada)).aplicar(_t(entrada), _ctx())
    assert t.texto == entrada and any("alteró" in a for a in t.avisos)


def test_acepta_limpieza_normal() -> None:
    entrada = "eh, bueno, recuérdame llamar a la gestoría, o sea, mañana por la mañana"
    limpia = "Recuérdame llamar a la gestoría mañana por la mañana."
    assert not es_sospechosa(entrada, limpia)
    entrada2 = "primero compramos pan, segundo, eh, vamos al banco, tercero volvemos"
    lista = "- Compramos pan\n- Vamos al banco\n- Volvemos"
    assert not es_sospechosa(entrada2, lista)


def test_rechaza_palabras_nuevas_aunque_la_longitud_cuadre() -> None:
    entrada = "el informe del martes queda pendiente hasta que llegue la factura"
    cambiada = "el resumen del viernes queda aprobado hasta que llegue la nómina"
    assert es_sospechosa(entrada, cambiada)


def test_rechaza_salida_demasiado_corta() -> None:
    entrada = "esto es un dictado largo con muchas palabras que no deberían desaparecer así"
    assert es_sospechosa(entrada, "Esto es un dictado.")


def test_pide_el_minimo_de_razonamiento_y_lo_esconde() -> None:
    """Los gpt-oss piensan antes de responder; ese pensamiento no puede pegarse."""
    visto: dict[str, Any] = {}

    def handler(peticion: httpx.Request) -> httpx.Response:
        visto["cuerpo"] = json.loads(peticion.read())
        return _respuesta("Nos vemos el jueves.")

    _llm(handler, modelo="openai/gpt-oss-120b").aplicar(_t("eh, el jueves"), _ctx())
    assert visto["cuerpo"]["reasoning_effort"] == "low"
    assert visto["cuerpo"]["reasoning_format"] == "hidden"


def test_se_descarta_el_razonamiento_que_se_cuele_en_el_texto() -> None:
    salida = (
        "<think>El usuario dice martes y luego jueves, me quedo con jueves.</think>\nEl jueves."
    )
    t = _llm(lambda _: _respuesta(salida)).aplicar(_t("el martes, no, el jueves"), _ctx())
    assert t.texto == "El jueves."
    assert "think" not in t.texto
