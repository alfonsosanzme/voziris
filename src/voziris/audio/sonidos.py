"""A6 — dos tonos cortos: inicio y fin.

Para no tener que mirar la pantalla. Deben ser breves (<120 ms), discretos y
distinguibles entre sí (inicio ascendente, fin descendente).

Se generan por síntesis, no se distribuyen archivos de audio: así no engordan
el paquete y no hay dudas de licencia.

Issue: VOZ-21.
"""

from __future__ import annotations


class Sonidos:
    def __init__(self, activos: bool = True) -> None:
        self._activos = activos

    def inicio(self) -> None:
        """Tono ascendente. No debe bloquear: se reproduce en un hilo aparte."""
        raise NotImplementedError("VOZ-21")

    def fin(self) -> None:
        """Tono descendente."""
        raise NotImplementedError("VOZ-21")

    def error(self) -> None:
        """Tono grave: el dictado no se pudo entregar."""
        raise NotImplementedError("VOZ-21")
