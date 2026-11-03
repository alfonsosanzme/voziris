"""Lo que Voziris necesita de Win32, con firmas de `ctypes` declaradas y tipadas.

Un solo sitio para las estructuras de `SendInput`, la ventana en primer plano
y el portapapeles, para que el resto del código no esparza `ctypes` ni
`# type: ignore`. Todo lo que toca el sistema está detrás de una función con
nombre en castellano y docstring.

El módulo se puede importar en cualquier sistema (los tests puros lo hacen);
las funciones que llaman a Windows fallan con `OSError` fuera de él.

Se usa en `destinos/app_activa.py` (VOZ-12) y en `atajos.py` (VOZ-02).
"""

from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
from collections.abc import Sequence
from ctypes import wintypes
from typing import NamedTuple

ES_WINDOWS = sys.platform == "win32"

# --- teclas virtuales (winuser.h) ---------------------------------------------

VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12  # Alt
VK_RETURN = 0x0D
VK_TAB = 0x09
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_LSHIFT = 0xA0
VK_RSHIFT = 0xA1
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_LMENU = 0xA4
VK_RMENU = 0xA5
VK_V = 0x56

MODIFICADORES_FISICOS = (
    VK_LCONTROL, VK_RCONTROL, VK_LMENU, VK_RMENU, VK_LSHIFT, VK_RSHIFT, VK_LWIN, VK_RWIN,
)
"""Las teclas modificadoras tal como las ve `GetAsyncKeyState`: lado a lado."""

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

MARCA_VOZIRIS = 0x564F5A49
"""`dwExtraInfo` de todo lo que inyecta Voziris («VOZI»): el hook de teclado
(VOZ-02) lo usa para no reaccionar a sus propias pulsaciones."""


# --- estructuras de SendInput -------------------------------------------------


class KEYBDINPUT(ctypes.Structure):
    _fields_ = (
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),  # ULONG_PTR
    )


class _UNION_INPUT(ctypes.Union):
    # MOUSEINPUT es la variante más grande (32 bytes en x64); el relleno
    # garantiza que INPUT mida los 40 bytes que espera Windows.
    _fields_ = (("ki", KEYBDINPUT), ("relleno", ctypes.c_ubyte * 32))


class INPUT(ctypes.Structure):
    _fields_ = (("type", wintypes.DWORD), ("union", _UNION_INPUT))


def evento_tecla(vk: int, arriba: bool = False, marca: int = MARCA_VOZIRIS) -> INPUT:
    """Pulsación (o liberación) de una tecla virtual.

    `marca` va en `dwExtraInfo`; el hook de teclado ignora lo que lleve
    `MARCA_VOZIRIS`. Los tests inyectan con `marca=0` para que el hook las
    trate como pulsaciones físicas.
    """
    ev = INPUT(type=INPUT_KEYBOARD)
    ev.union.ki = KEYBDINPUT(
        wVk=vk, wScan=0, dwFlags=KEYEVENTF_KEYUP if arriba else 0, time=0, dwExtraInfo=marca
    )
    return ev


def evento_unicode(unidad: int, arriba: bool = False) -> INPUT:
    """Un carácter tal cual, por su unidad UTF-16: acentos, eñes y emoji incluidos."""
    ev = INPUT(type=INPUT_KEYBOARD)
    flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if arriba else 0)
    ev.union.ki = KEYBDINPUT(wVk=0, wScan=unidad, dwFlags=flags, time=0, dwExtraInfo=MARCA_VOZIRIS)
    return ev


def eventos_texto(texto: str) -> list[INPUT]:
    """Los eventos que teclean `texto` carácter a carácter.

    `\\n` se manda como Enter y `\\t` como Tab, que es lo que espera cualquier
    editor. El resto va como Unicode: un carácter fuera del plano básico (un
    emoji) son dos unidades UTF-16 y Windows las junta él solo.
    """
    eventos: list[INPUT] = []
    for caracter in texto:
        if caracter == "\r":
            continue
        if caracter == "\n":
            eventos += [evento_tecla(VK_RETURN), evento_tecla(VK_RETURN, arriba=True)]
        elif caracter == "\t":
            eventos += [evento_tecla(VK_TAB), evento_tecla(VK_TAB, arriba=True)]
        else:
            for unidad in _unidades(caracter):
                eventos += [evento_unicode(unidad), evento_unicode(unidad, arriba=True)]
    return eventos


def _unidades(caracter: str) -> list[int]:
    datos = caracter.encode("utf-16-le")
    return [int.from_bytes(datos[i : i + 2], "little") for i in range(0, len(datos), 2)]


def eventos_combinacion(*vks: int) -> list[INPUT]:
    """Pulsa las teclas en orden y las suelta en orden inverso: Ctrl+V, Enter…"""
    abajo = [evento_tecla(vk) for vk in vks]
    arriba = [evento_tecla(vk, arriba=True) for vk in reversed(vks)]
    return abajo + arriba


# --- llamadas a Windows -------------------------------------------------------

if ES_WINDOWS:
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
    _user32.SendInput.restype = wintypes.UINT
    _user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
    _user32.GetAsyncKeyState.restype = ctypes.c_short
    _user32.GetForegroundWindow.argtypes = ()
    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.FindWindowW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
    _user32.FindWindowW.restype = wintypes.HWND
    _user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    _user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    _kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.QueryFullProcessImageNameW.argtypes = (
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
    )
    _kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    _kernel32.GlobalMemoryStatusEx.argtypes = (ctypes.c_void_p,)
    _kernel32.GlobalMemoryStatusEx.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _kernel32.CloseHandle.restype = wintypes.BOOL


def _solo_windows() -> None:
    if not ES_WINDOWS:
        raise OSError("esta función solo existe en Windows")


TAMANO_LOTE = 256
"""Eventos por llamada a SendInput. Un texto largo tecleado va en varias."""


def enviar(eventos: Sequence[INPUT]) -> None:
    """Inyecta los eventos de teclado en el orden dado.

    Raises:
        OSError: Windows no aceptó todos los eventos. La causa habitual es
            UIPI: la ventana en primer plano corre elevada y Voziris no (R3).
    """
    _solo_windows()
    for inicio in range(0, len(eventos), TAMANO_LOTE):
        lote = eventos[inicio : inicio + TAMANO_LOTE]
        matriz = (INPUT * len(lote))(*lote)
        enviados = _user32.SendInput(len(lote), matriz, ctypes.sizeof(INPUT))
        if enviados != len(lote):
            codigo = ctypes.get_last_error()
            raise OSError(
                f"Windows rechazó la pulsación sintética (SendInput: {enviados}/{len(lote)}, "
                f"error {codigo}). Si la aplicación de destino corre como administrador, "
                "no puede recibirla."
            )


def pulsar_combinacion(*vks: int) -> None:
    enviar(eventos_combinacion(*vks))


def teclear(texto: str) -> None:
    enviar(eventos_texto(texto))


def tecla_pulsada(vk: int) -> bool:
    """Si Windows da la tecla por apretada ahora mismo (`GetAsyncKeyState`).

    Cuenta también lo inyectado con `SendInput`. Dentro del hook de teclado
    aún no refleja la tecla que se está procesando, solo las anteriores.
    """
    _solo_windows()
    return bool(_user32.GetAsyncKeyState(vk) & 0x8000)


def modificadores_pulsados() -> list[int]:
    """Modificadores que el usuario tiene físicamente apretados ahora mismo."""
    return [vk for vk in MODIFICADORES_FISICOS if tecla_pulsada(vk)]


def soltar_modificadores() -> list[int]:
    """Manda «tecla arriba» de los modificadores apretados y devuelve cuáles.

    Sin esto, el Ctrl+V sintético que llega justo al soltar `ctrl+win` se
    convierte en Ctrl+Win+V y abre el historial del portapapeles de Windows.
    Cuando el usuario suelte de verdad, Windows recibe otro «arriba» y no pasa
    nada.
    """
    pulsados = modificadores_pulsados()
    if pulsados:
        enviar([evento_tecla(vk, arriba=True) for vk in pulsados])
    return pulsados


def ventana_en_primer_plano() -> int:
    _solo_windows()
    return int(_user32.GetForegroundWindow() or 0)


def ventana_por_titulo(titulo: str) -> int:
    """El HWND de la ventana de nivel superior con ese título exacto, o 0."""
    _solo_windows()
    return int(_user32.FindWindowW(None, titulo) or 0)


def ejecutable_en_primer_plano() -> str | None:
    """Nombre del ejecutable de la ventana activa («chrome.exe») o None si no se sabe.

    None no es un error: pasa con procesos elevados o del sistema, y entonces
    solo se pierde una etiqueta informativa.
    """
    _solo_windows()
    hwnd = _user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = wintypes.DWORD(0)
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return None
    proceso = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not proceso:
        return None
    try:
        tamano = wintypes.DWORD(1024)
        ruta = ctypes.create_unicode_buffer(tamano.value)
        if not _kernel32.QueryFullProcessImageNameW(proceso, 0, ruta, ctypes.byref(tamano)):
            return None
        return ruta.value.replace("/", "\\").rsplit("\\", 1)[-1].lower() or None
    finally:
        _kernel32.CloseHandle(proceso)


def nombre_de_proceso(pid: int) -> str | None:
    """«chrome.exe» a partir de un PID, o None si el proceso no se deja mirar."""
    ruta = ruta_de_proceso(pid)
    if ruta is None:
        return None
    return ruta.replace("/", "\\").rsplit("\\", 1)[-1].lower() or None


def ruta_de_proceso(pid: int) -> str | None:
    """La ruta del ejecutable de un proceso, o None si el proceso no se deja mirar."""
    _solo_windows()
    if pid <= 0:
        return None
    proceso = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not proceso:
        return None
    try:
        return _ruta_de_manejador(proceso)
    finally:
        _kernel32.CloseHandle(proceso)


def _ruta_de_manejador(proceso: int) -> str | None:
    tamano = wintypes.DWORD(1024)
    ruta = ctypes.create_unicode_buffer(tamano.value)
    if not _kernel32.QueryFullProcessImageNameW(proceso, 0, ruta, ctypes.byref(tamano)):
        return None
    return ruta.value or None


class _EstadoMemoria(ctypes.Structure):
    _fields_ = (
        ("dwLength", wintypes.DWORD),
        ("dwMemoryLoad", wintypes.DWORD),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    )


def memoria_libre_mb() -> float | None:
    """RAM física libre ahora mismo, en MB. None si no se puede saber.

    Sirve para decidir antes de empezar, no para afinar: lo que se evita es
    arrancar un trabajo de media hora que va a morir por falta de memoria.
    """
    if not ES_WINDOWS:
        return None
    estado = _EstadoMemoria()
    estado.dwLength = ctypes.sizeof(estado)
    if not _kernel32.GlobalMemoryStatusEx(ctypes.byref(estado)):
        return None
    return float(estado.ullAvailPhys) / 2**20


# --- ventanas sin foco (HUD) ----------------------------------------------------

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOPMOST = 0x00000008
SW_SHOWNOACTIVATE = 4
SW_HIDE = 0
MONITOR_DEFAULTTONEAREST = 2
SPI_GETCLIENTAREAANIMATION = 0x1042


class _RECT(ctypes.Structure):
    _fields_ = (("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long))


class _MONITORINFO(ctypes.Structure):
    _fields_ = (("cbSize", wintypes.DWORD), ("rcMonitor", _RECT),
                ("rcWork", _RECT), ("dwFlags", wintypes.DWORD))


def hacer_ventana_sin_foco(hwnd: int) -> None:
    """Marca una ventana para que nunca se active ni salga en Alt+Tab.

    `WS_EX_NOACTIVATE`: recibe clics y se pinta, pero no toma el foco. Si el
    HUD lo tomara, el texto se pegaría en el propio HUD. `WS_EX_TOOLWINDOW`:
    fuera de Alt+Tab y de la barra de tareas. `WS_EX_TOPMOST`: siempre encima.
    """
    _solo_windows()
    _user32.GetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int)
    _user32.GetWindowLongW.restype = ctypes.c_long
    _user32.SetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_long)
    _user32.SetWindowLongW.restype = ctypes.c_long
    actual = _user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    _user32.SetWindowLongW(
        hwnd, GWL_EXSTYLE, actual | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST
    )


def estilo_extendido(hwnd: int) -> int:
    _solo_windows()
    _user32.GetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int)
    _user32.GetWindowLongW.restype = ctypes.c_long
    return int(_user32.GetWindowLongW(hwnd, GWL_EXSTYLE)) & 0xFFFFFFFF


def mostrar_sin_activar(hwnd: int) -> None:
    """Muestra la ventana sin darle el foco. `deiconify()` de Tk sí lo daría."""
    _solo_windows()
    _user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
    _user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)


HWND_TOPMOST = ctypes.c_void_p(-1 & 0xFFFFFFFFFFFFFFFF)
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040


def colocar_sin_activar(hwnd: int, x: int, y: int, ancho: int, alto: int) -> None:
    """Posición, tamaño, «siempre encima» y visible, en una sola llamada y sin foco.

    Tk difiere la geometría de una ventana retirada hasta que la mapea él, y
    aquí la mapeamos nosotros: si se confía en `geometry()` + `ShowWindow`, la
    ventana puede aparecer donde estaba antes, detrás de otra, o no aparecer.
    `SetWindowPos` con `HWND_TOPMOST` deja las cuatro cosas atadas.
    """
    _solo_windows()
    _user32.SetWindowPos.argtypes = (
        wintypes.HWND, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.UINT,
    )
    _user32.SetWindowPos.restype = wintypes.BOOL
    if not _user32.SetWindowPos(
        hwnd, HWND_TOPMOST, x, y, ancho, alto, SWP_NOACTIVATE | SWP_SHOWWINDOW
    ):
        raise OSError(f"SetWindowPos falló (error {ctypes.get_last_error()})")


def area_de_trabajo_activa() -> tuple[int, int, int, int]:
    """(x, y, ancho, alto) del área útil del monitor donde está la ventana activa.

    Sin la barra de tareas. Si no se puede saber, el monitor principal.
    """
    _solo_windows()
    _user32.MonitorFromWindow.argtypes = (wintypes.HWND, wintypes.DWORD)
    _user32.MonitorFromWindow.restype = wintypes.HMONITOR
    _user32.GetMonitorInfoW.argtypes = (wintypes.HMONITOR, ctypes.POINTER(_MONITORINFO))
    _user32.GetMonitorInfoW.restype = wintypes.BOOL
    monitor = _user32.MonitorFromWindow(_user32.GetForegroundWindow(), MONITOR_DEFAULTTONEAREST)
    info = _MONITORINFO()
    info.cbSize = ctypes.sizeof(_MONITORINFO)
    if not monitor or not _user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        ancho = int(_user32.GetSystemMetrics(0))
        alto = int(_user32.GetSystemMetrics(1))
        return 0, 0, ancho, alto
    r = info.rcWork
    return int(r.left), int(r.top), int(r.right - r.left), int(r.bottom - r.top)


def animaciones_activas() -> bool:
    """False si el usuario desactivó las animaciones en Windows (movimiento reducido)."""
    if not ES_WINDOWS:
        return True
    _user32.SystemParametersInfoW.argtypes = (
        wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT,
    )
    _user32.SystemParametersInfoW.restype = wintypes.BOOL
    valor = wintypes.BOOL(1)
    if not _user32.SystemParametersInfoW(SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(valor), 0):
        return True
    return bool(valor.value)


# --- instancia única ------------------------------------------------------------

ERROR_ALREADY_EXISTS = 183
_mutex_instancia: int | None = None


def instancia_unica(nombre: str = "Local\\Voziris") -> bool:
    """True si esta es la primera instancia; False si ya hay otra Voziris abierta.

    Un mutex con nombre que vive lo que el proceso: Windows lo libera al morir,
    aunque sea de mala manera. Dos instancias (arranque con Windows más un
    doble clic) competirían por el micrófono y los atajos (VOZ-05).
    """
    global _mutex_instancia
    _solo_windows()
    if _mutex_instancia is not None:
        return True
    _kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    _kernel32.CreateMutexW.restype = wintypes.HANDLE
    ctypes.set_last_error(0)
    manejador = _kernel32.CreateMutexW(None, False, nombre)
    if not manejador:
        return True  # sin mutex no se puede saber: mejor arrancar que no arrancar
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        _kernel32.CloseHandle(manejador)
        return False
    _mutex_instancia = int(manejador)
    return True


def hay_instancia_abierta(nombre: str = "Local\\Voziris") -> bool:
    """True si otro proceso tiene el mutex, sin tomarlo. Para scripts y tests."""
    _solo_windows()
    if _mutex_instancia is not None:
        return False
    _kernel32.OpenMutexW.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
    _kernel32.OpenMutexW.restype = wintypes.HANDLE
    manejador = _kernel32.OpenMutexW(0x00100000, False, nombre)  # SYNCHRONIZE
    if not manejador:
        return False
    _kernel32.CloseHandle(manejador)
    return True


def existe_mutex(nombre: str) -> bool:
    """True si algún proceso (este también) tiene el mutex `nombre`, sin tomarlo."""
    _solo_windows()
    _kernel32.OpenMutexW.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
    _kernel32.OpenMutexW.restype = wintypes.HANDLE
    manejador = _kernel32.OpenMutexW(0x00100000, False, nombre)  # SYNCHRONIZE
    if not manejador:
        return False
    _kernel32.CloseHandle(manejador)
    return True


_mutex_tareas: dict[str, int] = {}


def tomar_mutex(nombre: str) -> bool:
    """True si nadie más tiene el mutex `nombre`: queda tomado hasta que el proceso muera.

    Para tareas que no deben correr dos a la vez (`--actualizar`, VOZ-82). No
    toca el de la instancia: `hay_instancia_abierta()` sigue diciendo la verdad.
    """
    _solo_windows()
    if nombre in _mutex_tareas:
        return True
    _kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    _kernel32.CreateMutexW.restype = wintypes.HANDLE
    ctypes.set_last_error(0)
    manejador = _kernel32.CreateMutexW(None, False, nombre)
    if not manejador:
        return True  # sin mutex no se puede saber: mejor seguir que no hacer nada
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        _kernel32.CloseHandle(manejador)
        return False
    _mutex_tareas[nombre] = int(manejador)
    return True


def soltar_mutex(nombre: str) -> None:
    """Suelta lo que tomó `tomar_mutex`, sin esperar a que el proceso muera. Si no lo tenía, nada.

    Se llama antes de un aviso final, que puede quedarse abierto mucho tiempo y
    detrás de otras ventanas: la tarea ya ha terminado, y mientras el mutex
    siga tomado la bandeja la da por en marcha (VOZ-85).
    """
    manejador = _mutex_tareas.pop(nombre, None)
    if manejador is not None:
        _kernel32.CloseHandle(manejador)


NOMBRE_EVENTO_SALIDA = "Local\\Voziris.Salir"
WAIT_OBJECT_0 = 0
EVENT_MODIFY_STATE = 0x0002
_evento_salida: int | None = None


def crear_evento_salida(nombre: str = NOMBRE_EVENTO_SALIDA) -> None:
    """La instancia abierta crea el evento con nombre que `pedir_salida()` activa."""
    global _evento_salida
    _solo_windows()
    _kernel32.CreateEventW.argtypes = (
        ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR,
    )
    _kernel32.CreateEventW.restype = wintypes.HANDLE
    manejador = _kernel32.CreateEventW(None, True, False, nombre)
    _evento_salida = int(manejador) if manejador else None


def salida_pedida() -> bool:
    """True si alguien ejecutó `voziris --salir`. Barato: se consulta cada medio segundo."""
    if _evento_salida is None:
        return False
    _kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    _kernel32.WaitForSingleObject.restype = wintypes.DWORD
    return int(_kernel32.WaitForSingleObject(_evento_salida, 0)) == WAIT_OBJECT_0


def pedir_salida(nombre: str = NOMBRE_EVENTO_SALIDA) -> bool:
    """Pide a la Voziris abierta que se cierre limpiamente. False si no hay ninguna."""
    _solo_windows()
    _kernel32.OpenEventW.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
    _kernel32.OpenEventW.restype = wintypes.HANDLE
    _kernel32.SetEvent.argtypes = (wintypes.HANDLE,)
    _kernel32.SetEvent.restype = wintypes.BOOL
    manejador = _kernel32.OpenEventW(EVENT_MODIFY_STATE, False, nombre)
    if not manejador:
        return False
    try:
        return bool(_kernel32.SetEvent(manejador))
    finally:
        _kernel32.CloseHandle(manejador)


# --- procesos: quién sigue vivo y quién retiene una carpeta (VOZ-85) ------------

PROCESS_TERMINATE = 0x0001
SYNCHRONIZE = 0x00100000
WM_CLOSE = 0x0010
ERROR_MORE_DATA = 234
_PIDS_DEL_SISTEMA = frozenset({0, 4})
"""«System Idle Process» y «System»: no se pueden cerrar y no dicen nada al usuario."""
_ESPERA_TERMINAR_S = 2.0
_PROCESS_COMMAND_LINE_INFORMATION = 60
_FILE_PROCESS_IDS_USING_FILE_INFORMATION = 47
_FILE_READ_ATTRIBUTES = 0x80
_FILE_SHARE_TODO = 0x7
_OPEN_EXISTING = 3
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value


class Proceso(NamedTuple):
    pid: int
    ruta: str
    """La del ejecutable, o el nombre que dé Restart Manager si Windows no deja verla. Puede
    estar vacía."""


class _UNICODE_STRING(ctypes.Structure):
    _fields_ = (
        ("Length", wintypes.USHORT),
        ("MaximumLength", wintypes.USHORT),
        ("Buffer", ctypes.c_void_p),
    )


class _IO_STATUS_BLOCK(ctypes.Structure):
    _fields_ = (("Status", ctypes.c_void_p), ("Information", ctypes.c_void_p))


class _RM_PROCESS_INFO(ctypes.Structure):
    _fields_ = (
        ("dwProcessId", wintypes.DWORD),
        ("ProcessStartTime", wintypes.FILETIME),
        ("strAppName", wintypes.WCHAR * 256),
        ("strServiceShortName", wintypes.WCHAR * 64),
        ("ApplicationType", ctypes.c_int),
        ("AppStatus", wintypes.ULONG),
        ("TSSessionId", wintypes.DWORD),
        ("bRestartable", wintypes.BOOL),
    )


if ES_WINDOWS:
    _ntdll = ctypes.WinDLL("ntdll")
    _WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    _ntdll.NtQueryInformationProcess.argtypes = (
        wintypes.HANDLE, wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG,
        ctypes.POINTER(wintypes.ULONG),
    )
    _ntdll.NtQueryInformationProcess.restype = ctypes.c_long
    _ntdll.NtQueryInformationFile.argtypes = (
        wintypes.HANDLE, ctypes.POINTER(_IO_STATUS_BLOCK), ctypes.c_void_p, wintypes.ULONG,
        wintypes.ULONG,
    )
    _ntdll.NtQueryInformationFile.restype = ctypes.c_long
    _kernel32.K32EnumProcesses.argtypes = (
        ctypes.POINTER(wintypes.DWORD), wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
    )
    _kernel32.K32EnumProcesses.restype = wintypes.BOOL
    _kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    _kernel32.WaitForSingleObject.restype = wintypes.DWORD
    _kernel32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
    _kernel32.TerminateProcess.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = (wintypes.HLOCAL,)
    _kernel32.LocalFree.restype = wintypes.HLOCAL
    _kernel32.CreateFileW.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
        wintypes.DWORD, wintypes.HANDLE,
    )
    _kernel32.CreateFileW.restype = wintypes.HANDLE
    _user32.EnumWindows.argtypes = (_WNDENUMPROC, wintypes.LPARAM)
    _user32.EnumWindows.restype = wintypes.BOOL
    _user32.IsWindowVisible.argtypes = (wintypes.HWND,)
    _user32.IsWindowVisible.restype = wintypes.BOOL
    _user32.PostMessageW.argtypes = (
        wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
    )
    _user32.PostMessageW.restype = wintypes.BOOL


def _pids_vivos() -> list[int]:
    capacidad = 4096
    while True:
        pids = (wintypes.DWORD * capacidad)()
        devuelto = wintypes.DWORD()
        if not _kernel32.K32EnumProcesses(pids, ctypes.sizeof(pids), ctypes.byref(devuelto)):
            raise OSError(f"K32EnumProcesses falló (error {ctypes.get_last_error()})")
        cuantos = devuelto.value // ctypes.sizeof(wintypes.DWORD)
        if cuantos < capacidad:  # si cabe justo, puede que falten: Windows no avisa
            return [int(pid) for pid in pids[:cuantos]]
        capacidad *= 2


def procesos_en_carpeta(carpeta: str | os.PathLike[str]) -> list[Proceso]:
    """Los procesos cuyo ejecutable está dentro de `carpeta`, salvo este.

    «Voziris.nuevo» no cuenta como dentro de «Voziris»: se compara con la barra.

    Raises:
        OSError: Windows no da la lista de procesos.
    """
    _solo_windows()
    prefijo = os.path.normcase(os.path.join(os.fspath(carpeta), ""))
    vivos = []
    for pid in _pids_vivos():
        ruta = None if pid == os.getpid() else ruta_de_proceso(pid)
        if ruta is not None and os.path.normcase(ruta).startswith(prefijo):
            vivos.append(Proceso(pid, ruta))
    return vivos


def orden_de_proceso(pid: int) -> list[str] | None:
    """La orden con la que arrancó un proceso, ya partida. None si no se deja leer."""
    _solo_windows()
    proceso = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not proceso:
        return None
    try:
        datos = ctypes.create_string_buffer(1 << 17)  # 32 767 caracteres: el máximo de Windows
        estado = _ntdll.NtQueryInformationProcess(
            proceso, _PROCESS_COMMAND_LINE_INFORMATION, datos, len(datos),
            ctypes.byref(wintypes.ULONG()),
        )
        if estado < 0:
            return None
        cadena = _UNICODE_STRING.from_buffer(datos)
        linea = ctypes.wstring_at(cadena.Buffer, cadena.Length // 2) if cadena.Buffer else ""
    finally:
        _kernel32.CloseHandle(proceso)
    return _partir_orden(linea) if linea else None


def _partir_orden(linea: str) -> list[str] | None:
    """Como lo parte el propio Windows. shell32 se carga aquí: cuesta, y casi nunca hace falta."""
    shell32 = ctypes.WinDLL("shell32")
    shell32.CommandLineToArgvW.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int))
    shell32.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    cuantas = ctypes.c_int()
    partes = shell32.CommandLineToArgvW(linea, ctypes.byref(cuantas))
    if not partes:
        return None
    try:
        return [partes[i] for i in range(cuantas.value)]
    finally:
        _kernel32.LocalFree(partes)


def _ventanas_visibles(pid: int) -> list[int]:
    """Las de `pid` en el escritorio de este hilo: `EnumWindows` no ve las de otros."""
    ventanas: list[int] = []

    def mirar(hwnd: int, _: int) -> bool:
        dueno = wintypes.DWORD(0)
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(dueno))
        if dueno.value == pid and _user32.IsWindowVisible(hwnd):
            ventanas.append(hwnd)
        return True

    _user32.EnumWindows(_WNDENUMPROC(mirar), 0)
    return ventanas


def cerrar_proceso(objetivo: Proceso, espera_s: float) -> str:
    """Cierra un proceso que no atiende otra petición. Devuelve cómo ha ido, para el registro.

    `Local\\Voziris.Salir` solo lo atiende la instancia que tiene el mutex: un
    duplicado parado en su aviso no lo oye (VOZ-85). Primero se pide a sus
    ventanas que se cierren, como quien pulsa la X, y si a los `espera_s` sigue
    vivo, se termina. Su ejecutable se comprueba con el manejador ya abierto:
    mientras lo esté, Windows no da ese PID a otro proceso.

    Returns:
        «cerrado», «terminado», «ya no estaba» o «no se pudo» (corre como
        administrador, por ejemplo).
    """
    _solo_windows()
    acceso = PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_TERMINATE | SYNCHRONIZE
    proceso = _kernel32.OpenProcess(acceso, False, objetivo.pid)
    if not proceso:
        return "ya no estaba" if ruta_de_proceso(objetivo.pid) != objetivo.ruta else "no se pudo"
    try:
        if _ruta_de_manejador(proceso) != objetivo.ruta:
            return "ya no estaba"
        for ventana in _ventanas_visibles(objetivo.pid):
            _user32.PostMessageW(ventana, WM_CLOSE, 0, 0)
        if _kernel32.WaitForSingleObject(proceso, int(espera_s * 1000)) == WAIT_OBJECT_0:
            return "cerrado"
        if not _kernel32.TerminateProcess(proceso, 1):
            return "no se pudo"
        esperado = int(_ESPERA_TERMINAR_S * 1000)
        return ("terminado" if _kernel32.WaitForSingleObject(proceso, esperado) == WAIT_OBJECT_0
                else "no se pudo")
    finally:
        _kernel32.CloseHandle(proceso)


def quien_usa(archivos: Sequence[str], carpetas: Sequence[str] = ()) -> list[Proceso]:
    """Qué procesos, salvo este, tienen abierto algo de esto. Tarda: solo cuando ya ha fallado.

    Son dos fuentes porque cada una tiene su punto ciego. Restart Manager ve
    archivos abiertos y DLL cargadas, y da su nombre, pero no admite carpetas.
    `NtQueryInformationFile` ve también quién tiene una carpeta como directorio
    de trabajo, pero tarda unos 70 ms por llamada: se le pasan solo unas pocas.
    Una DLL cargada no impide renombrar su carpeta, así que puede salir alguien
    que no estorba.
    """
    _solo_windows()
    vistos = _segun_restart_manager(archivos)
    for carpeta in carpetas:
        for pid in _pids_que_usan(carpeta):
            vistos.setdefault(pid, "")
    return [
        Proceso(pid, ruta_de_proceso(pid) or nombre)
        for pid, nombre in sorted(vistos.items())
        if pid not in _PIDS_DEL_SISTEMA and pid != os.getpid()
    ]


def _segun_restart_manager(archivos: Sequence[str]) -> dict[int, str]:
    """PID → nombre. Vacío si no hay nada que mirar o Restart Manager falla: es solo una pista."""
    if not archivos:
        return {}
    rm = ctypes.WinDLL("rstrtmgr")  # aquí y no arriba: casi nunca hace falta
    rm.RmStartSession.argtypes = (ctypes.POINTER(wintypes.DWORD), wintypes.DWORD, wintypes.LPWSTR)
    rm.RmRegisterResources.argtypes = (
        wintypes.DWORD, wintypes.UINT, ctypes.POINTER(wintypes.LPCWSTR), wintypes.UINT,
        ctypes.c_void_p, wintypes.UINT, ctypes.c_void_p,
    )
    rm.RmGetList.argtypes = (
        wintypes.DWORD, ctypes.POINTER(wintypes.UINT), ctypes.POINTER(wintypes.UINT),
        ctypes.POINTER(_RM_PROCESS_INFO), ctypes.POINTER(wintypes.DWORD),
    )
    rm.RmEndSession.argtypes = (wintypes.DWORD,)
    sesion = wintypes.DWORD()
    if rm.RmStartSession(ctypes.byref(sesion), 0, ctypes.create_unicode_buffer(64)):
        return {}
    try:
        lista = (wintypes.LPCWSTR * len(archivos))(*archivos)
        if rm.RmRegisterResources(sesion, len(archivos), lista, 0, None, 0, None):
            return {}
        capacidad = 16
        for _ in range(3):  # otra vez, si entre una llamada y la siguiente aparece alguien
            necesarios = wintypes.UINT()
            cuantos = wintypes.UINT(capacidad)
            info = (_RM_PROCESS_INFO * capacidad)()
            error = rm.RmGetList(
                sesion, ctypes.byref(necesarios), ctypes.byref(cuantos), info,
                ctypes.byref(wintypes.DWORD()),
            )
            if error == 0:
                return {int(info[i].dwProcessId): str(info[i].strAppName)
                        for i in range(cuantos.value)}
            if error != ERROR_MORE_DATA:
                return {}
            capacidad = necesarios.value + 8
        return {}
    finally:
        rm.RmEndSession(sesion)


def _pids_que_usan(ruta: str) -> list[int]:
    manejador = _kernel32.CreateFileW(
        ruta, _FILE_READ_ATTRIBUTES, _FILE_SHARE_TODO, None, _OPEN_EXISTING,
        _FILE_FLAG_BACKUP_SEMANTICS, None,
    )
    if not manejador or manejador == _INVALID_HANDLE_VALUE:
        return []
    try:
        datos = ctypes.create_string_buffer(1 << 16)
        estado = _ntdll.NtQueryInformationFile(
            manejador, ctypes.byref(_IO_STATUS_BLOCK()), datos, len(datos),
            _FILE_PROCESS_IDS_USING_FILE_INFORMATION,
        )
        if estado < 0:
            return []
        cuantos = ctypes.c_ulong.from_buffer(datos).value
        # Un ULONG con cuántos y, alineada tras él, la lista de ULONG_PTR.
        return [int(pid) for pid in (ctypes.c_size_t * cuantos).from_buffer(
            datos, ctypes.sizeof(ctypes.c_size_t))]
    finally:
        _kernel32.CloseHandle(manejador)


# --- cuadros de aviso -------------------------------------------------------------

MB_RETRYCANCEL = 0x5
MB_YESNO = 0x4
MB_ICONERROR = 0x10
MB_ICONWARNING = 0x30
MB_ICONINFORMATION = 0x40
MB_DEFBUTTON2 = 0x100
MB_SETFOREGROUND = 0x10000
MB_TOPMOST = 0x40000
IDRETRY = 4
IDYES = 6


def cuadro(
    titulo: str, texto: str, tipo: int = MB_ICONINFORMATION, segundos: float | None = None
) -> int:
    """Un cuadro nativo, encima de las demás ventanas. Devuelve el botón pulsado (`IDYES`…).

    Los de Tk podían abrirse detrás de otras ventanas: el 2 de noviembre, el
    aviso final del instalador se quedó oculto varios minutos (VOZ-85). Windows
    puede negarle el foco, porque `MB_SETFOREGROUND` es una petición, pero
    `MB_TOPMOST` hace que se vea igual.

    Con `segundos`, se cierra solo al cumplirse, y entonces no hay que fiarse de
    lo que devuelve. Es `MessageBoxTimeoutW`, que no está documentada: si en
    algún Windows faltara, no sale nada y devuelve 0. Sin `segundos`, espera
    a que alguien lo cierre.
    """
    _solo_windows()
    estilo = tipo | MB_TOPMOST | MB_SETFOREGROUND
    if segundos is None:
        _user32.MessageBoxW.argtypes = (
            wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT,
        )
        _user32.MessageBoxW.restype = ctypes.c_int
        return int(_user32.MessageBoxW(None, texto, titulo, estilo))
    try:
        con_plazo = _user32.MessageBoxTimeoutW
    except AttributeError:
        return 0
    con_plazo.argtypes = (
        wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT, wintypes.WORD,
        wintypes.DWORD,
    )
    con_plazo.restype = ctypes.c_int
    return int(con_plazo(None, texto, titulo, estilo, 0, int(segundos * 1000)))


# --- portapapeles -------------------------------------------------------------

INTENTOS_PORTAPAPELES = 10
ESPERA_PORTAPAPELES_S = 0.02
"""Otra aplicación puede tener el portapapeles abierto unos milisegundos."""


GMEM_MOVEABLE = 0x0002
CF_UNICODETEXT = 13

if ES_WINDOWS:
    _user32.OpenClipboard.argtypes = (wintypes.HWND,)
    _user32.OpenClipboard.restype = wintypes.BOOL
    _user32.CloseClipboard.argtypes = ()
    _user32.CloseClipboard.restype = wintypes.BOOL
    _user32.EmptyClipboard.argtypes = ()
    _user32.EmptyClipboard.restype = wintypes.BOOL
    _user32.IsClipboardFormatAvailable.argtypes = (wintypes.UINT,)
    _user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
    _user32.GetClipboardData.argtypes = (wintypes.UINT,)
    _user32.GetClipboardData.restype = wintypes.HANDLE
    _user32.SetClipboardData.argtypes = (wintypes.UINT, wintypes.HANDLE)
    _user32.SetClipboardData.restype = wintypes.HANDLE
    _kernel32.GlobalAlloc.argtypes = (wintypes.UINT, ctypes.c_size_t)
    _kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    _kernel32.GlobalFree.argtypes = (wintypes.HGLOBAL,)
    _kernel32.GlobalFree.restype = wintypes.HGLOBAL
    _kernel32.GlobalLock.argtypes = (wintypes.HGLOBAL,)
    _kernel32.GlobalLock.restype = wintypes.LPVOID
    _kernel32.GlobalUnlock.argtypes = (wintypes.HGLOBAL,)
    _kernel32.GlobalUnlock.restype = wintypes.BOOL
    _kernel32.GlobalSize.argtypes = (wintypes.HGLOBAL,)
    _kernel32.GlobalSize.restype = ctypes.c_size_t


def _abrir_portapapeles() -> None:
    ultimo: str | None = None
    for _ in range(INTENTOS_PORTAPAPELES):
        if _user32.OpenClipboard(None):
            return
        ultimo = f"error {ctypes.get_last_error()}"
        time.sleep(ESPERA_PORTAPAPELES_S)
    raise OSError(f"el portapapeles está ocupado por otra aplicación: {ultimo}")


def leer_portapapeles() -> str | None:
    """El texto del portapapeles, o None si está vacío o no contiene texto.

    Solo se conserva el formato texto Unicode: una imagen o unos archivos
    copiados no se pueden guardar y restaurar desde aquí (limitación de la v1,
    documentada en el README).
    """
    _solo_windows()
    _abrir_portapapeles()
    try:
        if not _user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return None
        manejador = _user32.GetClipboardData(CF_UNICODETEXT)
        if not manejador:
            return None
        puntero = _kernel32.GlobalLock(manejador)
        if not puntero:
            return None
        try:
            # Sin pasarse del bloque aunque falte el terminador (lo pone otro programa).
            caracteres = _kernel32.GlobalSize(manejador) // 2
            return ctypes.wstring_at(puntero, caracteres).split("\x00", 1)[0]
        finally:
            _kernel32.GlobalUnlock(manejador)
    finally:
        _user32.CloseClipboard()


def escribir_portapapeles(texto: str) -> None:
    """Deja `texto` en el portapapeles. Reintenta la secuencia entera.

    Con ctypes y la memoria a la vista, a propósito: el bloque se reserva con
    GlobalAlloc, se copia el texto con su terminador, y desde que
    SetClipboardData lo acepta es de Windows y no se toca más. Si lo rechaza,
    se libera aquí. Antes lo hacía pywin32 y un cierre por corrupción de
    montón (0xc0000374) señaló justo a esta llamada (VOZ-63, 16/09/2026).

    Abrirlo puede fallar porque otra aplicación lo tiene; y `SetClipboardData`
    puede fallar con «controlador no válido» aunque se haya abierto bien
    (visto con WhatsApp y el historial del portapapeles de Windows en medio).
    Se repite todo, no solo la apertura, y si al final no hay manera se lanza
    `OSError`: el destino lo convierte en `EntregaFallida` y el texto queda
    en el historial.
    """
    _solo_windows()
    ultimo: str | None = None
    for _ in range(INTENTOS_PORTAPAPELES):
        try:
            _abrir_portapapeles()
        except OSError as e:
            ultimo = str(e)
            continue
        try:
            if not _user32.EmptyClipboard():
                ultimo = f"EmptyClipboard: error {ctypes.get_last_error()}"
                continue
            datos = texto.encode("utf-16-le") + b"\x00\x00"
            bloque = _kernel32.GlobalAlloc(GMEM_MOVEABLE, len(datos))
            if not bloque:
                raise MemoryError("GlobalAlloc devolvió NULL")
            puntero = _kernel32.GlobalLock(bloque)
            if not puntero:
                _kernel32.GlobalFree(bloque)
                ultimo = f"GlobalLock: error {ctypes.get_last_error()}"
                continue
            ctypes.memmove(puntero, datos, len(datos))
            _kernel32.GlobalUnlock(bloque)
            if _user32.SetClipboardData(CF_UNICODETEXT, bloque):
                return  # el bloque es ya del sistema: ni GlobalFree ni nada
            ultimo = f"SetClipboardData: error {ctypes.get_last_error()}"
            _kernel32.GlobalFree(bloque)
        finally:
            _user32.CloseClipboard()
        time.sleep(ESPERA_PORTAPAPELES_S)
    raise OSError(f"no se pudo escribir en el portapapeles: {ultimo}")


cerrojo_portapapeles = threading.RLock()
"""Lo toma quien usa el portapapeles en varios pasos (pegar: guardar lo que había,
escribir, Ctrl+V, restaurar) y quien solo escribe en él. Sin esto, «Volver a
transcribir» podía copiar su texto justo a mitad de un pegado: acababa pegado
en la aplicación en lugar del dictado, o borrado por la restauración (VOZ-80)."""


ESPERA_PEGADO_S = 0.5
"""Lo que `copiar` espera a que termine un pegado. Un pegado normal dura ~170 ms."""


def copiar(texto: str) -> None:
    """Deja `texto` en el portapapeles sin pisar un pegado que esté a medias.

    Espera como mucho `ESPERA_PEGADO_S`. Se llama desde el hilo de la bandeja,
    y si el pegado se ha colgado (un dueño del portapapeles que no responde),
    esperarlo sin límite congelaba la bandeja entera, «Salir» incluido.

    Raises:
        OSError: hay un pegado en curso que no termina, o el portapapeles no se
            deja escribir.
    """
    if not cerrojo_portapapeles.acquire(timeout=ESPERA_PEGADO_S):
        raise OSError("se está pegando un dictado; prueba en un momento")
    try:
        escribir_portapapeles(texto)
    finally:
        cerrojo_portapapeles.release()
