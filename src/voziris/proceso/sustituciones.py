"""E3 — sustituciones literales configurables.

Pares texto→texto del `config.toml`. Sirven tanto para atajos de dictado
(«punto y aparte» → doble salto de línea) como para arreglar cosas tercas que
el diccionario no pilla.

Se aplican como frases completas y sin distinguir mayúsculas, respetando
límites de palabra. Sin expresiones regulares en la v1: la configuración es
para el usuario final, y una regex mal escrita rompe el dictado en silencio.

Issue: VOZ-41.
"""

from __future__ import annotations

from voziris.tipos import Contexto, Transcripcion


class Sustituciones:
    nombre = "sustituciones"
    requiere_red = False

    def __init__(self, reglas: dict[str, str]) -> None:
        self._reglas = reglas

    def aplicar(self, t: Transcripcion, ctx: Contexto) -> Transcripcion:
        raise NotImplementedError("VOZ-41")
