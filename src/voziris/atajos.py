"""A1, A2, H2 — atajos globales.

**H2 es explícito: un atajo por comportamiento, no una doble pulsación.**
Detectar dobles pulsaciones obliga a esperar la ventana de tiempo antes de
empezar a grabar, y eso se come el búfer previo. Tres combinaciones separadas:

    mantener  → graba mientras siga pulsada           (A1)
    clavar    → fija el micrófono hasta volver a pulsar (A2)
    markdown  → como «mantener», pero al archivo       (H1)
    cancelar  → descarta el dictado en curso

Implementación: `RegisterHotKey` de Win32 sirve para `clavar`, `markdown` y
`cancelar`, que son eventos de pulsación. Para `mantener` hace falta saber
cuándo se SUELTA la tecla, y `RegisterHotKey` no informa de eso: se necesita un
hook de bajo nivel (`SetWindowsHookEx` con `WH_KEYBOARD_LL`) en un hilo con
bombeo de mensajes propio.

El hook de bajo nivel es también la razón del riesgo R2 (falsos positivos de
antivirus) y de R3 (no funciona sobre ventanas elevadas). Ambos van
documentados en el README, no se arreglan.

Issue: VOZ-02.
"""

from __future__ import annotations

from collections.abc import Callable

from voziris.tipos import Modo


class Atajos:
    """Registra las combinaciones y llama de vuelta.

    Los callbacks se ejecutan en el hilo del hook y deben devolver el control
    de inmediato: encolar el trabajo, nunca hacerlo aquí. Un callback lento
    bloquea el teclado de todo el sistema.
    """

    def __init__(
        self,
        al_empezar: Callable[[Modo, str], None],   # (modo, destino)
        al_terminar: Callable[[], None],
        al_cancelar: Callable[[], None],
    ) -> None:
        self._al_empezar = al_empezar
        self._al_terminar = al_terminar
        self._al_cancelar = al_cancelar

    def registrar(self, combinaciones: dict[str, str]) -> None:
        """Registra las cuatro combinaciones del `config.toml`.

        Args:
            combinaciones: {"mantener": "ctrl+win", "clavar": "ctrl+shift+space", ...}

        Raises:
            ConfigInvalida: una combinación ya la tiene tomada otra aplicación.
                Hay que decir CUÁL falló: «Ctrl+Win ya está en uso» es
                accionable, «error al registrar atajos» no.
        """
        raise NotImplementedError("VOZ-02")

    def liberar(self) -> None:
        """Suelta el hook y las combinaciones. Obligatorio al salir."""
        raise NotImplementedError("VOZ-02")
