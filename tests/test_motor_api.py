"""Tests del motor por API (VOZ-30) y de la comprobación de red.

Con `httpx.MockTransport`: timeout, errores HTTP, JSON vacío, y que la clave
no aparece en ningún mensaje. Contra Groq de verdad solo el test marcado
`red`, que se salta sin GROQ_API_KEY.
"""

from __future__ import annotations

import io
import os
import re
import subprocess
import wave
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pytest

from voziris import red
from voziris.errores import MotorNoDisponible, TranscripcionFallida
from voziris.motores.api import MotorAPI, _proveedor_de, a_wav
from voziris.tipos import SAMPLE_RATE, Audio

RAIZ = Path(__file__).resolve().parents[1]
CLAVE = "gsk_secretisima_1234567890"


@pytest.fixture(autouse=True)
def con_red(monkeypatch: pytest.MonkeyPatch) -> None:
    red.olvidar()
    monkeypatch.setattr(red, "_sondear", lambda host, puerto: True)


def _audio(segundos: float = 1.0) -> Audio:
    t = np.arange(int(SAMPLE_RATE * segundos)) / SAMPLE_RATE
    return Audio(muestras=(0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32))


def _motor(handler: Any, timeout_s: int = 15) -> MotorAPI:
    return MotorAPI(
        "https://api.groq.com/openai/v1/",
        "whisper-large-v3-turbo",
        CLAVE,
        timeout_s,
        transporte=httpx.MockTransport(handler),
    )


# --- utilidades ----------------------------------------------------------------


def test_proveedor_de() -> None:
    assert _proveedor_de("https://api.groq.com/openai/v1") == "groq"
    assert _proveedor_de("https://api.openai.com/v1") == "openai"
    assert _proveedor_de("http://localhost:8000/v1") == "localhost"


def test_a_wav_es_pcm16_mono_16k() -> None:
    datos = a_wav(_audio(0.5))
    assert datos.startswith(b"RIFF")
    with wave.open(io.BytesIO(datos)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, SAMPLE_RATE)
        assert w.getnframes() == SAMPLE_RATE // 2
    assert len(a_wav(_audio(60))) < 2_000_000  # 1,9 MB por minuto: lejos del límite de 25


def test_hay_red_cachea(monkeypatch: pytest.MonkeyPatch) -> None:
    llamadas: list[str] = []

    def sondeo(host: str, puerto: int) -> bool:
        llamadas.append(host)
        return False

    red.olvidar()
    monkeypatch.setattr(red, "_sondear", sondeo)
    assert not red.hay_red("api.groq.com")
    assert not red.hay_red("api.groq.com")
    assert len(llamadas) == 1  # el fallo también se recuerda
    assert red.hay_red("api.groq.com", ttl_s=0) is False and len(llamadas) == 2
    assert red.host_de("https://api.groq.com/openai/v1") == "api.groq.com"
    assert red.puerto_de("http://localhost:8000/v1") == 8000
    assert red.puerto_de("https://api.groq.com/openai/v1") == 443


# --- transcribir --------------------------------------------------------------------


def test_transcribe_con_wav_multipart_e_idioma() -> None:
    visto: dict[str, Any] = {}

    def handler(peticion: httpx.Request) -> httpx.Response:
        visto["url"] = str(peticion.url)
        visto["auth"] = peticion.headers.get("authorization")
        visto["cuerpo"] = peticion.read()
        return httpx.Response(200, json={"text": "  Hola, mundo. "})

    motor = _motor(handler)
    assert motor.nombre == "api:groq"
    assert motor.disponible()
    t = motor.transcribir(_audio(2.0), "es")
    assert t.texto == "Hola, mundo." and t.motor == "api:groq" and t.idioma == "es"
    assert t.duracion_audio_s == 2.0 and t.ms_proceso >= 0
    assert visto["url"] == "https://api.groq.com/openai/v1/audio/transcriptions"
    assert visto["auth"] == f"Bearer {CLAVE}"
    cuerpo = visto["cuerpo"]
    assert b'name="model"' in cuerpo and b"whisper-large-v3-turbo" in cuerpo
    assert b'name="language"' in cuerpo and b"\r\nes\r\n" in cuerpo
    assert b'name="response_format"' in cuerpo and b"json" in cuerpo
    assert b'filename="dictado.wav"' in cuerpo and b"RIFF" in cuerpo


def test_timeout_es_motor_no_disponible() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("se acabó el tiempo")

    with pytest.raises(MotorNoDisponible, match="no respondió en 15 s"):
        _motor(handler).transcribir(_audio(), "es")


def test_error_de_conexion_es_motor_no_disponible() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("getaddrinfo failed")

    with pytest.raises(MotorNoDisponible, match="sin conexión"):
        _motor(handler).transcribir(_audio(), "es")


@pytest.mark.parametrize("codigo", [401, 429, 500])
def test_error_http_es_motor_no_disponible_sin_la_clave(codigo: int) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(codigo, json={"error": {"message": f"clave {CLAVE} rechazada"}})

    with pytest.raises(MotorNoDisponible) as info:
        _motor(handler).transcribir(_audio(), "es")
    mensaje = str(info.value)
    assert str(codigo) in mensaje and "rechazada" in mensaje
    assert CLAVE not in mensaje and CLAVE[:10] not in mensaje


def test_respuesta_vacia_o_rota() -> None:
    motor = _motor(lambda _: httpx.Response(200, json={"text": "   "}))
    with pytest.raises(TranscripcionFallida, match="no se oyó nada"):
        motor.transcribir(_audio(), "es")
    motor = _motor(lambda _: httpx.Response(200, content=b"<html>no json</html>"))
    with pytest.raises(MotorNoDisponible, match="no es JSON"):
        motor.transcribir(_audio(), "es")
    with pytest.raises(TranscripcionFallida, match="vacío"):
        _motor(lambda _: httpx.Response(200)).transcribir(
            Audio(muestras=np.zeros(0, np.float32)), "es"
        )


def test_sin_clave_no_esta_disponible() -> None:
    motor = MotorAPI(
        "https://api.groq.com/openai/v1",
        "m",
        "",
        transporte=httpx.MockTransport(lambda _: httpx.Response(200)),
    )
    assert not motor.disponible()
    with pytest.raises(MotorNoDisponible, match="GROQ_API_KEY"):
        motor.transcribir(_audio(), "es")
    motor.precalentar()  # sin clave: no hace nada ni lanza


def test_sin_red_no_esta_disponible(monkeypatch: pytest.MonkeyPatch) -> None:
    red.olvidar()
    monkeypatch.setattr(red, "_sondear", lambda h, p: False)
    motor = _motor(lambda _: httpx.Response(200, json={"text": "x"}))
    assert not motor.disponible()


def test_precalentar_y_probar_clave() -> None:
    llamadas: list[str] = []

    def handler(peticion: httpx.Request) -> httpx.Response:
        llamadas.append(peticion.url.path)
        if peticion.headers["authorization"].endswith("mala"):
            return httpx.Response(401, json={"error": {"message": "Invalid API Key"}})
        return httpx.Response(200, json={"data": []})

    motor = _motor(handler)
    motor.precalentar()
    assert llamadas == ["/openai/v1/models"]
    assert motor.probar_clave() == (True, "La clave es válida")
    mala = MotorAPI(
        "https://api.groq.com/openai/v1",
        "m",
        "gsk_clave_mala",
        transporte=httpx.MockTransport(handler),
    )
    ok, mensaje = mala.probar_clave()
    assert not ok and "no es válida" in mensaje and "mala" not in mensaje
    motor.cerrar()


def test_el_precalentado_no_lanza_aunque_falle() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    _motor(handler).precalentar()


# --- repositorio limpio de claves ------------------------------------------------------


def test_no_hay_claves_en_el_repositorio() -> None:
    """Criterio de VOZ-30: ninguna clave en el repositorio. Se busca en lo versionado."""
    if not (RAIZ / ".git").exists():
        pytest.skip("sin git")
    archivos = subprocess.run(
        ["git", "ls-files"], cwd=RAIZ, capture_output=True, text=True, check=True
    ).stdout.split()
    patron = re.compile(r"\bgsk_[A-Za-z0-9]{30,}\b|\bsk-[A-Za-z0-9]{30,}\b")
    for nombre in archivos:
        ruta = RAIZ / nombre
        if ruta.suffix in (".ico", ".png", ".wav") or not ruta.exists():
            continue
        contenido = ruta.read_text(encoding="utf-8", errors="ignore")
        assert not patron.search(contenido), f"parece haber una clave en {nombre}"


# --- Groq de verdad -----------------------------------------------------------------------


@pytest.mark.red
def test_groq_real_transcribe_una_muestra(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cuesta ~0,0002 $ (20 s de audio a 0,04 $/hora)."""
    clave = os.environ.get("GROQ_API_KEY", "")
    if not clave:
        pytest.skip("sin GROQ_API_KEY")
    wav = RAIZ / "banco" / "muestra-08.wav"
    if not wav.exists():
        pytest.skip("sin muestra")
    import soundfile as sf

    monkeypatch.undo()  # red real
    red.olvidar()
    datos, sr = sf.read(str(wav), dtype="float32")
    motor = MotorAPI("https://api.groq.com/openai/v1", "whisper-large-v3-turbo", clave)
    assert motor.disponible()
    t = motor.transcribir(Audio(muestras=datos, sr=sr), "es")
    print(f"\nGroq: {t.ms_proceso} ms, RTF {t.rtf:.3f}: {t.texto[:100]}")
    assert "sábado" in t.texto.lower() and t.motor == "api:groq"
    motor.cerrar()


def test_listar_modelos_separa_voz_de_texto() -> None:
    def handler(peticion: httpx.Request) -> httpx.Response:
        assert peticion.url.path.endswith("/models")
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "whisper-large-v3", "active": True},
                    {"id": "whisper-large-v3-turbo", "active": True},
                    {"id": "llama-3.3-70b-versatile", "active": True},
                    {"id": "playai-tts", "active": True},
                    {"id": "modelo-retirado", "active": False},
                    {"sin_id": True},
                ]
            },
        )

    audio, chat = _motor(handler).listar_modelos()
    assert audio == ["whisper-large-v3", "whisper-large-v3-turbo"]
    assert "llama-3.3-70b-versatile" in chat
    # La síntesis de voz no sirve ni para dictar ni para limpiar: fuera de las dos.
    assert "playai-tts" not in audio + chat
    assert "modelo-retirado" not in audio + chat


def test_listar_modelos_degrada_sin_clave_ni_red() -> None:
    sin_clave = MotorAPI("https://api.groq.com/openai/v1", "m", "")
    assert sin_clave.listar_modelos() == ([], [])
    assert _motor(lambda _: httpx.Response(500)).listar_modelos() == ([], [])
    roto = _motor(lambda _: (_ for _ in ()).throw(httpx.ConnectError("nada")))
    assert roto.listar_modelos() == ([], [])


def test_el_diccionario_va_en_el_parametro_prompt() -> None:
    visto: dict[str, Any] = {}

    def handler(peticion: httpx.Request) -> httpx.Response:
        visto["cuerpo"] = peticion.read()
        return httpx.Response(200, json={"text": "Creatics y Kairis."})

    motor = MotorAPI(
        "https://api.groq.com/openai/v1",
        "whisper-large-v3",
        CLAVE,
        transporte=httpx.MockTransport(handler),
        vocabulario=["Creatics", "Kairis", "Voziris"],
    )
    motor.transcribir(_audio(), "es")
    cuerpo = visto["cuerpo"]
    assert b'name="prompt"' in cuerpo
    assert b"Creatics, Kairis, Voziris" in cuerpo


def test_el_prompt_se_recorta_por_el_principio() -> None:
    """Groq solo mira los ULTIMOS 224 tokens: lo que sobra se va por delante."""
    motor = MotorAPI("https://api.groq.com/openai/v1", "m", CLAVE, vocabulario=["x" * 50] * 100)
    campos = motor._campos("es")
    assert len(campos["prompt"]) <= 800
    assert not campos["prompt"].startswith(", ")
    assert (
        MotorAPI("https://api.groq.com/openai/v1", "m", CLAVE)._campos("es").get("prompt") is None
    )


def test_modelos_no_conversacionales_fuera_de_las_dos_listas() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "whisper-large-v3", "active": True},
                    {"id": "openai/gpt-oss-120b", "active": True},
                    {"id": "canopylabs/orpheus-v1-english", "active": True},
                    {"id": "groq/compound", "active": True},
                    {"id": "meta-llama/llama-guard-4-12b", "active": True},
                ]
            },
        )

    audio, chat = _motor(handler).listar_modelos()
    assert audio == ["whisper-large-v3"]
    assert chat == ["openai/gpt-oss-120b"]
