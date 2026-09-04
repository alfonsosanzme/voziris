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
en lo que no se puede confiar. Y como el prompt no basta, hay una red de
seguridad: `es_sospechosa()` compara entrada y salida y, si la salida es
mucho más larga, mucho más corta o trae palabras que no estaban, se descarta
y se entrega el texto de entrada con aviso.

El modelo y la clave son los de `[motor.api]`; solo `proceso.llm_modelo` es
propio. Con `llm_modelo` vacío este paso no hace nada. La verificación en
castellano con dictados reales se hace con `tools/probar_llm.py`.

Issue: VOZ-42.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any

from voziris.tipos import Contexto, Nivel, Transcripcion

log = logging.getLogger(__name__)

TIMEOUT_S = 6.0
"""Más vale texto crudo ya que texto pulido dos segundos tarde.

Groq responde en 0,3–1,5 s para un dictado de un minuto (medido en H0 con la
API de transcripción, misma infraestructura). Seis segundos cubren un pico
de carga sin que el usuario se pregunte si la aplicación se ha colgado.
"""

PROMPT_LIMPIO = """\
Eres un corrector de dictado. Recibes una transcripción de voz en {idioma} y
devuelves EXCLUSIVAMENTE el texto corregido, sin comentarios, sin comillas y
sin encabezados.

Haz esto:
- Elimina muletillas y repeticiones («eh», «o sea», «este…», «bueno», «mmm»).
- Resuelve las autocorrecciones del hablante: si se corrige («el martes, no,
  el jueves»), conserva solo la versión final («el jueves»).
- Arregla la puntuación y las mayúsculas si están claramente mal.

No hagas esto:
- No añadas información que no esté en el texto. Ni un nombre, ni una fecha,
  ni una hora, ni una cortesía.
- No reordenes las ideas ni cambies el registro.
- No traduzcas. No resumas. No expliques lo que has hecho.
- Si el texto ya está bien, devuélvelo tal cual."""

PROMPT_REESCRITURA = """\
Eres un corrector de dictado. Recibes una transcripción de voz en {idioma} y
devuelves EXCLUSIVAMENTE el texto corregido y formateado, sin comentarios, sin
comillas y sin encabezados.

Haz esto:
- Elimina muletillas y repeticiones («eh», «o sea», «este…», «bueno», «mmm»).
- Resuelve las autocorrecciones del hablante: si se corrige, conserva solo la
  versión final.
- Arregla la puntuación y las mayúsculas.
- Aplica formato solo cuando el propio dictado lo pide: enumeraciones
  («primero… segundo…») como lista con guiones, cambios de tema como párrafos
  separados, y saludo y despedida en líneas propias si es un correo.

No hagas esto:
- No añadas información que no esté en el texto. Ni un nombre, ni una fecha,
  ni una hora, ni una cortesía, ni un asunto.
- No cambies el registro ni el orden de las ideas.
- No traduzcas. No resumas. No expliques lo que has hecho.
- Si el texto ya está bien, devuélvelo tal cual."""

PROMPTS = {Nivel.LIMPIO: PROMPT_LIMPIO, Nivel.REESCRITURA: PROMPT_REESCRITURA}

PREFIJOS = ("texto corregido:", "texto:", "corregido:", "resultado:")

_RAZONAMIENTO = re.compile(
    r"<(think|thinking|reasoning)>.*?</\1>\s*", re.IGNORECASE | re.DOTALL
)
"""Red de seguridad: si un modelo de razonamiento ignora «reasoning_format»,
su pensamiento no acaba pegado en el documento del usuario."""

# --- red de seguridad -------------------------------------------------------------

MARGEN_LARGO = 0.40
"""La salida puede crecer hasta un 40 % (puntuación, mayúsculas, saltos de línea)."""
MARGEN_CORTO = 0.45
"""Y encoger hasta un 45 %: las muletillas y las repeticiones se van."""
PALABRAS_NUEVAS_MAXIMAS = 3
"""Palabras de cinco letras o más que no estaban en la entrada. Alguna aparece
al corregir una falta; más de tres es contenido nuevo."""

_PALABRA = re.compile(r"[^\W\d_]{5,}", re.UNICODE)


def _normalizar(palabra: str) -> str:
    sin_acentos = unicodedata.normalize("NFD", palabra.casefold())
    return "".join(c for c in sin_acentos if unicodedata.category(c) != "Mn")


def es_sospechosa(entrada: str, salida: str) -> bool:
    """True si la salida del modelo no puede venir solo de limpiar la entrada."""
    if not salida.strip():
        return True
    largo_entrada, largo_salida = len(entrada.strip()), len(salida.strip())
    if largo_salida > largo_entrada * (1 + MARGEN_LARGO) + 20:
        return True
    if largo_salida < largo_entrada * (1 - MARGEN_CORTO) - 5:
        return True
    conocidas = {_normalizar(p) for p in _PALABRA.findall(entrada)}
    nuevas = {_normalizar(p) for p in _PALABRA.findall(salida)} - conocidas
    return len(nuevas) > PALABRAS_NUEVAS_MAXIMAS


def _limpiar_respuesta(texto: str) -> str:
    texto = _RAZONAMIENTO.sub("", texto).strip()
    for prefijo in PREFIJOS:
        if texto.lower().startswith(prefijo):
            texto = texto[len(prefijo) :].strip()
    if len(texto) >= 2 and texto[0] in "\"«“'" and texto[-1] in "\"»”'":
        texto = texto[1:-1].strip()
    return texto


class LimpiezaLLM:
    nombre = "llm"
    requiere_red = True

    def __init__(
        self,
        base_url: str,
        modelo: str,
        clave: str,
        timeout_s: float = TIMEOUT_S,
        transporte: Any = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._modelo = modelo
        self._clave = clave
        self._timeout = timeout_s
        self._transporte = transporte
        self._cliente: Any = None

    def _abrir(self) -> Any:
        if self._cliente is None:
            import httpx

            self._cliente = httpx.Client(
                base_url=self._base_url,
                timeout=self._timeout,
                headers={"Authorization": f"Bearer {self._clave}", "User-Agent": "voziris"},
                transport=self._transporte,
            )
        return self._cliente

    def cerrar(self) -> None:
        cliente, self._cliente = self._cliente, None
        if cliente is not None:
            cliente.close()

    def aplicar(self, t: Transcripcion, ctx: Contexto) -> Transcripcion:
        """Aplica el prompt del nivel de `ctx`. Nunca lanza; nunca pierde el dictado.

        Sin modelo configurado no hace nada. Sin clave o sin red, sin llamar,
        avisa. Con timeout, error o salida sospechosa, devuelve la entrada
        con aviso.
        """
        if ctx.nivel is Nivel.LITERAL or not self._modelo or not t.texto.strip():
            return t
        if not self._clave:
            t.avisos.append("Sin clave de API: entregado sin limpiar")
            return t
        if not ctx.hay_red:
            t.avisos.append("Sin red: entregado sin limpiar")
            return t
        try:
            salida = self._pedir(t.texto, ctx.nivel, t.idioma)
        except Exception as e:  # noqa: BLE001 — cualquier fallo del LLM degrada, no rompe
            motivo = _limpiar(str(e), self._clave)
            log.warning("LLM: %s", motivo)
            t.avisos.append(f"LLM no disponible ({motivo}): entregado sin limpiar")
            return t
        if es_sospechosa(t.texto, salida):
            log.warning(
                "LLM: salida sospechosa descartada (%d → %d caracteres)", len(t.texto), len(salida)
            )
            t.avisos.append("El LLM alteró demasiado el texto: entregado sin limpiar")
            return t
        t.texto = salida
        return t

    def _pedir(self, texto: str, nivel: Nivel, idioma: str) -> str:
        import httpx

        cuerpo = {
            "model": self._modelo,
            "temperature": 0,
            "max_tokens": max(64, len(texto) // 2 + 64),
            # Los modelos de razonamiento de Groq (gpt-oss, qwen3) piensan antes
            # de responder. Sin estos dos campos, ese razonamiento se cuela
            # entre etiquetas <think> en el texto que se pega, y además se
            # factura a precio de salida. «low» es el mínimo que aceptan los
            # gpt-oss; los que admiten «none» lo ignoran sin protestar.
            "reasoning_effort": "low",
            "reasoning_format": "hidden",
            "messages": [
                {"role": "system", "content": PROMPTS[nivel].format(idioma=idioma)},
                {"role": "user", "content": texto},
            ],
        }
        try:
            respuesta = self._abrir().post("/chat/completions", json=cuerpo)
        except httpx.TimeoutException as e:
            raise RuntimeError(f"sin respuesta en {self._timeout:g} s") from e
        except httpx.HTTPError as e:
            raise RuntimeError(f"sin conexión: {e}") from e
        if respuesta.status_code != 200:
            raise RuntimeError(f"HTTP {respuesta.status_code}")
        try:
            contenido = respuesta.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as e:
            raise RuntimeError("respuesta inesperada") from e
        return _limpiar_respuesta(str(contenido))


def _limpiar(texto: str, clave: str) -> str:
    if clave and len(clave) >= 8:
        texto = texto.replace(clave, "***")
    return texto
