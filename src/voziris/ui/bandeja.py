"""F2 — icono de bandeja y arranque con Windows.

La aplicación no tiene ventana principal. El icono de bandeja es toda su
presencia: estado, menú y salida.

Estados del icono, distinguibles de un vistazo y también en escala de grises
(hay quien no distingue rojo de verde):

    reposo        contorno
    grabando      relleno
    procesando    relleno con marca
    error         contorno con marca

Arranque con Windows: acceso directo en la carpeta Startup del usuario. NO una
clave del registro en HKLM ni una tarea programada — eso pide permisos de
administrador y rompe F1.

Issue: VOZ-03.
"""

from __future__ import annotations

from collections.abc import Callable


class Bandeja:
    def __init__(self, al_salir: Callable[[], None]) -> None:
        self._al_salir = al_salir

    def mostrar(self) -> None:
        """Crea el icono y entra en el bucle de eventos.

        Menú: Dictar ahora · Últimos dictados (submenú, VOZ-52) · Motor
        (local/API/auto) · Ajustes · Acerca de · Salir.

        «Acerca de» debe incluir la atribución a NVIDIA por CC-BY-4.0. Es una
        obligación de licencia, no un adorno.
        """
        raise NotImplementedError("VOZ-03")

    def estado(self, nombre: str) -> None:
        """Cambia el icono: "reposo" | "grabando" | "procesando" | "error"."""
        raise NotImplementedError("VOZ-03")

    def avisar(self, texto: str) -> None:
        """Notificación del sistema. Solo para lo que el usuario debe saber.

        Un aviso por cada dictado sería insufrible: esto es para «no hay red,
        he transcrito en local» o «no pude escribir en entrada.md».
        """
        raise NotImplementedError("VOZ-03")

    @staticmethod
    def configurar_arranque(activo: bool) -> None:
        """Crea o borra el acceso directo en la carpeta Startup del usuario."""
        raise NotImplementedError("VOZ-03")
