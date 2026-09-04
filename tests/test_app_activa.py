"""Tests de la inserción en la aplicación activa (VOZ-12).

Los puros sustituyen `winapi` por un doble que apunta la secuencia de
llamadas: así se comprueba el orden (guardar, pegar, esperar, restaurar) en
cualquier sistema. Los marcados `win` lanzan una ventana Tk en OTRO proceso
(`ventana_receptora.py`), como una aplicación real, y escriben en ella de
verdad; se saltan si no consigue el primer plano, para no teclear en la
ventana del usuario.
"""

from __future__ import annotations

import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from voziris import winapi
from voziris.destinos import app_activa as mod
from voziris.destinos.app_activa import AppActiva
from voziris.errores import EntregaFallida
from voziris.tipos import Contexto, Modo, Nivel

CTX = Contexto(destino="app_activa", modo=Modo.MANTENER, app_activa=None, hay_red=True,
               nivel=Nivel.LIMPIO)


# --- eventos de SendInput (puros) -------------------------------------------


def test_eventos_texto_con_acentos_saltos_y_emoji() -> None:
    eventos = winapi.eventos_texto("ñ\n🎙️")
    # ñ: abajo+arriba · Enter: abajo+arriba · 🎙 (2 unidades) + U+FE0F: 3×2
    assert len(eventos) == 2 + 2 + 6
    assert eventos[0].union.ki.wScan == ord("ñ")
    assert eventos[0].union.ki.dwFlags == winapi.KEYEVENTF_UNICODE
    assert eventos[1].union.ki.dwFlags == winapi.KEYEVENTF_UNICODE | winapi.KEYEVENTF_KEYUP
    assert eventos[2].union.ki.wVk == winapi.VK_RETURN and eventos[2].union.ki.wScan == 0
    assert (eventos[4].union.ki.wScan, eventos[6].union.ki.wScan) == (0xD83C, 0xDF99)
    assert all(e.union.ki.dwExtraInfo == winapi.MARCA_VOZIRIS for e in eventos)


def test_eventos_combinacion_suelta_en_orden_inverso() -> None:
    eventos = winapi.eventos_combinacion(winapi.VK_CONTROL, winapi.VK_V)
    assert [(e.union.ki.wVk, e.union.ki.dwFlags) for e in eventos] == [
        (winapi.VK_CONTROL, 0), (winapi.VK_V, 0),
        (winapi.VK_V, winapi.KEYEVENTF_KEYUP), (winapi.VK_CONTROL, winapi.KEYEVENTF_KEYUP),
    ]


def test_tamano_de_input_es_el_de_windows() -> None:
    import ctypes

    assert ctypes.sizeof(winapi.INPUT) == 40 if sys.maxsize > 2**32 else 28


# --- secuencia de entrega (puros, con winapi falso) ---------------------------


class WinapiFalso:
    def __init__(self, portapapeles: str | None = "lo que tenía copiado") -> None:
        self.portapapeles = portapapeles
        self.pasos: list[Any] = []
        self.falla_portapapeles = False
        self.falla_envio = False

    def leer_portapapeles(self) -> str | None:
        if self.falla_portapapeles:
            raise OSError("ocupado")
        self.pasos.append(("leer", self.portapapeles))
        return self.portapapeles

    def escribir_portapapeles(self, texto: str) -> None:
        self.portapapeles = texto
        self.pasos.append(("escribir", texto))

    def soltar_modificadores(self) -> list[int]:
        self.pasos.append(("soltar",))
        return []

    def pulsar_combinacion(self, *vks: int) -> None:
        if self.falla_envio:
            raise OSError("SendInput: 0/4")
        self.pasos.append(("pulsar", vks))

    def teclear(self, texto: str) -> None:
        self.pasos.append(("teclear", texto))

    def ejecutable_en_primer_plano(self) -> str | None:
        return "notepad.exe"


@pytest.fixture
def falso(monkeypatch: pytest.MonkeyPatch) -> Iterator[WinapiFalso]:
    doble = WinapiFalso()
    for nombre in ("leer_portapapeles", "escribir_portapapeles", "soltar_modificadores",
                   "pulsar_combinacion", "teclear", "ejecutable_en_primer_plano"):
        monkeypatch.setattr(mod.winapi, nombre, getattr(doble, nombre))
    monkeypatch.setattr(mod.time, "sleep", lambda s: doble.pasos.append(("esperar", s)))
    yield doble


def test_pegar_guarda_pega_espera_y_restaura(falso: WinapiFalso) -> None:
    entrega = AppActiva().entregar("Hola.", CTX)
    assert entrega.ok and entrega.detalle == "pegado en notepad.exe"
    assert falso.pasos == [
        ("leer", "lo que tenía copiado"),
        ("escribir", "Hola."),
        ("esperar", mod.ESPERA_TRAS_ESCRIBIR_MS / 1000),
        ("soltar",),
        ("pulsar", (winapi.VK_CONTROL, winapi.VK_V)),
        ("esperar", mod.RETARDO_RESTAURACION_MS / 1000),
        ("escribir", "lo que tenía copiado"),
    ]
    assert falso.portapapeles == "lo que tenía copiado"


def test_sin_restaurar_no_lee_ni_espera_a_restaurar(falso: WinapiFalso) -> None:
    AppActiva(restaurar_portapapeles=False).entregar("Hola.", CTX)
    assert [p[0] for p in falso.pasos] == ["escribir", "esperar", "soltar", "pulsar"]
    assert falso.portapapeles == "Hola."


def test_portapapeles_sin_texto_no_se_restaura(falso: WinapiFalso) -> None:
    """Había una imagen copiada: no se puede devolver, y el texto dictado se queda."""
    falso.portapapeles = None
    AppActiva().entregar("Hola.", CTX)
    assert [p[0] for p in falso.pasos] == [
        "leer", "escribir", "esperar", "soltar", "pulsar", "esperar",
    ]
    assert falso.portapapeles == "Hola."


def test_tecleo_no_toca_el_portapapeles(falso: WinapiFalso) -> None:
    entrega = AppActiva(metodo="tecleo").entregar("Hola, ñandú.", CTX)
    assert entrega.detalle == "tecleado en notepad.exe"
    assert falso.pasos == [("soltar",), ("teclear", "Hola, ñandú.")]
    assert falso.portapapeles == "lo que tenía copiado"


def test_auto_enter_manda_enter_al_final(falso: WinapiFalso) -> None:
    AppActiva(auto_enter=True).entregar("Hola.", CTX)
    assert falso.pasos[-1] == ("pulsar", (winapi.VK_RETURN,))
    falso.pasos.clear()
    AppActiva().entregar("Hola.", CTX)
    assert ("pulsar", (winapi.VK_RETURN,)) not in falso.pasos


def test_usa_la_app_del_contexto_si_viene(falso: WinapiFalso) -> None:
    ctx = Contexto(destino="app_activa", modo=Modo.MANTENER, app_activa="chrome.exe",
                   hay_red=True, nivel=Nivel.LIMPIO)
    assert AppActiva().entregar("Hola.", ctx).detalle == "pegado en chrome.exe"


def test_texto_vacio_no_hace_nada(falso: WinapiFalso) -> None:
    assert AppActiva().entregar("", CTX).ok
    assert falso.pasos == []


def test_portapapeles_ocupado_es_entrega_fallida(falso: WinapiFalso) -> None:
    falso.falla_portapapeles = True
    with pytest.raises(EntregaFallida, match="notepad.exe"):
        AppActiva().entregar("Hola.", CTX)


def test_ventana_elevada_es_entrega_fallida_con_el_portapapeles_restaurado(
    falso: WinapiFalso,
) -> None:
    falso.falla_envio = True
    with pytest.raises(EntregaFallida, match="SendInput"):
        AppActiva().entregar("Hola.", CTX)
    # El fallo llegó antes de restaurar: el dictado se queda en el portapapeles
    # a propósito, para que el usuario pueda pegarlo él mismo con Ctrl+V.
    assert falso.portapapeles == "Hola."


def test_metodo_desconocido() -> None:
    with pytest.raises(ValueError):
        AppActiva(metodo="magia")


# --- de verdad, sobre una ventana Tk (marcador win) ---------------------------


RECEPTOR = Path(__file__).with_name("ventana_receptora.py")


class Receptor:
    """Una ventana Tk en OTRO proceso que vuelca a un archivo lo que recibe."""

    def __init__(self, carpeta: Path) -> None:
        self.salida = carpeta / "salida.txt"
        self.parar = carpeta / "parar"
        self.proceso = subprocess.Popen([sys.executable, str(RECEPTOR), str(self.salida), "15"])

    def esperar_primer_plano(self) -> bool:
        limite = time.monotonic() + 4
        while time.monotonic() < limite:
            hwnd = winapi.ventana_por_titulo("Voziris receptor")
            if hwnd and winapi.ventana_en_primer_plano() == hwnd:
                time.sleep(0.15)  # que termine de activarse antes de inyectar teclas
                return True
            time.sleep(0.05)
        return False

    def contenido(self, esperar_s: float = 1.0) -> str:
        """Lo que hay en la ventana una vez deja de cambiar (o tras `esperar_s`)."""
        limite = time.monotonic() + esperar_s
        anterior, estable_desde = "", time.monotonic()
        while time.monotonic() < limite:
            time.sleep(0.05)
            try:
                actual = self.salida.read_text(encoding="utf-8")
            except OSError:  # justo durante el os.replace del receptor
                continue
            if actual != anterior:
                anterior, estable_desde = actual, time.monotonic()
            elif actual and time.monotonic() - estable_desde > 0.3:
                break
        return anterior

    def cerrar(self) -> None:
        self.parar.touch()
        try:
            self.proceso.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.proceso.kill()


@pytest.fixture
def receptor(tmp_path: Path) -> Iterator[Receptor]:
    """Ventana de destino en primer plano. Se salta si no lo consigue: teclear
    en la ventana del usuario no es una opción."""
    if not winapi.ES_WINDOWS:
        pytest.skip("solo en Windows")
    guardado = winapi.leer_portapapeles()
    r = Receptor(tmp_path)
    try:
        if not r.esperar_primer_plano():
            pytest.skip("la ventana receptora no consiguió el primer plano")
        yield r
    finally:
        r.cerrar()
        if guardado is not None:
            winapi.escribir_portapapeles(guardado)


TEXTO = "Hola, ñandú «cita» — año 2026 🎙️ ¿vale?"


def _pegar_con_un_reintento(receptor: Receptor, destino: AppActiva, texto: str) -> str:
    """Entrega y, si la ventana no recibió NADA, repite una vez.

    Tk no reintenta si encuentra el portapapeles abierto por el historial de
    Windows en el instante del pegado; una aplicación real sí suele hacerlo.
    Un pegado parcial o incorrecto NO se reintenta: eso sí sería un fallo.
    """
    destino.entregar(texto, CTX)
    recibido = receptor.contenido()
    if recibido.strip() == "":
        destino.entregar(texto, CTX)
        recibido = receptor.contenido()
    return recibido


@pytest.mark.win
def test_pegado_real_y_portapapeles_restaurado(receptor: Receptor) -> None:
    winapi.escribir_portapapeles("copiado antes")
    recibido = _pegar_con_un_reintento(receptor, AppActiva(), TEXTO)
    assert recibido == TEXTO
    assert winapi.leer_portapapeles() == "copiado antes"
    assert AppActiva().entregar("", CTX).ok


@pytest.mark.win
def test_tecleo_real_con_acentos_y_emoji(receptor: Receptor) -> None:
    winapi.escribir_portapapeles("intacto")
    AppActiva(metodo="tecleo").entregar("Ñu añejo\n🎙️ fin", CTX)
    assert receptor.contenido() == "Ñu añejo\n🎙️ fin"
    assert winapi.leer_portapapeles() == "intacto"


@pytest.mark.win
def test_auto_enter_real(receptor: Receptor) -> None:
    recibido = _pegar_con_un_reintento(receptor, AppActiva(auto_enter=True), "línea")
    assert recibido.endswith("línea\n")


@pytest.mark.win
def test_app_en_primer_plano_es_otro_python(receptor: Receptor) -> None:
    nombre = AppActiva.app_en_primer_plano()
    assert nombre is not None and nombre.startswith("python")


@pytest.mark.win
@pytest.mark.parametrize("retardo", [0, 10, 40])
def test_medir_retardo_minimo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, retardo: int
) -> None:
    """Cuánto tarda la ventana en leer el portapapeles tras el Ctrl+V.

    Informa, no exige: imprime si con este retardo el pegado llegó antes de la
    restauración. Es la referencia de RETARDO_RESTAURACION_MS.
    """
    if not winapi.ES_WINDOWS:
        pytest.skip("solo en Windows")
    guardado = winapi.leer_portapapeles()
    r = Receptor(tmp_path)
    try:
        if not r.esperar_primer_plano():
            pytest.skip("la ventana receptora no consiguió el primer plano")
        monkeypatch.setattr(mod, "RETARDO_RESTAURACION_MS", retardo)
        winapi.escribir_portapapeles("restaurado")
        AppActiva().entregar("dictado", CTX)
        recibido = r.contenido()
        print(f"\nretardo {retardo} ms → recibido {recibido!r}")
    finally:
        r.cerrar()
        if guardado is not None:
            winapi.escribir_portapapeles(guardado)
