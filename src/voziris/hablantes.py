"""Quién habla cuándo: separación de hablantes con sherpa-onnx.

Dos modelos ONNX, en CPU, que se descargan la primera vez a `modelos/hablantes/`:

  - Segmentación: pyannote segmentation 3.0 (MIT), 6 MB. Dice dónde hay voz
    y cuándo cambia, incluso con solapes.
  - Huella de voz: NVIDIA TitaNet small (CC-BY-4.0), 38 MB. Convierte cada
    trozo de voz en un vector que se parece más cuanto más se parece la voz.

La agrupación NO se deja a sherpa-onnx. Su agrupación con número fijo de
hablantes corta el árbol donde toca y, con una llamada de teléfono, el
segundo «hablante» acaba siendo un ruido de tres segundos mientras las dos
voces reales van juntas (medido en VOZ-71: 15,9 min contra 0,1). Aquí se
hace en tres pasos con control de cada uno:

  1. sherpa-onnx segmenta y se le pide que sobresegmente (umbral bajo): de
     ahí solo se toman las fronteras, dónde empieza y acaba cada voz.
  2. Cada segmento se parte en piezas de ≤ 6 s y se calcula la huella de
     cada pieza con TitaNet.
  3. Agrupación aglomerativa de enlace medio con distancia coseno. Con N
     conocido se corta en N grupos *grandes* (los grupos residuales, menos
     del 3 % del habla, no cuentan y se reparten al grupo más parecido).
     En automático se corta por umbral, calibrado con una llamada real.

No sabe nombres: devuelve «Hablante 1, 2, 3…» por orden de aparición.
"""

from __future__ import annotations

import logging
import tarfile
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from voziris.errores import VozirisError
from voziris.tipos import SAMPLE_RATE, Audio

log = logging.getLogger(__name__)

URL_SEGMENTACION = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    "speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
)
ARCHIVO_SEGMENTACION = "pyannote-segmentation-3-0.onnx"
URL_EMBEDDINGS = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    "speaker-recongition-models/nemo_en_titanet_small.onnx"
)
ARCHIVO_EMBEDDINGS = "nemo_en_titanet_small.onnx"

UMBRAL_AUTOMATICO = 0.75
"""Distancia coseno (enlace medio) por encima de la cual dos grupos son dos personas.

Calibrado en VOZ-71 con una llamada de dos personas: las dos voces se funden
a 0,86 y todo lo demás (misma voz con distinto ruido) por debajo de 0,68.
"""
UMBRAL_SEGMENTACION = 0.5
"""Lo que se le pide a sherpa-onnx: bajo, para que sobresegmente y no pegue dos voces."""
PIEZA_MAXIMA_S = 6.0
"""Un segmento largo se parte en piezas de como mucho esto para calcular huellas."""
PIEZA_MINIMA_S = 0.6
"""Por debajo, la huella no es fiable: la pieza hereda el hablante de la vecina."""
PIEZAS_MAXIMAS = 800
"""Con grabaciones de horas, las piezas se alargan para no pasar de aquí (coste n²)."""
RESTO_MINIMO = 0.03
"""Un grupo con menos del 3 % del habla es un resto, no una persona."""
PAUSA_MISMA_INTERVENCION_S = 1.0
"""Dos segmentos del mismo hablante separados por menos de esto son una intervención."""


class HablantesNoDisponibles(VozirisError):
    """Faltan los modelos o sherpa-onnx, y no se han podido conseguir."""


@dataclass
class Intervencion:
    inicio: float
    fin: float
    hablante: int  # 0, 1, 2… en orden de aparición

    @property
    def duracion(self) -> float:
        return self.fin - self.inicio


Progreso = Callable[[str, float | None], None]


class SeparadorHablantes:
    def __init__(
        self,
        carpeta_modelos: Path,
        al_progresar: Progreso | None = None,
        hilos: int = 0,
    ) -> None:
        self._carpeta = Path(carpeta_modelos) / "hablantes"
        self._al_progresar = al_progresar or (lambda m, f: None)
        self._hilos = hilos or 4
        self.error: str | None = None

    # --- modelos --------------------------------------------------------------------

    def precalentar(self) -> None:
        """Descarga lo que falte. No lanza: `disponible()` lo dirá."""
        try:
            self._asegurar_modelos()
            import sherpa_onnx  # noqa: F401 — solo comprobar que está
        except Exception as e:  # noqa: BLE001
            self.error = str(e)
            log.exception("separador de hablantes no disponible")

    def disponible(self) -> bool:
        return (
            self.error is None
            and (self._carpeta / ARCHIVO_SEGMENTACION).is_file()
            and (self._carpeta / ARCHIVO_EMBEDDINGS).is_file()
        )

    def _asegurar_modelos(self) -> None:
        self._carpeta.mkdir(parents=True, exist_ok=True)
        segmentacion = self._carpeta / ARCHIVO_SEGMENTACION
        if not segmentacion.is_file():
            self._al_progresar("Descargando el modelo de segmentación (6 MB)…", 0.0)
            with tempfile.TemporaryDirectory() as tmp:
                comprimido = Path(tmp) / "seg.tar.bz2"
                _descargar(URL_SEGMENTACION, comprimido)
                with tarfile.open(comprimido, "r:bz2") as tar:
                    miembro = next(
                        m for m in tar.getmembers() if m.name.endswith("/model.onnx")
                    )
                    extraido = tar.extractfile(miembro)
                    assert extraido is not None
                    segmentacion.write_bytes(extraido.read())
        embeddings = self._carpeta / ARCHIVO_EMBEDDINGS
        if not embeddings.is_file():
            self._al_progresar("Descargando el modelo de voces (38 MB)…", 0.0)
            temporal = embeddings.with_suffix(".parcial")
            _descargar(URL_EMBEDDINGS, temporal, self._al_progresar)
            temporal.replace(embeddings)
        self._al_progresar("Modelos de hablantes listos", 1.0)

    # --- uso --------------------------------------------------------------------------

    def separar(self, audio: Audio, hablantes: int | None = None) -> list[Intervencion]:
        """Intervenciones ordenadas. `hablantes=None` es automático.

        Raises:
            HablantesNoDisponibles: sin modelos.
        """
        if not self.disponible():
            raise HablantesNoDisponibles(self.error or "faltan los modelos de hablantes")
        if audio.sr != SAMPLE_RATE:
            raise HablantesNoDisponibles(f"el audio tiene que ir a {SAMPLE_RATE} Hz")
        muestras = np.ascontiguousarray(audio.muestras, dtype=np.float32)
        total = len(muestras) / SAMPLE_RATE

        segmentos = self._segmentar(muestras)
        log.info("hablantes: %d segmentos de voz en %.0f s de audio", len(segmentos), total)
        if not segmentos:
            return []
        piezas = trocear_en_piezas(segmentos)
        con_huella = [i for i, (a, b) in enumerate(piezas) if b - a >= PIEZA_MINIMA_S]
        huellas = self._huellas(muestras, [piezas[i] for i in con_huella])
        duraciones = np.array([piezas[i][1] - piezas[i][0] for i in con_huella])
        self._al_progresar("Agrupando voces…", None)
        grupos = agrupar(huellas, duraciones, hablantes, UMBRAL_AUTOMATICO)
        etiquetas = heredar_etiquetas(len(piezas), dict(zip(con_huella, grupos, strict=True)))
        brutos = [(a, b, h) for (a, b), h in zip(piezas, etiquetas, strict=True)]
        log.info(
            "hablantes: %d piezas, %d con huella, %d hablantes",
            len(piezas), len(con_huella), len(set(etiquetas)),
        )
        return fundir(renumerar(brutos))

    def _segmentar(self, muestras: np.ndarray) -> list[tuple[float, float]]:
        """Fronteras de voz según pyannote; las etiquetas de sherpa se ignoran."""
        import sherpa_onnx

        config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
            segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
                pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                    model=str(self._carpeta / ARCHIVO_SEGMENTACION)
                ),
            ),
            embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                model=str(self._carpeta / ARCHIVO_EMBEDDINGS), num_threads=self._hilos
            ),
            clustering=sherpa_onnx.FastClusteringConfig(
                num_clusters=-1, threshold=UMBRAL_SEGMENTACION
            ),
            min_duration_on=0.3,
            min_duration_off=0.5,
        )
        separador = sherpa_onnx.OfflineSpeakerDiarization(config)

        def progreso(procesados: int, total_: int) -> int:
            self._al_progresar("Buscando dónde habla cada voz…", procesados / max(1, total_))
            return 0

        resultado = separador.process(muestras, callback=progreso).sort_by_start_time()
        return [(float(s.start), float(s.end)) for s in resultado]

    def _huellas(self, muestras: np.ndarray, piezas: list[tuple[float, float]]) -> np.ndarray:
        import sherpa_onnx

        extractor = sherpa_onnx.SpeakerEmbeddingExtractor(
            sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                model=str(self._carpeta / ARCHIVO_EMBEDDINGS), num_threads=self._hilos
            )
        )
        salida = []
        for i, (a, b) in enumerate(piezas):
            if i % 20 == 0:
                self._al_progresar("Reconociendo las voces…", i / max(1, len(piezas)))
            flujo = extractor.create_stream()
            flujo.accept_waveform(
                sample_rate=SAMPLE_RATE,
                waveform=muestras[int(a * SAMPLE_RATE) : int(b * SAMPLE_RATE)],
            )
            flujo.input_finished()
            vector = np.asarray(extractor.compute(flujo), dtype=np.float32)
            salida.append(vector / (np.linalg.norm(vector) + 1e-9))
        return np.stack(salida) if salida else np.zeros((0, 1), dtype=np.float32)


# --- piezas y agrupación (puro, con tests) -----------------------------------------------


def trocear_en_piezas(
    segmentos: list[tuple[float, float]], maximo: float = PIEZA_MAXIMA_S
) -> list[tuple[float, float]]:
    """Cada segmento en piezas iguales de ≤ `maximo` s (alargado si hay demasiadas)."""
    habla = sum(b - a for a, b in segmentos)
    maximo = max(maximo, habla / PIEZAS_MAXIMAS)
    piezas: list[tuple[float, float]] = []
    for a, b in segmentos:
        n = max(1, int(np.ceil((b - a) / maximo)))
        paso = (b - a) / n
        piezas.extend((a + i * paso, a + (i + 1) * paso) for i in range(n))
    return piezas


def agrupar(
    huellas: np.ndarray,
    duraciones: np.ndarray,
    hablantes: int | None = None,
    umbral: float = UMBRAL_AUTOMATICO,
    resto: float = RESTO_MINIMO,
) -> list[int]:
    """Etiqueta de grupo de cada huella (vectores unitarios, uno por fila).

    Con `hablantes` se corta el árbol donde quedan tantos grupos *grandes*;
    sin él, por `umbral`. Los grupos pequeños (menos de `resto` del habla)
    se reparten al grupo grande de huella media más parecida.
    """
    n = len(huellas)
    if n == 0:
        return []
    if n == 1:
        return [0]
    fusiones = _enlace_medio(huellas)
    habla = float(duraciones.sum()) or 1.0

    def grandes(etiquetas: list[int]) -> list[int]:
        tiempo: dict[int, float] = {}
        for e, d in zip(etiquetas, duraciones, strict=True):
            tiempo[e] = tiempo.get(e, 0.0) + float(d)
        orden = sorted(tiempo, key=lambda k: -tiempo[k])
        return [k for k in orden if tiempo[k] / habla >= resto] or orden[:1]

    if hablantes:
        k = min(max(1, hablantes), n)
        etiquetas = _cortar(fusiones, n, k)
        while len(grandes(etiquetas)) < hablantes and k < n:
            k += 1
            etiquetas = _cortar(fusiones, n, k)
        principales = grandes(etiquetas)[:hablantes]
    else:
        k = 1 + sum(1 for d, _, _ in fusiones if d > umbral)
        etiquetas = _cortar(fusiones, n, k)
        principales = grandes(etiquetas)

    centroides = {}
    for p in principales:
        miembros = huellas[[i for i, e in enumerate(etiquetas) if e == p]]
        centro = miembros.mean(axis=0)
        centroides[p] = centro / (np.linalg.norm(centro) + 1e-9)
    salida = []
    for i, e in enumerate(etiquetas):
        if e in centroides:
            salida.append(e)
        else:
            salida.append(max(centroides, key=lambda p: float(huellas[i] @ centroides[p])))
    return salida


def _enlace_medio(huellas: np.ndarray) -> list[tuple[float, int, int]]:
    """Fusiones (distancia, grupo que absorbe, grupo absorbido) de menor a mayor distancia."""
    n = len(huellas)
    distancia = 1.0 - huellas @ huellas.T
    np.fill_diagonal(distancia, np.inf)
    activo = np.ones(n, dtype=bool)
    tamano = np.ones(n)
    fusiones: list[tuple[float, int, int]] = []
    for _ in range(n - 1):
        plano = int(np.argmin(distancia))
        i, j = divmod(plano, n)
        if i > j:
            i, j = j, i
        d = float(distancia[i, j])
        # Lance–Williams para enlace medio: la distancia del grupo unido a
        # cualquier otro es la media ponderada por tamaños.
        nueva = (tamano[i] * distancia[i] + tamano[j] * distancia[j]) / (tamano[i] + tamano[j])
        distancia[i, :] = nueva
        distancia[:, i] = nueva
        distancia[i, i] = np.inf
        distancia[j, :] = np.inf
        distancia[:, j] = np.inf
        tamano[i] += tamano[j]
        activo[j] = False
        fusiones.append((d, i, j))
    return fusiones


def _cortar(fusiones: list[tuple[float, int, int]], n: int, k: int) -> list[int]:
    """Etiquetas cuando quedan `k` grupos: se aplican las primeras n-k fusiones."""
    etiqueta = list(range(n))
    for _, i, j in fusiones[: max(0, n - k)]:
        etiqueta = [i if e == j else e for e in etiqueta]
    return etiqueta


def heredar_etiquetas(cuantas: int, conocidas: dict[int, int]) -> list[int]:
    """Las piezas sin huella toman la etiqueta de la anterior con huella (o la siguiente)."""
    salida: list[int] = []
    ultima: int | None = None
    for i in range(cuantas):
        if i in conocidas:
            ultima = conocidas[i]
        salida.append(-1 if ultima is None else ultima)
    siguiente: int | None = None
    for i in range(cuantas - 1, -1, -1):
        if salida[i] >= 0:
            siguiente = salida[i]
        elif siguiente is not None:
            salida[i] = siguiente
    return [max(0, e) for e in salida]


def renumerar(segmentos: list[tuple[float, float, int]]) -> list[tuple[float, float, int]]:
    """Hablante 0 es el primero que habla, 1 el siguiente distinto, etc."""
    orden: dict[int, int] = {}
    salida = []
    for a, b, h in segmentos:
        if h not in orden:
            orden[h] = len(orden)
        salida.append((a, b, orden[h]))
    return salida


def fundir(
    segmentos: list[tuple[float, float, int]], pausa: float = PAUSA_MISMA_INTERVENCION_S
) -> list[Intervencion]:
    """Segmentos consecutivos del mismo hablante con pausa corta → una intervención."""
    salida: list[Intervencion] = []
    for a, b, h in segmentos:
        if salida and salida[-1].hablante == h and a - salida[-1].fin <= pausa:
            salida[-1].fin = max(salida[-1].fin, b)
        else:
            salida.append(Intervencion(a, b, h))
    return salida


def _descargar(url: str, destino: Path, al_progresar: Progreso | None = None) -> None:
    import httpx

    with httpx.stream("GET", url, follow_redirects=True, timeout=60) as respuesta:
        respuesta.raise_for_status()
        total = int(respuesta.headers.get("content-length") or 0)
        leido = 0
        with open(destino, "wb") as archivo:
            for trozo in respuesta.iter_bytes(1 << 16):
                archivo.write(trozo)
                leido += len(trozo)
                if total and al_progresar:
                    al_progresar(f"Descargando el modelo de voces… {leido * 100 // total} %",
                                 leido / total)

