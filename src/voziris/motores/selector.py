"""B3 — elige motor y hace de respaldo.

Reglas, en este orden:

1. `motor = "local"` → siempre el local. Si no está disponible, error visible.
   La API ni se precalienta ni se consulta: no sale nada del equipo.
2. `motor = "api"`   → el de API. Si no está disponible, **cae al local** y lo
   avisa en la transcripción; no se pierde el dictado.
3. `motor = "auto"`  → API si está disponible (mejor precisión), local si no.

El respaldo es solo en esa dirección: de la nube al local. Nunca al revés,
para que nadie acabe mandando audio fuera sin haberlo pedido.

La API tiene paciencia limitada (VOZ-74): Groq contesta en medio segundo; si
a los pocos segundos no ha dicho nada, esperar a su `timeout_s` (15 s) con el
indicador en «procesando» es lo que el usuario vive como «se ha quedado
pillado». Pasada la paciencia, se transcribe en local y la respuesta de la
API, si llega, se tira.

Y tiene un cortacircuitos (VOZ-78). Groq admite 20 peticiones por minuto
con `whisper-large-v3`, así que una grabación larga troceada agota el límite
siempre: a partir de ahí cada trozo sube su WAV para que el servidor lo tire
con un 429. Medido con un vídeo de 105 minutos: decenas de minutos de subida
inútil. Tras dos 429 seguidos —o ante uno que traiga `Retry-After`— se deja
de llamar a la API un rato y se transcribe directo en local, diciéndolo una
vez en el log en vez de una por trozo. El corte se cierra solo.

Solo lo abre el 429. Un fallo de red o de clave no: esos son baratos de
reintentar (la comprobación de red va cacheada) y no se arreglan esperando,
así que el respaldo de toda la vida ya los cubre.

La preferencia se puede cambiar en caliente desde la bandeja: vale para el
dictado siguiente. Si pasa a necesitar la API y esta no se había
precalentado, se precalienta en un hilo aparte.

Issue: VOZ-31, VOZ-78.
"""

from __future__ import annotations

import logging
import threading
import time

from voziris.errores import LimiteDeTasa, MotorNoDisponible
from voziris.motores.base import MotorSTT
from voziris.tipos import Audio, Transcripcion

log = logging.getLogger(__name__)

PREFERENCIAS = ("local", "api", "auto")
PACIENCIA_BASE_S = 3.0
PACIENCIA_POR_SEGUNDO = 0.1
"""Espera a la API: 3 s más una décima por segundo de audio (un minuto → 9 s)."""

FALLOS_429_PARA_CORTAR = 2
"""429 seguidos antes de cortar por nuestra cuenta. Uno puede ser mala suerte; dos, no.

Con `Retry-After` se corta ya al primero, porque el servidor ha dicho cuánto
esperar y no hay nada que adivinar.
"""
CORTE_MINIMO_S = 30.0
"""Lo que dura el primer corte propio. La ventana de Groq es de un minuto."""
CORTE_MAXIMO_S = 300.0
"""Tope del corte. Pasados cinco minutos vale la pena volver a probar aunque siga lleno."""
DOBLADOS_MAXIMOS = 4
"""El corte se dobla en cada reintento fallido (30 s → 1 → 2 → 4 → 5 por el tope).

Sin esto, una grabación de dos horas en local gasta media hora de trabajo
probando cada 30 s: unas sesenta subidas tiradas. Doblando son seis.
"""
MOTIVO_LIMITE = "La API está al límite de peticiones"
"""Lo que se le cuenta al usuario en el aviso de la transcripción."""


class Selector:
    """Cumple `MotorSTT` hacia el orquestador y reparte entre los dos motores."""

    nombre = "selector"
    requiere_red = False

    def __init__(
        self,
        preferencia: str,
        local: MotorSTT,
        api: MotorSTT | None,
        paciencia_base_s: float = PACIENCIA_BASE_S,
    ) -> None:
        if preferencia not in PREFERENCIAS:
            raise ValueError(f"preferencia de motor desconocida: {preferencia!r}")
        self._preferencia = preferencia
        self._local = local
        self._api = api
        self._lock = threading.Lock()
        self._api_precalentada = False
        self._paciencia_base_s = paciencia_base_s
        self._fallos_429 = 0
        """429 seguidos. Un acierto de la API lo vuelve a poner a cero."""
        self._corte_hasta = 0.0
        """Momento (`time.monotonic`) hasta el que no se llama a la API. 0: no hay corte."""

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

    # --- cortacircuitos del 429 ------------------------------------------------------

    def _cortado(self) -> bool:
        """¿Sigue abierto el corte? Una comparación bajo el lock: no cuesta nada."""
        with self._lock:
            return self._corte_hasta > time.monotonic()

    def _anotar_acierto(self) -> None:
        """La API ha contestado: se olvida la racha y se cierra el corte si lo había."""
        with self._lock:
            if self._fallos_429 or self._corte_hasta:
                self._fallos_429 = 0
                self._corte_hasta = 0.0

    def _anotar_429(self, espera_s: float | None) -> None:
        """Apunta el 429 y abre el corte si toca. Lo dice en el log una vez por corte.

        Al primero se corta solo si el servidor ha dicho cuánto esperar; a
        partir del segundo se corta siempre, con el mínimo propio doblándose
        en cada reintento fallido. Se respeta la espera del servidor si es más
        larga que la nuestra: el que sabe cuándo se libera la cuota es él.
        """
        ahora = time.monotonic()
        with self._lock:
            self._fallos_429 += 1
            doblados = self._fallos_429 - FALLOS_429_PARA_CORTAR
            if doblados >= 0:
                espera = CORTE_MINIMO_S * 2 ** min(doblados, DOBLADOS_MAXIMOS)
                espera = min(max(espera, espera_s or 0.0), CORTE_MAXIMO_S)
            elif espera_s:
                espera = min(espera_s, CORTE_MAXIMO_S)
            else:
                return  # un 429 suelto y mudo: todavía no se corta nada
            estaba_abierto = self._corte_hasta > ahora
            self._corte_hasta = ahora + espera
        if not estaba_abierto:
            log.warning(
                "la API está al límite de peticiones (429): no se vuelve a intentar "
                "en %.0f s, se transcribe en local",
                espera,
            )

    # --- petición -----------------------------------------------------------------------

    def _pedir_a_la_api(self, audio: Audio, idioma: str) -> Transcripcion:
        """Una petición, anotando cómo fue en el cortacircuitos.

        Pasa por aquí TODA llamada a la API, también la del hilo que la
        paciencia ya dio por perdido: en una grabación larga el 429 suele
        llegar después de que se agote la paciencia —subir el WAV de un trozo
        tarda más que los 6 s que se le dan—, y si ese hilo huérfano tirase su
        respuesta, el corte no se abriría nunca justo cuando más falta hace.
        """
        assert self._api is not None
        try:
            t = self._api.transcribir(audio, idioma)
        except LimiteDeTasa as e:
            self._anotar_429(e.espera_s)
            raise
        self._anotar_acierto()
        return t

    def _con_paciencia(self, audio: Audio, idioma: str) -> Transcripcion:
        """La API en un hilo; si no contesta a tiempo, `MotorNoDisponible` y a local.

        Si el local no está disponible no hay a dónde caer: se espera lo que
        haga falta.
        """
        assert self._api is not None
        if not self._local.disponible():
            return self._pedir_a_la_api(audio, idioma)
        resultado: list[Transcripcion] = []
        fallo: list[BaseException] = []

        def pedir() -> None:
            try:
                resultado.append(self._pedir_a_la_api(audio, idioma))
            except BaseException as e:  # noqa: BLE001 — se relanza en el hilo que espera
                fallo.append(e)

        hilo = threading.Thread(target=pedir, name="voziris-api-peticion", daemon=True)
        hilo.start()
        paciencia = self._paciencia_base_s + PACIENCIA_POR_SEGUNDO * audio.duracion_s
        hilo.join(timeout=paciencia)
        if hilo.is_alive():
            raise MotorNoDisponible(f"la API no contestó en {paciencia:.0f} s")
        if fallo:
            raise fallo[0]
        return resultado[0]

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
        # El corte solo vale mientras haya local al que caer. Si no lo hay, se
        # llama igual: una espera larga es mejor que quedarse sin transcripción.
        if self._cortado() and self._local.disponible():
            aviso = f"{MOTIVO_LIMITE}: transcrito en local"
        elif self._api.disponible():
            try:
                return self._con_paciencia(audio, idioma)
            except LimiteDeTasa:
                # No se registra aquí: `_anotar_429` ya lo ha dicho una vez por
                # corte, y este camino lo recorre cada trozo de la grabación.
                aviso = f"{MOTIVO_LIMITE}: transcrito en local"
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
