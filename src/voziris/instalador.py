"""Instalación por usuario: que Voziris aparezca en Inicio como un programa más.

El requisito F1 (portable, sin instalador, sin registro, sin administrador)
sigue siendo el modo por defecto: la carpeta se copia y funciona. Esto es un
paso OPCIONAL para quien quiera escribir «voziris» en la búsqueda de Windows
y verlo instalado, con su entrada en «Aplicaciones instaladas» y su botón de
desinstalar. Se hace sin permisos de administrador:

  - La carpeta va a `%LOCALAPPDATA%\\Programs\\Voziris`, que es donde Windows
    espera los programas de un solo usuario.
  - El acceso directo va al menú Inicio del usuario: es lo que hace que la
    búsqueda lo encuentre.
  - La entrada de «Aplicaciones instaladas» es una clave en HKCU, la rama del
    usuario, no la del sistema. Es la única escritura en el registro de todo
    el proyecto, y solo ocurre si se pide instalar.

`config.toml`, `modelos/` e `historial/` viajan con la copia si estaban al
lado del ejecutable: no hay que volver a descargar 640 MB ni reconfigurar.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from voziris import __version__

log = logging.getLogger(__name__)

NOMBRE = "Voziris"
EDITOR = "Alfonso Juan Sanz López"
CLAVE_DESINSTALAR = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Voziris"
ACCESO_MENU = "Voziris.lnk"
ACCESO_STARTUP = "Voziris.lnk"
DATOS_DEL_USUARIO = ("config.toml", "modelos", "historial")
"""Lo que se lleva a la instalación si está junto al ejecutable de origen."""


def carpeta_instalacion() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "Programs" / NOMBRE


def carpeta_menu_inicio() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def carpeta_startup() -> Path:
    return carpeta_menu_inicio() / "Startup"


def ejecutable_actual() -> Path:
    return Path(sys.executable).resolve()


def esta_instalado(ejecutable: Path | None = None, destino: Path | None = None) -> bool:
    """True si el ejecutable que corre vive dentro de la carpeta de instalación."""
    exe = (ejecutable or ejecutable_actual()).resolve()
    carpeta = (destino or carpeta_instalacion()).resolve()
    return exe.parent == carpeta


def instalar(
    origen: Path | None = None,
    destino: Path | None = None,
    *,
    menu_inicio: Path | None = None,
    startup: Path | None = None,
    raiz_registro: int | None = None,
    clave_registro: str = CLAVE_DESINSTALAR,
    version: str = __version__,
) -> Path:
    """Copia el programa, crea el acceso directo de Inicio y la entrada de desinstalación.

    Args:
        origen: carpeta del ejecutable congelado (`voziris.exe` + `_internal/`).
            Por defecto, la del ejecutable que corre.
        destino: por defecto, `%LOCALAPPDATA%\\Programs\\Voziris`.
        menu_inicio, startup, raiz_registro, clave_registro: para los tests.

    Returns:
        La carpeta de instalación.

    Raises:
        FileNotFoundError: `origen` no tiene `voziris.exe` y `_internal/`.
        OSError: no se pudo copiar (disco lleno, permisos).
    """
    origen = (origen or ejecutable_actual().parent).resolve()
    destino = (destino or carpeta_instalacion()).resolve()
    exe_origen = origen / "voziris.exe"
    if not exe_origen.is_file() or not (origen / "_internal").is_dir():
        raise FileNotFoundError(
            f"En {origen} no hay un Voziris congelado (voziris.exe y _internal). "
            "La instalación se hace desde la carpeta descomprimida del ZIP."
        )
    if destino != origen:
        log.info("copiando %s → %s", origen, destino)
        destino.mkdir(parents=True, exist_ok=True)
        shutil.copy2(exe_origen, destino / "voziris.exe")
        _copiar_arbol(origen / "_internal", destino / "_internal", reemplazar=True)
        for nombre in DATOS_DEL_USUARIO:
            fuente = origen / nombre
            if fuente.is_file():
                if not (destino / nombre).exists():
                    shutil.copy2(fuente, destino / nombre)
            elif fuente.is_dir():
                _copiar_arbol(fuente, destino / nombre, reemplazar=False)
        # El .cmd de instalar no tiene sentido dentro de la instalación.
    exe = destino / "voziris.exe"

    from voziris.ui.bandeja import crear_acceso_directo

    menu = menu_inicio or carpeta_menu_inicio()
    menu.mkdir(parents=True, exist_ok=True)
    crear_acceso_directo(menu / ACCESO_MENU, str(exe), "", str(destino))
    log.info("acceso directo en el menú Inicio: %s", menu / ACCESO_MENU)

    arranque = (startup or carpeta_startup()) / ACCESO_STARTUP
    if arranque.exists():
        # Estaba apuntando a la copia portable: que arranque la instalada.
        crear_acceso_directo(arranque, str(exe), "", str(destino))
        log.info("acceso directo de Inicio de sesión actualizado: %s", arranque)

    _registrar(destino, exe, version, raiz_registro, clave_registro)
    return destino


def desinstalar(
    destino: Path | None = None,
    *,
    menu_inicio: Path | None = None,
    startup: Path | None = None,
    raiz_registro: int | None = None,
    clave_registro: str = CLAVE_DESINSTALAR,
    borrar_carpeta: bool = True,
    aplazado: bool | None = None,
) -> None:
    """Quita accesos directos, la entrada del registro y la carpeta.

    Si se llama desde el propio ejecutable instalado, la carpeta no se puede
    borrar mientras corre: se deja programado un borrado a los dos segundos
    (`aplazado`), que es lo que hacen los desinstaladores de verdad.
    """
    destino = (destino or carpeta_instalacion()).resolve()
    for acceso in (
        (menu_inicio or carpeta_menu_inicio()) / ACCESO_MENU,
        (startup or carpeta_startup()) / ACCESO_STARTUP,
    ):
        if acceso.exists():
            acceso.unlink()
            log.info("borrado %s", acceso)
    _desregistrar(raiz_registro, clave_registro)
    if not borrar_carpeta or not destino.exists():
        return
    if aplazado is None:
        aplazado = esta_instalado(destino=destino)
    if aplazado:
        _borrar_aplazado(destino)
    else:
        shutil.rmtree(destino, ignore_errors=True)


# --- detalles ----------------------------------------------------------------------


def _copiar_arbol(fuente: Path, destino: Path, *, reemplazar: bool) -> None:
    """Copia `fuente` en `destino`. Con `reemplazar`, lo que hubiera desaparece."""
    if reemplazar and destino.exists():
        shutil.rmtree(destino)
    shutil.copytree(fuente, destino, dirs_exist_ok=True)


def _tamano_kb(carpeta: Path) -> int:
    return sum(p.stat().st_size for p in carpeta.rglob("*") if p.is_file()) // 1024


def _registrar(
    destino: Path, exe: Path, version: str, raiz: int | None, clave: str
) -> None:
    """La entrada de «Aplicaciones instaladas». Solo en la rama del usuario."""
    if sys.platform != "win32":
        return
    import winreg

    raiz = winreg.HKEY_CURRENT_USER if raiz is None else raiz
    with winreg.CreateKeyEx(raiz, clave, 0, winreg.KEY_WRITE) as k:
        for nombre, valor in (
            ("DisplayName", NOMBRE),
            ("DisplayVersion", version),
            ("Publisher", EDITOR),
            ("InstallLocation", str(destino)),
            ("DisplayIcon", str(exe)),
            ("UninstallString", f'"{exe}" --desinstalar'),
            ("QuietUninstallString", f'"{exe}" --desinstalar'),
            ("InstallDate", time.strftime("%Y%m%d")),
            ("URLInfoAbout", "https://github.com/alfonsosanzme/voziris"),
        ):
            winreg.SetValueEx(k, nombre, 0, winreg.REG_SZ, valor)
        winreg.SetValueEx(k, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "NoRepair", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "EstimatedSize", 0, winreg.REG_DWORD, _tamano_kb(destino))
    log.info("registrado en Aplicaciones instaladas (%s)", clave)


def _desregistrar(raiz: int | None, clave: str) -> None:
    if sys.platform != "win32":
        return
    import winreg

    raiz = winreg.HKEY_CURRENT_USER if raiz is None else raiz
    try:
        winreg.DeleteKey(raiz, clave)
        log.info("quitado de Aplicaciones instaladas")
    except FileNotFoundError:
        pass


def _borrar_aplazado(carpeta: Path) -> None:
    """Un cmd aparte que espera a que este proceso muera y borra la carpeta."""
    orden = f'ping 127.0.0.1 -n 3 >nul & rmdir /s /q "{carpeta}"'
    subprocess.Popen(  # noqa: S603 — orden fija, sin entrada del usuario
        ["cmd", "/c", orden],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
        | getattr(subprocess, "DETACHED_PROCESS", 0),
        close_fds=True,
    )
    log.info("borrado de %s programado al salir", carpeta)
