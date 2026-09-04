"""B1 — motor local sin GPU: Parakeet TDT 0.6B v3 en ONNX int8.

Por qué este modelo y no Whisper: RTF 0,033 frente a 0,13 en CPU, WER 6,34 %
frente a 7,44 %, y —lo decisivo— **emite puntuación y mayúsculas** en los 25
idiomas europeos que cubre, español incluido. Eso resuelve C1 sin red y sin
LLM. No hay alternativa razonable si el motor no puntuara: sherpa-onnx solo
publica modelos de puntuación para inglés y chino.

Licencia CC-BY-4.0: la atribución a NVIDIA es obligatoria. Ver ATRIBUCIONES.md.

Issue: VOZ-11.
"""

from __future__ import annotations

from pathlib import Path

from voziris.tipos import Audio, Transcripcion


class MotorLocal:
    """Implementa `MotorSTT` sobre `onnx-asr`.

    El modelo son ~680 MB que NO viajan dentro del ejecutable: se descargan a
    `carpeta` en el primer arranque, con progreso visible.
    """

    nombre = "local"
    requiere_red = False

    def __init__(self, modelo: str, carpeta: Path, hilos: int = 0) -> None:
        self._modelo_id = modelo
        self._carpeta = carpeta
        self._hilos = hilos
        self._modelo: object | None = None

    def precalentar(self) -> None:
        """Carga el modelo en memoria (~1,5 GB de RAM en uso).

        Implementación de referencia:

            import onnx_asr
            self._modelo = onnx_asr.load_model(self._modelo_id, path=self._carpeta)

        Si el modelo no está en `carpeta`, `onnx-asr` lo descarga de Hugging
        Face. Esa descarga debe mostrar progreso en la interfaz, no bloquear en
        silencio: son varios minutos la primera vez.

        Para fijar el número de hilos hay que pasar `SessionOptions` de
        onnxruntime; con `hilos = 0` se deja el valor por defecto.
        """
        raise NotImplementedError("VOZ-11")

    def disponible(self) -> bool:
        """True cuando el modelo está cargado. No depende de la red."""
        raise NotImplementedError("VOZ-11")

    def transcribir(self, audio: Audio, idioma: str) -> Transcripcion:
        """Transcribe con Parakeet.

        Comprobar durante la implementación si `recognize()` acepta un
        `np.ndarray` directamente. Si solo acepta rutas de archivo, escribir un
        WAV temporal en la carpeta temporal del sistema y borrarlo después
        — nunca en una carpeta del usuario.

        `ms_proceso` mide solo la inferencia, sin la escritura del temporal, para
        que el RTF sea comparable con el del banco de pruebas (`tools/banco_h0.py`).
        """
        raise NotImplementedError("VOZ-11")
