#!/usr/bin/env python3
"""Hace `voziris-<versión>-win64.msi`, el instalador de un solo archivo, a partir del ZIP (VOZ-88).

Lo llama `tools/empaquetar.py` al terminar el ZIP. A mano, sobre un ZIP ya hecho:

    python tools/hacer_msi.py RUTA/voziris-<versión>-win64.zip

El MSI lleva lo que hay en el ZIP y nada más: se extrae el ZIP y se empaqueta
eso. Qué hace al abrirlo, y por qué no es un MSI corriente, está en la cabecera
de `build/voziris.wxs`. Antes de darlo por bueno se comprueba que lleva los
mismos archivos, que no se registra en Windows y que arranca el instalador.

Necesita WiX 5 y su extensión de interfaz. Se instalan sin administrador:

    dotnet tool install --global wix --version 5.0.2
    wix extension add --global WixToolset.UI.wixext/5.0.2
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath

RAIZ = Path(__file__).resolve().parents[1]
RECETA = RAIZ / "build" / "voziris.wxs"
TEXTOS = RAIZ / "build" / "voziris.es-es.wxl"
CONDICIONES = RAIZ / "CONDICIONES.md"
ICONO = RAIZ / "assets" / "voziris.ico"
NOMBRE_ZIP = re.compile(r"voziris-(\d+\.\d+\.\d+)-win64\.zip")
"""Tres números: Windows Installer no admite otra forma de versión."""
WIX_MAYOR = 5
EXTENSION_UI = "WixToolset.UI.wixext"
COMO_INSTALAR_WIX = (
    "Hace falta WiX 5 con su extensión de interfaz (no piden administrador):\n"
    "    dotnet tool install --global wix --version 5.0.2\n"
    "    wix extension add --global WixToolset.UI.wixext/5.0.2\n"
    "Para empaquetar solo el ZIP: tools/empaquetar.py --sin-msi"
)
ACCION = "LanzarInstalador"
SIN_REGISTRO = (
    "ProcessComponents", "RegisterUser", "RegisterProduct", "PublishFeatures", "PublishProduct",
)
"""Las acciones que apuntarían el MSI en Windows Installer. No van: ver `build/voziris.wxs`."""
ACENTO = (0x3D, 0x5A, 0x80)
"""El azul de la guía (`--acento` en docs/LEEME-instalar.html)."""


class MsiFallido(Exception):
    """No se ha hecho el MSI. El mensaje dice qué falta o qué no cuadra."""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("zip", type=Path, help="el voziris-<versión>-win64.zip ya hecho")
    parser.add_argument("--salida", type=Path,
                        help="dónde dejar el MSI (por defecto, junto al ZIP)")
    args = parser.parse_args(argv)
    try:
        msi = construir(args.zip, args.salida)
    except MsiFallido as e:
        print(f"No se ha hecho el MSI: {e}", file=sys.stderr)
        return 1
    print(f"{msi.name}: {msi.stat().st_size // 2**20} MB · SHA256 {sha256(msi)}")
    return 0


def construir(zip_: Path, salida: Path | None = None) -> Path:
    """Hace el MSI del ZIP y lo comprueba. Devuelve su ruta.

    Raises:
        MsiFallido: falta WiX, el ZIP no es de Voziris, WiX da un error o el
            MSI no lleva lo que debe.
    """
    zip_ = zip_.resolve()
    version = version_del_zip(zip_.name)
    if not zip_.is_file():
        raise MsiFallido(f"no está {zip_}")
    salida = (salida or zip_.parent).resolve()
    wix = buscar_wix()
    comprobar_wix(wix)
    sys.path.insert(0, str(RAIZ / "src"))
    from voziris import instalador

    trabajo = salida / "msi-trabajo"
    shutil.rmtree(trabajo, ignore_errors=True)
    if trabajo.exists():
        raise MsiFallido(f"no se puede vaciar {trabajo}: ¿hay algo abierto dentro?")
    trabajo.mkdir(parents=True)
    try:
        paquete = extraer(zip_, trabajo / "paquete")
        condiciones = trabajo / "condiciones.rtf"
        condiciones.write_text(
            rtf_de_condiciones(CONDICIONES.read_text(encoding="utf-8")),
            encoding="ascii", newline="\r\n",
        )
        dialogo, banner = dibujar_imagenes(trabajo)
        msi = salida / f"voziris-{version}-win64.msi"
        msi.unlink(missing_ok=True)
        variables = {
            "Version": version, "Editor": instalador.EDITOR, "Paquete": str(paquete),
            "Condiciones": str(condiciones), "Dialogo": str(dialogo), "Banner": str(banner),
        }
        orden = [
            str(wix), "build", str(RECETA), "-arch", "x64", "-culture", "es-ES",
            "-ext", EXTENSION_UI, "-loc", str(TEXTOS),
            # 1008: «la acción X se suprime». Es justo lo que se quiere (SIN_REGISTRO).
            "-sw1008", "-pdbtype", "none", "-intermediatefolder", str(trabajo / "obj"),
            "-o", str(msi),
        ]
        for nombre, valor in variables.items():
            orden += ["-d", f"{nombre}={valor}"]
        print("Haciendo el MSI con WiX (comprime unos 230 MB: tarda un par de minutos)…")
        r = subprocess.run(  # noqa: S603 — orden construida aquí
            orden, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
        )
        salida_wix = (r.stdout + r.stderr).strip()
        if r.returncode != 0 or not msi.is_file():
            raise MsiFallido(f"WiX ha fallado (salida {r.returncode}):\n{salida_wix}")
        if salida_wix:
            print(salida_wix)
        problemas = comprobar_msi(msi, paquete, version)
        if problemas:
            msi.unlink(missing_ok=True)
            raise MsiFallido("el MSI no ha salido como debe:\n  " + "\n  ".join(problemas))
    finally:
        shutil.rmtree(trabajo, ignore_errors=True)
    return msi


def version_del_zip(nombre: str) -> str:
    m = NOMBRE_ZIP.fullmatch(nombre)
    if m is None:
        raise MsiFallido(f"{nombre} no se llama voziris-<X.Y.Z>-win64.zip")
    return m.group(1)


def sha256(ruta: Path) -> str:
    resumen = hashlib.sha256()
    with ruta.open("rb") as f:
        for trozo in iter(lambda: f.read(2**20), b""):
            resumen.update(trozo)
    return resumen.hexdigest()


# --- WiX --------------------------------------------------------------------------------


def buscar_wix() -> Path:
    """`wix.exe`: el del PATH o el que deja `dotnet tool install --global`."""
    en_ruta = shutil.which("wix")
    if en_ruta:
        return Path(en_ruta)
    de_dotnet = Path.home() / ".dotnet" / "tools" / "wix.exe"
    if de_dotnet.is_file():
        return de_dotnet
    raise MsiFallido(f"no se encuentra WiX.\n{COMO_INSTALAR_WIX}")


def comprobar_wix(wix: Path) -> None:
    """Que sea la versión con la que está escrita la receta, y que tenga la extensión."""
    version = _salida_de(wix, "--version")
    if not version.startswith(f"{WIX_MAYOR}."):
        raise MsiFallido(
            f"{wix} es WiX {version or 'desconocido'} y la receta es de WiX {WIX_MAYOR}.\n"
            f"{COMO_INSTALAR_WIX}"
        )
    if not tiene_extension(_salida_de(wix, "extension", "list", "--global")):
        raise MsiFallido(f"a WiX le falta la extensión {EXTENSION_UI} {WIX_MAYOR}.\n"
                         f"{COMO_INSTALAR_WIX}")


def tiene_extension(listado: str) -> bool:
    """¿Sale la extensión de interfaz, sana y de la versión buena, en `wix extension list`?"""
    for linea in listado.splitlines():
        partes = linea.split()
        if (partes[:1] == [EXTENSION_UI] and len(partes) > 1
                and partes[1].startswith(f"{WIX_MAYOR}.") and "damaged" not in linea.lower()):
            return True
    return False


def _salida_de(wix: Path, *argumentos: str) -> str:
    try:
        r = subprocess.run(  # noqa: S603 — orden fija
            [str(wix), *argumentos], capture_output=True, text=True, encoding="utf-8",
            errors="replace", check=False, timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise MsiFallido(f"no se puede ejecutar {wix}: {e}") from e
    return r.stdout.strip()


# --- lo que entra -----------------------------------------------------------------------


def extraer(zip_: Path, destino: Path) -> Path:
    """Saca el ZIP en `destino` y devuelve su carpeta `voziris/`. Nada fuera de ella."""
    try:
        with zipfile.ZipFile(zip_) as archivo:
            for nombre in archivo.namelist():
                ruta = PurePosixPath(nombre)
                if ruta.is_absolute() or ".." in ruta.parts or ruta.parts[:1] != ("voziris",):
                    raise MsiFallido(f"el ZIP trae una ruta que no es de Voziris: {nombre}")
            archivo.extractall(destino)
    except (zipfile.BadZipFile, OSError) as e:
        raise MsiFallido(f"no se puede extraer {zip_.name}: {e}") from e
    paquete = destino / "voziris"
    if not (paquete / "voziris.exe").is_file() or not (paquete / "_internal").is_dir():
        raise MsiFallido(f"{zip_.name} no trae voziris.exe y _internal")
    return paquete


def rtf_de_condiciones(markdown: str) -> str:
    """`CONDICIONES.md` en el RTF que enseña el asistente antes de instalar.

    Entiende lo que ese archivo usa: títulos (`#`, `##`), párrafos, listas con
    `- `, `**negrita**` y `` `código` ``. Sale en ASCII puro: cada letra con
    tilde va como escape, que es lo único que el cuadro de Windows Installer
    lee igual en todos los equipos.
    """
    partes = [
        r"{\rtf1\ansi\ansicpg1252\deff0\uc1{\fonttbl{\f0\fswiss\fcharset0 Segoe UI;}}",
        r"\viewkind4\f0\fs18",
    ]
    for linea in markdown.splitlines():
        linea = linea.strip()
        if not linea:
            continue
        if linea.startswith("# "):
            partes.append(r"{\pard\sa140\b\fs24 " + _rtf(linea[2:]) + r"\par}")
        elif linea.startswith("## "):
            partes.append(r"{\pard\sb180\sa80\b\fs20 " + _rtf(linea[3:]) + r"\par}")
        elif linea.startswith("- "):
            partes.append(
                r"{\pard\fi-200\li280\sa80\tx280 \'95\tab " + _rtf(linea[2:]) + r"\par}"
            )
        else:
            partes.append(r"{\pard\sa100 " + _rtf(linea) + r"\par}")
    partes.append("}")
    return "\n".join(partes) + "\n"


def _rtf(texto: str) -> str:
    """Un trozo de Markdown de una línea, en RTF ASCII."""
    salida: list[str] = []
    for letra in texto:
        if letra in "\\{}":
            salida.append("\\" + letra)
        elif ord(letra) < 128:
            salida.append(letra)
        else:
            try:
                salida.append(f"\\'{letra.encode('cp1252')[0]:02x}")
            except UnicodeEncodeError:  # fuera de cp1252: el número Unicode y un «?» de respaldo
                numero = ord(letra) if ord(letra) < 32768 else ord(letra) - 65536
                salida.append(f"\\u{numero}?")
    rtf = "".join(salida)
    rtf = re.sub(r"\*\*(.+?)\*\*", r"{\\b \1}", rtf)
    return re.sub(r"`(.+?)`", r"\1", rtf)


def dibujar_imagenes(trabajo: Path) -> tuple[Path, Path]:
    """El lateral de los diálogos (493×312) y la banda del de progreso (493×58), en BMP.

    Son las medidas de WixUI. Sobre la parte blanca escribe el asistente, en
    negro: por eso el color va solo en el lateral izquierdo, donde no hay texto.
    """
    from PIL import Image, ImageDraw, ImageFont

    blanco = (255, 255, 255)
    with Image.open(ICONO) as icono:
        silueta = icono.ico.getimage(max(icono.ico.sizes())).convert("RGBA").getchannel("A")

    def pegar_microfono(lienzo: Image.Image, lado: int, donde: tuple[int, int],
                        color: tuple[int, int, int]) -> None:
        mascara = silueta.resize((lado, lado), Image.Resampling.LANCZOS)
        lienzo.paste(Image.new("RGB", (lado, lado), color), donde, mascara)

    def fuente(archivo: str, cuerpo: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        try:
            return ImageFont.truetype(archivo, cuerpo)
        except OSError:  # sin Segoe UI (no es Windows): la de serie
            return ImageFont.load_default()

    ancho_lateral = 164
    dialogo = Image.new("RGB", (493, 312), blanco)
    lapiz = ImageDraw.Draw(dialogo)
    lapiz.rectangle((0, 0, ancho_lateral - 1, 311), fill=ACENTO)
    pegar_microfono(dialogo, 84, ((ancho_lateral - 84) // 2, 46), blanco)
    centro = ancho_lateral // 2
    lapiz.text((centro, 166), "Voziris", font=fuente("seguisb.ttf", 27), fill=blanco, anchor="ms")
    lapiz.text((centro, 190), "Dictado por voz", font=fuente("segoeui.ttf", 13),
               fill=(216, 226, 238), anchor="ms")

    banner = Image.new("RGB", (493, 58), blanco)
    pegar_microfono(banner, 38, (443, 10), ACENTO)

    ruta_dialogo, ruta_banner = trabajo / "dialogo.bmp", trabajo / "banner.bmp"
    dialogo.save(ruta_dialogo, "BMP")
    banner.save(ruta_banner, "BMP")
    return ruta_dialogo, ruta_banner


# --- lo que sale ------------------------------------------------------------------------


def comprobar_msi(msi: Path, paquete: Path, version: str) -> list[str]:
    """Lo que el MSI no cumple. Vacía si lleva el paquete entero y se comporta como debe."""
    problemas: list[str] = []
    en_disco = [p for p in paquete.rglob("*") if p.is_file()]
    archivos = leer_tabla(msi, "SELECT `FileName`, `FileSize` FROM `File`", 2)
    if len(archivos) != len(en_disco):
        problemas.append(f"lleva {len(archivos)} archivos y el ZIP trae {len(en_disco)}")
    bytes_msi = sum(int(tamano) for _, tamano in archivos)
    bytes_zip = sum(p.stat().st_size for p in en_disco)
    if bytes_msi != bytes_zip:
        problemas.append(f"sus archivos suman {bytes_msi} bytes y los del ZIP, {bytes_zip}")
    nombres = {nombre.split("|")[-1].lower() for nombre, _ in archivos}  # «corto|largo»
    for imprescindible in ("voziris.exe", "voziris-manifiesto.txt"):
        if imprescindible not in nombres:
            problemas.append(f"no lleva {imprescindible}")

    secuencia = dict(
        leer_tabla(msi, "SELECT `Action`, `Sequence` FROM `InstallExecuteSequence`", 2)
    )
    for accion in SIN_REGISTRO:
        if accion in secuencia:
            problemas.append(f"se registraría en Windows Installer: lleva la acción {accion}")
    if ACCION not in secuencia:
        problemas.append(f"no arranca el instalador: falta la acción {ACCION}")
    elif int(secuencia[ACCION]) <= int(secuencia.get("InstallFinalize", "0")):
        problemas.append(f"{ACCION} va antes de terminar de copiar")
    acciones = dict(leer_tabla(msi, "SELECT `Action`, `Target` FROM `CustomAction`", 2))
    orden = acciones.get(ACCION, "")
    if '[PAQUETE]voziris.exe" ' not in orden or not orden.endswith(" --instalar"):
        problemas.append(f"{ACCION} no lanza «voziris.exe --instalar»: {orden!r}")

    propiedades = dict(leer_tabla(msi, "SELECT `Property`, `Value` FROM `Property`", 2))
    if propiedades.get("ProductVersion") != version:
        dice = propiedades.get("ProductVersion")
        problemas.append(f"dice ser la versión {dice} y es la {version}")
    if "ALLUSERS" in propiedades:
        problemas.append("pide instalar para todos los usuarios (ALLUSERS): pediría administrador")
    return problemas


def leer_tabla(msi: Path, consulta: str, columnas: int) -> list[tuple[str, ...]]:
    """Las filas de una consulta sobre la base de datos del MSI, cada valor como texto.

    Con la API de Windows Installer (msi.dll), que va en todos los Windows: sin
    dependencias. Solo lectura.
    """
    if sys.platform != "win32":
        raise MsiFallido("un MSI solo se puede leer en Windows")
    dll = ctypes.WinDLL("msi")
    manejador = ctypes.c_uint32
    dll.MsiOpenDatabaseW.argtypes = (ctypes.c_wchar_p, ctypes.c_void_p, ctypes.POINTER(manejador))
    dll.MsiDatabaseOpenViewW.argtypes = (manejador, ctypes.c_wchar_p, ctypes.POINTER(manejador))
    dll.MsiViewExecute.argtypes = (manejador, manejador)
    dll.MsiViewFetch.argtypes = (manejador, ctypes.POINTER(manejador))
    dll.MsiRecordGetStringW.argtypes = (
        manejador, ctypes.c_uint, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint32),
    )
    dll.MsiCloseHandle.argtypes = (manejador,)

    base, vista, fila = manejador(), manejador(), manejador()
    filas: list[tuple[str, ...]] = []
    if dll.MsiOpenDatabaseW(str(msi), None, ctypes.byref(base)) != 0:
        raise MsiFallido(f"no se puede abrir {msi.name} como base de datos de Windows Installer")
    try:
        if (dll.MsiDatabaseOpenViewW(base, consulta, ctypes.byref(vista)) != 0
                or dll.MsiViewExecute(vista, 0) != 0):
            raise MsiFallido(f"{msi.name} no admite la consulta {consulta!r}")
        try:
            while dll.MsiViewFetch(vista, ctypes.byref(fila)) == 0:
                try:
                    filas.append(tuple(_campo(dll, fila, i) for i in range(1, columnas + 1)))
                finally:
                    dll.MsiCloseHandle(fila)
        finally:
            dll.MsiCloseHandle(vista)
    finally:
        dll.MsiCloseHandle(base)
    return filas


def _campo(dll: ctypes.WinDLL, fila: ctypes.c_uint32, indice: int) -> str:
    largo = ctypes.c_uint32(512)
    for _ in range(2):  # si no cabe (ERROR_MORE_DATA), `largo` vuelve con lo que hace falta
        texto = ctypes.create_unicode_buffer(largo.value + 1)
        largo = ctypes.c_uint32(len(texto))
        if dll.MsiRecordGetStringW(fila, indice, texto, ctypes.byref(largo)) == 0:
            return texto.value
    raise MsiFallido("no se pudo leer un valor del MSI")


if __name__ == "__main__":
    raise SystemExit(main())
