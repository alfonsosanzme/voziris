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
import sys
import time
from collections.abc import Sequence
from ctypes import wintypes

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


def modificadores_pulsados() -> list[int]:
    """Modificadores que el usuario tiene físicamente apretados ahora mismo."""
    _solo_windows()
    return [vk for vk in MODIFICADORES_FISICOS if _user32.GetAsyncKeyState(vk) & 0x8000]


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
    _solo_windows()
    if pid <= 0:
        return None
    proceso = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
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
