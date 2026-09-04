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


def evento_tecla(vk: int, arriba: bool = False) -> INPUT:
    """Pulsación (o liberación) de una tecla virtual."""
    ev = INPUT(type=INPUT_KEYBOARD)
    ev.union.ki = KEYBDINPUT(
        wVk=vk, wScan=0, dwFlags=KEYEVENTF_KEYUP if arriba else 0, time=0,
        dwExtraInfo=MARCA_VOZIRIS,
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


# --- portapapeles -------------------------------------------------------------

INTENTOS_PORTAPAPELES = 10
ESPERA_PORTAPAPELES_S = 0.02
"""Otra aplicación puede tener el portapapeles abierto unos milisegundos."""


def _abrir_portapapeles() -> None:
    import win32clipboard

    ultimo: Exception | None = None
    for _ in range(INTENTOS_PORTAPAPELES):
        try:
            win32clipboard.OpenClipboard()
            return
        except Exception as e:  # noqa: BLE001 — pywintypes.error, sin stubs
            ultimo = e
            time.sleep(ESPERA_PORTAPAPELES_S)
    raise OSError(f"el portapapeles está ocupado por otra aplicación: {ultimo}")


def leer_portapapeles() -> str | None:
    """El texto del portapapeles, o None si está vacío o no contiene texto.

    Solo se conserva el formato texto Unicode: una imagen o unos archivos
    copiados no se pueden guardar y restaurar desde aquí (limitación de la v1,
    documentada en el README).
    """
    _solo_windows()
    import win32clipboard
    import win32con

    _abrir_portapapeles()
    try:
        if not win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
            return None
        return str(win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT))
    finally:
        win32clipboard.CloseClipboard()


def escribir_portapapeles(texto: str) -> None:
    _solo_windows()
    import win32clipboard
    import win32con

    _abrir_portapapeles()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, texto)
    finally:
        win32clipboard.CloseClipboard()
