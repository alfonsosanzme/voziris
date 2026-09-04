"""Tests del panel de ajustes (VOZ-60). Necesitan Tk; no roban el foco ni inyectan teclas."""

from __future__ import annotations

import time
import tkinter as tk
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from voziris import config as cfg
from voziris.errores import ConfigInvalida
from voziris.tipos import Nivel
from voziris.ui import ajustes as mod
from voziris.ui.ajustes import PESTANAS, Ajustes, combinacion_de, nombre_de_keysym

RAIZ = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def raiz() -> Iterator[tk.Tk]:
    try:
        r = tk.Tk()
    except tk.TclError as e:
        pytest.skip(f"sin Tk: {e}")
    r.withdraw()
    yield r
    r.destroy()


@pytest.fixture
def configuracion(tmp_path: Path) -> cfg.Config:
    (tmp_path / cfg.NOMBRE_EJEMPLO).write_text(
        (RAIZ / cfg.NOMBRE_EJEMPLO)
        .read_text(encoding="utf-8")
        .replace('ruta = "C:/Users/CAMBIAME/vault/entrada.md"', 'ruta = "./vault/entrada.md"'),
        encoding="utf-8",
    )
    (tmp_path / "vault").mkdir()
    return cfg.cargar(tmp_path / cfg.NOMBRE_ARCHIVO)


def _bombear(raiz: tk.Tk, segundos: float = 0.05) -> None:
    limite = time.monotonic() + segundos
    while time.monotonic() < limite:
        raiz.update()
        time.sleep(0.005)


def _panel(
    raiz: tk.Tk, configuracion: cfg.Config, **kwargs: Any
) -> tuple[Ajustes, list[cfg.Config]]:
    aplicadas: list[cfg.Config] = []
    pendientes: list[str] = kwargs.pop("pendientes", [])

    def aplicar(c: cfg.Config) -> list[str]:
        aplicadas.append(c)
        return pendientes

    a = Ajustes(raiz, configuracion, aplicar, **kwargs)
    a.abrir()
    _bombear(raiz)
    return a, aplicadas


# --- captura de atajos (pura) ---------------------------------------------------------


def test_keysyms_a_nombres() -> None:
    assert nombre_de_keysym("Control_L") == "ctrl" and nombre_de_keysym("Win_R") == "win"
    assert nombre_de_keysym("F5") == "f5" and nombre_de_keysym("a") == "a"
    assert nombre_de_keysym("KP_7") == "numpad7" and nombre_de_keysym("Cyrillic_a") is None


def test_combinacion_de_teclas_pulsadas() -> None:
    assert combinacion_de(["Control_L", "Shift_L", "space"]) == "ctrl+shift+space"
    assert combinacion_de(["Win_L", "Control_L"]) == "ctrl+win"
    assert combinacion_de(["Control_L", "a", "b"]) is None  # dos teclas normales: no vale
    assert combinacion_de([]) is None


# --- panel --------------------------------------------------------------------------------


def test_seis_pestanas_en_orden(raiz: tk.Tk, configuracion: cfg.Config) -> None:
    a, _ = _panel(raiz, configuracion)
    assert a.notebook is not None
    assert [a.notebook.tab(t, "text") for t in a.notebook.tabs()] == list(PESTANAS)
    a.cerrar()


def test_guardar_conserva_comentarios_y_aplica(raiz: tk.Tk, configuracion: cfg.Config) -> None:
    antes = configuracion.ruta_archivo.read_text(encoding="utf-8")
    a, aplicadas = _panel(raiz, configuracion, pendientes=[])
    a._vars["audio.sonidos"].set(False)
    a._vars["proceso.nivel"].set("reescritura")
    a._vars["atajos.clavar"].set("ctrl+shift+f9")
    a._textos["proceso.diccionario"].delete("1.0", "end")
    a._textos["proceso.diccionario"].insert("1.0", "Creatics\n\nKairis\n")
    a._textos["proceso.sustituciones"].delete("1.0", "end")
    a._textos["proceso.sustituciones"].insert("1.0", "punto y aparte = \\n\\n\ncoma = ,\n")
    assert a.guardar()
    assert a.estado.get().startswith("Guardado")
    nueva = aplicadas[0]
    assert nueva.audio.sonidos is False and nueva.proceso.nivel is Nivel.REESCRITURA
    assert nueva.atajos.clavar == "ctrl+shift+f9"
    assert nueva.proceso.diccionario == ["Creatics", "Kairis"]
    assert nueva.proceso.sustituciones == {"punto y aparte": "\n\n", "coma": ","}
    despues = configuracion.ruta_archivo.read_text(encoding="utf-8")
    assert "# C4: literal | limpio | reescritura" in despues  # el comentario sigue ahí
    assert 'nivel = "reescritura"' in despues and "sonidos = false" in despues
    cambiadas = [
        d for a_, d in zip(antes.splitlines(), despues.splitlines(), strict=False) if a_ != d
    ]
    assert len(cambiadas) <= 8  # solo lo tocado
    a.cerrar()


def test_valor_invalido_no_guarda_y_dice_que(
    raiz: tk.Tk, configuracion: cfg.Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    errores: list[str] = []
    monkeypatch.setattr(mod.messagebox, "showerror", lambda t, m, **k: errores.append(m))
    a, aplicadas = _panel(raiz, configuracion)
    a._vars["atajos.mantener"].set("ctrl+shift+space")  # igual que clavar
    assert not a.guardar()
    assert aplicadas == []
    assert errores and "repite la combinación" in errores[0]
    a._vars["atajos.mantener"].set("ctrl+win")
    a._vars["audio.buffer_previo_ms"].set(99999)
    assert not a.guardar()
    assert "buffer_previo_ms" in errores[-1]
    a.cerrar()


def test_lo_que_exige_reiniciar_se_dice(
    raiz: tk.Tk, configuracion: cfg.Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    avisos: list[str] = []
    monkeypatch.setattr(mod.messagebox, "showinfo", lambda t, m, **k: avisos.append(m))
    a, _ = _panel(raiz, configuracion, pendientes=["cuantización del modelo local"])
    a._vars["motor.local.cuantizacion"].set("fp32")
    assert a.guardar()
    assert "reiniciar" in a.estado.get() and "cuantización" in avisos[0]
    a.cerrar()


def test_probar_clave_llama_al_callback_sin_revelarla(
    raiz: tk.Tk, configuracion: cfg.Config
) -> None:
    llamadas: list[tuple[str, str]] = []

    def probar(base_url: str, clave: str) -> tuple[bool, str]:
        llamadas.append((base_url, clave))
        return False, "La clave no es válida"

    a, _ = _panel(raiz, configuracion, probar_clave=probar)
    a._vars["motor.api.clave"].set("gsk_de_prueba")
    a._probar()
    _bombear(raiz, 0.3)
    assert llamadas == [("https://api.groq.com/openai/v1", "gsk_de_prueba")]
    assert a._resultado_clave.get() == "✗ La clave no es válida"
    a.cerrar()


def test_auto_enter_lleva_advertencia_visible(raiz: tk.Tk, configuracion: cfg.Config) -> None:
    a, _ = _panel(raiz, configuracion)
    assert "no se deshace" in a.advertencia_auto_enter.cget("text")
    assert a.advertencia_auto_enter.winfo_manager() == "grid"
    assert a._vars["destino.app_activa.auto_enter"].get() is False  # desactivado por defecto
    a.cerrar()


def test_acerca_de_con_atribucion_y_medidor_en_vivo(raiz: tk.Tk, configuracion: cfg.Config) -> None:
    nivel = {"v": 0.0}
    a, _ = _panel(
        raiz,
        configuracion,
        nivel_actual=lambda: nivel["v"],
        dispositivos=lambda: [(1, "Micro USB")],
    )
    assert a.notebook is not None
    acerca = a.notebook.nametowidget(a.notebook.tabs()[-1])
    textos = " ".join(str(w.cget("text")) for w in acerca.winfo_children())
    assert "NVIDIA" in textos and "CC-BY-4.0" in textos and "MIT" in textos
    assert a._medidor is not None
    nivel["v"] = 0.5
    _bombear(raiz, 0.15)
    assert a._medidor.coords("barra")[2] == 130
    a.cerrar()


def test_sustituciones_mal_escritas() -> None:
    with pytest.raises(ConfigInvalida, match="línea 2"):
        mod._leer_sustituciones("a = b\nsin igual\n")
    assert mod._leer_sustituciones(" x  =  y\\n ") == {"x": "y\n"}


def test_abrir_dos_veces_no_duplica(raiz: tk.Tk, configuracion: cfg.Config) -> None:
    a, _ = _panel(raiz, configuracion)
    ventana = a._ventana
    a.abrir()
    assert a._ventana is ventana
    a.cerrar()
    assert a._ventana is None
