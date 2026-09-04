"""Carga y validación del config.toml.

Dos reglas que vienen del requisito de portabilidad (F1):

  - La configuración vive JUNTO AL EJECUTABLE, no en %APPDATA%. Es lo que
    permite llevar la carpeta en un USB con los ajustes dentro.
  - Las rutas relativas se resuelven desde la carpeta del ejecutable, nunca
    desde el directorio de trabajo: Voziris arranca con Windows y ese
    directorio no es predecible.

`ConfigInvalida` es el único error que detiene el arranque. Todo lo demás
degrada con aviso.

Issue: VOZ-01.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path


def carpeta_base() -> Path:
    """Carpeta donde viven config.toml, modelos/ e historial/.

    Congelado por PyInstaller, `sys.executable` apunta al .exe; en desarrollo,
    a python.exe, así que se usa la raíz del proyecto. `sys._MEIPASS` NO sirve:
    en modo onefile apunta a la carpeta temporal de extracción, que se borra.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parents[2]


@dataclass
class Config:
    """Configuración completa, ya validada y con rutas absolutas.

    Se construye una vez al arrancar y se pasa hacia abajo. Ningún módulo lee
    el TOML por su cuenta.
    """

    # Rellenar en VOZ-01 reflejando config.ejemplo.toml. Un campo por opción,
    # con el mismo nombre, para que el panel de ajustes (VOZ-60) pueda
    # recorrerlo sin una tabla de correspondencias.


def cargar(ruta: Path | None = None) -> Config:
    """Lee y valida el TOML.

    Se valida con tomlkit para conservar comentarios: el panel de ajustes
    reescribe el archivo y perder los comentarios dejaría al usuario con un
    TOML mudo.

    Comprobaciones mínimas:
      - `general.motor` en {local, api, auto} y `proceso.nivel` en los tres niveles.
      - Los cuatro atajos son sintácticamente válidos y no se repiten entre sí.
      - `destino.markdown.ruta` apunta a una carpeta existente (el archivo puede
        no existir todavía).
      - Los valores numéricos, dentro de rango.

    Si falta `config.toml`, copiar `config.ejemplo.toml` y seguir con los valores
    por defecto: un primer arranque no debe fallar.
    """
    raise NotImplementedError("VOZ-01")


def guardar(config: Config, ruta: Path | None = None) -> None:
    """Reescribe el TOML conservando comentarios y orden. Lo usa VOZ-60."""
    raise NotImplementedError("VOZ-01")
