"""Punto de entrada: arranque, orquestación y bucle de bandeja.

Este archivo es el único sitio donde se conocen todas las piezas. Todo lo demás
depende solo de los tres protocolos.

Orden de arranque (importa):

    1. Cargar y validar la configuración. Si falla, mensaje claro y salir: es
       el único error fatal.
    2. Abrir la captura de audio y poner a girar el búfer previo.
    3. Precalentar los motores en un hilo aparte. La interfaz no se bloquea; si
       el usuario dicta antes de que termine, se espera con el HUD en
       «procesando».
    4. Registrar los atajos. Si uno está tomado, decir cuál.
    5. Mostrar el icono de bandeja y entrar en el bucle.

Issue: VOZ-04.
"""

from __future__ import annotations


def dictar(modo: str, destino: str) -> None:
    """Un dictado completo, de principio a fin.

    Secuencia:

        empezar_dictado()  →  sonido de inicio  →  HUD grabando
          (mantener: hasta soltar · clavar: hasta pulsar o VAD)
        terminar_dictado() →  sonido de fin     →  HUD procesando
        selector.transcribir()
        cadena de post-proceso, en orden fijo
        destino.entregar()
        historial.registrar()
        HUD oculto, con aviso si hubo alguno

    Corre en un hilo de trabajo, no en el del hook de teclado. Dos dictados
    simultáneos no existen: si llega uno nuevo mientras hay otro en curso, se
    ignora y suena el tono de error.
    """
    raise NotImplementedError("VOZ-04")


def main() -> int:
    """Arranca la aplicación. Devuelve el código de salida."""
    raise NotImplementedError("VOZ-04")


if __name__ == "__main__":
    raise SystemExit(main())
