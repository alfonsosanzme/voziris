"""A2 — corte por silencio, solo en modo «clavar».

En modo «mantener» el usuario decide cuándo acaba soltando la tecla; aquí no
hace falta VAD. En modo «clavar» hay que detectar que ha terminado de hablar.

Silero VAD en ONNX es la opción recomendada: acierta más que webrtcvad y son
unos pocos megas. Se sirve con el mismo onnxruntime que ya arrastra el motor
local, así que no añade dependencias nuevas.

Issue: VOZ-20.
"""

from __future__ import annotations

import numpy as np


class DetectorSilencio:
    """Decide si un bloque de audio es voz o silencio."""

    def __init__(self, silencio_corte_ms: int = 1200) -> None:
        self._corte_ms = silencio_corte_ms

    def reiniciar(self) -> None:
        """Se llama al empezar cada dictado: el detector guarda estado."""
        raise NotImplementedError("VOZ-20")

    def alimentar(self, bloque: np.ndarray) -> bool:
        """Procesa un bloque y devuelve True cuando el dictado debe cerrarse.

        Cerrar solo tras `silencio_corte_ms` de silencio CONTINUO. Las pausas
        cortas al pensar son normales y no deben cortar la frase: cortar de más
        es peor que tardar un poco, porque parte el dictado en dos.
        """
        raise NotImplementedError("VOZ-20")
