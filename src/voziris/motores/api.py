"""B2 — motor por API: cliente compatible con el esquema de OpenAI.

Apunta a Groq por defecto (~0,04 $/hora de audio, unos 0,60 $/mes dictando
media hora diaria). Al ser compatible con OpenAI, cambiar de proveedor es
cambiar `base_url` y `modelo` en la configuración, no tocar este archivo.

La clave es SIEMPRE del usuario final. No se embebe ninguna clave en el
repositorio ni en el binario. Llega ya resuelta desde `config` (TOML o
variable de entorno GROQ_API_KEY) y no sale de aquí: ni en logs, ni en
mensajes de error, ni truncada.

Issue: VOZ-30.
"""

from __future__ import annotations

import io
import logging
import time
import wave
from typing import Any

import numpy as np

from voziris import red
from voziris.errores import MotorNoDisponible, TranscripcionFallida
from voziris.tipos import Audio, Transcripcion

log = logging.getLogger(__name__)

TIMEOUT_PRUEBA_S = 5.0


def _proveedor_de(base_url: str) -> str:
    """Nombre corto del proveedor a partir de la URL, para el historial.

    «https://api.groq.com/openai/v1» → «groq»; «https://api.openai.com/v1» →
    «openai»; «http://localhost:8000/v1» → «localhost».
    """
    host = red.host_de(base_url).lower()
    partes = host.split(".")
    if len(partes) >= 2:
        return partes[-2]
    return host or "api"


def a_wav(audio: Audio) -> bytes:
    """PCM de 16 bits mono en memoria. Un dictado de 60 s son 1,9 MB (límite 25 MB)."""
    muestras = np.clip(audio.muestras, -1.0, 1.0)
    enteros = (muestras * 32767).astype("<i2")
    salida = io.BytesIO()
    with wave.open(salida, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(audio.sr)
        w.writeframes(enteros.tobytes())
    return salida.getvalue()


class MotorAPI:
    """Implementa `MotorSTT` contra `POST {base_url}/audio/transcriptions`."""

    requiere_red = True

    def __init__(
        self,
        base_url: str,
        modelo: str,
        clave: str,
        timeout_s: int = 15,
        transporte: Any = None,
    ) -> None:
        self.nombre = f"api:{_proveedor_de(base_url)}"
        self._base_url = base_url.rstrip("/")
        self._modelo = modelo
        self._clave = clave
        self._timeout = timeout_s
        self._transporte = transporte  # httpx.MockTransport en los tests
        self._cliente: Any = None
        self._host = red.host_de(self._base_url)
        self._puerto = red.puerto_de(self._base_url)

    # --- vida ------------------------------------------------------------------

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

    def precalentar(self) -> None:
        """Abre el cliente httpx y levanta la conexión TLS por adelantado.

        Ahorra unos cientos de milisegundos en el primer dictado. Si no hay
        clave o no hay red, no hace nada y no lanza: `disponible()` lo dirá.
        """
        if not self._clave or not red.hay_red(self._host, self._puerto):
            return
        try:
            self._abrir().get("/models", timeout=TIMEOUT_PRUEBA_S)
        except Exception as e:  # noqa: BLE001 — solo era un calentamiento
            log.info("no se pudo precalentar la API: %s", _limpiar(str(e), self._clave))

    def cerrar(self) -> None:
        cliente, self._cliente = self._cliente, None
        if cliente is not None:
            cliente.close()

    def disponible(self) -> bool:
        """Hay clave y hay red. La comprobación de red va cacheada (`red.hay_red`)."""
        return bool(self._clave) and red.hay_red(self._host, self._puerto)

    def motivo_no_disponible(self) -> str:
        """Para el aviso del selector: qué falta, sin adivinar."""
        if not self._clave:
            return "Sin clave de API"
        return "Sin conexión con la API"

    # --- uso ---------------------------------------------------------------------

    def transcribir(self, audio: Audio, idioma: str) -> Transcripcion:
        """Sube el audio como WAV multipart y devuelve el texto.

        Manda `language=idioma` para no pagar la detección automática (B4).
        Timeout, error HTTP y respuesta vacía se convierten en
        `MotorNoDisponible`, para que el selector caiga al motor local en vez
        de perder el dictado. Nunca escapa una excepción de httpx.
        """
        if not self._clave:
            raise MotorNoDisponible("no hay clave de API: ponla en config.toml o en GROQ_API_KEY")
        if audio.duracion_s <= 0:
            raise TranscripcionFallida("el audio está vacío")
        import httpx

        cliente = self._abrir()
        t0 = time.perf_counter()
        try:
            respuesta = cliente.post(
                "/audio/transcriptions",
                files={"file": ("dictado.wav", a_wav(audio), "audio/wav")},
                data={
                    "model": self._modelo,
                    "language": idioma,
                    "response_format": "json",
                    "temperature": "0",
                },
            )
        except httpx.TimeoutException as e:
            raise MotorNoDisponible(f"la API no respondió en {self._timeout} s") from e
        except httpx.HTTPError as e:
            detalle = _limpiar(str(e), self._clave)
            raise MotorNoDisponible(f"sin conexión con la API: {detalle}") from e
        ms = int((time.perf_counter() - t0) * 1000)

        if respuesta.status_code != 200:
            raise MotorNoDisponible(
                f"la API respondió {respuesta.status_code}: "
                f"{_limpiar(_resumen_error(respuesta), self._clave)}"
            )
        try:
            texto = str(respuesta.json().get("text", ""))
        except ValueError as e:
            raise MotorNoDisponible("la API devolvió algo que no es JSON") from e
        texto = texto.strip()
        if not texto:
            raise TranscripcionFallida("no se oyó nada")
        return Transcripcion(
            texto=texto,
            idioma=idioma,
            motor=self.nombre,
            ms_proceso=ms,
            duracion_audio_s=audio.duracion_s,
        )

    def probar_clave(self) -> tuple[bool, str]:
        """Para el botón «probar» de los ajustes: dice si la clave vale, sin revelarla."""
        if not self._clave:
            return False, "No hay clave"
        if not red.hay_red(self._host, self._puerto):
            return False, "Sin red"
        import httpx

        try:
            respuesta = self._abrir().get("/models", timeout=TIMEOUT_PRUEBA_S)
        except httpx.HTTPError as e:
            return False, f"Sin conexión: {_limpiar(str(e), self._clave)}"
        if respuesta.status_code == 200:
            return True, "La clave es válida"
        if respuesta.status_code in (401, 403):
            return False, "La clave no es válida"
        return False, f"La API respondió {respuesta.status_code}"


def _resumen_error(respuesta: Any) -> str:
    try:
        cuerpo = respuesta.json()
        mensaje = cuerpo.get("error", {}).get("message") if isinstance(cuerpo, dict) else None
        if mensaje:
            return str(mensaje)[:200]
    except ValueError:
        pass
    return str(respuesta.text)[:200]


def _limpiar(texto: str, clave: str) -> str:
    """Por si un mensaje de httpx o de la API repitiera la clave."""
    if clave and len(clave) >= 8:
        texto = texto.replace(clave, "***")
    return texto
