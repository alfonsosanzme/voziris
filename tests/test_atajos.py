"""Tests de los atajos globales (VOZ-02).

El `Detector` es puro y se prueba con secuencias de teclas en cualquier
sistema. Los marcados `win` instalan el hook real e inyectan pulsaciones con
`SendInput` sin la marca de Voziris, para que el hook las tome por físicas.
Usan combinaciones que no hacen nada en la ventana que tenga el foco.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator

import pytest

from voziris import teclas, winapi
from voziris.atajos import Accion, Atajos, Detector
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
    def __init__(self, en_curso: bool = False) -> None:
        self.en_curso = en_curso
        self.detector = Detector(dict(COMBOS), lambda: self.en_curso)
        self.reloj = 0.0
        self.acciones: list[Accion] = []
        self.consumidas: list[tuple[int, bool]] = []

    def abajo(self, vk: int, tras_ms: int = 0) -> None:
        self._tecla(vk, True, tras_ms)

    def arriba(self, vk: int, tras_ms: int = 0) -> None:
        self._tecla(vk, False, tras_ms)

    def _tecla(self, vk: int, abajo: bool, tras_ms: int) -> None:
        self.reloj += tras_ms / 1000
        acciones, consumir = self.detector.tecla(vk, abajo, self.reloj)
        self.acciones += acciones
        if consumir:
            self.consumidas.append((vk, abajo))


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
