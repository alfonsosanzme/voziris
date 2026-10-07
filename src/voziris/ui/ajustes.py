"""F4 — panel de configuración.

Para no tener que editar el TOML a mano. Es lo que hace publicable el proyecto:
sin esto, solo lo usa quien lo ha escrito.

Pestañas, en este orden — el usuario lo abre por los atajos y por el micrófono:

    Atajos     las cuatro combinaciones, capturadas al pulsar, no escritas
               (más idioma y arranque con Windows)
    Audio      micrófono, ganancia con medidor en vivo, sonidos, silencios
    Motor      local/API/auto, clave de API, botón «probar»
    Texto      nivel, modelo de LLM, diccionario, sustituciones
    Destinos   método de inserción, auto-Enter con su advertencia, ruta del
               Markdown con selector de archivo, formato
    Acerca de  versión, licencias y **la atribución a NVIDIA (obligatoria)**

Tkinter, que viene con Python: nada de Qt para seis pestañas. Añadiría decenas
de megas al paquete portable.

Guardar valida y reescribe el TOML con tomlkit (conserva los comentarios) y
llama a `aplicar(config)`, que la aplicación implementa: cambia en caliente lo
que se pueda y devuelve la lista de lo que exige reiniciar, que se muestra.

Vive en el hilo principal (Tk). `abrir()` se llama desde ahí; la bandeja lo
encola con `en_hilo_tk`.

Issue: VOZ-60.
"""

from __future__ import annotations

import copy
import logging
import threading
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from voziris import __version__, teclas
from voziris import config as cfg
from voziris.errores import ConfigInvalida
from voziris.tipos import Nivel
from voziris.ui.bandeja import texto_acerca_de

log = logging.getLogger(__name__)

PESTANAS = ("Atajos", "Audio", "Motor", "Texto", "Destinos", "Acerca de")
ADVERTENCIA_AUTO_ENTER = (
    "Un Enter en el chat equivocado no se deshace: el mensaje sale antes de que lo leas."
)

# Tk llama a las teclas por su keysym; aquí se traducen a los nombres del TOML.
_KEYSYM_A_NOMBRE: dict[str, str] = {
    "Control_L": "ctrl",
    "Control_R": "ctrl",
    "Alt_L": "alt",
    "Alt_R": "alt",
    "Shift_L": "shift",
    "Shift_R": "shift",
    "Win_L": "win",
    "Win_R": "win",
    "Super_L": "win",
    "Super_R": "win",
    "space": "space",
    "Escape": "esc",
    "Return": "enter",
    "Tab": "tab",
    "BackSpace": "backspace",
    "Delete": "delete",
    "Insert": "insert",
    "Home": "home",
    "End": "end",
    "Prior": "pageup",
    "Next": "pagedown",
    "Up": "up",
    "Down": "down",
    "Left": "left",
    "Right": "right",
    "Caps_Lock": "capslock",
    "Print": "printscreen",
    "Pause": "pause",
}


def nombre_de_keysym(keysym: str) -> str | None:
    """«Control_L» → «ctrl», «F5» → «f5», «a» → «a». None si no es una tecla admitida."""
    if keysym in _KEYSYM_A_NOMBRE:
        return _KEYSYM_A_NOMBRE[keysym]
    bajo = keysym.lower()
    if bajo in teclas.NOMBRES:
        return bajo
    if bajo.startswith("kp_") and bajo[3:].isdigit():
        return f"numpad{bajo[3:]}"
    return None


def combinacion_de(keysyms: list[str]) -> str | None:
    """La combinación normalizada de un conjunto de teclas pulsadas a la vez, o None."""
    nombres = [n for n in (nombre_de_keysym(k) for k in keysyms) if n]
    if not nombres:
        return None
    try:
        return teclas.analizar("+".join(dict.fromkeys(nombres))).texto
    except ValueError:
        return None


class CapturaDeAtajo(ttk.Entry):
    """Un Entry que, con el foco, escribe la combinación que se pulsa.

    Se acumulan las teclas mientras alguna esté apretada; al soltar la última
    queda la combinación. Retroceso la borra. Solo modificadores («ctrl+win»)
    también vale.
    """

    def __init__(self, padre: tk.Misc, variable: tk.StringVar) -> None:
        super().__init__(padre, textvariable=variable, width=24)
        self._variable = variable
        self._pulsadas: list[str] = []
        self._apretadas: set[str] = set()
        self.bind("<KeyPress>", self._abajo)
        self.bind("<KeyRelease>", self._arriba)
        self.bind("<FocusIn>", lambda _e: self._reiniciar())
        self.bind("<Key>", lambda _e: "break", add=True)  # nada de escribir a mano

    def _reiniciar(self) -> None:
        self._pulsadas = []
        self._apretadas = set()

    def _abajo(self, evento: tk.Event[Any]) -> str:
        keysym = str(evento.keysym)
        if keysym == "BackSpace" and not self._apretadas:
            self._variable.set("")
            return "break"
        if keysym == "Tab":
            return ""  # Tab sigue moviendo el foco por el panel
        if not self._apretadas:
            self._pulsadas = []
        self._apretadas.add(keysym)
        if keysym not in self._pulsadas:
            self._pulsadas.append(keysym)
        combo = combinacion_de(self._pulsadas)
        if combo:
            self._variable.set(combo)
        return "break"

    def _arriba(self, evento: tk.Event[Any]) -> str:
        self._apretadas.discard(str(evento.keysym))
        return "break"


class Ajustes:
    def __init__(
        self,
        raiz: tk.Tk,
        configuracion: cfg.Config,
        aplicar: Callable[[cfg.Config], list[str]],
        nivel_actual: Callable[[], float] = lambda: 0.0,
        dispositivos: Callable[[], list[tuple[int, str]]] = list,
        probar_clave: Callable[[str, str], tuple[bool, str]] | None = None,
        listar_modelos: Callable[[str, str], tuple[list[str], list[str]]] | None = None,
        version: str = __version__,
    ) -> None:
        self._raiz = raiz
        self._config = configuracion
        self._aplicar = aplicar
        self._nivel_actual = nivel_actual
        self._dispositivos = dispositivos
        self._probar_clave = probar_clave
        self._listar_modelos = listar_modelos
        self._version = version
        self._combos_modelo: dict[str, ttk.Combobox] = {}
        self._ventana: tk.Toplevel | None = None
        self._vars: dict[str, tk.Variable] = {}
        self._buscar_al_abrir = False
        self._textos: dict[str, tk.Text] = {}
        self._medidor: tk.Canvas | None = None
        self._temporizador: str | None = None
        self.notebook: ttk.Notebook | None = None
        self.estado = tk.StringVar(master=raiz, value="")

    # --- ventana ---------------------------------------------------------------------

    def abrir(self) -> None:
        """Crea el panel, o lo trae al frente si ya está abierto."""
        if self._ventana is not None and self._ventana.winfo_exists():
            self._ventana.deiconify()
            self._ventana.lift()
            self._ventana.focus_force()
            return
        v = tk.Toplevel(self._raiz)
        v.title("Ajustes de Voziris")
        v.minsize(560, 480)
        ico = cfg.carpeta_recursos() / "assets" / "voziris.ico"
        if ico.exists():
            try:
                v.iconbitmap(str(ico))  # type: ignore[no-untyped-call]
            except tk.TclError:
                log.debug("sin icono para la ventana de ajustes")
        v.protocol("WM_DELETE_WINDOW", self.cerrar)
        v.bind("<Escape>", lambda _e: self.cerrar())
        self._ventana = v
        self._vars = {}
        self._textos = {}

        nb = ttk.Notebook(v)
        nb.pack(fill="both", expand=True, padx=8, pady=8)
        self.notebook = nb
        constructores = (
            self._pestana_atajos,
            self._pestana_audio,
            self._pestana_motor,
            self._pestana_texto,
            self._pestana_destinos,
            self._pestana_acerca,
        )
        for titulo, construir in zip(PESTANAS, constructores, strict=True):
            marco = ttk.Frame(nb, padding=12)
            nb.add(marco, text=titulo)
            construir(marco)

        pie = ttk.Frame(v, padding=(8, 0, 8, 8))
        pie.pack(fill="x")
        ttk.Label(pie, textvariable=self.estado, foreground="#555").pack(side="left")
        ttk.Button(pie, text="Cancelar", command=self.cerrar).pack(side="right")
        guardar = ttk.Button(pie, text="Guardar", command=self.guardar, default="active")
        guardar.pack(side="right", padx=(0, 8))
        v.bind("<Return>", lambda _e: self.guardar() if self._foco_no_es_texto() else None)
        self._tic_medidor()

    def _foco_no_es_texto(self) -> bool:
        return not isinstance(self._ventana.focus_get() if self._ventana else None, tk.Text)

    def cerrar(self) -> None:
        if self._temporizador is not None:
            self._raiz.after_cancel(self._temporizador)
            self._temporizador = None
        if self._ventana is not None:
            self._ventana.destroy()
            self._ventana = None
        self.notebook = None

    # --- utilidades de construcción ---------------------------------------------------

    def _var(self, clave: str, valor: Any) -> tk.Variable:
        if isinstance(valor, bool):
            var: tk.Variable = tk.BooleanVar(master=self._raiz, value=valor)
        elif isinstance(valor, int) and not isinstance(valor, bool):
            var = tk.IntVar(master=self._raiz, value=valor)
        elif isinstance(valor, float):
            var = tk.DoubleVar(master=self._raiz, value=valor)
        else:
            var = tk.StringVar(master=self._raiz, value=str(valor))
        self._vars[clave] = var
        return var

    def _fila(self, marco: ttk.Frame, fila: int, etiqueta: str) -> None:
        ttk.Label(marco, text=etiqueta).grid(row=fila, column=0, sticky="w", pady=3, padx=(0, 10))

    def _entrada(
        self,
        marco: ttk.Frame,
        fila: int,
        etiqueta: str,
        clave: str,
        valor: Any,
        ancho: int = 40,
        **kwargs: Any,
    ) -> ttk.Entry:
        self._fila(marco, fila, etiqueta)
        e = ttk.Entry(marco, textvariable=self._var(clave, valor), width=ancho, **kwargs)
        e.grid(row=fila, column=1, sticky="we", pady=3)
        return e

    def _casilla(self, marco: ttk.Frame, fila: int, etiqueta: str, clave: str, valor: bool) -> None:
        c = ttk.Checkbutton(marco, text=etiqueta, variable=self._var(clave, valor))
        c.grid(row=fila, column=0, columnspan=2, sticky="w", pady=3)

    def _combo(
        self,
        marco: ttk.Frame,
        fila: int,
        etiqueta: str,
        clave: str,
        valor: str,
        opciones: list[str],
    ) -> ttk.Combobox:
        self._fila(marco, fila, etiqueta)
        c = ttk.Combobox(
            marco, textvariable=self._var(clave, valor), values=opciones, state="readonly", width=37
        )
        c.grid(row=fila, column=1, sticky="w", pady=3)
        return c

    def _combo_libre(
        self, marco: ttk.Frame, fila: int, etiqueta: str, clave: str, valor: str
    ) -> ttk.Combobox:
        """Desplegable que además admite escribir: la lista es una ayuda, no una jaula.

        Los nombres de modelo cambian sin avisar, así que el usuario tiene que
        poder poner uno que la lista todavía no conozca.
        """
        self._fila(marco, fila, etiqueta)
        c = ttk.Combobox(marco, textvariable=self._var(clave, valor), values=[valor], width=37)
        c.grid(row=fila, column=1, sticky="w", pady=3)
        return c

    # --- pestañas ------------------------------------------------------------------------

    def _pestana_atajos(self, m: ttk.Frame) -> None:
        c = self._config
        ttk.Label(
            m,
            text="Pulsa la combinación con el cursor en la casilla. Retroceso la borra.",
            foreground="#555",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        etiquetas = {
            "mantener": "Mantener (graba mientras se pulsa)",
            "clavar": "Clavar (fija el micrófono)",
            "markdown": "Markdown (dicta al archivo)",
            "clavar_markdown": "Clavar al Markdown (fija el micrófono, al archivo)",
            "cancelar": "Cancelar el dictado",
        }
        for fila, nombre in enumerate(cfg.NOMBRES_ATAJOS, start=1):
            self._fila(m, fila, etiquetas[nombre])
            var = self._var(f"atajos.{nombre}", getattr(c.atajos, nombre))
            CapturaDeAtajo(m, var).grid(row=fila, column=1, sticky="w", pady=3)  # type: ignore[arg-type]
        ttk.Separator(m).grid(row=7, column=0, columnspan=2, sticky="we", pady=10)
        self._entrada(m, 8, "Idioma (ISO-639-1)", "general.idioma", c.general.idioma, ancho=6)
        self._casilla(
            m,
            9,
            "Arrancar con Windows (acceso directo en Inicio)",
            "general.arranque_con_windows",
            c.general.arranque_con_windows,
        )

    def _pestana_audio(self, m: ttk.Frame) -> None:
        c = self._config.audio
        nombres = [n for _, n in self._dispositivos()]
        self._fila(m, 0, "Micrófono")
        cb = ttk.Combobox(
            m,
            textvariable=self._var("audio.dispositivo", c.dispositivo),
            values=["(predeterminado)", *nombres],
            width=37,
        )
        cb.grid(row=0, column=1, sticky="w", pady=3)
        if not c.dispositivo:
            cb.set("(predeterminado)")
        self._fila(m, 1, "Ganancia (dB)")
        var_ganancia = tk.DoubleVar(master=self._raiz, value=float(c.ganancia_db))
        self._vars["audio.ganancia_db"] = var_ganancia
        gan = ttk.Scale(
            m, from_=-20, to=20, variable=var_ganancia, orient="horizontal", length=260
        )
        gan.grid(row=1, column=1, sticky="w")
        ttk.Label(m, textvariable=self._vars["audio.ganancia_db"]).grid(row=1, column=2)
        self._fila(m, 2, "Nivel ahora")
        self._medidor = tk.Canvas(m, width=260, height=14, bg="#dddddd", highlightthickness=0)
        self._medidor.grid(row=2, column=1, sticky="w", pady=6)
        self._medidor.create_rectangle(0, 0, 0, 14, fill="#2e7d5b", outline="", tags="barra")
        self._entrada(m, 3, "Búfer previo (ms)", "audio.buffer_previo_ms", c.buffer_previo_ms, 8)
        self._entrada(
            m,
            4,
            "Silencio que cierra «clavar» (ms)",
            "audio.silencio_corte_ms",
            c.silencio_corte_ms,
            8,
        )
        self._casilla(
            m, 5, "Cortar el dictado clavado al callar", "audio.corte_por_silencio",
            c.corte_por_silencio,
        )
        self._casilla(m, 6, "Sonidos de inicio, fin y error", "audio.sonidos", c.sonidos)
        self._fila(m, 7, "Mientras dicto")
        marco = ttk.Frame(m)
        marco.grid(row=7, column=1, sticky="w")
        var = self._var("audio.al_dictar", c.al_dictar)
        for texto, valor in (
            ("Silenciar lo demás", "silenciar"),
            ("Bajarle el volumen", "atenuar"),
            ("Nada", "nada"),
        ):
            ttk.Radiobutton(marco, text=texto, value=valor, variable=var).pack(
                side="left", padx=(0, 10)
            )

    def _pestana_motor(self, m: ttk.Frame) -> None:
        c = self._config
        self._fila(m, 0, "Motor")
        marco = ttk.Frame(m)
        marco.grid(row=0, column=1, sticky="w")
        var = self._var("general.motor", c.general.motor)
        for texto, valor in (("Local", "local"), ("API", "api"), ("Automático", "auto")):
            ttk.Radiobutton(marco, text=texto, value=valor, variable=var).pack(
                side="left", padx=(0, 10)
            )
        ttk.Label(m, text="Local (sin conexión)", font=("Segoe UI", 9, "bold")).grid(
            row=1, column=0, sticky="w", pady=(12, 2)
        )
        self._entrada(m, 2, "Modelo", "motor.local.modelo", c.motor.local.modelo)
        self._entrada(m, 3, "Carpeta de modelos", "motor.local.carpeta", str(c.motor.local.carpeta))
        self._entrada(m, 4, "Hilos (0 = automático)", "motor.local.hilos", c.motor.local.hilos, 8)
        self._combo(
            m,
            5,
            "Cuantización",
            "motor.local.cuantizacion",
            c.motor.local.cuantizacion,
            list(cfg.CUANTIZACIONES),
        )
        ttk.Label(m, text="API (compatible con OpenAI)", font=("Segoe UI", 9, "bold")).grid(
            row=6, column=0, sticky="w", pady=(12, 2)
        )
        self._entrada(m, 7, "URL base", "motor.api.base_url", c.motor.api.base_url)
        self._combos_modelo["audio"] = self._combo_libre(
            m, 8, "Modelo de transcripción", "motor.api.modelo", c.motor.api.modelo
        )
        self._entrada(
            m, 9, "Clave (vacía = GROQ_API_KEY)", "motor.api.clave", c.motor.api.clave, show="•"
        )
        self._entrada(m, 10, "Timeout (s)", "motor.api.timeout_s", c.motor.api.timeout_s, 8)
        self._resultado_clave = tk.StringVar(master=self._raiz, value="")
        ttk.Button(m, text="Probar la clave y ver modelos", command=self._probar).grid(
            row=11, column=0, pady=6, sticky="w"
        )
        ttk.Label(m, textvariable=self._resultado_clave, wraplength=360).grid(
            row=11, column=1, sticky="w"
        )

    def _probar(self) -> None:
        if self._probar_clave is None:
            self._resultado_clave.set("No disponible")
            return
        self._resultado_clave.set("Probando…")
        base_url = str(self._valor("motor.api.base_url"))
        clave = str(self._valor("motor.api.clave"))
        probar = self._probar_clave
        resultado: list[tuple[bool, str]] = []

        def trabajo() -> None:
            try:
                resultado.append(probar(base_url, clave))
            except Exception as e:  # noqa: BLE001 — se muestra, no se propaga
                resultado.append((False, str(e)))

        def comprobar() -> None:
            # Tk no es seguro entre hilos: el hilo deja el resultado y el
            # principal lo recoge cada 100 ms.
            if resultado:
                ok, mensaje = resultado[0]
                self._resultado_clave.set(("✓ " if ok else "✗ ") + mensaje)
                if ok:
                    self._cargar_modelos(base_url, clave)
            elif self._ventana is not None:
                self._raiz.after(100, comprobar)

        threading.Thread(target=trabajo, name="voziris-probar-clave", daemon=True).start()
        self._raiz.after(100, comprobar)

    def _cargar_modelos(self, base_url: str, clave: str) -> None:
        """Rellena los desplegables con los modelos que esa clave puede usar."""
        if self._listar_modelos is None:
            return
        listar = self._listar_modelos
        listas: list[tuple[list[str], list[str]]] = []

        def trabajo() -> None:
            try:
                listas.append(listar(base_url, clave))
            except Exception:  # noqa: BLE001 — decorativo: si falla, se queda como estaba
                log.info("no se pudieron listar los modelos", exc_info=True)
                listas.append(([], []))

        def recoger() -> None:
            if not listas:
                if self._ventana is not None:
                    self._raiz.after(150, recoger)
                return
            audio, chat = listas[0]
            if audio and "audio" in self._combos_modelo:
                self._combos_modelo["audio"].configure(values=audio)
            if chat and "llm" in self._combos_modelo:
                self._combos_modelo["llm"].configure(values=["", *chat])
            if audio or chat:
                self._resultado_clave.set(
                    self._resultado_clave.get()
                    + f" · {len(audio)} de voz y {len(chat)} de texto en la lista"
                )

        threading.Thread(target=trabajo, name="voziris-listar-modelos", daemon=True).start()
        self._raiz.after(150, recoger)

    def _pestana_texto(self, m: ttk.Frame) -> None:
        c = self._config.proceso
        self._fila(m, 0, "Nivel de limpieza")
        marco = ttk.Frame(m)
        marco.grid(row=0, column=1, sticky="w")
        var = self._var("proceso.nivel", c.nivel.value)
        for texto, valor in (
            ("Literal", "literal"),
            ("Limpio", "limpio"),
            ("Reescritura", "reescritura"),
        ):
            ttk.Radiobutton(marco, text=texto, value=valor, variable=var).pack(
                side="left", padx=(0, 10)
            )
        self._combos_modelo["llm"] = self._combo_libre(
            m, 1, "Modelo de LLM (vacío = sin limpieza)", "proceso.llm_modelo", c.llm_modelo
        )
        ttk.Label(
            m,
            text="La lista se rellena al probar la clave en la pestaña Motor.",
            foreground="#555",
        ).grid(row=1, column=2, sticky="w", padx=(8, 0))
        self._fila(m, 2, "Diccionario (una palabra por línea)")
        dic = tk.Text(m, width=40, height=6, undo=True)
        dic.insert("1.0", "\n".join(c.diccionario))
        dic.grid(row=2, column=1, sticky="we", pady=3)
        self._textos["proceso.diccionario"] = dic
        self._fila(m, 3, "Sustituciones (frase = texto, una por línea)")
        sus = tk.Text(m, width=40, height=6, undo=True)
        sus.insert("1.0", "\n".join(f"{k} = {_escapar(v)}" for k, v in c.sustituciones.items()))
        sus.grid(row=3, column=1, sticky="we", pady=3)
        self._textos["proceso.sustituciones"] = sus
        ttk.Label(
            m, text="En el texto de una sustitución, \\n es un salto de línea.", foreground="#555"
        ).grid(row=4, column=1, sticky="w")

    def _pestana_destinos(self, m: ttk.Frame) -> None:
        c = self._config.destino
        ttk.Label(m, text="Aplicación activa", font=("Segoe UI", 9, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 2)
        )
        self._combo(
            m,
            1,
            "Método de inserción",
            "destino.app_activa.metodo",
            c.app_activa.metodo,
            list(cfg.METODOS),
        )
        self._casilla(
            m,
            2,
            "Restaurar el portapapeles después de pegar",
            "destino.app_activa.restaurar_portapapeles",
            c.app_activa.restaurar_portapapeles,
        )
        self._casilla(
            m,
            3,
            "Enviar Enter después de insertar (auto-Enter)",
            "destino.app_activa.auto_enter",
            c.app_activa.auto_enter,
        )
        self.advertencia_auto_enter = ttk.Label(
            m, text="⚠ " + ADVERTENCIA_AUTO_ENTER, foreground="#b3261e", wraplength=420
        )
        self.advertencia_auto_enter.grid(row=4, column=0, columnspan=2, sticky="w", padx=(24, 0))
        ttk.Label(m, text="Archivo Markdown", font=("Segoe UI", 9, "bold")).grid(
            row=5, column=0, sticky="w", pady=(12, 2)
        )
        self._entrada(m, 6, "Ruta", "destino.markdown.ruta", str(c.markdown.ruta))
        ttk.Button(m, text="Elegir…", command=self._elegir_markdown).grid(row=6, column=2, padx=4)
        self._entrada(
            m, 7, "Formato ({sello} y {texto})", "destino.markdown.formato", c.markdown.formato
        )
        self._entrada(
            m, 8, "Sello de tiempo (strftime)", "destino.markdown.sello", c.markdown.sello
        )
        self._entrada(
            m, 9, "Separador entre entradas", "destino.markdown.separador", c.markdown.separador
        )

    def _elegir_markdown(self) -> None:
        actual = Path(str(self._valor("destino.markdown.ruta")))
        ruta = filedialog.asksaveasfilename(
            parent=self._ventana or self._raiz,
            title="Archivo de captura de Markdown",
            initialdir=str(actual.parent) if actual.parent.exists() else None,
            initialfile=actual.name,
            defaultextension=".md",
            filetypes=[("Markdown", "*.md"), ("Todos", "*.*")],
            confirmoverwrite=False,
        )
        if ruta:
            self._vars["destino.markdown.ruta"].set(ruta)

    def _pestana_acerca(self, m: ttk.Frame) -> None:
        ttk.Label(m, text=f"Voziris {self._version}", font=("Segoe UI", 12, "bold")).pack(
            anchor="w"
        )
        # «preguntar» se ve sin marcar y sigue siendo «preguntar» si no se toca (VOZ-82).
        self._buscar_al_abrir = self._config.actualizaciones.buscar == "sí"
        ttk.Checkbutton(
            m,
            text="Avisar de las versiones nuevas (mira en GitHub una vez al día)",
            variable=self._var("actualizaciones.buscar", self._buscar_al_abrir),
        ).pack(anchor="w", pady=(6, 0))
        ttk.Label(m, text=texto_acerca_de(self._version), justify="left", wraplength=500).pack(
            anchor="w", pady=8
        )
        ttk.Label(
            m, text="Historial: " + str(self._config.carpeta / "historial"), foreground="#555"
        ).pack(anchor="w", pady=(12, 0))
        ttk.Label(
            m, text="Registro: " + str(self._config.carpeta / "voziris.log"), foreground="#555"
        ).pack(anchor="w")

    # --- medidor -----------------------------------------------------------------------------

    def _tic_medidor(self) -> None:
        if self._ventana is None or self._medidor is None:
            return
        try:
            nivel = max(0.0, min(1.0, float(self._nivel_actual())))
        except Exception:  # noqa: BLE001 — decorativo
            nivel = 0.0
        self._medidor.coords("barra", 0, 0, int(260 * nivel), 14)
        self._temporizador = self._raiz.after(33, self._tic_medidor)

    # --- guardar -------------------------------------------------------------------------------

    def _valor(self, clave: str) -> Any:
        """El valor de una variable de Tk. `Variable.get` no lleva tipos en typeshed."""
        return self._vars[clave].get()  # type: ignore[no-untyped-call]

    def leer(self) -> cfg.Config:
        """Una `Config` nueva con lo que hay en el panel. Lanza `ConfigInvalida` si no cuadra."""
        nueva = copy.deepcopy(self._config)
        g = self._valor
        try:
            nueva.general.idioma = str(g("general.idioma")).strip()
            nueva.general.motor = str(g("general.motor"))
            nueva.general.arranque_con_windows = bool(g("general.arranque_con_windows"))
            buscar = bool(g("actualizaciones.buscar"))
            if buscar != self._buscar_al_abrir:
                # Solo si se ha tocado: la respuesta a la pregunta del arranque, dada
                # con el panel abierto, no se pisa con lo que la casilla enseñaba.
                nueva.actualizaciones.buscar = "sí" if buscar else "no"
            for nombre in cfg.NOMBRES_ATAJOS:
                setattr(nueva.atajos, nombre, str(g(f"atajos.{nombre}")).strip())
            dispositivo = str(g("audio.dispositivo"))
            nueva.audio.dispositivo = "" if dispositivo == "(predeterminado)" else dispositivo
            nueva.audio.ganancia_db = round(float(g("audio.ganancia_db")), 1)
            nueva.audio.buffer_previo_ms = int(g("audio.buffer_previo_ms"))
            nueva.audio.silencio_corte_ms = int(g("audio.silencio_corte_ms"))
            nueva.audio.corte_por_silencio = bool(g("audio.corte_por_silencio"))
            nueva.audio.sonidos = bool(g("audio.sonidos"))
            nueva.audio.al_dictar = str(g("audio.al_dictar"))
            nueva.motor.local.modelo = str(g("motor.local.modelo")).strip()
            nueva.motor.local.carpeta = Path(str(g("motor.local.carpeta")).strip())
            nueva.motor.local.hilos = int(g("motor.local.hilos"))
            nueva.motor.local.cuantizacion = str(g("motor.local.cuantizacion"))
            nueva.motor.api.base_url = str(g("motor.api.base_url")).strip()
            nueva.motor.api.modelo = str(g("motor.api.modelo")).strip()
            nueva.motor.api.clave = str(g("motor.api.clave")).strip()
            nueva.motor.api.timeout_s = int(g("motor.api.timeout_s"))
            nueva.proceso.nivel = Nivel(str(g("proceso.nivel")))
            nueva.proceso.llm_modelo = str(g("proceso.llm_modelo")).strip()
            nueva.proceso.diccionario = [
                p.strip()
                for p in self._textos["proceso.diccionario"].get("1.0", "end").splitlines()
                if p.strip()
            ]
            nueva.proceso.sustituciones = _leer_sustituciones(
                self._textos["proceso.sustituciones"].get("1.0", "end")
            )
            nueva.destino.app_activa.metodo = str(g("destino.app_activa.metodo"))
            nueva.destino.app_activa.restaurar_portapapeles = bool(
                g("destino.app_activa.restaurar_portapapeles")
            )
            nueva.destino.app_activa.auto_enter = bool(g("destino.app_activa.auto_enter"))
            nueva.destino.markdown.ruta = Path(str(g("destino.markdown.ruta")).strip())
            nueva.destino.markdown.formato = str(g("destino.markdown.formato"))
            nueva.destino.markdown.sello = str(g("destino.markdown.sello"))
            nueva.destino.markdown.separador = str(g("destino.markdown.separador"))
        except (tk.TclError, ValueError) as e:
            raise ConfigInvalida(f"Hay un valor que no es un número donde debería: {e}") from e
        return nueva

    def guardar(self) -> bool:
        """Valida, escribe el TOML y aplica. Devuelve True si se guardó."""
        try:
            nueva = self.leer()
            cfg.guardar(nueva)
        except ConfigInvalida as e:
            messagebox.showerror("No se puede guardar", str(e), parent=self._ventana or self._raiz)
            return False
        self._config = nueva
        try:
            pendientes = self._aplicar(nueva)
        except Exception as e:  # noqa: BLE001 — guardado sí; aplicar en caliente, no
            log.exception("al aplicar los ajustes")
            pendientes = [f"no se pudo aplicar en caliente: {e}"]
        if pendientes:
            self.estado.set("Guardado. Requiere reiniciar: " + ", ".join(pendientes))
            messagebox.showinfo(
                "Guardado",
                "Guardado. Estos cambios se aplican al reiniciar Voziris:\n\n- "
                + "\n- ".join(pendientes),
                parent=self._ventana or self._raiz,
            )
        else:
            self.estado.set("Guardado y aplicado.")
        return True


def _escapar(valor: str) -> str:
    return valor.replace("\\", "\\\\").replace("\n", "\\n")


def _desescapar(valor: str) -> str:
    return valor.replace("\\n", "\n").replace("\\\\", "\\")


def _leer_sustituciones(texto: str) -> dict[str, str]:
    reglas: dict[str, str] = {}
    for numero, linea in enumerate(texto.splitlines(), 1):
        if not linea.strip():
            continue
        if "=" not in linea:
            raise ConfigInvalida(
                f"Sustituciones, línea {numero}: falta el «=» entre la frase y el texto"
            )
        clave, valor = linea.split("=", 1)
        if not clave.strip():
            raise ConfigInvalida(f"Sustituciones, línea {numero}: la frase está vacía")
        reglas[clave.strip()] = _desescapar(valor.strip())
    return reglas
