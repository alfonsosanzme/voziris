"""E1 — diccionario personal.

Sin esto, «Creatics» y «Kairis» salen mal en todos los dictados. Funciona sin
red, así que es el único arreglo de vocabulario disponible en modo offline.

Estrategia: corrección por similitud fonética sobre el texto ya transcrito,
no sesgo en la decodificación. Parakeet, al ser un transductor, no expone un
mecanismo de sesgo léxico utilizable desde onnx-asr, así que se corrige a
posteriori.

Cómo se compara: cada palabra del texto se reduce a una clave fonética del
castellano (sin acentos, «k» y «qu» como «c», «v» como «b», «z» y «ce/ci»
como «s», sin «h»…) y se mide la similitud con la clave de cada palabra del
diccionario. Así «bociris» y «vosiris» dan «bosiris», que está a un paso de
«Voziris» («bosiris»): el motor confunde justo los sonidos que la clave
iguala.

Cuidado con dos cosas:
  - Solo sustituir cuando la similitud es alta (`UMBRAL`). Un umbral flojo
    destroza texto correcto, y eso se nota más que un nombre propio mal
    escrito. Se empieza restrictivo.
  - Respetar los límites de palabra: no convertir «creaticidad» en «Creatics».
    Se comparan palabras enteras, y una palabra bastante más larga que la del
    diccionario no es candidata.

Issue: VOZ-40.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from difflib import SequenceMatcher

from voziris.tipos import Contexto, Transcripcion

log = logging.getLogger(__name__)

UMBRAL = 0.84
"""Similitud mínima entre claves fonéticas para sustituir.

Calibrado el 4 sep 2026 con `tests/test_diccionario.py`: 10 frases que deben
corregirse y 10 que no deben tocarse. Con 0,80 «carisma» se acercaba a
«Kairis»; con 0,84 pasan los veinte casos y queda margen. Si el diccionario
del usuario tiene palabras muy cortas, conviene subirlo, no bajarlo.
"""

LONGITUD_MINIMA = 4
"""Palabras más cortas no se tocan: hay demasiadas parecidas entre sí."""

DIFERENCIA_MAXIMA = 2
"""Diferencia de longitud (en letras) a partir de la cual no se compara."""

_PALABRA = re.compile(r"[^\W\d_]+", re.UNICODE)


def normalizar(palabra: str) -> str:
    """Minúsculas y sin acentos: «Creátics» → «creatics»."""
    sin_acentos = unicodedata.normalize("NFD", palabra.casefold())
    return "".join(c for c in sin_acentos if unicodedata.category(c) != "Mn")


def clave_fonetica(palabra: str) -> str:
    """Reduce una palabra a cómo suena en castellano, para comparar.

    Iguala los pares que un motor de voz confunde: b/v, k/qu/c, z/s, ce/ci,
    ll/y, h muda, ge/gi/j, letras dobles.
    """
    p = normalizar(palabra)
    reglas = (
        ("qu", "c"), ("k", "c"), ("ce", "se"), ("ci", "si"), ("z", "s"), ("v", "b"),
        ("ll", "y"), ("ge", "je"), ("gi", "ji"), ("gue", "ge"), ("gui", "gi"), ("x", "s"),
        ("w", "u"), ("h", ""), ("ph", "f"), ("y", "i"),
    )
    for viejo, nuevo in reglas:
        p = p.replace(viejo, nuevo)
    p = re.sub(r"(.)\1+", r"\1", p)  # letras dobles
    return p


class Diccionario:
    nombre = "diccionario"
    requiere_red = False

    def __init__(self, palabras: list[str], umbral: float = UMBRAL) -> None:
        self._umbral = umbral
        self.palabras = palabras

    @property
    def palabras(self) -> list[str]:
        return list(self._palabras)

    @palabras.setter
    def palabras(self, valor: list[str]) -> None:
        """Desde los ajustes, en caliente."""
        self._palabras = [p.strip() for p in valor if p.strip()]
        self._entradas = [
            (p, normalizar(p), clave_fonetica(p)) for p in self._palabras if " " not in p
        ]
        self._frases = [p for p in self._palabras if " " in p]

    def aplicar(self, t: Transcripcion, ctx: Contexto) -> Transcripcion:
        if not self._palabras or not t.texto:
            return t
        try:
            texto = _PALABRA.sub(self._corregir, t.texto)
            for frase in self._frases:
                patron = r"(?<!\w)" + r"\s+".join(re.escape(x) for x in frase.split()) + r"(?!\w)"
                texto = re.sub(patron, frase, texto, flags=re.IGNORECASE)
            t.texto = texto
        except Exception as e:  # noqa: BLE001 — el protocolo dice que no lanza
            log.exception("diccionario falló")
            t.avisos.append(f"Diccionario no aplicado: {e}")
        return t

    def _corregir(self, coincidencia: re.Match[str]) -> str:
        palabra = coincidencia.group(0)
        mejor = self.corregir_palabra(palabra)
        return mejor if mejor is not None else palabra

    def corregir_palabra(self, palabra: str) -> str | None:
        """La entrada del diccionario que corresponde, o None si no hay que tocarla."""
        if len(palabra) < LONGITUD_MINIMA:
            return None
        plana = normalizar(palabra)
        fonetica = clave_fonetica(palabra)
        mejor: tuple[float, str] | None = None
        for original, original_plana, original_fonetica in self._entradas:
            if plana == original_plana:
                return original if palabra != original else None  # solo la capitalización
            if abs(len(plana) - len(original_plana)) > DIFERENCIA_MAXIMA:
                continue
            similitud = SequenceMatcher(None, fonetica, original_fonetica).ratio()
            if similitud >= self._umbral and (mejor is None or similitud > mejor[0]):
                mejor = (similitud, original)
        return mejor[1] if mejor else None
