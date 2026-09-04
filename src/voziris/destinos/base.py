"""Contrato de los destinos de salida (D1, H1).

Este protocolo es la razón de que H1 sea barato. La alternativa —insertar
siempre en la app activa y añadir el volcado a Markdown como un caso especial
más adelante— obligaría a partir en dos el punto donde se concentran D2, D4 y
el historial. Con dos implementaciones de la misma interfaz, añadir un tercer
destino (una nota de Notion, un archivo de texto plano) es una clase nueva.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from voziris.tipos import Contexto, Entrega


@runtime_checkable
class Destino(Protocol):
    """Entrega el texto final en algún sitio."""

    nombre: str

    def entregar(self, texto: str, ctx: Contexto) -> Entrega:
        """Entrega el texto.

        Devuelve `Entrega` con `detalle` legible para el usuario («pegado en
        chrome.exe», «añadido a entrada.md»): va al historial y al HUD.

        Raises:
            EntregaFallida: no se pudo. El texto queda en el historial para que
                el usuario reintente sin volver a dictar.
        """
        ...
