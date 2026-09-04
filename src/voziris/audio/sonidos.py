"""A6 — tres tonos cortos: inicio, fin y error.

Para no tener que mirar la pantalla. Breves (< 120 ms), discretos y
distinguibles entre sí también a volumen bajo:

    inicio   barrido ascendente 440 → 660 Hz, 90 ms
    fin      barrido descendente 660 → 440 Hz, 90 ms
    error    220 Hz con su segundo armónico, 110 ms: más grave y más largo

Se sintetizan una vez al arrancar, con envolvente para que no chasqueen. No
se distribuyen archivos de audio: así no engordan el paquete y no hay dudas
de licencia.

Reproducir no bloquea: cada tono se escribe en un flujo de salida propio
(independiente del de captura) desde un hilo efímero. El flujo se abre la
primera vez y se queda abierto: abrirlo cuesta ~100 ms y el tono de inicio
tiene que sonar cuando se pulsa, no después.

Issue: VOZ-21.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

SR_SALIDA = 44_100
"""Frecuencia de la salida. La mayoría de tarjetas la aceptan sin resamplear."""

AMPLITUD = 0.25
"""Pico de los tonos: discretos, no un aviso de error de Windows."""

ATAQUE_MS = 5
CAIDA_MS = 20


def _envolvente(n: int) -> np.ndarray:
    """Sube en `ATAQUE_MS` y baja en `CAIDA_MS`, con medio coseno: sin chasquidos."""
    ataque = int(SR_SALIDA * ATAQUE_MS / 1000)
    caida = int(SR_SALIDA * CAIDA_MS / 1000)
    env = np.ones(n, dtype=np.float32)
    env[:ataque] = 0.5 - 0.5 * np.cos(np.linspace(0, np.pi, ataque))
    env[n - caida :] = 0.5 + 0.5 * np.cos(np.linspace(0, np.pi, caida))
    return env


def barrido(f_inicio: float, f_fin: float, ms: int) -> np.ndarray:
    """Un tono cuya frecuencia va de `f_inicio` a `f_fin` en `ms` milisegundos."""
    n = int(SR_SALIDA * ms / 1000)
    frecuencia = np.linspace(f_inicio, f_fin, n)
    fase = 2 * np.pi * np.cumsum(frecuencia) / SR_SALIDA
    onda: np.ndarray = np.asarray(AMPLITUD * np.sin(fase) * _envolvente(n), dtype=np.float32)
    return onda


def tono_error(ms: int = 110) -> np.ndarray:
    """220 Hz más su octava, a la mitad: grave y reconocible a poco volumen."""
    n = int(SR_SALIDA * ms / 1000)
    t = np.arange(n) / SR_SALIDA
    mezcla = np.sin(2 * np.pi * 220 * t) + 0.5 * np.sin(2 * np.pi * 440 * t)
    onda: np.ndarray = np.asarray(AMPLITUD / 1.5 * mezcla * _envolvente(n), dtype=np.float32)
    return onda


class Sonidos:
    def __init__(self, activos: bool = True) -> None:
        self._activos = activos
        self._inicio = barrido(440, 660, 90)
        self._fin = barrido(660, 440, 90)
        self._error = tono_error(110)
        self._avisado = False
        self._flujo: Any = None
        self._lock = threading.Lock()

    def precalentar(self) -> None:
        """Abre el flujo de salida ya, para que el primer tono no llegue tarde."""
        if self._activos:
            threading.Thread(target=self._abrir, name="voziris-sonidos", daemon=True).start()

    def cerrar(self) -> None:
        with self._lock:
            flujo, self._flujo = self._flujo, None
        if flujo is not None:
            try:
                flujo.stop()
                flujo.close()
            except Exception:  # noqa: BLE001 — al cerrar, nada que hacer con el error
                pass

    def _abrir(self) -> Any:
        """Con el lock tomado por quien llama, o desde precalentar (que lo toma)."""
        import sounddevice as sd

        with self._lock:
            if self._flujo is None:
                flujo = sd.OutputStream(samplerate=SR_SALIDA, channels=1, dtype="float32")
                flujo.start()
                self._flujo = flujo
            return self._flujo

    @property
    def activos(self) -> bool:
        return self._activos

    @activos.setter
    def activos(self, valor: bool) -> None:
        """`sonidos = false` en el TOML, o desde los ajustes en caliente."""
        self._activos = valor

    def inicio(self) -> None:
        """Tono ascendente. No bloquea."""
        self._reproducir(self._inicio)

    def fin(self) -> None:
        """Tono descendente."""
        self._reproducir(self._fin)

    def error(self) -> None:
        """Tono grave: algo no salió (dictado ignorado, entrega fallida)."""
        self._reproducir(self._error)

    def _reproducir(self, onda: np.ndarray) -> None:
        if not self._activos:
            return
        threading.Thread(target=self._tocar, args=(onda,), daemon=True).start()

    def _tocar(self, onda: np.ndarray) -> None:
        """En su hilo: `write()` bloquea lo que dura el tono."""
        try:
            flujo = self._abrir()
            with self._lock:
                flujo.write(onda)
        except Exception as e:  # noqa: BLE001 — sin salida de audio, se dicta igual
            if not self._avisado:
                self._avisado = True
                log.warning("no se pueden reproducir los tonos: %s", e)
