"""Que el paquete esté entero, y si no lo está, decir qué archivo falta (VOZ-81).

Un compañero recibió «Falta PyAV, que decodifica el audio» con un ZIP que
estaba completo. Se reprodujo exacto quitando UNO cualquiera de los 74
binarios de PyAV: el antivirus pone en cuarentena una DLL sin firmar, o el
Explorador se salta un archivo al extraer, y Windows solo dice «No se puede
encontrar el módulo especificado», sin decir cuál. Este módulo da el nombre:

  - `tools/empaquetar.py` escribe `_internal/voziris-manifiesto.txt` con la
    ruta, el tamaño y el sha256 de cada archivo del programa.
  - El instalador lo usa para negarse a instalar una carpeta incompleta y para
    comprobar la copia antes de darla por buena.
  - Cuando un import falla, `explicar_fallo_de_carga` compara el disco con el
    manifiesto y nombra el archivo que falta o que no cuadra.

También reconoce los bloqueos de Windows. «Control inteligente de
aplicaciones» (Smart App Control, Windows 11) decide archivo por archivo si
deja cargar el código que no lleva firma digital, según la reputación que
Microsoft tenga de él. Lo que bloquea falla con los errores 4551 y siguientes
(«Una directiva de Control de aplicaciones bloqueó este archivo»), y eso no se
arregla reinstalando: hay que contarlo de otra manera.

Y la marca de «descargado de Internet» (el flujo alternativo
`Zone.Identifier`): se hereda al extraer un ZIP sin desbloquear y
`shutil.copy2` la copia. La instalación se la quita, porque al ejecutar el
instalador ya se aceptó una vez.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib
import json
import logging
import os
import sys
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

NOMBRE_MANIFIESTO = "voziris-manifiesto.txt"
"""Dentro de `_internal/`: viaja con el programa y se instala con él."""
CABECERA = "# Voziris · manifiesto del paquete: sha256, tamaño en bytes, ruta"

LIMITE_RUTA = 180
"""Caracteres de la carpeta de voziris.exe a partir de los que se avisa.

Medido en VOZ-81: con la carpeta en 193 caracteres deja de cargar
onnxruntime, en 211 PyAV y en 227 ni arranca el .exe. Activar las rutas
largas de Windows no cambia nada. 180 deja margen; %LOCALAPPDATA%\\Programs
mide unos 40.
"""

ERRORES_DE_BLOQUEO = frozenset({4551, 4556, 4557, 4558, 4559, 4580, 4581, 4582, 4583})
"""Los ERROR_SYSTEM_INTEGRITY_* de Windows: un control de aplicaciones bloqueó el archivo.

4558 es «extensión de archivo peligrosa de web» y 4559, «no se puede contactar
con el servicio de reputación para un archivo desconocido» (sin red, Control
inteligente de aplicaciones bloquea lo que no conoce).
"""
_TEXTOS_DE_BLOQUEO = ("directiva de control de aplicaciones", "application control policy")

POR_QUE_FALTAN = (
    "Suele ser el antivirus, que ha puesto ese archivo en cuarentena, o una "
    "extracción del ZIP que se saltó archivos. Vuelve a extraer el ZIP entero "
    "(botón derecho → Extraer todo, en Documentos) y espera a que termine. Si el "
    "antivirus avisó, restaura el archivo o añade una excepción para la carpeta."
)


# --- el manifiesto ---------------------------------------------------------------------


@dataclass(frozen=True)
class Entrada:
    ruta: str
    """Relativa a la carpeta de voziris.exe, con «/»."""
    tamano: int
    sha256: str


def ruta_manifiesto(raiz: Path) -> Path:
    return Path(raiz) / "_internal" / NOMBRE_MANIFIESTO


def archivos_del_programa(raiz: Path) -> list[Path]:
    """voziris.exe y todo `_internal/`, menos el propio manifiesto.

    Lo que hay al lado del ejecutable y no es el programa (config.toml,
    modelos/, historial/, los logs, el .cmd de instalar) no entra: cambia o
    es del usuario.
    """
    raiz = Path(raiz)
    manifiesto = ruta_manifiesto(raiz)
    archivos = [raiz / "voziris.exe"]
    archivos += sorted(
        p for p in (raiz / "_internal").rglob("*") if p.is_file() and p != manifiesto
    )
    return archivos


def escribir_manifiesto(raiz: Path) -> Path:
    """Lo llama `tools/empaquetar.py` sobre la carpeta recién compilada."""
    raiz = Path(raiz)
    lineas = [CABECERA]
    for archivo in archivos_del_programa(raiz):
        relativa = archivo.relative_to(raiz).as_posix()
        lineas.append(f"{_sha256(archivo)} {archivo.stat().st_size} {relativa}")
    destino = ruta_manifiesto(raiz)
    destino.write_text("\n".join(lineas) + "\n", encoding="utf-8", newline="\n")
    return destino


class ManifiestoDanado(ValueError):
    """El manifiesto existe pero no se puede leer: a medias, vacío o sin el ejecutable."""


def leer_manifiesto(raiz: Path) -> list[Entrada] | None:
    """None si no hay manifiesto: en desarrollo, o un paquete anterior a VOZ-81.

    Raises:
        ManifiestoDanado: está, pero truncado, vacío o sin voziris.exe. No vale
            tratarlo como «no hay»: un manifiesto vacío daría el paquete por
            completo sin mirar nada.
    """
    try:
        texto = ruta_manifiesto(raiz).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as e:
        raise ManifiestoDanado(f"no se puede leer: {e}") from e
    entradas = []
    for n, linea in enumerate(texto.splitlines(), 1):
        if not linea.strip() or linea.startswith("#"):
            continue
        partes = linea.split(" ", 2)
        if len(partes) != 3 or len(partes[0]) != 64 or not partes[1].isdigit():
            raise ManifiestoDanado(f"la línea {n} no se entiende: {linea[:80]!r}")
        entradas.append(Entrada(partes[2], int(partes[1]), partes[0]))
    if not any(e.ruta == "voziris.exe" for e in entradas):
        raise ManifiestoDanado("no incluye voziris.exe")
    return entradas


def _sha256(archivo: Path) -> str:
    h = hashlib.sha256()
    with open(archivo, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


# --- comprobar una carpeta -------------------------------------------------------------


@dataclass
class Resultado:
    raiz: Path
    comprobados: int = 0
    faltan: list[str] = field(default_factory=list)
    distintos: list[str] = field(default_factory=list)
    """Están, pero con otro tamaño o, si se miraron, otro contenido."""
    sin_manifiesto: bool = False

    @property
    def ok(self) -> bool:
        return not self.faltan and not self.distintos

    def resumen(self, maximo: int = 3) -> str:
        """«Falta _internal\\av.libs\\avcodec-….dll» o «Faltan 3 archivos: …»."""
        partes = []
        for lista, uno, varios in (
            (self.faltan, "Falta", "Faltan {n} archivos"),
            (self.distintos, "Está dañado", "Están dañados {n} archivos"),
        ):
            if not lista:
                continue
            nombres = [_para_mostrar(r) for r in lista[:maximo]]
            if len(lista) == 1:
                partes.append(f"{uno} {nombres[0]}")
            else:
                resto = f" y {len(lista) - maximo} más" if len(lista) > maximo else ""
                partes.append(f"{varios.format(n=len(lista))}: {', '.join(nombres)}{resto}")
        return ". ".join(partes) if partes else f"Los {self.comprobados} archivos están completos"


def _para_mostrar(relativa: str) -> str:
    return relativa.replace("/", "\\")


def comprobar(
    raiz: Path, *, hashes: bool = False, entradas: list[Entrada] | None = None
) -> Resultado:
    """Compara la carpeta con el manifiesto. Sin `hashes`, solo existencia y tamaño.

    Solo tamaños son unas 1.200 llamadas a stat: unos milisegundos, vale para
    cada arranque. Con hashes se lee todo el programa (~400 MB, un par de
    segundos): para el instalador, que copia una vez.
    """
    raiz = Path(raiz)
    if entradas is None:
        try:
            entradas = leer_manifiesto(raiz)
        except ManifiestoDanado as danado:
            log.warning("manifiesto dañado en %s: %s", raiz, danado)
            return Resultado(raiz, distintos=[f"_internal/{NOMBRE_MANIFIESTO}"])
    if entradas is None:
        return Resultado(raiz, sin_manifiesto=True)
    resultado = Resultado(raiz, comprobados=len(entradas))
    for e in entradas:
        archivo = raiz / e.ruta
        try:
            tamano = archivo.stat().st_size
        except OSError:
            resultado.faltan.append(e.ruta)
            continue
        if tamano != e.tamano:
            resultado.distintos.append(e.ruta)
            continue
        if hashes:
            try:
                if _sha256(archivo) != e.sha256:
                    resultado.distintos.append(e.ruta)
            except OSError:
                # Existe pero no se puede leer: el antivirus lo tiene, o está bloqueado.
                resultado.distintos.append(e.ruta)
    return resultado


def tamano_total(entradas: list[Entrada]) -> int:
    return sum(e.tamano for e in entradas)


def carpeta_del_paquete() -> Path | None:
    """La carpeta de voziris.exe si se corre congelado; None en desarrollo."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return None


def ruta_demasiado_larga(carpeta: Path) -> bool:
    return len(str(carpeta)) > LIMITE_RUTA


def aviso_de_ruta_larga(carpeta: Path) -> str:
    return (
        f"Voziris está en una carpeta con una ruta demasiado larga ({len(str(carpeta))} "
        f"caracteres; a partir de unos {LIMITE_RUTA} Windows deja de cargar sus "
        "archivos). Muévelo a una carpeta más corta, como Documentos, o instálalo."
    )


# --- por qué no carga algo ---------------------------------------------------------------


def es_bloqueo_de_windows(error: BaseException) -> bool:
    """¿Lo ha bloqueado una directiva de control de aplicaciones? Mira la cadena de causas."""
    visto: BaseException | None = error
    for _ in range(5):
        if visto is None:
            break
        if getattr(visto, "winerror", None) in ERRORES_DE_BLOQUEO:
            return True
        texto = str(visto).lower()
        if any(t in texto for t in _TEXTOS_DE_BLOQUEO):
            return True
        visto = visto.__cause__ or visto.__context__
    return False


def explicacion_de_bloqueo(que: str) -> str:
    return (
        f"Windows no deja cargar {que}: lo ha bloqueado una directiva de control de "
        "aplicaciones. En Windows 11 suele ser «Control inteligente de aplicaciones», "
        "que bloquea los programas sin firma digital que Microsoft no conoce. Abre "
        "Voziris desde la carpeta donde descomprimiste el ZIP; si también lo bloquea, "
        "mira el apartado «Si Windows lo bloquea» del LÉEME."
    )


def explicar_fallo_de_carga(que: str, error: BaseException, raiz: Path | None = None) -> str:
    """Un mensaje para el usuario sobre un import que ha fallado.

    Por orden: si Windows lo ha bloqueado, eso; si la carpeta tiene una ruta
    demasiado larga, eso; si falta o no cuadra un archivo del manifiesto, cuál;
    y si no se sabe más, el error tal cual, que es mejor que nada.

    Args:
        que: lo que no carga, como se le nombra al usuario («PyAV, que decodifica
            el audio»).
        error: la excepción del import.
        raiz: la carpeta de voziris.exe. Por defecto, la del congelado.
    """
    if es_bloqueo_de_windows(error):
        return explicacion_de_bloqueo(que)
    raiz = raiz if raiz is not None else carpeta_del_paquete()
    if raiz is not None:
        if ruta_demasiado_larga(raiz):
            return f"No se pudo cargar {que}. {aviso_de_ruta_larga(raiz)}"
        try:
            resultado = comprobar(raiz)
        except Exception as e:  # noqa: BLE001 — un manifiesto raro no tapa el error de verdad
            log.warning("no se pudo comprobar el paquete: %s", e)
        else:
            if not resultado.ok:
                return f"No se pudo cargar {que}. {resultado.resumen()}. {POR_QUE_FALTAN}"
    return f"No se pudo cargar {que} ({type(error).__name__}: {_en_una_linea(error)})"


def _en_una_linea(error: BaseException, maximo: int = 300) -> str:
    """El error sin el sermón: numpy, si no carga, suelta veinte líneas en inglés.

    Lo útil está en «Original error was: …» cuando lo hay; si no, en la
    primera línea con texto.
    """
    lineas = [ln.strip() for ln in str(error).splitlines() if ln.strip()]
    for ln in lineas:
        if ln.lower().startswith("original error was:"):
            return ln.split(":", 1)[1].strip()[:maximo]
    return (lineas[0] if lineas else repr(error))[:maximo]


# --- Control inteligente de aplicaciones y la marca de Internet --------------------------


def control_inteligente() -> str | None:
    """«activado», «evaluación», «desactivado», o None si no se sabe (no es Windows 11).

    Se lee de HKLM sin administrador. En evaluación, Windows observa y puede
    activarlo por su cuenta más adelante.
    """
    if sys.platform != "win32":
        return None
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\CI\Policy"
        ) as k:
            valor, _ = winreg.QueryValueEx(k, "VerifiedAndReputablePolicyState")
    except OSError:
        return None
    return {0: "desactivado", 1: "activado", 2: "evaluación"}.get(valor)


def tiene_marca_de_internet(archivo: Path) -> bool:
    return os.path.exists(f"{archivo}:Zone.Identifier")


def quitar_marca_de_internet(archivo: Path) -> bool:
    """Borra el flujo `Zone.Identifier`. True si lo tenía."""
    try:
        os.remove(f"{archivo}:Zone.Identifier")
    except FileNotFoundError:
        return False
    except OSError as e:
        log.warning("no se pudo quitar la marca de Internet de %s: %s", archivo, e)
        return False
    return True


def quitar_marcas_de_internet(carpeta: Path) -> int:
    """A todos los archivos de la carpeta. Devuelve a cuántos se les quitó."""
    if sys.platform != "win32":
        return 0
    quitadas = 0
    for archivo in Path(carpeta).rglob("*"):
        if archivo.is_file() and quitar_marca_de_internet(archivo):
            quitadas += 1
    return quitadas


# --- la autoprueba: voziris.exe --comprobar ------------------------------------------------

MODULOS: tuple[tuple[str, str], ...] = (
    ("av", "PyAV, que decodifica grabaciones y vídeos"),
    ("numpy", "numpy"),
    ("onnxruntime", "el motor de voz (onnxruntime)"),
    ("sherpa_onnx", "la separación de hablantes (sherpa-onnx)"),
    ("soundfile", "soundfile"),
    ("sounddevice", "el acceso al micrófono (PortAudio)"),
    ("pystray", "el icono de la bandeja (pystray)"),
    ("PIL.Image", "Pillow"),
    ("win32com.client", "pywin32, que crea los accesos directos"),
    ("tkinter", "las ventanas (Tk)"),
)
"""Lo que se importa en algún momento y trae código nativo. Si uno no carga, algo falla."""


def autoprueba(
    raiz: Path | None = None,
    pausar_volcados: Callable[[], AbstractContextManager[object]] | None = None,
) -> dict[str, Any]:
    """Prueba lo que el programa necesitará, sin ventanas: el informe de `--comprobar`.

    Revisa el paquete contra el manifiesto, importa cada módulo con código
    nativo, decodifica un audio de prueba con PyAV y crea un acceso directo
    en una carpeta temporal, que es lo que hace el instalador. Nunca lanza.

    Args:
        raiz: la carpeta de voziris.exe; por defecto, la del congelado.
        pausar_volcados: apaga faulthandler mientras se crea el acceso directo.
            COM provoca violaciones de acceso que él mismo resuelve, y
            faulthandler las anotaría en voziris-fallos.log como fallos graves.
    """
    from voziris import __version__

    raiz = raiz if raiz is not None else carpeta_del_paquete()
    pruebas: list[dict[str, Any]] = []

    def anotar(nombre: str, ok: bool, detalle: str, bloqueado: bool = False) -> None:
        pruebas.append({"nombre": nombre, "ok": ok, "detalle": detalle, "bloqueado": bloqueado})
        (log.info if ok else log.error)("comprobar %s: %s", nombre, detalle)

    if raiz is not None:
        r = comprobar(raiz)
        if r.sin_manifiesto:
            anotar("paquete", True, "sin manifiesto: no se puede comprobar archivo por archivo")
        else:
            anotar("paquete", r.ok, r.resumen() + ("" if r.ok else f". {POR_QUE_FALTAN}"))
            # Sin el consejo: el instalador da el suyo, que en la copia es otro.
            pruebas[-1]["resumen"] = r.resumen()
        largo = ruta_demasiado_larga(raiz)
        anotar("ruta", not largo,
               aviso_de_ruta_larga(raiz) if largo else f"{len(str(raiz))} caracteres")

    for modulo, que in MODULOS:
        try:
            cargado = importlib.import_module(modulo)
        except Exception as e:  # noqa: BLE001 — ImportError, OSError, lo que sea: se cuenta
            log.error("no carga %s", modulo, exc_info=True)
            anotar(modulo, False, explicar_fallo_de_carga(que, e, raiz), es_bloqueo_de_windows(e))
        else:
            anotar(modulo, True, str(getattr(cargado, "__version__", "") or "carga"))

    if all(p["ok"] for p in pruebas if p["nombre"] in ("av", "numpy")):
        anotar("decodificar", *_probar_decodificar())
    anotar("acceso directo", *_probar_acceso_directo(pausar_volcados))

    return {
        "version": __version__,
        "ejecutable": sys.executable,
        "carpeta": str(raiz) if raiz is not None else None,
        "control_inteligente": control_inteligente(),
        "pruebas": pruebas,
        "bloqueado": any(p["bloqueado"] for p in pruebas),
        "ok": all(p["ok"] for p in pruebas),
    }


def _probar_decodificar() -> tuple[bool, str]:
    """Un cuarto de segundo de tono en WAV, de vuelta por PyAV como cualquier grabación."""
    import tempfile

    try:
        import numpy as np
        import soundfile

        from voziris.archivos import decodificar

        with tempfile.TemporaryDirectory(prefix="voziris-comprobar-") as carpeta:
            wav = Path(carpeta) / "tono.wav"
            t = np.arange(4000, dtype=np.float32) / 16_000
            soundfile.write(wav, 0.3 * np.sin(2 * np.pi * 440 * t), 16_000)
            audio, _ = decodificar(wav)
        if abs(len(audio.muestras) - 4000) > 400:
            return False, f"salen {len(audio.muestras)} muestras en vez de 4000"
        return True, f"{len(audio.muestras)} muestras"
    except Exception as e:  # noqa: BLE001
        log.error("la prueba de decodificar falló", exc_info=True)
        return False, f"{type(e).__name__}: {_en_una_linea(e)}"


def _probar_acceso_directo(
    pausar_volcados: Callable[[], AbstractContextManager[object]] | None,
) -> tuple[bool, str]:
    if sys.platform != "win32":
        return True, "solo en Windows"
    import tempfile

    try:
        from voziris.ui.bandeja import crear_acceso_directo

        with tempfile.TemporaryDirectory(prefix="voziris-comprobar-") as carpeta:
            acceso = Path(carpeta) / "prueba.lnk"
            with (pausar_volcados or contextlib.nullcontext)():
                crear_acceso_directo(acceso, sys.executable, "", carpeta)
            if not acceso.is_file():
                return False, "WScript.Shell no creó el acceso directo"
        return True, "creado y borrado"
    except Exception as e:  # noqa: BLE001
        log.error("la prueba del acceso directo falló", exc_info=True)
        return False, f"{type(e).__name__}: {_en_una_linea(e)}"


def comprobar_desde_argv(argv: list[str]) -> int:
    """`voziris.exe --comprobar [--informe x.json]`, sin pasar por argparse ni por __main__."""
    informe = None
    if "--informe" in argv and argv.index("--informe") + 1 < len(argv):
        informe = Path(argv[argv.index("--informe") + 1])
    return ejecutar_comprobacion(informe)


def ejecutar_comprobacion(informe: Path | None) -> int:
    """Lo que hace `--comprobar`: la autoprueba y su informe. Nunca lanza ni abre ventanas.

    La lanza otro proceso (el instalador, el empaquetado, el diagnóstico) que
    se queda esperando el informe: pase lo que pase, se escribe uno. Los
    volcados de faulthandler van junto al informe, y no a la carpeta que se
    comprueba, que puede ser una copia a medio instalar. Devuelve 0 si todo
    carga, 1 si no y 2 si no se pudo escribir el informe.
    """
    import faulthandler

    propio: Any = None
    if informe is not None and not faulthandler.is_enabled():
        try:
            propio = open(informe.parent / "voziris-fallos.log", "a", encoding="utf-8")  # noqa: SIM115
            faulthandler.enable(propio, all_threads=True)
        except OSError:
            propio = None

    @contextlib.contextmanager
    def pausa() -> Iterator[None]:
        if propio is not None:
            faulthandler.disable()
        try:
            yield
        finally:
            if propio is not None:
                faulthandler.enable(propio, all_threads=True)

    try:
        resultado = autoprueba(pausar_volcados=pausa)
    except BaseException as e:  # noqa: BLE001 — quien espera necesita un informe, no una traza
        log.exception("la autoprueba falló")
        bloqueado = es_bloqueo_de_windows(e)
        resultado = {
            "pruebas": [{"nombre": "autoprueba", "ok": False, "bloqueado": bloqueado,
                         "detalle": f"{type(e).__name__}: {_en_una_linea(e)}"}],
            "control_inteligente": control_inteligente(),
            "bloqueado": bloqueado,
            "ok": False,
        }
    finally:
        if propio is not None:
            faulthandler.disable()
            propio.close()
    with contextlib.suppress(Exception):  # sin consola, o una que no sabe escribir «→»
        print(informe_a_texto(resultado))
    if informe is not None:
        try:
            informe.write_text(json.dumps(resultado, ensure_ascii=False, indent=1),
                               encoding="utf-8")
        except OSError as e:
            log.error("no se pudo escribir el informe en %s: %s", informe, e)
            return 2
    return 0 if resultado["ok"] else 1


def informe_a_texto(informe: dict[str, Any]) -> str:
    """Para el diagnóstico: una línea por prueba."""
    lineas = [
        f"  {'bien' if p['ok'] else 'MAL '}  {p['nombre']}: {p['detalle']}"
        for p in informe.get("pruebas", [])
    ]
    estado = informe.get("control_inteligente")
    if estado:
        lineas.append(f"  Control inteligente de aplicaciones: {estado}")
    return "\n".join(lineas)


def fallos_del_informe(informe: dict[str, Any]) -> list[str]:
    return [p["detalle"] for p in informe.get("pruebas", []) if not p["ok"]]


def comprobar_en_otro_proceso(orden: list[str], espera_s: float = 180.0) -> dict[str, Any]:
    """Lanza `orden + ["--comprobar", "--informe", tmp]` y devuelve su informe.

    En otro proceso porque es lo que se quiere probar: que ESE ejecutable
    arranque y cargue lo suyo. Si Windows no lo deja arrancar, si muere o si
    no contesta, el informe lo dice con `ok` False y una prueba «arranque»
    que explica por qué.
    """
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory(prefix="voziris-comprobar-") as carpeta:
        ruta = Path(carpeta) / "informe.json"
        try:
            proceso = subprocess.run(  # noqa: S603 — orden construida por el programa
                [*orden, "--comprobar", "--informe", str(ruta)],
                capture_output=True, timeout=espera_s,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired:
            return _informe_de_arranque(
                f"voziris.exe no terminó la comprobación en {espera_s:.0f} s"
            )
        except OSError as e:
            if es_bloqueo_de_windows(e):
                return _informe_de_arranque(explicacion_de_bloqueo("voziris.exe"), bloqueado=True)
            return _informe_de_arranque(f"voziris.exe no arranca: {e}")
        try:
            informe: dict[str, Any] = json.loads(ruta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            with contextlib.suppress(OSError):
                volcado = (Path(carpeta) / "voziris-fallos.log").read_text("utf-8", "replace")
                log.error("la comprobación murió; su voziris-fallos.log:\n%s", volcado[-4000:])
            salida = (proceso.stderr or b"").decode("utf-8", "replace").strip()[-500:]
            texto = f"voziris.exe terminó con el código {proceso.returncode} sin dejar informe"
            return _informe_de_arranque(texto + (f": {salida}" if salida else ""))
    return informe


def _informe_de_arranque(detalle: str, *, bloqueado: bool = False) -> dict[str, Any]:
    return {
        "pruebas": [{"nombre": "arranque", "ok": False, "detalle": detalle,
                     "bloqueado": bloqueado}],
        "control_inteligente": control_inteligente(),
        "bloqueado": bloqueado,
        "ok": False,
    }
