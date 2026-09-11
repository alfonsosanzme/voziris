"""Interfaz de la transcripción de grabaciones: elegir el archivo y ver el progreso.

Dos piezas de Tk, pequeñas a propósito:

  - `elegir_grabacion(raiz)`: selector de archivo y una pregunta, «¿cuántas
    personas hablan?». Devuelve la ruta y "1", "auto" o un número. Se llama
    desde el hilo de Tk (la bandeja lo encola con `en_hilo_tk`).
  - `ejecutar_con_progreso(...)`: una ventana con el nombre del archivo, la
    fase en curso y una barra, que corre el trabajo en un hilo y devuelve su
    resultado. Es lo que ve quien pulsa «Transcribir con Voziris» en el
    Explorador, porque el ejecutable no tiene consola. Sale con su propio
    `tk.Tk()`: en ese proceso no hay otra cosa.

Cancelar no interrumpe a sherpa-onnx en mitad de la separación (no expone
forma de hacerlo), pero sí para entre trozo y trozo de la transcripción,
que es donde se va el grueso del tiempo.

Issue: VOZ-72.
"""

from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, ttk
from typing import TypeVar

from voziris.archivos import FORMATOS
from voziris.errores import VozirisError

log = logging.getLogger(__name__)

T = TypeVar("T")
Progreso = Callable[[str, float | None], None]

OPCIONES_HABLANTES: tuple[tuple[str, str], ...] = (
    ("1", "Una sola voz (una nota, un dictado largo)"),
    ("auto", "Varias personas: que Voziris las distinga"),
    ("n", "Varias, y sé cuántas:"),
)


class TranscripcionCancelada(VozirisError):
    """El usuario pulsó «Cancelar» en la ventana de progreso."""


# --- elegir ------------------------------------------------------------------------------


def elegir_grabacion(
    raiz: tk.Misc, carpeta_inicial: Path | None = None
) -> tuple[Path, str] | None:
    """Selector de archivo y pregunta de hablantes. None si se cancela cualquiera de los dos."""
    tipos = [
        ("Grabaciones", " ".join(f"*{ext}" for ext in FORMATOS)),
        ("Todos los archivos", "*.*"),
    ]
    elegido = filedialog.askopenfilename(
        parent=raiz,
        title="Grabación que transcribir",
        filetypes=tipos,
        initialdir=str(carpeta_inicial) if carpeta_inicial else None,
    )
    if not elegido:
        return None
    hablantes = preguntar_hablantes(raiz, Path(elegido).name)
    if hablantes is None:
        return None
    return Path(elegido), hablantes


def preguntar_hablantes(raiz: tk.Misc, nombre: str) -> str | None:
    """Diálogo modal: "1", "auto", un número como texto, o None si se cancela."""
    v = tk.Toplevel(raiz)
    v.title("Transcribir con Voziris")
    v.resizable(False, False)
    v.attributes("-topmost", True)
    marco = ttk.Frame(v, padding=14)
    marco.grid(sticky="nsew")
    ttk.Label(marco, text=nombre, font=("Segoe UI", 10, "bold")).grid(
        row=0, column=0, columnspan=2, sticky="w"
    )
    ttk.Label(marco, text="¿Cuántas personas hablan?").grid(
        row=1, column=0, columnspan=2, sticky="w", pady=(8, 4)
    )
    eleccion = tk.StringVar(value="auto")
    cuantos = tk.IntVar(value=2)
    fila = 2
    for clave, etiqueta in OPCIONES_HABLANTES:
        ttk.Radiobutton(marco, text=etiqueta, value=clave, variable=eleccion).grid(
            row=fila, column=0, sticky="w", padx=(6, 0)
        )
        if clave == "n":
            caja = ttk.Spinbox(marco, from_=2, to=20, width=4, textvariable=cuantos)
            caja.grid(row=fila, column=1, sticky="w", padx=(4, 0))
        fila += 1
    ttk.Label(
        marco,
        text="El resultado se guarda como .md junto a la grabación y se abre al terminar.",
        foreground="#666",
    ).grid(row=fila, column=0, columnspan=2, sticky="w", pady=(10, 0))
    fila += 1
    resultado: list[str | None] = [None]

    def aceptar() -> None:
        clave = eleccion.get()
        resultado[0] = str(max(2, cuantos.get())) if clave == "n" else clave
        v.destroy()

    botones = ttk.Frame(marco)
    botones.grid(row=fila, column=0, columnspan=2, sticky="e", pady=(12, 0))
    ttk.Button(botones, text="Cancelar", command=v.destroy).grid(row=0, column=0, padx=(0, 6))
    ttk.Button(botones, text="Transcribir", command=aceptar, default="active").grid(
        row=0, column=1
    )
    v.bind("<Return>", lambda _e: aceptar())
    v.bind("<Escape>", lambda _e: v.destroy())
    if isinstance(raiz, tk.Wm):
        v.transient(raiz)
    v.grab_set()
    v.focus_force()
    raiz.wait_window(v)
    return resultado[0]


# --- progreso ----------------------------------------------------------------------------


class VentanaProgreso:
    """Nombre del archivo, fase y barra. `progreso()` se puede llamar desde cualquier hilo."""

    def __init__(self, raiz: tk.Tk, titulo: str, detalle: str) -> None:
        self._raiz = raiz
        self._cancelado = threading.Event()
        self._cola: queue.Queue[tuple[str, float | None]] = queue.Queue()
        raiz.title(titulo)
        raiz.resizable(False, False)
        raiz.attributes("-topmost", True)
        raiz.protocol("WM_DELETE_WINDOW", self.cancelar)
        marco = ttk.Frame(raiz, padding=14)
        marco.grid(sticky="nsew")
        ttk.Label(marco, text=detalle, font=("Segoe UI", 10, "bold"), wraplength=380).grid(
            row=0, column=0, sticky="w"
        )
        self._mensaje = tk.StringVar(value="Preparando…")
        ttk.Label(marco, textvariable=self._mensaje, wraplength=380).grid(
            row=1, column=0, sticky="w", pady=(8, 4)
        )
        self._barra = ttk.Progressbar(marco, length=380, mode="indeterminate")
        self._barra.grid(row=2, column=0, sticky="we")
        self._barra.start(12)
        self._boton = ttk.Button(marco, text="Cancelar", command=self.cancelar)
        self._boton.grid(row=3, column=0, sticky="e", pady=(12, 0))
        raiz.after(100, self._vaciar)

    def progreso(self, mensaje: str, fraccion: float | None) -> None:
        """Callback para `grabaciones.transcribir_archivo`. Lanza si se canceló."""
        if self._cancelado.is_set():
            raise TranscripcionCancelada("cancelado por el usuario")
        self._cola.put((mensaje, fraccion))

    def cancelar(self) -> None:
        self._cancelado.set()
        self._mensaje.set("Cancelando…")
        self._boton.state(["disabled"])

    @property
    def cancelado(self) -> bool:
        return self._cancelado.is_set()

    def _vaciar(self) -> None:
        ultimo: tuple[str, float | None] | None = None
        while True:
            try:
                ultimo = self._cola.get_nowait()
            except queue.Empty:
                break
        if ultimo is not None and not self._cancelado.is_set():
            mensaje, fraccion = ultimo
            self._mensaje.set(mensaje + (f"  {fraccion:.0%}" if fraccion is not None else ""))
            if fraccion is None:
                if self._barra["mode"] != "indeterminate":
                    self._barra.configure(mode="indeterminate")
                    self._barra.start(12)
            else:
                if self._barra["mode"] != "determinate":
                    self._barra.stop()
                    self._barra.configure(mode="determinate", maximum=1.0)
                self._barra["value"] = fraccion
        self._raiz.after(100, self._vaciar)


def ejecutar_con_progreso(titulo: str, detalle: str, trabajo: Callable[[Progreso], T]) -> T:
    """Abre la ventana, corre `trabajo(progreso)` en un hilo y devuelve lo que devuelva.

    Raises:
        TranscripcionCancelada: se pulsó Cancelar (o se cerró la ventana).
        Lo que lance `trabajo`, tal cual.
    """
    raiz = tk.Tk()
    ventana = VentanaProgreso(raiz, titulo, detalle)
    resultado: list[T] = []
    fallo: list[BaseException] = []

    def correr() -> None:
        try:
            resultado.append(trabajo(ventana.progreso))
        except BaseException as e:  # noqa: BLE001 — se relanza en el hilo principal
            fallo.append(e)
        finally:
            raiz.after(0, raiz.quit)

    hilo = threading.Thread(target=correr, name="voziris-transcripcion", daemon=True)
    hilo.start()
    raiz.mainloop()
    hilo.join(timeout=2)
    raiz.destroy()
    if fallo:
        raise fallo[0]
    if not resultado:
        raise TranscripcionCancelada("cancelado por el usuario")
    return resultado[0]
