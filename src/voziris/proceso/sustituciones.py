"""E3 — sustituciones literales configurables.

Pares texto→texto del `config.toml`. Sirven tanto para atajos de dictado
(«punto y aparte» → doble salto de línea) como para arreglar cosas tercas que
el diccionario no pilla.

Se aplican como frases completas y sin distinguir mayúsculas, respetando
límites de palabra. Sin expresiones regulares en la v1: la configuración es
para el usuario final, y una regex mal escrita rompe el dictado en silencio.
(Por dentro sí se compila una, con `re.escape`: el usuario nunca la ve.)

Detalle que importa: el motor puntúa. «Hola punto y aparte. Adiós» sale con
un punto pegado a la frase clave. Cuando la sustitución es un salto de
línea, se lleva por delante los espacios de alrededor y un signo de
puntuación que venga justo después, para no dejar «\\n\\n. Adiós».

Issue: VOZ-41.
"""

from __future__ import annotations

import logging
import re

from voziris.tipos import Contexto, Transcripcion

log = logging.getLogger(__name__)


class Sustituciones:
    nombre = "sustituciones"
    requiere_red = False

    def __init__(self, reglas: dict[str, str]) -> None:
        self._reglas = dict(reglas)
        self._patrones = self._compilar(self._reglas)

    @property
    def reglas(self) -> dict[str, str]:
        return dict(self._reglas)

    @reglas.setter
    def reglas(self, valor: dict[str, str]) -> None:
        """Desde los ajustes, en caliente."""
        self._reglas = dict(valor)
        self._patrones = self._compilar(self._reglas)

    @staticmethod
    def _compilar(reglas: dict[str, str]) -> list[tuple[re.Pattern[str], str]]:
        patrones: list[tuple[re.Pattern[str], str]] = []
        # Las frases más largas primero: «punto y aparte» antes que «punto».
        for clave in sorted(reglas, key=len, reverse=True):
            clave_limpia = " ".join(clave.split())
            if not clave_limpia:
                continue
            literal = r"\s+".join(re.escape(p) for p in clave_limpia.split(" "))
            valor = reglas[clave]
            if "\n" in valor:
                # Salto de línea: absorbe espacios y un signo de puntuación pegado.
                patron = rf"[ \t]*(?<!\w){literal}(?!\w)[ \t]*[.,;:]?[ \t]*"
            else:
                patron = rf"(?<!\w){literal}(?!\w)"
            patrones.append((re.compile(patron, re.IGNORECASE | re.UNICODE), valor))
        return patrones

    def aplicar(self, t: Transcripcion, ctx: Contexto) -> Transcripcion:
        """Aplica cada regla como frase literal. Una regla que no encaja no toca nada."""
        if not self._patrones or not t.texto:
            return t
        try:
            texto = t.texto
            for patron, valor in self._patrones:
                # Función y no cadena: así «\n» o «\1» en el valor no se interpretan.
                texto = patron.sub(lambda _m: valor, texto)  # noqa: B023 — se usa al momento
            t.texto = texto.strip()
        except Exception as e:  # noqa: BLE001 — el protocolo dice que no lanza
            log.exception("sustituciones falló")
            t.avisos.append(f"Sustituciones no aplicadas: {e}")
        return t
