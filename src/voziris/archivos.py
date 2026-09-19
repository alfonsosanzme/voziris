"""Decodificar grabaciones y vídeos al formato del pipeline: 16 kHz mono float32.

Se usa PyAV, que lleva ffmpeg dentro: así el paquete portable abre lo que
graba un móvil sin que el usuario instale nada. Son unos 65 MB más de DLL,
que es el precio de no depender de un ffmpeg externo.

De un vídeo se saca su pista de sonido y se transcribe igual que una
grabación: para el resto del programa no hay diferencia (VOZ-77).

Cuando un archivo trae VARIAS pistas de audio hay que elegir, y la elección
importa más de lo que parece. Se eligió una sola pista, nunca la suma de
todas, por un motivo medido: sumar dos voces que hablan a la vez produce una
transcripción entreverada que parece correcta y no lo es, y nadie la detecta
leyendo el Markdown. Equivocarse de pista, en cambio, da un texto incompleto
pero verdadero, y el aviso dice cómo repetirlo. Entre un error que se nota y
uno que no, se prefiere el que se nota.

Issue: VOZ-70 (audio), VOZ-77 (vídeo y pistas).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from voziris.errores import VozirisError
from voziris.tipos import SAMPLE_RATE, Audio

log = logging.getLogger(__name__)

FORMATOS_AUDIO = (
    ".m4a", ".mp3", ".wav", ".flac", ".ogg", ".oga", ".opus", ".aac", ".wma", ".aiff", ".aif",
    ".amr", ".caf", ".mka",
)
FORMATOS_VIDEO = (
    ".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".wmv", ".mpg", ".mpeg", ".mts", ".m2ts",
    ".3gp",
)
"""Contenedores de vídeo: iPhone y Mac (.mov), OBS (.mkv), cámaras (.mts), archivo viejo (.avi).

Fuera a propósito: las imágenes (.gif, .png, .heic…), que ffmpeg abre como un
vídeo de un fotograma y no tienen sonido, y que meterían a Voziris en el botón
derecho de todas las fotos del equipo; y .ts, que es vídeo para ffmpeg pero
TypeScript para quien programa. Lo que no está en la lista se puede transcribir
igual eligiéndolo a mano: ffmpeg reconoce el archivo por su contenido, no por
la extensión."""
FORMATOS = FORMATOS_AUDIO + FORMATOS_VIDEO
"""Lo que se ofrece en el diálogo y en el menú contextual. ffmpeg abre bastante más."""

IDIOMAS_INDEFINIDOS = frozenset({"", "und", "unk", "mis", "zxx", "qaa"})
"""Lo que ffmpeg pone cuando no sabe el idioma: no cuenta como idioma."""
EQUIVALENTES = {
    "es": {"es", "spa", "esp", "cas"}, "en": {"en", "eng"}, "fr": {"fr", "fra", "fre"},
    "de": {"de", "deu", "ger"}, "it": {"it", "ita"}, "pt": {"pt", "por"},
    "ca": {"ca", "cat"}, "gl": {"gl", "glg"}, "eu": {"eu", "eus", "baq"},
}
"""ffmpeg escribe ISO 639-2 («spa») y la configuración usa ISO 639-1 («es»)."""
TITULOS_GENERICOS = frozenset({"stereo", "mono", "core media audio", "audio"})
"""Nombres que ponen las cámaras y los móviles y que no dicen nada: se ignoran."""
PREFIJOS_GENERICOS = ("soundhandle",)
"""Igual, pero por el principio: Android escribe «SoundHandle» y ffmpeg «SoundHandler»."""


class ArchivoNoLegible(VozirisError):
    """El archivo no existe, no es audio o está dañado."""


@dataclass(frozen=True)
class Pista:
    """Una pista de audio dentro del archivo."""

    n: int
    """Ordinal entre las de audio, empezando en 0."""
    titulo: str | None
    idioma: str | None
    """None si el archivo no lo dice o dice «indefinido»."""
    canales: int
    predeterminada: bool
    secundaria: bool
    """Comentario del director o audiodescripción: no es la pista que se quiere."""

    @property
    def etiqueta(self) -> str:
        """«2: English · eng», para el aviso y los mensajes de error."""
        partes = [x for x in (self.titulo, self.idioma) if x]
        if partes:
            return f"{self.n + 1}: " + " · ".join(partes)
        canales = "canal" if self.canales == 1 else "canales"
        return f"{self.n + 1}: {self.canales} {canales}"


def pistas(ruta: Path) -> list[Pista]:
    """Las pistas de audio del archivo, sin decodificar nada."""
    import av

    ruta = Path(ruta)
    if not ruta.is_file():
        raise ArchivoNoLegible(f"No existe {ruta}")
    try:
        with av.open(str(ruta)) as contenedor:
            return _pistas_de(contenedor)
    except ArchivoNoLegible:
        raise
    except Exception as e:  # noqa: BLE001 — av.error.* son muchas clases distintas
        raise ArchivoNoLegible(_por_que_no_se_abre(ruta, e)) from e


def _por_que_no_se_abre(ruta: Path, error: Exception, accion: str = "abrir") -> str:
    """El error de ffmpeg no se le enseña a nadie: dice «[Errno 1094995529]»."""
    import av

    if isinstance(error, av.error.InvalidDataError):
        return (
            f"{ruta.name} parece estar incompleto o dañado. "
            "¿Se interrumpió la grabación o la descarga?"
        )
    return f"No se pudo {accion} {ruta.name}: {error}"


def _segundos_de_pista(flujo: Any) -> float | None:
    """Lo que dice durar una pista concreta. None si no lo dice."""
    if flujo.duration is None or flujo.time_base is None:
        return None
    segundos = float(flujo.duration * flujo.time_base)
    return segundos if 0 < segundos < 24 * 3600 else None


PARECIDAS_S = 0.5
"""Dos duraciones que no se diferencian en esto son, a efectos prácticos, la misma."""


def _cuanto_deberia_durar(contenedor: Any, flujo: Any, av: Any) -> float | None:
    """Cuánto sonido debería haber en esta pista, o None si no hay forma de saberlo.

    Es la cifra contra la que se decide si un archivo está a medias, y por eso
    prefiere callar a inventarse un número: un aviso falso sobre una grabación
    buena asusta y no se puede comprobar, mientras que no avisar deja al
    usuario donde estaba.

    Sin imagen, la duración del contenedor es la del sonido y vale. Con
    imagen no vale, porque la manda la pista más larga, que casi siempre es el
    vídeo: un micrófono que entra tarde o se para antes daría un archivo
    «incompleto» estando sano. Ahí solo sirve lo que declare la propia pista,
    y ni eso cuando Matroska ha copiado la del contenedor, que es lo que hace
    porque el formato no guarda duraciones por pista.
    """
    del_contenedor = _segundos_de(contenedor, av)
    if not contenedor.streams.video:
        return del_contenedor
    de_pista = _segundos_de_pista(flujo)
    if de_pista is None:
        return None
    if del_contenedor is not None and abs(de_pista - del_contenedor) < PARECIDAS_S:
        return None  # copiada del contenedor: no dice nada de esta pista
    return de_pista


def _pistas_de(contenedor: Any) -> list[Pista]:
    from av.stream import Disposition

    descartables = Disposition.comment | Disposition.visual_impaired | Disposition.descriptions
    salida = []
    for n, flujo in enumerate(contenedor.streams.audio):
        idioma = str(
            flujo.metadata.get("language") or flujo.metadata.get("LANGUAGE") or ""
        ).lower().strip()
        salida.append(
            Pista(
                n=n,
                titulo=_titulo_de(flujo),
                idioma=None if idioma in IDIOMAS_INDEFINIDOS else idioma,
                canales=int(getattr(flujo.codec_context, "channels", 0) or 0),
                predeterminada=bool(flujo.disposition & Disposition.default),
                secundaria=bool(flujo.disposition & descartables),
            )
        )
    return salida


def _titulo_de(flujo: Any) -> str | None:
    for clave in ("title", "TITLE", "name", "handler_name", "HANDLER_NAME"):
        valor = str(flujo.metadata.get(clave) or "").strip()
        if not valor:
            continue
        minusculas = valor.lower()
        if minusculas not in TITULOS_GENERICOS and not minusculas.startswith(PREFIJOS_GENERICOS):
            return valor
    return None


def elegir_pista(disponibles: list[Pista], idioma: str | None = None) -> tuple[Pista, str]:
    """La pista que se transcribe y por qué. Nunca suma varias: eso se pide aparte.

    El orden: se descartan las que no son la principal (comentario,
    audiodescripción, sin canales); si quedan varios IDIOMAS distintos es un
    doblaje, y manda el idioma configurado; si no, la marcada como
    predeterminada; y si nada distingue, la primera.
    """
    utiles = [p for p in disponibles if p.canales > 0]
    principales = [p for p in utiles if not p.secundaria] or utiles
    if not principales:
        raise ArchivoNoLegible("ninguna pista de audio tiene canales")
    if len(principales) == 1:
        return principales[0], "única"
    idiomas = {p.idioma for p in principales if p.idioma}
    if len(idiomas) >= 2:
        # Doblaje o versiones: la que esté en el idioma que se va a transcribir.
        if idioma:
            equivalentes = EQUIVALENTES.get(idioma[:2].lower(), {idioma[:2].lower()})
            for pista in principales:
                if pista.idioma in equivalentes:
                    return pista, f"está en {pista.idioma}, el idioma configurado"
        marcadas = [p for p in principales if p.predeterminada]
        if len(marcadas) == 1:
            return marcadas[0], "varios idiomas, y es la marcada como predeterminada"
        return principales[0], "varios idiomas, y es la primera"
    marcadas = [p for p in principales if p.predeterminada]
    if len(marcadas) == 1:
        return marcadas[0], "es la marcada como predeterminada"
    return principales[0], "es la primera"


def aviso_de_pistas(disponibles: list[Pista], elegida: Pista, es_video: bool) -> str | None:
    """Qué contarle al usuario cuando había más de una pista. None si solo había una."""
    if len(disponibles) < 2:
        return None
    lista = "; ".join(
        f"**{p.etiqueta}**" if p.n == elegida.n else p.etiqueta for p in disponibles
    )
    cual = "El vídeo" if es_video else "El archivo"
    return (
        f"{cual} trae {len(disponibles)} pistas de audio y se ha transcrito solo la "
        f"pista {elegida.n + 1}. Pistas: {lista}. Para usar otra, repítelo con --pista N."
    )


def decodificar(
    ruta: Path,
    sr: int = SAMPLE_RATE,
    pista: int | str = "auto",
    idioma: str | None = None,
) -> tuple[Audio, list[str]]:
    """Todo el archivo como `Audio` mono a `sr`, y los avisos que haya que dar.

    Args:
        ruta: la grabación o el vídeo.
        sr: frecuencia de salida.
        pista: "auto" elige por `elegir_pista`; un número es el ordinal (empezando
            en 1, como se le enseña al usuario); "todas" las suma, que solo vale
            si las voces no se solapan.
        idioma: el idioma configurado, para desempatar en los doblajes.

    Raises:
        ArchivoNoLegible: no se puede abrir, no trae audio, o la pista pedida no existe.
    """
    ruta = Path(ruta)
    if not ruta.is_file():
        raise ArchivoNoLegible(f"No existe {ruta}")
    try:
        import av
    except ImportError as e:  # pragma: no cover - dependencia declarada
        raise ArchivoNoLegible("Falta PyAV, que decodifica el audio") from e

    avisos: list[str] = []
    try:
        with av.open(str(ruta)) as contenedor:
            if not contenedor.streams.audio:
                raise ArchivoNoLegible(
                    f"{ruta.name} no tiene sonido: es un vídeo mudo"
                    if contenedor.streams.video
                    else f"{ruta.name} no tiene ninguna pista de audio"
                )
            es_video = bool(contenedor.streams.video)
            disponibles = _pistas_de(contenedor)
            elegidas, avisos = _que_pistas(disponibles, pista, idioma, es_video, ruta)
            segundos = _segundos_de(contenedor, av)
            # Lo que dice durar la pista que se va a leer. NO vale la duración
            # del contenedor: esa la manda la pista más larga (la imagen) e
            # incluye el desfase de arranque, así que un vídeo sano cuyo micro
            # entre tarde o se pare antes se tomaría por un archivo a medias.
            esperados = max(
                (_cuanto_deberia_durar(contenedor, contenedor.streams.audio[p.n], av) or 0.0)
                for p in elegidas
            )
            muestras = np.zeros(0, dtype=np.float32)
            for orden, pista_elegida in enumerate(elegidas):
                trozo = _decodificar_pista(
                    contenedor, contenedor.streams.audio[pista_elegida.n], sr, av, segundos,
                    rebobinar=orden > 0,
                )
                # Se suma según llega: con «todas» en un vídeo doblado de horas,
                # guardarlas para sumarlas al final serían varios GB de más.
                muestras = trozo if not len(muestras) else _sumar(muestras, trozo)
            if len(elegidas) > 1:
                _recortar_si_satura(muestras)
    except ArchivoNoLegible:
        raise
    except Exception as e:  # noqa: BLE001 — av.error.* son muchas clases distintas
        raise ArchivoNoLegible(_por_que_no_se_abre(ruta, e, "leer")) from e
    if not len(muestras):
        raise ArchivoNoLegible(f"{ruta.name} está vacío")
    leidos = len(muestras) / sr
    if esperados and leidos < esperados * 0.9:
        # Media transcripción que parece entera es peor que ninguna: se dice.
        avisos.append(
            f"{ruta.name} parece estar incompleto: la pista de sonido dice durar "
            f"{_reloj(esperados)} y solo se han podido leer {_reloj(leidos)}. "
            "¿Se cortó la grabación o la descarga?"
        )
        log.warning("%s: leídos %.0f s de los %.0f que dice la pista", ruta.name, leidos,
                    esperados)
    log.info(
        "decodificado %s: %.1f s (%d pista(s) de %d)",
        ruta.name, leidos, len(elegidas), len(disponibles),
    )
    return Audio(muestras=muestras, sr=sr), avisos


def _reloj(segundos: float) -> str:
    if 0 < segundos < 1:
        return "menos de un segundo"
    minutos, seg = divmod(int(segundos), 60)
    horas, minutos = divmod(minutos, 60)
    return f"{horas}:{minutos:02d}:{seg:02d}" if horas else f"{minutos}:{seg:02d}"


def _que_pistas(
    disponibles: list[Pista], pedida: int | str, idioma: str | None, es_video: bool, ruta: Path
) -> tuple[list[Pista], list[str]]:
    """Qué pistas hay que decodificar y qué avisos genera esa elección."""
    if pedida == "todas":
        if len(disponibles) < 2:
            return disponibles, []
        return disponibles, [
            f"Se han sumado las {len(disponibles)} pistas de audio en una. Si en el original "
            "hay voces que se solapan, el texto saldrá entremezclado."
        ]
    if isinstance(pedida, int) or (isinstance(pedida, str) and pedida.isdigit()):
        numero = int(pedida)
        elegida = next((p for p in disponibles if p.n == numero - 1), None)
        if elegida is None:
            cuales = "; ".join(p.etiqueta for p in disponibles)
            raise ArchivoNoLegible(
                f"{ruta.name} no tiene la pista {numero}. "
                f"Tiene {len(disponibles)} "
                f"{'pista' if len(disponibles) == 1 else 'pistas'} — {cuales}"
            )
        return [elegida], []
    elegida, motivo = elegir_pista(disponibles, idioma)
    log.info("pista elegida: %s (%s)", elegida.etiqueta, motivo)
    aviso = aviso_de_pistas(disponibles, elegida, es_video)
    return [elegida], [aviso] if aviso else []


def _decodificar_pista(
    contenedor: Any, flujo: Any, sr: int, av: Any, segundos: float | None = None,
    rebobinar: bool = False,
) -> np.ndarray:
    """El audio de una pista, en un solo array.

    Se reserva sitio de una vez según la duración que dice la cabecera, en vez
    de juntar mil trozos al final. No es una micro-optimización: juntarlos
    obliga a tener a la vez los trozos y el resultado, o sea el doble de
    memoria. Con las tres horas y media del vídeo más largo del cliente eso
    son 1,4 GB en lugar de 725 MB, y en el mismo proceso vive el modelo, que
    ocupa otros 640 MB.
    """
    remuestreador = av.AudioResampler(format="flt", layout="mono", rate=sr)
    capacidad = int((segundos or 0) * sr * 1.02) + sr if segundos else 0
    salida = np.empty(capacidad, dtype=np.float32) if capacidad else np.empty(0, dtype=np.float32)
    usado = 0
    if rebobinar:
        # Solo al pasar de una pista a la siguiente, que es cosa de «--pista
        # todas». Rebobinar antes de la PRIMERA pierde audio en Matroska
        # cuando el contenedor no arranca en cero.
        contenedor.seek(0)

    def guardar(bloque: np.ndarray) -> None:
        nonlocal salida, usado
        if usado + len(bloque) > len(salida):
            # La cabecera mentía o no decía nada: se dobla y se sigue.
            crecida = np.empty(max(len(salida) * 2, usado + len(bloque), sr * 60), np.float32)
            crecida[:usado] = salida[:usado]
            salida = crecida
        salida[usado : usado + len(bloque)] = bloque
        usado += len(bloque)

    for cuadro in contenedor.decode(flujo):
        for trozo in remuestreador.resample(cuadro):
            guardar(trozo.to_ndarray().reshape(-1))
    for trozo in remuestreador.resample(None):  # lo que quede en el remuestreador
        guardar(trozo.to_ndarray().reshape(-1))
    return salida[:usado]


def _segundos_de(contenedor: Any, av: Any) -> float | None:
    """Lo que dice la cabecera, para reservar sitio. None si no lo dice.

    Se le resta el arranque: en Matroska la duración es la del último
    timestamp, así que un archivo que empieza en el minuto 3 dice durar tres
    minutos de más, que no son contenido de nadie.
    """
    if contenedor.duration is None:
        return None
    segundos = float(contenedor.duration) / av.time_base
    if contenedor.start_time is not None:
        segundos -= float(contenedor.start_time) / av.time_base
    return segundos if 0 < segundos < 24 * 3600 else None


def _sumar(acumulado: np.ndarray, otra: np.ndarray) -> np.ndarray:
    """Suma una pista más sobre lo que ya hay, alargando si esta es más larga."""
    if not len(otra):
        return acumulado
    if len(otra) > len(acumulado):
        mayor = np.zeros(len(otra), dtype=np.float32)
        mayor[: len(acumulado)] = acumulado
        acumulado = mayor
    acumulado[: len(otra)] += otra
    return acumulado


def _recortar_si_satura(muestras: np.ndarray) -> None:
    """Sumar pistas puede pasarse de 1: se baja todo por igual, sin copiar el array."""
    if not len(muestras):
        return
    pico = 0.0
    for i in range(0, len(muestras), 1 << 20):
        pico = max(pico, float(np.abs(muestras[i : i + (1 << 20)]).max()))
    if pico > 1.0:
        muestras /= pico


def duracion_s(ruta: Path) -> float | None:
    """Duración según la cabecera, sin decodificar. None si no se sabe."""
    try:
        import av

        with av.open(str(ruta)) as contenedor:
            if contenedor.duration is None:
                return None
            return float(contenedor.duration) / av.time_base
    except Exception:  # noqa: BLE001
        return None
