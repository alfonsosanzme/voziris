#!/usr/bin/env python3
"""Prueba el MSI sin instalar nada (VOZ-88): lo abre en silencio y mira lo que deja.

    python tools/probar_msi.py RUTA/voziris-<versión>-win64.msi

Lo abre con `msiexec /qn LANZAR=0`: Windows Installer copia el paquete a su
carpeta temporal y NO arranca el instalador de Voziris. No se abre ninguna
ventana y no se tocan la Voziris instalada, el menú Inicio ni «Aplicaciones
instaladas». Comprueba que:

  - msiexec termina bien y deja el paquete donde dice `build/voziris.wxs`;
  - lo que deja es el paquete entero: el manifiesto, con sus hashes;
  - ningún archivo lleva la marca de «descargado de Internet», aunque el MSI
    la lleve (se le pone a una copia, como si viniera del navegador);
  - `voziris.exe --comprobar` carga todo desde esa carpeta;
  - Windows Installer no ha apuntado el producto en ningún sitio.

Al terminar borra la carpeta y la copia. Sale con 0 si todo cuadra y con 1 si no.

Lo que NO prueba: el asistente con sus ventanas, ni el relevo al instalador
(`voziris.exe --instalar`). Eso se hace a mano: ver docs/DESPLIEGUE.md.
"""

from __future__ import annotations

import argparse
import ctypes
import importlib.util
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType

RAIZ = Path(__file__).resolve().parents[1]
ESPERA_MSIEXEC_S = 900
CLAVES_DESINSTALAR = (
    r"Software\Microsoft\Windows\CurrentVersion\Uninstall",
    r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("msi", type=Path)
    parser.add_argument("--sin-comprobar", action="store_true",
                        help="no arranca voziris.exe --comprobar (tarda hasta un minuto)")
    args = parser.parse_args(argv)
    msi: Path = args.msi.resolve()
    if sys.platform != "win32" or not msi.is_file():
        print(f"No está {msi}, o esto no es Windows.", file=sys.stderr)
        return 1

    sys.path.insert(0, str(RAIZ / "src"))
    from voziris import actualizaciones, integridad

    hacer_msi = _cargar("hacer_msi")
    propiedades = dict(hacer_msi.leer_tabla(msi, "SELECT `Property`, `Value` FROM `Property`", 2))
    codigo, version = propiedades["ProductCode"], propiedades["ProductVersion"]

    trabajo = msi.parent / "msi-prueba"
    shutil.rmtree(trabajo, ignore_errors=True)
    trabajo.mkdir(parents=True)
    copia = trabajo / msi.name
    shutil.copyfile(msi, copia)
    # Como recién descargado: la marca de la zona «Internet».
    with open(f"{copia}:Zone.Identifier", "w", encoding="ascii", newline="\r\n") as marca:
        marca.write("[ZoneTransfer]\nZoneId=3\n")
    registro = trabajo / "msiexec.log"

    fallos: list[str] = []
    inicio = time.monotonic()
    proceso = subprocess.Popen(  # noqa: S603 — orden construida aquí
        ["msiexec.exe", "/i", str(copia), "/qn", "LANZAR=0", "/l*v", str(registro)]
    )
    try:
        salida = proceso.wait(timeout=ESPERA_MSIEXEC_S)
    except subprocess.TimeoutExpired:
        proceso.kill()
        print(f"msiexec no ha terminado en {ESPERA_MSIEXEC_S} s.", file=sys.stderr)
        return 1
    print(f"msiexec: salida {salida}, {time.monotonic() - inicio:.0f} s")
    # La carpeta lleva el PID de quien lanza la instalación: ver build/voziris.wxs.
    tanda = actualizaciones.carpeta_de_descargas() / f"msi-{version}-{proceso.pid}"
    paquete = tanda / "voziris"
    try:
        if salida != 0:
            fallos.append(f"msiexec ha salido con {salida}: mira {registro}")
        elif not (paquete / "voziris.exe").is_file():
            fallos.append(f"no ha dejado el paquete en {paquete}")
        else:
            print(f"paquete en {paquete}")
            fallos += _mirar_paquete(paquete, integridad, comprobar=not args.sin_comprobar)
        fallos += _rastros(codigo)
    finally:
        shutil.rmtree(tanda, ignore_errors=True)
        if tanda.exists():
            fallos.append(f"no se ha podido borrar {tanda}")
        if not fallos:
            shutil.rmtree(trabajo, ignore_errors=True)
    if fallos:
        print("NO CUADRA:", *fallos, sep="\n  ", file=sys.stderr)
        return 1
    print("Todo cuadra: el MSI deja el paquete entero, sin marcas y sin registrarse.")
    return 0


def _mirar_paquete(paquete: Path, integridad: ModuleType, *, comprobar: bool) -> list[str]:
    fallos: list[str] = []
    resultado = integridad.comprobar(paquete, hashes=True)
    if resultado.sin_manifiesto or not resultado.ok:
        fallos.append(f"el paquete no cuadra con su manifiesto: {resultado.resumen(5)}")
    else:
        print(f"manifiesto: {resultado.comprobados} archivos, con sus hashes")
    marcados = [p for p in paquete.rglob("*") if p.is_file() and _tiene_marca(p)]
    if marcados:
        fallos.append(f"{len(marcados)} archivos llevan la marca de Internet, como {marcados[0]}")
    else:
        print("ningún archivo lleva la marca de Internet")
    if comprobar:
        informe = integridad.comprobar_en_otro_proceso([str(paquete / "voziris.exe")], espera_s=300)
        if informe.get("ok"):
            print("voziris.exe --comprobar: carga todo")
        else:
            fallos.append("voziris.exe --comprobar falla:\n" + integridad.informe_a_texto(informe))
    return fallos


def _tiene_marca(ruta: Path) -> bool:
    """¿Existe el flujo `Zone.Identifier`? Abrirlo es la forma de saberlo, mida lo que mida."""
    try:
        with open(f"{ruta}:Zone.Identifier", "rb"):
            return True
    except OSError:
        return False


def _rastros(codigo: str) -> list[str]:
    """Lo que Windows Installer sabría del producto. No tiene que saber nada."""
    import winreg

    fallos: list[str] = []
    estado = ctypes.WinDLL("msi").MsiQueryProductStateW(codigo)
    if estado != -1:  # INSTALLSTATE_UNKNOWN
        fallos.append(f"Windows Installer conoce el producto {codigo} (estado {estado})")
    for raiz, nombre in ((winreg.HKEY_CURRENT_USER, "HKCU"), (winreg.HKEY_LOCAL_MACHINE, "HKLM")):
        for clave in CLAVES_DESINSTALAR:
            try:
                winreg.CloseKey(winreg.OpenKey(raiz, f"{clave}\\{codigo}"))
            except OSError:
                continue
            entrada = f"{nombre}\\{clave}\\{codigo}"
            fallos.append(f"hay una entrada en «Aplicaciones instaladas»: {entrada}")
    if not fallos:
        print("Windows Installer no ha apuntado nada")
    return fallos


def _cargar(nombre: str) -> ModuleType:
    """Otra herramienta de esta carpeta, que no es un paquete."""
    spec = importlib.util.spec_from_file_location(nombre, Path(__file__).with_name(f"{nombre}.py"))
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


if __name__ == "__main__":
    raise SystemExit(main())
