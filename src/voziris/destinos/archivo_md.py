"""H1 — anexar el dictado a un archivo Markdown.

Requisito propio, no de Wispr Flow: capturar notas rápidas en `entrada.md` del
vault de Obsidian sin abrirlo, para procesarlas después con el flujo habitual.

Con el formato por defecto, tres dictados dejan esto:

    - 2026-09-03 19:42 · Llamar a la gestoría por lo del modelo 111.
    - 2026-09-03 19:44 · Mirar si Parakeet aguanta los nombres propios del vault.
    - 2026-09-03 20:01 · Idea para Kairis: una sesión sobre dictado por voz.

Reglas de escritura, todas por el mismo motivo — Obsidian tiene el archivo
abierto al mismo tiempo:

  - Abrir en modo *append*, escribir, cerrar. En cada dictado. No mantener el
    descriptor abierto entre dictados.
  - UTF-8 sin BOM y saltos de línea `\\n`.
  - Asegurar que el archivo termina en salto de línea antes de añadir, para no
    pegar la entrada nueva al final de la anterior.
  - Si el archivo está bloqueado, reintentar una vez tras 200 ms. Si vuelve a
    fallar, `EntregaFallida`: el texto sigue en el historial.
  - Si el archivo no existe, crearlo. Si la carpeta no existe, NO crearla:
    señal de que la ruta está mal configurada, y crear árboles de carpetas en
    el vault de alguien por un error de configuración es peor que fallar.

Issue: VOZ-50.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path

from voziris.errores import EntregaFallida
from voziris.tipos import Contexto, Entrega

log = logging.getLogger(__name__)

ESPERA_REINTENTO_S = 0.2


class _Marcadores(dict[str, str]):
    """Un marcador desconocido en `formato` se deja tal cual, no revienta."""

    def __missing__(self, clave: str) -> str:
        log.warning("marcador desconocido en destino.markdown.formato: {%s}", clave)
        return "{" + clave + "}"


class ArchivoMarkdown:
    nombre = "markdown"

    def __init__(
        self,
        ruta: Path,
        formato: str = "- {sello} · {texto}",
        sello: str = "%Y-%m-%d %H:%M",
        separador: str = "",
    ) -> None:
        self._ruta = Path(ruta)
        self._formato = formato
        self._sello = sello
        self._separador = separador

    @property
    def ruta(self) -> Path:
        return self._ruta

    def entregar(self, texto: str, ctx: Contexto) -> Entrega:
        """Añade una entrada al final del archivo.

        `formato` admite los marcadores `{sello}` y `{texto}`. Un formato con un
        marcador desconocido no revienta: se deja literal y se avisa en el log.

        El texto se aplana a una sola línea (los saltos internos pasan a
        espacios) salvo que `formato` contenga un salto explícito. Si no, una
        entrada multilínea rompe la lista de Markdown.
        """
        if not self._ruta.parent.is_dir():
            raise EntregaFallida(
                f"La carpeta {self._ruta.parent} no existe: revisa destino.markdown.ruta"
            )
        texto = texto.strip() if "\n" in self._formato else " ".join(texto.split())
        sello = datetime.now().strftime(self._sello)
        linea = self._formato.format_map(_Marcadores(sello=sello, texto=texto))
        bloque = (self._separador + "\n" if self._separador else "") + linea + "\n"

        try:
            self._anadir(bloque)
        except OSError as primero:
            log.info("archivo ocupado (%s); reintento en %g s", primero, ESPERA_REINTENTO_S)
            time.sleep(ESPERA_REINTENTO_S)
            try:
                self._anadir(bloque)
            except OSError as segundo:
                raise EntregaFallida(
                    f"No se pudo escribir en {self._ruta.name}: {segundo}"
                ) from segundo
        return Entrega(ok=True, detalle=f"añadido a {self._ruta.name}")

    def _anadir(self, bloque: str) -> None:
        """Abre, garantiza el salto final, escribe y cierra. Todo en una apertura."""
        existe = self._ruta.exists()
        with open(self._ruta, "ab") as archivo:
            if existe and archivo.tell() > 0:
                with open(self._ruta, "rb") as lectura:
                    lectura.seek(-1, 2)
                    ultimo = lectura.read(1)
                if ultimo not in (b"\n", b""):
                    archivo.write(b"\n")
            archivo.write(bloque.encode("utf-8"))
