"""VOZ-82 — la pregunta del primer arranque, sin quitar el foco.

Una tarjeta abajo a la derecha, por encima de todo y con `WS_EX_NOACTIVATE`,
como el indicador de grabación (`hud.py`): recibe clics, pero nunca el
teclado. Si salta mientras escribes, ninguna tecla la contesta. Un sí es un
clic.

Un `MessageBox` de Windows no valía: se lleva el foco, y sus botones «&Sí» y
«&No» se pulsan con una letra. Escribiendo un correo, una «s» habría sido un
sí a conectarse a GitHub todos los días.

Hilos: todo en el de Tk.
"""

from __future__ import annotations

import contextlib
import logging
import tkinter as tk
from collections.abc import Callable
from tkinter import ttk

from voziris import winapi

log = logging.getLogger(__name__)

ANCHO = 380
MARGEN = 16
FONDO = "#ffffff"
BORDE = "#b8bcc4"
TEXTO = "#1f2328"


class Pregunta:
    """Una pregunta de sí o no que espera un clic. `al_contestar` recibe True o False."""

    def __init__(
        self,
        raiz: tk.Tk,
        titulo: str,
        texto: str,
        al_contestar: Callable[[bool], None],
        si: str = "Sí, mirar una vez al día",
        no: str = "No, gracias",
    ) -> None:
        self._al_contestar = al_contestar
        self.contestada = False
        v = tk.Toplevel(raiz)
        v.withdraw()
        v.overrideredirect(True)
        v.attributes("-topmost", True)
        marco = tk.Frame(v, bg=FONDO, highlightthickness=1, highlightbackground=BORDE)
        marco.pack(fill="both", expand=True)
        tk.Label(
            marco, text=titulo, bg=FONDO, fg=TEXTO, font=("Segoe UI", 11, "bold"), anchor="w",
        ).pack(fill="x", padx=16, pady=(14, 6))
        tk.Label(
            marco, text=texto, bg=FONDO, fg=TEXTO, font=("Segoe UI", 10), justify="left",
            anchor="w", wraplength=ANCHO - 34,
        ).pack(fill="x", padx=16)
        botones = tk.Frame(marco, bg=FONDO)
        botones.pack(fill="x", padx=16, pady=(14, 14))
        self.boton_no = ttk.Button(
            botones, text=no, takefocus=False, command=lambda: self._contestar(False)
        )
        self.boton_no.pack(side="right")
        self.boton_si = ttk.Button(
            botones, text=si, takefocus=False, command=lambda: self._contestar(True)
        )
        self.boton_si.pack(side="right", padx=(0, 8))
        self.ventana = v
        v.update_idletasks()
        try:
            # Desde que existe, antes de enseñarla: como el HUD.
            winapi.hacer_ventana_sin_foco(self._hwnd())
        except OSError:
            log.warning("no se pudo marcar la pregunta como ventana sin foco")
        self._presentar(raiz)

    def _hwnd(self) -> int:
        return int(self.ventana.frame(), 16)

    def _presentar(self, raiz: tk.Tk) -> None:
        """Coloca y muestra sin activar. Lo mismo que `Hud._presentar`, y por lo mismo (VOZ-63).

        Tk puede rehacer la ventana nativa tras un cambio de geometría con
        `overrideredirect`, y se lleva los estilos: se deja que lo haga
        (`update_idletasks`) y luego se ponen los estilos y se muestra con
        `SetWindowPos`, nunca con `deiconify()`, que activa.
        """
        v = self.ventana
        alto = v.winfo_reqheight()
        try:
            x, y, ancho, alto_area = winapi.area_de_trabajo_activa()
        except OSError:
            x, y = 0, 0
            ancho, alto_area = raiz.winfo_screenwidth(), raiz.winfo_screenheight()
        px, py = x + ancho - ANCHO - MARGEN, y + alto_area - alto - MARGEN
        v.geometry(f"{ANCHO}x{alto}+{px}+{py}")
        v.update_idletasks()
        try:
            hwnd = self._hwnd()
            winapi.hacer_ventana_sin_foco(hwnd)
            winapi.colocar_sin_activar(hwnd, px, py, ANCHO, alto)
        except OSError as e:
            log.info("no se pudo mostrar la pregunta sin foco (%s); deiconify", e)
            v.deiconify()  # fuera de Windows: mejor visible que nada

    def _contestar(self, si: bool) -> None:
        if self.contestada:
            return
        self.contestada = True
        self.cerrar()
        self._al_contestar(si)

    def cerrar(self) -> None:
        """Quita la tarjeta sin contestar (al salir de Voziris, por ejemplo)."""
        with contextlib.suppress(tk.TclError):
            self.ventana.destroy()
