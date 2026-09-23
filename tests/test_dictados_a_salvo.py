"""Tests de VOZ-80: ningún dictado se pierde, todos se ven en el menú, y se pueden rehacer.

Con el historial y los pendientes de verdad, en disco: el fallo que motivó esto
se coló porque los tests miraban una capa que no era la que falla (el menú de
Python, que sí es dinámico, en vez del menú nativo de Windows, que no lo era).
Los del menú leen el HMENU que Windows enseñaría.
"""

from __future__ import annotations

import contextlib
import ctypes
import logging
import os
import stat
import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from voziris import orquestador as modulo_orquestador
from voziris.errores import EntregaFallida, TranscripcionFallida
from voziris.historial import Historial
from voziris.orquestador import Estado, Orquestador
from voziris.pendientes import Pendientes
from voziris.tipos import SAMPLE_RATE, Audio, EntradaHistorial, Entrega, Modo, Transcripcion
from voziris.ui.bandeja import AccionesBandeja, Bandeja, rehacer_menu_al_abrir

# --- el banco: captura, motor y destino falsos; historial y pendientes de verdad ----------------


class Captura:
    """Como la real: escribe el pendiente según «se habla» y lo deja al terminar."""

    oyente_bloques: Any = None

    def __init__(self, pendientes: Pendientes, segundos: float) -> None:
        self._pendientes = pendientes
        self.segundos = segundos
        self.ultimo_pendiente: Path | None = None

    def empezar_dictado(self) -> None: ...

    def terminar_dictado(self) -> Audio:
        escritor = self._pendientes.abrir()
        assert escritor is not None
        muestras = np.full(int(SAMPLE_RATE * self.segundos), 0.1, dtype=np.float32)
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
        self.avisos: list[str] = []

    def precalentar(self) -> None: ...

    def disponible(self) -> bool:
        return True

    def transcribir(self, audio: Audio, idioma: str) -> Transcripcion:
        time.sleep(self.tarda_s)
        if self.falla is not None:
            raise self.falla
        t = Transcripcion(self.texto, idioma, self.nombre, 5, audio.duracion_s)
        t.avisos.extend(self.avisos)
        return t


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


class Banco:
    def __init__(self, tmp_path: Path, conservar: bool = True, segundos: float = 2.0,
                 **opciones: Any) -> None:
        self.pendientes = Pendientes(tmp_path / "historial" / "pendientes")
        self.historial = Historial(tmp_path / "historial" / "dictados.jsonl",
                                   conservar_audio=conservar)
        self.captura = Captura(self.pendientes, segundos)
        self.motor, self.destino = Motor(), Destino()
        self.avisos: list[str] = []
        self.atascos: list[tuple[str, bool]] = []
        self.orq = Orquestador(
            self.captura, self.motor, {"app_activa": self.destino}, [],
            historial=self.historial, pendientes=self.pendientes,
            al_aviso=self.avisos.append,
            al_atasco=lambda fase, hay_copia: self.atascos.append((fase, hay_copia)),
            **opciones,
        )
        self.orq.arrancar()
        assert self.orq.motor_listo.wait(3)

    def dictar(self) -> None:
        assert self.orq.empezar(Modo.MANTENER, "app_activa")
        self.orq.terminar(1500)
        limite = time.monotonic() + 5
        while self.orq.estado is Estado.PROCESANDO and time.monotonic() < limite:
            time.sleep(0.01)
        time.sleep(0.05)


@pytest.fixture
def banco(tmp_path: Path) -> Any:
    bancos: list[Banco] = []

    def montar(**opciones: Any) -> Banco:
        b = Banco(tmp_path, **opciones)
        bancos.append(b)
        return b

    yield montar
    for b in bancos:
        b.orq.parar()


# --- a salvo antes de pegar -----------------------------------------------------------------------


def test_al_pegar_el_texto_y_la_grabacion_ya_estan_guardados(banco: Any) -> None:
    """Si el pegado se cuelga o el proceso muere justo ahí, ya no se pierde nada."""
    b = banco()
    visto: dict[str, Any] = {}

    def mirar() -> None:
        entradas = b.historial.todas()
        visto["entradas"] = [(e.texto, e.entregado, e.audio) for e in entradas]
        visto["grabacion"] = b.historial.cargar_audio(entradas[0].indice) is not None
        visto["pendientes"] = b.pendientes.listar()

    b.destino.al_pegar = mirar
    b.dictar()

    assert visto["entradas"] == [("Hola, mundo.", False, "000001.f32")]
    assert visto["grabacion"] is True
    assert visto["pendientes"] == []  # el historial ya se lo llevó con el texto
    assert [(e.texto, e.entregado) for e in b.historial.todas()] == [("Hola, mundo.", True)]
    assert b.destino.textos == ["Hola, mundo."]


def test_si_el_pegado_revienta_el_texto_y_la_grabacion_siguen_ahi(banco: Any) -> None:
    """El caso del cierre al pegar: antes se perdía el texto; ahora queda marcado sin entregar."""
    b = banco()

    def reventar() -> None:
        raise RuntimeError("fallo nativo al pegar (simulado)")

    b.destino.al_pegar = reventar
    b.dictar()
    assert b.orq.estado is Estado.ERROR

    [entrada] = b.historial.todas()
    assert (entrada.texto, entrada.entregado) == ("Hola, mundo.", False)
    assert b.historial.cargar_audio(entrada.indice) is not None
    assert b.pendientes.listar() == []  # ni se duplica como «dictado sin transcribir»
    # y no se promete lo que no hay: el audio no está en «sin transcribir», está con el texto
    assert not any("Dictados sin transcribir" in a for a in b.avisos), b.avisos


def test_si_no_se_puede_escribir_el_texto_la_grabacion_vuelve_a_pendientes(banco: Any) -> None:
    """dictados.jsonl bloqueado y el pegado que falla: al menos el audio no se pierde."""
    b = banco()
    b.dictar()  # uno bueno, para que el archivo exista
    ruta = b.historial.ruta
    os.chmod(ruta, stat.S_IREAD)
    try:
        def fallar() -> None:
            raise EntregaFallida("no hay ventana donde pegar")

        b.destino.al_pegar = fallar
        b.dictar()
    finally:
        os.chmod(ruta, stat.S_IREAD | stat.S_IWRITE)

    assert [e.indice for e in b.historial.todas()] == [1]
    assert len(b.pendientes.listar()) == 1  # el audio del segundo, a salvo
    assert sorted(f.name for f in b.historial.carpeta_audio.iterdir()) == ["000001.f32"]
    assert any("Dictados sin transcribir" in a for a in b.avisos), b.avisos


def test_sin_conservar_audio_el_pendiente_se_borra_como_antes(banco: Any) -> None:
    b = banco(conservar=False)
    b.dictar()
    assert [(e.texto, e.entregado, e.audio) for e in b.historial.todas()] == [
        ("Hola, mundo.", True, None)
    ]
    assert b.pendientes.listar() == []
    assert not b.historial.carpeta_audio.exists()


@pytest.mark.parametrize(("segundos", "se_guarda"), [(2.0, False), (6.0, True)])
def test_un_dictado_largo_sin_texto_no_se_tira(banco: Any, segundos: float,
                                              se_guarda: bool) -> None:
    """Seis segundos sin texto suele ser el motor, no silencio: se guarda y se avisa en serio."""
    b = banco(segundos=segundos)
    b.motor.falla = TranscripcionFallida("no se oyó nada")
    b.dictar()
    assert b.destino.textos == [] and b.historial.todas() == []
    assert len(b.pendientes.listar()) == (1 if se_guarda else 0)
    # Largo: icono de error y aviso de bandeja, no solo 1,5 s en el indicador.
    assert (b.orq.estado is Estado.ERROR) is se_guarda


# --- recuperar y volver a transcribir -----------------------------------------------------------


def test_recuperar_un_dictado_largo_que_vuelve_a_fallar_no_lo_borra(banco: Any) -> None:
    """El reintento que falla igual no puede ser lo que borre diez minutos de dictado."""
    b = banco(segundos=6.0)
    b.motor.falla = TranscripcionFallida("la API devolvió vacío")
    b.dictar()
    [pendiente] = b.pendientes.listar()

    with pytest.raises(TranscripcionFallida):
        b.orq.recuperar(pendiente.ruta)
    assert pendiente.ruta.exists()

    b.motor.falla = None
    assert b.orq.recuperar(pendiente.ruta) == "Hola, mundo."
    assert not pendiente.ruta.exists()
    [entrada] = b.historial.todas()
    assert entrada.audio == "000001.f32"  # la grabación se va con el texto al historial


def test_recuperar_uno_corto_sin_nada_si_lo_borra(banco: Any) -> None:
    b = banco()
    escritor = b.pendientes.abrir()
    assert escritor is not None
    escritor.escribir(np.zeros(int(SAMPLE_RATE * 2), dtype=np.float32))
    ruta = escritor.cerrar()
    b.motor.falla = TranscripcionFallida("no se oyó nada")
    assert b.orq.recuperar(ruta) == ""
    assert not ruta.exists()


def test_volver_a_transcribir_guarda_el_texto_de_antes_y_dice_sus_avisos(banco: Any) -> None:
    b = banco()
    b.dictar()

    b.motor.texto = "Hola, mundo entero."
    b.motor.avisos = ["La API no contestó: transcrito en local"]
    nueva = b.orq.retranscribir(1)
    assert nueva is not None and nueva.texto == "Hola, mundo entero."
    assert nueva.avisos == ["La API no contestó: transcrito en local"]
    entrada = b.historial.buscar(1)
    assert entrada is not None
    assert (entrada.texto, entrada.texto_anterior) == ("Hola, mundo entero.", "Hola, mundo.")
    assert b.destino.textos == ["Hola, mundo."]  # no pega en ninguna ventana

    b.motor.falla = TranscripcionFallida("no se oyó nada")
    with pytest.raises(TranscripcionFallida):
        b.orq.retranscribir(1)
    entrada = b.historial.buscar(1)
    assert entrada is not None and entrada.texto == "Hola, mundo entero."  # el de antes se queda

    assert b.orq.retranscribir(99) is None


def test_volver_a_transcribir_no_escribe_encima_de_otro_dictado(
    banco: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mientras se transcribe, se borra ese dictado y otro hereda su número (VOZ-80)."""
    b = banco()
    b.dictar()
    cargar = b.historial.cargar_audio

    def cargar_y_que_cambie_mientras(indice: int) -> Audio | None:
        audio = cargar(indice)
        b.historial.borrar(indice)
        otro = EntradaHistorial(datetime(2030, 1, 1, 12, 0), "Otro dictado", "local",
                                "app_activa", True, 1, 1.0)
        b.historial.registrar(otro)  # hereda el índice 1
        return audio

    monkeypatch.setattr(b.historial, "cargar_audio", cargar_y_que_cambie_mientras)
    assert b.orq.retranscribir(1) is None
    entrada = b.historial.buscar(1)
    assert entrada is not None and entrada.texto == "Otro dictado"


def test_mientras_se_transcribe_no_sale_como_sin_transcribir(banco: Any) -> None:
    """Si saliera, «Recuperar» lo transcribiría dos veces y lo duplicaría."""
    b = banco()
    b.motor.tarda_s = 0.4
    assert b.orq.empezar(Modo.MANTENER, "app_activa")
    b.orq.terminar(1500)
    time.sleep(0.1)
    en_proceso = b.orq.pendiente_en_proceso
    assert en_proceso is not None and en_proceso.exists()
    assert b.pendientes.listar(excepto=[en_proceso]) == []
    limite = time.monotonic() + 3
    while b.orq.estado is Estado.PROCESANDO and time.monotonic() < limite:
        time.sleep(0.01)
    time.sleep(0.05)
    assert b.orq.pendiente_en_proceso is None


def test_esc_en_una_grabacion_larga_la_guarda_y_en_una_corta_no(banco: Any) -> None:
    """Un Esc a destiempo tras un minuto hablando no debería costar el minuto."""
    b = banco()
    assert b.orq.empezar(Modo.CLAVAR, "app_activa")
    b.orq.cancelar()
    assert b.pendientes.listar() == []

    assert b.orq.empezar(Modo.CLAVAR, "app_activa")
    assert b.orq._dictado is not None
    b.orq._dictado.inicio -= 30  # como si llevara medio minuto hablando
    b.orq.cancelar()
    assert b.orq.estado is Estado.REPOSO
    assert len(b.pendientes.listar()) == 1
    assert any("Dictados sin transcribir" in a for a in b.avisos), b.avisos
    assert b.destino.textos == [] and b.historial.todas() == []  # guardada, no procesada


# --- rastro de los atascos ------------------------------------------------------------------------


def test_el_vigia_deja_rastro_si_un_dictado_se_atasca(
    banco: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No corta nada: dice en qué fase estaba, y si hay copia en disco."""
    monkeypatch.setattr(modulo_orquestador, "ESPERA_ATASCO_S", 0.05)
    b = banco(segundos=0.2)
    b.motor.tarda_s = 0.6
    b.dictar()
    assert b.atascos == [("transcribiendo", True)]
    assert [e.entregado for e in b.historial.todas()] == [True]  # y el dictado termina bien


def test_el_vigia_no_salta_si_el_dictado_acaba_a_tiempo(
    banco: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sin cancelarlo, cada dictado dejaría un volcado y un «se ha atascado» falsos."""
    monkeypatch.setattr(modulo_orquestador, "ESPERA_ATASCO_S", 0.3)
    b = banco(segundos=0.2)
    b.dictar()
    time.sleep(0.8)  # más allá de 0,3 + 0,2
    assert b.atascos == []


def test_las_fases_del_dictado_quedan_en_el_log_sin_lo_dicho(
    banco: Any, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="voziris.orquestador")
    b = banco()
    b.dictar()
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


# --- el portapapeles, compartido ------------------------------------------------------------------


def test_copiar_desde_la_bandeja_espera_a_que_acabe_un_pegado(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sin cerrojo, una copia a mitad de pegar acababa pegada en la app o borrada."""
    from voziris import winapi
    from voziris.destinos.app_activa import AppActiva

    orden: list[str] = []
    monkeypatch.setattr(winapi, "leer_portapapeles", lambda: "lo que tenía el usuario")
    monkeypatch.setattr(winapi, "escribir_portapapeles", lambda t: orden.append(f"escribir {t}"))
    monkeypatch.setattr(winapi, "soltar_modificadores", lambda: None)
    monkeypatch.setattr(winapi, "pulsar_combinacion", lambda *t: orden.append("ctrl+v"))
    destino = AppActiva(metodo="portapapeles", restaurar_portapapeles=True)
    pegando = threading.Thread(target=destino._pegar, args=("el dictado",))
    pegando.start()
    time.sleep(0.01)  # ya dentro del pegado
    winapi.copiar("lo retranscrito")
    pegando.join()
    assert orden == [
        "escribir el dictado", "ctrl+v", "escribir lo que tenía el usuario",
        "escribir lo retranscrito",
    ]


# --- el menú --------------------------------------------------------------------------------------


def _acciones(**extra: Any) -> AccionesBandeja:
    return AccionesBandeja(
        dictar_ahora=lambda: None, dictar_markdown=lambda: None, alternar_corte=lambda: None,
        abrir_ajustes=lambda: None, cambiar_motor=lambda m: None, reintentar=lambda i: None,
        borrar_entrada=lambda i: None, salir=lambda: None, **extra,
    )


def _entrada(texto: str, indice: int, audio: str | None = None,
             anterior: str | None = None) -> EntradaHistorial:
    return EntradaHistorial(
        momento=datetime(2026, 9, 23, 9, 54), texto=texto, motor="api:groq",
        destino="app_activa", entregado=True, ms_total=577, duracion_audio_s=83.4,
        indice=indice, audio=audio, texto_anterior=anterior,
    )


def test_copiar_el_ultimo_dictado_a_un_clic_y_diciendo_cual() -> None:
    """Para cuando se pegó con el foco fuera. La etiqueta dice cuál: tras un fallo,
    el último del historial es uno anterior, y hay que verlo antes de pegarlo."""
    copiados: list[int] = []
    historial: list[EntradaHistorial] = []
    bandeja = Bandeja(_acciones(copiar=copiados.append), ultimas=lambda: historial)
    items = list(bandeja.construir_menu().items)
    textos = [str(i.text) for i in items]
    assert textos.index("Copiar el último dictado") + 1 == textos.index("Últimos dictados")
    copiar = items[textos.index("Copiar el último dictado")]
    assert not copiar.enabled  # todavía no hay nada que copiar

    historial[:] = [_entrada("Correo a la gestoría sobre el trimestre", 8),
                    _entrada("El de antes", 7)]
    assert copiar.enabled
    assert str(copiar.text).startswith("Copiar el último dictado (09:54 · Correo a la")
    copiar(None)
    assert copiados == [8]


def test_sin_accion_de_copiar_no_hay_item_de_copiar_el_ultimo() -> None:
    bandeja = Bandeja(_acciones(), ultimas=lambda: [_entrada("Algo", 1)])
    assert not any("Copiar el último" in str(i.text) for i in bandeja.construir_menu().items)


def test_volver_a_transcribir_y_el_texto_de_antes_solo_donde_toca() -> None:
    pedidos: list[tuple[str, int]] = []
    historial = [
        _entrada("Retranscrito", 3, audio="000003.f32", anterior="Lo de antes"),
        _entrada("Sin grabación", 2),
    ]
    bandeja = Bandeja(
        _acciones(retranscribir=lambda i: pedidos.append(("otra vez", i)),
                  copiar_anterior=lambda i: pedidos.append(("antes", i))),
        ultimas=lambda: historial,
    )
    ultimos = [i for i in bandeja.construir_menu().items if str(i.text) == "Últimos dictados"][0]
    con, sin = list(ultimos.submenu.items)
    acciones = {str(a.text): a for a in con.submenu.items}
    assert "Volver a transcribir la grabación (1:23)" in acciones
    acciones["Volver a transcribir la grabación (1:23)"](None)
    acciones["Copiar el texto de antes de volver a transcribir"](None)
    assert pedidos == [("otra vez", 3), ("antes", 3)]
    etiquetas_sin = [str(a.text) for a in sin.submenu.items]
    assert not any("Volver a transcribir" in t or "de antes" in t for t in etiquetas_sin)


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
def test_el_enganche_construye_antes_de_destruir_y_no_rehace_el_menu_abierto() -> None:
    from pystray._util import win32

    orden: list[str] = []

    class IconoFalso:
        menu = "el menú"

        def __init__(self) -> None:
            self._message_handlers: dict[int, Any] = {win32.WM_NOTIFY: self._on_notify}
            self._menu_handle: Any = None
            self.falla = False
            self.reentrar = False

        def _create_menu(self, menu: Any, callbacks: list[Any]) -> int:
            orden.append("construir")
            if self.falla:
                raise RuntimeError("menú imposible")
            return 1000 + len(orden)

        def _on_notify(self, wparam: int, lparam: int) -> int:
            orden.append(f"enseñar {self._menu_handle[0] if self._menu_handle else None}")
            if self.reentrar:  # otro clic derecho con este menú en pantalla
                self._message_handlers[win32.WM_NOTIFY](0, win32.WM_RBUTTONUP)
            return 0

    icono = IconoFalso()
    assert rehacer_menu_al_abrir(icono) is True
    manejar = icono._message_handlers[win32.WM_NOTIFY]

    manejar(0, win32.WM_RBUTTONUP)
    assert orden == ["construir", "enseñar 1001"]
    manejar(0, win32.WM_LBUTTONUP)  # el clic izquierdo dicta: no hay menú que rehacer
    assert orden[-1] == "enseñar 1001"

    icono.falla = True
    manejar(0, win32.WM_RBUTTONUP)
    assert orden[-1] == "enseñar 1001"  # el de antes, entero: no uno ya destruido

    icono.falla, icono.reentrar = False, True
    orden.clear()
    manejar(0, win32.WM_RBUTTONUP)
    assert orden == ["construir", "enseñar 1001"]  # el segundo clic ni construye ni enseña

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


def _es_menu(hmenu: int) -> bool:
    user32 = ctypes.WinDLL("user32")
    user32.IsMenu.argtypes = (ctypes.c_void_p,)
    return bool(user32.IsMenu(hmenu))


@contextlib.contextmanager
def _clic_derecho_sin_pantalla(monkeypatch: pytest.MonkeyPatch) -> Any:
    """El `_on_notify` de pystray de verdad, pero sin enseñar nada: apunta qué HMENU
    habría enseñado y lo que Windows habría puesto en él."""
    from pystray._util import win32

    enseñados: list[list[str]] = []

    def track(hmenu: Any, *resto: Any) -> int:
        enseñados.append(_textos_nativos(hmenu) if _es_menu(hmenu) else ["<menú destruido>"])
        return 0

    monkeypatch.setattr(win32, "TrackPopupMenuEx", track)
    monkeypatch.setattr(win32, "SetForegroundWindow", lambda *a: True)
    yield enseñados


@pytest.fixture
def icono_de(request: pytest.FixtureRequest) -> Any:
    """Crea el icono real de una Bandeja y, al acabar, desregistra su clase de ventana.

    pystray la registra al crear el icono y solo la quita al pararlo. Un icono
    de prueba que nunca arranca la deja puesta, y el siguiente objeto que caiga
    en la misma dirección de memoria choca con ella (WinError 1410).
    """
    creados: list[Any] = []

    def crear(bandeja: Bandeja) -> Any:
        icono = bandeja._crear_icono()
        creados.append(icono)
        return icono

    yield crear
    for icono in creados:
        with contextlib.suppress(Exception):
            icono._unregister_class(icono._atom)


@pytest.mark.skipif(sys.platform != "win32", reason="es el menú nativo de Windows")
def test_la_bandeja_de_verdad_ensena_el_dictado_de_hace_un_momento(
    monkeypatch: pytest.MonkeyPatch, icono_de: Any
) -> None:
    """El fallo de VOZ-80 en la capa donde fallaba, y por el camino de la app: el icono
    lo crea Bandeja, y el clic derecho pasa por el `_on_notify` real de pystray."""
    from pystray._util import win32

    historial = [_entrada("Primero del día", 1)]
    bandeja = Bandeja(_acciones(copiar=lambda i: None), ultimas=lambda: list(historial))
    icono = icono_de(bandeja)
    assert bandeja._rehace_al_abrir is True
    icono._update_menu()  # el menú del arranque
    historial.insert(0, _entrada("Recién dictado", 2))
    # Así se quedaba antes: la foto del arranque, sin el dictado nuevo.
    assert not any("Recién dictado" in t for t in _textos_nativos(icono._menu_handle[0]))

    with _clic_derecho_sin_pantalla(monkeypatch) as enseñados:
        icono._message_handlers[win32.WM_NOTIFY](0, win32.WM_RBUTTONUP)

    [menu] = enseñados
    assert any("Recién dictado" in t for t in menu)
    assert any("Primero del día" in t for t in menu)
    assert any(t.startswith("Copiar el último dictado (09:54 · Recién") for t in menu)


@pytest.mark.skipif(sys.platform != "win32", reason="es el menú nativo de Windows")
def test_si_el_menu_nuevo_no_se_puede_construir_se_ensena_el_de_antes(
    monkeypatch: pytest.MonkeyPatch, icono_de: Any
) -> None:
    from pystray._util import win32

    roto = [False]

    def motor() -> str:
        if roto[0]:
            raise RuntimeError("una marca del menú que revienta")
        return "auto"

    bandeja = Bandeja(_acciones(), motor_actual=motor)
    icono = icono_de(bandeja)
    icono._update_menu()
    viejo = icono._menu_handle[0]
    roto[0] = True
    with _clic_derecho_sin_pantalla(monkeypatch) as enseñados:
        icono._message_handlers[win32.WM_NOTIFY](0, win32.WM_RBUTTONUP)
    assert enseñados and enseñados[0] != ["<menú destruido>"]
    assert "Dictar ahora" in enseñados[0]
    assert icono._menu_handle[0] == viejo and _es_menu(viejo)


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


def test_una_construccion_del_menu_lee_el_historial_una_sola_vez() -> None:
    lecturas: list[int] = []

    def ultimas() -> list[EntradaHistorial]:
        lecturas.append(1)
        return [_entrada("Uno", 1)]

    bandeja = Bandeja(_acciones(copiar=lambda i: None, pendientes=lambda: []), ultimas=ultimas)
    menu = bandeja.construir_menu()
    with bandeja._una_lectura():
        for item in menu.items:  # lo que hace pystray al construir: evaluarlo todo
            str(item.text), item.enabled, item.visible
            if item.submenu:
                list(item.submenu.items)
    assert len(lecturas) == 1


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
        principal.anotar_arranque_en_fallos()
        principal.volcar_hilos("dictado atascado (pegando en la ventana activa)")
    finally:
        if principal._archivo_fallos is not None:
            principal._archivo_fallos.close()
    texto = (tmp_path / "voziris-fallos.log").read_text(encoding="utf-8")
    assert "· arranque de Voziris" in texto and "(pid " in texto
    assert "· dictado atascado (pegando en la ventana activa) (el proceso sigue vivo) ===" in texto
    assert "Thread 0x" in texto or "Current thread 0x" in texto  # el volcado de verdad
    assert "test_cada_arranque_y_cada_atasco" in texto  # con la línea de Python de cada hilo


def test_solo_la_bandeja_anota_el_arranque(tmp_path: Path,
                                           monkeypatch: pytest.MonkeyPatch) -> None:
    """La consola, «Transcribir» o el diagnóstico no: echarían de la vista los volcados."""
    import faulthandler

    from voziris import __main__ as principal

    monkeypatch.setattr(principal, "_archivo_fallos", None)
    monkeypatch.setattr(faulthandler, "enable", lambda *a, **k: None)
    try:
        principal.activar_faulthandler(tmp_path)
    finally:
        if principal._archivo_fallos is not None:
            principal._archivo_fallos.close()
    assert (tmp_path / "voziris-fallos.log").read_text(encoding="utf-8") == ""


def test_el_diagnostico_no_confunde_las_marcas_de_arranque_con_fallos(tmp_path: Path) -> None:
    from voziris.diagnostico import _fallos_nativos

    ruta = tmp_path / "voziris-fallos.log"
    ruta.write_text("\n=== 2026-09-23 07:18:35 · arranque de Voziris 0.1.0 (pid 1) ===\n"
                    "\n=== 2026-09-23 09:00:00 · arranque de Voziris 0.1.0 (pid 2) ===\n",
                    encoding="utf-8")
    assert _fallos_nativos(ruta) == "(sin fallos; 2 arranque(s) anotado(s))"
    with open(ruta, "a", encoding="utf-8") as archivo:
        archivo.write("Windows fatal exception: code 0xc0000374\n")
    assert "0xc0000374" in _fallos_nativos(ruta)


def test_volcar_hilos_sin_archivo_no_hace_nada(monkeypatch: pytest.MonkeyPatch) -> None:
    from voziris import __main__ as principal

    monkeypatch.setattr(principal, "_archivo_fallos", None)
    principal.volcar_hilos("nada")  # no lanza
    principal.anotar_arranque_en_fallos()  # tampoco
