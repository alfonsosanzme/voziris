"""A1, A2, H2 — atajos globales.

**H2 es explícito: un atajo por comportamiento, no una doble pulsación.**
Detectar dobles pulsaciones obliga a esperar la ventana de tiempo antes de
empezar a grabar, y eso se come el búfer previo. Cuatro combinaciones:

    mantener  → graba mientras siga pulsada           (A1)
    clavar    → fija el micrófono hasta volver a pulsar (A2)
    markdown  → como «mantener», pero al archivo       (H1)
    cancelar  → descarta el dictado en curso

Implementación: **un solo hook de bajo nivel** (`SetWindowsHookEx` con
`WH_KEYBOARD_LL`) para las cuatro. `RegisterHotKey` no sirve para
«mantener»: no informa de cuándo se suelta la tecla y ni siquiera admite una
combinación de solo modificadores como `ctrl+win`. Se usa únicamente como
sondeo al arrancar, para avisar de que otra aplicación tiene tomada una
combinación.

Tres hilos:

  - **hook**: instala el hook y bombea mensajes. Su callback solo convierte
    la tecla en acciones con el `Detector` y las encola. Un hook lento
    congela el teclado de todo el sistema y Windows lo desinstala.
  - **despachador**: saca acciones de la cola y llama a los callbacks de la
    aplicación. Si un callback tarda, tarda aquí, no en el hook.
  - el que llama a `registrar()` / `liberar()`.

Las pulsaciones que inyecta el propio Voziris (`SendInput` con
`winapi.MARCA_VOZIRIS`) se ignoran: el Ctrl+V del pegado no es un atajo.

El hook de bajo nivel es también la razón del riesgo R2 (falsos positivos de
antivirus) y de R3 (no funciona sobre ventanas elevadas). Ambos van
documentados en el README, no se arreglan.

Issue: VOZ-02.
"""

from __future__ import annotations

import ctypes
import logging
import queue
import threading
import time
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass
from typing import Literal

from voziris import teclas, winapi
from voziris.errores import AtajosNoDisponibles, ConfigInvalida
from voziris.tipos import Modo

log = logging.getLogger(__name__)

WH_KEYBOARD_LL = 13
HC_ACTION = 0
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_QUIT = 0x0012
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN = 0x0001, 0x0002, 0x0004, 0x0008
MOD_NOREPEAT = 0x4000
ERROR_HOTKEY_ALREADY_REGISTERED = 1409

NOMBRES_ATAJOS = ("mantener", "clavar", "markdown", "cancelar")

QUE_DISPARA: dict[str, tuple[Modo, str]] = {
    "mantener": (Modo.MANTENER, "app_activa"),
    "clavar": (Modo.CLAVAR, "app_activa"),
    "markdown": (Modo.MANTENER, "markdown"),
}
"""Modo y destino que inicia cada atajo. «cancelar» no inicia nada."""

_NOMBRE_DE_VK: dict[int, str] = {vk: nombre for nombre, vks in teclas.VK.items() for vk in vks}


# --- detector puro ------------------------------------------------------------


@dataclass(frozen=True)
class Accion:
    tipo: Literal["empezar", "terminar", "cancelar"]
    atajo: str
    duracion_ms: int = 0


class Detector:
    """Convierte teclas abajo/arriba en acciones. Sin Windows: se prueba solo.

    Reglas:
      - Una combinación se completa en la pulsación de su última tecla, con
        exactamente sus modificadores apretados y ninguno más. Si es de solo
        modificadores (`ctrl+win`), además no puede haber otra tecla apretada.
      - «mantener» y «markdown» duran hasta que se suelta cualquiera de sus
        teclas; entonces se emite `terminar` con la duración real.
      - «clavar» emite `empezar` en cada pulsación: el orquestador decide si
        eso arranca o cierra el dictado.
      - «cancelar» solo actúa (y solo se consume) con un dictado en curso.
      - La tecla no modificadora de una combinación se consume, al bajar y al
        subir, para que no llegue a la aplicación. Los modificadores nunca:
        consumirlos descoloca a las aplicaciones.
    """

    def __init__(
        self,
        combinaciones: dict[str, teclas.Combinacion],
        en_curso: Callable[[], bool],
    ) -> None:
        self.combinaciones = combinaciones
        self._en_curso = en_curso
        self._pulsadas: set[str] = set()
        self._otras: set[int] = set()
        self._consumidas: set[str] = set()
        self._activo: str | None = None
        self._inicio = 0.0

    def tecla(self, vk: int, abajo: bool, ahora: float) -> tuple[list[Accion], bool]:
        """Procesa un evento. Devuelve (acciones, consumir_este_evento)."""
        nombre = _NOMBRE_DE_VK.get(vk)
        if nombre is None:
            (self._otras.add if abajo else self._otras.discard)(vk)
            return [], False
        return self._abajo(nombre, ahora) if abajo else self._arriba(nombre, ahora)

    def _abajo(self, nombre: str, ahora: float) -> tuple[list[Accion], bool]:
        if nombre in self._pulsadas:  # autorrepetición de Windows al mantener
            return [], nombre in self._consumidas
        self._pulsadas.add(nombre)
        for atajo, combo in self.combinaciones.items():
            # «cancelar» admite modificadores de más: lo normal es pulsar Esc
            # sin haber soltado todavía el ctrl+win del dictado que se cancela.
            if not self._completa(combo, nombre, exacto=atajo != "cancelar"):
                continue
            consumir = combo.tecla == nombre
            if atajo == "cancelar":
                if not self._en_curso():
                    return [], False
                self._activo = None
                self._marcar(nombre, consumir)
                return [Accion("cancelar", atajo)], consumir
            if atajo == "clavar":
                self._marcar(nombre, consumir)
                return [Accion("empezar", atajo)], consumir
            if self._activo is None:  # mantener / markdown
                self._activo, self._inicio = atajo, ahora
                self._marcar(nombre, consumir)
                return [Accion("empezar", atajo)], consumir
            return [], False
        return [], False

    def _arriba(self, nombre: str, ahora: float) -> tuple[list[Accion], bool]:
        self._pulsadas.discard(nombre)
        consumir = nombre in self._consumidas
        self._consumidas.discard(nombre)
        acciones: list[Accion] = []
        if self._activo is not None and nombre in self.combinaciones[self._activo].teclas:
            atajo, self._activo = self._activo, None
            acciones.append(Accion("terminar", atajo, int((ahora - self._inicio) * 1000)))
        return acciones, consumir

    def _completa(self, combo: teclas.Combinacion, recien: str, exacto: bool = True) -> bool:
        if recien not in combo.teclas:
            return False
        modificadores = {t for t in self._pulsadas if t in teclas.MODIFICADORES}
        if exacto and modificadores != combo.modificadores:
            return False
        if not exacto and not combo.modificadores <= modificadores:
            return False
        if combo.tecla is None:
            return len(self._pulsadas) == len(modificadores) and not self._otras
        return recien == combo.tecla

    def _marcar(self, nombre: str, consumir: bool) -> None:
        if consumir:
            self._consumidas.add(nombre)

    def soltar_todo(self) -> None:
        """Al reinstalar el hook o cambiar de atajos: ninguna tecla se da por pulsada."""
        self._pulsadas.clear()
        self._otras.clear()
        self._consumidas.clear()
        self._activo = None


# --- hook de Windows ----------------------------------------------------------


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = (
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    )


if winapi.ES_WINDOWS:
    _HOOKPROC = ctypes.WINFUNCTYPE(
        ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
    )
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _user32.SetWindowsHookExW.argtypes = (
        ctypes.c_int, _HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD,
    )
    _user32.SetWindowsHookExW.restype = wintypes.HHOOK
    _user32.UnhookWindowsHookEx.argtypes = (wintypes.HHOOK,)
    _user32.UnhookWindowsHookEx.restype = wintypes.BOOL
    _user32.CallNextHookEx.argtypes = (
        wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM,
    )
    _user32.CallNextHookEx.restype = ctypes.c_ssize_t
    _user32.GetMessageW.argtypes = (
        ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT,
    )
    _user32.GetMessageW.restype = wintypes.BOOL
    _user32.TranslateMessage.argtypes = (ctypes.POINTER(wintypes.MSG),)
    _user32.DispatchMessageW.argtypes = (ctypes.POINTER(wintypes.MSG),)
    _user32.PostThreadMessageW.argtypes = (
        wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
    )
    _user32.PostThreadMessageW.restype = wintypes.BOOL
    _user32.RegisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT)
    _user32.RegisterHotKey.restype = wintypes.BOOL
    _user32.UnregisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int)
    _user32.UnregisterHotKey.restype = wintypes.BOOL
    _kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
    _kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    _kernel32.GetCurrentThreadId.argtypes = ()
    _kernel32.GetCurrentThreadId.restype = wintypes.DWORD


class Atajos:
    """Registra las combinaciones y llama de vuelta.

    Los callbacks se ejecutan en el hilo despachador, nunca en el del hook:
    pueden tardar lo que quieran sin bloquear el teclado del sistema. Aun así,
    conviene que solo encolen trabajo.

    Args:
        al_empezar: (modo, destino) cuando se activa mantener, clavar o markdown.
        al_terminar: (duración en ms) cuando se suelta mantener o markdown.
        al_cancelar: al pulsar cancelar con un dictado en curso.
        en_curso: dice si hay un dictado en marcha. Decide si «cancelar» actúa
            y se consume, o pasa a la aplicación como una tecla normal (C-3).
    """

    def __init__(
        self,
        al_empezar: Callable[[Modo, str], None],
        al_terminar: Callable[[int], None],
        al_cancelar: Callable[[], None],
        en_curso: Callable[[], bool] = lambda: False,
    ) -> None:
        self._al_empezar = al_empezar
        self._al_terminar = al_terminar
        self._al_cancelar = al_cancelar
        self._detector = Detector({}, en_curso)
        self._cola: queue.Queue[Accion | None] = queue.Queue()
        self._hilo_hook: threading.Thread | None = None
        self._hilo_despacho: threading.Thread | None = None
        self._id_hilo_hook = 0
        self._hook: int | None = None
        self._proc: object = None  # referencia viva al callback de ctypes
        self._listo = threading.Event()
        self._error_hook: str | None = None
        self._lock = threading.Lock()

    # --- registro ------------------------------------------------------------

    def registrar(self, combinaciones: dict[str, str]) -> list[str]:
        """Registra (o cambia en caliente) las cuatro combinaciones del TOML.

        Args:
            combinaciones: {"mantener": "ctrl+win", "clavar": "ctrl+shift+space", ...}

        Returns:
            Avisos: combinaciones que otra aplicación ya tiene registradas
            (sondeo con `RegisterHotKey`). Siguen funcionando para Voziris,
            porque el hook ve las teclas antes que nadie, pero la otra
            aplicación también reaccionará. El aviso dice CUÁL.

        Raises:
            ConfigInvalida: una combinación no se puede analizar.
            AtajosNoDisponibles: el hook de teclado no se pudo instalar.
        """
        combos: dict[str, teclas.Combinacion] = {}
        for nombre in NOMBRES_ATAJOS:
            texto = combinaciones.get(nombre, "")
            try:
                combos[nombre] = teclas.analizar(texto)
            except ValueError as e:
                raise ConfigInvalida(f"[atajos] {nombre} = {texto!r}: {e}") from e

        with self._lock:
            self._detector.combinaciones = combos
            self._detector.soltar_todo()
        if self._hilo_hook is None:
            self._arrancar()
        return self._sondear_conflictos(combos)

    def _arrancar(self) -> None:
        if not winapi.ES_WINDOWS:
            raise AtajosNoDisponibles("los atajos globales solo existen en Windows")
        self._listo.clear()
        self._error_hook = None
        self._hilo_despacho = threading.Thread(
            target=self._despachar, name="voziris-atajos-despacho", daemon=True
        )
        self._hilo_despacho.start()
        self._hilo_hook = threading.Thread(
            target=self._bombear, name="voziris-teclado", daemon=True
        )
        self._hilo_hook.start()
        self._listo.wait(timeout=5)
        if self._error_hook:
            self.liberar()
            raise AtajosNoDisponibles(self._error_hook)

    def liberar(self) -> None:
        """Suelta el hook y las combinaciones. Obligatorio al salir."""
        hilo, self._hilo_hook = self._hilo_hook, None
        if hilo is not None and winapi.ES_WINDOWS and self._id_hilo_hook:
            _user32.PostThreadMessageW(self._id_hilo_hook, WM_QUIT, 0, 0)
            hilo.join(timeout=2)
        despacho, self._hilo_despacho = self._hilo_despacho, None
        if despacho is not None:
            self._cola.put(None)
            despacho.join(timeout=2)
        self._id_hilo_hook = 0

    @property
    def activo(self) -> bool:
        return self._hilo_hook is not None and self._hook is not None

    # --- hilo del hook --------------------------------------------------------

    def _bombear(self) -> None:
        self._id_hilo_hook = _kernel32.GetCurrentThreadId()
        proc = _HOOKPROC(self._al_recibir_tecla)
        self._proc = proc  # si el recolector se lo lleva, Windows llama a basura
        hook = _user32.SetWindowsHookExW(WH_KEYBOARD_LL, proc, _kernel32.GetModuleHandleW(None), 0)
        if not hook:
            self._error_hook = (
                f"No se pudo instalar el hook de teclado (error {ctypes.get_last_error()})"
            )
            self._listo.set()
            return
        self._hook = int(hook)
        self._listo.set()
        msg = wintypes.MSG()
        try:
            while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                _user32.TranslateMessage(ctypes.byref(msg))
                _user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            _user32.UnhookWindowsHookEx(hook)
            self._hook = None

    def _al_recibir_tecla(self, codigo: int, wparam: int, lparam: int) -> int:
        """El callback del hook. Cuanto menos haga, mejor."""
        try:
            if codigo == HC_ACTION:
                datos = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if datos.dwExtraInfo != winapi.MARCA_VOZIRIS:
                    abajo = wparam in (WM_KEYDOWN, WM_SYSKEYDOWN)
                    with self._lock:
                        acciones, consumir = self._detector.tecla(
                            int(datos.vkCode), abajo, time.monotonic()
                        )
                    for accion in acciones:
                        self._cola.put(accion)
                    if consumir:
                        return 1
        except Exception:  # noqa: BLE001 — una excepción aquí tumba el hook
            log.exception("fallo en el hook de teclado")
        return int(_user32.CallNextHookEx(None, codigo, wparam, lparam))

    # --- hilo despachador ------------------------------------------------------

    def _despachar(self) -> None:
        while True:
            accion = self._cola.get()
            if accion is None:
                return
            try:
                if accion.tipo == "empezar":
                    modo, destino = QUE_DISPARA[accion.atajo]
                    self._al_empezar(modo, destino)
                elif accion.tipo == "terminar":
                    self._al_terminar(accion.duracion_ms)
                else:
                    self._al_cancelar()
            except Exception:  # noqa: BLE001 — un callback roto no para los atajos
                log.exception("el callback de «%s» falló", accion.atajo)

    # --- sondeo de conflictos ---------------------------------------------------

    @staticmethod
    def _sondear_conflictos(combos: dict[str, teclas.Combinacion]) -> list[str]:
        """Pregunta a Windows si otra aplicación tiene registrada cada combinación.

        Solo para las que llevan una tecla no modificadora: `RegisterHotKey` no
        admite otras, y «cancelar» (Esc a secas) no se registra nunca.
        """
        if not winapi.ES_WINDOWS:
            return []
        avisos: list[str] = []
        mods = {"alt": MOD_ALT, "ctrl": MOD_CONTROL, "shift": MOD_SHIFT, "win": MOD_WIN}
        for indice, (nombre, combo) in enumerate(combos.items(), start=1):
            if combo.tecla is None or nombre == "cancelar":
                continue
            modificadores = sum(mods[m] for m in combo.modificadores) | MOD_NOREPEAT
            vk = next(iter(teclas.VK[combo.tecla]))
            if _user32.RegisterHotKey(None, indice, modificadores, vk):
                _user32.UnregisterHotKey(None, indice)
            elif ctypes.get_last_error() == ERROR_HOTKEY_ALREADY_REGISTERED:
                avisos.append(
                    f"«{combo.texto}» ({nombre}) ya está en uso por otra aplicación: "
                    "las dos reaccionarán. Cámbialo en los ajustes si molesta"
                )
        return avisos
