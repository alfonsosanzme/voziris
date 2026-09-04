"""A3, A5 — captura con búfer previo.

El búfer previo es lo que separa una app usable de una inservible: el
micrófono graba SIEMPRE en un anillo de 500 ms, así que cuando el usuario
pulsa el atajo ya tenemos medio segundo de audio anterior a la pulsación. Sin
esto se pierde la primera sílaba de cada dictado y el usuario aprende a hacer
una pausa antes de hablar, que es justo la fricción que veníamos a quitar.

El anillo está siempre girando mientras la app vive. No se graba a disco.

Issue: VOZ-10.
"""

from __future__ import annotations

from voziris.tipos import Audio


class Captura:
    """Anillo continuo sobre `sounddevice.InputStream`.

    Vida del objeto: se abre al arrancar la aplicación y se cierra al salir.
    Abrir el flujo en cada dictado cuesta cientos de milisegundos y a veces
    falla si otra aplicación tiene el micrófono tomado.
    """

    def __init__(
        self,
        dispositivo: str = "",
        ganancia_db: float = 0.0,
        buffer_previo_ms: int = 500,
    ) -> None:
        self._dispositivo = dispositivo or None
        self._ganancia_db = ganancia_db
        self._buffer_previo_ms = buffer_previo_ms

    def abrir(self) -> None:
        """Abre el flujo de entrada a 16 kHz mono float32 y empieza a girar.

        El callback de sounddevice corre en un hilo de audio con plazos
        estrictos: solo copia al anillo. Ninguna otra cosa — nada de logging,
        nada de inferencia, nada de bloqueos.
        """
        raise NotImplementedError("VOZ-10")

    def cerrar(self) -> None:
        raise NotImplementedError("VOZ-10")

    def dispositivos(self) -> list[tuple[int, str]]:
        """A5 — micrófonos disponibles, como (índice, nombre), para los ajustes."""
        raise NotImplementedError("VOZ-10")

    def empezar_dictado(self) -> None:
        """Marca el inicio: a partir de aquí se acumula, con el anillo por delante."""
        raise NotImplementedError("VOZ-10")

    def terminar_dictado(self) -> Audio:
        """Cierra la acumulación y devuelve búfer previo + dictado, con la ganancia aplicada.

        Debe recortar por arriba en lugar de dejar que la ganancia sature: un
        clip saturado empeora la transcripción más que un audio bajo.
        """
        raise NotImplementedError("VOZ-10")

    def nivel_actual(self) -> float:
        """A4 — nivel RMS en 0..1 para el indicador. Se consulta ~30 veces por segundo."""
        raise NotImplementedError("VOZ-10")
