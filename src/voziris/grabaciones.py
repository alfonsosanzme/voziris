"""Transcribir una grabación entera a Markdown, con o sin hablantes.

    voziris.exe --transcribir reunion.m4a --hablantes auto     (varios, automático)
    voziris.exe --transcribir nota.m4a --hablantes 1           (una sola voz)
    voziris.exe --transcribir reunion.m4a --hablantes 3        (se sabe cuántos)

Cadena: decodificar (PyAV) → normalizar → [separar hablantes] → trocear en
ventanas que el motor digiere (≤ 30 s, cortadas en el punto más silencioso)
→ transcribir cada trozo con el motor configurado (local o API) → Markdown.

El modelo local se lleva bien con audio largo, pero no con 19 minutos de
golpe en memoria; y la API tiene 25 MB por archivo. Trocear resuelve las dos.

Issue: VOZ-70 (una voz), VOZ-71 (varias).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

from voziris import archivos
from voziris.audio.captura import normalizar
from voziris.errores import TranscripcionFallida
from voziris.hablantes import SeparadorHablantes
from voziris.motores.base import MotorSTT
from voziris.tipos import SAMPLE_RATE, Audio

log = logging.getLogger(__name__)

VENTANA_MAXIMA_S = 30.0
"""Whisper se entrenó con ventanas de 30 s y Parakeet rinde igual: no más largo."""
BUSQUEDA_DE_CORTE_S = 4.0
"""Cuánto se mira alrededor del corte teórico para caer en el punto más silencioso."""
TROZO_MINIMO_S = 0.8
"""Una intervención más corta no da texto fiable (el motor se inventa un «Yeah»)."""
PAUSA_DE_PARRAFO_S = 1.5
"""En una sola voz, un silencio así separa párrafos."""

Progreso = Callable[[str, float | None], None]


@dataclass
class Linea:
    inicio: float
    hablante: int | None  # None: transcripción de una sola voz
    texto: str


@dataclass
class Resultado:
    lineas: list[Linea]
    duracion_s: float
    hablantes: int
    motor: str
    ms_proceso: int
    avisos: list[str] = field(default_factory=list)

    def markdown(self, titulo: str, momento: datetime | None = None) -> str:
        return render_markdown(self, titulo, momento or datetime.now())


# --- troceado --------------------------------------------------------------------------


def trocear(
    muestras: np.ndarray, sr: int = SAMPLE_RATE, ventana: float = VENTANA_MAXIMA_S
) -> list[tuple[int, int]]:
    """Índices (inicio, fin) de trozos de como mucho `ventana` s, cortados donde menos ruido hay."""
    n = len(muestras)
    maximo = int(ventana * sr)
    if n <= maximo:
        return [(0, n)] if n else []
    marco = sr // 50  # 20 ms
    energia = np.sqrt(
        np.mean(np.square(muestras[: n - n % marco].reshape(-1, marco)), axis=1)
    )
    cortes: list[tuple[int, int]] = []
    inicio = 0
    margen = int(BUSQUEDA_DE_CORTE_S * sr)
    while n - inicio > maximo:
        teorico = inicio + maximo
        desde, hasta = (teorico - margen) // marco, min(teorico, n) // marco
        ventana_energia = energia[desde:hasta]
        if len(ventana_energia):
            corte = (desde + int(np.argmin(ventana_energia))) * marco
        else:
            corte = teorico
        if corte <= inicio:
            corte = teorico
        cortes.append((inicio, corte))
        inicio = corte
    cortes.append((inicio, n))
    return cortes


PALABRAS_FRECUENTES_ES = frozenset(
    "de la que el en y a los se del las un por con no una su para es al lo como más o pero "  # noqa: SIM905, E501
    "sus le ya fue este ha sí porque esta son entre cuando muy sin sobre también me hasta "
    "hay donde quien desde todo nos durante todos uno les ni contra otros ese eso ante ellos "
    "e esto mí antes algunos qué unos yo otro otras otra él tanto esa estos mucho quienes "
    "nada muchos cual poco ella estar estas algunas algo nosotros mi mis tú te ti tu tus "
    "ellas nosotras vosotros vosotras os mío mía míos mías tuyo tuya tuyos tuyas suyo suya "
    "suyos suyas nuestro nuestra nuestros nuestras vuestro vuestra vuestros vuestras esos "
    "esas estoy estás está estamos estáis están esté estés estemos estéis estén estaré "
    "vale bueno claro pues entonces bien soy eres somos sois son era eras éramos erais eran "
    "he has hemos habéis han hola gracias ahora luego aquí ahí allí".split()
)
"""Para cazar el «Yeah» que el motor multilingüe se inventa en un trozo corto y ruidoso."""


def parece_relleno(texto: str, idioma: str) -> bool:
    """Una línea de cuatro palabras o menos sin ninguna palabra corriente del idioma.

    Parakeet detecta el idioma por su cuenta y, con la voz de enfrente de una
    llamada, un «sí, sí» de dos segundos sale como «Yeah» o «Sure». Solo se
    aplica al español, que es el único idioma con lista.
    """
    if idioma != "es":
        return False
    palabras = [p.strip(".,;:¿?¡!\"'()").lower() for p in texto.split()]
    palabras = [p for p in palabras if p]
    if not palabras or len(palabras) > 4:
        return False
    return not any(p in PALABRAS_FRECUENTES_ES for p in palabras)


def _transcribir_trozo(
    motor: MotorSTT, muestras: np.ndarray, idioma: str
) -> tuple[str, int, str]:
    """(texto, ms, motor que respondió). Cada trozo se normaliza por su cuenta: en
    una llamada, la voz de enfrente llega mucho más baja que la propia y con la
    normalización global se pierde."""
    try:
        t = motor.transcribir(Audio(muestras=normalizar(np.ascontiguousarray(muestras))), idioma)
    except TranscripcionFallida:
        return "", 0, ""
    return t.texto.strip(), t.ms_proceso, t.motor


# --- una voz ------------------------------------------------------------------------------


def transcribir_una_voz(
    audio: Audio, motor: MotorSTT, idioma: str, al_progresar: Progreso | None = None
) -> Resultado:
    muestras = audio.muestras
    trozos = trocear(muestras, audio.sr)
    lineas: list[Linea] = []
    ms = 0
    motores: set[str] = set()
    for i, (a, b) in enumerate(trozos):
        if al_progresar:
            al_progresar("Transcribiendo…", i / max(1, len(trozos)))
        texto, tardo, nombre = _transcribir_trozo(motor, muestras[a:b], idioma)
        ms += tardo
        motores.add(nombre)
        if texto:
            lineas.append(Linea(a / audio.sr, None, texto))
    return Resultado(lineas, audio.duracion_s, 1, _nombre_motor(motores, motor), ms)


# --- varias voces ---------------------------------------------------------------------------


def transcribir_varias_voces(
    audio: Audio,
    motor: MotorSTT,
    separador: SeparadorHablantes,
    idioma: str,
    hablantes: int | None = None,
    al_progresar: Progreso | None = None,
) -> Resultado:
    # La separación sí va con el audio normalizado entero: las huellas de voz
    # salen más estables (medido en VOZ-71) y así todas se calculan igual.
    muestras = normalizar(audio.muestras)
    intervenciones = separador.separar(Audio(muestras=muestras, sr=audio.sr), hablantes)
    lineas: list[Linea] = []
    ms = 0
    descartadas = 0
    motores: set[str] = set()
    for i, inter in enumerate(intervenciones):
        if al_progresar:
            al_progresar("Transcribiendo intervenciones…", i / max(1, len(intervenciones)))
        a, b = int(inter.inicio * audio.sr), int(inter.fin * audio.sr)
        if b - a < TROZO_MINIMO_S * audio.sr:
            continue
        partes = []
        for x, y in trocear(muestras[a:b], audio.sr):
            texto, tardo, nombre = _transcribir_trozo(motor, muestras[a + x : a + y], idioma)
            ms += tardo
            motores.add(nombre)
            if texto:
                partes.append(texto)
        texto = " ".join(partes)
        if partes and parece_relleno(texto, idioma):
            log.debug("descartado como relleno en %.0f s: %r", inter.inicio, texto)
            descartadas += 1
        elif partes:
            lineas.append(Linea(inter.inicio, inter.hablante, texto))
    if descartadas:
        log.info("%d intervenciones cortas descartadas como relleno", descartadas)
    cuantos = len({ln.hablante for ln in lineas}) if lineas else 0
    return Resultado(lineas, audio.duracion_s, cuantos, _nombre_motor(motores, motor), ms)


def _nombre_motor(vistos: set[str], motor: MotorSTT) -> str:
    """«local», «api:groq» o «api:groq+local» si el selector cayó al respaldo a medias."""
    nombres = sorted(n for n in vistos if n)
    return "+".join(nombres) if nombres else motor.nombre


# --- de archivo a Markdown -------------------------------------------------------------------


def transcribir_archivo(
    ruta: Path,
    motor: MotorSTT,
    idioma: str,
    hablantes: str = "auto",
    separador: SeparadorHablantes | None = None,
    al_progresar: Progreso | None = None,
) -> Resultado:
    """`hablantes`: "1" una voz, "auto" varias sin saber cuántas, o un número."""
    if al_progresar:
        al_progresar(f"Abriendo {ruta.name}…", None)
    audio = archivos.decodificar(ruta)
    if hablantes == "1" or separador is None:
        resultado = transcribir_una_voz(audio, motor, idioma, al_progresar)
        if hablantes != "1" and separador is None:
            resultado.avisos.append("Sin separación de hablantes: los modelos no están disponibles")
        return resultado
    cuantos = None if hablantes == "auto" else max(1, int(hablantes))
    return transcribir_varias_voces(audio, motor, separador, idioma, cuantos, al_progresar)


def render_markdown(resultado: Resultado, titulo: str, momento: datetime) -> str:
    minutos = int(resultado.duracion_s) // 60
    segundos = int(resultado.duracion_s) % 60
    cabecera = [
        f"# {titulo}",
        "",
        f"Transcrito el {momento:%Y-%m-%d %H:%M} · duración {minutos}:{segundos:02d} · "
        + (f"{resultado.hablantes} hablantes · " if resultado.hablantes > 1 else "")
        + f"motor {resultado.motor}",
    ]
    for aviso in resultado.avisos:
        cabecera.append(f"> {aviso}")
    cabecera.append("")
    cuerpo = []
    for ln in resultado.lineas:
        m, s = divmod(int(ln.inicio), 60)
        if ln.hablante is None:
            cuerpo.append(f"{ln.texto}")
            cuerpo.append("")
        else:
            cuerpo.append(f"**Hablante {ln.hablante + 1}** ({m:02d}:{s:02d}) {ln.texto}")
            cuerpo.append("")
    return "\n".join(cabecera + cuerpo).rstrip() + "\n"


def ruta_de_salida(ruta_audio: Path) -> Path:
    """`reunion.m4a` → `reunion.md` al lado; si existe, `reunion (2).md`, etc."""
    base = ruta_audio.with_suffix(".md")
    if not base.exists():
        return base
    n = 2
    while (candidata := base.with_name(f"{base.stem} ({n}).md")).exists():
        n += 1
    return candidata


def guardar(resultado: Resultado, ruta_audio: Path, destino: Path | None = None) -> Path:
    destino = destino or ruta_de_salida(ruta_audio)
    destino.write_text(resultado.markdown(ruta_audio.stem), encoding="utf-8", newline="\n")
    log.info("transcripción guardada en %s (%d líneas)", destino, len(resultado.lineas))
    return destino


def cronometrar(fn: Callable[[], Resultado]) -> tuple[Resultado, float]:
    t0 = time.perf_counter()
    r = fn()
    return r, time.perf_counter() - t0
