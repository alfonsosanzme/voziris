"""B3 — elige motor y hace de respaldo.

Reglas, en este orden:

1. `motor = "local"` → siempre el local. Si no está disponible, error visible.
2. `motor = "api"`   → el de API. Si no está disponible, **cae al local** y lo
   avisa en la transcripción; no se pierde el dictado.
3. `motor = "auto"`  → API si está disponible (mejor precisión), local si no.

El respaldo es solo en esa dirección: de la nube al local. Nunca al revés, para
que nadie acabe mandando audio fuera sin haberlo pedido.

Issue: VOZ-31.
"""

from __future__ import annotations

from voziris.motores.base import MotorSTT
from voziris.tipos import Audio, Transcripcion


class Selector:
    def __init__(self, preferencia: str, local: MotorSTT, api: MotorSTT | None) -> None:
        self._preferencia = preferencia
        self._local = local
        self._api = api

    def precalentar(self) -> None:
        """Precalienta los motores que se vayan a usar, en hilos aparte."""
        raise NotImplementedError("VOZ-31")

    def transcribir(self, audio: Audio, idioma: str) -> Transcripcion:
        """Aplica las reglas de arriba.

        Cuando hay respaldo, añade a `Transcripcion.avisos` una línea como
        «Sin conexión: transcrito en local», que el HUD muestra un instante.
        """
        raise NotImplementedError("VOZ-31")
