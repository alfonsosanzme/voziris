"""D3 — historial de dictados con reintento.

Marcada como crítica, y con razón: es la red de seguridad de todo lo demás. Si
el pegado falla, si el usuario tenía el foco en la ventana equivocada, si el
destino estaba bloqueado — el texto sigue aquí y se reintenta sin volver a
dictar.

Archivo JSON Lines junto al ejecutable, recortado a `entradas` líneas. JSONL y
no una base de datos porque se abre con cualquier editor si algo va mal, y
porque añadir una línea no puede corromper las anteriores.

`guardar_audio` deja el WAV al lado. Solo para depurar: ocupa mucho y es
información sensible. Por defecto, desactivado.

Issue: VOZ-52.
"""

from __future__ import annotations

from pathlib import Path

from voziris.tipos import EntradaHistorial, Entrega


class Historial:
    def __init__(self, ruta: Path, maximo: int = 50, guardar_audio: bool = False) -> None:
        self._ruta = ruta
        self._maximo = maximo
        self._guardar_audio = guardar_audio

    def registrar(self, entrada: EntradaHistorial) -> None:
        """Añade una entrada y recorta el archivo si hace falta."""
        raise NotImplementedError("VOZ-52")

    def ultimas(self, n: int = 10) -> list[EntradaHistorial]:
        """Las n más recientes, de la más nueva a la más vieja. Para el menú de bandeja."""
        raise NotImplementedError("VOZ-52")

    def reintentar(self, indice: int) -> Entrega:
        """Vuelve a entregar una entrada al destino que tenía.

        Reentrega el texto ya procesado: no se vuelve a transcribir ni a pasar
        por el LLM. Es un reintento de la entrega, no del dictado.
        """
        raise NotImplementedError("VOZ-52")
