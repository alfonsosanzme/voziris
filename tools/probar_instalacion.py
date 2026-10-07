#!/usr/bin/env python3
"""Instala un ZIP de Voziris en carpetas temporales y comprueba el resultado.

Para antes de publicar una versión: prueba el instalador del código de este
repositorio con el paquete de verdad, sin tocar la instalación real, el menú
Inicio, la carpeta de arranque ni el registro de verdad (usa una clave de
pruebas bajo HKCU y la borra al terminar).

    python tools/probar_instalacion.py <ZIP nuevo> [--anterior <ZIP de otra versión>]

Con `--anterior`, primero simula esa versión instalada con datos del usuario
(config.toml, historial de 500 líneas) y luego instala la nueva encima: los
datos tienen que seguir igual. La carpeta extraída de la nueva trae además su
propio historial de una línea, que es lo que hacía perder el de la instalada
hasta la 0.1.1.

No ejecuta `Instalar Voziris.cmd` ni `voziris.exe --instalar`: no prueba la
ventana de progreso, ni cerrar y reabrir la Voziris abierta, ni el arranque
por el acceso de Inicio. Eso, en una máquina limpia (docs/RELEASE.md §4).

Hay que ejecutarlo con el Python del proyecto y con este repositorio delante
en el `PYTHONPATH` (`$env:PYTHONPATH = "<repo>\\src"`), para probar ESTE
instalador. Sale con 0 si todo cuadra y con 1 si algo no.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

RAIZ_PRUEBAS = r"Software\Voziris-pruebas-instalacion"
LINEAS_HISTORIAL = 500


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("zip", type=Path, help="el ZIP nuevo, voziris-X.Y.Z-win64.zip")
    parser.add_argument("--anterior", type=Path,
                        help="ZIP de una versión anterior: se simula instalada con datos")
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        print("Solo en Windows: usa el registro y los accesos directos.", file=sys.stderr)
        return 1

    import winreg

    import voziris
    from voziris import instalador, integridad

    print(f"instalador: {voziris.__file__} ({voziris.__version__})")
    hkcu = winreg.HKEY_CURRENT_USER
    fallos: list[str] = []

    def comprobar(nombre: str, ok: bool, detalle: str = "") -> None:
        print(f"  {'bien' if ok else 'MAL '}  {nombre}{f': {detalle}' if detalle else ''}")
        if not ok:
            fallos.append(nombre)

    base = Path(tempfile.mkdtemp(prefix="voziris-prueba-"))
    try:
        origen = _extraer(args.zip, base / "nuevo")
        # Como un ZIP descargado sin desbloquear: el exe lleva la marca de Internet.
        with open(f"{origen / 'voziris.exe'}:Zone.Identifier", "w", encoding="utf-8") as f:
            f.write("[ZoneTransfer]\nZoneId=3\n")
        destino = base / "Programs" / "Voziris"
        historial = destino / "historial" / "dictados.jsonl"
        if args.anterior is not None:
            anterior = _extraer(args.anterior, base / "anterior")
            destino.mkdir(parents=True)
            shutil.copy2(anterior / "voziris.exe", destino / "voziris.exe")
            shutil.copytree(anterior / "_internal", destino / "_internal")
            historial.parent.mkdir()
            historial.write_text("{}\n" * LINEAS_HISTORIAL, encoding="utf-8")
            (destino / "config.toml").write_text("# la de la instalada\n", encoding="utf-8")
            (origen / "historial").mkdir()
            (origen / "historial" / "dictados.jsonl").write_text("{}\n", encoding="utf-8")
            print(f"simulada instalada: {_version(destino)}")

        instalador.instalar(
            origen, destino,
            menu_inicio=base / "menu", startup=base / "startup",
            raiz_registro=hkcu, clave_registro=RAIZ_PRUEBAS + r"\Uninstall\Voziris",
            base_menu=RAIZ_PRUEBAS + r"\SystemFileAssociations",
            comprobar_copia=instalador.comprobar_arrancando,
        )
        print(f"instalada en {destino}")
        comprobar("versión", _version(destino) == _version(origen), _version(destino))
        comprobar("programa igual al del ZIP", _huella(destino) == _huella(origen))
        comprobar("manifiesto con hashes", integridad.comprobar(destino, hashes=True).ok)
        marcados = sum(integridad.tiene_marca_de_internet(p)
                       for p in destino.rglob("*") if p.is_file())
        comprobar("sin marca de Internet", marcados == 0, f"{marcados} archivos")
        comprobar("acceso del menú Inicio", (base / "menu" / instalador.ACCESO_MENU).is_file())
        restos = [p.name for p in destino.parent.iterdir() if p.name != destino.name]
        restos += [p.name for p in destino.glob(f"*{instalador.SUFIJO_VIEJO}*")]
        comprobar("sin restos", not restos, ", ".join(restos))
        if args.anterior is not None:
            config = (destino / "config.toml").read_text(encoding="utf-8")
            comprobar("config.toml de la instalada", config == "# la de la instalada\n")
            lineas = historial.read_text(encoding="utf-8").count("\n")
            comprobar("historial de la instalada", lineas == LINEAS_HISTORIAL, f"{lineas} líneas")
    finally:
        instalador.quitar_menu_contextual(raiz=hkcu, base=RAIZ_PRUEBAS + r"\SystemFileAssociations")
        with contextlib.suppress(OSError):
            instalador._borrar_clave(hkcu, RAIZ_PRUEBAS)  # borra también las subclaves
        shutil.rmtree(base, ignore_errors=True)
    print("todo bien" if not fallos else f"FALLA: {', '.join(fallos)}")
    return 1 if fallos else 0


def _extraer(zip_: Path, carpeta: Path) -> Path:
    with zipfile.ZipFile(zip_) as z:
        z.extractall(carpeta)
    return carpeta / "voziris"  # el ZIP lo lleva todo bajo voziris/


def _version(carpeta: Path) -> str:
    r = subprocess.run([str(carpeta / "voziris.exe"), "--version"],
                       capture_output=True, text=True, timeout=120)
    return r.stdout.strip() or f"(sin respuesta, código {r.returncode})"


def _huella(carpeta: Path) -> dict[str, str]:
    """voziris.exe y _internal: ruta → sha256."""
    archivos = [carpeta / "voziris.exe",
                *(p for p in (carpeta / "_internal").rglob("*") if p.is_file())]
    return {p.relative_to(carpeta).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in archivos}


if __name__ == "__main__":
    raise SystemExit(main())
