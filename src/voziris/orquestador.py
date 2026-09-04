"""La máquina de estados del dictado y el hilo de trabajo.

Un único objeto posee el estado; toda transición pasa por su lock y es O(1).
Lo pesado (transcribir, post-procesar, entregar) corre en el hilo de trabajo.

    REPOSO ──empezar──► GRABANDO ──terminar──► PROCESANDO ──entrega ok──► REPOSO
      ▲                    │ cancelar · mic perdido · pulsación corta          │
      └────────────────────┴──────────── fallo en cualquier etapa ──► ERROR ───┘
                                          (el siguiente evento lo devuelve a REPOSO)

Reglas de concurrencia (docs/PLAN.md, 3.1):

  - Un `empezar` fuera de REPOSO se ignora y suena el tono de error. Nunca se
    solapan dos dictados.
  - `terminar` solo actúa en GRABANDO. «Clavar» alterna: empieza en REPOSO y
    termina si el dictado en curso es de modo CLAVAR.
  - `cancelar` en GRABANDO descarta; en PROCESANDO marca el dictado y el hilo
    de trabajo lo comprueba justo antes de entregar.
  - Un dictado antes de que el motor esté listo espera en PROCESANDO.
  - La única excepción que sale de aquí es ninguna: cada etapa se captura,
    se registra y deja la aplicación viva con el icono en error.

Issue: VOZ-04.
"""

from __future__ import annotations

import contextlib
import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

import numpy as np

from voziris.destinos.base import Destino
from voziris.errores import (
    EntregaFallida,
    MicrofonoNoDisponible,
    MotorNoDisponible,
    TranscripcionFallida,
)
from voziris.motores.base import MotorSTT
from voziris.proceso.base import PostProceso
from voziris.tipos import Audio, Contexto, EntradaHistorial, Modo, Nivel, Transcripcion

log = logging.getLogger(__name__)

PULSACION_MINIMA_MS = 250
"""Una pulsación de «mantener» más corta que esto es un roce: se descarta sin
transcribir ni sonar el tono de fin. Medido a ojo; 250 ms es más corto que
cualquier sílaba y más largo que cualquier roce."""


class Estado(StrEnum):
    REPOSO = "reposo"
    GRABANDO = "grabando"
    PROCESANDO = "procesando"
    ERROR = "error"


class CapturaDeAudio(Protocol):
    """Lo que el orquestador necesita de `audio.captura.Captura`."""

    oyente_bloques: Callable[[np.ndarray], None] | None

    def empezar_dictado(self) -> None: ...
    def terminar_dictado(self) -> Audio: ...
    def cancelar_dictado(self) -> None: ...


class DetectorDeSilencio(Protocol):
    """Lo que el orquestador necesita de `audio.vad.DetectorSilencio` (VOZ-20)."""

    def precalentar(self) -> None: ...
    def reiniciar(self) -> None: ...
    def alimentar(self, bloque: np.ndarray) -> bool: ...


class Sonidos(Protocol):
    """Lo que el orquestador necesita de `audio.sonidos.Sonidos` (VOZ-21)."""

    def inicio(self) -> None: ...
    def fin(self) -> None: ...
    def error(self) -> None: ...


class Historial(Protocol):
    """Lo que el orquestador necesita de `historial.Historial` (VOZ-52)."""

    def registrar(self, entrada: EntradaHistorial, audio: Audio | None = None) -> None: ...


@dataclass
class Dictado:
    modo: Modo
    destino: str
    inicio: float = field(default_factory=time.monotonic)
    app_activa: str | None = None
    cancelado: threading.Event = field(default_factory=threading.Event)


class Orquestador:
    def __init__(
        self,
        captura: CapturaDeAudio,
        motor: MotorSTT,
        destinos: dict[str, Destino],
        postprocesos: list[PostProceso],
        idioma: str = "es",
        nivel: Nivel = Nivel.LIMPIO,
        al_estado: Callable[[str], None] = lambda _e: None,
        al_aviso: Callable[[str], None] = lambda _t: None,
        sonidos: Sonidos | None = None,
        historial: Historial | None = None,
        vad: DetectorDeSilencio | None = None,
        hay_red: Callable[[], bool] = lambda: False,
        app_en_primer_plano: Callable[[], str | None] = lambda: None,
    ) -> None:
        self._captura = captura
        self._motor = motor
        self._destinos = destinos
        self._postprocesos = postprocesos
        self.idioma = idioma
        self.nivel = nivel
        self._al_estado = al_estado
        self._al_aviso = al_aviso
        self._sonidos = sonidos
        self._historial = historial
        self._vad = vad
        self._hay_red = hay_red
        self._app_en_primer_plano = app_en_primer_plano

        self._lock = threading.Lock()
        self._estado = Estado.REPOSO
        self._dictado: Dictado | None = None
        self._cola: queue.Queue[tuple[Dictado, Audio] | None] = queue.Queue()
        self._hilo: threading.Thread | None = None
        self.motor_listo = threading.Event()
        self.ultimo_error: str | None = None

        # Modo clavar: los bloques van del hilo de audio a la cola, y de ahí al
        # VAD en su propio hilo. Acotada: si el VAD se atasca, se pierden
        # bloques de VAD (no de audio) en vez de bloquear la captura.
        self._cola_vad: queue.Queue[np.ndarray | None] = queue.Queue(maxsize=64)
        self._hilo_vad: threading.Thread | None = None
        self._escuchando_vad = False

    # --- vida --------------------------------------------------------------------

    def arrancar(self) -> None:
        """Arranca los hilos de trabajo y de VAD, y precalienta en otro hilo."""
        self._hilo = threading.Thread(target=self._trabajar, name="voziris-trabajo", daemon=True)
        self._hilo.start()
        if self._vad is not None:
            self._captura.oyente_bloques = self._al_bloque
            self._hilo_vad = threading.Thread(target=self._vigilar_silencio, name="voziris-vad",
                                              daemon=True)
            self._hilo_vad.start()
        threading.Thread(target=self._precalentar, name="voziris-precalentado", daemon=True).start()

    def _precalentar(self) -> None:
        try:
            self._motor.precalentar()
        except Exception:  # noqa: BLE001 — el protocolo dice que no lanza, por si acaso
            log.exception("el precalentado del motor falló")
        finally:
            self.motor_listo.set()
            if not self._motor.disponible():
                self._avisar("El motor de voz no está disponible; mira voziris.log")
        if self._vad is not None:
            try:
                self._vad.precalentar()
            except Exception:  # noqa: BLE001 — sin VAD, «clavar» se cierra a mano
                log.exception("el precalentado del VAD falló")

    def parar(self) -> None:
        self.cancelar()
        self._cola.put(None)
        if self._hilo is not None:
            self._hilo.join(timeout=5)
            self._hilo = None
        if self._hilo_vad is not None:
            self._captura.oyente_bloques = None
            self._cola_vad.put(None)
            self._hilo_vad.join(timeout=2)
            self._hilo_vad = None

    # --- VAD (modo clavar) ---------------------------------------------------------

    def _al_bloque(self, bloque: np.ndarray) -> None:
        """En el hilo de audio: solo encola, y solo mientras se graba clavado."""
        if self._escuchando_vad:
            with contextlib.suppress(queue.Full):
                self._cola_vad.put_nowait(bloque)

    def _vigilar_silencio(self) -> None:
        assert self._vad is not None
        while True:
            bloque = self._cola_vad.get()
            if bloque is None:
                return
            if not self._escuchando_vad:
                continue
            try:
                if self._vad.alimentar(bloque):
                    self._escuchando_vad = False
                    log.info("silencio: se cierra el dictado clavado")
                    self.terminar()
            except Exception:  # noqa: BLE001 — el VAD no puede tumbar un dictado
                log.exception("el VAD falló")
                self._escuchando_vad = False

    def _empezar_vad(self) -> None:
        """Con el lock tomado, al empezar un dictado CLAVAR."""
        if self._vad is None:
            return
        while True:
            try:
                self._cola_vad.get_nowait()
            except queue.Empty:
                break
        self._vad.reiniciar()
        self._escuchando_vad = True

    # --- estado ----------------------------------------------------------------

    @property
    def estado(self) -> Estado:
        return self._estado

    def en_curso(self) -> bool:
        """Para el hook: si hay dictado, «cancelar» actúa y se consume."""
        return self._estado in (Estado.GRABANDO, Estado.PROCESANDO)

    def _cambiar(self, estado: Estado) -> None:
        """Con el lock tomado."""
        self._estado = estado
        try:
            self._al_estado(estado.value)
        except Exception:  # noqa: BLE001 — la interfaz no puede tumbar el estado
            log.exception("al_estado falló")

    def _avisar(self, texto: str) -> None:
        log.info("aviso: %s", texto)
        try:
            self._al_aviso(texto)
        except Exception:  # noqa: BLE001
            log.exception("al_aviso falló")

    def _tono(self, cual: str) -> None:
        if self._sonidos is None:
            return
        try:
            getattr(self._sonidos, cual)()
        except Exception:  # noqa: BLE001 — un tono no puede parar un dictado
            log.exception("el tono «%s» falló", cual)

    # --- eventos (O(1), desde cualquier hilo) --------------------------------------

    def empezar(self, modo: Modo, destino: str) -> bool:
        """Inicia un dictado. Devuelve False (y suena el tono de error) si no se puede."""
        with self._lock:
            if self._estado == Estado.ERROR:
                self._cambiar(Estado.REPOSO)
            if self._estado != Estado.REPOSO:
                self._tono("error")
                return False
            if destino not in self._destinos:
                self._tono("error")
                self._avisar(f"El destino «{destino}» no está disponible")
                return False
            self._dictado = Dictado(modo=modo, destino=destino)
            try:
                self._captura.empezar_dictado()
            except Exception as e:  # noqa: BLE001 — sin micrófono no hay dictado
                self._dictado = None
                self._fallar(f"No se pudo empezar a grabar: {e}")
                return False
            if modo is Modo.CLAVAR:
                self._empezar_vad()
            self._cambiar(Estado.GRABANDO)
        self._tono("inicio")
        return True

    @property
    def modo(self) -> Modo | None:
        """El modo del dictado en curso, para que el HUD distinga clavar de mantener."""
        dictado = self._dictado
        return dictado.modo if dictado is not None and self.en_curso() else None

    def terminar(self, duracion_ms: int | None = None) -> None:
        """Cierra la grabación y encola el procesado. Solo actúa en GRABANDO.

        `duracion_ms` es la de la pulsación de «mantener»; por debajo de
        `PULSACION_MINIMA_MS` se descarta como un roce.
        """
        with self._lock:
            if self._estado != Estado.GRABANDO or self._dictado is None:
                return
            dictado = self._dictado
            self._escuchando_vad = False
            if duracion_ms is not None and duracion_ms < PULSACION_MINIMA_MS:
                self._captura.cancelar_dictado()
                self._dictado = None
                self._cambiar(Estado.REPOSO)
                log.debug("pulsación de %d ms descartada", duracion_ms)
                return
            dictado.app_activa = self._app_en_primer_plano()
            try:
                audio = self._captura.terminar_dictado()
            except MicrofonoNoDisponible as e:
                self._dictado = None
                self._fallar(f"Dictado descartado: {e}")
                return
            self._cambiar(Estado.PROCESANDO)
        self._tono("fin")
        self._cola.put((dictado, audio))

    def alternar_clavar(self, destino: str = "app_activa") -> None:
        """El atajo «clavar» (y «Dictar ahora»): empieza, o termina si ya está clavado."""
        with self._lock:
            en_clavar = (
                self._estado == Estado.GRABANDO
                and self._dictado is not None
                and self._dictado.modo is Modo.CLAVAR
            )
        if en_clavar:
            self.terminar()
        else:
            self.empezar(Modo.CLAVAR, destino)

    def al_empezar_atajo(self, modo: Modo, destino: str) -> None:
        """Adaptador para `Atajos.al_empezar`."""
        if modo is Modo.CLAVAR:
            self.alternar_clavar(destino)
        else:
            self.empezar(modo, destino)

    def cancelar(self) -> None:
        """Descarta el dictado en curso sin entregar nada."""
        with self._lock:
            if self._estado == Estado.GRABANDO:
                self._escuchando_vad = False
                self._captura.cancelar_dictado()
                self._dictado = None
                self._cambiar(Estado.REPOSO)
                self._tono("error")
            elif self._estado == Estado.PROCESANDO and self._dictado is not None:
                self._dictado.cancelado.set()  # el hilo de trabajo lo mira antes de entregar
            elif self._estado == Estado.ERROR:
                self._cambiar(Estado.REPOSO)

    def _fallar(self, texto: str) -> None:
        """Con el lock tomado. Deja la aplicación viva y el icono en error."""
        self.ultimo_error = texto
        log.error(texto)
        self._cambiar(Estado.ERROR)
        self._tono("error")
        self._avisar(texto)

    # --- hilo de trabajo --------------------------------------------------------------

    def _trabajar(self) -> None:
        while True:
            trabajo = self._cola.get()
            if trabajo is None:
                return
            dictado, audio = trabajo
            try:
                self._procesar(dictado, audio)
            except Exception as e:  # noqa: BLE001 — última red: la app sigue viva
                log.exception("fallo inesperado procesando un dictado")
                with self._lock:
                    self._fallar(f"Fallo inesperado: {e}")
            finally:
                with self._lock:
                    if self._dictado is dictado:
                        self._dictado = None
                    if self._estado == Estado.PROCESANDO:
                        self._cambiar(Estado.REPOSO)

    def _procesar(self, dictado: Dictado, audio: Audio) -> None:
        t0 = time.perf_counter()
        ctx = Contexto(
            destino=dictado.destino,
            modo=dictado.modo,
            app_activa=dictado.app_activa,
            hay_red=self._hay_red(),
            nivel=self.nivel,
        )
        try:
            transcripcion = self._transcribir(audio)
        except MotorNoDisponible as e:
            with self._lock:
                self._fallar(f"No se pudo transcribir: {e}")
            return
        except TranscripcionFallida as e:
            self._avisar(f"Nada que escribir: {e}")
            return

        transcripcion = self.postprocesar(transcripcion, ctx)

        if dictado.cancelado.is_set():
            log.info("dictado cancelado antes de entregar")
            return

        entregado, detalle = self.entregar(transcripcion.texto, ctx)
        ms_total = int((time.perf_counter() - t0) * 1000)
        self._registrar(dictado, transcripcion, entregado, ms_total, audio)
        if not entregado:
            with self._lock:
                self._fallar(f"{detalle}. El texto está en el historial")
            return
        for aviso in transcripcion.avisos:
            self._avisar(aviso)
        log.info("dictado entregado: %s (%d ms, RTF %.3f)", detalle, transcripcion.ms_proceso,
                 transcripcion.rtf)

    def _transcribir(self, audio: Audio) -> Transcripcion:
        self.motor_listo.wait()  # el primer dictado del día puede pillar el modelo cargando
        if not self._motor.disponible():
            raise MotorNoDisponible("el motor de voz no está disponible")
        return self._motor.transcribir(audio, self.idioma)

    def postprocesar(self, t: Transcripcion, ctx: Contexto) -> Transcripcion:
        """La cadena en orden fijo. Ningún paso puede perder el dictado."""
        for paso in self._postprocesos:
            if paso.requiere_red and not ctx.hay_red:
                t.avisos.append(f"Sin red: «{paso.nombre}» omitido")
                continue
            try:
                t = paso.aplicar(t, ctx)
            except Exception as e:  # noqa: BLE001 — el protocolo dice que no lanza
                log.exception("el post-proceso «%s» lanzó", paso.nombre)
                t.avisos.append(f"«{paso.nombre}» falló: {e}")
        return t

    def entregar(self, texto: str, ctx: Contexto) -> tuple[bool, str]:
        destino = self._destinos[ctx.destino]
        try:
            entrega = destino.entregar(texto, ctx)
        except EntregaFallida as e:
            return False, str(e)
        return entrega.ok, entrega.detalle

    def _registrar(
        self, dictado: Dictado, t: Transcripcion, entregado: bool, ms_total: int, audio: Audio
    ) -> None:
        if self._historial is None:
            return
        entrada = EntradaHistorial(
            momento=datetime.now(),
            texto=t.texto,
            motor=t.motor,
            destino=dictado.destino,
            entregado=entregado,
            ms_total=ms_total,
            duracion_audio_s=t.duracion_audio_s,
        )
        try:
            self._historial.registrar(entrada, audio)
        except Exception:  # noqa: BLE001 — el historial es la red, no la trampa
            log.exception("no se pudo registrar en el historial")

    # --- modo consola -------------------------------------------------------------------

    def dictar_audio(self, audio: Audio, destino: str, modo: Modo = Modo.MANTENER) -> Any:
        """El pipeline completo sobre un audio ya capturado, en este hilo.

        Para el modo consola (`python -m voziris --archivo x.wav`) y para
        reproducir fallos sin tocar el teclado. Devuelve un dict con la
        transcripción, los tiempos y el resultado de la entrega.
        """
        self.motor_listo.wait()
        t0 = time.perf_counter()
        transcripcion = self._transcribir(audio)
        ms_motor = int((time.perf_counter() - t0) * 1000)
        ctx = Contexto(
            destino=destino, modo=modo, app_activa=self._app_en_primer_plano(),
            hay_red=self._hay_red(), nivel=self.nivel,
        )
        t1 = time.perf_counter()
        transcripcion = self.postprocesar(transcripcion, ctx)
        ms_proceso = int((time.perf_counter() - t1) * 1000)
        t2 = time.perf_counter()
        entregado, detalle = self.entregar(transcripcion.texto, ctx)
        ms_entrega = int((time.perf_counter() - t2) * 1000)
        return {
            "texto": transcripcion.texto,
            "motor": transcripcion.motor,
            "rtf": transcripcion.rtf,
            "ms_motor": ms_motor,
            "ms_postproceso": ms_proceso,
            "ms_entrega": ms_entrega,
            "entregado": entregado,
            "detalle": detalle,
            "avisos": list(transcripcion.avisos),
        }
