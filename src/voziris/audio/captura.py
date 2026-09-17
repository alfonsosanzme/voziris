"""A3, A5 — captura con búfer previo.

El búfer previo es lo que separa una app usable de una inservible: el
micrófono graba SIEMPRE en un anillo de 500 ms, así que cuando el usuario
pulsa el atajo ya tenemos medio segundo de audio anterior a la pulsación. Sin
esto se pierde la primera sílaba de cada dictado y el usuario aprende a hacer
una pausa antes de hablar, que es justo la fricción que veníamos a quitar.

El anillo está siempre girando mientras la app vive. No se graba a disco.

Normalización de nivel (decisión del hito 0, `docs/H0.md`): Parakeet int8
descarta frases enteras cuando el audio le llega bajo, y las grabaciones
reales del cliente llegan a RMS 0,02. Cada dictado se escala a RMS 0,30 con
recorte duro antes de aplicar `ganancia_db`, que queda como ajuste fino. A
este modelo el recorte no le molesta; el nivel bajo sí.

Issue: VOZ-10 (captura y búfer previo), VOZ-23 (cambio de micrófono).
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from voziris.errores import MicrofonoNoDisponible
from voziris.tipos import SAMPLE_RATE, Audio

BLOQUE = 512
"""Muestras por bloque: 32 ms a 16 kHz, el tamaño que espera Silero VAD (VOZ-20)."""

RMS_OBJETIVO = 0.30
"""Nivel al que se normaliza cada dictado antes del motor, con recorte duro.

Medido en el hito 0 (4 sep 2026) sobre 8 dictados grabados a RMS 0,02:
Parakeet int8 en crudo, WER 30,7 %; normalizado a RMS 0,30 con recorte,
16,7 %. La compresión suave (tanh) dio 18,7 % y la normalización limitada por
pico, 27,9 %, porque la voz tiene un factor de cresta de 11–15 y el limitador
anula la ganancia. Al fp32 le da igual (14,0 → 13,8 %). Ver `docs/H0.md`.
"""

SIN_BLOQUES_S = 1.5
"""Si el callback lleva más de esto sin entregar audio, el micrófono ha muerto.

PortAudio en Windows no siempre avisa cuando se desconecta un dispositivo USB:
el flujo sigue «activo» y simplemente deja de llamar. Este plazo es la única
forma fiable de enterarse.
"""

_SUELO_DB = -60.0
"""Nivel que el indicador muestra como 0. Por debajo es silencio de sala."""


def normalizar(muestras: np.ndarray, rms_objetivo: float = RMS_OBJETIVO) -> np.ndarray:
    """Escala a `rms_objetivo` y recorta a [-1, 1]. Ver `RMS_OBJETIVO`."""
    rms = float(np.sqrt(np.mean(np.square(muestras, dtype=np.float64)))) if len(muestras) else 0.0
    if rms < 1e-6:
        return muestras.astype(np.float32)
    return np.clip(muestras * (rms_objetivo / rms), -1.0, 1.0).astype(np.float32)


def aplicar_ganancia(muestras: np.ndarray, ganancia_db: float) -> np.ndarray:
    """Ganancia en dB con recorte, sin saturar por encima de ±1."""
    if ganancia_db == 0:
        return muestras
    return np.clip(muestras * 10 ** (ganancia_db / 20), -1.0, 1.0).astype(np.float32)


class Captura:
    """Anillo continuo sobre `sounddevice.InputStream`.

    Vida del objeto: se abre al arrancar la aplicación y se cierra al salir.
    Abrir el flujo en cada dictado cuesta cientos de milisegundos y a veces
    falla si otra aplicación tiene el micrófono tomado.

    Hilos: el callback de PortAudio escribe; `empezar_dictado()` y
    `terminar_dictado()` se llaman desde el hilo de teclado y el de trabajo.
    El lock protege solo el anillo y la lista de acumulación, y se sostiene
    unos microsegundos.
    """

    def __init__(
        self,
        dispositivo: str = "",
        ganancia_db: float = 0.0,
        buffer_previo_ms: int = 500,
    ) -> None:
        self._dispositivo = dispositivo
        self._ganancia_db = ganancia_db
        self._buffer_previo_ms = buffer_previo_ms
        self._muestras_previas = int(SAMPLE_RATE * buffer_previo_ms / 1000)
        # El anillo guarda el búfer previo más un bloque de margen, en bloques enteros.
        capacidad = (math.ceil(self._muestras_previas / BLOQUE) + 1) * BLOQUE
        self._anillo = np.zeros(capacidad, dtype=np.float32)
        self._pos = 0
        self._escritas = 0
        self._lock = threading.Lock()
        self._grabando = False
        self._bloques: list[np.ndarray] = []
        self._nivel = 0.0
        self._ultimo_bloque = 0.0
        self._error: str | None = None
        self._stream: Any = None
        self.nombre_dispositivo: str | None = None
        """Nombre del micrófono realmente abierto, para el HUD y los ajustes."""
        self.pendientes: Any = None
        """Un `voziris.pendientes.Pendientes`: si está, cada dictado va a disco según se graba."""
        self._escritor: Any = None
        self.ultimo_pendiente: Path | None = None
        """El archivo del dictado que acaba de devolver `terminar_dictado()` (VOZ-74)."""
        self.oyente_bloques: Callable[[np.ndarray], None] | None = None
        """Recibe cada bloque mientras se graba. Lo usa el VAD (VOZ-20).

        Se llama en el hilo de audio: debe devolver el control al instante
        (encolar con `put_nowait`, nada más).
        """

    # --- vida del flujo ---------------------------------------------------

    def abrir(self) -> str | None:
        """Abre el flujo de entrada a 16 kHz mono float32 y empieza a girar.

        Devuelve un aviso si el micrófono configurado no existe y se ha caído
        al predeterminado, o None si todo fue como se pidió.

        El callback de sounddevice corre en un hilo de audio con plazos
        estrictos: solo copia al anillo. Ninguna otra cosa — nada de logging,
        nada de inferencia, nada de bloqueos.

        Raises:
            MicrofonoNoDisponible: no hay ningún micrófono utilizable.
        """
        import sounddevice as sd

        aviso: str | None = None
        indice: int | None = None
        if self._dispositivo:
            indice = self._indice_de(self._dispositivo)
            if indice is None:
                aviso = f"No encuentro el micrófono «{self._dispositivo}»; uso el predeterminado"
        try:
            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="float32",
                blocksize=BLOQUE,
                device=indice,
                callback=self._callback,
                finished_callback=self._al_terminar_flujo,
            )
            self._stream.start()
        except Exception as e:  # sounddevice lanza PortAudioError o ValueError
            self._stream = None
            raise MicrofonoNoDisponible(f"No se puede abrir el micrófono: {e}") from e
        self._error = None
        self._ultimo_bloque = time.monotonic()
        self.nombre_dispositivo = self._nombre_de(self._stream.device)
        return aviso

    def cerrar(self) -> None:
        stream, self._stream = self._stream, None
        if stream is None:
            return
        try:
            stream.stop()
            stream.close()
        except Exception:  # noqa: BLE001 — al cerrar, nada que hacer con el error
            pass

    @property
    def ganancia_db(self) -> float:
        return self._ganancia_db

    @ganancia_db.setter
    def ganancia_db(self, valor: float) -> None:
        """Desde los ajustes, en caliente. Se aplica al terminar el dictado siguiente."""
        self._ganancia_db = max(-20.0, min(20.0, float(valor)))

    def cambiar_dispositivo(self, dispositivo: str) -> str | None:
        """A5 — cambia de micrófono sin reiniciar. Nunca durante un dictado."""
        self._dispositivo = dispositivo
        self.cerrar()
        return self.abrir()

    @staticmethod
    def dispositivos() -> list[tuple[int, str]]:
        """A5 — micrófonos disponibles, como (índice, nombre), para los ajustes.

        Solo los del API de sonido predeterminado: Windows expone cada
        micrófono tres veces (MME, DirectSound, WASAPI) y la lista sería
        confusa.
        """
        import sounddevice as sd

        hostapi = sd.query_hostapis(sd.default.hostapi)
        salida: list[tuple[int, str]] = []
        for indice in hostapi["devices"]:
            info = sd.query_devices(indice)
            if info["max_input_channels"] > 0:
                salida.append((int(indice), str(info["name"])))
        return salida

    def _indice_de(self, nombre: str) -> int | None:
        objetivo = nombre.strip().casefold()
        for indice, candidato in self.dispositivos():
            if objetivo in candidato.casefold():
                return indice
        return None

    @staticmethod
    def _nombre_de(indice: object) -> str | None:
        import sounddevice as sd

        try:
            return str(sd.query_devices(indice)["name"])
        except Exception:  # noqa: BLE001 — solo es una etiqueta
            return None

    # --- callback de audio (hilo de PortAudio) ------------------------------

    def _callback(self, indata: np.ndarray, frames: int, _tiempo: object, _estado: object) -> None:
        try:
            bloque = indata[:, 0]
            n = len(bloque)
            with self._lock:
                fin = self._pos + n
                capacidad = len(self._anillo)
                if fin <= capacidad:
                    self._anillo[self._pos : fin] = bloque
                else:
                    corte = capacidad - self._pos
                    self._anillo[self._pos :] = bloque[:corte]
                    self._anillo[: fin - capacidad] = bloque[corte:]
                self._pos = fin % capacidad
                self._escritas += n
                if self._grabando:
                    copia = bloque.copy()
                    self._bloques.append(copia)
                    if self._escritor is not None:
                        self._escritor.escribir(copia)  # solo encola
            self._nivel = float(np.sqrt(np.mean(np.square(bloque))))
            self._ultimo_bloque = time.monotonic()
            oyente = self.oyente_bloques
            if oyente is not None and self._grabando:
                oyente(bloque.copy())
        except Exception as e:  # noqa: BLE001 — una excepción aquí mata el flujo
            self._error = f"fallo en el callback de audio: {e}"

    def _al_terminar_flujo(self) -> None:
        """PortAudio avisa de que el flujo se detuvo por su cuenta."""
        if self._stream is not None:
            self._error = "el micrófono dejó de entregar audio"

    # --- dictado -----------------------------------------------------------

    def empezar_dictado(self) -> None:
        """Marca el inicio: a partir de aquí se acumula, con el anillo por delante.

        Cuesta lo que cuesta copiar medio segundo de audio (32 KB): se puede
        llamar desde el hilo del hook de teclado.
        """
        escritor = self.pendientes.abrir() if self.pendientes is not None else None
        with self._lock:
            previo = self._previo()
            self._bloques = [previo]
            if escritor is not None:
                escritor.escribir(previo)
            self._escritor = escritor
            self._grabando = True

    def _previo(self) -> np.ndarray:
        """Las últimas `buffer_previo_ms` del anillo, en orden. Con el lock tomado."""
        n = min(self._muestras_previas, self._escritas)
        if n == 0:
            return np.zeros(0, dtype=np.float32)
        inicio = (self._pos - n) % len(self._anillo)
        if inicio + n <= len(self._anillo):
            return self._anillo[inicio : inicio + n].copy()
        resto = (inicio + n) % len(self._anillo)
        return np.concatenate((self._anillo[inicio:], self._anillo[:resto]))

    def terminar_dictado(self) -> Audio:
        """Cierra la acumulación y devuelve búfer previo + dictado, ya tratado.

        El audio se normaliza a `RMS_OBJETIVO` con recorte y después se aplica
        `ganancia_db`. Ver la nota del módulo: al motor le va mejor así.

        Raises:
            MicrofonoNoDisponible: el micrófono desapareció durante la grabación
                (unos auriculares USB desconectados). El dictado se descarta.
        """
        with self._lock:
            self._grabando = False
            bloques, self._bloques = self._bloques, []
            escritor, self._escritor = self._escritor, None
        self.ultimo_pendiente = escritor.cerrar() if escritor is not None else None
        self.comprobar()
        muestras = np.concatenate(bloques) if bloques else np.zeros(0, dtype=np.float32)
        muestras = aplicar_ganancia(normalizar(muestras), self._ganancia_db)
        return Audio(muestras=muestras)

    def cancelar_dictado(self) -> None:
        """Descarta lo acumulado sin devolver nada."""
        with self._lock:
            self._grabando = False
            self._bloques = []
            escritor, self._escritor = self._escritor, None
        if escritor is not None:
            escritor.descartar()

    @property
    def grabando(self) -> bool:
        return self._grabando

    def comprobar(self) -> None:
        """Lanza `MicrofonoNoDisponible` si el flujo ha muerto en silencio."""
        if self._stream is None:
            return
        if self._error:
            raise MicrofonoNoDisponible(self._error)
        if time.monotonic() - self._ultimo_bloque > SIN_BLOQUES_S:
            raise MicrofonoNoDisponible(
                f"el micrófono lleva más de {SIN_BLOQUES_S:g} s sin entregar audio"
            )

    # --- indicador -----------------------------------------------------------

    def nivel_actual(self) -> float:
        """A4 — nivel para el indicador, en 0..1. Se consulta ~30 veces por segundo.

        Es el RMS del último bloque en escala de decibelios entre −60 dBFS (0)
        y 0 dBFS (1): así una voz normal, que ronda −30 dBFS, queda a media
        barra y el silencio de sala cerca de cero. Solo lee un float: no toca
        el lock ni afecta a la captura.
        """
        if self._nivel <= 0:
            return 0.0
        db = 20 * math.log10(self._nivel)
        return min(1.0, max(0.0, (db - _SUELO_DB) / -_SUELO_DB))
