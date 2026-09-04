"""Tipos compartidos por todo el pipeline.

Este módulo no depende de nada del proyecto: es la base sobre la que se
construyen los tres protocolos (MotorSTT, PostProceso, Destino).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

import numpy as np

SAMPLE_RATE = 16_000
"""Frecuencia de muestreo de todo el pipeline. Parakeet exige 16 kHz mono."""


class Modo(StrEnum):
    """Cómo se ha iniciado el dictado. Determina cuándo se corta."""

    MANTENER = "mantener"   # A1: mientras la combinación siga pulsada
    CLAVAR = "clavar"       # A2: hasta volver a pulsar o hasta silencio (VAD)


class Nivel(StrEnum):
    """C4 — cuánto interviene el post-proceso sobre lo transcrito."""

    LITERAL = "literal"          # solo diccionario y sustituciones
    LIMPIO = "limpio"            # + muletillas y autocorrecciones (C2)
    REESCRITURA = "reescritura"  # + formato y estructura (C3)


@dataclass(frozen=True)
class Audio:
    """Un fragmento de audio capturado, ya normalizado."""

    muestras: np.ndarray   # float32 mono en [-1, 1]
    sr: int = SAMPLE_RATE

    @property
    def duracion_s(self) -> float:
        return len(self.muestras) / self.sr


@dataclass
class Transcripcion:
    """Resultado de un motor, y unidad que atraviesa el post-proceso.

    Cada PostProceso devuelve una Transcripcion nueva o esta misma con el
    texto cambiado; `avisos` va acumulando lo que no se pudo hacer, para que
    la interfaz lo muestre sin que nada haya lanzado una excepción.
    """

    texto: str
    idioma: str
    motor: str                # "local" | "api:groq"
    ms_proceso: int
    duracion_audio_s: float
    avisos: list[str] = field(default_factory=list)

    @property
    def rtf(self) -> float:
        """Real-time factor: segundos de cómputo por segundo de audio.

        Menor es mejor. 0,033 significa 30 veces más rápido que tiempo real.
        Es la métrica que decide si el motor local sirve (ver H0).
        """
        if self.duracion_audio_s <= 0:
            return 0.0
        return (self.ms_proceso / 1000) / self.duracion_audio_s


@dataclass(frozen=True)
class Contexto:
    """Lo que se sabe en el momento de entregar el texto."""

    destino: str              # "app_activa" | "markdown"
    modo: Modo
    app_activa: str | None    # ejecutable en primer plano, p. ej. "chrome.exe"
    hay_red: bool
    nivel: Nivel


@dataclass
class Entrega:
    """Qué pasó al intentar entregar el texto en su destino."""

    ok: bool
    detalle: str              # "pegado en chrome.exe" | "añadido a entrada.md"


@dataclass
class EntradaHistorial:
    """D3 — un dictado terminado, reintentable."""

    momento: datetime
    texto: str
    motor: str
    destino: str
    entregado: bool
    ms_total: int
    duracion_audio_s: float
    indice: int = 0
    """Lo asigna el historial al registrar: es lo que identifica la entrada en el menú."""
    audio: str | None = None
    """Nombre del WAV junto al historial, si `guardar_audio` estaba activo."""
