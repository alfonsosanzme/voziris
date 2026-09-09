"""A4 — indicador flotante con nivel de audio.

Confirma que te está oyendo. Sin esto, el usuario habla sin saber si se está
grabando y descubre el fallo cuando no aparece el texto.

Requisitos de comportamiento:
  - Ventana sin bordes, siempre encima, sin foco. **No debe robar el foco
    nunca**: si lo hace, el texto se pega en el propio indicador. Estilos
    `WS_EX_NOACTIVATE | WS_EX_TOPMOST | WS_EX_TOOLWINDOW` puestos con
    `SetWindowLong`, y mostrada con `ShowWindow(SW_SHOWNOACTIVATE)`, nunca
    con `deiconify()` (que activa).
  - Fuera de Alt+Tab y de la barra de tareas (`WS_EX_TOOLWINDOW`).
  - Centrada en el borde inferior del monitor donde está la ventana activa,
    que es donde se está dictando. El cursor del ratón no tiene relación.
  - Barra de nivel a ~30 fps mientras graba; «procesando» al soltar.
  - Al terminar, desaparece. Si hubo aviso, se queda ~1,5 s mostrándolo.
  - Con las animaciones de Windows desactivadas (movimiento reducido), la
    barra salta al valor en vez de deslizarse.

Hilos: vive en el hilo principal (Tk). Los métodos públicos se pueden llamar
desde cualquier hilo: encolan en el hilo de Tk a través de `en_hilo_tk`.

Issue: VOZ-22.
"""

from __future__ import annotations

import logging
import tkinter as tk
from collections.abc import Callable

from voziris import winapi
from voziris.tipos import Modo

log = logging.getLogger(__name__)

ANCHO, ALTO = 300, 64
MARGEN_INFERIOR = 48
FPS_MS = 33
AVISO_MS = 1500
SUAVIZADO = 0.45
"""Cuánto se acerca la barra al nivel real en cada fotograma (0..1). 1 = salta."""

ETIQUETAS = {
    Modo.MANTENER: "Grabando…  suelta para terminar",
    Modo.CLAVAR: "Grabando ●  clavado: pulsa otra vez o calla",
}

FONDO = "#1e2229"
TEXTO = "#e6e8ec"
BARRA_FONDO = "#2f353f"
BARRA = "#6bc29a"
BARRA_ALTA = "#e8735e"
PROCESANDO = "#7fa9d3"


class Hud:
    def __init__(
        self,
        raiz: tk.Tk,
        nivel: Callable[[], float] = lambda: 0.0,
        en_hilo_tk: Callable[[Callable[[], None]], None] | None = None,
        animaciones: bool | None = None,
    ) -> None:
        self._raiz = raiz
        self._nivel = nivel
        self._en_hilo_tk = en_hilo_tk or (lambda fn: raiz.after(0, fn))
        self._animaciones = winapi.animaciones_activas() if animaciones is None else animaciones
        self._ventana: tk.Toplevel | None = None
        self._lienzo: tk.Canvas | None = None
        self._texto_id = 0
        self._barra_id = 0
        self._barra_fondo_id = 0
        self._grabando = False
        self._nivel_dibujado = 0.0
        self._temporizador: str | None = None
        self._aviso_pendiente: str | None = None
        self.visible = False

    # --- API pública (cualquier hilo) --------------------------------------------

    def mostrar_grabando(self, modo: Modo = Modo.MANTENER) -> None:
        self._en_hilo_tk(lambda: self._mostrar(ETIQUETAS[modo], grabando=True))

    def actualizar_nivel(self, nivel: float) -> None:
        """`nivel` en 0..1, desde `Captura.nivel_actual()`. Opcional: el HUD ya lo consulta."""
        self._en_hilo_tk(lambda: self._dibujar_nivel(nivel))

    def mostrar_procesando(self) -> None:
        self._en_hilo_tk(lambda: self._mostrar("Procesando…", grabando=False))

    def ocultar(self, aviso: str | None = None) -> None:
        self._en_hilo_tk(lambda: self._ocultar(aviso))

    def aviso(self, texto: str) -> None:
        """Muestra un aviso ~1,5 s aunque no haya dictado (p. ej. «sin red»)."""
        self._en_hilo_tk(lambda: self._ocultar(texto))

    # --- hilo de Tk ---------------------------------------------------------------

    def _crear(self) -> tk.Toplevel:
        if self._ventana is not None:
            return self._ventana
        v = tk.Toplevel(self._raiz)
        v.withdraw()
        v.overrideredirect(True)
        v.attributes("-topmost", True)
        v.configure(bg=FONDO)
        lienzo = tk.Canvas(v, width=ANCHO, height=ALTO, bg=FONDO, highlightthickness=0)
        lienzo.pack()
        self._texto_id = lienzo.create_text(
            16, 20, anchor="w", text="", fill=TEXTO, font=("Segoe UI", 10),
        )
        self._barra_fondo_id = lienzo.create_rectangle(
            16, 40, ANCHO - 16, 50, fill=BARRA_FONDO, outline=""
        )
        self._barra_id = lienzo.create_rectangle(16, 40, 16, 50, fill=BARRA, outline="")
        v.update_idletasks()
        try:
            winapi.hacer_ventana_sin_foco(self.hwnd(v))
        except OSError:
            log.warning("no se pudo marcar el HUD como ventana sin foco")
        self._ventana, self._lienzo = v, lienzo
        return v

    @staticmethod
    def hwnd(ventana: tk.Toplevel) -> int:
        """El HWND del marco de nivel superior de la ventana Tk."""
        return int(ventana.frame(), 16)

    def _colocar(self, v: tk.Toplevel) -> tuple[int, int]:
        """Calcula la posición (abajo, centrado, en el monitor activo) y se la dice a Tk."""
        try:
            x, y, ancho, alto = winapi.area_de_trabajo_activa()
        except OSError:
            x, y = 0, 0
            ancho, alto = self._raiz.winfo_screenwidth(), self._raiz.winfo_screenheight()
        px, py = x + (ancho - ANCHO) // 2, y + alto - ALTO - MARGEN_INFERIOR
        v.geometry(f"{ANCHO}x{ALTO}+{px}+{py}")
        return px, py

    def _presentar(self, v: tk.Toplevel) -> None:
        """Coloca y muestra sin activar, reaplicando los estilos cada vez.

        Tk puede recrear la ventana nativa detrás de un `withdraw()` con
        `overrideredirect`, y con ella se van WS_EX_NOACTIVATE y TOPMOST. Y
        difiere la geometría de una ventana retirada hasta que la mapea él,
        que aquí no ocurre porque la mapeamos nosotros. Resultado, a veces:
        la barra no sale, o sale detrás. Por eso en cada muestra se ponen los
        estilos otra vez y se usa `SetWindowPos` con posición, TOPMOST y
        SHOWWINDOW en la misma llamada (VOZ-63).
        """
        px, py = self._colocar(v)
        v.update_idletasks()
        try:
            hwnd = self.hwnd(v)
            winapi.hacer_ventana_sin_foco(hwnd)
            winapi.colocar_sin_activar(hwnd, px, py, ANCHO, ALTO)
        except OSError as e:
            log.info("no se pudo mostrar el HUD con SetWindowPos (%s); deiconify", e)
            v.deiconify()  # fuera de Windows (tests en CI): mejor visible que nada

    def _mostrar(self, texto: str, grabando: bool) -> None:
        v = self._crear()
        assert self._lienzo is not None
        self._cancelar_temporizador()
        self._lienzo.itemconfigure(self._texto_id, text=texto)
        self._grabando = grabando
        self._lienzo.itemconfigure(self._barra_id, fill=BARRA if grabando else PROCESANDO)
        if not grabando:
            self._dibujar_nivel(1.0 if not self._animaciones else 0.0)
        self._presentar(v)
        self.visible = True
        if grabando:
            self._tic()

    def _tic(self) -> None:
        if not self._grabando or not self.visible:
            return
        try:
            objetivo = max(0.0, min(1.0, float(self._nivel())))
        except Exception:  # noqa: BLE001 — el nivel es decorativo
            objetivo = 0.0
        if self._animaciones:
            self._nivel_dibujado += (objetivo - self._nivel_dibujado) * SUAVIZADO
        else:
            self._nivel_dibujado = objetivo
        self._dibujar_nivel(self._nivel_dibujado)
        self._temporizador = self._raiz.after(FPS_MS, self._tic)

    def _dibujar_nivel(self, nivel: float) -> None:
        if self._lienzo is None:
            return
        nivel = max(0.0, min(1.0, nivel))
        largo = 16 + int((ANCHO - 32) * nivel)
        self._lienzo.coords(self._barra_id, 16, 40, largo, 50)
        if self._grabando:
            self._lienzo.itemconfigure(self._barra_id, fill=BARRA_ALTA if nivel > 0.9 else BARRA)

    def _ocultar(self, aviso: str | None) -> None:
        self._cancelar_temporizador()
        self._grabando = False
        if aviso:
            v = self._crear()
            assert self._lienzo is not None
            self._lienzo.itemconfigure(self._texto_id, text=aviso)
            self._dibujar_nivel(0.0)
            if not self.visible:
                self._presentar(v)
                self.visible = True
            self._temporizador = self._raiz.after(AVISO_MS, lambda: self._ocultar(None))
            return
        if self._ventana is not None:
            self._ventana.withdraw()
        self.visible = False
        self._nivel_dibujado = 0.0

    def _cancelar_temporizador(self) -> None:
        if self._temporizador is not None:
            self._raiz.after_cancel(self._temporizador)
            self._temporizador = None

    def destruir(self) -> None:
        self._cancelar_temporizador()
        if self._ventana is not None:
            self._ventana.destroy()
            self._ventana = None
            self._lienzo = None
        self.visible = False
