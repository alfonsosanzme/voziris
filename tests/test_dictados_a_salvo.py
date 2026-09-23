"""Tests de VOZ-80: ningún dictado se pierde, todos se ven en el menú, y se pueden rehacer.

Con el historial y los pendientes de verdad, en disco: el fallo que motivó esto
se coló porque los tests miraban una capa que no era la que falla (el menú de
Python, que sí es dinámico, en vez del menú nativo de Windows, que no lo era).
"""

from __future__ import annotations

import ctypes
import logging
import sys
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from voziris import orquestador as modulo_orquestador
from voziris.errores import TranscripcionFallida
from voziris.historial import Historial
from voziris.orquestador import Estado, Orquestador
from voziris.pendientes import Pendientes
from voziris.tipos import SAMPLE_RATE, Audio, EntradaHistorial, Entrega, Modo, Transcripcion
from voziris.ui import iconos
from voziris.ui.bandeja import AccionesBandeja, Bandeja, rehacer_menu_al_abrir

# --- el banco: captura, motor y destino falsos; historial y pendientes de verdad ----------------


class Captura:
    """Como la real: escribe el pendiente según «se habla» y lo deja al terminar."""

    oyente_bloques: Any = None

    def __init__(self, pendientes: Pendientes, segundos: float) -> None:
        self._pendientes = pendientes
        self._segundos = segundos
        self.ultimo_pendiente: Path | None = None

    def empezar_dictado(self) -> None: ...

    def terminar_dictado(self) -> Audio:
        escritor = self._pendientes.abrir()
        assert escritor is not None
        muestras = np.full(int(SAMPLE_RATE * self._segundos), 0.1, dtype=np.float32)
        for i in range(0, len(muestras), 512):
            escritor.escribir(muestras[i : i + 512])
        self.ultimo_pendiente = escritor.cerrar()
        return Audio(muestras=muestras)

    def cancelar_dictado(self) -> None: ...


class Motor:
    nombre = "local"
    requiere_red = False

    def __init__(self) -> None:
        self.texto = "Hola, mundo."
        self.falla: Exception | None = None
        self.tarda_s = 0.0

    def precalentar(self) -> None: ...

    def disponible(self) -> bool:
        return True

    def transcribir(self, audio: Audio, idioma: str) -> Transcripcion:
        time.sleep(self.tarda_s)
        if self.falla is not None:
            raise self.falla
        return Transcripcion(self.texto, idioma, self.nombre, 5, audio.duracion_s)


class Destino:
    """Pega; antes, hace lo que se le diga: mirar qué hay guardado, o reventar."""

    nombre = "app_activa"

    def __init__(self) -> None:
        self.textos: list[str] = []
        self.al_pegar: Callable[[], None] = lambda: None

    def entregar(self, texto: str, ctx: object) -> Entrega:
        self.al_pegar()
        self.textos.append(texto)
        return Entrega(ok=True, detalle="pegado en prueba.exe")


def _montar(
    tmp_path: Path, conservar: bool = True, segundos: float = 2.0, **opciones: Any
) -> tuple[Orquestador, Historial, Pendientes, Motor, Destino]:
    pendientes = Pendientes(tmp_path / "historial" / "pendientes")
    historial = Historial(tmp_path / "historial" / "dictados.jsonl", conservar_audio=conservar)
    motor, destino = Motor(), Destino()
    orq = Orquestador(
        Captura(pendientes, segundos), motor, {"app_activa": destino}, [],
        historial=historial, pendientes=pendientes, **opciones,
    )
    orq.arrancar()
    assert orq.motor_listo.wait(3)
    return orq, historial, pendientes, motor, destino


def _dictar(orq: Orquestador) -> None:
    assert orq.empezar(Modo.MANTENER, "app_activa")
    orq.terminar(1500)
    limite = time.monotonic() + 5
    while orq.estado is Estado.PROCESANDO and time.monotonic() < limite:
        time.sleep(0.01)
    time.sleep(0.05)


# --- a salvo antes de pegar -----------------------------------------------------------------------


def test_al_pegar_el_texto_y_la_grabacion_ya_estan_guardados(tmp_path: Path) -> None:
    """Si el pegado se cuelga o el proceso muere justo ahí, ya no se pierde nada."""
    atascos: list[str] = []
    orq, historial, pendientes, _motor, destino = _montar(tmp_path, al_atasco=atascos.append)
    visto: dict[str, Any] = {}

    def mirar() -> None:
        entradas = historial.todas()
        visto["entradas"] = [(e.texto, e.entregado, e.audio) for e in entradas]
        visto["grabacion"] = historial.cargar_audio(entradas[0].indice) is not None
        visto["pendientes"] = pendientes.listar()

    destino.al_pegar = mirar
    _dictar(orq)
    orq.parar()

    assert visto["entradas"] == [("Hola, mundo.", False, "000001.f32")]
    assert visto["grabacion"] is True
    assert visto["pendientes"] == []  # el historial ya se lo llevó con el texto
    assert [(e.texto, e.entregado) for e in historial.todas()] == [("Hola, mundo.", True)]
    assert destino.textos == ["Hola, mundo."]
    assert atascos == []


def test_si_el_pegado_revienta_el_texto_y_la_grabacion_siguen_ahi(tmp_path: Path) -> None:
    """El caso del cierre al pegar: antes se perdía el texto; ahora queda marcado sin entregar."""
    orq, historial, pendientes, _motor, destino = _montar(tmp_path)

    def reventar() -> None:
        raise RuntimeError("fallo nativo al pegar (simulado)")

    destino.al_pegar = reventar
    _dictar(orq)
    assert orq.estado is Estado.ERROR  # antes de parar(): parar cancela y vuelve a reposo
    orq.parar()

    [entrada] = historial.todas()
    assert (entrada.texto, entrada.entregado) == ("Hola, mundo.", False)
    assert historial.cargar_audio(entrada.indice) is not None
    assert pendientes.listar() == []  # ni se duplica como «dictado sin transcribir»


def test_sin_conservar_audio_el_pendiente_se_borra_como_antes(tmp_path: Path) -> None:
    orq, historial, pendientes, _motor, _destino = _montar(tmp_path, conservar=False)
    _dictar(orq)
    orq.parar()
    assert [(e.texto, e.entregado, e.audio) for e in historial.todas()] == [
        ("Hola, mundo.", True, None)
    ]
    assert pendientes.listar() == []
    assert not historial.carpeta_audio.exists()


# --- volver a transcribir -------------------------------------------------------------------------


def test_volver_a_transcribir_un_dictado_del_historial(tmp_path: Path) -> None:
    orq, historial, _pendientes, motor, destino = _montar(tmp_path)
    _dictar(orq)

    motor.texto = "Hola, mundo entero."
    assert orq.retranscribir(1) == "Hola, mundo entero."
    entrada = historial.buscar(1)
    assert entrada is not None and entrada.texto == "Hola, mundo entero."
    assert destino.textos == ["Hola, mundo."]  # no pega en ninguna ventana

    motor.falla = TranscripcionFallida("no se oyó nada")
    assert orq.retranscribir(1) == ""
    entrada = historial.buscar(1)
    assert entrada is not None and entrada.texto == "Hola, mundo entero."  # el de antes se queda

    assert orq.retranscribir(99) is None
    orq.parar()


# --- rastro de los atascos ------------------------------------------------------------------------


def test_el_vigia_deja_rastro_si_un_dictado_se_atasca(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No corta nada: dice en qué fase estaba, que es lo que faltaba para diagnosticar."""
    monkeypatch.setattr(modulo_orquestador, "ESPERA_ATASCO_S", 0.05)
    atascos: list[str] = []
    orq, historial, _pendientes, motor, _destino = _montar(
        tmp_path, segundos=0.2, al_atasco=atascos.append
    )
    motor.tarda_s = 0.6
    _dictar(orq)
    orq.parar()
    assert atascos == ["transcribiendo"]
    assert [e.entregado for e in historial.todas()] == [True]  # y el dictado termina bien


def test_las_fases_del_dictado_quedan_en_el_log_sin_lo_dicho(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="voziris.orquestador")
    orq, *_ = _montar(tmp_path)
    _dictar(orq)
    orq.parar()
    mensajes = [r.getMessage() for r in caplog.records if r.name == "voziris.orquestador"]
    esperados = [
        "grabando (mantener, hacia app_activa)",
        "grabación cerrada: 2.0 s de audio",
        "transcribiendo 2.0 s de audio",
        "entregando en app_activa",
    ]
    posiciones = [mensajes.index(m) for m in esperados]
    assert posiciones == sorted(posiciones)
    assert any(m.startswith("dictado entregado por local") for m in mensajes)
    assert not any("Hola, mundo" in m for m in mensajes)  # el log no guarda lo que se dice


# --- el menú --------------------------------------------------------------------------------------


def _acciones(**extra: Any) -> AccionesBandeja:
    return AccionesBandeja(
        dictar_ahora=lambda: None, dictar_markdown=lambda: None, alternar_corte=lambda: None,
        abrir_ajustes=lambda: None, cambiar_motor=lambda m: None, reintentar=lambda i: None,
        borrar_entrada=lambda i: None, salir=lambda: None, **extra,
    )


def _entrada(texto: str, indice: int, audio: str | None = None) -> EntradaHistorial:
    return EntradaHistorial(
        momento=datetime(2026, 9, 23, 9, 54), texto=texto, motor="api:groq",
        destino="app_activa", entregado=True, ms_total=577, duracion_audio_s=83.4,
        indice=indice, audio=audio,
    )


def test_copiar_el_ultimo_dictado_a_un_clic() -> None:
    """Para cuando se pegó con el foco fuera del campo de texto."""
    copiados: list[int] = []
    historial: list[EntradaHistorial] = []
    bandeja = Bandeja(_acciones(copiar=copiados.append), ultimas=lambda: historial)
    items = list(bandeja.construir_menu().items)
    textos = [str(i.text) for i in items]
    assert textos.index("Copiar el último dictado") + 1 == textos.index("Últimos dictados")
    copiar = items[textos.index("Copiar el último dictado")]
    assert not copiar.enabled  # todavía no hay nada que copiar

    historial[:] = [_entrada("El más nuevo", 8), _entrada("El de antes", 7)]
    assert copiar.enabled
    copiar(None)
    assert copiados == [8]


def test_sin_accion_de_copiar_no_hay_item_de_copiar_el_ultimo() -> None:
    bandeja = Bandeja(_acciones(), ultimas=lambda: [_entrada("Algo", 1)])
    assert "Copiar el último dictado" not in [str(i.text) for i in bandeja.construir_menu().items]


def test_volver_a_transcribir_solo_en_los_dictados_con_grabacion() -> None:
    pedidos: list[int] = []
    historial = [_entrada("Con grabación", 3, audio="000003.f32"), _entrada("Sin", 2)]
    bandeja = Bandeja(_acciones(retranscribir=pedidos.append), ultimas=lambda: historial)
    ultimos = {str(i.text): i for i in bandeja.construir_menu().items}["Últimos dictados"]
    con, sin = list(ultimos.submenu.items)
    etiquetas = [str(a.text) for a in con.submenu.items]
    assert "Volver a transcribir la grabación (1:23)" in etiquetas
    assert not any("Volver a transcribir" in str(a.text) for a in sin.submenu.items)
    [a for a in con.submenu.items if "Volver a transcribir" in str(a.text)][0](None)
    assert pedidos == [3]


def test_si_el_historial_no_se_puede_leer_el_menu_no_se_rompe() -> None:
    def roto() -> list[EntradaHistorial]:
        raise OSError("disco desconectado")

    bandeja = Bandeja(_acciones(copiar=lambda i: None), ultimas=roto)
    items = {str(i.text): i for i in bandeja.construir_menu().items}
    assert not items["Copiar el último dictado"].enabled
    assert [str(i.text) for i in items["Últimos dictados"].submenu.items] == [
        "(todavía no hay dictados)"
    ]


@pytest.mark.skipif(sys.platform != "win32", reason="el enganche es del menú de Windows")
def test_el_enganche_rehace_el_menu_justo_antes_de_ensenarlo() -> None:
    from pystray._util import win32

    orden: list[tuple[str, int]] = []

    class IconoFalso:
        def __init__(self) -> None:
            self._message_handlers: dict[int, Any] = {win32.WM_NOTIFY: self._on_notify}
            self.falla = False

        def _on_notify(self, wparam: int, lparam: int) -> int:
            orden.append(("enseñar", lparam))
            return 0

        def _update_menu(self) -> None:
            orden.append(("rehacer", 0))
            if self.falla:
                raise RuntimeError("menú imposible")

    icono = IconoFalso()
    assert rehacer_menu_al_abrir(icono) is True
    manejar = icono._message_handlers[win32.WM_NOTIFY]
    manejar(0, win32.WM_RBUTTONUP)
    manejar(0, win32.WM_LBUTTONUP)  # el clic izquierdo dicta: no hay menú que rehacer
    icono.falla = True
    manejar(0, win32.WM_RBUTTONUP)  # si no se puede rehacer, se enseña el de antes
    assert orden == [
        ("rehacer", 0), ("enseñar", win32.WM_RBUTTONUP),
        ("enseñar", win32.WM_LBUTTONUP),
        ("rehacer", 0), ("enseñar", win32.WM_RBUTTONUP),
    ]
    assert rehacer_menu_al_abrir(object()) is False  # un pystray sin esos internos


def _textos_nativos(hmenu: int) -> list[str]:
    """Lo que Windows enseñaría de verdad: el texto de cada entrada del HMENU, recursivo."""
    user32 = ctypes.WinDLL("user32")
    user32.GetMenuItemCount.argtypes = (ctypes.c_void_p,)
    user32.GetMenuStringW.argtypes = (
        ctypes.c_void_p, ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_int, ctypes.c_uint,
    )
    user32.GetSubMenu.argtypes = (ctypes.c_void_p, ctypes.c_int)
    user32.GetSubMenu.restype = ctypes.c_void_p
    salida = []
    for i in range(user32.GetMenuItemCount(hmenu)):
        texto = ctypes.create_unicode_buffer(256)
        user32.GetMenuStringW(hmenu, i, texto, 256, 0x400)  # MF_BYPOSITION
        salida.append(texto.value)
        submenu = user32.GetSubMenu(hmenu, i)
        if submenu:
            salida.extend(_textos_nativos(submenu))
    return salida


@pytest.mark.skipif(sys.platform != "win32", reason="es el menú nativo de Windows")
def test_el_menu_de_windows_ensena_el_dictado_de_hace_un_momento() -> None:
    """El fallo de VOZ-80, en la capa donde fallaba: el HMENU que Windows enseña."""
    import pystray
    from pystray._util import win32

    historial = [_entrada("Primero del día", 1)]
    bandeja = Bandeja(_acciones(copiar=lambda i: None), ultimas=lambda: list(historial))
    icono = pystray.Icon("voziris-prueba", icon=iconos.imagen("reposo"),
                         menu=bandeja.construir_menu())
    abiertos: list[int] = []
    icono._on_notify = lambda wparam, lparam: abiertos.append(lparam)  # sin enseñarlo de verdad
    assert rehacer_menu_al_abrir(icono) is True

    icono._update_menu()  # el menú del arranque
    assert any("Primero del día" in t for t in _textos_nativos(icono._menu_handle[0]))

    historial.insert(0, _entrada("Recién dictado", 2))
    # Así se quedaba antes: la foto del arranque, sin el dictado nuevo.
    assert not any("Recién dictado" in t for t in _textos_nativos(icono._menu_handle[0]))

    icono._message_handlers[win32.WM_NOTIFY](0, win32.WM_RBUTTONUP)  # clic derecho

    textos = _textos_nativos(icono._menu_handle[0])
    assert any("Recién dictado" in t for t in textos)
    assert any("Primero del día" in t for t in textos)
    assert abiertos == [win32.WM_RBUTTONUP]


def test_con_el_enganche_puesto_no_se_rehace_desde_otros_hilos() -> None:
    """Rehacerlo desde el hilo de trabajo podía destruirlo mientras estaba abierto."""
    rehechos: list[int] = []

    class Icono:
        def update_menu(self) -> None:
            rehechos.append(1)

    bandeja = Bandeja(_acciones())
    bandeja._icono = Icono()
    bandeja._rehace_al_abrir = True
    bandeja.actualizar_menu()
    assert rehechos == []
    bandeja._rehace_al_abrir = False  # sin enganche (otro sistema, otro pystray): como antes
    bandeja.actualizar_menu()
    assert rehechos == [1]


# --- el rastro en voziris-fallos.log --------------------------------------------------------------


def test_cada_arranque_y_cada_atasco_dejan_fecha_en_el_archivo_de_fallos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Los volcados de faulthandler no llevan fecha: sin esto no se sabe de qué arranque son."""
    import faulthandler

    from voziris import __main__ as principal

    monkeypatch.setattr(principal, "_archivo_fallos", None)
    # Sin tocar el faulthandler de verdad: pytest lo tiene puesto para sí mismo.
    monkeypatch.setattr(faulthandler, "enable", lambda *a, **k: None)
    try:
        principal.activar_faulthandler(tmp_path)
        principal.volcar_hilos("dictado atascado (entregando en app_activa)")
    finally:
        if principal._archivo_fallos is not None:
            principal._archivo_fallos.close()
    texto = (tmp_path / "voziris-fallos.log").read_text(encoding="utf-8")
    assert "· arranque de Voziris" in texto and "(pid " in texto
    assert "· dictado atascado (entregando en app_activa) (el proceso sigue vivo) ===" in texto
    assert "Thread 0x" in texto or "Current thread 0x" in texto  # el volcado de verdad
    assert "test_cada_arranque_y_cada_atasco" in texto  # con la línea de Python de cada hilo


def test_volcar_hilos_sin_archivo_no_hace_nada(monkeypatch: pytest.MonkeyPatch) -> None:
    from voziris import __main__ as principal

    monkeypatch.setattr(principal, "_archivo_fallos", None)
    principal.volcar_hilos("nada")  # no lanza
