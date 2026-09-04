"""B2 — motor por API: cliente compatible con el esquema de OpenAI.

Apunta a Groq por defecto (~0,04 $/hora de audio, unos 0,60 $/mes dictando
media hora diaria). Al ser compatible con OpenAI, cambiar de proveedor es
cambiar `base_url` y `modelo` en la configuración, no tocar este archivo.

La clave es SIEMPRE del usuario final. No se embebe ninguna clave en el
repositorio ni en el binario. Se lee de la configuración o, si está vacía, de
la variable de entorno GROQ_API_KEY.

Issue: VOZ-30.
"""

from __future__ import annotations

from voziris.tipos import Audio, Transcripcion


class MotorAPI:
    """Implementa `MotorSTT` contra `POST {base_url}/audio/transcriptions`."""

    requiere_red = True

    def __init__(self, base_url: str, modelo: str, clave: str, timeout_s: int = 15) -> None:
        self.nombre = f"api:{_proveedor_de(base_url)}"
        self._base_url = base_url.rstrip("/")
        self._modelo = modelo
        self._clave = clave
        self._timeout = timeout_s

    def precalentar(self) -> None:
        """Abre el cliente httpx y reutiliza la conexión.

        Levantar la conexión TLS por adelantado ahorra unos cientos de
        milisegundos en el primer dictado.
        """
        raise NotImplementedError("VOZ-30")

    def disponible(self) -> bool:
        """Hay clave y hay red.

        La comprobación de red debe ser barata y cacheada unos segundos: se
        consulta en cada dictado y no puede añadir latencia perceptible.
        """
        raise NotImplementedError("VOZ-30")

    def transcribir(self, audio: Audio, idioma: str) -> Transcripcion:
        """Sube el audio como WAV multipart y devuelve el texto.

        Manda `language=idioma` para no pagar la detección automática (B4).
        Ante timeout o error HTTP, lanza `MotorNoDisponible` para que el
        selector caiga al motor local, en vez de perder el dictado.
        """
        raise NotImplementedError("VOZ-30")


def _proveedor_de(base_url: str) -> str:
    """Nombre corto del proveedor a partir de la URL, para el historial."""
    raise NotImplementedError("VOZ-30")
