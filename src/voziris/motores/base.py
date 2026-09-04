"""Contrato de los motores de transcripción (B1, B2)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from voziris.tipos import Audio, Transcripcion


@runtime_checkable
class MotorSTT(Protocol):
    """Convierte audio en texto.

    Implementaciones: `local.MotorLocal` (B1) y `api.MotorAPI` (B2).
    El selector (B3) elige entre ellas y hace de respaldo.
    """

    nombre: str
    """Identificador que acaba en Transcripcion.motor: "local", "api:groq"."""

    requiere_red: bool

    def precalentar(self) -> None:
        """Deja el motor listo para el primer dictado.

        Se llama al arrancar la aplicación, en un hilo aparte. Sin esto, el
        primer dictado del día pagaría la carga del modelo (varios segundos)
        y la aplicación parecería rota.

        No debe lanzar: si falla, `disponible()` pasa a devolver False.
        """
        ...

    def disponible(self) -> bool:
        """True si puede atender un dictado AHORA.

        Falso si falta el modelo en disco, falta la clave de API o no hay red.
        Barato de llamar: el selector lo consulta en cada dictado.
        """
        ...

    def transcribir(self, audio: Audio, idioma: str) -> Transcripcion:
        """Transcribe.

        Args:
            audio: mono a 16 kHz, float32.
            idioma: código ISO-639-1 ("es"). Fijado en configuración (B4), nunca
                detectado.

        Returns:
            Transcripcion con `ms_proceso` medido y el texto **ya puntuado**:
            los dos motores devuelven puntuación y mayúsculas de serie, así que
            C1 no necesita ningún paso adicional.

        Raises:
            MotorNoDisponible: no se puede atender; el selector prueba el otro.
            TranscripcionFallida: respondió pero no hay texto utilizable.
        """
        ...
