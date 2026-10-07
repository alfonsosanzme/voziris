"""Un archivo de diagnóstico que se puede mandar tal cual.

Cuando algo falla, lo útil no es «se ha cerrado», sino el registro, los
eventos que Windows anotó sobre el proceso y qué versión era. Esto lo reúne
todo en `diagnostico.txt`, junto al ejecutable, con la clave de API tachada.

Hay un tipo de fallo que el registro de Voziris no puede ver: cuando una
biblioteca nativa corrompe la memoria, Windows mata el proceso al instante y
Python no llega a escribir nada. Eso se ve en el visor de eventos (código
`0xc0000374`) y, si se activan los volcados, en un `.dmp`. Los volcados exigen
una clave en HKLM y administrador: no los activa la aplicación, se explica
cómo hacerlo.
"""

from __future__ import annotations

import logging
import os
import platform
import re
import subprocess
import sys
from pathlib import Path

from voziris import __version__

log = logging.getLogger(__name__)

NOMBRE_ARCHIVO = "diagnostico.txt"
LINEAS_DE_LOG = 150

_CLAVE_VOLCADOS = (
    r"HKLM\SOFTWARE\Microsoft\Windows\Windows Error Reporting\LocalDumps\voziris.exe"
)
_CARPETA_VOLCADOS = r"%LOCALAPPDATA%\Voziris\volcados"

INSTRUCCIONES_VOLCADOS = "\n".join(
    (
        "Para que Windows guarde un volcado (.dmp) la próxima vez que voziris.exe se",
        "cierre de golpe, ejecuta ESTO en un PowerShell como administrador (una vez):",
        "",
        f'  reg add "{_CLAVE_VOLCADOS}" /v DumpFolder /t REG_EXPAND_SZ'
        f' /d "{_CARPETA_VOLCADOS}" /f',
        f'  reg add "{_CLAVE_VOLCADOS}" /v DumpType /t REG_DWORD /d 1 /f',
        f'  reg add "{_CLAVE_VOLCADOS}" /v DumpCount /t REG_DWORD /d 5 /f',
        "",
        f"El volcado aparece en {_CARPETA_VOLCADOS}. Para quitarlo:",
        "",
        f'  reg delete "{_CLAVE_VOLCADOS}" /f',
        "",
    )
)

_CLAVE = re.compile(r"gsk_[A-Za-z0-9]{8,}")


def generar(carpeta: Path, ruta_log: Path | None = None, version: str = __version__) -> Path:
    """Escribe `carpeta/diagnostico.txt` y devuelve su ruta. Nunca lanza."""
    partes = [
        f"Diagnóstico de Voziris {version}",
        f"Fecha: {platform.node()} · {os.environ.get('USERNAME', '?')} · {_ahora()}",
        f"Ejecutable: {sys.executable}",
        f"Congelado: {bool(getattr(sys, 'frozen', False))}",
        f"Python: {platform.python_version()} · {platform.platform()}",
        f"Windows: {_version_de_windows()} · {platform.machine()}",
        f"Carpeta de datos: {carpeta}",
        "",
        "== Comprobación del paquete (voziris.exe --comprobar) ==",
        comprobacion_del_paquete(),
        "",
        "== Control de aplicaciones de Windows ==",
        control_de_aplicaciones(),
        "",
        "== Últimas líneas del registro (voziris.log) ==",
        _cola_del_log(ruta_log or carpeta / "voziris.log"),
        "",
        "== Últimas transcripciones de archivos (voziris-transcribir.log) ==",
        _cola_del_log(carpeta / "voziris-transcribir.log", lineas=60),
        "",
        "== Fallos nativos (voziris-fallos.log) ==",
        _fallos_nativos(carpeta / "voziris-fallos.log"),
        "",
        "== Eventos de Windows sobre voziris.exe (últimos 30 días) ==",
        eventos_windows(),
        "",
        "== Informes de error de Windows (WER) ==",
        "\n".join(informes_wer()) or "(ninguno accesible)",
        "",
        "== Cómo conseguir un volcado del próximo fallo ==",
        INSTRUCCIONES_VOLCADOS,
    ]
    destino = carpeta / NOMBRE_ARCHIVO
    try:
        destino.write_text("\n".join(partes), encoding="utf-8", newline="\n")
    except OSError as e:
        log.warning("no se pudo escribir el diagnóstico en %s: %s", destino, e)
    return destino


def _ahora() -> str:
    import time

    return time.strftime("%Y-%m-%d %H:%M:%S")


def _version_de_windows() -> str:
    if sys.platform != "win32":
        return "(no es Windows)"
    v = sys.getwindowsversion()
    return f"{platform.release()} (compilación {v.build})"


def comprobacion_del_paquete(espera_s: float = 120.0) -> str:
    """Lo que dice `voziris.exe --comprobar`, en otro proceso (VOZ-81).

    En otro proceso para no cargar PyAV y compañía en la bandeja, que vive
    todo el día, y porque lo que se quiere saber es si un arranque nuevo lo
    carga todo.
    """
    from voziris import integridad

    congelado = getattr(sys, "frozen", False)
    orden = [sys.executable] if congelado else [sys.executable, "-m", "voziris"]
    try:
        informe = integridad.comprobar_en_otro_proceso(orden, espera_s)
    except Exception as e:  # noqa: BLE001 — el diagnóstico nunca lanza
        return f"(no se pudo comprobar: {e})"
    estado = "todo carga" if informe.get("ok") else "HAY PROBLEMAS"
    return f"{estado}\n{integridad.informe_a_texto(informe)}"


def control_de_aplicaciones() -> str:
    """Control inteligente de aplicaciones, la marca de Internet y lo que Windows bloqueó.

    Control inteligente de aplicaciones (Windows 11) bloquea código sin firma
    que Microsoft no conoce, archivo por archivo. Los bloqueos quedan en el
    registro CodeIntegrity/Operational, que se lee sin administrador.
    """
    from voziris import integridad

    estado = integridad.control_inteligente() or "no se sabe (¿no es Windows 11?)"
    lineas = [f"Control inteligente de aplicaciones: {estado}"]
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable)
        marca = "sí" if integridad.tiene_marca_de_internet(exe) else "no"
        lineas.append(f"voziris.exe con la marca de «descargado de Internet»: {marca}")
    lineas += ["", "Bloqueos anotados por Windows (CodeIntegrity, últimos 30 días):",
               bloqueos_de_codigo()]
    return "\n".join(lineas)


def bloqueos_de_codigo(dias: int = 30) -> str:
    """Eventos 3033/3034/3076/3077 del registro CodeIntegrity que mencionan voziris."""
    if sys.platform != "win32":
        return "(solo en Windows)"
    consulta = (
        "*[System[(EventID=3033 or EventID=3034 or EventID=3076 or EventID=3077) and "
        f"TimeCreated[timediff(@SystemTime) <= {dias * 86_400_000}]]]"
    )
    salida = _wevtutil("Microsoft-Windows-CodeIntegrity/Operational", consulta, 400)
    if salida.startswith("("):
        return salida
    bloques = [b for b in salida.split("\n\nEvent[") if "voziris" in b.lower()]
    if not bloques:
        return "(ninguno)"
    return "\n\nEvent[".join(bloques[:15])[:8_000]


def _wevtutil(registro: str, consulta: str, cuantos: int) -> str:
    """El texto de wevtutil, o «(…)» si no se pudo.

    Escribe en la página de códigos ANSI («mbcs»), no en UTF-8: con UTF-8 un
    usuario «AlfonsoSanzLópez» salía como «AlfonsoSanzL�pez». Y con finales de
    línea CRLF, que se pasan a LF: si no, partir por evento no parte nada y el
    filtro por «voziris» deja pasar los eventos de todos los programas.
    """
    try:
        salida = subprocess.run(
            ["wevtutil", "qe", registro, f"/q:{consulta}", "/rd:true", "/f:text", f"/c:{cuantos}"],
            capture_output=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"(no se pudo consultar el visor de eventos: {e})"
    texto = salida.stdout.decode("mbcs", errors="replace").replace("\r\n", "\n")
    if salida.returncode != 0:
        error = salida.stderr.decode("mbcs", errors="replace").strip()[:200]
        return f"(wevtutil devolvió {salida.returncode}: {error})"
    return texto


def _es_marca(linea: str) -> bool:
    return linea.startswith("=== ") and linea.endswith(" ===")


def _fallos_nativos(ruta: Path, lineas: int = 60) -> str:
    """Los volcados de voziris-fallos.log, cada uno con la marca que lo fecha.

    Desde VOZ-80 cada proceso deja una línea «=== fecha · arranque: modo (pid)
    ===». Las marcas sin volcado detrás no se enseñan: con un arranque al día
    llenarían la cola y echarían fuera de la vista un volcado de hace un mes.
    Por eso se lee el archivo entero y no solo el final. Si no hay ningún
    volcado, se dice que no hubo fallos.
    """
    if not ruta.exists():
        return f"(no existe {ruta.name})"
    try:
        texto = ruta.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return f"(no se pudo leer {ruta.name}: {e})"
    segmentos: list[list[str]] = [[]]
    for linea in texto.splitlines():
        if _es_marca(linea):
            segmentos.append([linea])
        else:
            segmentos[-1].append(linea)
    marcas = sum(1 for s in segmentos if s and _es_marca(s[0]))
    con_volcado = [s for s in segmentos if any(ln.strip() and not _es_marca(ln) for ln in s)]
    if not con_volcado:
        return f"(sin fallos; {marcas} arranque(s) anotado(s))" if marcas else "(vacío)"
    salida = [ln for s in con_volcado for ln in s if ln.strip()]
    return _CLAVE.sub("gsk_***", "\n".join(salida[-lineas:]))


def _cola_del_log(ruta: Path, lineas: int = LINEAS_DE_LOG) -> str:
    if not ruta.exists():
        return f"(no existe {ruta.name})"
    try:
        texto = ruta.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return f"(no se pudo leer {ruta.name}: {e})"
    cola = texto.splitlines()[-lineas:]
    return _CLAVE.sub("gsk_***", "\n".join(cola)) or "(vacío)"


def eventos_windows(dias: int = 30) -> str:
    """Los eventos 1000/1001 (APPCRASH) del registro «Application» que hablan de voziris.

    `wevtutil` lee el registro de aplicaciones sin administrador. Se filtra
    por texto porque el evento no lleva el nombre del proceso en un campo
    consultable.
    """
    if sys.platform != "win32":
        return "(solo en Windows)"
    consulta = (
        "*[System[(EventID=1000 or EventID=1001 or EventID=1026) and "
        f"TimeCreated[timediff(@SystemTime) <= {dias * 86_400_000}]]]"
    )
    try:
        salida = subprocess.run(
            ["wevtutil", "qe", "Application", f"/q:{consulta}", "/rd:true", "/f:text", "/c:80"],
            capture_output=True, text=True, timeout=20, encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"(no se pudo consultar el visor de eventos: {e})"
    if salida.returncode != 0:
        return f"(wevtutil devolvió {salida.returncode}: {salida.stderr.strip()[:200]})"
    bloques = [b for b in salida.stdout.split("\n\nEvent[") if "voziris" in b.lower()]
    if not bloques:
        return "(ningún fallo de voziris.exe en ese periodo)"
    return "\n\nEvent[".join(bloques)[:12_000]


def informes_wer() -> list[str]:
    """Carpetas de informes de WER que mencionan voziris, con su fecha."""
    if sys.platform != "win32":
        return []
    base = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Microsoft" / "Windows" / "WER"
    encontrados: list[str] = []
    for sub in ("ReportArchive", "ReportQueue"):
        carpeta = base / sub
        try:
            for informe in carpeta.iterdir():
                if "voziris" in informe.name.lower():
                    marca = _ahora_de(informe)
                    encontrados.append(f"{marca}  {informe}")
        except OSError:
            continue
    volcados = Path(os.environ.get("LOCALAPPDATA", "")) / "Voziris" / "volcados"
    if volcados.is_dir():
        for dmp in sorted(volcados.glob("*.dmp")):
            encontrados.append(f"{_ahora_de(dmp)}  {dmp}  (volcado: mándalo entero)")
    return sorted(encontrados, reverse=True)


def _ahora_de(ruta: Path) -> str:
    import time

    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(ruta.stat().st_mtime))
    except OSError:
        return "????-??-?? ??:??"
