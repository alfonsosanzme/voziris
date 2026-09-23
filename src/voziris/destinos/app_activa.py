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

Y una de la v1: solo se conserva el portapapeles si contenía texto. Una
imagen o unos archivos copiados se pierden al dictar.

Issue: VOZ-12 (inserción y portapapeles), VOZ-51 (auto-Enter).
"""

from __future__ import annotations

import logging
import time

from voziris import winapi
from voziris.errores import EntregaFallida
from voziris.tipos import Contexto, Entrega

log = logging.getLogger(__name__)

RETARDO_RESTAURACION_MS = 150
"""Espera entre mandar Ctrl+V y devolver el portapapeles a lo que tenía.

Demasiado corto y la aplicación de destino pega el contenido restaurado;
demasiado largo y se nota. Medido el 4 sep 2026 con `tests/test_app_activa.py`
(`test_medir_retardo_minimo`, marcador `win`) contra una ventana Tk en este
portátil: con 0 ms el pegado llega tarde y entra lo restaurado; con 5 y 10 ms
funciona; a 20 ms falló una vez (el planificador de Windows no es puntual);
desde 40 ms es estable. Las aplicaciones pesadas (Word, Teams, navegadores
con muchas pestañas) leen el portapapeles más tarde que Tk: 150 ms les da
margen de sobra y sigue por debajo de lo que se percibe como espera.
"""

ESPERA_TRAS_ESCRIBIR_MS = 20
"""Pausa entre poner el dictado en el portapapeles y mandar Ctrl+V.

El historial del portapapeles de Windows (Win+V), si está activado, lee todo
contenido nuevo unos 7 ms después de escribirlo (medido el 4 sep 2026). Si la
aplicación de destino intenta leer mientras el historial lo tiene abierto, su
pegado sale vacío: Tk, por ejemplo, no reintenta. Con 20 ms de margen el
historial ya ha terminado cuando llega el Ctrl+V.

Nota de diseño: se probó el renderizado diferido (`SetClipboardData` con
`NULL` y esperar `WM_RENDERFORMAT`) para restaurar justo cuando la aplicación
lee, sin retardo fijo. Es inservible con el historial activado: el propio
historial dispara la lectura antes de que nadie pegue.
"""

METODOS = ("portapapeles", "tecleo")


class AppActiva:
    nombre = "app_activa"

    def __init__(
        self,
        metodo: str = "portapapeles",
        restaurar_portapapeles: bool = True,
        auto_enter: bool = False,
    ) -> None:
        if metodo not in METODOS:
            raise ValueError(f"método de inserción desconocido: {metodo!r}")
        self._metodo = metodo
        self._restaurar = restaurar_portapapeles
        self._auto_enter = auto_enter

    def entregar(self, texto: str, ctx: Contexto) -> Entrega:
        """Escribe el texto donde esté el cursor.

        Secuencia con `metodo = "portapapeles"`:

            1. Leer y guardar el portapapeles actual (solo formato texto).
            2. Poner el texto del dictado y esperar `ESPERA_TRAS_ESCRIBIR_MS`.
            3. Soltar los modificadores que el usuario aún tenga apretados
               (viene de soltar `ctrl+win`) y mandar Ctrl+V.
            4. Esperar `RETARDO_RESTAURACION_MS`.
            5. Restaurar lo guardado, si `restaurar_portapapeles` y había texto.

        Con `metodo = "tecleo"`: soltar modificadores y teclear carácter a
        carácter. El portapapeles no se toca.

        D4: si `auto_enter`, mandar Enter al final. Nunca activado por defecto:
        un Enter no deseado en el chat equivocado no se puede deshacer.

        Raises:
            EntregaFallida: el portapapeles no se pudo abrir o Windows rechazó
                la pulsación sintética (ventana elevada). El texto sigue en el
                historial.
        """
        if not texto:
            return Entrega(ok=True, detalle="nada que escribir")
        app = ctx.app_activa or self.app_en_primer_plano() or "la aplicación activa"
        try:
            if self._metodo == "portapapeles":
                self._pegar(texto)
                verbo = "pegado"
            else:
                winapi.soltar_modificadores()
                winapi.teclear(texto)
                verbo = "tecleado"
            if self._auto_enter:
                winapi.pulsar_combinacion(winapi.VK_RETURN)
        except OSError as e:
            log.warning("no se pudo escribir en %s: %s", app, e)
            raise EntregaFallida(f"No se pudo escribir en {app}: {e}") from e
        return Entrega(ok=True, detalle=f"{verbo} en {app}")

    def _pegar(self, texto: str) -> None:
        # Entero bajo el cerrojo: guardar, escribir, Ctrl+V y restaurar son un solo
        # gesto, y una copia desde la bandeja en medio lo estropea (VOZ-80).
        with winapi.cerrojo_portapapeles:
            anterior = winapi.leer_portapapeles() if self._restaurar else None
            winapi.escribir_portapapeles(texto)
            time.sleep(ESPERA_TRAS_ESCRIBIR_MS / 1000)
            winapi.soltar_modificadores()
            winapi.pulsar_combinacion(winapi.VK_CONTROL, winapi.VK_V)
            if self._restaurar:
                time.sleep(RETARDO_RESTAURACION_MS / 1000)
                if anterior is not None:
                    winapi.escribir_portapapeles(anterior)

    @staticmethod
    def app_en_primer_plano() -> str | None:
        """Nombre del ejecutable en primer plano, p. ej. "chrome.exe".

        Se usa para el `detalle` de la entrega y el historial. Devuelve None si
        no se puede averiguar: no es un error, solo se pierde una etiqueta
        informativa.
        """
        try:
            return winapi.ejecutable_en_primer_plano()
        except OSError:
            return None
