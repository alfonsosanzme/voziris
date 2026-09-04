"""Tests del indicador flotante (VOZ-22). Necesitan Tk; la comprobación de foco, Windows."""

from __future__ import annotations

import sys
import time
import tkinter as tk
from collections.abc import Iterator
from typing import Any

import pytest

from voziris import winapi
from voziris.tipos import Modo
from voziris.ui import hud as mod
from voziris.ui.hud import Hud


@pytest.fixture(scope="module")
def raiz() -> Iterator[tk.Tk]:
    try:
        r = tk.Tk()
    except tk.TclError as e:
        pytest.skip(f"sin Tk: {e}")
    r.withdraw()
    yield r
    r.destroy()


def _bombear(raiz: tk.Tk, segundos: float) -> None:
    limite = time.monotonic() + segundos
    while time.monotonic() < limite:
        raiz.update()
        time.sleep(0.01)


def _nuevo(raiz: tk.Tk, nivel: float = 0.5) -> Hud:
    return Hud(raiz, nivel=lambda: nivel, en_hilo_tk=lambda fn: fn(), animaciones=True)


def test_mostrar_y_ocultar(raiz: tk.Tk) -> None:
    h = _nuevo(raiz)
    assert not h.visible
    h.mostrar_grabando(Modo.MANTENER)
    _bombear(raiz, 0.1)
    assert h.visible and h._ventana is not None
    assert h._lienzo is not None
    assert "suelta" in h._lienzo.itemcget(h._texto_id, "text")
    h.mostrar_grabando(Modo.CLAVAR)
    _bombear(raiz, 0.05)
    assert "clavado" in h._lienzo.itemcget(h._texto_id, "text")  # distingue los modos
    h.mostrar_procesando()
    _bombear(raiz, 0.05)
    assert "Procesando" in h._lienzo.itemcget(h._texto_id, "text")
    h.ocultar()
    _bombear(raiz, 0.05)
    assert not h.visible
    h.destruir()


def test_la_barra_sigue_al_nivel_a_30_fps(raiz: tk.Tk) -> None:
    nivel = {"v": 0.0}
    h = Hud(raiz, nivel=lambda: nivel["v"], en_hilo_tk=lambda fn: fn(), animaciones=False)
    h.mostrar_grabando()
    _bombear(raiz, 0.1)
    assert h._lienzo is not None
    ancho0 = h._lienzo.coords(h._barra_id)[2]
    nivel["v"] = 1.0
    _bombear(raiz, 0.1)  # tres fotogramas: sin animación salta al valor
    ancho1 = h._lienzo.coords(h._barra_id)[2]
    assert ancho1 - ancho0 > 200
    assert h._lienzo.itemcget(h._barra_id, "fill") == mod.BARRA_ALTA  # > 0,9: pico
    h.destruir()


def test_con_animaciones_la_barra_se_desliza(raiz: tk.Tk) -> None:
    h = _nuevo(raiz, nivel=1.0)
    h.mostrar_grabando()
    _bombear(raiz, 0.04)  # un fotograma
    assert h._lienzo is not None
    ancho = h._lienzo.coords(h._barra_id)[2]
    assert 16 < ancho < mod.ANCHO - 16  # a medio camino, no de golpe
    h.destruir()


def test_aviso_se_queda_y_luego_desaparece(raiz: tk.Tk, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "AVISO_MS", 200)
    h = _nuevo(raiz)
    h.mostrar_grabando()
    h.mostrar_procesando()
    h.ocultar(aviso="Sin conexión: transcrito en local")
    _bombear(raiz, 0.05)
    assert h.visible and h._lienzo is not None
    assert "Sin conexión" in h._lienzo.itemcget(h._texto_id, "text")
    _bombear(raiz, 0.4)
    assert not h.visible
    h.aviso("solo un aviso, sin dictado")
    _bombear(raiz, 0.05)
    assert h.visible
    _bombear(raiz, 0.4)
    assert not h.visible
    h.destruir()


def test_llamadas_desde_otro_hilo_pasan_por_en_hilo_tk(raiz: tk.Tk) -> None:
    encoladas: list[Any] = []
    h = Hud(raiz, en_hilo_tk=encoladas.append, animaciones=True)
    h.mostrar_grabando()
    h.actualizar_nivel(0.3)
    h.ocultar()
    assert len(encoladas) == 3 and not h.visible  # nada tocó Tk todavía
    for fn in encoladas:
        fn()
    assert not h.visible
    h.destruir()


@pytest.mark.skipif(sys.platform != "win32", reason="estilos de ventana de Windows")
def test_no_roba_el_foco_y_esta_fuera_de_alt_tab(raiz: tk.Tk) -> None:
    antes = winapi.ventana_en_primer_plano()
    h = _nuevo(raiz)
    h.mostrar_grabando()
    _bombear(raiz, 0.3)
    assert h._ventana is not None
    hwnd = Hud.hwnd(h._ventana)
    estilo = winapi.estilo_extendido(hwnd)
    assert estilo & winapi.WS_EX_NOACTIVATE
    assert estilo & winapi.WS_EX_TOOLWINDOW
    assert estilo & winapi.WS_EX_TOPMOST
    assert winapi.ventana_en_primer_plano() == antes  # el foco sigue donde estaba
    assert winapi.ventana_en_primer_plano() != hwnd
    h.ocultar()
    _bombear(raiz, 0.05)
    h.destruir()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows")
def test_se_coloca_en_el_borde_inferior_del_monitor_activo(raiz: tk.Tk) -> None:
    x, y, ancho, alto = winapi.area_de_trabajo_activa()
    assert ancho > 200 and alto > 200
    h = _nuevo(raiz)
    h.mostrar_grabando()
    _bombear(raiz, 0.2)
    assert h._ventana is not None
    geo = h._ventana.geometry()  # "300x64+X+Y"
    _, pos = geo.split("+", 1)
    px, py = (int(v) for v in pos.split("+"))
    assert x <= px <= x + ancho - mod.ANCHO
    assert y + alto - mod.ALTO - mod.MARGEN_INFERIOR - 2 <= py <= y + alto
    h.destruir()


def test_animaciones_activas_devuelve_bool() -> None:
    assert isinstance(winapi.animaciones_activas(), bool)
