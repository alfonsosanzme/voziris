"""Bajar lo que esté sonando mientras se dicta, y devolverlo como estaba (VOZ-75).

Dictar con música de fondo es peor por dos motivos: el micrófono se come lo
que sale de los altavoces, y quien dicta se oye mal a sí mismo. Windows tiene
una preferencia para esto (Panel de control → Sonido → Comunicaciones) pero
solo se dispara con llamadas reconocidas como tales, no con una app de
dictado.

Tres modos, en `[audio].al_dictar`:

    nada         no se toca el audio de nadie
    atenuar      lo que suene baja al VOLUMEN_ATENUADO
    silenciar    lo que suene se calla (por defecto)

Solo se toca **lo que está sonando de verdad** (sesiones en estado activo), y
nunca: la propia Voziris, los sonidos del sistema, lo que el usuario ya tenía
callado, ni ninguna aplicación que esté grabando por el micrófono — esa es una
llamada de Teams, Zoom o Meet, y callarle el audio sería justo lo contrario de
lo que se quiere. Tampoco se toca el volumen maestro, que es del usuario.

Dos capas, a propósito:

  - `ControlDeSesiones`: hablar con Windows. `SesionesCoreAudio` es la de
    verdad (COM con ctypes); los tests usan una falsa.
  - `Mezclador`: la política. Qué había antes, cuándo restaurar, qué hacer si
    el usuario cambia el volumen por su cuenta, y el archivo de rescate.

Dos decisiones que se notan al usarlo:

  - El silenciado espera `RETRASO_S`. Un roce del atajo que se descarta por
    corto no abre un agujero en la música, porque la orden caduca antes.
  - Mientras dura la grabación se vuelve a mirar cada `REVISION_S`: si
    empieza a sonar algo a media frase, también se calla.

El archivo de rescate existe porque un mute no se deshace solo: Windows lo
recuerda por aplicación incluso entre reinicios. Si Voziris muriera con la
música callada (tiene historial de cierres nativos), el usuario se quedaría
sin sonido sin saber por qué. Antes de tocar nada se escribe en disco qué
había; al restaurar del todo se borra. Si al arrancar sigue ahí, es que la vez
anterior no se restauró, y se deshace entonces.

Todo el trabajo va en un hilo propio, que es además el único que habla con
COM: llamar a COM desde el hilo del hook de teclado retrasaría el tono de
inicio, y soltar punteros desde otro hilo es un uso después de liberar.

Issue: VOZ-75.
"""

from __future__ import annotations

import contextlib
import ctypes
import json
import logging
import os
import queue
import sys
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from voziris import winapi

log = logging.getLogger(__name__)

MODOS = ("nada", "atenuar", "silenciar")
VOLUMEN_ATENUADO = 0.15
"""En modo «atenuar», a cuánto baja lo que suene: se oye, pero no tapa la voz."""
TOLERANCIA_VOLUMEN = 0.02
"""Si al restaurar el volumen no es el que dejamos puesto, lo movió el usuario: no se pisa."""
RETRASO_S = 0.25
"""Lo que se espera antes de callar nada. Igual que la pulsación mínima de un dictado."""
REVISION_S = 1.5
"""Cada cuánto se mira si ha empezado a sonar algo nuevo mientras se graba."""
REINTENTOS_RESTAURAR = 3
ESPERA_REINTENTO_S = 0.2
ESPERA_CIERRE_S = 3.0


@dataclass
class Sesion:
    """Una aplicación reproduciendo audio. `clave` la identifica entre llamadas."""

    clave: str
    nombre: str
    pid: int
    mute: bool
    volumen: float
    activa: bool = True
    """False: la aplicación tiene audio abierto pero ahora mismo no suena."""


class ControlDeSesiones(Protocol):
    """Lo que el mezclador necesita del sistema de audio."""

    def sesiones(self) -> list[Sesion]:
        """Las sesiones de salida de otros procesos, con su estado actual."""
        ...

    def aplicar(
        self, clave: str, mute: bool, volumen: float | None, nombre: str | None = None
    ) -> bool:
        """Cambia una sesión. `volumen=None` deja el volumen como esté.

        `nombre` es el ejecutable, como respaldo cuando la clave ya no existe
        porque la aplicación se cerró y se volvió a abrir. False si no se
        encontró la sesión.
        """
        ...

    def cerrar(self) -> None: ...


@dataclass
class Cambiada:
    """Lo que se le hizo a una sesión, para poder deshacerlo."""

    clave: str
    nombre: str
    mute_antes: bool
    volumen_antes: float
    volumen_puesto: float | None
    """Lo que dejamos puesto, para saber si después lo tocó el usuario."""

    def a_dict(self) -> dict[str, object]:
        return {
            "clave": self.clave, "nombre": self.nombre, "mute_antes": self.mute_antes,
            "volumen_antes": self.volumen_antes, "volumen_puesto": self.volumen_puesto,
        }

    @staticmethod
    def de_dict(datos: dict[str, object]) -> Cambiada:
        return Cambiada(
            clave=str(datos["clave"]),
            nombre=str(datos.get("nombre", "")),
            mute_antes=bool(datos["mute_antes"]),
            volumen_antes=float(datos["volumen_antes"]),  # type: ignore[arg-type]
            volumen_puesto=(
                float(datos["volumen_puesto"])  # type: ignore[arg-type]
                if datos.get("volumen_puesto") is not None
                else None
            ),
        )


_CERRAR = object()
"""La orden de cierre: el hilo restaura, suelta COM y sale."""


class Mezclador:
    """Baja y devuelve el audio en su propio hilo. Las órdenes no bloquean a quien las da."""

    def __init__(
        self,
        modo: str = "silenciar",
        control: ControlDeSesiones | None = None,
        rescate: Path | None = None,
        volumen_atenuado: float = VOLUMEN_ATENUADO,
        retraso_s: float = RETRASO_S,
        revision_s: float = REVISION_S,
    ) -> None:
        self.modo = modo if modo in MODOS else "silenciar"
        self._control = control
        self._rescate = Path(rescate) if rescate is not None else None
        self._volumen_atenuado = volumen_atenuado
        self._retraso_s = retraso_s
        self._revision_s = revision_s
        self._cambiadas: list[Cambiada] = []
        self._cola: queue.Queue[object] = queue.Queue()
        self._hilo: threading.Thread | None = None
        self._lock = threading.Lock()
        self.ultimo_error: str | None = None

    # --- vida --------------------------------------------------------------------------

    def arrancar(self) -> None:
        """Lanza el hilo y deshace lo que quedara colgado de una ejecución anterior."""
        if self._hilo is not None:
            return
        self._hilo = threading.Thread(target=self._trabajar, name="voziris-mezclador", daemon=True)
        self._hilo.start()
        self._cola.put(False)  # una restauración de entrada: aplica el rescate si lo hay

    def cerrar(self) -> None:
        """Pide restaurar y cerrar. Espera un poco, pero no toca COM desde aquí."""
        if self._hilo is None:
            return
        self._cola.put(_CERRAR)
        self._hilo.join(timeout=ESPERA_CIERRE_S)
        if self._hilo.is_alive():
            # No se le quitan los punteros a un hilo que sigue usándolos: es
            # demonio, morirá con el proceso, y el rescate en disco cubre lo
            # que quedara bajado.
            log.warning("el mezclador no terminó a tiempo: el audio se devuelve al arrancar")
        self._hilo = None

    # --- órdenes (desde cualquier hilo, sin bloquear) ---------------------------------

    def silenciar(self) -> None:
        if self.modo != "nada":
            self._cola.put(True)

    def restaurar(self) -> None:
        self._cola.put(False)

    def devolver_el_sonido(self) -> None:
        """Desde la bandeja, si algo quedó callado. Fuerza la restauración."""
        self._cola.put(False)

    @property
    def silenciado(self) -> bool:
        with self._lock:
            return bool(self._cambiadas)

    def hay_sonido_bajado(self) -> bool:
        """Para la bandeja: ¿hay algo callado por nuestra culpa, ahora o de antes?"""
        return self.silenciado or bool(self._rescate is not None and self._rescate.is_file())

    # --- hilo ---------------------------------------------------------------------------

    def _trabajar(self) -> None:
        deseado = False
        desde = 0.0
        while True:
            try:
                orden = self._cola.get(timeout=self._espera(deseado, desde))
            except queue.Empty:
                orden = None  # venció el plazo: toca actuar
            try:
                if orden is _CERRAR:
                    self._restaurar_con_reintentos()
                    self._cerrar_control()
                    return
                if orden is not None:
                    nuevo = bool(orden)
                    if nuevo != deseado:
                        deseado, desde = nuevo, time.monotonic()
                    if not deseado:
                        self._restaurar_con_reintentos()
                    continue
                if deseado and not self.silenciado:
                    self._silenciar_ahora()
                elif deseado:
                    self._silenciar_las_nuevas()
            except Exception as e:  # noqa: BLE001 — el audio de otros no puede tumbar el dictado
                self.ultimo_error = str(e)
                log.exception("el mezclador falló")

    def _espera(self, deseado: bool, desde: float) -> float | None:
        """Cuánto esperar en la cola. None es hasta que llegue una orden."""
        if not deseado:
            return None
        if not self.silenciado:
            return max(0.0, self._retraso_s - (time.monotonic() - desde))
        return self._revision_s

    def _cerrar_control(self) -> None:
        if self._control is not None:
            with contextlib.suppress(Exception):
                self._control.cerrar()

    # --- bajar --------------------------------------------------------------------------

    def _silenciar_ahora(self) -> None:
        if self._control is None or self.modo == "nada":
            return
        cambiadas = self._elegir(self._control.sesiones(), ya_tocadas=set())
        if not cambiadas:
            return
        if not self._escribir_rescate(cambiadas):
            # Sin poder anotar qué había, no se toca nada: un mute que no se
            # sabe deshacer es peor que dictar con música de fondo.
            log.warning("no se baja el audio: no se pudo escribir el rescate")
            return
        self._aplicar_bajada(cambiadas)
        with self._lock:
            self._cambiadas = list(cambiadas)
        log.info("audio: %d sesión(es) en modo %s", len(cambiadas), self.modo)

    def _silenciar_las_nuevas(self) -> None:
        """Algo empezó a sonar a media grabación: también se calla."""
        if self._control is None:
            return
        with self._lock:
            conocidas = {c.clave for c in self._cambiadas}
        nuevas = self._elegir(self._control.sesiones(), ya_tocadas=conocidas)
        if not nuevas:
            return
        with self._lock:
            todas = self._cambiadas + nuevas
        if not self._escribir_rescate(todas):
            return
        self._aplicar_bajada(nuevas)
        with self._lock:
            self._cambiadas = todas
        log.info("audio: %d sesión(es) más, que empezaron a sonar", len(nuevas))

    def _elegir(self, sesiones: list[Sesion], ya_tocadas: set[str]) -> list[Cambiada]:
        """Las que hay que bajar: solo lo que suena, y solo lo que es nuestro tocar."""
        objetivo_mute = self.modo == "silenciar"
        elegidas: list[Cambiada] = []
        for sesion in sesiones:
            if sesion.clave in ya_tocadas or not sesion.activa:
                continue
            if sesion.mute:
                continue  # el usuario ya la tenía callada: es decisión suya
            volumen = None if objetivo_mute else self._volumen_atenuado
            if volumen is not None and sesion.volumen <= volumen:
                continue  # ya sonaba más bajo que la atenuación
            elegidas.append(
                Cambiada(sesion.clave, sesion.nombre, sesion.mute, sesion.volumen, volumen)
            )
        return elegidas

    def _aplicar_bajada(self, cambiadas: list[Cambiada]) -> None:
        assert self._control is not None
        objetivo_mute = self.modo == "silenciar"
        for cambiada in cambiadas:
            # En modo «atenuar» el mute no se toca nunca: solo el volumen.
            mute = True if objetivo_mute else cambiada.mute_antes
            self._control.aplicar(cambiada.clave, mute, cambiada.volumen_puesto, cambiada.nombre)

    # --- devolver -----------------------------------------------------------------------

    def _restaurar_con_reintentos(self) -> None:
        for intento in range(REINTENTOS_RESTAURAR):
            if self._restaurar_ahora():
                return
            time.sleep(ESPERA_REINTENTO_S * (intento + 1))
        log.error("quedó audio sin devolver; se reintentará al arrancar (mira el rescate)")

    def _restaurar_ahora(self) -> bool:
        """True si no quedó nada pendiente. Lo que no se pudo devolver se conserva."""
        with self._lock:
            pendientes = list(self._cambiadas)
        if not pendientes:
            pendientes = self._leer_rescate()
        if not pendientes:
            self._borrar_rescate()
            return True
        if self._control is None:
            return True
        estado = {s.clave: s for s in self._control.sesiones()}
        sin_devolver = [c for c in pendientes if not self._devolver(c, estado)]
        with self._lock:
            self._cambiadas = sin_devolver
        if sin_devolver:
            self._escribir_rescate(sin_devolver, fusionar=False)
            log.warning("%d sesión(es) de audio sin devolver todavía", len(sin_devolver))
            return False
        self._borrar_rescate()
        log.info("audio: %d sesión(es) devueltas a su estado", len(pendientes))
        return True

    def _devolver(self, cambiada: Cambiada, estado: dict[str, Sesion]) -> bool:
        assert self._control is not None
        ahora = estado.get(cambiada.clave)
        # El volumen solo se devuelve si fuimos nosotros quienes lo bajamos
        # (modo atenuar). En modo silenciar no se tocó: lo que tenga es suyo.
        volumen: float | None = None
        if cambiada.volumen_puesto is not None:
            volumen = cambiada.volumen_antes
            if (
                ahora is not None
                and abs(ahora.volumen - cambiada.volumen_puesto) > TOLERANCIA_VOLUMEN
            ):
                volumen = None  # lo movió el usuario mientras tanto: manda él
        if self._control.aplicar(cambiada.clave, cambiada.mute_antes, volumen, cambiada.nombre):
            return True
        # No se encontró. Si tampoco hay ninguna sesión de ese programa, es que
        # se cerró y su mute se fue con él: se da por devuelta, porque si no el
        # rescate se quedaría ahí para siempre.
        return not any(s.nombre == cambiada.nombre for s in estado.values())

    # --- rescate ----------------------------------------------------------------------

    def _escribir_rescate(self, cambiadas: list[Cambiada], fusionar: bool = True) -> bool:
        """Anota en disco qué había ANTES de tocar nada. False si no se pudo."""
        if self._rescate is None:
            return True
        entradas = {c.clave: c for c in (self._leer_rescate() if fusionar else [])}
        entradas.update({c.clave: c for c in cambiadas})
        temporal = self._rescate.with_suffix(".tmp")
        try:
            self._rescate.parent.mkdir(parents=True, exist_ok=True)
            datos = json.dumps([c.a_dict() for c in entradas.values()], ensure_ascii=False)
            with open(temporal, "w", encoding="utf-8", newline="\n") as archivo:
                archivo.write(datos)
                archivo.flush()
                os.fsync(archivo.fileno())
            os.replace(temporal, self._rescate)  # atómico: o está el viejo o el nuevo
        except OSError as e:
            log.warning("no se pudo escribir el rescate del audio: %s", e)
            with contextlib.suppress(OSError):
                temporal.unlink(missing_ok=True)
            return False
        return True

    def _leer_rescate(self) -> list[Cambiada]:
        if self._rescate is None or not self._rescate.is_file():
            return []
        try:
            datos = json.loads(self._rescate.read_text(encoding="utf-8"))
            cambiadas = [Cambiada.de_dict(d) for d in datos]
        except (OSError, ValueError, KeyError, TypeError) as e:
            # No se borra: se aparta. Dentro está la única pista de qué tocamos.
            log.warning("rescate del audio ilegible (%s): se aparta como .corrupto", e)
            with contextlib.suppress(OSError):
                self._rescate.replace(self._rescate.with_suffix(".corrupto"))
            return []
        if cambiadas:
            log.info(
                "la vez anterior el audio quedó bajado (%d sesiones): se devuelve a su sitio",
                len(cambiadas),
            )
        return cambiadas

    def _borrar_rescate(self) -> None:
        if self._rescate is not None:
            with contextlib.suppress(OSError):
                self._rescate.unlink(missing_ok=True)


# --- Windows: las sesiones de audio de verdad ------------------------------------------
#
# Core Audio se habla por COM. Sin comtypes ni pycaw: el proyecto ya hace todo
# lo de Win32 con ctypes y una dependencia más engorda el paquete portable.
# Hacen falta tres cosas por interfaz: su IID, el ÍNDICE de cada método en la
# tabla de funciones (contando los tres de IUnknown — QueryInterface=0,
# AddRef=1, Release=2) y la firma. Los índices salen del orden de declaración
# en mmdeviceapi.h y audiopolicy.h, y están comprobados en el equipo real.

CLSID_ENUMERADOR = "{BCDE0395-E52F-467C-8E3D-C4579291692E}"
IID_ENUMERADOR = "{A95664D2-9614-4F35-A746-DE8DB63617E6}"
IID_GESTOR_SESIONES = "{77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F}"
IID_CONTROL_SESION2 = "{BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D}"
IID_VOLUMEN_SIMPLE = "{87CE5498-68D6-44E5-9215-6DA47EF883D8}"

SALIDA, ENTRADA = 0, 1
MULTIMEDIA = 1
DISPOSITIVO_ACTIVO = 0x1
CLSCTX_ALL = 23
COINIT_APARTMENTTHREADED = 2
RPC_E_CHANGED_MODE = -2147417850
"""0x80010106: el hilo ya estaba en el otro apartamento. No es un fallo, pero no hay que cerrar."""
S_FALSE = 1
ESTADO_ACTIVA = 1
"""Solo se toca lo que está sonando: los estados 0 (inactiva) y 2 (expirada) se dejan en paz."""
CACHE_GRABANDO_S = 2.0
"""Quién está en una llamada se mira como mucho cada tanto: cuesta una cadena COM entera."""

ENUM_ENDPOINTS = 3                    # IMMDeviceEnumerator::EnumAudioEndpoints
ENUM_PREDETERMINADO = 4               # IMMDeviceEnumerator::GetDefaultAudioEndpoint
COLECCION_CUANTOS = 3                 # IMMDeviceCollection::GetCount
COLECCION_ELEMENTO = 4                # IMMDeviceCollection::Item
DISPOSITIVO_ACTIVAR = 3               # IMMDevice::Activate
GESTOR_ENUMERAR_SESIONES = 5          # IAudioSessionManager2::GetSessionEnumerator
LISTA_CUANTAS = 3                     # IAudioSessionEnumerator::GetCount
LISTA_SESION = 4                      # IAudioSessionEnumerator::GetSession
CONTROL_ESTADO = 3                    # IAudioSessionControl::GetState
CONTROL2_IDENTIFICADOR = 13           # IAudioSessionControl2::GetSessionInstanceIdentifier
CONTROL2_PID = 14                     # IAudioSessionControl2::GetProcessId
CONTROL2_ES_DEL_SISTEMA = 15          # IAudioSessionControl2::IsSystemSoundsSession
VOLUMEN_PONER_NIVEL = 3               # ISimpleAudioVolume::SetMasterVolume
VOLUMEN_LEER_NIVEL = 4                # ISimpleAudioVolume::GetMasterVolume
VOLUMEN_PONER_MUTE = 5                # ISimpleAudioVolume::SetMute
VOLUMEN_LEER_MUTE = 6                 # ISimpleAudioVolume::GetMute


class _Guid(ctypes.Structure):
    _fields_ = (
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    )

    @classmethod
    def de_texto(cls, texto: str) -> _Guid:
        guid = cls()
        if _ole32.CLSIDFromString(ctypes.c_wchar_p(texto), ctypes.byref(guid)) < 0:
            raise OSError(f"GUID inválido: {texto}")
        return guid


def _cargar_ole32() -> Any:
    """ole32 una sola vez y con las firmas declaradas, como se hace en winapi.py."""
    if sys.platform != "win32":
        return None
    ole = ctypes.WinDLL("ole32", use_last_error=True)
    ole.CoInitializeEx.argtypes = (ctypes.c_void_p, wintypes.DWORD)
    ole.CoInitializeEx.restype = ctypes.c_long
    ole.CoUninitialize.argtypes = ()
    ole.CoUninitialize.restype = None
    ole.CoCreateInstance.argtypes = (
        ctypes.POINTER(_Guid), ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(_Guid), ctypes.POINTER(ctypes.c_void_p),
    )
    ole.CoCreateInstance.restype = ctypes.c_long
    ole.CoTaskMemFree.argtypes = (ctypes.c_void_p,)
    ole.CoTaskMemFree.restype = None
    ole.CLSIDFromString.argtypes = (ctypes.c_wchar_p, ctypes.POINTER(_Guid))
    ole.CLSIDFromString.restype = ctypes.c_long
    return ole


_ole32 = _cargar_ole32()


def _metodo(puntero: ctypes.c_void_p, indice: int, *tipos: object) -> Any:
    """El método `indice` de la vtable, listo para llamar."""
    tabla = ctypes.cast(puntero, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))
    prototipo = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *tipos)  # type: ignore[arg-type]
    return prototipo(tabla[0][indice])


def _llamar(
    puntero: ctypes.c_void_p, indice: int, tipos: tuple[object, ...], *argumentos: object
) -> int:
    """Invoca un método de la vtable. Solo un HRESULT NEGATIVO es error.

    Los positivos también son éxito: `GetProcessId` devuelve
    AUDCLNT_S_NO_SINGLE_PROCESS (0x0889000D) cuando la sesión abarca varios
    procesos, e `IsSystemSoundsSession` contesta S_FALSE (1) para decir «no».
    """
    resultado = int(_metodo(puntero, indice, *tipos)(puntero, *argumentos))
    if resultado < 0:
        raise OSError(f"COM devolvió 0x{resultado & 0xFFFFFFFF:08X} en el método {indice}")
    return resultado


def _soltar(puntero: ctypes.c_void_p | None) -> None:
    """IUnknown::Release. Sin esto se filtran objetos COM en cada dictado."""
    if not puntero:
        return
    tabla = ctypes.cast(puntero, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))
    ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(tabla[0][2])(puntero)


def _consultar(puntero: ctypes.c_void_p, iid: str) -> ctypes.c_void_p:
    """IUnknown::QueryInterface."""
    guid = _Guid.de_texto(iid)
    salida = ctypes.c_void_p()
    _llamar(
        puntero, 0, (ctypes.POINTER(_Guid), ctypes.POINTER(ctypes.c_void_p)),
        ctypes.byref(guid), ctypes.byref(salida),
    )
    return salida


def _texto_com(puntero: ctypes.c_void_p, indice: int) -> str | None:
    """Un LPWSTR devuelto por COM, ya liberado con CoTaskMemFree."""
    texto = ctypes.c_wchar_p()
    try:
        _llamar(puntero, indice, (ctypes.POINTER(ctypes.c_wchar_p),), ctypes.byref(texto))
    except OSError:
        return None
    valor = texto.value
    with contextlib.suppress(Exception):
        _ole32.CoTaskMemFree(texto)
    return valor


class SesionesCoreAudio:
    """Las sesiones de audio de salida de este equipo, por COM.

    Todo el COM ocurre en el hilo que llama, y el mezclador llama siempre desde
    el suyo: los punteros no cruzan hilos, que es lo que obligaría a marshalar.
    Los punteros de volumen de la última enumeración se guardan para que
    `aplicar()` no tenga que volver a enumerar; se sueltan en la siguiente
    llamada a `sesiones()` y en `cerrar()`.
    """

    def __init__(self, caduca_grabando_s: float = CACHE_GRABANDO_S) -> None:
        self._hr_inicio: int | None = None
        self._volumenes: dict[str, ctypes.c_void_p] = {}
        self._nombres: dict[str, str] = {}
        self._propio = os.getpid()
        self._enumerador: ctypes.c_void_p | None = None
        self._grabando: set[int] = set()
        self._grabando_hasta = 0.0
        self._caduca_grabando_s = caduca_grabando_s

    def _iniciar_com(self) -> None:
        if self._hr_inicio is not None:
            return
        # STA: es el apartamento en el que sounddevice y pywin32 dejan a los
        # hilos que los importan, y pedir MTA allí devuelve RPC_E_CHANGED_MODE.
        # Eso no impide trabajar — el hilo ya está inicializado — pero entonces
        # no hay que llamar a CoUninitialize.
        self._hr_inicio = int(_ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED))
        if self._hr_inicio < 0 and self._hr_inicio != RPC_E_CHANGED_MODE:
            raise OSError(f"CoInitializeEx: 0x{self._hr_inicio & 0xFFFFFFFF:08X}")

    def sesiones(self) -> list[Sesion]:
        self._iniciar_com()
        self._soltar_volumenes()
        grabando = self._pids_grabando()
        salida: list[Sesion] = []
        for dispositivo in self._salidas_activas():
            try:
                salida.extend(self._sesiones_de(dispositivo, grabando, salida))
            except OSError as e:
                log.debug("no se pudieron leer las sesiones de una salida: %s", e)
            finally:
                _soltar(dispositivo)
        return salida

    def _salidas_activas(self) -> list[ctypes.c_void_p]:
        """Todas las salidas activas, no solo la predeterminada (altavoces y auriculares)."""
        enumerador = self._crear_enumerador()  # cacheado: no se suelta aquí
        coleccion = ctypes.c_void_p()
        dispositivos: list[ctypes.c_void_p] = []
        try:
            _llamar(
                enumerador, ENUM_ENDPOINTS,
                (ctypes.c_int, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)),
                SALIDA, DISPOSITIVO_ACTIVO, ctypes.byref(coleccion),
            )
            cuantos = wintypes.UINT()
            _llamar(coleccion, COLECCION_CUANTOS, (ctypes.POINTER(wintypes.UINT),),
                    ctypes.byref(cuantos))
            for i in range(cuantos.value):
                dispositivo = ctypes.c_void_p()
                _llamar(
                    coleccion, COLECCION_ELEMENTO,
                    (wintypes.UINT, ctypes.POINTER(ctypes.c_void_p)),
                    i, ctypes.byref(dispositivo),
                )
                dispositivos.append(dispositivo)
        finally:
            _soltar(coleccion)
        return dispositivos

    def _pids_grabando(self) -> set[int]:
        """Procesos con el micrófono abierto: a una llamada en curso no se le calla nada.

        Se cachea unos segundos: es una cadena COM entera y nadie entra en una
        reunión a mitad de una frase.
        """
        ahora = time.monotonic()
        if ahora < self._grabando_hasta:
            return self._grabando
        dispositivo = gestor = lista = None
        pids: set[int] = set()
        try:
            dispositivo = ctypes.c_void_p()
            _llamar(
                self._crear_enumerador(), ENUM_PREDETERMINADO,
                (ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)),
                ENTRADA, MULTIMEDIA, ctypes.byref(dispositivo),
            )
            gestor, lista = self._lista_de_sesiones(dispositivo)
            cuantas = ctypes.c_int()
            _llamar(lista, LISTA_CUANTAS, (ctypes.POINTER(ctypes.c_int),), ctypes.byref(cuantas))
            for i in range(cuantas.value):
                pid = self._pid_si_graba(lista, i)
                if pid:
                    pids.add(pid)
        except OSError as e:
            log.debug("no se pudo mirar quién está grabando: %s", e)
        finally:
            _soltar(lista)
            _soltar(gestor)
            _soltar(dispositivo)
        pids.discard(self._propio)
        self._grabando = pids
        self._grabando_hasta = ahora + self._caduca_grabando_s
        return pids

    def _pid_si_graba(self, lista: ctypes.c_void_p, indice: int) -> int:
        control = control2 = None
        try:
            control = ctypes.c_void_p()
            _llamar(
                lista, LISTA_SESION, (ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)),
                indice, ctypes.byref(control),
            )
            estado = ctypes.c_int()
            _llamar(control, CONTROL_ESTADO, (ctypes.POINTER(ctypes.c_int),), ctypes.byref(estado))
            if estado.value != ESTADO_ACTIVA:
                return 0
            control2 = _consultar(control, IID_CONTROL_SESION2)
            pid = wintypes.DWORD()
            _llamar(control2, CONTROL2_PID, (ctypes.POINTER(wintypes.DWORD),), ctypes.byref(pid))
            return int(pid.value)
        except OSError:
            return 0
        finally:
            _soltar(control2)
            _soltar(control)

    def _lista_de_sesiones(
        self, dispositivo: ctypes.c_void_p
    ) -> tuple[ctypes.c_void_p, ctypes.c_void_p]:
        gestor = ctypes.c_void_p()
        iid = _Guid.de_texto(IID_GESTOR_SESIONES)
        _llamar(
            dispositivo, DISPOSITIVO_ACTIVAR,
            (ctypes.POINTER(_Guid), wintypes.DWORD, ctypes.c_void_p,
             ctypes.POINTER(ctypes.c_void_p)),
            ctypes.byref(iid), CLSCTX_ALL, None, ctypes.byref(gestor),
        )
        lista = ctypes.c_void_p()
        _llamar(gestor, GESTOR_ENUMERAR_SESIONES, (ctypes.POINTER(ctypes.c_void_p),),
                ctypes.byref(lista))
        return gestor, lista

    def _sesiones_de(
        self, dispositivo: ctypes.c_void_p, grabando: set[int], vistas: list[Sesion]
    ) -> list[Sesion]:
        gestor = lista = None
        ya = {s.clave for s in vistas}
        salida: list[Sesion] = []
        try:
            gestor, lista = self._lista_de_sesiones(dispositivo)
            cuantas = ctypes.c_int()
            _llamar(lista, LISTA_CUANTAS, (ctypes.POINTER(ctypes.c_int),), ctypes.byref(cuantas))
            for i in range(cuantas.value):
                try:
                    sesion = self._leer_sesion(lista, i, grabando)
                except OSError as e:
                    log.debug("sesión de audio %d ilegible: %s", i, e)
                    continue
                if sesion is not None and sesion.clave not in ya:
                    ya.add(sesion.clave)
                    salida.append(sesion)
        finally:
            _soltar(lista)
            _soltar(gestor)
        return salida

    def _leer_sesion(
        self, lista: ctypes.c_void_p, indice: int, grabando: set[int]
    ) -> Sesion | None:
        control = ctypes.c_void_p()
        _llamar(
            lista, LISTA_SESION, (ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)),
            indice, ctypes.byref(control),
        )
        control2 = volumen = None
        try:
            estado = ctypes.c_int()
            _llamar(control, CONTROL_ESTADO, (ctypes.POINTER(ctypes.c_int),), ctypes.byref(estado))
            control2 = _consultar(control, IID_CONTROL_SESION2)
            if _llamar(control2, CONTROL2_ES_DEL_SISTEMA, ()) != S_FALSE:
                return None  # los pitidos de Windows se quedan como están
            pid = wintypes.DWORD()
            _llamar(control2, CONTROL2_PID, (ctypes.POINTER(wintypes.DWORD),), ctypes.byref(pid))
            if pid.value == self._propio:
                return None  # los tonos de Voziris se siguen oyendo
            if int(pid.value) in grabando:
                return None  # está en una llamada: no se le calla el audio
            clave = _texto_com(control2, CONTROL2_IDENTIFICADOR) or f"{pid.value}:{indice}"
            volumen = _consultar(control, IID_VOLUMEN_SIMPLE)
            mute = wintypes.BOOL()
            nivel = ctypes.c_float()
            _llamar(volumen, VOLUMEN_LEER_MUTE, (ctypes.POINTER(wintypes.BOOL),),
                    ctypes.byref(mute))
            _llamar(volumen, VOLUMEN_LEER_NIVEL, (ctypes.POINTER(ctypes.c_float),),
                    ctypes.byref(nivel))
            nombre = winapi.nombre_de_proceso(int(pid.value)) or f"pid {pid.value}"
            self._volumenes[clave] = volumen
            self._nombres[clave] = nombre
            volumen = None  # queda guardado: no soltarlo en el finally
            return Sesion(
                clave, nombre, int(pid.value), bool(mute.value), float(nivel.value),
                activa=estado.value == ESTADO_ACTIVA,
            )
        finally:
            _soltar(volumen)
            _soltar(control2)
            _soltar(control)

    def _crear_enumerador(self) -> ctypes.c_void_p:
        """El enumerador de dispositivos, creado una vez y reutilizado.

        `CoCreateInstance` es lo caro de toda la cadena (cientos de
        milisegundos la primera vez, decenas después). El objeto vale para
        todo el hilo, así que se conserva y solo se suelta al cerrar.
        """
        if self._enumerador is not None:
            return self._enumerador
        self._enumerador = self._nuevo_enumerador()
        return self._enumerador

    def _nuevo_enumerador(self) -> ctypes.c_void_p:
        clsid = _Guid.de_texto(CLSID_ENUMERADOR)
        iid = _Guid.de_texto(IID_ENUMERADOR)
        puntero = ctypes.c_void_p()
        resultado = int(_ole32.CoCreateInstance(
            ctypes.byref(clsid), None, CLSCTX_ALL, ctypes.byref(iid), ctypes.byref(puntero)
        ))
        if resultado < 0:
            raise OSError(f"sin enumerador de audio: 0x{resultado & 0xFFFFFFFF:08X}")
        return puntero

    def aplicar(
        self, clave: str, mute: bool, volumen: float | None, nombre: str | None = None
    ) -> bool:
        puntero = self._volumenes.get(clave)
        if puntero is None and nombre:
            # La aplicación se cerró y se abrió otra vez, así que su
            # identificador es nuevo: se busca por ejecutable. La coincidencia
            # se consume, para que dos sesiones del mismo programa no acaben
            # las dos sobre el mismo puntero.
            for otra, suyo in list(self._nombres.items()):
                if suyo == nombre:
                    puntero = self._volumenes.get(otra)
                    self._nombres.pop(otra, None)
                    break
        if puntero is None:
            return False
        try:
            if volumen is not None:
                _llamar(
                    puntero, VOLUMEN_PONER_NIVEL, (ctypes.c_float, ctypes.c_void_p),
                    ctypes.c_float(max(0.0, min(1.0, volumen))), None,
                )
            _llamar(
                puntero, VOLUMEN_PONER_MUTE, (wintypes.BOOL, ctypes.c_void_p),
                wintypes.BOOL(1 if mute else 0), None,
            )
        except OSError as e:
            log.debug("no se pudo cambiar el volumen de %s: %s", nombre or clave, e)
            return False
        return True

    def _soltar_volumenes(self) -> None:
        for puntero in self._volumenes.values():
            _soltar(puntero)
        self._volumenes.clear()
        self._nombres.clear()

    def cerrar(self) -> None:
        self._soltar_volumenes()
        _soltar(self._enumerador)
        self._enumerador = None
        # Solo se deshace lo que se hizo: con RPC_E_CHANGED_MODE no llegamos a
        # inicializar nada, y llamar a CoUninitialize desequilibraría el hilo.
        if self._hr_inicio is not None and self._hr_inicio >= 0:
            with contextlib.suppress(Exception):
                _ole32.CoUninitialize()
        self._hr_inicio = None


def control_del_sistema() -> ControlDeSesiones | None:
    """El control real en Windows; None en cualquier otro sitio."""
    if sys.platform != "win32" or _ole32 is None:
        return None
    return SesionesCoreAudio()
