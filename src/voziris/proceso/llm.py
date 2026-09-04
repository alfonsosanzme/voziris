"""C2, C3, C4 — limpieza y formato con un modelo de lenguaje.

Lo único del pipeline que exige red. En modo local sin conexión este paso se
salta y se avisa: la puntuación ya viene del motor (C1), así que el texto sigue
siendo perfectamente utilizable, solo más crudo.

Los tres niveles (C4):

  literal      — este paso no se ejecuta.
  limpio       — quita muletillas y resuelve autocorrecciones habladas (C2):
                 «el martes, no, el jueves» → «el jueves». No reordena ni
                 reescribe: lo que el usuario dijo, limpio.
  reescritura  — además aplica formato (C3): listas, párrafos, estructura de
                 correo cuando el dictado lo pide.

Regla de oro del prompt: **nunca añadir contenido que el usuario no haya
dicho.** Un LLM que «mejora» inventando datos convierte la herramienta en algo
en lo que no se puede confiar. Ante duda, devolver el texto tal cual.

Ojo con el castellano: hay que verificar el comportamiento en español antes de
cerrar el prompt. Casi todo lo publicado sobre esto está probado en inglés.

Issue: VOZ-42.
"""

from __future__ import annotations

from voziris.tipos import Contexto, Nivel, Transcripcion

PROMPT_LIMPIO = """\
Eres un corrector de dictado. Recibes una transcripción de voz en {idioma} y
devuelves EXCLUSIVAMENTE el texto corregido, sin comentarios.

Haz esto:
- Elimina muletillas y repeticiones («eh», «o sea», «este…»).
- Resuelve las autocorrecciones del hablante: si se corrige, conserva solo la
  versión final.
- Arregla la puntuación si está claramente mal.

No hagas esto:
- No añadas información que no esté en el texto.
- No reordenes las ideas ni cambies el registro.
- No traduzcas.
- No resumas.

Transcripción:
{texto}"""

PROMPT_REESCRITURA = """\
(pendiente de VOZ-42: partir de PROMPT_LIMPIO y añadir formato — listas,
párrafos, estructura de correo — manteniendo intacta la prohibición de añadir
contenido)"""


class LimpiezaLLM:
    nombre = "llm"
    requiere_red = True

    def __init__(self, base_url: str, modelo: str, clave: str, timeout_s: int = 10) -> None:
        self._base_url = base_url
        self._modelo = modelo
        self._clave = clave
        self._timeout = timeout_s

    def aplicar(self, t: Transcripcion, ctx: Contexto) -> Transcripcion:
        """Aplica el prompt del nivel de `ctx`.

        Si `ctx.nivel is Nivel.LITERAL`, devuelve `t` sin llamar a nada.
        Si no hay red, no hay clave o la llamada supera el timeout, devuelve
        `t` con un aviso. El timeout es corto a propósito: más vale texto crudo
        entregado ya que texto pulido dos segundos tarde.
        """
        if ctx.nivel is Nivel.LITERAL:
            return t
        raise NotImplementedError("VOZ-42")
