"""B1 — motor local sin GPU: Parakeet TDT 0.6B v3 en ONNX int8.

Por qué este modelo y no Whisper: RTF 0,033 frente a 0,13 en CPU, WER 6,34 %
frente a 7,44 %, y —lo decisivo— **emite puntuación y mayúsculas** en los 25
idiomas europeos que cubre, español incluido. Eso resuelve C1 sin red y sin
LLM. No hay alternativa razonable si el motor no puntuara: sherpa-onnx solo
publica modelos de puntuación para inglés y chino.

Medido en el hito 0 (`docs/H0.md`): RTF 0,064–0,074 en el portátil del
cliente, 2 s de carga y 1,2 GB de RAM en int8. El modelo en int8 exige que
el audio llegue normalizado (lo hace `Captura`); en fp32 no le importa, pero
cuesta el doble de tiempo y 2,5 GB de RAM. `cuantizacion` lo elige.

Cómo descarga onnx-asr (leído en su `resolver.py`, versión 0.12): si la
carpeta `path` existe, entra en modo offline y solo busca ahí; si no existe,
descarga en ella. Por eso cada modelo y cuantización tiene su propia carpeta
bajo `carpeta`, y una descarga interrumpida —carpeta creada, archivos a
medias— se detecta y se borra antes de volver a intentarlo.

Licencia CC-BY-4.0: la atribución a NVIDIA es obligatoria. Ver ATRIBUCIONES.md.

Issue: VOZ-11.
"""

from __future__ import annotations

import logging
import shutil
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from voziris.errores import MotorNoDisponible, TranscripcionFallida
from voziris.tipos import Audio, Transcripcion

log = logging.getLogger(__name__)

Progreso = Callable[[str, float | None], None]
"""Callback de progreso: (mensaje para el usuario, fracción 0..1 o None si no se sabe)."""

TAMANO_APROX_MB: dict[str, int] = {"int8": 640, "fp32": 2400}
"""Tamaño de la descarga si Hugging Face no dice el real. Medido en H0."""

DURACION_MINIMA_S = 0.1
"""Por debajo de esto no hay nada que transcribir: ni se llama al modelo."""


class MotorLocal:
    """Implementa `MotorSTT` sobre `onnx-asr`.

    El modelo son ~640 MB (int8) que NO viajan dentro del ejecutable: se
    descargan a `carpeta / "<modelo>-<cuantizacion>"` en el primer arranque,
    con progreso visible a través de `al_progresar`.
    """

    nombre = "local"
    requiere_red = False

    def __init__(
        self,
        modelo: str,
        carpeta: Path,
        hilos: int = 0,
        cuantizacion: str = "int8",
        al_progresar: Progreso | None = None,
    ) -> None:
        self._modelo_id = modelo
        self._carpeta_modelos = Path(carpeta)
        self._hilos = hilos
        self._cuantizacion = cuantizacion
        self._al_progresar = al_progresar
        self._modelo: Any = None
        self._error: str | None = None
        self._cargando = threading.Lock()

    @property
    def carpeta_modelo(self) -> Path:
        """Dónde vive (o vivirá) este modelo con esta cuantización."""
        return self._carpeta_modelos / f"{self._modelo_id}-{self._cuantizacion}"

    @property
    def error(self) -> str | None:
        """Por qué no está disponible, para el icono de error y el log."""
        return self._error

    # --- carga -------------------------------------------------------------

    def precalentar(self) -> None:
        """Carga el modelo en memoria (~1,2 GB de RAM en int8).

        Si el modelo no está en disco lo descarga, informando por
        `al_progresar`. Una descarga anterior a medias se borra y se repite.
        No lanza: si falla, `disponible()` devuelve False y `error` dice por qué.
        Se puede llamar más de una vez; solo carga la primera.
        """
        with self._cargando:
            if self._modelo is not None:
                return
            try:
                modelo = self._cargar()
                # La primera inferencia de onnxruntime tarda el doble (RTF 0,20
                # frente a 0,10 en la siguiente, medido en H1): se paga aquí, en
                # el arranque, con un segundo de silencio, y no en el primer dictado.
                modelo.recognize(np.zeros(16_000, dtype=np.float32), sample_rate=16_000)
                self._modelo = modelo
                self._error = None
                log.info("modelo local %s (%s) cargado", self._modelo_id, self._cuantizacion)
            except Exception as e:  # noqa: BLE001 — no debe lanzar: el selector mira disponible()
                self._error = f"No se pudo cargar el modelo local: {e}"
                log.exception("fallo al cargar el modelo local")

    def _cargar(self) -> Any:
        carpeta = self.carpeta_modelo
        self._carpeta_modelos.mkdir(parents=True, exist_ok=True)
        if carpeta.exists() and not self._completa(carpeta):
            log.warning("descarga incompleta en %s: se borra y se repite", carpeta)
            shutil.rmtree(carpeta)
        try:
            return self._cargar_de(carpeta)
        except FileNotFoundError:
            # La carpeta existía y onnx-asr entró en modo offline sin encontrar
            # los archivos: quedó a medias de una forma que no detectamos. Se
            # borra y se descarga de nuevo, una sola vez.
            if not carpeta.exists():
                raise
            log.warning("faltan archivos del modelo en %s: se borra y se descarga", carpeta)
            shutil.rmtree(carpeta)
            return self._cargar_de(carpeta)

    def _cargar_de(self, carpeta: Path) -> Any:
        import onnx_asr

        descargando = not carpeta.exists()
        observador = _Observador(self, carpeta) if descargando else None
        if observador is not None:
            observador.start()
        try:
            modelo = onnx_asr.load_model(
                self._modelo_id,
                path=carpeta,
                quantization=self._cuantizacion if self._cuantizacion != "fp32" else None,
                sess_options=self._opciones_sesion(),
            )
        finally:
            if observador is not None:
                observador.parar()
        if descargando:
            self._avisar("Modelo local descargado", 1.0)
        return modelo

    def _opciones_sesion(self) -> Any:
        if self._hilos <= 0:
            return None
        import onnxruntime as ort

        opciones = ort.SessionOptions()
        opciones.intra_op_num_threads = self._hilos
        return opciones

    @staticmethod
    def _completa(carpeta: Path) -> bool:
        """False si quedan restos de una descarga interrumpida o no hay ningún .onnx."""
        if any(carpeta.rglob("*.incomplete")):
            return False
        return any(carpeta.glob("*.onnx"))

    def _avisar(self, mensaje: str, fraccion: float | None) -> None:
        if self._al_progresar is not None:
            try:
                self._al_progresar(mensaje, fraccion)
            except Exception:  # noqa: BLE001 — el progreso es informativo
                log.exception("el callback de progreso falló")

    # --- uso -----------------------------------------------------------------

    def disponible(self) -> bool:
        """True cuando el modelo está cargado. No depende de la red."""
        return self._modelo is not None

    def transcribir(self, audio: Audio, idioma: str) -> Transcripcion:
        """Transcribe con Parakeet.

        `recognize()` acepta el `ndarray` directamente (verificado en H0), así
        que no hay archivo temporal. `ms_proceso` mide solo esa llamada, para
        que el RTF sea comparable con el de `tools/banco_h0.py`.

        `idioma` no se le pasa al modelo: Parakeet TDT v3 es multilingüe sin
        indicador de idioma. Se conserva en la transcripción para el resto
        del pipeline.

        Raises:
            MotorNoDisponible: el modelo no está cargado (aún, o por un fallo).
            TranscripcionFallida: audio vacío o el modelo no devolvió texto.
        """
        if self._modelo is None:
            raise MotorNoDisponible(self._error or "el modelo local todavía no está cargado")
        if audio.duracion_s < DURACION_MINIMA_S:
            raise TranscripcionFallida("el audio es demasiado corto")
        muestras = np.ascontiguousarray(audio.muestras, dtype=np.float32)
        t0 = time.perf_counter()
        try:
            resultado = self._modelo.recognize(muestras, sample_rate=audio.sr)
        except Exception as e:
            raise TranscripcionFallida(f"el modelo local falló: {e}") from e
        ms = int((time.perf_counter() - t0) * 1000)
        texto = str(resultado).strip()
        if not texto:
            raise TranscripcionFallida("no se oyó nada")
        return Transcripcion(
            texto=texto,
            idioma=idioma,
            motor=self.nombre,
            ms_proceso=ms,
            duracion_audio_s=audio.duracion_s,
        )


class _Observador(threading.Thread):
    """Mira crecer la carpeta mientras onnx-asr descarga, y lo cuenta.

    onnx-asr no deja meter una barra de progreso en su descarga, así que se
    mide el tamaño de la carpeta cada segundo. El total sale de Hugging Face
    si responde; si no, del tamaño aproximado conocido.
    """

    def __init__(self, motor: MotorLocal, carpeta: Path) -> None:
        super().__init__(name="voziris-descarga", daemon=True)
        self._motor = motor
        self._carpeta = carpeta
        self._parar = threading.Event()

    def parar(self) -> None:
        self._parar.set()
        self.join(timeout=2)

    def run(self) -> None:
        self._motor._avisar("Descargando el modelo local…", 0.0)
        total = self._total_bytes()
        while not self._parar.wait(1.0):
            bajado = self._bytes_en_carpeta()
            if total:
                fraccion = min(bajado / total, 0.99)
                self._motor._avisar(
                    f"Descargando el modelo local: {bajado // 2**20} de {total // 2**20} MB",
                    fraccion,
                )
            else:
                self._motor._avisar(f"Descargando el modelo local: {bajado // 2**20} MB", None)

    def _bytes_en_carpeta(self) -> int:
        try:
            return sum(p.stat().st_size for p in self._carpeta.rglob("*") if p.is_file())
        except OSError:
            return 0

    def _total_bytes(self) -> int:
        aproximado = TAMANO_APROX_MB.get(self._motor._cuantizacion, 0) * 2**20
        try:
            from huggingface_hub import HfApi
            from onnx_asr.resolver import model_repos

            repo = model_repos.get(self._motor._modelo_id)
            if repo is None:
                return aproximado
            info = HfApi().model_info(repo, files_metadata=True)
            cuant = self._motor._cuantizacion
            total = 0
            for archivo in info.siblings or []:
                nombre = archivo.rfilename
                es_onnx = ".onnx" in nombre
                de_esta = (f".{cuant}." in nombre) if cuant != "fp32" else not any(
                    f".{q}." in nombre for q in ("int8", "fp16", "int4")
                )
                if not es_onnx or de_esta:
                    total += archivo.size or 0
            return total or aproximado
        except Exception:  # noqa: BLE001 — sin red o sin API: se usa el aproximado
            return aproximado
