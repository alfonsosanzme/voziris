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

from voziris import archivos, winapi
from voziris.audio.captura import _rms, normalizar
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
VECES_EL_AUDIO_QUE_PIDEN_LOS_HABLANTES = 11
"""sherpa-onnx reserva unas once veces el tamaño del audio, y de golpe, al final.

Medido con diálogos de dos voces: dos horas de audio (439 MB) llegan a un pico
de 4,9 GB. No se puede evitar desde aquí — lo pide la biblioteca — así que lo
que se hace es mirar antes si cabe.
"""
MEMORIA_BASE_HABLANTES_MB = 1100.0
"""Lo que ya ocupan el motor y los dos modelos de voces antes de empezar.

Medido sobre vídeos reales: el pico del proceso fue 1226 MB con un audio de
15 MB, o sea unos 1100 de base. Antes ponía 900 y se quedaba corto.
"""
RMS_DE_SILENCIO = 0.005
"""Por debajo de esto no hay voz que valga: es silencio o poco más que ruido de fondo."""
DURACION_RIDICULA_S = 0.5
"""Un archivo más corto que esto no es una grabación: algo salió mal al abrirlo."""
MARGEN_MEMORIA_MB = 500.0
"""Lo que se deja libre para el resto del equipo: no se le llena la RAM al usuario."""
LARGO_PARA_AVISAR_S = 20 * 60
"""A partir de aquí se dice cuánto va a tardar: una reunión larga son decenas de minutos."""
VECES_MAS_RAPIDO_QUE_EL_AUDIO = 6.0
"""Cuántas veces más rápido que el audio va el motor local, para decir la espera.

Medido en el portátil del cliente (8 núcleos): 9,25 veces. Se usa 6 para no
quedarse corto en un equipo más flojo o con el ventilador bajado: vale más
prometer de más y terminar antes.
"""

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
    energia = _energia_por_marcos(muestras[: n - n % marco], marco)
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


def _energia_por_marcos(muestras: np.ndarray, marco: int) -> np.ndarray:
    """Valor eficaz de cada marco de 20 ms, por bloques para no duplicar el audio.

    Elevar al cuadrado el audio entero de una vez cuesta otro tanto de memoria,
    y con un vídeo largo eso es casi un giga de más para nada.
    """
    marcos = len(muestras) // marco
    energia = np.empty(marcos, dtype=np.float32)
    por_bloque = max(1, (1 << 20) // marco)
    for i in range(0, marcos, por_bloque):
        cuantos = min(por_bloque, marcos - i)
        vista = muestras[i * marco : (i + cuantos) * marco].reshape(cuantos, marco)
        energia[i : i + cuantos] = np.sqrt(np.mean(np.square(vista, dtype=np.float32), axis=1))
    return energia


def _transcribir_trozo(
    motor: MotorSTT, muestras: np.ndarray, idioma: str
) -> tuple[str, int, str]:
    """(texto, ms, motor que respondió). Cada trozo se normaliza por su cuenta: en
    una llamada, la voz de enfrente llega mucho más baja que la propia y con la
    normalización global se pierde."""
    t0 = time.perf_counter()
    try:
        t = motor.transcribir(Audio(muestras=normalizar(np.ascontiguousarray(muestras))), idioma)
    except TranscripcionFallida:
        # El motor ya ha pasado el audio por el modelo y no ha sacado texto: ese
        # tiempo es real y se cuenta, o la cifra del .md miente. Medido con un
        # vídeo de 31 min: 43 de 68 trozos salieron sin texto y costaron 106 s
        # que se daban por cero, sobre 170 s reales.
        return "", int((time.perf_counter() - t0) * 1000), ""
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
    # salen más estables (medido en VOZ-71) y así todas se calculan igual. Se
    # normaliza sobre el mismo array: quien llama viene de `decodificar` y no
    # lo vuelve a mirar, y con un vídeo largo la copia serían cientos de MB.
    muestras = normalizar(audio.muestras, en_sitio=True)
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


NOMBRE_INTERNO_DEL_SELECTOR = "selector"


def _nombre_motor(vistos: set[str], motor: MotorSTT) -> str:
    """«local», «api:groq» o «api:groq+local» si el selector cayó al respaldo a medias."""
    nombres = sorted(n for n in vistos if n)
    if nombres:
        return "+".join(nombres)
    if motor.nombre == NOMBRE_INTERNO_DEL_SELECTOR:
        # Ningún trozo dio texto, así que nadie dijo su nombre. Antes salía
        # «motor selector» en la cabecera del .md, que no quiere decir nada.
        return "sin respuesta"
    return motor.nombre


# --- de archivo a Markdown -------------------------------------------------------------------


def transcribir_archivo(
    ruta: Path,
    motor: MotorSTT,
    idioma: str,
    hablantes: str = "auto",
    separador: SeparadorHablantes | None = None,
    al_progresar: Progreso | None = None,
    pista: int | str = "auto",
) -> Resultado:
    """De una grabación o un vídeo al `Resultado` que se vuelca en Markdown.

    `hablantes`: "1" una voz, "auto" varias sin saber cuántas, o un número.
    `pista`: cuál de las de audio, si el archivo trae varias (ver `archivos`).
    """
    if al_progresar:
        al_progresar(f"Abriendo {ruta.name}…", None)
    audio, avisos = archivos.decodificar(ruta, pista=pista, idioma=idioma)
    if al_progresar:
        al_progresar(_cuanto_queda(audio.duracion_s), None)
    sin_memoria = ""
    if hablantes != "1" and separador is not None:
        libre = winapi.memoria_libre_mb()
        if libre is not None and not caben_los_hablantes(audio, libre):
            sin_memoria = _aviso_sin_memoria(audio, libre)
            log.warning("sin memoria para separar hablantes: %s", sin_memoria)
    if hablantes == "1" or separador is None or sin_memoria:
        resultado = transcribir_una_voz(audio, motor, idioma, al_progresar)
        if hablantes != "1" and separador is None:
            resultado.avisos.append("Sin separación de hablantes: los modelos no están disponibles")
        if sin_memoria:
            resultado.avisos.append(sin_memoria)
    else:
        cuantos = None if hablantes == "auto" else max(1, int(hablantes))
        resultado = transcribir_varias_voces(
            audio, motor, separador, idioma, cuantos, al_progresar
        )
    # Los avisos de la decodificación (varias pistas, pistas sumadas) van
    # los primeros: son lo que más cambia cómo hay que leer el resultado.
    resultado.avisos[:0] = avisos
    if not resultado.lineas:
        resultado.avisos.append(_por_que_no_hay_texto(audio, ruta, avisos))
    return resultado


def _por_que_no_hay_texto(audio: Audio, ruta: Path, avisos: list[str]) -> str:
    """Un Markdown vacío sin explicación es lo peor que puede pasar: aquí se explica.

    Pasa de verdad — una pista muda elegida por defecto, un vídeo con el audio
    en silencio, un archivo que se abrió pero no traía casi nada — y sin esto
    el usuario solo ve un archivo con el título y nada debajo.
    """
    if audio.duracion_s < DURACION_RIDICULA_S:
        return (
            f"No se ha sacado nada: de {ruta.name} solo se pudieron leer "
            f"{audio.duracion_s:.2f} segundos de sonido. Puede que el archivo esté a medias."
        )
    if _rms(audio.muestras) < RMS_DE_SILENCIO:
        callada = " La pista que se ha usado está muda; prueba con otra (--pista N)." if any(
            "pistas de audio" in a for a in avisos
        ) else ""
        return (
            "No se ha reconocido ninguna palabra: el audio está en silencio o casi."
            + callada
        )
    return (
        "No se ha reconocido ninguna palabra, aunque sí hay sonido. Puede ser música, "
        "ruido, o voz en otro idioma del configurado."
    )


def caben_los_hablantes(audio: Audio, libre_mb: float | None) -> bool:
    """¿Hay memoria para separar hablantes en este audio, o va a morir a medias?

    Vale más decirlo antes y transcribir sin separar que pelearse media hora
    con un vídeo y acabar sin nada.
    """
    if libre_mb is None:
        return True  # sin dato, se intenta: es lo que se hacía siempre
    hacen_falta = (
        audio.muestras.nbytes * VECES_EL_AUDIO_QUE_PIDEN_LOS_HABLANTES / 2**20
        + MEMORIA_BASE_HABLANTES_MB
    )
    return hacen_falta + MARGEN_MEMORIA_MB <= libre_mb


def _aviso_sin_memoria(audio: Audio, libre_mb: float) -> str:
    hacen_falta = (
        audio.muestras.nbytes * VECES_EL_AUDIO_QUE_PIDEN_LOS_HABLANTES / 2**20
        + MEMORIA_BASE_HABLANTES_MB
    )
    minutos = int(audio.duracion_s // 60)
    return (
        f"Transcrito sin separar a los hablantes: son {minutos} minutos de audio y "
        f"distinguir las voces pediría unos {hacen_falta / 1024:.1f} GB de memoria, "
        f"con {libre_mb / 1024:.1f} GB libres. Cierra algún programa y repítelo si "
        "los necesitas, o pártelo en trozos más cortos."
    )


def _cuanto_queda(duracion_s: float) -> str:
    """Lo primero que se lee en la ventana de progreso: qué hay y cuánto va a costar."""
    minutos, segundos = divmod(int(duracion_s), 60)
    horas, minutos = divmod(minutos, 60)
    largo = f"{horas}:{minutos:02d}:{segundos:02d}" if horas else f"{minutos}:{segundos:02d}"
    if duracion_s < LARGO_PARA_AVISAR_S:
        return f"{largo} de audio. Transcribiendo…"
    espera = max(1, round(duracion_s / VECES_MAS_RAPIDO_QUE_EL_AUDIO / 60))
    return f"{largo} de audio: esto tarda unos {espera} minutos. Puedes seguir a lo tuyo."


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
