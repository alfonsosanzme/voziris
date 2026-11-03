"""Ajustes comunes de la batería de pruebas.

Aquí hay dos seguros contra trampas de Tkinter que solo salen dentro de pytest.

El primero, contra una que mataba la suite entera: si un objeto `Tk` queda
atrapado en un ciclo de referencias, solo lo libera el recolector de basura, y
si el recolector salta en un hilo que no es el dueño del intérprete Tcl, Tcl
entra en pánico («async handler deleted by the wrong thread») y mata el proceso
con un 0x80000003, sin resumen de tests ni nada. Pasaba en 9 de cada 11
ejecuciones. Recogiendo al final de cada test, en el hilo principal, la basura
de Tk nunca llega a manos de un hilo de fondo. Ver bugs.python.org/issue39093.

El segundo, contra una que saltaba los tests de ventanas sin avisar: ver
`_tcl_sin_canales_estandar`.
"""

from __future__ import annotations

import gc
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest


def _tcl_sin_canales_estandar() -> None:
    """Crea el primer intérprete Tcl del proceso sin stdin, stdout ni stderr.

    El primer intérprete Tcl de un hilo envuelve en canales los handles que da
    `GetStdHandle`, sin duplicarlos, y los apunta en una lista del hilo que no
    se vacía hasta que acaba el proceso. Dentro de pytest esos handles son los
    de los archivos temporales de su captura, y pytest los cierra y los cambia
    (`dup2`) de un test a otro. Windows recicla los números: si un `tk.Tk()`
    posterior abre un .tcl y le toca el número de aquel stdout, Tcl encuentra
    el canal viejo en su lista, ve que era de escritura, devuelve NULL sin
    cerrar el handle, y `source` falla con un errno de antes: «couldn't read
    file ".../ttk/defaults.tcl": no such file or directory». Así se saltaban
    los 10 tests de test_ajustes.py en 7 de cada 20 ejecuciones con test_hud.py
    delante: el primer `Tk` de test_hud se quedaba con los handles.

    Con los handles estándar a NULL mientras nace este intérprete, Tcl apunta
    que el hilo no tiene canales estándar y no los vuelve a buscar
    (`Tcl_GetStdChannel`, tclIO.c). Todas las raíces de Tk de la suite se crean
    en el hilo principal, que es donde corre esto.
    """
    if sys.platform != "win32":
        return
    import ctypes
    import tkinter
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetStdHandle.argtypes = (wintypes.DWORD,)
    kernel32.GetStdHandle.restype = wintypes.HANDLE
    kernel32.SetStdHandle.argtypes = (wintypes.DWORD, wintypes.HANDLE)
    kernel32.SetStdHandle.restype = wintypes.BOOL
    estandar = (-10, -11, -12)  # STD_INPUT_HANDLE, STD_OUTPUT_HANDLE, STD_ERROR_HANDLE
    guardados = [kernel32.GetStdHandle(n) for n in estandar]
    try:
        for n in estandar:
            kernel32.SetStdHandle(n, None)
        # `file channels` crea la tabla de canales del intérprete, y con ella
        # los canales estándar del hilo (aquí, ninguno).
        tkinter.Tcl().eval("file channels")
    except tkinter.TclError:
        pass  # sin Tcl: los tests de ventanas ya se saltan solos
    finally:
        for n, h in zip(estandar, guardados, strict=True):
            kernel32.SetStdHandle(n, h)


def pytest_configure(config: pytest.Config) -> None:
    _tcl_sin_canales_estandar()


@pytest.fixture(autouse=True)
def _recoger_en_el_hilo_principal() -> Iterator[None]:
    yield
    gc.collect()


# --- procesos de verdad, para lo que retiene una carpeta (VOZ-85) -------------------------


@pytest.fixture
def copia_viva() -> Iterator[Callable[..., subprocess.Popen[bytes]]]:
    """Lanza un proceso con su ejecutable en la ruta que se pida. Lo recoge al acabar el test.

    Es una copia de cmd esperando en su entrada: sin ventana y sin hijos, y lo
    que se le pase queda en su orden, como `--transcribir reunion.m4a`.
    """
    cmd = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / "cmd.exe"
    lanzados: list[subprocess.Popen[bytes]] = []

    def lanzar(
        ejecutable: Path, *argumentos: str, cwd: Path | None = None
    ) -> subprocess.Popen[bytes]:
        ejecutable.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cmd, ejecutable)
        proceso: Any = subprocess.Popen(
            [str(ejecutable), "/k", "rem", *argumentos], cwd=cwd,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        lanzados.append(proceso)
        proceso.stdout.read(1)  # su primera salida: ya ha arrancado y retiene lo que retenga
        return proceso

    yield lanzar
    for proceso in lanzados:
        proceso.kill()
        proceso.communicate(timeout=10)


@dataclass
class HijoQueRetiene:
    proceso: subprocess.Popen[bytes]
    pid: int
    """El de verdad: `Popen.pid`, con el Python de un venv, es el de su lanzador."""

    def soltar(self) -> None:
        if self.proceso.poll() is None:
            self.proceso.communicate(timeout=30)  # cierra su entrada: es lo que espera para salir


@pytest.fixture
def hijo_que_retiene() -> Iterator[Callable[..., HijoQueRetiene]]:
    """Lanza un Python con `archivo` abierto y/o con `cwd` como directorio de trabajo."""
    codigo = (
        "import os, sys; "
        "f = open(sys.argv[1], 'rb') if len(sys.argv) > 1 else None; "
        "print(os.getpid(), flush=True); "
        "sys.stdin.read()"
    )
    lanzados: list[HijoQueRetiene] = []

    def lanzar(archivo: Path | None = None, cwd: Path | None = None) -> HijoQueRetiene:
        proceso: Any = subprocess.Popen(
            [sys.executable, "-c", codigo, *([str(archivo)] if archivo else [])], cwd=cwd,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        hijo = HijoQueRetiene(proceso, int(proceso.stdout.readline()))
        lanzados.append(hijo)
        return hijo

    yield lanzar
    for hijo in lanzados:
        hijo.soltar()
