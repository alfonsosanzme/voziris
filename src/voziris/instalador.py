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
Lo que ya esté en la instalación no se pisa: al actualizar, manda lo instalado.

Con la instalación va también el menú contextual del Explorador: botón
derecho sobre un .m4a (o .mp3, .wav…) → «Transcribir con Voziris» → «Una sola
voz» / «Varios hablantes» (VOZ-72). También es una clave de HKCU, bajo
`Software\\Classes\\SystemFileAssociations`, y también se puede pedir sin
instalar (`voziris.exe --menu-contextual`) para la copia portable.

Desde VOZ-81 la instalación no da nada por bueno sin comprobarlo. Un
compañero instaló una carpeta a la que el antivirus le había quitado una DLL,
y el programa arrancaba y solo fallaba al transcribir. Ahora:

  1. Se comprueba el origen contra el manifiesto del paquete: si falta un
     archivo, no se toca nada y se dice cuál.
  2. Se copia a `Voziris.nuevo`, junto al destino, y se comprueba esa copia,
     también arrancándola (`voziris.exe --comprobar`).
  3. Solo entonces se cambia la instalación anterior por la nueva. Si algo
     falla a medias, la anterior se queda como estaba.
"""

from __future__ import annotations

import contextlib
import errno
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path

from voziris import __version__, integridad

log = logging.getLogger(__name__)


class InstalacionFallida(Exception):
    """No se instaló. El mensaje es para el usuario; la instalación anterior sigue intacta."""

    def __init__(self, mensaje: str, *, bloqueado: bool = False) -> None:
        super().__init__(mensaje)
        self.bloqueado = bloqueado
        """Windows no deja ejecutar la copia (Control inteligente de aplicaciones)."""



NOMBRE = "Voziris"
EDITOR = "Alfonso Juan Sanz López"
CLAVE_DESINSTALAR = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Voziris"
ACCESO_MENU = "Voziris.lnk"
ACCESO_STARTUP = "Voziris.lnk"
DATOS_DEL_USUARIO = ("config.toml", "modelos", "historial")
"""Lo que se lleva a la instalación si está junto al ejecutable de origen."""
BASE_MENU_CONTEXTUAL = r"Software\Classes\SystemFileAssociations"
NOMBRE_MENU_CONTEXTUAL = "Voziris"
VERBOS_MENU_CONTEXTUAL: tuple[tuple[str, str, str], ...] = (
    ("01uno", "Una sola voz", "1"),
    ("02varios", "Varios hablantes", "auto"),
)
"""(subclave, etiqueta, valor de --hablantes). Windows ordena las subclaves por nombre."""
PROGRAMA = ("voziris.exe", "_internal")
"""Lo que se cambia al reinstalar. Lo demás de la carpeta es del usuario."""
SUFIJO_NUEVO = ".nuevo"
SUFIJO_VIEJO = ".viejo"
MARGEN_DISCO = 50 * 2**20
"""Lo que se pide libre además de lo que ocupa el programa."""
REINTENTOS = 8
ESPERA_REINTENTO_S = 0.5
"""El antivirus abre cada DLL recién copiada para analizarla y la retiene un momento, y el
indexador deja a veces `_internal` con el acceso denegado unos segundos (VOZ-73). La espera
crece en cada intento: en total, hasta 14 s antes de darse por vencido."""
_ERRORES_PASAJEROS = frozenset({5, 32, 33})
"""Acceso denegado, archivo en uso, archivo bloqueado: suele ser el antivirus. Se reintenta."""


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
    base_menu: str = BASE_MENU_CONTEXTUAL,
    version: str = __version__,
    comprobar_copia: Callable[[Path], None] | None = None,
    antes_de_sustituir: Callable[[], None] | None = None,
    al_progresar: Callable[[str, float | None], None] | None = None,
    pausar_volcados: Callable[[], AbstractContextManager[object]] | None = None,
) -> Path:
    """Copia el programa, crea el acceso directo de Inicio y la entrada de desinstalación.

    Args:
        origen: carpeta del ejecutable congelado (`voziris.exe` + `_internal/`).
            Por defecto, la del ejecutable que corre.
        destino: por defecto, `%LOCALAPPDATA%\\Programs\\Voziris`.
        menu_inicio, startup, raiz_registro, clave_registro: para los tests.
        comprobar_copia: recibe el voziris.exe copiado, antes de sustituir la
            instalación anterior, y lanza `InstalacionFallida` si no sirve. En
            el programa es `comprobar_arrancando`; los tests, con un .exe de
            mentira, no lo pasan.
        antes_de_sustituir: se llama una vez comprobada la copia, justo antes
            de cambiar el programa. Es donde se cierra la Voziris abierta: si
            la instalación no llega hasta aquí, el usuario no se queda sin ella.
        al_progresar: (mensaje, fracción) para la ventana de progreso. Puede
            lanzar para cancelar, pero solo se le llama antes de tocar nada.
        pausar_volcados: apaga faulthandler mientras se crean los accesos
            directos (ver `integridad.autoprueba`).

    Returns:
        La carpeta de instalación.

    Raises:
        FileNotFoundError: `origen` no tiene `voziris.exe` y `_internal/`.
        InstalacionFallida: falta algo en el origen, no hay sitio, la copia
            falló o no arranca. La instalación anterior queda como estaba.
        OSError: al crear los accesos o el registro, ya con el programa copiado.
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
        log.info("instalando %s → %s", origen, destino)
        _instalar_programa(origen, destino, comprobar_copia, al_progresar, antes_de_sustituir)
        for nombre in DATOS_DEL_USUARIO:
            fuente = origen / nombre
            try:
                if fuente.is_file():
                    if not (destino / nombre).exists():
                        _copiar_archivo(fuente, destino / nombre)
                elif fuente.is_dir():
                    _copiar_arbol(fuente, destino / nombre)
            except OSError as e:
                # El programa ya está instalado y funciona: sin esto solo hay que
                # volver a configurar o a descargar el modelo. No es motivo para parar.
                log.warning("no se pudo llevar %s a la instalación: %s", nombre, e)
        # El .cmd de instalar no tiene sentido dentro de la instalación.
    elif antes_de_sustituir is not None:
        antes_de_sustituir()  # se reinstala sobre sí misma: también hay que cerrarla
    exe = destino / "voziris.exe"

    from voziris.ui.bandeja import crear_acceso_directo

    menu = menu_inicio or carpeta_menu_inicio()
    menu.mkdir(parents=True, exist_ok=True)
    arranque = (startup or carpeta_startup()) / ACCESO_STARTUP
    with (pausar_volcados or contextlib.nullcontext)():
        crear_acceso_directo(menu / ACCESO_MENU, str(exe), "", str(destino))
        log.info("acceso directo en el menú Inicio: %s", menu / ACCESO_MENU)
        if arranque.exists():
            # Estaba apuntando a la copia portable: que arranque la instalada.
            crear_acceso_directo(arranque, str(exe), "", str(destino))
            log.info("acceso directo de Inicio de sesión actualizado: %s", arranque)
    for acceso in (menu / ACCESO_MENU, arranque):
        if acceso.exists() and integridad.quitar_marca_de_internet(acceso):
            log.info("quitada la marca de Internet de %s", acceso)

    _registrar(destino, exe, version, raiz_registro, clave_registro)
    registrar_menu_contextual([str(exe)], raiz=raiz_registro, base=base_menu)
    return destino


def comprobar_arrancando(exe: Path) -> None:
    """Arranca la copia con `--comprobar` y lanza `InstalacionFallida` si algo no carga.

    Es la única forma de saber si Windows va a dejar ejecutarla: Control
    inteligente de aplicaciones decide archivo por archivo, y la copia son
    archivos nuevos que no ha visto nunca.
    """
    informe = integridad.comprobar_en_otro_proceso([str(exe)])
    if informe.get("ok"):
        log.info("la copia de %s arranca y carga todo", exe.parent)
        return
    log.error("la copia no pasa la comprobación:\n%s", integridad.informe_a_texto(informe))
    if informe.get("bloqueado"):
        raise InstalacionFallida(MENSAJE_BLOQUEO, bloqueado=True)
    paquete = next(
        (p for p in informe.get("pruebas", []) if p["nombre"] == "paquete" and not p["ok"]), None
    )
    if paquete is not None:
        # Si falta un archivo, lo demás que falla es por eso: se dice solo la causa.
        # `exe` está en «Voziris.nuevo»; la excepción hay que ponerla en «Voziris».
        raise InstalacionFallida(
            "La copia ha quedado incompleta, así que no se ha cambiado nada.\n\n"
            f"{paquete.get('resumen', paquete['detalle'])}.\n\n"
            + ANTIVIRUS_EN_LA_COPIA.format(carpeta=exe.parent.with_suffix(""))
        )
    fallos = integridad.fallos_del_informe(informe) or ["no se sabe por qué"]
    mas = ""
    if len(fallos) > 1:
        mas = f"\n\n(Y {len(fallos) - 1} problema(s) más, anotados en voziris.log.)"
    raise InstalacionFallida(
        f"La copia instalada no funciona, así que no se ha cambiado nada.\n\n{fallos[0]}{mas}"
    )


ANTIVIRUS_EN_LA_COPIA = (
    "Suele ser el antivirus, que retira el archivo nada más copiarlo. Restáuralo desde "
    "su cuarentena o añade una excepción para {carpeta} y vuelve a instalar."
)
MENSAJE_BLOQUEO = (
    "Windows no deja ejecutar Voziris desde la carpeta de instalación: lo bloquea "
    "«Control inteligente de aplicaciones», que frena los programas sin firma digital "
    "que Microsoft todavía no conoce. No se ha instalado nada.\n\n"
    "Puedes seguir usando Voziris desde esta carpeta: ábrelo con doble clic en "
    "voziris.exe y, para tenerlo a mano, botón derecho → Anclar a Inicio."
)


def _instalar_programa(
    origen: Path,
    destino: Path,
    comprobar_copia: Callable[[Path], None] | None,
    al_progresar: Callable[[str, float | None], None] | None,
    antes_de_sustituir: Callable[[], None] | None = None,
) -> None:
    """voziris.exe y _internal de `origen` a `destino`, comprobados, o nada."""

    def avisar(mensaje: str, fraccion: float | None = None) -> None:
        if al_progresar is not None:
            al_progresar(mensaje, fraccion)

    avisar("Comprobando que el paquete está completo…")
    try:
        entradas = integridad.leer_manifiesto(origen)
    except integridad.ManifiestoDanado as e:
        log.error("manifiesto del origen dañado: %s", e)
        raise InstalacionFallida(
            "La carpeta desde la que instalas está incompleta: no se ha instalado nada.\n\n"
            f"Está dañado _internal\\{integridad.NOMBRE_MANIFIESTO}.\n\n"
            f"{integridad.POR_QUE_FALTAN}"
        ) from e
    if entradas is None:
        log.warning("%s no trae manifiesto: no se puede comprobar que esté entero", origen)
    else:
        r = integridad.comprobar(origen, hashes=True, entradas=entradas)
        if not r.ok:
            raise InstalacionFallida(
                f"La carpeta desde la que instalas está incompleta: no se ha instalado nada.\n\n"
                f"{r.resumen()}.\n\n{integridad.POR_QUE_FALTAN}"
            )
        log.info("origen completo: %d archivos", r.comprobados)

    destino.parent.mkdir(parents=True, exist_ok=True)
    nuevo = destino.with_name(destino.name + SUFIJO_NUEVO)
    _limpiar_restos(destino, nuevo)
    _comprobar_espacio(origen, destino, entradas)

    total = len(entradas) if entradas else sum(1 for _ in (origen / "_internal").rglob("*"))
    copiados = 0

    def copiar_contando(fuente: str, copia: str) -> str:
        nonlocal copiados
        _copiar_archivo(Path(fuente), Path(copia))
        copiados += 1
        if copiados % 25 == 0:
            avisar("Copiando el programa…", min(copiados / max(total, 1), 0.99))
        return copia

    try:
        avisar("Copiando el programa…", 0.0)
        nuevo.mkdir()
        _copiar_archivo(origen / "voziris.exe", nuevo / "voziris.exe")
        shutil.copytree(origen / "_internal", nuevo / "_internal", copy_function=copiar_contando)
        quitadas = integridad.quitar_marcas_de_internet(nuevo)
        if quitadas:
            log.info("quitada la marca de «descargado de Internet» a %d archivos", quitadas)
        if entradas is not None:
            r = integridad.comprobar(nuevo, entradas=entradas)
            if not r.ok:
                raise InstalacionFallida(
                    "La copia ha quedado incompleta, así que no se ha cambiado nada.\n\n"
                    f"{r.resumen()}.\n\n" + ANTIVIRUS_EN_LA_COPIA.format(carpeta=destino)
                )
        if comprobar_copia is not None:
            avisar("Comprobando que la copia arranca…")
            comprobar_copia(nuevo / "voziris.exe")
        avisar("Sustituyendo la versión anterior…")
        if antes_de_sustituir is not None:
            antes_de_sustituir()
        _sustituir(nuevo, destino)
    except shutil.Error as e:
        raise InstalacionFallida(explicar_error_de_copia(e)) from e
    except OSError as e:
        raise InstalacionFallida(explicar_error_de_copia(e)) from e
    finally:
        _borrar(nuevo)


def _sustituir(nuevo: Path, destino: Path) -> None:
    """Cambia voziris.exe y _internal por los de `nuevo`. Si algo falla, deja lo de antes.

    Se aparta lo viejo con un nombre propio, se mueve lo nuevo (es un
    renombrado dentro del mismo disco: instantáneo) y al final se borra lo
    apartado. Lo que haya en `destino` además del programa es del usuario y
    no se toca.
    """
    destino.mkdir(parents=True, exist_ok=True)
    marca = f"{SUFIJO_VIEJO}-{time.strftime('%Y%m%d%H%M%S')}"
    apartados: list[tuple[Path, Path]] = []
    puestos: list[Path] = []
    try:
        for nombre in PROGRAMA:
            actual = destino / nombre
            if actual.exists():
                viejo = destino / f"{nombre}{marca}"
                _reintentar(os.replace, actual, viejo)
                apartados.append((actual, viejo))
        for nombre in PROGRAMA:
            _reintentar(os.replace, nuevo / nombre, destino / nombre)
            puestos.append(destino / nombre)
    except OSError as e:
        log.exception("no se pudo cambiar la instalación: se deja la anterior")
        for puesto in puestos:
            _borrar(puesto)
        sin_devolver = []
        for actual, viejo in reversed(apartados):
            try:
                _reintentar(os.replace, viejo, actual)
            except OSError:
                log.exception("no se pudo devolver %s a su sitio", actual)
                sin_devolver.append(actual.name)
        if sin_devolver:
            # Lo apartado sigue ahí con su nombre .viejo-…: la próxima instalación
            # lo devuelve a su sitio antes de nada (ver `_limpiar_restos`).
            raise InstalacionFallida(
                "No se pudo cambiar la versión anterior por la nueva, y tampoco dejarla "
                f"como estaba: {_causa(str(e), e)}.\n\nNo se ha perdido nada. Cierra lo que "
                "tenga abierta la carpeta de Voziris, espera un minuto y vuelve a instalar: "
                "la instalación recupera primero la versión anterior."
            ) from e
        raise
    for _, viejo in apartados:
        _borrar(viejo)


def _limpiar_restos(destino: Path, nuevo: Path) -> None:
    """Lo que dejó una instalación interrumpida: la copia a medias y lo apartado.

    Si al programa le falta su voziris.exe o su _internal y hay uno apartado
    (`.viejo-…`), es que una instalación se cortó a mitad del cambio o no pudo
    deshacerlo: se devuelve a su sitio en vez de borrarlo, porque puede ser
    la única copia que funciona.
    """
    _borrar(nuevo)
    if not destino.is_dir():
        return
    for nombre in PROGRAMA:
        apartados = sorted(destino.glob(f"{nombre}{SUFIJO_VIEJO}-*"), reverse=True)
        if apartados and not (destino / nombre).exists():
            try:
                _reintentar(os.replace, apartados[0], destino / nombre)
                log.warning("recuperado %s de una instalación interrumpida", apartados[0].name)
            except OSError:
                log.exception("no se pudo recuperar %s", apartados[0])
    for resto in destino.glob(f"*{SUFIJO_VIEJO}-*"):
        _borrar(resto)


def _comprobar_espacio(
    origen: Path, destino: Path, entradas: list[integridad.Entrada] | None
) -> None:
    if entradas is not None:
        necesario = integridad.tamano_total(entradas)
    else:
        necesario = _tamano(origen / "_internal") + (origen / "voziris.exe").stat().st_size
    for nombre in DATOS_DEL_USUARIO:
        if not (destino / nombre).exists():
            necesario += _tamano(origen / nombre)
    libre = shutil.disk_usage(destino.parent).free
    if libre < necesario + MARGEN_DISCO:
        raise InstalacionFallida(
            f"No hay sitio en el disco: Voziris necesita unos "
            f"{(necesario + MARGEN_DISCO) // 2**20} MB libres y hay {libre // 2**20} MB. "
            "Libera espacio y vuelve a intentarlo."
        )


def _tamano(ruta: Path) -> int:
    if ruta.is_file():
        return ruta.stat().st_size
    if not ruta.is_dir():
        return 0
    return sum(p.stat().st_size for p in ruta.rglob("*") if p.is_file())


def _reintentar(funcion: Callable[..., object], *argumentos: object) -> object:
    """Repite ante «archivo en uso» o «acceso denegado», que suele ser el antivirus."""
    for intento in range(REINTENTOS):
        try:
            return funcion(*argumentos)
        except OSError as e:
            if getattr(e, "winerror", None) not in _ERRORES_PASAJEROS or intento == REINTENTOS - 1:
                raise
            log.info("reintento %d: %s", intento + 1, e)
            time.sleep(ESPERA_REINTENTO_S * (intento + 1))
    raise AssertionError("inalcanzable")


def _copiar_archivo(fuente: Path, copia: Path) -> None:
    _reintentar(shutil.copy2, fuente, copia)


def _borrar(ruta: Path) -> None:
    """Borra un archivo o una carpeta lo mejor que pueda. Nunca lanza."""
    try:
        if ruta.is_dir():
            for _ in range(3):
                shutil.rmtree(ruta, ignore_errors=True)
                if not ruta.exists():
                    return
                time.sleep(ESPERA_REINTENTO_S)
            log.warning("no se pudo borrar del todo %s", ruta)
        elif ruta.exists():
            ruta.unlink()
    except OSError as e:
        log.warning("no se pudo borrar %s: %s", ruta, e)


_WINERROR = re.compile(r"\[WinError (\d+)\]")
_ERRNO = re.compile(r"\[Errno (\d+)\]")


def explicar_error_de_copia(error: BaseException) -> str:
    """El texto de `shutil.Error` es una lista de tuplas con las barras dobladas: ilegible."""
    pie = "No se ha cambiado nada: si había una instalación anterior, sigue como estaba."
    if isinstance(error, shutil.Error) and error.args and isinstance(error.args[0], list):
        fallos = error.args[0]
        log.error("no se copiaron %d archivos: %s", len(fallos), fallos[:20])
        nombres = ", ".join(Path(str(f[0])).name for f in fallos[:3])
        resto = f" y {len(fallos) - 3} más" if len(fallos) > 3 else ""
        causa = _causa(str(fallos[0][2]) if fallos else "", None)
        return f"No se pudieron copiar {len(fallos)} archivos ({nombres}{resto}): {causa}.\n\n{pie}"
    if isinstance(error, OSError):
        return f"No se pudo instalar: {_causa(str(error), error)}.\n\n{pie}"
    return f"No se pudo instalar: {error}\n\n{pie}"


def _causa(texto: str, error: OSError | None) -> str:
    winerror = getattr(error, "winerror", None)
    if winerror is None and (m := _WINERROR.search(texto)):
        winerror = int(m.group(1))
    numero = getattr(error, "errno", None)
    if numero is None and (m := _ERRNO.search(texto)):
        numero = int(m.group(1))
    if winerror in _ERRORES_PASAJEROS:
        return ("otro programa tiene abierto un archivo (el antivirus analizándolo, o una "
                "Voziris que sigue abierta). Espera un minuto y vuelve a intentarlo")
    if winerror in (39, 112) or numero == errno.ENOSPC:
        return "el disco está lleno"
    if winerror == 206 or numero == errno.ENAMETOOLONG:
        return "una ruta es demasiado larga"
    if winerror in integridad.ERRORES_DE_BLOQUEO:
        return ("Windows lo ha bloqueado (Control inteligente de aplicaciones o el "
                "antivirus)")
    return texto or "error desconocido"


def desinstalar(
    destino: Path | None = None,
    *,
    menu_inicio: Path | None = None,
    startup: Path | None = None,
    raiz_registro: int | None = None,
    clave_registro: str = CLAVE_DESINSTALAR,
    base_menu: str = BASE_MENU_CONTEXTUAL,
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
    quitar_menu_contextual(raiz=raiz_registro, base=base_menu)
    if not borrar_carpeta or not destino.exists():
        return
    if aplazado is None:
        aplazado = esta_instalado(destino=destino)
    if aplazado:
        _borrar_aplazado(destino)
    else:
        shutil.rmtree(destino, ignore_errors=True)


# --- menú contextual del Explorador ------------------------------------------------------


def registrar_menu_contextual(
    orden: list[str],
    extensiones: tuple[str, ...] | None = None,
    raiz: int | None = None,
    base: str = BASE_MENU_CONTEXTUAL,
) -> None:
    """«Transcribir con Voziris» en el botón derecho de cada extensión de audio.

    Un menú en cascada con dos verbos. `orden` es cómo lanzar Voziris
    (`["C:\\...\\voziris.exe"]` o `[python, "-m", "voziris"]`); a cada verbo
    se le añade `--transcribir "%1" --hablantes … --ventana`. Solo en HKCU: el
    Explorador lo lee sin permisos y no afecta a otros usuarios.
    """
    if sys.platform != "win32":
        return
    import winreg

    from voziris.archivos import FORMATOS

    raiz = winreg.HKEY_CURRENT_USER if raiz is None else raiz
    exe = orden[0]
    # El ejecutable siempre entre comillas (rutas con espacios); las opciones, tal cual.
    prefijo = " ".join(f'"{parte}"' if i == 0 or " " in parte else parte
                       for i, parte in enumerate(orden))
    for ext in extensiones or FORMATOS:
        clave = f"{base}\\{ext}\\shell\\{NOMBRE_MENU_CONTEXTUAL}"
        with winreg.CreateKeyEx(raiz, clave, 0, winreg.KEY_WRITE) as k:
            winreg.SetValueEx(k, "MUIVerb", 0, winreg.REG_SZ, "Transcribir con Voziris")
            winreg.SetValueEx(k, "Icon", 0, winreg.REG_SZ, f'"{exe}",0')
            winreg.SetValueEx(k, "SubCommands", 0, winreg.REG_SZ, "")
        for subclave, etiqueta, hablantes in VERBOS_MENU_CONTEXTUAL:
            verbo = f"{clave}\\shell\\{subclave}"
            with winreg.CreateKeyEx(raiz, verbo, 0, winreg.KEY_WRITE) as k:
                winreg.SetValueEx(k, None, 0, winreg.REG_SZ, etiqueta)
            with winreg.CreateKeyEx(raiz, verbo + "\\command", 0, winreg.KEY_WRITE) as k:
                mandato = f'{prefijo} --transcribir "%1" --hablantes {hablantes} --ventana'
                winreg.SetValueEx(k, None, 0, winreg.REG_SZ, mandato)
    log.info("menú contextual «Transcribir con Voziris» registrado para %d extensiones",
             len(extensiones or FORMATOS))


def quitar_menu_contextual(
    extensiones: tuple[str, ...] | None = None,
    raiz: int | None = None,
    base: str = BASE_MENU_CONTEXTUAL,
) -> None:
    if sys.platform != "win32":
        return
    import winreg

    from voziris.archivos import FORMATOS

    raiz = winreg.HKEY_CURRENT_USER if raiz is None else raiz
    quitadas = 0
    for ext in extensiones or FORMATOS:
        clave = f"{base}\\{ext}\\shell\\{NOMBRE_MENU_CONTEXTUAL}"
        if _borrar_clave(raiz, clave):
            quitadas += 1
    if quitadas:
        log.info("menú contextual quitado de %d extensiones", quitadas)


def hay_menu_contextual(raiz: int | None = None, base: str = BASE_MENU_CONTEXTUAL) -> bool:
    if sys.platform != "win32":
        return False
    import winreg

    raiz = winreg.HKEY_CURRENT_USER if raiz is None else raiz
    try:
        with winreg.OpenKey(raiz, f"{base}\\.m4a\\shell\\{NOMBRE_MENU_CONTEXTUAL}"):
            return True
    except FileNotFoundError:
        return False


def _borrar_clave(raiz: int, clave: str) -> bool:
    """`winreg.DeleteKey` no borra subclaves; esto sí. False si no existía."""
    import winreg

    try:
        with winreg.OpenKey(raiz, clave, 0, winreg.KEY_READ) as k:
            hijas = []
            i = 0
            while True:
                try:
                    hijas.append(winreg.EnumKey(k, i))
                except OSError:
                    break
                i += 1
    except FileNotFoundError:
        return False
    for hija in hijas:
        _borrar_clave(raiz, f"{clave}\\{hija}")
    winreg.DeleteKey(raiz, clave)
    return True


# --- detalles ----------------------------------------------------------------------


def _copiar_arbol(fuente: Path, destino: Path) -> None:
    """Los datos del usuario (modelos/, historial/): solo lo que no esté ya en la instalación.

    Lo que hay en la instalación manda, igual que con config.toml. Si quien
    actualiza abre antes la voziris.exe recién extraída, esa carpeta tiene
    su propio historial/dictados.jsonl de una línea, y copiarlo encima
    borraba el historial de meses de la instalada (visto al verificar la 0.1.1).
    """

    def copiar(origen: str, copia: str) -> str:
        if Path(copia).exists():
            return copia
        _copiar_archivo(Path(origen), Path(copia))
        return copia

    shutil.copytree(fuente, destino, dirs_exist_ok=True, copy_function=copiar)


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


def version_instalada(raiz: int | None = None, clave: str = CLAVE_DESINSTALAR) -> str | None:
    """La versión que dice «Aplicaciones instaladas», o None si no hay ninguna instalada.

    Se lee antes de instalar: si había otra, el mensaje final dice
    «actualizado» (VOZ-82).
    """
    if sys.platform != "win32":
        return None
    import winreg

    raiz = winreg.HKEY_CURRENT_USER if raiz is None else raiz
    try:
        with winreg.OpenKey(raiz, clave) as k:
            valor, tipo = winreg.QueryValueEx(k, "DisplayVersion")
    except OSError:
        return None
    return valor if tipo == winreg.REG_SZ and isinstance(valor, str) and valor else None


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
