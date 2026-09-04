"""Contrato del post-proceso (C2, C3, C4, E1, E3)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from voziris.tipos import Contexto, Transcripcion


@runtime_checkable
class PostProceso(Protocol):
    """Transforma el texto transcrito.

    Los pasos se aplican en cadena, en este orden fijo:

        1. diccionario.Diccionario      (E1, sin red)
        2. sustituciones.Sustituciones  (E3, sin red)
        3. llm.LimpiezaLLM              (C2/C3/C4, con red)

    El orden importa: el diccionario arregla nombres propios ANTES de que el
    LLM los vea, para que no los «corrija» a otra cosa.
    """

    nombre: str
    requiere_red: bool

    def aplicar(self, t: Transcripcion, ctx: Contexto) -> Transcripcion:
        """Devuelve la transcripción transformada.

        **No lanza nunca.** Ante cualquier fallo devuelve `t` sin tocar y añade
        una línea a `t.avisos`. Un post-proceso roto degrada la calidad del
        texto; no puede perder el dictado.
        """
        ...
