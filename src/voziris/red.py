"""¿Hay red? La única comprobación de conectividad del proyecto.

La consulta el selector en cada dictado (para decidir API o local) y el
post-proceso con LLM, así que tiene que ser barata: una conexión TCP al host
de la API, con timeout corto, y el resultado cacheado unos segundos. Sin
peticiones HTTP y sin DNS mientras la caché esté caliente.

Con `motor = "local"` y `nivel = "literal"` nadie la llama: en ese modo no
sale ni un paquete del equipo (VOZ-32).
"""

from __future__ import annotations

import socket
import threading
import time
from urllib.parse import urlparse

TTL_S = 10.0
TIMEOUT_S = 1.5

_cache: dict[tuple[str, int], tuple[float, bool]] = {}
_lock = threading.Lock()


def host_de(base_url: str) -> str:
    """«https://api.groq.com/openai/v1» → «api.groq.com»."""
    return urlparse(base_url).hostname or base_url


def puerto_de(base_url: str) -> int:
    partes = urlparse(base_url)
    if partes.port:
        return partes.port
    return 80 if partes.scheme == "http" else 443


def hay_red(host: str = "api.groq.com", puerto: int = 443, ttl_s: float = TTL_S) -> bool:
    """True si se puede abrir una conexión TCP con `host:puerto`.

    El resultado se recuerda `ttl_s` segundos: diez dictados seguidos no son
    diez comprobaciones. Un fallo también se recuerda, para que sin red cada
    dictado no espere el timeout.
    """
    clave = (host, puerto)
    ahora = time.monotonic()
    with _lock:
        recordado = _cache.get(clave)
        if recordado is not None and ahora - recordado[0] < ttl_s:
            return recordado[1]
    resultado = _sondear(host, puerto)
    with _lock:
        _cache[clave] = (time.monotonic(), resultado)
    return resultado


def _sondear(host: str, puerto: int) -> bool:
    try:
        with socket.create_connection((host, puerto), timeout=TIMEOUT_S):
            return True
    except OSError:
        return False


def olvidar() -> None:
    """Vacía la caché. Para los tests y para «probar» desde los ajustes."""
    with _lock:
        _cache.clear()
