"""F2 — icono de bandeja y arranque con Windows.

La aplicación no tiene ventana principal. El icono de bandeja es toda su
presencia: estado, menú y salida.

Estados del icono (dibujados en `iconos.py`), distinguibles también en
escala de grises:

    reposo        contorno
    grabando      relleno
    procesando    relleno con marca
    error         contorno con marca

Hilos: pystray exige el hilo principal en macOS y Linux, pero en Windows
documenta que `run()` desde otro hilo es seguro. Tkinter sí exige el hilo
principal, así que la bandeja corre en su propio hilo (`mostrar_en_hilo()`)
y Tk se queda con el principal. Los callbacks del menú llegan en el hilo de
la bandeja: solo deben encolar trabajo.

Arranque con Windows: acceso directo en la carpeta Startup del usuario. NO
una clave del registro en HKLM ni una tarea programada — eso pide permisos
de administrador y rompe F1.

Issue: VOZ-03.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from voziris import __version__
from voziris.tipos import EntradaHistorial
from voziris.ui import iconos

log = logging.getLogger(__name__)

NOMBRE_ACCESO_DIRECTO = "Voziris.lnk"
MOTORES = (("local", "Local (sin conexión)"), ("api", "API"), ("auto", "Automático"))
AL_DICTAR = (
    ("silenciar", "Silenciar lo que suene"),
    ("atenuar", "Bajarle el volumen"),
    ("nada", "No tocar nada"),
)
ULTIMOS_EN_MENU = 10
ESPERA_REENTREGA_S = 3
ETIQUETA_REENTREGAR = f"Volver a entregar (en {ESPERA_REENTREGA_S} s, haz clic donde lo quieras)"
LARGO_RESUMEN = 42


def texto_acerca_de(version: str = __version__) -> str:
    """Lo que muestra «Acerca de». La atribución a NVIDIA es obligatoria (CC-BY-4.0)."""
    return (
        f"Voziris {version}\n"
        "Dictado por voz para Windows. Código bajo licencia MIT.\n"
        "\n"
        "Motor local: NVIDIA Parakeet TDT 0.6B v3, © NVIDIA Corporation,\n"
        "distribuido bajo Creative Commons Attribution 4.0 (CC-BY-4.0).\n"
        "https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3\n"
        "\n"
        "Separación de hablantes: NVIDIA TitaNet small, © NVIDIA Corporation\n"
        "(CC-BY-4.0), y pyannote segmentation 3.0 (MIT), con sherpa-onnx\n"
        "(Apache-2.0). Grabaciones decodificadas con PyAV y FFmpeg (GPL)."
    )


@dataclass
class AccionesBandeja:
    """Lo que el menú puede pedirle a la aplicación. Todo se llama en el hilo de la bandeja."""

    dictar_ahora: Callable[[], None]
    dictar_markdown: Callable[[], None]
    alternar_corte: Callable[[], None]
    abrir_ajustes: Callable[[], None]
    cambiar_motor: Callable[[str], None]
    reintentar: Callable[[int], None]
    borrar_entrada: Callable[[int], None]
    salir: Callable[[], None]
    acerca_de: Callable[[], None] | None = None
    """Si falta, «Acerca de» se muestra como notificación."""
    ver_registro: Callable[[], None] | None = None
    """Abre voziris.log. Si falta, la entrada no aparece."""
    diagnostico: Callable[[], None] | None = None
    """Genera y abre diagnostico.txt. Si falta, la entrada no aparece."""
    instalar: Callable[[], None] | None = None
    """Instala en el equipo (menú Inicio). Solo cuando se corre portable."""
    transcribir: Callable[[], None] | None = None
    """Transcribir una grabación a Markdown (VOZ-72). Si falta, la entrada no aparece."""
    cambiar_al_dictar: Callable[[str], None] | None = None
    """Qué hacer con el audio de otras apps mientras se dicta (VOZ-75)."""
    al_dictar_actual: Callable[[], str] = lambda: "silenciar"
    devolver_sonido: Callable[[], None] | None = None
    """Fuerza a devolver el audio que quedara bajado. Solo se ve si hay algo bajado."""
    hay_sonido_bajado: Callable[[], bool] = lambda: False
    copiar: Callable[[int], None] | None = None
    """Copia el texto de una entrada del historial al portapapeles (VOZ-74)."""
    pendientes: Callable[[], list[Any]] | None = None
    """Dictados cuyo audio está en disco sin transcribir (`Pendiente`). VOZ-74."""
    recuperar: Callable[[Any], None] | None = None
    borrar_pendiente: Callable[[Any], None] | None = None


def resumen(entrada: EntradaHistorial, largo: int = LARGO_RESUMEN) -> str:
    """«19:42 · Llamar a la gestoría por lo del…» para el submenú de últimos dictados."""
    texto = " ".join(entrada.texto.split())
    if len(texto) > largo:
        texto = texto[: largo - 1].rstrip() + "…"
    marca = "" if entrada.entregado else " ⚠"
    return f"{entrada.momento:%H:%M} · {texto}{marca}"


class Bandeja:
    def __init__(
        self,
        acciones: AccionesBandeja,
        ultimas: Callable[[], list[EntradaHistorial]] = lambda: [],
        motor_actual: Callable[[], str] = lambda: "auto",
        corte_activo: Callable[[], bool] = lambda: True,
        version: str = __version__,
    ) -> None:
        self._acciones = acciones
        self._ultimas = ultimas
        self._motor_actual = motor_actual
        self._corte_activo = corte_activo
        self._version = version
        self._estado = "reposo"
        self._icono: Any = None
        self._hilo: threading.Thread | None = None
        # pystray no es seguro entre hilos: cambiar el icono desde el hilo de
        # trabajo mientras el suyo atiende un WM_DISPLAYCHANGE (que también
        # recrea el icono) es una carrera sobre el mismo HICON. Un cerrojo
        # para todo lo que toque el icono, y sin cambios redundantes (VOZ-63).
        self._cerrojo = threading.Lock()
        self._imagenes = {e: iconos.imagen(e) for e in iconos.ESTADOS}

    # --- menú ------------------------------------------------------------------

    def construir_menu(self) -> Any:
        """El menú completo. Público para poder inspeccionarlo en los tests."""
        import pystray

        Item = pystray.MenuItem

        # pystray cuenta los parámetros de cada callable (0, 1 o 2) y rechaza
        # las lambdas con argumento por defecto: de ahí las fábricas.
        def elegir(clave: str) -> Callable[[], None]:
            return lambda: self._acciones.cambiar_motor(clave)

        def marcado(clave: str) -> Callable[[Any], bool]:
            return lambda _item: self._motor_actual() == clave

        return pystray.Menu(
            Item("Dictar ahora", lambda: self._acciones.dictar_ahora(), default=True),
            Item("Dictar al Markdown", lambda: self._acciones.dictar_markdown()),
            *self._item_transcribir(),
            Item(
                "Cortar al callar (modo clavar)",
                lambda: self._acciones.alternar_corte(),
                checked=lambda _item: self._corte_activo(),
            ),
            *self._item_al_dictar(),
            Item("Últimos dictados", pystray.Menu(self._items_ultimos)),
            Item(
                lambda _item: f"Dictados sin transcribir ({len(self._pendientes())})",
                pystray.Menu(self._items_pendientes),
                visible=lambda _item: bool(self._pendientes()),
            ),
            Item(
                "Motor",
                pystray.Menu(
                    *(
                        Item(etiqueta, elegir(clave), checked=marcado(clave), radio=True)
                        for clave, etiqueta in MOTORES
                    )
                ),
            ),
            Item("Ajustes…", lambda: self._acciones.abrir_ajustes()),
            *self._items_opcionales(),
            Item("Acerca de Voziris", lambda: self._acerca_de()),
            pystray.Menu.SEPARATOR,
            Item("Salir", lambda: self._acciones.salir()),
        )

    def _item_transcribir(self) -> list[Any]:
        import pystray

        if self._acciones.transcribir is None:
            return []
        transcribir = self._acciones.transcribir
        return [pystray.MenuItem("Transcribir grabación o vídeo…", lambda: transcribir())]

    def _item_al_dictar(self) -> list[Any]:
        """Submenú «Mientras dicto»: qué pasa con la música. Solo si se cablea."""
        import pystray

        Item = pystray.MenuItem
        if self._acciones.cambiar_al_dictar is None:
            return []
        cambiar = self._acciones.cambiar_al_dictar

        def elegir(clave: str) -> Callable[[], None]:
            return lambda: cambiar(clave)

        def marcado(clave: str) -> Callable[[Any], bool]:
            return lambda _item: self._acciones.al_dictar_actual() == clave

        entradas = [Item("Mientras dicto", pystray.Menu(*(
            Item(etiqueta, elegir(clave), checked=marcado(clave), radio=True)
            for clave, etiqueta in AL_DICTAR
        )))]
        if self._acciones.devolver_sonido is not None:
            devolver = self._acciones.devolver_sonido
            # Salvavidas a la vista: si algo quedó callado, se deshace desde aquí.
            entradas.append(Item(
                "Devolver el sonido",
                lambda: devolver(),
                visible=lambda _item: self._acciones.hay_sonido_bajado(),
            ))
        return entradas

    def _items_opcionales(self) -> list[Any]:
        """Registro, diagnóstico e instalar: solo si la aplicación los cablea."""
        import pystray

        Item = pystray.MenuItem
        elementos: list[Any] = []
        a = self._acciones
        if a.ver_registro is not None:
            ver = a.ver_registro
            elementos.append(Item("Ver registro (voziris.log)", lambda: ver()))
        if a.diagnostico is not None:
            diag = a.diagnostico
            elementos.append(Item("Guardar diagnóstico…", lambda: diag()))
        if a.instalar is not None:
            inst = a.instalar
            elementos.append(Item("Instalar en este equipo…", lambda: inst()))
        return elementos

    def _items_ultimos(self) -> list[Any]:
        import pystray

        Item = pystray.MenuItem
        entradas = self._ultimas()[:ULTIMOS_EN_MENU]
        if not entradas:
            return [Item("(todavía no hay dictados)", None, enabled=False)]
        def reintentar(indice: int) -> Callable[[], None]:
            return lambda: self._acciones.reintentar(indice)

        def borrar(indice: int) -> Callable[[], None]:
            return lambda: self._acciones.borrar_entrada(indice)

        def copiar(indice: int) -> list[Any]:
            if self._acciones.copiar is None:
                return []
            accion = self._acciones.copiar
            return [Item("Copiar al portapapeles", lambda: accion(indice), default=True)]

        return [
            Item(
                resumen(entrada),
                pystray.Menu(
                    *copiar(entrada.indice),
                    Item(ETIQUETA_REENTREGAR, reintentar(entrada.indice)),
                    Item("Borrar del historial", borrar(entrada.indice)),
                ),
            )
            for entrada in entradas
        ]

    def _pendientes(self) -> list[Any]:
        if self._acciones.pendientes is None:
            return []
        try:
            return self._acciones.pendientes()
        except Exception:  # noqa: BLE001 — el menú no puede romperse por esto
            log.exception("no se pudieron listar los dictados pendientes")
            return []

    def _items_pendientes(self) -> list[Any]:
        import pystray

        Item = pystray.MenuItem

        def recuperar(pendiente: Any) -> Callable[[], None]:
            return lambda: self._acciones.recuperar and self._acciones.recuperar(pendiente)

        def borrar(pendiente: Any) -> Callable[[], None]:
            return lambda: (
                self._acciones.borrar_pendiente and self._acciones.borrar_pendiente(pendiente)
            )

        pendientes = self._pendientes()[:ULTIMOS_EN_MENU]
        if not pendientes:
            return [Item("(ninguno)", None, enabled=False)]
        return [
            Item(
                pendiente.etiqueta(),
                pystray.Menu(
                    Item("Transcribir y copiar", recuperar(pendiente), default=True),
                    Item("Borrar el audio", borrar(pendiente)),
                ),
            )
            for pendiente in pendientes
        ]

    def _acerca_de(self) -> None:
        if self._acciones.acerca_de is not None:
            self._acciones.acerca_de()
        else:
            self.avisar(texto_acerca_de(self._version), titulo="Acerca de Voziris")

    # --- icono ------------------------------------------------------------------

    def _crear_icono(self) -> Any:
        import pystray

        self._icono = pystray.Icon(
            "voziris",
            icon=self._imagenes[self._estado],
            title=self._titulo(),
            menu=self.construir_menu(),
        )
        return self._icono

    def mostrar(self) -> None:
        """Crea el icono y entra en el bucle de eventos. Bloquea hasta `cerrar()`.

        Menú: Dictar ahora · Dictar al Markdown · Transcribir una grabación… ·
        Cortar al callar · Últimos dictados (submenú) · Motor (local/API/auto) ·
        Ajustes · Acerca de · Salir.

        «Acerca de» incluye la atribución a NVIDIA por CC-BY-4.0. Es una
        obligación de licencia, no un adorno.
        """
        self._crear_icono().run()

    def mostrar_en_hilo(self) -> threading.Thread:
        """Como `mostrar()`, en un hilo propio. Devuelve cuando el icono existe."""
        listo = threading.Event()

        def preparar(icono: Any) -> None:
            icono.visible = True
            listo.set()

        def correr() -> None:
            self._crear_icono().run(setup=preparar)

        self._hilo = threading.Thread(target=correr, name="voziris-bandeja", daemon=True)
        self._hilo.start()
        listo.wait(timeout=5)
        return self._hilo

    def cerrar(self) -> None:
        icono, self._icono = self._icono, None
        if icono is not None:
            try:
                icono.stop()
            except Exception:  # noqa: BLE001 — al cerrar, nada que hacer con el error
                log.debug("al parar la bandeja", exc_info=True)
        if self._hilo is not None:
            self._hilo.join(timeout=3)
            self._hilo = None

    @property
    def estado_actual(self) -> str:
        return self._estado

    def estado(self, nombre: str) -> None:
        """Cambia el icono: "reposo" | "grabando" | "procesando" | "error"."""
        if nombre not in iconos.ESTADOS:
            raise ValueError(f"estado desconocido: {nombre!r}")
        with self._cerrojo:
            if nombre == self._estado and self._icono is not None:
                return
            self._estado = nombre
            if self._icono is not None:
                self._icono.icon = self._imagenes[nombre]
                self._icono.title = self._titulo()

    def _titulo(self) -> str:
        etiquetas = {
            "reposo": "Voziris",
            "grabando": "Voziris — grabando",
            "procesando": "Voziris — procesando",
            "error": "Voziris — error, mira el menú",
        }
        return etiquetas[self._estado]

    def actualizar_menu(self) -> None:
        """Tras cambiar el historial o el motor desde fuera del menú."""
        with self._cerrojo:
            if self._icono is not None:
                self._icono.update_menu()

    def avisar(self, texto: str, titulo: str = "Voziris") -> None:
        """Notificación del sistema. Solo para lo que el usuario debe saber.

        Un aviso por cada dictado sería insufrible: esto es para «no hay red,
        he transcrito en local» o «no pude escribir en entrada.md».
        """
        if self._icono is None:
            log.info("aviso (sin bandeja): %s", texto)
            return
        try:
            with self._cerrojo:
                self._icono.notify(texto, titulo)
        except Exception:  # noqa: BLE001 — una notificación fallida no es un fallo
            log.warning("no se pudo mostrar la notificación: %s", texto)

    # --- arranque con Windows -----------------------------------------------------

    @staticmethod
    def carpeta_startup() -> Path:
        """La carpeta Startup del usuario actual. No pide permisos."""
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"

    @staticmethod
    def destino_arranque() -> tuple[str, str, str]:
        """(ejecutable, argumentos, carpeta de trabajo) que debe lanzar el acceso directo."""
        from voziris.config import carpeta_base

        if getattr(sys, "frozen", False):
            return sys.executable, "", str(Path(sys.executable).parent)
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        ejecutable = pythonw if pythonw.exists() else Path(sys.executable)
        return str(ejecutable), "-m voziris", str(carpeta_base())

    @classmethod
    def arranque_activo(cls, carpeta: Path | None = None) -> bool:
        return ((carpeta or cls.carpeta_startup()) / NOMBRE_ACCESO_DIRECTO).exists()

    @classmethod
    def configurar_arranque(cls, activo: bool, carpeta: Path | None = None) -> bool:
        """Crea o borra el acceso directo en la carpeta Startup del usuario.

        Devuelve True si cambió algo. Sin tocar HKLM ni tareas programadas:
        pediría administrador y rompería la portabilidad (F1).
        """
        carpeta = carpeta or cls.carpeta_startup()
        acceso = carpeta / NOMBRE_ACCESO_DIRECTO
        if not activo:
            if acceso.exists():
                acceso.unlink()
                return True
            return False
        if acceso.exists():
            return False
        ejecutable, argumentos, trabajo = cls.destino_arranque()
        carpeta.mkdir(parents=True, exist_ok=True)
        crear_acceso_directo(acceso, ejecutable, argumentos, trabajo)
        return True


def crear_acceso_directo(acceso: Path, ejecutable: str, argumentos: str, trabajo: str) -> None:
    """Un .lnk con WScript.Shell, que es lo que Windows entiende en Startup e Inicio."""
    import pythoncom
    from win32com.client import Dispatch

    pythoncom.CoInitialize()  # el hilo de la bandeja o el de ajustes no lo tienen
    try:
        shell = Dispatch("WScript.Shell")
        atajo = shell.CreateShortcut(str(acceso))
        atajo.TargetPath = ejecutable
        atajo.Arguments = argumentos
        atajo.WorkingDirectory = trabajo
        atajo.Description = "Voziris — dictado por voz"
        if getattr(sys, "frozen", False):
            atajo.IconLocation = ejecutable  # el .exe lleva el icono dentro
        else:
            from voziris.config import carpeta_recursos

            ico = carpeta_recursos() / "assets" / "voziris.ico"
            if ico.exists():
                atajo.IconLocation = str(ico)
        atajo.Save()
    finally:
        pythoncom.CoUninitialize()


def leer_acceso_directo(acceso: Path) -> tuple[str, str, str]:
    """(ejecutable, argumentos, carpeta de trabajo) de un .lnk. Para los tests y los ajustes."""
    import pythoncom
    from win32com.client import Dispatch

    pythoncom.CoInitialize()
    try:
        atajo = Dispatch("WScript.Shell").CreateShortcut(str(acceso))
        return str(atajo.TargetPath), str(atajo.Arguments), str(atajo.WorkingDirectory)
    finally:
        pythoncom.CoUninitialize()


_crear_acceso_directo = crear_acceso_directo  # nombre anterior
