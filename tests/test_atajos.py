"""Tests de los atajos globales (VOZ-02).

El `Detector` es puro y se prueba con secuencias de teclas en cualquier
sistema. Los marcados `win` instalan el hook real e inyectan pulsaciones con
`SendInput` sin la marca de Voziris, para que el hook las tome por físicas.
Usan combinaciones que no hacen nada en la ventana que tenga el foco.
"""

from __future__ import annotations

import ctypes
import logging
import threading
import time
from collections.abc import Iterator
from types import SimpleNamespace

import pytest

from voziris import teclas, winapi
from voziris.atajos import (
    KBDLLHOOKSTRUCT,
    LLKHF_INJECTED,
    WM_KEYDOWN,
    WM_KEYUP,
    Accion,
    Atajos,
    Detector,
)
from voziris.errores import ConfigInvalida
from voziris.tipos import Modo

VK = {n: next(iter(v)) for n, v in teclas.VK.items()}
VK["lctrl"], VK["rctrl"], VK["lwin"] = 0xA2, 0xA3, 0x5B

COMBOS = {
    "mantener": teclas.analizar("ctrl+win"),
    "clavar": teclas.analizar("ctrl+shift+space"),
    "markdown": teclas.analizar("ctrl+alt+m"),
    "cancelar": teclas.analizar("esc"),
}


class Escenario:
    """Lo que ve el hook y lo que Windows da por apretado, que casi siempre coinciden.

    `visto=False` es una tecla que Windows procesa y el hook no ve (VOZ-83).
    Lo que el Detector consume no llega a Windows.
    """

    def __init__(self, en_curso: bool = False) -> None:
        self.en_curso = en_curso
        self.windows: set[int] = set()
        """Lo que contestaría GetAsyncKeyState."""
        self.detector = Detector(dict(COMBOS), lambda: self.en_curso, self.windows.__contains__)
        self.reloj = 0.0
        self.acciones: list[Accion] = []
        self.consumidas: list[tuple[int, bool]] = []

    def abajo(self, vk: int, tras_ms: int = 0, visto: bool = True) -> None:
        self._tecla(vk, True, tras_ms, visto)

    def arriba(self, vk: int, tras_ms: int = 0, visto: bool = True) -> None:
        self._tecla(vk, False, tras_ms, visto)

    def voziris(self, vk: int, abajo: bool) -> None:
        """Una pulsación que inyecta el propio Voziris, con su marca."""
        self.detector.de_voziris(vk, abajo)
        (self.windows.add if abajo else self.windows.discard)(vk)

    def _tecla(self, vk: int, abajo: bool, tras_ms: int, visto: bool) -> None:
        self.reloj += tras_ms / 1000
        consumir = False
        if visto:
            acciones, consumir = self.detector.tecla(vk, abajo, self.reloj)
            self.acciones += acciones
            if consumir:
                self.consumidas.append((vk, abajo))
        # Windows la anota después de pasar por el hook: dentro, aún no se ve.
        if not consumir:
            (self.windows.add if abajo else self.windows.discard)(vk)

    @property
    def disparos(self) -> list[Accion]:
        """Lo que le llega al orquestador: todo menos `olvidar`, que solo va al registro."""
        return [a for a in self.acciones if a.tipo != "olvidar"]

    @property
    def olvidadas(self) -> list[str]:
        return [a.tecla for a in self.acciones if a.tipo == "olvidar"]


# --- detector -------------------------------------------------------------------


def test_mantener_dispara_al_pulsar_y_al_soltar_con_la_duracion() -> None:
    e = Escenario()
    e.abajo(VK["lctrl"])
    assert e.acciones == []
    e.abajo(VK["lwin"], tras_ms=30)
    assert e.acciones == [Accion("empezar", "mantener")]
    e.abajo(VK["lwin"], tras_ms=500)  # autorrepetición de Windows: no es otra pulsación
    e.arriba(VK["lwin"], tras_ms=1200)
    assert e.acciones[-1] == Accion("terminar", "mantener", 1700)
    e.arriba(VK["lctrl"])
    assert len(e.acciones) == 2
    assert e.consumidas == []  # los modificadores nunca se consumen


def test_mantener_termina_al_soltar_cualquiera_de_sus_teclas() -> None:
    e = Escenario()
    e.abajo(VK["lwin"])
    e.abajo(VK["rctrl"])  # el ctrl derecho también es «ctrl»
    e.arriba(VK["rctrl"], tras_ms=800)
    assert e.acciones == [Accion("empezar", "mantener"), Accion("terminar", "mantener", 800)]


def test_clavar_dispara_solo_al_pulsar_y_consume_la_tecla() -> None:
    e = Escenario()
    e.abajo(VK["lctrl"])
    e.abajo(VK["shift"])
    e.abajo(VK["space"])
    e.arriba(VK["space"])
    e.arriba(VK["shift"])
    e.arriba(VK["lctrl"])
    assert e.acciones == [Accion("empezar", "clavar")]
    assert e.consumidas == [(VK["space"], True), (VK["space"], False)]
    # Segunda pulsación: otro «empezar»; el orquestador lo interpreta como cierre.
    e.abajo(VK["lctrl"])
    e.abajo(VK["shift"])
    e.abajo(VK["space"])
    assert e.acciones == [Accion("empezar", "clavar"), Accion("empezar", "clavar")]


def test_markdown_es_como_mantener_pero_al_archivo() -> None:
    e = Escenario()
    e.abajo(VK["lctrl"])
    e.abajo(VK["alt"])
    e.abajo(VK["m"])
    e.arriba(VK["m"], tras_ms=2000)
    assert e.acciones == [Accion("empezar", "markdown"), Accion("terminar", "markdown", 2000)]
    assert e.consumidas == [(VK["m"], True), (VK["m"], False)]


def test_cancelar_solo_actua_con_dictado_en_curso() -> None:
    e = Escenario(en_curso=False)
    e.abajo(VK["esc"])
    e.arriba(VK["esc"])
    assert e.acciones == [] and e.consumidas == []  # Esc pasa a la aplicación (C-3)
    e.en_curso = True
    e.abajo(VK["esc"])
    e.arriba(VK["esc"])
    assert e.acciones == [Accion("cancelar", "cancelar")]
    assert e.consumidas == [(VK["esc"], True), (VK["esc"], False)]


def test_cancelar_cierra_el_mantener_activo_sin_terminar() -> None:
    e = Escenario(en_curso=True)
    e.abajo(VK["lctrl"])
    e.abajo(VK["lwin"])
    e.abajo(VK["esc"])
    e.arriba(VK["lwin"])
    assert e.acciones == [Accion("empezar", "mantener"), Accion("cancelar", "cancelar")]


def test_modificador_de_mas_no_completa_la_combinacion() -> None:
    e = Escenario()
    e.abajo(VK["lctrl"])
    e.abajo(VK["shift"])
    e.abajo(VK["lwin"])  # ctrl+shift+win no es ctrl+win
    assert e.acciones == []
    e.abajo(VK["alt"])
    e.abajo(VK["m"])  # ctrl+shift+alt+m no es ctrl+alt+m
    assert e.acciones == []


def test_solo_modificadores_no_se_activa_con_otra_tecla_pulsada() -> None:
    e = Escenario()
    e.abajo(VK["a"])
    e.abajo(VK["lctrl"])
    e.abajo(VK["lwin"])
    assert e.acciones == []
    e.abajo(0xFF)  # tecla desconocida pulsada: tampoco
    e.arriba(VK["a"])
    assert e.acciones == []


def test_una_tecla_desconocida_no_molesta() -> None:
    e = Escenario()
    e.abajo(0xE8)
    e.arriba(0xE8)
    e.abajo(VK["lctrl"])
    e.abajo(VK["lwin"])
    assert e.acciones == [Accion("empezar", "mantener")]


def test_los_dos_atajos_conviven() -> None:
    """H2: usar uno no interfiere con el otro."""
    e = Escenario()
    e.abajo(VK["lctrl"])
    e.abajo(VK["lwin"])
    e.arriba(VK["lwin"], tras_ms=500)
    e.arriba(VK["lctrl"])
    e.abajo(VK["lctrl"])
    e.abajo(VK["shift"])
    e.abajo(VK["space"])
    e.arriba(VK["space"])
    e.arriba(VK["shift"])
    e.arriba(VK["lctrl"])
    assert [a.tipo for a in e.acciones] == ["empezar", "terminar", "empezar"]
    assert [a.atajo for a in e.acciones] == ["mantener", "mantener", "clavar"]


def test_soltar_todo_olvida_las_teclas() -> None:
    e = Escenario()
    e.abajo(VK["lctrl"])
    e.detector.soltar_todo()
    e.abajo(VK["lwin"])
    assert e.acciones == []


# --- VOZ-83: subidas que el hook no ve --------------------------------------------
#
# Si el callback del hook tarda más de ~300 ms (con el GIL ocupado mientras carga
# el modelo local), Windows se salta las teclas que lleguen mientras tanto; y en
# el escritorio seguro (Win+L) no le cuenta nada. En el equipo del cliente, el
# 30/10, una subida de Win perdida al arrancar hizo que cada Ctrl arrancara un
# dictado durante cinco minutos.


def pegar(e: Escenario) -> None:
    """Lo que inyecta `app_activa` al pegar: suelta los modificadores apretados y Ctrl+V."""
    for vk in winapi.MODIFICADORES_FISICOS:
        if vk in e.windows:
            e.voziris(vk, abajo=False)
    for vk, abajo in ((winapi.VK_CONTROL, True), (winapi.VK_V, True),
                      (winapi.VK_V, False), (winapi.VK_CONTROL, False)):
        e.voziris(vk, abajo)


def test_ctrl_solo_no_es_ctrl_win_aunque_el_hook_no_viera_soltarse_win() -> None:
    e = Escenario()
    e.abajo(VK["lwin"])
    e.arriba(VK["lwin"], tras_ms=250, visto=False)
    e.abajo(VK["lctrl"], tras_ms=40_000)
    e.arriba(VK["lctrl"], tras_ms=300)
    assert e.disparos == []
    assert e.olvidadas == ["win"]
    assert e.acciones[0].duracion_ms == 40_250  # lo que el hook la creyó apretada
    e.abajo(VK["lctrl"], tras_ms=2_000)
    e.abajo(VK["lwin"], tras_ms=40)  # ctrl+win de verdad sigue funcionando
    assert e.disparos == [Accion("empezar", "mantener")]


def test_ni_ctrl_c_ni_ctrl_v_arrancan_el_dictado() -> None:
    e = Escenario()
    e.abajo(VK["lwin"])
    e.arriba(VK["lwin"], tras_ms=150, visto=False)
    for letra in ("c", "v"):
        e.abajo(VK["lctrl"], tras_ms=5_000)
        e.abajo(VK[letra], tras_ms=80)
        e.arriba(VK[letra], tras_ms=90)
        e.arriba(VK["lctrl"], tras_ms=60)
    assert e.disparos == [] and e.consumidas == []


def test_tras_bloquear_con_win_l_ctrl_solo_no_dicta() -> None:
    """La L se suelta antes de que cambie el escritorio; Win, ya en el seguro."""
    e = Escenario()
    e.abajo(VK["lwin"])
    e.abajo(VK["l"], tras_ms=120)
    e.arriba(VK["l"], tras_ms=90)
    e.arriba(VK["lwin"], tras_ms=200, visto=False)
    e.abajo(VK["lctrl"], tras_ms=600_000)
    e.arriba(VK["lctrl"], tras_ms=300)
    assert e.disparos == []


def test_una_tecla_atascada_ya_no_deja_el_atajo_sin_responder() -> None:
    """El síntoma contrario: con la «l» de Win+L atascada, ctrl+win no hacía nada."""
    e = Escenario()
    e.abajo(VK["lwin"])
    e.abajo(VK["l"], tras_ms=120)
    e.arriba(VK["l"], tras_ms=90, visto=False)
    e.arriba(VK["lwin"], tras_ms=200, visto=False)
    e.abajo(VK["lctrl"], tras_ms=600_000)
    e.abajo(VK["lwin"], tras_ms=40)
    assert e.disparos == [Accion("empezar", "mantener")]
    assert sorted(e.olvidadas) == ["l", "win"]


def test_tampoco_una_tecla_sin_nombre() -> None:
    e = Escenario()
    e.abajo(0xC0)  # la Ñ: no tiene nombre en `teclas.VK`
    e.arriba(0xC0, tras_ms=100, visto=False)
    e.abajo(VK["lctrl"], tras_ms=1_000)
    e.abajo(VK["lwin"], tras_ms=40)
    assert e.disparos == [Accion("empezar", "mantener")]
    assert e.olvidadas == ["0xc0"]


def test_volver_a_pulsar_la_tecla_atascada_es_una_pulsacion_nueva() -> None:
    """No es autorrepetición: Windows ya la tenía por suelta. Cierra un dictado y abre otro."""
    e = Escenario()
    e.abajo(VK["lctrl"])
    e.abajo(VK["lwin"], tras_ms=250)
    e.arriba(VK["lwin"], tras_ms=2_000, visto=False)  # el dedo sigue en Ctrl
    e.abajo(VK["lwin"], tras_ms=1_000)
    assert e.disparos == [
        Accion("empezar", "mantener"), Accion("terminar", "mantener", 3_000),
        Accion("empezar", "mantener"),
    ]
    assert e.olvidadas == ["win"]


def test_encadenar_dictados_sin_soltar_ctrl() -> None:
    """Al pegar, Voziris suelta el Ctrl que sigue apretado. No por eso se olvida."""
    e = Escenario()
    e.abajo(VK["lctrl"])
    e.abajo(VK["lwin"], tras_ms=250)
    e.arriba(VK["lwin"], tras_ms=3_000)
    pegar(e)
    assert VK["lctrl"] not in e.windows  # para Windows, Ctrl ya está suelto
    e.abajo(VK["lwin"], tras_ms=500)  # el dedo sigue en Ctrl: otro dictado
    e.arriba(VK["lwin"], tras_ms=2_000)
    e.arriba(VK["lctrl"], tras_ms=100)
    assert e.disparos == [
        Accion("empezar", "mantener"), Accion("terminar", "mantener", 3_000),
        Accion("empezar", "mantener"), Accion("terminar", "mantener", 2_000),
    ]
    assert e.olvidadas == []
    e.abajo(VK["lwin"], tras_ms=1_000)  # ya soltado de verdad: Win solo no es nada
    assert len(e.disparos) == 4


def test_el_ctrl_del_pegado_no_da_por_apretado_un_ctrl_atascado() -> None:
    """Ctrl atascado en el hook y un pegado sin tocar el teclado (dictado desde la bandeja)."""
    e = Escenario()
    e.abajo(VK["lctrl"])
    e.arriba(VK["lctrl"], tras_ms=100, visto=False)
    pegar(e)
    e.abajo(VK["lwin"], tras_ms=1_000)  # Win solo, para abrir el menú Inicio
    assert e.disparos == []
    assert e.olvidadas == ["ctrl"]


def test_el_dictado_mantenido_se_cierra_si_sus_teclas_ya_no_estan() -> None:
    e = Escenario()
    e.abajo(VK["lctrl"])
    e.abajo(VK["lwin"], tras_ms=250)
    e.arriba(VK["lwin"], tras_ms=2_000, visto=False)
    e.arriba(VK["lctrl"], tras_ms=250, visto=False)
    e.abajo(VK["a"], tras_ms=5_000)  # la siguiente pulsación, la que sea
    assert e.disparos == [Accion("empezar", "mantener"), Accion("terminar", "mantener", 7_250)]


def test_mantener_apretado_clavar_no_lo_repite() -> None:
    """La tecla consumida no llega a Windows: su autorrepetición no es una pulsación nueva."""
    e = Escenario()
    e.abajo(VK["lctrl"])
    e.abajo(VK["shift"])
    e.abajo(VK["space"])
    for _ in range(3):
        e.abajo(VK["space"], tras_ms=500)
    e.arriba(VK["space"])
    assert e.disparos == [Accion("empezar", "clavar")]
    assert e.olvidadas == []
    assert e.consumidas == [(VK["space"], True)] * 4 + [(VK["space"], False)]


def test_el_registro_dice_con_que_tecla_se_dispara_y_que_se_olvida(
    caplog: pytest.LogCaptureFixture,
) -> None:
    atajos = Atajos(lambda m, d: None, lambda ms: None, lambda: None)
    for accion in (
        Accion("empezar", "mantener", tecla="ctrl"),
        Accion("empezar", "clavar", tecla="space", inyectada=True),
        Accion("olvidar", "", 41_200, tecla="win"),
        None,
    ):
        atajos._cola.put(accion)
    with caplog.at_level(logging.INFO, logger="voziris.atajos"):
        atajos._despachar()
    assert [r.getMessage() for r in caplog.records] == [
        "atajo «mantener» al pulsar ctrl",
        "atajo «clavar» al pulsar space (inyectada por otra aplicación)",
        "el hook no vio soltarse «win» (llevaba 41.2 s apretada): se da por suelta",
    ]


def test_el_callback_del_hook_reparte_lo_de_voziris_y_lo_de_otras_aplicaciones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sin instalar el hook: eventos como los que da Windows, directos al callback."""
    windows: set[int] = set()
    monkeypatch.setattr(winapi, "tecla_pulsada", windows.__contains__)
    user32 = SimpleNamespace(CallNextHookEx=lambda *argumentos: 0)
    monkeypatch.setattr("voziris.atajos._user32", user32, raising=False)
    atajos = Atajos(lambda m, d: None, lambda ms: None, lambda: None)
    atajos._detector.combinaciones = dict(COMBOS)

    def evento(vk: int, abajo: bool, marca: int = 0, flags: int = 0) -> None:
        datos = KBDLLHOOKSTRUCT(vk, 0, flags, 0, marca)
        atajos._al_recibir_tecla(0, WM_KEYDOWN if abajo else WM_KEYUP, ctypes.addressof(datos))
        (windows.add if abajo else windows.discard)(vk)

    evento(VK["lctrl"], True)
    evento(VK["lwin"], True, flags=LLKHF_INJECTED)  # p. ej., un gesto del ratón
    evento(VK["lwin"], False)
    evento(winapi.VK_LCONTROL, False, marca=winapi.MARCA_VOZIRIS)  # el pegado suelta Ctrl
    evento(VK["lwin"], True)  # con el dedo aún en Ctrl
    acciones: list[Accion | None] = []
    while not atajos._cola.empty():
        acciones.append(atajos._cola.get_nowait())
    assert [(a.tipo, a.tecla, a.inyectada) for a in acciones if a is not None] == [
        ("empezar", "win", True), ("terminar", "", False), ("empezar", "win", False),
    ]


# --- registro sin Windows ---------------------------------------------------------


def test_combinacion_invalida_es_config_invalida() -> None:
    atajos = Atajos(lambda m, d: None, lambda ms: None, lambda: None)
    with pytest.raises(ConfigInvalida, match="patata"):
        atajos.registrar({"mantener": "ctrl+patata", "clavar": "f9", "markdown": "f10",
                          "cancelar": "esc"})


# --- hook real (marcador win) --------------------------------------------------------


class Registro:
    def __init__(self) -> None:
        self.eventos: list[tuple[str, object]] = []
        self.hay = threading.Condition()
        self.en_curso = False
        self.tardar_s = 0.0

    def empezar(self, modo: Modo, destino: str) -> None:
        self._anotar("empezar", (modo, destino))
        time.sleep(self.tardar_s)

    def terminar(self, ms: int) -> None:
        self._anotar("terminar", ms)

    def cancelar(self) -> None:
        self._anotar("cancelar", None)

    def _anotar(self, tipo: str, dato: object) -> None:
        with self.hay:
            self.eventos.append((tipo, dato))
            self.hay.notify_all()

    def esperar(self, n: int, timeout: float = 3.0) -> list[tuple[str, object]]:
        with self.hay:
            self.hay.wait_for(lambda: len(self.eventos) >= n, timeout=timeout)
            return list(self.eventos)


# Combinaciones que no hacen nada en un terminal ni en un editor.
COMBOS_PRUEBA = {
    "mantener": "ctrl+shift+f11",
    "clavar": "ctrl+shift+f10",
    "markdown": "ctrl+shift+f9",
    "cancelar": "ctrl+shift+f8",
}


def _fisica(*vks: int, arriba: bool = False) -> None:
    """Inyecta pulsaciones SIN la marca de Voziris: el hook las trata como físicas."""
    orden = reversed(vks) if arriba else vks
    winapi.enviar([winapi.evento_tecla(vk, arriba=arriba, marca=0) for vk in orden])


@pytest.fixture
def atajos_reales() -> Iterator[tuple[Atajos, Registro]]:
    if not winapi.ES_WINDOWS:
        pytest.skip("solo en Windows")
    registro = Registro()
    atajos = Atajos(registro.empezar, registro.terminar, registro.cancelar,
                    en_curso=lambda: registro.en_curso)
    atajos.registrar(COMBOS_PRUEBA)
    assert atajos.activo
    try:
        yield atajos, registro
    finally:
        atajos.liberar()
        assert not atajos.activo


@pytest.mark.win
def test_hook_real_mantener_y_clavar(atajos_reales: tuple[Atajos, Registro]) -> None:
    _, registro = atajos_reales
    _fisica(VK["lctrl"], VK["shift"], VK["f11"])
    time.sleep(0.3)
    _fisica(VK["lctrl"], VK["shift"], VK["f11"], arriba=True)
    eventos = registro.esperar(2)
    assert eventos[0] == ("empezar", (Modo.MANTENER, "app_activa"))
    assert eventos[1][0] == "terminar" and 250 <= int(eventos[1][1]) < 1500  # type: ignore[call-overload]

    _fisica(VK["lctrl"], VK["shift"], VK["f10"])
    _fisica(VK["lctrl"], VK["shift"], VK["f10"], arriba=True)
    assert registro.esperar(3)[2] == ("empezar", (Modo.CLAVAR, "app_activa"))


@pytest.mark.win
def test_hook_real_markdown_y_cancelar(atajos_reales: tuple[Atajos, Registro]) -> None:
    _, registro = atajos_reales
    _fisica(VK["lctrl"], VK["shift"], VK["f8"])  # sin dictado en curso: no hace nada
    _fisica(VK["lctrl"], VK["shift"], VK["f8"], arriba=True)
    _fisica(VK["lctrl"], VK["shift"], VK["f9"])
    registro.en_curso = True
    _fisica(VK["lctrl"], VK["shift"], VK["f8"])
    _fisica(VK["lctrl"], VK["shift"], VK["f8"], arriba=True)
    _fisica(VK["lctrl"], VK["shift"], VK["f9"], arriba=True)
    eventos = registro.esperar(2)
    assert eventos == [("empezar", (Modo.MANTENER, "markdown")), ("cancelar", None)]


@pytest.mark.win
def test_un_callback_lento_no_bloquea_el_teclado(atajos_reales: tuple[Atajos, Registro]) -> None:
    """El hook encola y devuelve el control; el callback tarda en otro hilo."""
    _, registro = atajos_reales
    registro.tardar_s = 2.0
    t0 = time.perf_counter()
    _fisica(VK["lctrl"], VK["shift"], VK["f10"])
    _fisica(VK["lctrl"], VK["shift"], VK["f10"], arriba=True)
    # Mientras el callback duerme, el hook sigue atendiendo: otra pulsación
    # también se encola, y SendInput no se queda esperando.
    _fisica(VK["lctrl"], VK["shift"], VK["f10"])
    _fisica(VK["lctrl"], VK["shift"], VK["f10"], arriba=True)
    assert time.perf_counter() - t0 < 0.5
    assert len(registro.esperar(2, timeout=6)) == 2


@pytest.mark.win
def test_ignora_lo_que_inyecta_voziris(atajos_reales: tuple[Atajos, Registro]) -> None:
    _, registro = atajos_reales
    winapi.pulsar_combinacion(VK["lctrl"], VK["shift"], VK["f10"])  # con marca
    time.sleep(0.3)
    assert registro.eventos == []


@pytest.mark.win
def test_registrar_en_caliente_cambia_las_combinaciones(
    atajos_reales: tuple[Atajos, Registro],
) -> None:
    atajos, registro = atajos_reales
    atajos.registrar({**COMBOS_PRUEBA, "clavar": "ctrl+shift+f7"})
    _fisica(VK["lctrl"], VK["shift"], VK["f10"])
    _fisica(VK["lctrl"], VK["shift"], VK["f10"], arriba=True)
    _fisica(VK["lctrl"], VK["shift"], VK["f7"])
    _fisica(VK["lctrl"], VK["shift"], VK["f7"], arriba=True)
    assert registro.esperar(1) == [("empezar", (Modo.CLAVAR, "app_activa"))]


@pytest.mark.win
def test_sondeo_avisa_de_combinacion_tomada() -> None:
    """Se registra una combinación con RegisterHotKey y el sondeo la detecta."""
    import ctypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    assert user32.RegisterHotKey(None, 99, 0x0002 | 0x0004, VK["f6"])  # ctrl+shift+f6
    try:
        atajos = Atajos(lambda m, d: None, lambda ms: None, lambda: None)
        avisos = atajos.registrar({**COMBOS_PRUEBA, "markdown": "ctrl+shift+f6"})
        atajos.liberar()
    finally:
        user32.UnregisterHotKey(None, 99)
    assert len(avisos) == 1 and "ctrl+shift+f6" in avisos[0] and "markdown" in avisos[0]


def test_atajo_prefijo_cambia_el_destino_del_dictado_mantenido() -> None:
    """ctrl+win (mantener) es prefijo de ctrl+win+z (markdown): la Z cambia el destino."""
    e = Escenario()
    e.detector.combinaciones = {**COMBOS, "markdown": teclas.analizar("ctrl+win+z")}
    e.abajo(VK["lctrl"])
    e.abajo(VK["lwin"])
    assert e.acciones == [Accion("empezar", "mantener")]
    e.abajo(VK["z"], tras_ms=120)
    assert e.acciones[-1] == Accion("cambiar", "markdown")
    assert e.consumidas == [(VK["z"], True)]  # la z no llega a la aplicación
    e.arriba(VK["z"], tras_ms=900)
    assert e.acciones[-1] == Accion("terminar", "markdown", 1020)
    e.arriba(VK["lwin"])
    e.arriba(VK["lctrl"])
    assert [a.tipo for a in e.acciones] == ["empezar", "cambiar", "terminar"]


def test_tras_soltar_la_tecla_del_prefijo_otra_pulsacion_empieza_de_nuevo() -> None:
    e = Escenario()
    e.detector.combinaciones = {**COMBOS, "markdown": teclas.analizar("ctrl+win+z")}
    e.abajo(VK["lctrl"])
    e.abajo(VK["lwin"])
    e.abajo(VK["z"])
    e.arriba(VK["z"], tras_ms=500)  # soltar la z termina el dictado de markdown
    assert e.acciones[-1].tipo == "terminar" and e.acciones[-1].atajo == "markdown"
    e.abajo(VK["z"])  # otra z con ctrl+win aún pulsado: empieza otro markdown
    assert e.acciones[-1] == Accion("empezar", "markdown")


def test_clavar_markdown_dispara_al_pulsar_y_el_atajo_vacio_se_desactiva() -> None:
    e = Escenario()
    e.detector.combinaciones = {**COMBOS, "clavar_markdown": teclas.analizar("ctrl+shift+m")}
    e.abajo(VK["lctrl"])
    e.abajo(VK["shift"])
    e.abajo(VK["m"])
    e.arriba(VK["m"])
    assert e.acciones == [Accion("empezar", "clavar_markdown")]
    assert e.consumidas == [(VK["m"], True), (VK["m"], False)]
    atajos = Atajos(lambda m, d: None, lambda ms: None, lambda: None)
    with pytest.raises(ConfigInvalida):
        atajos.registrar({"mantener": "ctrl+patata", "clavar_markdown": ""})
