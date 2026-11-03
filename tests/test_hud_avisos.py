"""VOZ-86: los avisos del dictado se ven. La lógica del indicador, sin abrir ninguna ventana.

La secuencia es la que llega en la aplicación: el orquestador de verdad y una cola
que se vacía de una vez, como `bombear` en `__main__.py`. Los tests de antes miraban
que el aviso se mandara, y se mandaba; lo que fallaba venía justo después. Con la
ventana de verdad se prueba en `test_hud.py`, que se lanza aparte.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import numpy as np
import pytest

from voziris.orquestador import Estado, Orquestador
from voziris.tipos import SAMPLE_RATE, Audio, Entrega, Modo, Transcripcion
from voziris.ui.hud import Hud


class RaizFalsa:
    """Los temporizadores de Tk, a mano: `pasar_el_tiempo` dispara los que haya."""

    def __init__(self) -> None:
        self._ultimo = 0
        self.temporizadores: dict[str, Callable[[], None]] = {}

    def after(self, _ms: int, fn: Callable[[], None]) -> str:
        self._ultimo += 1
        identificador = f"after#{self._ultimo}"
        self.temporizadores[identificador] = fn
        return identificador

    def after_cancel(self, identificador: str) -> None:
        self.temporizadores.pop(identificador, None)

    def pasar_el_tiempo(self) -> None:
        pendientes, self.temporizadores = self.temporizadores, {}
        for fn in pendientes.values():
            fn()


class LienzoFalso:
    def __init__(self) -> None:
        self.texto = ""

    def itemconfigure(self, _elemento: int, **opciones: Any) -> None:
        self.texto = opciones.get("text", self.texto)

    def coords(self, *_: Any) -> None: ...


class VentanaFalsa:
    def __init__(self) -> None:
        self.a_la_vista = False

    def withdraw(self) -> None:
        self.a_la_vista = False


class HudSinVentana(Hud):
    """El indicador con todo menos la ventana."""

    def _crear(self) -> Any:
        if self._ventana is None:
            self._ventana, self._lienzo = VentanaFalsa(), LienzoFalso()  # type: ignore[assignment]
        return self._ventana

    def _presentar(self, v: Any) -> None:
        v.a_la_vista = True

    @property
    def se_ve(self) -> bool:
        return self._ventana is not None and self._ventana.a_la_vista  # type: ignore[attr-defined]

    @property
    def texto(self) -> str:
        assert self._lienzo is not None
        return self._lienzo.texto  # type: ignore[attr-defined]


@pytest.fixture
def raiz() -> RaizFalsa:
    return RaizFalsa()


def _hud(raiz: RaizFalsa, cola: list[Callable[[], None]]) -> HudSinVentana:
    return HudSinVentana(raiz, en_hilo_tk=cola.append, animaciones=True)  # type: ignore[arg-type]


def _vaciar(cola: list[Callable[[], None]]) -> None:
    """De una vez: `bombear` no vuelve a Tk, y por tanto no pinta, hasta acabar la cola."""
    for fn in cola:
        fn()
    cola.clear()


# --- el indicador ---------------------------------------------------------------------------


def test_el_paso_a_reposo_no_se_lleva_el_aviso(raiz: RaizFalsa) -> None:
    cola: list[Callable[[], None]] = []
    h = _hud(raiz, cola)
    h.mostrar_procesando()
    h.aviso("Nada que escribir: no se oyó voz")
    h.ocultar()
    _vaciar(cola)
    assert h.se_ve and h.visible
    assert h.texto == "Nada que escribir: no se oyó voz"
    raiz.pasar_el_tiempo()  # lo quita su temporizador
    assert not h.se_ve and not h.visible


def test_sin_aviso_se_oculta_al_momento(raiz: RaizFalsa) -> None:
    cola: list[Callable[[], None]] = []
    h = _hud(raiz, cola)
    h.mostrar_procesando()
    h.ocultar()
    _vaciar(cola)
    assert not h.se_ve and not raiz.temporizadores


def test_el_dictado_siguiente_quita_el_aviso_y_no_hereda_su_temporizador(raiz: RaizFalsa) -> None:
    cola: list[Callable[[], None]] = []
    h = _hud(raiz, cola)
    h.aviso("Nada que escribir: no se oyó voz")
    _vaciar(cola)
    h.mostrar_grabando()
    _vaciar(cola)
    assert "suelta" in h.texto
    raiz.pasar_el_tiempo()  # el tiempo del aviso ya no oculta la grabación
    assert h.se_ve
    h.mostrar_procesando()
    h.ocultar()
    _vaciar(cola)
    assert not h.se_ve and not raiz.temporizadores  # este dictado no tenía aviso


def test_un_aviso_nuevo_sustituye_al_anterior_y_dura_lo_suyo(raiz: RaizFalsa) -> None:
    cola: list[Callable[[], None]] = []
    h = _hud(raiz, cola)
    h.aviso("el primero")
    h.aviso("el segundo")
    h.ocultar()
    _vaciar(cola)
    assert h.texto == "el segundo" and len(raiz.temporizadores) == 1
    raiz.pasar_el_tiempo()
    assert not h.se_ve


def test_pasado_el_aviso_el_siguiente_dictado_se_oculta_al_momento(raiz: RaizFalsa) -> None:
    cola: list[Callable[[], None]] = []
    h = _hud(raiz, cola)
    h.aviso("un aviso")
    _vaciar(cola)
    raiz.pasar_el_tiempo()
    h.mostrar_procesando()
    h.ocultar()
    _vaciar(cola)
    assert not h.se_ve


# --- con el orquestador de verdad ---------------------------------------------------------


class Captura:
    oyente_bloques: Any = None

    def empezar_dictado(self) -> None: ...

    def terminar_dictado(self) -> Audio:
        return Audio(muestras=np.zeros(SAMPLE_RATE, dtype=np.float32))

    def cancelar_dictado(self) -> None: ...


class Motor:
    nombre = "api:groq"
    requiere_red = True

    def __init__(self, avisos: list[str]) -> None:
        self.avisos = avisos

    def precalentar(self) -> None: ...

    def disponible(self) -> bool:
        return True

    def transcribir(self, audio: Audio, idioma: str) -> Transcripcion:
        t = Transcripcion("Hola.", idioma, self.nombre, 5, audio.duracion_s)
        t.avisos.extend(self.avisos)
        return t


class Destino:
    nombre = "app_activa"

    def entregar(self, _texto: str, _ctx: object) -> Entrega:
        return Entrega(ok=True, detalle="pegado en prueba.exe")


class Vad:
    def __init__(self, hay: bool) -> None:
        self.hay = hay

    def precalentar(self) -> None: ...

    def reiniciar(self) -> None: ...

    def alimentar(self, _bloque: np.ndarray) -> bool:
        return False

    def hay_voz(self, _muestras: np.ndarray) -> bool | None:
        return self.hay


def _dictar(raiz: RaizFalsa, hay_voz: bool, avisos: list[str]) -> HudSinVentana:
    """Un dictado entero, con el indicador enganchado como en `__main__.py`."""
    cola: list[Callable[[], None]] = []
    h = _hud(raiz, cola)

    def cambiar_estado(estado: str) -> None:
        if estado == "grabando":
            h.mostrar_grabando()
        elif estado == "procesando":
            h.mostrar_procesando()
        else:
            h.ocultar()

    orq = Orquestador(
        Captura(), Motor(avisos), {"app_activa": Destino()}, [],
        al_estado=cambiar_estado, al_aviso=h.aviso, vad=Vad(hay_voz),
    )
    orq.arrancar()
    assert orq.motor_listo.wait(3)
    try:
        assert orq.empezar(Modo.MANTENER, "app_activa")
        orq.terminar(1000)
        limite = time.monotonic() + 3
        while orq.estado is Estado.PROCESANDO and time.monotonic() < limite:
            time.sleep(0.005)
        assert orq.estado is Estado.REPOSO
    finally:
        orq.parar()
    _vaciar(cola)
    return h


def test_pulsar_sin_hablar_se_dice_en_el_indicador(raiz: RaizFalsa) -> None:
    h = _dictar(raiz, hay_voz=False, avisos=[])
    assert h.se_ve and h.texto == "Nada que escribir: no se oyó voz"
    raiz.pasar_el_tiempo()
    assert not h.se_ve


def test_si_transcribe_el_local_porque_fallo_la_api_se_dice(raiz: RaizFalsa) -> None:
    aviso = "Sin conexión con la API: transcrito en local"
    h = _dictar(raiz, hay_voz=True, avisos=[aviso])
    assert h.se_ve and h.texto == aviso
    raiz.pasar_el_tiempo()
    assert not h.se_ve


def test_un_dictado_normal_no_deja_nada_a_la_vista(raiz: RaizFalsa) -> None:
    h = _dictar(raiz, hay_voz=True, avisos=[])
    assert not h.se_ve and not raiz.temporizadores
