"""A4 — indicador flotante con nivel de audio.

Confirma que te está oyendo. Sin esto, el usuario habla sin saber si se está
grabando y descubre el fallo cuando no aparece el texto.

Requisitos de comportamiento:
  - Ventana sin bordes, siempre encima, sin foco. **No debe robar el foco
    nunca**: si lo hace, el texto se pega en el propio indicador.
  - Estilos `WS_EX_NOACTIVATE | WS_EX_TOPMOST | WS_EX_TOOLWINDOW`, y quedarse
    fuera de Alt+Tab.
  - Pequeña y cerca del cursor, o anclada donde diga la configuración.
  - Barra de nivel a ~30 fps mientras graba; «procesando» al soltar.
  - Al terminar, desaparece. Si hubo aviso, se queda ~1,5 s mostrándolo.

Issue: VOZ-22.
"""

from __future__ import annotations


class Hud:
    def mostrar_grabando(self) -> None:
        raise NotImplementedError("VOZ-22")

    def actualizar_nivel(self, nivel: float) -> None:
        """`nivel` en 0..1, desde `Captura.nivel_actual()`."""
        raise NotImplementedError("VOZ-22")

    def mostrar_procesando(self) -> None:
        raise NotImplementedError("VOZ-22")

    def ocultar(self, aviso: str | None = None) -> None:
        raise NotImplementedError("VOZ-22")
