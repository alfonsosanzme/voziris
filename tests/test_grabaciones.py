"""Tests de la transcripción de grabaciones (VOZ-70/71): troceado, hablantes, Markdown, PyAV."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from voziris import archivos, grabaciones, hablantes
from voziris.errores import TranscripcionFallida
from voziris.grabaciones import Linea, Resultado, render_markdown, trocear
from voziris.hablantes import (
    Intervencion,
    agrupar,
    fundir,
    heredar_etiquetas,
    renumerar,
    trocear_en_piezas,
)
from voziris.tipos import SAMPLE_RATE, Audio, Transcripcion

SR = SAMPLE_RATE


class MotorFalso:
    """Devuelve la duración del trozo como texto: así se ve qué recibió."""

    nombre = "falso"
    requiere_red = False

    def __init__(self) -> None:
        self.trozos: list[float] = []

    def precalentar(self) -> None: ...

    def disponible(self) -> bool:
        return True

    def transcribir(self, audio: Audio, idioma: str) -> Transcripcion:
        self.trozos.append(round(audio.duracion_s, 2))
        if audio.duracion_s < 0.5:
            raise TranscripcionFallida("no se oyó nada")
        return Transcripcion(
            f"trozo de {audio.duracion_s:.1f} s", idioma, "falso", 7, audio.duracion_s
        )


# --- troceado -----------------------------------------------------------------------


def test_trocear_corta_en_el_punto_mas_silencioso() -> None:
    # 70 s de ruido con un silencio total entre los segundos 27 y 28.
    rng = np.random.default_rng(1)
    muestras = (rng.standard_normal(70 * SR) * 0.2).astype(np.float32)
    muestras[27 * SR : 28 * SR] = 0.0
    muestras[55 * SR : 56 * SR] = 0.0
    trozos = trocear(muestras, SR, ventana=30.0)
    assert len(trozos) == 3
    assert trozos[0][0] == 0 and trozos[-1][1] == len(muestras)
    assert 27 * SR <= trozos[0][1] <= 28 * SR  # cayó en el silencio, no en los 30 s exactos
    assert all(b - a <= 30 * SR for a, b in trozos)
    assert all(trozos[i][1] == trozos[i + 1][0] for i in range(len(trozos) - 1))


def test_trocear_audio_corto_o_vacio() -> None:
    assert trocear(np.zeros(SR * 10, np.float32), SR) == [(0, SR * 10)]
    assert trocear(np.zeros(0, np.float32), SR) == []


# --- una voz -----------------------------------------------------------------------------


def test_una_voz_trocea_y_junta() -> None:
    motor = MotorFalso()
    audio = Audio(
        muestras=(np.random.default_rng(2).standard_normal(75 * SR) * 0.1).astype(np.float32)
    )
    r = grabaciones.transcribir_una_voz(audio, motor, "es")
    assert len(motor.trozos) == 3 and max(motor.trozos) <= 30.0
    assert len(r.lineas) == 3 and all(ln.hablante is None for ln in r.lineas)
    assert r.hablantes == 1 and r.motor == "falso" and r.ms_proceso == 21
    md = r.markdown("nota", datetime(2026, 9, 11, 10, 0))
    assert md.startswith("# nota\n\nTranscrito el 2026-09-11 10:00 · duración 1:15 · motor falso\n")
    assert "Hablante" not in md


# --- posproceso de hablantes ---------------------------------------------------------------


def _huellas(voces: list[tuple[int, int]], semilla: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """Huellas sintéticas: (voz, cuántas) → vectores unitarios cerca del centro de esa voz."""
    rng = np.random.default_rng(semilla)
    centros = rng.standard_normal((8, 32))
    filas, duraciones = [], []
    for voz, cuantas in voces:
        for _ in range(cuantas):
            v = centros[voz] + 0.35 * rng.standard_normal(32)
            filas.append(v / np.linalg.norm(v))
            duraciones.append(4.0)
    return np.stack(filas), np.array(duraciones)


def test_agrupar_con_numero_conocido_ignora_los_restos() -> None:
    # Dos voces de verdad y un ruido suelto que está lejos de las dos.
    huellas, duraciones = _huellas([(0, 20), (1, 15), (5, 1)])
    etiquetas = agrupar(huellas, duraciones, hablantes=2)
    assert len(set(etiquetas)) == 2
    assert len(set(etiquetas[:20])) == 1 and len(set(etiquetas[20:35])) == 1
    assert etiquetas[0] != etiquetas[20]
    assert etiquetas[35] in (etiquetas[0], etiquetas[20])  # el ruido se reparte, no es hablante


def test_agrupar_automatico_por_umbral() -> None:
    huellas, duraciones = _huellas([(0, 12), (1, 12), (2, 12)])
    etiquetas = agrupar(huellas, duraciones, hablantes=None, umbral=0.5)
    assert len(set(etiquetas)) == 3
    # Con un umbral imposible de superar, todo es una voz.
    assert len(set(agrupar(huellas, duraciones, None, umbral=5.0))) == 1


def test_agrupar_casos_borde() -> None:
    assert agrupar(np.zeros((0, 4)), np.zeros(0)) == []
    assert agrupar(np.ones((1, 4)) / 2, np.ones(1), hablantes=3) == [0]
    # Si se pide un número, se respeta aunque sea una sola voz: es decisión del usuario.
    huellas, duraciones = _huellas([(0, 5)])
    assert len(set(agrupar(huellas, duraciones, hablantes=4))) == 4
    assert len(set(agrupar(huellas, duraciones, hablantes=9))) == 5  # no más que piezas


def test_trocear_en_piezas_y_heredar() -> None:
    piezas = trocear_en_piezas([(0.0, 13.0), (20.0, 21.0)], maximo=6.0)
    assert len(piezas) == 4 and piezas[0] == (0.0, 13.0 / 3) and piezas[-1] == (20.0, 21.0)
    assert heredar_etiquetas(5, {1: 7, 3: 2}) == [7, 7, 7, 2, 2]
    assert heredar_etiquetas(2, {}) == [0, 0]


def test_renumerar_por_orden_de_aparicion() -> None:
    assert renumerar([(0, 1, 5), (1, 2, 2), (2, 3, 5), (3, 4, 9)]) == [
        (0, 1, 0),
        (1, 2, 1),
        (2, 3, 0),
        (3, 4, 2),
    ]


def test_fundir_segmentos_del_mismo_hablante() -> None:
    inter = fundir([(0.0, 2.0, 0), (2.5, 4.0, 0), (6.0, 7.0, 0), (7.2, 9.0, 1)])
    assert inter == [
        Intervencion(0.0, 4.0, 0),
        Intervencion(6.0, 7.0, 0),
        Intervencion(7.2, 9.0, 1),
    ]


# --- varias voces con separador falso -------------------------------------------------------


class SeparadorFalso(hablantes.SeparadorHablantes):
    def __init__(self) -> None:
        super().__init__(Path("no-importa"))
        self.pedido: int | None = None

    def disponible(self) -> bool:
        return True

    def separar(self, audio: Audio, hablantes_: int | None = None) -> list[Intervencion]:
        self.pedido = hablantes_
        return [Intervencion(0.0, 5.0, 0), Intervencion(5.0, 45.0, 1), Intervencion(45.0, 45.2, 0)]


def test_varias_voces_transcribe_por_intervencion_y_trocea_las_largas() -> None:
    motor, sep = MotorFalso(), SeparadorFalso()
    audio = Audio(
        muestras=(np.random.default_rng(3).standard_normal(46 * SR) * 0.1).astype(np.float32)
    )
    r = grabaciones.transcribir_varias_voces(audio, motor, sep, "es", hablantes=2)
    assert sep.pedido == 2
    assert [ln.hablante for ln in r.lineas] == [0, 1]  # la de 0,2 s se descarta
    assert len(motor.trozos) == 3  # 5 s + 40 s partida en dos
    assert r.hablantes == 2
    md = r.markdown("reunion", datetime(2026, 9, 11, 10, 0))
    assert "2 hablantes" in md
    assert "**Hablante 1** (00:00) trozo de 5.0 s" in md
    assert "**Hablante 2** (00:05) trozo de" in md


def test_transcribir_archivo_sin_separador_avisa(tmp_path: Path) -> None:
    wav = tmp_path / "nota.wav"
    _escribir_wav(wav, 3.0)
    r = grabaciones.transcribir_archivo(wav, MotorFalso(), "es", hablantes="auto", separador=None)
    assert r.hablantes == 1 and any("Sin separación" in a for a in r.avisos)
    destino = grabaciones.guardar(r, wav)
    assert destino == tmp_path / "nota.md" and destino.read_text(encoding="utf-8").startswith(
        "# nota"
    )
    assert grabaciones.ruta_de_salida(wav) == tmp_path / "nota (2).md"


# --- decodificación ---------------------------------------------------------------------------


def _escribir_wav(ruta: Path, segundos: float, sr: int = 48000) -> None:
    import soundfile as sf

    t = np.arange(int(sr * segundos)) / sr
    estereo = np.stack(
        [0.3 * np.sin(2 * np.pi * 440 * t), 0.3 * np.sin(2 * np.pi * 660 * t)], axis=1
    )
    sf.write(str(ruta), estereo.astype(np.float32), sr)


def test_decodificar_remuestrea_y_mezcla(tmp_path: Path) -> None:
    wav = tmp_path / "estereo48.wav"
    _escribir_wav(wav, 2.0)
    audio = archivos.decodificar(wav)
    assert audio.sr == SR and audio.muestras.dtype == np.float32
    assert abs(audio.duracion_s - 2.0) < 0.05
    assert 0.1 < float(np.abs(audio.muestras).max()) < 0.5
    assert archivos.duracion_s(wav) == pytest.approx(2.0, abs=0.05)


def test_decodificar_m4a_generado_con_pyav(tmp_path: Path) -> None:
    import av

    m4a = tmp_path / "nota.m4a"
    with av.open(str(m4a), "w") as contenedor:
        flujo = contenedor.add_stream("aac", rate=44100)
        flujo.layout = "mono"
        t = np.arange(44100 * 2) / 44100
        senal = (0.3 * np.sin(2 * np.pi * 300 * t)).astype(np.float32).reshape(1, -1)
        cuadro = av.AudioFrame.from_ndarray(senal, format="flt", layout="mono")
        cuadro.sample_rate = 44100
        for paquete in flujo.encode(cuadro):
            contenedor.mux(paquete)
        for paquete in flujo.encode(None):
            contenedor.mux(paquete)
    audio = archivos.decodificar(m4a)
    assert 1.8 < audio.duracion_s < 2.3


def test_decodificar_errores(tmp_path: Path) -> None:
    with pytest.raises(archivos.ArchivoNoLegible, match="No existe"):
        archivos.decodificar(tmp_path / "nada.m4a")
    roto = tmp_path / "roto.m4a"
    roto.write_bytes(b"esto no es audio")
    with pytest.raises(archivos.ArchivoNoLegible):
        archivos.decodificar(roto)


def test_render_markdown_completo() -> None:
    r = Resultado(
        [Linea(0.0, 0, "Hola."), Linea(65.0, 1, "Qué tal.")], 130.0, 2, "local", 500, ["un aviso"]
    )
    md = render_markdown(r, "llamada", datetime(2026, 9, 11, 12, 0))
    assert md == (
        "# llamada\n\nTranscrito el 2026-09-11 12:00 · duración 2:10 · 2 hablantes · motor local\n"
        "> un aviso\n\n**Hablante 1** (00:00) Hola.\n\n**Hablante 2** (01:05) Qué tal.\n"
    )


# --- interfaz y línea de comandos ------------------------------------------------------------


def test_hablantes_validos() -> None:
    from voziris.__main__ import _hablantes_validos

    assert all(_hablantes_validos(v) for v in ("auto", "1", "2", "50"))
    assert not any(_hablantes_validos(v) for v in ("0", "51", "dos", "", "-3"))


def test_ventana_de_progreso_ejecuta_y_cancela() -> None:
    import tkinter as tk

    from voziris.ui import transcripcion

    try:
        tk.Tk().destroy()
    except tk.TclError as e:
        pytest.skip(f"sin Tk: {e}")

    def trabajo(progreso: transcripcion.Progreso) -> str:
        progreso("Abriendo…", None)
        progreso("Transcribiendo…", 0.5)
        return "hecho"

    assert transcripcion.ejecutar_con_progreso("t", "nota.m4a", trabajo) == "hecho"

    def cancelado(progreso: transcripcion.Progreso) -> str:
        # La ventana se cierra desde el hilo de trabajo (como haría el usuario) y
        # el siguiente aviso de progreso tiene que lanzar.
        raise transcripcion.TranscripcionCancelada("cancelado por el usuario")

    with pytest.raises(transcripcion.TranscripcionCancelada):
        transcripcion.ejecutar_con_progreso("t", "nota.m4a", cancelado)

    def falla(progreso: transcripcion.Progreso) -> str:
        raise archivos.ArchivoNoLegible("roto")

    with pytest.raises(archivos.ArchivoNoLegible):
        transcripcion.ejecutar_con_progreso("t", "nota.m4a", falla)


def test_parece_relleno() -> None:
    from voziris.grabaciones import parece_relleno

    assert parece_relleno("Yeah.", "es") and parece_relleno("Mm-hmm.", "es")
    assert parece_relleno("Shit on T.", "es") and parece_relleno("I do have you.", "es")
    assert not parece_relleno("Soy educador", "es")
    assert not parece_relleno("Mucho trap.", "es")
    assert not parece_relleno("Sí.", "es")
    assert not parece_relleno("Recogida de nombre tal, tener sitio, datos.", "es")
    assert not parece_relleno("Yeah.", "en")  # solo hay lista para el español
