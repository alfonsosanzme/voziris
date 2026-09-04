"""B3 — elige motor y hace de respaldo.

Reglas, en este orden:

1. `motor = "local"` → siempre el local. Si no está disponible, error visible.
   La API ni se precalienta ni se consulta: no sale nada del equipo.
2. `motor = "api"`   → el de API. Si no está disponible, **cae al local** y lo
   avisa en la transcripción; no se pierde el dictado.
3. `motor = "auto"`  → API si está disponible (mejor precisión), local si no.

El respaldo es solo en esa dirección: de la nube al local. Nunca al revés,
para que nadie acabe mandando audio fuera sin haberlo pedido.

La preferencia se puede cambiar en caliente desde la bandeja: vale para el
dictado siguiente. Si pasa a necesitar la API y esta no se había
precalentado, se precalienta en un hilo aparte.

Issue: VOZ-31.
"""

from __future__ import annotations

import logging
import threading

from voziris.errores import MotorNoDisponible
from voziris.motores.base import MotorSTT
from voziris.tipos import Audio, Transcripcion

log = logging.getLogger(__name__)

PREFERENCIAS = ("local", "api", "auto")


class Selector:
    """Cumple `MotorSTT` hacia el orquestador y reparte entre los dos motores."""

    nombre = "selector"
    requiere_red = False

    def __init__(self, preferencia: str, local: MotorSTT, api: MotorSTT | None) -> None:
        if preferencia not in PREFERENCIAS:
            raise ValueError(f"preferencia de motor desconocida: {preferencia!r}")
        self._preferencia = preferencia
        self._local = local
        self._api = api
        self._lock = threading.Lock()
        self._api_precalentada = False

    @property
    def preferencia(self) -> str:
        return self._preferencia

    @preferencia.setter
    def preferencia(self, valor: str) -> None:
        """Desde la bandeja o los ajustes. Se aplica al dictado siguiente."""
        if valor not in PREFERENCIAS:
            raise ValueError(f"preferencia de motor desconocida: {valor!r}")
        with self._lock:
            self._preferencia = valor
            necesita_api = valor != "local" and self._api is not None
            ya = self._api_precalentada
        log.info("motor preferido: %s", valor)
        if necesita_api and not ya:
            threading.Thread(target=self._precalentar_api, name="voziris-api", daemon=True).start()

    def _usa_api(self) -> bool:
        return self._preferencia != "local" and self._api is not None

    # --- MotorSTT --------------------------------------------------------------------

    def precalentar(self) -> None:
        """Precalienta los motores que se vayan a usar, en paralelo, y espera a ambos.

        Con `local`, solo el local: la API no se toca (VOZ-32). Con `api` y
        `auto`, los dos, porque el local es el respaldo.
        """
        hilos = [
            threading.Thread(target=self._local.precalentar, name="voziris-local", daemon=True)
        ]
        if self._usa_api():
            hilos.append(
                threading.Thread(target=self._precalentar_api, name="voziris-api", daemon=True)
            )
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()

    def _precalentar_api(self) -> None:
        if self._api is None:
            return
        with self._lock:
            if self._api_precalentada:
                return
            self._api_precalentada = True
        try:
            self._api.precalentar()
        except Exception:  # noqa: BLE001 — el protocolo dice que no lanza
            log.exception("el precalentado de la API falló")

    def disponible(self) -> bool:
        if self._usa_api() and self._api is not None and self._api.disponible():
            return True
        return self._local.disponible()

    def transcribir(self, audio: Audio, idioma: str) -> Transcripcion:
        """Aplica las reglas del módulo. Ver arriba: nunca del local a la API."""
        if not self._usa_api() or self._api is None:
            return self._local.transcribir(audio, idioma)

        aviso: str
        if self._api.disponible():
            try:
                return self._api.transcribir(audio, idioma)
            except MotorNoDisponible as e:
                log.warning("la API falló, se transcribe en local: %s", e)
                aviso = f"La API falló ({e}): transcrito en local"
        else:
            motivo = getattr(self._api, "motivo_no_disponible", None)
            aviso = (motivo() if motivo else "Sin conexión con la API") + ": transcrito en local"

        if not self._local.disponible():
            raise MotorNoDisponible(
                "ni la API ni el motor local están disponibles ahora mismo"
            )
        t = self._local.transcribir(audio, idioma)
        t.avisos.append(aviso)
        return t
