"""E1 — diccionario personal.

Sin esto, «Creatics» y «Kairis» salen mal en todos los dictados. Funciona sin
red, así que es el único arreglo de vocabulario disponible en modo offline.

Estrategia: corrección por similitud fonética sobre el texto ya transcrito, no
sesgo en la decodificación. Parakeet, al ser un transductor, no expone un
mecanismo de sesgo léxico utilizable desde onnx-asr, así que se corrige a
posteriori.

Cuidado con dos cosas:
  - Solo sustituir cuando la similitud es alta. Un umbral flojo destroza texto
    correcto, y eso se nota más que un nombre propio mal escrito.
  - Respetar los límites de palabra: no convertir «creaticidad» en «Creatics».

Issue: VOZ-40.
"""

from __future__ import annotations

from voziris.tipos import Contexto, Transcripcion


class Diccionario:
    nombre = "diccionario"
    requiere_red = False

    def __init__(self, palabras: list[str]) -> None:
        self._palabras = palabras

    def aplicar(self, t: Transcripcion, ctx: Contexto) -> Transcripcion:
        raise NotImplementedError("VOZ-40")
