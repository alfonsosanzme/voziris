"""D1, D2, D4 — escribir en la aplicación que tiene el foco.

Dos métodos, configurables:

  portapapeles  (por defecto)  copia el texto y manda Ctrl+V con SendInput.
                Rápido y funciona en casi todo.
  tecleo                       envía el texto carácter a carácter con SendInput.
                Lento en textos largos, pero funciona donde el pegado no.

**D2 no es un extra.** Si dictar borra lo que el usuario tenía copiado, deja de
usar la aplicación en una semana. El portapapeles se guarda antes de escribir y
se restaura después, con un retardo breve para que la aplicación de destino haya
terminado de leerlo.

Dos limitaciones de Windows que hay que documentar, no arreglar:

  - Si la ventana en primer plano corre elevada y Voziris no, Windows no
    entrega la pulsación sintética (UIPI). No hay forma de sortearlo sin
    elevar Voziris, y eso choca con «sin permisos de administrador» (F1).
  - Algunas terminales y aplicaciones con protección de entrada rechazan el
    pegado sintético. Para esas, `metodo = "tecleo"`.

Issue: VOZ-12 (inserción y portapapeles), VOZ-51 (auto-Enter).
"""

from __future__ import annotations

from voziris.tipos import Contexto, Entrega


class AppActiva:
    nombre = "app_activa"

    def __init__(
        self,
        metodo: str = "portapapeles",
        restaurar_portapapeles: bool = True,
        auto_enter: bool = False,
    ) -> None:
        self._metodo = metodo
        self._restaurar = restaurar_portapapeles
        self._auto_enter = auto_enter

    def entregar(self, texto: str, ctx: Contexto) -> Entrega:
        """Escribe el texto donde esté el cursor.

        Secuencia con `metodo = "portapapeles"`:

            1. Leer y guardar el portapapeles actual (solo formato texto).
            2. Poner el texto del dictado.
            3. Mandar Ctrl+V.
            4. Esperar ~120 ms.
            5. Restaurar lo guardado, si `restaurar_portapapeles`.

        El paso 4 es un compromiso: sin espera, algunas aplicaciones pegan el
        contenido restaurado; con más espera, se nota. Hay que medir el mínimo
        que funcione y dejarlo constante y comentado.

        D4: si `auto_enter`, mandar Enter después del paso 3. Nunca activado
        por defecto — un Enter no deseado en el chat equivocado es un accidente
        que no se puede deshacer.
        """
        raise NotImplementedError("VOZ-12")

    @staticmethod
    def app_en_primer_plano() -> str | None:
        """Nombre del ejecutable en primer plano, p. ej. "chrome.exe".

        Se usa para el `detalle` de la entrega y el historial. `GetForegroundWindow`
        más `GetWindowThreadProcessId`. Devuelve None si no se puede averiguar:
        no es un error, solo se pierde una etiqueta informativa.
        """
        raise NotImplementedError("VOZ-12")
