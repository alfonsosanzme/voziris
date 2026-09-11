"""Decodificar grabaciones (m4a, mp3, wav…) al formato del pipeline: 16 kHz mono float32.

Se usa PyAV, que lleva ffmpeg dentro: así el paquete portable abre lo que
graba un móvil sin que el usuario instale nada. Son unos 65 MB más de DLL,
que es el precio de no depender de un ffmpeg externo.

Issue: VOZ-70.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from voziris.errores import VozirisError
from voziris.tipos import SAMPLE_RATE, Audio

log = logging.getLogger(__name__)

FORMATOS = (
    ".m4a", ".mp3", ".wav", ".flac", ".ogg", ".opus", ".aac", ".wma", ".mp4", ".webm", ".mkv",
)
"""Extensiones que se ofrecen en el diálogo y en el menú contextual. ffmpeg abre más."""


class ArchivoNoLegible(VozirisError):
    """El archivo no existe, no es audio o está dañado."""


def decodificar(ruta: Path, sr: int = SAMPLE_RATE) -> Audio:
    """Todo el archivo como `Audio` mono a `sr`. Remuestrea y mezcla canales ffmpeg.

    Raises:
        ArchivoNoLegible: no se puede abrir o no trae ninguna pista de audio.
    """
    ruta = Path(ruta)
    if not ruta.is_file():
        raise ArchivoNoLegible(f"No existe {ruta}")
    try:
        import av
    except ImportError as e:  # pragma: no cover - dependencia declarada
        raise ArchivoNoLegible("Falta PyAV, que decodifica el audio") from e

    trozos: list[np.ndarray] = []
    try:
        with av.open(str(ruta)) as contenedor:
            if not contenedor.streams.audio:
                raise ArchivoNoLegible(f"{ruta.name} no tiene ninguna pista de audio")
            flujo = contenedor.streams.audio[0]
            remuestreador = av.AudioResampler(format="flt", layout="mono", rate=sr)
            for cuadro in contenedor.decode(flujo):
                for salida in remuestreador.resample(cuadro):
                    trozos.append(salida.to_ndarray().reshape(-1))
            for salida in remuestreador.resample(None):  # lo que quede en el remuestreador
                trozos.append(salida.to_ndarray().reshape(-1))
    except ArchivoNoLegible:
        raise
    except Exception as e:  # noqa: BLE001 — av.error.* son muchas clases distintas
        raise ArchivoNoLegible(f"No se pudo decodificar {ruta.name}: {e}") from e
    if not trozos:
        raise ArchivoNoLegible(f"{ruta.name} está vacío")
    muestras = np.concatenate(trozos).astype(np.float32, copy=False)
    log.info("decodificado %s: %.1f s", ruta.name, len(muestras) / sr)
    return Audio(muestras=muestras, sr=sr)


def duracion_s(ruta: Path) -> float | None:
    """Duración según la cabecera, sin decodificar. None si no se sabe."""
    try:
        import av

        with av.open(str(ruta)) as contenedor:
            if contenedor.duration is None:
                return None
            return float(contenedor.duration) / av.time_base
    except Exception:  # noqa: BLE001
        return None
