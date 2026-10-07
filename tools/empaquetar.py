#!/usr/bin/env python3
"""Empaqueta `build/dist/voziris/` en el ZIP portable, con su SHA-256.

Uso, desde la raíz del repositorio y después de PyInstaller (ver docs/RELEASE.md):

    python tools/empaquetar.py [--dist CARPETA]

Deja `voziris-<versión>-win64.zip` y `SHA256SUMS.txt` junto a la carpeta, y
escribe el hash en `docs/notas-release.md`. Antes de comprimir:

  - Crea `Instalar Voziris.cmd` junto al ejecutable y copia la guía
    (`docs/LEEME-instalar.html`).
  - Escribe `_internal/voziris-versiones.txt` (qué versión de cada paquete
    lleva) y `_internal/voziris-manifiesto.txt` (tamaño y sha256 de cada
    archivo), con el que el instalador y el propio programa dicen qué falta.
  - Se niega a seguir si faltan los runtimes de Visual C++, si alguna ruta del
    paquete es tan larga que «Extraer todo» se la saltaría, o si
    `voziris.exe --comprobar` no carga todo (VOZ-81).

Lo que sea del usuario (`config.toml`, `modelos/`, `historial/`, los logs)
NO entra en el ZIP.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parents[1]
DIST = RAIZ / "build" / "dist" / "voziris"
GUIA = RAIZ / "docs" / "LEEME-instalar.html"
NOMBRE_GUIA = "LÉEME - Instalar Voziris.html"
RUNTIMES_VC = ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll", "msvcp140_1.dll")
"""Van dentro: un Windows recién instalado no los trae."""
LIMITE_RUTA = 100
"""Caracteres de una ruta relativa del paquete.

«Extraer todo» del Explorador no admite rutas largas: por encima de 259
caracteres por archivo ofrece Reintentar/Omitir/Cancelar, y quien pulsa
Omitir se queda con un programa a medias (medido en VOZ-81). Con 100 por
dentro quedan 159 para la carpeta donde se extrae.
"""
INSTALAR_CMD = """@echo off
rem Instala Voziris para este usuario: menu Inicio y "Aplicaciones instaladas".
rem No pide administrador. Para volver al modo portable: voziris.exe --desinstalar
if not exist "%~dp0voziris.exe" goto incompleto
if not exist "%~dp0_internal\\{python_dll}" goto incompleto
if not exist "%~dp0_internal\\voziris-manifiesto.txt" goto incompleto
"%~dp0voziris.exe" --instalar
if errorlevel 1 (
  echo.
  echo No se ha instalado. El motivo esta en el aviso que acaba de salir
  echo y en voziris.log, en esta misma carpeta.
  pause
)
goto :eof

:incompleto
echo.
echo A esta carpeta le falta parte de Voziris: el ZIP no se ha extraido entero,
echo o el antivirus ha retirado algun archivo (mira su cuarentena).
echo Vuelve a extraerlo (boton derecho sobre el ZIP, "Extraer todo") en una
echo carpeta nueva, espera a que termine y abre este archivo desde ahi.
echo.
pause
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dist", type=Path, default=DIST,
                        help="la carpeta voziris/ que dejó PyInstaller")
    args = parser.parse_args(argv)
    dist: Path = args.dist.resolve()

    sys.path.insert(0, str(RAIZ / "src"))
    from voziris import __version__, integridad

    if not (dist / "voziris.exe").exists():
        print(f"No hay ejecutable en {dist}: ejecuta antes PyInstaller.", file=sys.stderr)
        return 1
    python_dll = _dll_de_python(dist)
    problemas = _faltan_runtimes(dist) + _rutas_largas(dist, integridad.archivos_del_programa(dist))
    if python_dll is None:
        problemas.append("no hay una sola python3XX.dll en _internal")
    elif python_dll != f"python{sys.version_info.major}{sys.version_info.minor}.dll":
        # voziris-versiones.txt diría los paquetes de otro Python que el del paquete.
        problemas.append(f"el paquete lleva {python_dll} y esto corre con Python "
                         f"{sys.version.split()[0]}: usa el mismo Python que PyInstaller")
    if problemas:
        print("El paquete no se puede repartir así:", *problemas, sep="\n  ", file=sys.stderr)
        return 1

    (dist / "Instalar Voziris.cmd").write_text(
        INSTALAR_CMD.format(python_dll=python_dll), encoding="cp1252", newline="\r\n"
    )
    if GUIA.exists():
        shutil.copyfile(GUIA, dist / NOMBRE_GUIA)
    else:
        print(f"aviso: no está {GUIA}; el ZIP sale sin la guía", file=sys.stderr)
    _escribir_versiones(dist / "_internal" / "voziris-versiones.txt", __version__)
    integridad.escribir_manifiesto(dist)

    print("Comprobando que el paquete carga todo (voziris.exe --comprobar)…")
    informe = integridad.comprobar_en_otro_proceso([str(dist / "voziris.exe")], espera_s=300)
    print(integridad.informe_a_texto(informe))
    if not informe.get("ok"):
        print("El paquete no pasa su propia comprobación: no se hace el ZIP.", file=sys.stderr)
        return 1
    if not integridad.comprobar(dist, hashes=True).ok:  # que la prueba no haya tocado nada
        print("El paquete ha cambiado durante la comprobación.", file=sys.stderr)
        return 1

    zip_path = dist.parent / f"voziris-{__version__}-win64.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archivo:
        for p in _lo_que_va_en_el_zip(dist, integridad):
            archivo.write(p, Path("voziris") / p.relative_to(dist))
    h = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    # Con LF: con CRLF, `sha256sum -c` (Linux, macOS, Git Bash) da FAILED con el hash bueno.
    (dist.parent / "SHA256SUMS.txt").write_text(
        f"{h}  {zip_path.name}\n", encoding="ascii", newline="\n"
    )

    notas = RAIZ / "docs" / "notas-release.md"
    if notas.exists():
        t = notas.read_text(encoding="utf-8")
        t = re.sub(
            r"(SHA-256  voziris-[\d.]+-win64\.zip\n)[0-9a-f]{64}", lambda m: m.group(1) + h, t
        )
        notas.write_text(t, encoding="utf-8", newline="\n")
    print(f"{zip_path.name}: {zip_path.stat().st_size // 2**20} MB · SHA256 {h}")
    return 0


def _lo_que_va_en_el_zip(dist: Path, integridad: Any) -> list[Path]:
    """El programa (lo que dice el manifiesto), el manifiesto, el .cmd y la guía. Nada más.

    Una lista de lo que entra y no de lo que se excluye: si alguien arrancó
    el dist para probarlo, junto al .exe quedan logs rotados
    (`voziris.log.1`), config.toml o modelos/, y no deben viajar.
    """
    archivos = integridad.archivos_del_programa(dist) + [integridad.ruta_manifiesto(dist)]
    archivos += [p for p in (dist / "Instalar Voziris.cmd", dist / NOMBRE_GUIA) if p.exists()]
    return sorted(archivos)


def _dll_de_python(dist: Path) -> str | None:
    """«python313.dll»: la que lleva el paquete, no la del Python que corre esto."""
    dlls = [p.name for p in (dist / "_internal").glob("python3*.dll") if p.name != "python3.dll"]
    return dlls[0] if len(dlls) == 1 else None


def _faltan_runtimes(dist: Path) -> list[str]:
    presentes = {p.name.lower() for p in (dist / "_internal").glob("*.dll")}
    return [f"falta {r} en _internal" for r in RUNTIMES_VC if r not in presentes]


def _rutas_largas(dist: Path, archivos: list[Path]) -> list[str]:
    largas = []
    for p in archivos:
        relativa = str(p.relative_to(dist))
        if len(relativa) > LIMITE_RUTA:
            largas.append(f"ruta de {len(relativa)} caracteres (> {LIMITE_RUTA}): {relativa}")
    return largas


def _escribir_versiones(destino: Path, version: str) -> None:
    """El entorno con el que se compiló, para cuando algo falle en otro equipo.

    Es el `pip freeze` entero del entorno de compilación, no solo lo que va en
    el paquete: incluye herramientas (pytest, ruff…) que PyInstaller no mete.
    Sin rutas del equipo que compila: el paquete se reparte, y `pip freeze`
    escribe las instalaciones editables con su carpeta, nombre de usuario incluido.
    """
    congelado = subprocess.run(
        [sys.executable, "-m", "pip", "freeze", "--all"],
        capture_output=True, text=True, check=False,
    ).stdout
    lineas = [
        re.sub(r" @ file:.*$", " (local)", linea)
        for linea in congelado.splitlines()
        if linea.strip() and not linea.startswith("-e ")
    ]
    destino.write_text(
        f"# Voziris {version} · compilado el {time.strftime('%Y-%m-%d %H:%M')}\n"
        f"# Entorno de compilación (pip freeze), no solo lo que va en el paquete\n"
        f"# Python {sys.version.split()[0]}\n" + "\n".join(lineas) + "\n",
        encoding="utf-8", newline="\n",
    )


if __name__ == "__main__":
    raise SystemExit(main())
