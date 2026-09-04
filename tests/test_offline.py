"""VOZ-32 — modo offline verificable.

Un «monitor de red» dentro del proceso: se sustituye `socket.socket.connect`
(y `create_connection`) por una versión que apunta cada intento y lo
rechaza. Con `motor = "local"` y `nivel = "literal"`, diez dictados con el
motor real no deben provocar ni un intento. Con `motor = "api"` sí debe
haberlo (para demostrar que el monitor ve algo), y el dictado cae al local.
"""

from __future__ import annotations

import re
import socket
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from voziris import red
from voziris.motores.api import MotorAPI
from voziris.motores.local import MotorLocal
from voziris.motores.selector import Selector
from voziris.orquestador import Orquestador
from voziris.tipos import SAMPLE_RATE, Audio, Contexto, Entrega, Nivel

RAIZ = Path(__file__).resolve().parents[1]


class MonitorDeRed:
    def __init__(self) -> None:
        self.intentos: list[str] = []


@pytest.fixture
def sin_red(monkeypatch: pytest.MonkeyPatch) -> Iterator[MonitorDeRed]:
    """Apunta y rechaza cualquier conexión saliente del proceso."""
    monitor = MonitorDeRed()

    def connect(self: socket.socket, direccion: Any, *_: Any) -> None:
        monitor.intentos.append(str(direccion))
        raise OSError("red bloqueada por el test")

    def create_connection(direccion: Any, *_: Any, **__: Any) -> None:
        monitor.intentos.append(str(direccion))
        raise OSError("red bloqueada por el test")

    def connect_ex(self: socket.socket, direccion: Any) -> int:
        monitor.intentos.append(str(direccion))
        return 1

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "create_connection", create_connection)
    red.olvidar()
    yield monitor
    red.olvidar()


class DestinoNulo:
    nombre = "app_activa"

    def __init__(self) -> None:
        self.textos: list[str] = []

    def entregar(self, texto: str, ctx: Contexto) -> Entrega:
        self.textos.append(texto)
        return Entrega(ok=True, detalle="guardado")


class SinCaptura:
    oyente_bloques: Any = None

    def empezar_dictado(self) -> None: ...

    def terminar_dictado(self) -> Audio:
        return Audio(muestras=np.zeros(SAMPLE_RATE, np.float32))

    def cancelar_dictado(self) -> None: ...


def _muestras(n: int) -> list[Audio]:
    wavs = sorted((RAIZ / "banco").glob("muestra-*.wav"))
    if not wavs:
        pytest.skip("sin muestras de audio")
    import soundfile as sf

    from voziris.audio.captura import normalizar

    audios = []
    for wav in wavs:
        datos, _ = sf.read(str(wav), dtype="float32")
        audios.append(Audio(muestras=normalizar(datos[: SAMPLE_RATE * 8])))
    return [audios[i % len(audios)] for i in range(n)]


def _modelo() -> Path:
    carpeta = RAIZ / "modelos"
    if not (carpeta / "nemo-parakeet-tdt-0.6b-v3-int8").exists():
        pytest.skip("modelo no descargado")
    return carpeta


@pytest.mark.modelo
def test_local_literal_diez_dictados_sin_un_solo_paquete(sin_red: MonitorDeRed) -> None:
    local = MotorLocal("nemo-parakeet-tdt-0.6b-v3", _modelo())
    api = MotorAPI("https://api.groq.com/openai/v1", "whisper-large-v3-turbo", "gsk_no_se_usa")
    selector = Selector("local", local, api)
    destino = DestinoNulo()
    orq = Orquestador(SinCaptura(), selector, {"app_activa": destino}, [], nivel=Nivel.LITERAL)
    orq.arrancar()
    try:
        assert orq.motor_listo.wait(120)
        for audio in _muestras(10):
            r = orq.dictar_audio(audio, "app_activa")
            assert r["entregado"] and r["motor"] == "local"
    finally:
        orq.parar()
    assert len(destino.textos) == 10
    assert sin_red.intentos == [], sin_red.intentos


@pytest.mark.modelo
def test_con_motor_api_el_monitor_si_ve_el_intento_y_cae_al_local(sin_red: MonitorDeRed) -> None:
    local = MotorLocal("nemo-parakeet-tdt-0.6b-v3", _modelo())
    api = MotorAPI("https://api.groq.com/openai/v1", "whisper-large-v3-turbo", "gsk_no_se_usa")
    selector = Selector("api", local, api)
    orq = Orquestador(SinCaptura(), selector, {"app_activa": DestinoNulo()}, [])
    orq.arrancar()
    try:
        assert orq.motor_listo.wait(120)
        r = orq.dictar_audio(_muestras(1)[0], "app_activa")
    finally:
        orq.parar()
    assert r["motor"] == "local" and any("transcrito en local" in a for a in r["avisos"])
    assert any("api.groq.com" in i for i in sin_red.intentos), sin_red.intentos


def test_sin_telemetria_en_el_codigo() -> None:
    """Ni anónima ni opcional: ninguna biblioteca ni endpoint de telemetría en src/."""
    sospechosos = re.compile(
        r"\b(sentry|posthog|mixpanel|amplitude|telemetry|analytics|crashlytics|bugsnag)\b", re.I
    )
    for archivo in (RAIZ / "src").rglob("*.py"):
        texto = archivo.read_text(encoding="utf-8")
        for numero, linea in enumerate(texto.splitlines(), 1):
            if sospechosos.search(linea):
                raise AssertionError(f"{archivo.name}:{numero}: {linea.strip()}")


def test_el_readme_dice_que_sale_del_equipo() -> None:
    readme = (RAIZ / "README.md").read_text(encoding="utf-8")
    assert "## Qué sale de tu equipo" in readme
    for frase in ('motor = "local"', 'nivel = "literal"', "**nada**", "Hugging Face", "telemetría"):
        assert frase in readme, frase
