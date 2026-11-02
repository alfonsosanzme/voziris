"""Tests de VOZ-85: lo que impedía instalar y actualizar sin que se viera.

Lo nativo, con procesos de verdad. Lo que saca un cuadro, en un escritorio que
no se ve: en la pantalla no sale nada, y estos tests no abren ninguna ventana.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from voziris import __main__ as principal
from voziris import actualizaciones as act
from voziris import instalador, winapi
from voziris.ui.transcripcion import TranscripcionCancelada

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="procesos y cuadros de Windows")


# --- quién sigue vivo desde una carpeta ------------------------------------------------------


def test_procesos_en_carpeta_ve_lo_que_corre_desde_ella(
    tmp_path: Path, copia_viva: Callable[..., Any]
) -> None:
    carpeta = tmp_path / "Voziris"
    dentro = copia_viva(carpeta / "voziris.exe")
    en_una_subcarpeta = copia_viva(carpeta / "_internal" / "ayudante.exe")
    al_lado = copia_viva(tmp_path / "Voziris.nuevo" / "voziris.exe")  # «Voziris» es su prefijo

    vivos = winapi.procesos_en_carpeta(carpeta)

    assert {p.pid for p in vivos} == {dentro.pid, en_una_subcarpeta.pid}
    assert al_lado.pid not in {p.pid for p in vivos}
    assert {Path(p.ruta).name for p in vivos} == {"voziris.exe", "ayudante.exe"}


def test_procesos_en_carpeta_no_distingue_mayusculas_ni_se_incluye_a_si_mismo(
    tmp_path: Path, copia_viva: Callable[..., Any]
) -> None:
    carpeta = tmp_path / "Voziris"
    proceso = copia_viva(carpeta / "voziris.exe")
    assert [p.pid for p in winapi.procesos_en_carpeta(str(carpeta).upper())] == [proceso.pid]
    propia = Path(winapi.ruta_de_proceso(os.getpid()) or "").parent
    assert os.getpid() not in {p.pid for p in winapi.procesos_en_carpeta(propia)}


def test_orden_de_proceso_la_parte_como_windows(
    tmp_path: Path, copia_viva: Callable[..., Any]
) -> None:
    proceso = copia_viva(tmp_path / "voziris.exe", "--transcribir", r"C:\mis cosas\reunión 1.m4a")
    orden = winapi.orden_de_proceso(proceso.pid)
    assert orden is not None and orden[0] == str(tmp_path / "voziris.exe")
    assert orden[-2:] == ["--transcribir", r"C:\mis cosas\reunión 1.m4a"]


def test_de_un_proceso_que_no_existe_no_se_sabe_nada() -> None:
    inexistente = winapi.Proceso(0x7FFFFFFC, r"C:\no\existe.exe")
    assert winapi.orden_de_proceso(inexistente.pid) is None
    assert winapi.ruta_de_proceso(inexistente.pid) is None
    assert winapi.cerrar_proceso(inexistente, 0.1) == "ya no estaba"


# --- cerrarlo --------------------------------------------------------------------------------


def test_cerrar_proceso_termina_al_que_no_se_va(
    tmp_path: Path, copia_viva: Callable[..., Any]
) -> None:
    proceso = copia_viva(tmp_path / "voziris.exe")
    objetivo = winapi.Proceso(proceso.pid, str(tmp_path / "voziris.exe"))
    assert winapi.cerrar_proceso(objetivo, 0.1) == "terminado"
    assert proceso.wait(timeout=5) == 1


def test_cerrar_proceso_no_toca_otro_con_ese_pid(
    tmp_path: Path, copia_viva: Callable[..., Any]
) -> None:
    """Entre mirar y cerrar, Windows puede haber dado el PID a otro: se comprueba su ruta."""
    proceso = copia_viva(tmp_path / "otro.exe")
    assert winapi.cerrar_proceso(
        winapi.Proceso(proceso.pid, str(tmp_path / "voziris.exe")), 0.1
    ) == "ya no estaba"
    assert proceso.poll() is None


# --- quién retiene una carpeta ---------------------------------------------------------------


def test_quien_usa_ve_un_archivo_abierto_y_dice_quien_es(
    tmp_path: Path, hijo_que_retiene: Callable[..., Any]
) -> None:
    (tmp_path / "subcarpeta").mkdir()
    archivo = tmp_path / "subcarpeta" / "base_library.zip"
    archivo.write_bytes(b"zip")
    assert winapi.quien_usa([str(archivo)]) == []
    hijo = hijo_que_retiene(archivo)
    retienen = {p.pid: p.ruta for p in winapi.quien_usa([str(archivo)])}
    assert retienen[hijo.pid] == winapi.ruta_de_proceso(hijo.pid)
    hijo.soltar()
    assert hijo.pid not in {p.pid for p in winapi.quien_usa([str(archivo)])}


def test_quien_usa_ve_un_directorio_de_trabajo(
    tmp_path: Path, hijo_que_retiene: Callable[..., Any]
) -> None:
    """Restart Manager no lo ve, y también impide renombrar la carpeta."""
    carpeta = tmp_path / "_internal"
    (carpeta / "numpy").mkdir(parents=True)
    hijo = hijo_que_retiene(cwd=carpeta / "numpy")
    with pytest.raises(PermissionError):
        os.replace(carpeta, tmp_path / "apartada")
    assert hijo.pid not in {p.pid for p in winapi.quien_usa([], [str(carpeta)])}
    assert hijo.pid in {p.pid for p in winapi.quien_usa([], [str(carpeta / "numpy")])}


def test_quien_usa_no_se_incluye_a_si_mismo(tmp_path: Path) -> None:
    archivo = tmp_path / "abierto.txt"
    archivo.write_bytes(b"x")
    with archivo.open("rb"):
        assert os.getpid() not in {p.pid for p in winapi.quien_usa([str(archivo)])}


def test_quien_usa_sin_nada_que_mirar_o_con_lo_que_ya_no_existe(tmp_path: Path) -> None:
    assert winapi.quien_usa([], []) == []
    assert winapi.quien_usa([], [str(tmp_path / "no-esta")]) == []


# --- el mutex --------------------------------------------------------------------------------


def test_soltar_mutex_deja_tomarlo_a_otro() -> None:
    nombre = f"Local\\Voziris.pruebas-soltar-{os.getpid()}"
    winapi.soltar_mutex(nombre)  # sin haberlo tomado: nada
    assert winapi.tomar_mutex(nombre) and winapi.existe_mutex(nombre)
    winapi.soltar_mutex(nombre)
    assert not winapi.existe_mutex(nombre)
    assert nombre not in winapi._mutex_tareas


# --- en un escritorio que no se ve -----------------------------------------------------------


class _Oculto:
    """Un escritorio aparte. Lo que se abre en él no sale en la pantalla."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        self.nombre = f"voziris-pruebas-{os.getpid()}"
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._user32.CreateDesktopW.argtypes = (
            wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
            ctypes.c_void_p,
        )
        self._user32.CreateDesktopW.restype = wintypes.HANDLE
        self._user32.SetThreadDesktop.argtypes = (wintypes.HANDLE,)
        self._user32.CloseDesktop.argtypes = (wintypes.HANDLE,)
        self._manejador = self._user32.CreateDesktopW(self.nombre, None, None, 0, 0x10000000, None)
        assert self._manejador, f"sin escritorio: error {ctypes.get_last_error()}"

    def en_un_hilo(self, funcion: Callable[[], Any], espera_s: float = 30.0) -> Any:
        """`funcion()` en un hilo de este escritorio. Si no vuelve a tiempo, el test falla."""
        hecho: list[Any] = []
        fallo: list[BaseException] = []

        def correr() -> None:
            try:
                assert self._user32.SetThreadDesktop(self._manejador)
                hecho.append(funcion())
            except BaseException as e:  # noqa: BLE001 — se relanza abajo
                fallo.append(e)

        hilo = threading.Thread(target=correr, daemon=True)
        hilo.start()
        hilo.join(espera_s)
        assert not hilo.is_alive(), f"sigue colgado a los {espera_s} s"
        if fallo:
            raise fallo[0]
        return hecho[0]

    def lanzar(self, linea: str) -> None:
        """Un proceso en este escritorio. `subprocess` no deja elegirlo."""
        ctypes = self._ctypes
        from ctypes import wintypes

        class Inicio(ctypes.Structure):
            _fields_ = (
                ("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR),
                ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
                ("relleno", wintypes.DWORD * 8), ("dwFlags", wintypes.DWORD),
                ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
                ("lpReserved2", ctypes.c_void_p), ("hStdInput", wintypes.HANDLE),
                ("hStdOutput", wintypes.HANDLE), ("hStdError", wintypes.HANDLE),
            )

        class Info(ctypes.Structure):
            _fields_ = (
                ("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD),
            )

        inicio = Inicio(cb=ctypes.sizeof(Inicio), lpDesktop=self.nombre)
        info = Info()
        crear = self._kernel32.CreateProcessW
        crear.argtypes = (
            wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, wintypes.BOOL,
            wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(Inicio),
            ctypes.POINTER(Info),
        )
        creado = crear(None, ctypes.create_unicode_buffer(linea), None, None, False, 0x08000000,
                       None, None, ctypes.byref(inicio), ctypes.byref(info))
        assert creado, f"no arranca: error {ctypes.get_last_error()}"
        self._kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        self._kernel32.CloseHandle(info.hProcess)
        self._kernel32.CloseHandle(info.hThread)

    def cerrar(self) -> None:
        self._user32.CloseDesktop(self._manejador)


@pytest.fixture(scope="module")
def oculto() -> Iterator[_Oculto]:
    escritorio = _Oculto()
    yield escritorio
    escritorio.cerrar()


def test_el_aviso_con_plazo_se_cierra_solo(oculto: _Oculto) -> None:
    inicio = time.monotonic()
    oculto.en_un_hilo(lambda: winapi.cuadro("Voziris", "se cierra solo", segundos=0.3))
    assert 0.2 < time.monotonic() - inicio < 10


def test_un_duplicado_no_se_queda_colgado_en_su_aviso(
    oculto: _Oculto, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(principal, "AVISO_YA_ABIERTA_S", 0.3)
    oculto.en_un_hilo(principal._aviso_ya_abierta)
    assert "Voziris ya está abierto" in capsys.readouterr().err


DUPLICADOS = {
    # Lo que hará un duplicado si alguien cierra el cuadro antes de tiempo.
    "con plazo": (
        "import ctypes; ctypes.WinDLL('user32').MessageBoxTimeoutW"
        "(None, 'ya abierto', 'Voziris', 0x40040, 0, 600000)"
    ),
    # Como el del 31 de octubre: hasta la 0.1.2 usan Tk, y sin plazo. Con ellos se instalará
    # encima la primera versión que traiga esto.
    "como hasta la 0.1.2": (
        "import tkinter as tk; from tkinter import messagebox; r = tk.Tk(); r.withdraw(); "
        "messagebox.showerror('Voziris no puede arrancar', 'ya abierto'); r.destroy()"
    ),
}


@pytest.mark.parametrize("codigo", DUPLICADOS.values(), ids=DUPLICADOS.keys())
def test_cerrar_proceso_cierra_el_aviso_de_un_duplicado(
    oculto: _Oculto, tmp_path: Path, codigo: str
) -> None:
    aviso = tmp_path / "pid.txt"
    envoltura = (
        f"import os, pathlib; pathlib.Path(r'{aviso}').write_text(str(os.getpid())); {codigo}"
    )
    oculto.lanzar(f'"{sys.executable}" -c "{envoltura}"')

    limite = time.monotonic() + 60
    while not (aviso.exists() and aviso.read_text()) and time.monotonic() < limite:
        time.sleep(0.05)
    pid = int(aviso.read_text())
    try:
        while not oculto.en_un_hilo(lambda: winapi._ventanas_visibles(pid)):
            assert time.monotonic() < limite, "no ha salido su aviso"
            time.sleep(0.05)
        objetivo = winapi.Proceso(pid, winapi.ruta_de_proceso(pid) or "")
        assert oculto.en_un_hilo(lambda: winapi.cerrar_proceso(objetivo, 20.0)) == "cerrado"
    finally:
        if winapi.ruta_de_proceso(pid):
            winapi.cerrar_proceso(winapi.Proceso(pid, winapi.ruta_de_proceso(pid) or ""), 0.0)


# --- los avisos: siempre encima ------------------------------------------------------------


@pytest.fixture
def cuadros(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, int, float | None]]:
    vistos: list[tuple[str, str, int, float | None]] = []

    def cuadro(
        titulo: str, texto: str, tipo: int = winapi.MB_ICONINFORMATION,
        segundos: float | None = None,
    ) -> int:
        vistos.append((titulo, texto, tipo, segundos))
        return 0

    monkeypatch.setattr(winapi, "cuadro", cuadro)
    return vistos


def test_error_fatal_usa_el_cuadro_nativo(
    cuadros: list[Any], capsys: pytest.CaptureFixture[str]
) -> None:
    principal._error_fatal("no se puede", con_ventana=True, titulo="Mal")
    assert cuadros == [("Mal", "no se puede", winapi.MB_ICONERROR, None)]
    cuadros.clear()
    principal._error_fatal("no se puede", con_ventana=False)
    assert cuadros == []
    assert "no se puede" in capsys.readouterr().err


def test_mensaje_solo_saca_cuadro_en_el_programa_congelado(
    cuadros: list[Any], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    principal._mensaje("Título", "texto")
    assert cuadros == []  # con Python hay una consola que leer
    monkeypatch.setattr(principal.sys, "frozen", True, raising=False)
    principal._mensaje("Título", "texto")
    assert cuadros == [("Título", "texto", winapi.MB_ICONINFORMATION, None)]
    assert "Título: texto" in capsys.readouterr().out


def test_arrancar_con_otra_abierta_avisa_con_plazo_y_sale(
    cuadros: list[Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(principal, "configurar_log", lambda *a, **k: None)
    monkeypatch.setattr(principal, "activar_faulthandler", lambda carpeta: None)
    monkeypatch.setattr(winapi, "instancia_unica", lambda: False)
    monkeypatch.setattr(principal.cfg, "cargar", lambda r: pytest.fail("no debería seguir"))
    assert principal.main(["--config", str(tmp_path / "config.toml")]) == 0
    assert [(c[1], c[3]) for c in cuadros] == [
        ("Voziris ya está abierto: mira el icono de la bandeja.", principal.AVISO_YA_ABIERTA_S)
    ]


def test_cuadro_de_verdad_lleva_encima_y_primer_plano(monkeypatch: pytest.MonkeyPatch) -> None:
    llamadas: list[tuple[Any, ...]] = []

    class User32Falso:
        class MessageBoxW:  # noqa: N801
            argtypes: Any = None
            restype: Any = None

            def __new__(cls, *a: Any) -> Any:
                llamadas.append(a)
                return winapi.IDYES

    monkeypatch.setattr(winapi, "_user32", User32Falso)
    assert winapi.cuadro("t", "x", winapi.MB_YESNO) == winapi.IDYES
    estilo = llamadas[0][3]
    assert estilo & winapi.MB_TOPMOST and estilo & winapi.MB_SETFOREGROUND
    assert estilo & winapi.MB_YESNO


def test_sin_messageboxtimeout_no_sale_nada(monkeypatch: pytest.MonkeyPatch) -> None:
    class User32Falso:
        pass  # como un Windows sin la función, que no está documentada

    monkeypatch.setattr(winapi, "_user32", User32Falso)
    assert winapi.cuadro("t", "x", segundos=1) == 0


# --- --instalar: el mutex se suelta antes del aviso final ------------------------------------


@pytest.fixture
def instalar_cli(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """`_instalar_cli` sin ventanas. Devuelve lo que va pasando, en orden."""
    hechos: list[str] = []
    monkeypatch.setattr(principal.sys, "frozen", True, raising=False)
    monkeypatch.setattr(winapi, "tomar_mutex", lambda nombre: True)
    monkeypatch.setattr(winapi, "soltar_mutex", lambda nombre: hechos.append(f"suelta {nombre}"))
    monkeypatch.setattr(winapi, "hay_instancia_abierta", lambda: True)
    monkeypatch.setattr(principal, "_mensaje", lambda t, x: hechos.append(f"aviso: {t}"))
    monkeypatch.setattr(principal, "_error_fatal",
                        lambda m, con_ventana, titulo="": hechos.append(f"error: {m}"))
    monkeypatch.setattr(principal, "_cerrar_la_abierta", lambda: hechos.append("cierra"))
    monkeypatch.setattr(principal, "_reabrir_si_se_cerro",
                        lambda cerro, destino: hechos.append(f"reabre: {cerro}"))
    monkeypatch.setattr(principal, "_abrir_la_instalada",
                        lambda destino: hechos.append("abre la instalada"))
    monkeypatch.setattr(instalador, "version_instalada", lambda: None)
    monkeypatch.setattr("voziris.ui.transcripcion.ejecutar_con_progreso",
                        lambda titulo, detalle, trabajo, **k: trabajo(lambda m, f: None))
    return hechos


@pytest.mark.parametrize(("fallo", "codigo", "aviso"), [
    (instalador.InstalacionCancelada("no"), 3, "aviso: Instalación cancelada"),
    (TranscripcionCancelada(), 3, "aviso: Instalación cancelada"),
    (instalador.InstalacionFallida("no se pudo"), 1, "error: no se pudo"),
    (RuntimeError("raro"), 1, "error: No se pudo instalar Voziris: raro"),
])
def test_si_no_se_instala_reabre_y_suelta_el_mutex_antes_del_aviso(
    instalar_cli: list[str], monkeypatch: pytest.MonkeyPatch,
    fallo: Exception, codigo: int, aviso: str,
) -> None:
    def instalar(**k: Any) -> Path:
        k["antes_de_sustituir"]()
        raise fallo

    monkeypatch.setattr(instalador, "instalar", instalar)
    assert principal._instalar_cli() == codigo
    assert instalar_cli == [
        "cierra", "reabre: True", f"suelta {principal.MUTEX_INSTALAR}", aviso,
    ]


def test_si_se_instala_abre_la_nueva_y_suelta_el_mutex_antes_del_aviso(
    instalar_cli: list[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(instalador, "instalar", lambda **k: tmp_path)
    assert principal._instalar_cli() == 0
    assert instalar_cli == [
        "abre la instalada", f"suelta {principal.MUTEX_INSTALAR}", "aviso: Voziris instalado",
    ]


def test_las_preguntas_del_instalador_son_cuadros_con_sus_botones(
    instalar_cli: list[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    respuestas = iter([winapi.IDYES, 7, winapi.IDRETRY, 2])  # 7 = No, 2 = Cancelar
    estilos: list[int] = []

    def cuadro(titulo: str, texto: str, tipo: int = 0, segundos: float | None = None) -> int:
        assert segundos is None  # una pregunta espera: no se contesta sola
        estilos.append(tipo)
        return next(respuestas)

    monkeypatch.setattr(winapi, "cuadro", cuadro)

    def instalar(**k: Any) -> Path:
        assert k["preguntar_cerrar"]("¿La cierro?") is True
        assert k["preguntar_cerrar"]("¿La cierro?") is False
        assert k["preguntar_reintento"]("Lo retiene cmd.exe") is True
        assert k["preguntar_reintento"]("Lo retiene cmd.exe") is False
        return tmp_path

    monkeypatch.setattr(instalador, "instalar", instalar)
    assert principal._instalar_cli() == 0
    assert estilos[0] == winapi.MB_YESNO | winapi.MB_ICONWARNING | winapi.MB_DEFBUTTON2  # «No»
    assert estilos[2] == winapi.MB_RETRYCANCEL | winapi.MB_ICONWARNING


# --- --actualizar: lo mismo ------------------------------------------------------------------


@pytest.fixture
def actualizar_cli(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    hechos: list[str] = []
    monkeypatch.setattr(principal, "_esta_instalada", lambda: True)
    monkeypatch.setattr(winapi, "tomar_mutex", lambda nombre: True)
    monkeypatch.setattr(winapi, "soltar_mutex", lambda nombre: hechos.append(f"suelta {nombre}"))
    monkeypatch.setattr(principal, "_mensaje", lambda t, x: hechos.append(f"aviso: {t}"))
    monkeypatch.setattr(principal, "_error_fatal",
                        lambda m, con_ventana, titulo="": hechos.append(f"error: {m}"))
    monkeypatch.setattr(principal, "_abrir_pagina", lambda url: None)
    monkeypatch.setattr("voziris.ui.transcripcion.ejecutar_con_progreso",
                        lambda titulo, detalle, trabajo, **k: trabajo(lambda m, f: None))
    return hechos


def _novedad() -> act.Novedad:
    return act.Novedad("9.9.9", zip_url="http://z", sumas_url="http://s")


def _falla(error: Exception) -> Callable[..., Any]:
    def lanzar(*a: Any, **k: Any) -> Any:
        raise error

    return lanzar


@pytest.mark.parametrize(("consultar", "descargar", "codigo", "aviso"), [
    (_falla(act.ConsultaFallida("sin red")), None, 1,
     "error: No se pudo consultar la versión nueva: sin red."),
    (_novedad, _falla(act.ActualizacionFallida("no coincide")), 1, "error: no coincide"),
    (_falla(RuntimeError("raro")), None, 1, "error: No se pudo actualizar Voziris: raro"),
    (lambda: None, None, principal.SALIDA_NADA_QUE_INSTALAR, "aviso: Voziris está al día"),
    (lambda: act.Novedad("9.9.9"), None, principal.SALIDA_NADA_QUE_INSTALAR,
     "aviso: Actualizar Voziris"),
], ids=["sin red", "descarga mala", "fallo raro", "al día", "sin paquete"])
def test_actualizar_suelta_el_mutex_antes_de_su_aviso_final(
    actualizar_cli: list[str], monkeypatch: pytest.MonkeyPatch,
    consultar: Callable[..., Any], descargar: Callable[..., Any] | None, codigo: int, aviso: str,
) -> None:
    monkeypatch.setattr(act, "consultar", consultar)
    if descargar is not None:
        monkeypatch.setattr(act, "descargar", descargar)
    assert principal._actualizar_cli() == codigo
    assert actualizar_cli == [f"suelta {principal.MUTEX_ACTUALIZAR}", aviso]


def test_actualizar_cancelada_suelta_el_mutex_antes_de_su_aviso(
    actualizar_cli: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("voziris.ui.transcripcion.ejecutar_con_progreso",
                        _falla(TranscripcionCancelada()))
    assert principal._actualizar_cli() == 3
    assert actualizar_cli == [
        f"suelta {principal.MUTEX_ACTUALIZAR}", "aviso: Actualización cancelada",
    ]


def test_si_el_instalador_nuevo_no_arranca_suelta_el_mutex_antes_del_error(
    actualizar_cli: list[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(act, "consultar", _novedad)
    monkeypatch.setattr(act, "descargar", lambda n, p: tmp_path / "voziris.exe")
    monkeypatch.setattr(principal, "_lanzar_desatendido", _falla(OSError("no arranca")))
    assert principal._actualizar_cli() == 1
    assert actualizar_cli[0] == f"suelta {principal.MUTEX_ACTUALIZAR}"
    assert actualizar_cli[1].startswith("error: No arranca el instalador")


def test_si_el_instalador_nuevo_arranca_el_mutex_sigue_hasta_salir(
    actualizar_cli: list[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Sin hueco en el que la bandeja no vea nada: el mutex se va con el proceso."""
    monkeypatch.setattr(act, "consultar", _novedad)
    monkeypatch.setattr(act, "descargar", lambda n, p: tmp_path / "voziris.exe")
    monkeypatch.setattr(principal, "_lanzar_desatendido", lambda orden, cwd=None: None)
    monkeypatch.setattr(principal, "_esperar_al_instalador", lambda proceso, segundos: None)
    assert principal._actualizar_cli() == 0
    assert actualizar_cli == []
