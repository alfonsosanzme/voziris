"""Tests de las señales acústicas (VOZ-21). Síntesis pura; la reproducción real, marcada `audio`."""

from __future__ import annotations

import time
from typing import Any

import numpy as np
import pytest

from voziris.audio import sonidos as mod
from voziris.audio.sonidos import SR_SALIDA, Sonidos, barrido, tono_error


def _frecuencia_media(onda: np.ndarray) -> float:
    """Por cruces por cero: sirve para saber si sube o baja."""
    cruces = np.count_nonzero(np.diff(np.signbit(onda)))
    return cruces / 2 / (len(onda) / SR_SALIDA)


def test_los_tres_tonos_duran_menos_de_120_ms() -> None:
    s = Sonidos()
    for onda in (s._inicio, s._fin, s._error):
        assert len(onda) / SR_SALIDA < 0.120
        assert onda.dtype == np.float32
        assert np.abs(onda).max() <= 0.26  # discretos


def test_inicio_sube_fin_baja_error_grave() -> None:
    s = Sonidos()
    mitad = len(s._inicio) // 2
    assert _frecuencia_media(s._inicio[:mitad]) < _frecuencia_media(s._inicio[mitad:])
    assert _frecuencia_media(s._fin[:mitad]) > _frecuencia_media(s._fin[mitad:])
    assert _frecuencia_media(s._error) < _frecuencia_media(s._inicio)
    assert len(s._error) > len(s._inicio)  # también más largo: se distingue a poco volumen


def test_envolvente_sin_chasquidos() -> None:
    onda = barrido(440, 660, 90)
    assert abs(onda[0]) < 1e-6 and abs(onda[-1]) < 1e-3
    assert np.abs(onda[:50]).max() < 0.05  # arranca suave
    error = tono_error()
    assert abs(error[0]) < 1e-6 and abs(error[-1]) < 1e-3


class FlujoFalso:
    escritos: list[np.ndarray] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def close(self) -> None: ...

    def write(self, onda: np.ndarray) -> None:
        FlujoFalso.escritos.append(onda)


def _esperar_hilos() -> None:
    import threading

    for h in threading.enumerate():
        if h is not threading.current_thread() and h.name.startswith(("Thread-", "voziris-son")):
            h.join(timeout=2)


def test_desactivados_no_reproducen(monkeypatch: pytest.MonkeyPatch) -> None:
    import sounddevice as sd

    FlujoFalso.escritos.clear()
    monkeypatch.setattr(sd, "OutputStream", FlujoFalso)
    s = Sonidos(activos=False)
    s.inicio()
    s.fin()
    s.error()
    _esperar_hilos()
    assert FlujoFalso.escritos == []
    s.activos = True
    s.inicio()
    _esperar_hilos()
    assert len(FlujoFalso.escritos) == 1 and len(FlujoFalso.escritos[0]) == len(s._inicio)
    assert s._flujo.kwargs["samplerate"] == SR_SALIDA
    s.cerrar()
    assert s._flujo is None


def test_sin_salida_de_audio_no_revienta(monkeypatch: pytest.MonkeyPatch, caplog: Any) -> None:
    import sounddevice as sd

    def revienta(**_: Any) -> None:
        raise sd.PortAudioError("no output device")

    monkeypatch.setattr(sd, "OutputStream", revienta)
    s = Sonidos()
    s.inicio()
    _esperar_hilos()
    s.fin()
    _esperar_hilos()
    assert sum("tonos" in r.message for r in caplog.records) == 1  # avisa una vez


@pytest.mark.audio
def test_reproduccion_real_no_bloquea() -> None:
    import sounddevice as sd

    try:
        if sd.query_devices(kind="output")["max_output_channels"] < 1:
            pytest.skip("sin salida de audio")
    except Exception:  # noqa: BLE001
        pytest.skip("sin salida de audio")
    s = Sonidos()
    s.precalentar()
    time.sleep(0.3)
    t0 = time.perf_counter()
    s.inicio()
    assert time.perf_counter() - t0 < 0.02  # devuelve antes de que suene entero
    time.sleep(0.15)
    s.fin()
    time.sleep(0.15)
    s.error()
    time.sleep(0.2)
    s.cerrar()
    assert mod.AMPLITUD == 0.25
