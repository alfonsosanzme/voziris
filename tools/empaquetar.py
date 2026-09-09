#!/usr/bin/env python3
"""Empaqueta `build/dist/voziris/` en el ZIP portable, con su SHA-256.

Uso, desde la raíz del repositorio y después de PyInstaller (ver docs/RELEASE.md):

    python tools/empaquetar.py

Deja `build/dist/voziris-<versión>-win64.zip`, `build/dist/SHA256SUMS.txt`, y
escribe el hash en `docs/notas-release.md`. Antes de comprimir crea
`Instalar Voziris.cmd` junto al ejecutable, para quien prefiera verlo en
Inicio en vez de usarlo portable. Lo que sea del usuario (`config.toml`,
`modelos/`, `historial/`, los logs) NO entra en el ZIP.
"""

from __future__ import annotations

import hashlib
import re
import sys
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
DIST = RAIZ / "build" / "dist" / "voziris"
EXCLUIDOS = {"config.toml", "modelos", "historial", "voziris.log", "voziris-fallos.log",
             "diagnostico.txt"}
INSTALAR_CMD = """@echo off
rem Instala Voziris para este usuario: menu Inicio y "Aplicaciones instaladas".
rem No pide administrador. Para volver al modo portable: voziris.exe --desinstalar
"%~dp0voziris.exe" --instalar
if errorlevel 1 (
  echo.
  echo No se pudo instalar. Mira voziris.log en esta carpeta.
  pause
)
"""


def main() -> int:
    sys.path.insert(0, str(RAIZ / "src"))
    from voziris import __version__

    if not (DIST / "voziris.exe").exists():
        print(f"No hay ejecutable en {DIST}: ejecuta antes PyInstaller.", file=sys.stderr)
        return 1
    (DIST / "Instalar Voziris.cmd").write_text(INSTALAR_CMD, encoding="cp1252", newline="\r\n")

    zip_path = DIST.parent / f"voziris-{__version__}-win64.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archivo:
        for p in sorted(DIST.rglob("*")):
            relativo = p.relative_to(DIST)
            if relativo.parts[0] in EXCLUIDOS or p.suffix == ".log":
                continue
            if p.is_file():
                archivo.write(p, Path("voziris") / relativo)
    h = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    (DIST.parent / "SHA256SUMS.txt").write_text(f"{h}  {zip_path.name}\n", encoding="ascii")

    notas = RAIZ / "docs" / "notas-release.md"
    if notas.exists():
        t = notas.read_text(encoding="utf-8")
        t = re.sub(
            r"(SHA-256  voziris-[\d.]+-win64\.zip\n)[0-9a-f]{64}", lambda m: m.group(1) + h, t
        )
        notas.write_text(t, encoding="utf-8", newline="\n")
    print(f"{zip_path.name}: {zip_path.stat().st_size // 2**20} MB · SHA256 {h}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
