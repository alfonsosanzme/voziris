#!/usr/bin/env python3
"""Empaqueta `build/dist/voziris/` en el ZIP portable y en el instalador MSI, con sus SHA-256.

Uso, desde la raíz del repositorio y después de PyInstaller (ver docs/DESPLIEGUE.md):

    python tools/empaquetar.py [--dist CARPETA] [--sin-msi]

Deja `voziris-<versión>-win64.zip`, `voziris-<versión>-win64.msi` y
`SHA256SUMS.txt` junto a la carpeta, y escribe los hashes en
`docs/notas-release.md`. Antes de comprimir:

  - Crea `Instalar Voziris.cmd` junto al ejecutable y copia la guía
    (`docs/LEEME-instalar.html`).
  - Escribe `_internal/voziris-versiones.txt` (qué versión de cada paquete
    lleva) y `_internal/voziris-manifiesto.txt` (tamaño y sha256 de cada
    archivo), con el que el instalador y el propio programa dicen qué falta.
  - Se niega a seguir si faltan los runtimes de Visual C++ o las guías, si
    alguna ruta del paquete es tan larga que «Extraer todo» se la saltaría, o
    si `voziris.exe --comprobar` no carga todo (VOZ-81).

El MSI (VOZ-88) se hace del ZIP ya terminado, con `tools/hacer_msi.py`: lleva lo
mismo, y al abrirlo arranca el mismo instalador que `Instalar Voziris.cmd`.

Lo que sea del usuario (`config.toml`, `modelos/`, `historial/`, los logs)
NO entra ni en el ZIP ni en el MSI.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import re
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from types import ModuleType
from typing import Any

RAIZ = Path(__file__).resolve().parents[1]
DIST = RAIZ / "build" / "dist" / "voziris"
GUIA = RAIZ / "docs" / "LEEME-instalar.html"
NOMBRE_GUIA = "LÉEME - Instalar Voziris.html"
GUIAS_DENTRO = ("LEEME-instalar.html", "GUIA-api.html")
"""Las que `build/voziris.spec` mete en `_internal/docs/` (las de `config.GUIA_*`)."""
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
  echo y en voziris-instalar.log, en esta misma carpeta.
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
    parser.add_argument("--sin-msi", action="store_true",
                        help="solo el ZIP: no hace el instalador MSI (que pide WiX)")
    args = parser.parse_args(argv)
    dist: Path = args.dist.resolve()

    sys.path.insert(0, str(RAIZ / "src"))
    from voziris import __version__, integridad

    if not (dist / "voziris.exe").exists():
        print(f"No hay ejecutable en {dist}: ejecuta antes PyInstaller.", file=sys.stderr)
        return 1
    hacer_msi = None if args.sin_msi else _herramienta("hacer_msi")
    if hacer_msi is not None:
        try:  # antes de nada: que no falte WiX después de minutos de comprobaciones
            hacer_msi.comprobar_wix(hacer_msi.buscar_wix())
        except hacer_msi.MsiFallido as e:
            print(f"No se puede hacer el MSI: {e}", file=sys.stderr)
            return 1
    python_dll = _dll_de_python(dist)
    problemas = (_faltan_runtimes(dist) + _sobra_setuptools(dist) + _faltan_guias(dist)
                 + _rutas_largas(dist, integridad.archivos_del_programa(dist)))
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
        (dist / NOMBRE_GUIA).write_text(
            guia_junto_al_exe(GUIA.read_text(encoding="utf-8")), encoding="utf-8", newline="\n"
        )
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
    paquetes = [zip_path]
    if hacer_msi is not None:
        try:
            paquetes.append(hacer_msi.construir(zip_path))
        except hacer_msi.MsiFallido as e:
            print(f"No se ha hecho el MSI: {e}", file=sys.stderr)
            return 1
    sumas = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paquetes}
    # El ZIP, el primero: es la línea que busca «Actualizar» (actualizaciones._hash_publicado).
    # Con LF: con CRLF, `sha256sum -c` (Linux, macOS, Git Bash) da FAILED con el hash bueno.
    (dist.parent / "SHA256SUMS.txt").write_text(
        "".join(f"{h}  {nombre}\n" for nombre, h in sumas.items()), encoding="ascii", newline="\n"
    )

    notas = RAIZ / "docs" / "notas-release.md"
    if notas.exists():
        notas.write_text(
            poner_hashes(notas.read_text(encoding="utf-8"), sumas), encoding="utf-8", newline="\n"
        )
    for p in paquetes:
        print(f"{p.name}: {p.stat().st_size // 2**20} MB · SHA256 {sumas[p.name]}")
    return 0


def poner_hashes(notas: str, sumas: dict[str, str]) -> str:
    """Las notas con el hash de cada paquete bajo su línea `SHA-256  voziris-<versión>-win64.<ext>`.

    Busca la línea por la extensión, no por la versión: el nombre lo pone quien
    escribe las notas (docs/DESPLIEGUE.md, paso 2). Si la línea no está, no avisa.
    """
    for nombre, h in sumas.items():
        extension = re.escape(Path(nombre).suffix)
        linea = re.compile(rf"(SHA-256  voziris-[\d.]+-win64{extension}\n)[0-9a-f]{{64}}")
        notas = linea.sub(rf"\g<1>{h}", notas)  # el hash es hexadecimal: nada que escapar
    return notas


def guia_junto_al_exe(html: str) -> str:
    """La guía tal como va junto a `voziris.exe`: sus enlaces a las otras guías, bien puestos.

    En el repositorio y en `_internal/docs/` las guías son vecinas y se enlazan
    por su nombre. La copia que va en la raíz del ZIP está dos carpetas más
    arriba (VOZ-88).
    """
    for nombre in GUIAS_DENTRO:
        html = html.replace(f'href="{nombre}', f'href="_internal/docs/{nombre}')
    return html


def _herramienta(nombre: str) -> ModuleType:
    """Otra herramienta de esta carpeta, que no es un paquete de Python."""
    spec = importlib.util.spec_from_file_location(nombre, Path(__file__).with_name(f"{nombre}.py"))
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _faltan_guias(dist: Path) -> list[str]:
    """Sin ellas, «Ayuda» y la pregunta del final de la instalación no abren nada (VOZ-88)."""
    return [
        f"falta _internal/docs/{nombre}: compila con el build/voziris.spec de esta versión"
        for nombre in GUIAS_DENTRO
        if not (dist / "_internal" / "docs" / nombre).is_file()
    ]


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


def _sobra_setuptools(dist: Path) -> list[str]:
    """Con él, cualquier Voziris viva impide cambiar `_internal` al instalar (VOZ-85)."""
    if (dist / "_internal" / "setuptools").exists():
        return ["setuptools va en _internal: mira los excludes de build/voziris.spec"]
    return []


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
