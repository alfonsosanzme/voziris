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

from pathlib import Path

from voziris.tipos import Contexto, Entrega


class ArchivoMarkdown:
    nombre = "markdown"

    def __init__(
        self,
        ruta: Path,
        formato: str = "- {sello} · {texto}",
        sello: str = "%Y-%m-%d %H:%M",
        separador: str = "",
    ) -> None:
        self._ruta = ruta
        self._formato = formato
        self._sello = sello
        self._separador = separador

    def entregar(self, texto: str, ctx: Contexto) -> Entrega:
        """Añade una entrada al final del archivo.

        `formato` admite los marcadores `{sello}` y `{texto}`. Un formato con un
        marcador desconocido no debe reventar: se ignora y se avisa.

        El texto se aplana a una sola línea (los saltos internos pasan a
        espacios) salvo que `formato` contenga un salto explícito. Si no, una
        entrada multilínea rompe la lista de Markdown.
        """
        raise NotImplementedError("VOZ-50")
