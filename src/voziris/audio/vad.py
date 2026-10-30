"""A2 — corte por silencio en modo «clavar», y si hay voz que transcribir (VOZ-84).

En modo «mantener» el usuario decide cuándo acaba soltando la tecla; aquí no
hace falta VAD para cortar. En modo «clavar» hay que detectar que ha terminado
de hablar.

Además, al cerrar cualquier dictado se mira si en él habló alguien
(`hay_voz`). Si nadie habló, no se manda a ningún motor: Whisper, el de la
API, escribe «Gracias por ver el vídeo» con cualquier audio sin voz. Medido en
VOZ-84 con el silencio del micrófono del cliente, con ceros, sin normalizar,
con poca ganancia y sin el vocabulario: no hay petición que lo evite. Con el
audio ya normalizado, el silencio del cliente no pasa de 0,05, y ni ceros ni
ruidos de sala, zumbidos, teclas o ventiladores llegan a 0,35. Los 21 dictados
reales con voz que se probaron pasan todos, también bajados 40 dB.

Silero VAD en ONNX, con el mismo onnxruntime que ya arrastra el motor local:
sin dependencias nuevas. El modelo (2,3 MB) se descarga a `carpeta` la
primera vez, igual que Parakeet.

Se ejecuta bloque a bloque, en streaming: onnx-asr solo ofrece el VAD para
segmentar audio completo, así que aquí se llama al modelo directamente. Lo
que espera Silero v5 a 16 kHz (leído en `onnx_asr/models/silero.py`):
entradas `input[1, 64 + 512]` (los últimos 64 muestras del bloque anterior
como contexto, más el bloque de 512), `state[2, 1, 128]` y `sr`; salidas
`output[1, 1]` (probabilidad de voz) y `stateN`. Los 512 son exactamente el
`BLOQUE` de `Captura`.

Histéresis: voz por encima de 0,5; silencio por debajo de 0,35 (los umbrales
de onnx-asr). Entre medias no cuenta ni como voz ni como silencio. El corte
solo llega tras `silencio_corte_ms` de silencio CONTINUO: una pausa de 700 ms
para pensar no corta la frase. Cortar de más es peor que tardar un poco,
porque parte el dictado en dos.

Issue: VOZ-20, VOZ-84.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

import numpy as np

from voziris.tipos import SAMPLE_RATE

log = logging.getLogger(__name__)

BLOQUE = 512
CONTEXTO = 64
UMBRAL_VOZ = 0.5
UMBRAL_SILENCIO = 0.35
REPO = "istupakov/silero-vad-onnx"
ARCHIVO = "silero_vad.onnx"
MS_POR_BLOQUE = BLOQUE * 1000 / SAMPLE_RATE  # 32 ms


class DetectorSilencio:
    """Decide, bloque a bloque, si el dictado clavado debe cerrarse."""

    def __init__(self, silencio_corte_ms: int = 2000, carpeta: Path | None = None) -> None:
        self._corte_ms = silencio_corte_ms
        self._carpeta = Path(carpeta) if carpeta else None
        self._sesion: Any = None
        self._cargando = threading.Lock()
        self._avisado = False
        self._estado = np.zeros((2, 1, 128), dtype=np.float32)
        self._contexto = np.zeros(CONTEXTO, dtype=np.float32)
        self._silencio_ms = 0.0
        self.hubo_voz = False
        self.ultima_probabilidad = 0.0

    @property
    def silencio_corte_ms(self) -> int:
        return self._corte_ms

    @silencio_corte_ms.setter
    def silencio_corte_ms(self, valor: int) -> None:
        """Desde los ajustes, en caliente."""
        self._corte_ms = valor

    # --- modelo ------------------------------------------------------------------

    def precalentar(self) -> None:
        """Descarga (si falta) y carga el modelo. No lanza: sin modelo, nunca corta."""
        with self._cargando:
            if self._sesion is not None:
                return
            try:
                import onnxruntime as ort

                ruta = self._ruta_modelo()
                opciones = ort.SessionOptions()
                opciones.intra_op_num_threads = 1  # 512 muestras: un hilo sobra
                opciones.log_severity_level = 3
                self._sesion = ort.InferenceSession(str(ruta), opciones)
                log.info("Silero VAD cargado desde %s", ruta)
            except Exception:  # noqa: BLE001 — sin VAD, «clavar» se cierra a mano
                log.exception("no se pudo cargar Silero VAD")

    def _ruta_modelo(self) -> Path:
        carpeta = (self._carpeta or Path("modelos")) / "silero-vad"
        ruta = carpeta / ARCHIVO
        if not ruta.exists():
            from huggingface_hub import snapshot_download

            log.info("descargando Silero VAD a %s", carpeta)
            snapshot_download(REPO, local_dir=carpeta, allow_patterns=[ARCHIVO, "config.json"])
        return ruta

    @property
    def disponible(self) -> bool:
        return self._sesion is not None

    # --- streaming -------------------------------------------------------------------

    def reiniciar(self) -> None:
        """Se llama al empezar cada dictado: el detector guarda estado."""
        self._estado = np.zeros((2, 1, 128), dtype=np.float32)
        self._contexto = np.zeros(CONTEXTO, dtype=np.float32)
        self._silencio_ms = 0.0
        self.hubo_voz = False
        self.ultima_probabilidad = 0.0

    def alimentar(self, bloque: np.ndarray) -> bool:
        """Procesa un bloque y devuelve True cuando el dictado debe cerrarse.

        Admite bloques de cualquier tamaño; los trocea en 512. Sin modelo
        cargado devuelve siempre False.
        """
        if self._sesion is None:
            return False
        muestras = np.asarray(bloque, dtype=np.float32).reshape(-1)
        cerrar = False
        for inicio in range(0, len(muestras), BLOQUE):
            trozo = muestras[inicio : inicio + BLOQUE]
            if len(trozo) < BLOQUE:
                trozo = np.pad(trozo, (0, BLOQUE - len(trozo)))
            cerrar = self._procesar(trozo) or cerrar
        return cerrar

    def _procesar(self, trozo: np.ndarray) -> bool:
        entrada = np.concatenate((self._contexto, trozo))[np.newaxis, :]
        try:
            salida, estado = self._sesion.run(
                ["output", "stateN"],
                {"input": entrada, "state": self._estado, "sr": np.array(SAMPLE_RATE, np.int64)},
            )
        except Exception:  # noqa: BLE001 — el VAD no puede tumbar un dictado
            if not self._avisado:
                self._avisado = True
                log.exception("Silero VAD falló; el dictado clavado se cierra a mano")
            return False
        self._estado = estado
        self._contexto = trozo[-CONTEXTO:]
        probabilidad = float(salida[0, 0])
        self.ultima_probabilidad = probabilidad
        if probabilidad >= UMBRAL_VOZ:
            self.hubo_voz = True
            self._silencio_ms = 0.0
        elif probabilidad < UMBRAL_SILENCIO:
            self._silencio_ms += MS_POR_BLOQUE
        return self._silencio_ms >= self._corte_ms

    @property
    def silencio_ms(self) -> float:
        """Silencio continuo acumulado, para el HUD."""
        return self._silencio_ms

    # --- ¿habló alguien? (VOZ-84) --------------------------------------------------------

    def hay_voz(self, muestras: np.ndarray) -> bool | None:
        """True en cuanto un bloque llega a `UMBRAL_VOZ`; False si ninguno llega.

        Recorre el audio entero con un estado propio: no toca el del corte en
        streaming, así que se puede preguntar mientras otro dictado se graba
        clavado. Para en el primer bloque con voz, que en un dictado normal
        llega en el primer par de segundos: unos milisegundos, a 0,12 ms por
        cada bloque de 32 ms.

        None si no puede decidir, porque el modelo aún no ha cargado o ha
        fallado. Quien llama transcribe entonces como siempre: ante la duda, el
        dictado no se tira.
        """
        sesion = self._sesion
        if sesion is None:
            return None
        datos = np.asarray(muestras, dtype=np.float32).reshape(-1)
        estado = np.zeros((2, 1, 128), dtype=np.float32)
        contexto = np.zeros(CONTEXTO, dtype=np.float32)
        try:
            for inicio in range(0, len(datos), BLOQUE):
                trozo = datos[inicio : inicio + BLOQUE]
                if len(trozo) < BLOQUE:
                    trozo = np.pad(trozo, (0, BLOQUE - len(trozo)))
                entrada = np.concatenate((contexto, trozo))[np.newaxis, :]
                salida, estado = sesion.run(
                    ["output", "stateN"],
                    {"input": entrada, "state": estado, "sr": np.array(SAMPLE_RATE, np.int64)},
                )
                if float(salida[0, 0]) >= UMBRAL_VOZ:
                    return True
                contexto = trozo[-CONTEXTO:]
        except Exception:  # noqa: BLE001 — el VAD no puede tumbar un dictado
            log.exception("Silero VAD falló buscando voz; se transcribe sin mirar")
            return None
        return False
