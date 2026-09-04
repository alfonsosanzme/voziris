"""Tests de la bandeja, los iconos y el arranque con Windows (VOZ-03).

El menú se construye con las clases reales de pystray y se inspecciona sin
mostrar nada. El icono real solo se muestra en el test marcado `win`.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from voziris.tipos import EntradaHistorial
from voziris.ui import iconos
from voziris.ui.bandeja import (
    NOMBRE_ACCESO_DIRECTO,
    AccionesBandeja,
    Bandeja,
    leer_acceso_directo,
    resumen,
    texto_acerca_de,
)


class Llamadas:
    def __init__(self) -> None:
        self.lista: list[tuple[str, object]] = []

    def acciones(self, con_acerca_de: bool = True) -> AccionesBandeja:
        return AccionesBandeja(
            dictar_ahora=lambda: self.lista.append(("dictar", None)),
            dictar_markdown=lambda: self.lista.append(("dictar_md", None)),
            alternar_corte=lambda: self.lista.append(("corte", None)),
            abrir_ajustes=lambda: self.lista.append(("ajustes", None)),
            cambiar_motor=lambda m: self.lista.append(("motor", m)),
            reintentar=lambda i: self.lista.append(("reintentar", i)),
            borrar_entrada=lambda i: self.lista.append(("borrar", i)),
            salir=lambda: self.lista.append(("salir", None)),
            acerca_de=(lambda: self.lista.append(("acerca", None))) if con_acerca_de else None,
        )


def _entrada(texto: str, entregado: bool = True, indice: int = 0) -> EntradaHistorial:
    return EntradaHistorial(
        momento=datetime(2026, 9, 4, 19, 42), texto=texto, motor="local",
        destino="app_activa", entregado=entregado, ms_total=900, duracion_audio_s=4.0,
        indice=indice,
    )


def _items(menu: Any) -> list[Any]:
    return list(menu.items)


def _texto(item: Any) -> str:
    return str(item.text)


# --- iconos ---------------------------------------------------------------------


def test_cuatro_estados_distintos_tambien_en_gris() -> None:
    grises = {}
    for estado in iconos.ESTADOS:
        img = iconos.imagen(estado, 64, iconos.OSCURO)
        assert img.size == (64, 64) and img.mode == "RGBA"
        # Fondo transparente y algo dibujado.
        alfa = np.asarray(img)[:, :, 3]
        assert alfa[0, 0] == 0 and alfa.max() == 255
        grises[estado] = np.asarray(img.convert("LA"))
    estados = list(grises)
    for i, a in enumerate(estados):
        for b in estados[i + 1 :]:
            diferencia = np.abs(grises[a].astype(int) - grises[b].astype(int)).sum()
            assert diferencia > 10_000, f"{a} y {b} se parecen demasiado en gris"


def test_estado_desconocido() -> None:
    with pytest.raises(ValueError):
        iconos.imagen("dormido")


def test_guardar_ico(tmp_path: Path) -> None:
    from PIL import Image

    ruta = tmp_path / "voziris.ico"
    iconos.guardar_ico(ruta)
    with Image.open(ruta) as ico:
        assert ico.format == "ICO"
        assert (256, 256) in ico.info["sizes"] and (16, 16) in ico.info["sizes"]


def test_el_ico_del_repositorio_esta_al_dia() -> None:
    """`assets/voziris.ico` se genera desde iconos.py; si cambia el dibujo, se regenera."""
    ruta = Path(__file__).resolve().parents[1] / "assets" / "voziris.ico"
    assert ruta.exists(), "falta assets/voziris.ico: python -m voziris.ui.iconos"


# --- menú -------------------------------------------------------------------------


def test_menu_completo_en_orden() -> None:
    bandeja = Bandeja(Llamadas().acciones())
    textos = [_texto(i) for i in _items(bandeja.construir_menu())]
    assert textos == [
        "Dictar ahora", "Dictar al Markdown", "Cortar al callar (modo clavar)",
        "Últimos dictados", "Motor", "Ajustes…", "Acerca de Voziris",
        "- - - -", "Salir",  # el separador de pystray se muestra así
    ]


def test_las_acciones_del_menu_llaman_a_la_aplicacion() -> None:
    llamadas = Llamadas()
    bandeja = Bandeja(llamadas.acciones(), motor_actual=lambda: "api")
    items = {_texto(i): i for i in _items(bandeja.construir_menu())}
    items["Dictar ahora"](None)
    items["Dictar al Markdown"](None)
    items["Cortar al callar (modo clavar)"](None)
    assert bool(items["Cortar al callar (modo clavar)"].checked)
    items["Ajustes…"](None)
    items["Acerca de Voziris"](None)
    items["Salir"](None)
    motores = _items(items["Motor"].submenu)
    assert [_texto(m) for m in motores] == ["Local (sin conexión)", "API", "Automático"]
    assert [bool(m.checked) for m in motores] == [False, True, False]
    assert all(m.radio for m in motores)
    motores[0](None)
    assert llamadas.lista == [
        ("dictar", None), ("dictar_md", None), ("corte", None), ("ajustes", None),
        ("acerca", None), ("salir", None), ("motor", "local"),
    ]


def test_ultimos_dictados_dinamicos_con_reintento_y_borrado() -> None:
    llamadas = Llamadas()
    historial: list[EntradaHistorial] = []
    bandeja = Bandeja(llamadas.acciones(), ultimas=lambda: historial)
    submenu = {_texto(i): i for i in _items(bandeja.construir_menu())}["Últimos dictados"].submenu
    vacio = _items(submenu)
    assert len(vacio) == 1 and not vacio[0].enabled

    historial[:] = [
        _entrada("Llamar a la gestoría por lo del modelo 111, que se acaba el plazo.", indice=7),
        _entrada("Idea para Kairis", entregado=False, indice=8),
    ]
    entradas = _items(submenu)
    assert [_texto(e) for e in entradas] == [
        "19:42 · Llamar a la gestoría por lo del modelo 11…",
        "19:42 · Idea para Kairis ⚠",
    ]
    acciones = _items(entradas[1].submenu)
    assert [_texto(a) for a in acciones] == ["Volver a entregar", "Borrar del historial"]
    acciones[0](None)
    acciones[1](None)
    assert llamadas.lista == [("reintentar", 8), ("borrar", 8)]  # el índice del historial

    historial[:] = [_entrada(f"dictado {n}") for n in range(15)]
    assert len(_items(submenu)) == 10


def test_resumen() -> None:
    assert resumen(_entrada("  hola\n  mundo  ")) == "19:42 · hola mundo"
    assert resumen(_entrada("a" * 100), largo=10) == "19:42 · aaaaaaaaa…"


def test_acerca_de_lleva_la_atribucion_a_nvidia() -> None:
    texto = texto_acerca_de("1.2.3")
    assert "Voziris 1.2.3" in texto and "MIT" in texto
    assert "NVIDIA" in texto and "CC-BY-4.0" in texto and "parakeet-tdt-0.6b-v3" in texto


def test_acerca_de_sin_accion_avisa(monkeypatch: pytest.MonkeyPatch) -> None:
    llamadas = Llamadas()
    bandeja = Bandeja(llamadas.acciones(con_acerca_de=False))
    avisos: list[tuple[str, str]] = []
    monkeypatch.setattr(bandeja, "avisar", lambda t, titulo="": avisos.append((t, titulo)))
    {_texto(i): i for i in _items(bandeja.construir_menu())}["Acerca de Voziris"](None)
    assert avisos and "NVIDIA" in avisos[0][0] and avisos[0][1] == "Acerca de Voziris"


def test_estado_sin_icono_y_con_nombre_malo() -> None:
    bandeja = Bandeja(Llamadas().acciones())
    bandeja.estado("grabando")
    assert bandeja.estado_actual == "grabando"
    with pytest.raises(ValueError):
        bandeja.estado("dormido")
    bandeja.avisar("sin icono no revienta")
    bandeja.cerrar()


# --- arranque con Windows ------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="accesos directos de Windows")
def test_configurar_arranque_crea_y_borra_el_acceso_directo(tmp_path: Path) -> None:
    carpeta = tmp_path / "Startup"
    assert not Bandeja.arranque_activo(carpeta)
    assert Bandeja.configurar_arranque(True, carpeta) is True
    acceso = carpeta / NOMBRE_ACCESO_DIRECTO
    assert acceso.exists() and Bandeja.arranque_activo(carpeta)
    ejecutable, argumentos, trabajo = leer_acceso_directo(acceso)
    esperado = Bandeja.destino_arranque()
    assert (ejecutable.lower(), argumentos, trabajo.lower()) == (
        esperado[0].lower(), esperado[1], esperado[2].lower()
    )
    assert Bandeja.configurar_arranque(True, carpeta) is False  # ya estaba: no cambia nada
    assert Bandeja.configurar_arranque(False, carpeta) is True
    assert not acceso.exists()
    assert Bandeja.configurar_arranque(False, carpeta) is False


def test_destino_de_arranque_congelado_y_en_desarrollo(monkeypatch: pytest.MonkeyPatch) -> None:
    ejecutable, argumentos, _ = Bandeja.destino_arranque()
    assert argumentos == "-m voziris" and ejecutable.lower().endswith(("pythonw.exe", "python.exe"))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Apps\voziris\voziris.exe")
    assert Bandeja.destino_arranque() == (r"C:\Apps\voziris\voziris.exe", "", r"C:\Apps\voziris")


def test_carpeta_startup_es_la_del_usuario() -> None:
    ruta = Bandeja.carpeta_startup()
    assert ruta.name == "Startup" and "Roaming" in str(ruta)


# --- icono real ------------------------------------------------------------------------


@pytest.mark.win
def test_icono_real_se_muestra_cambia_de_estado_y_se_cierra() -> None:
    if sys.platform != "win32":
        pytest.skip("solo en Windows")
    bandeja = Bandeja(Llamadas().acciones())
    hilo = bandeja.mostrar_en_hilo()
    assert hilo.is_alive()
    for estado in iconos.ESTADOS:
        bandeja.estado(estado)
        time.sleep(0.15)
    bandeja.actualizar_menu()
    bandeja.cerrar()
    assert not hilo.is_alive()
