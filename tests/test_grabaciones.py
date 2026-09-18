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


def test_agrupar_automatico_por_eigengap() -> None:
    huellas, duraciones = _huellas([(0, 12), (1, 12), (2, 12)])
    etiquetas = agrupar(huellas, duraciones, hablantes=None)
    assert len(set(etiquetas)) == 3
    assert len({etiquetas[0], etiquetas[12], etiquetas[24]}) == 3
    # Una sola voz con ruido: un grupo, no ocho.
    huellas, duraciones = _huellas([(0, 30)])
    assert len(set(agrupar(huellas, duraciones, hablantes=None))) == 1
    # Una que habla mucho y otra poco (lo que rompía el enlace medio).
    huellas, duraciones = _huellas([(0, 60), (1, 10)])
    etiquetas = agrupar(huellas, duraciones, hablantes=None)
    assert len(set(etiquetas)) == 2 and len(set(etiquetas[60:])) == 1


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
    audio, _avisos = archivos.decodificar(wav)
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
    audio, _avisos = archivos.decodificar(m4a)
    assert 1.8 < audio.duracion_s < 2.3


def _escribir_video(
    ruta: Path, segundos: float = 2.0, sr: int = 44100, pistas: int = 1, con_audio: bool = True
) -> Path:
    """Un vídeo de verdad, con imagen y `pistas` pistas de audio. Como el de un móvil."""
    import av

    t = np.arange(int(sr * segundos)) / sr
    voz = (0.3 * np.sin(2 * np.pi * 300 * t)).astype(np.float32)
    with av.open(str(ruta), "w") as contenedor:
        video = contenedor.add_stream("libx264", rate=10)
        video.width, video.height, video.pix_fmt = 160, 120, "yuv420p"
        flujos = []
        for _ in range(pistas if con_audio else 0):
            a = contenedor.add_stream("aac", rate=sr)
            a.layout = "mono"
            flujos.append(a)
        for n in range(int(segundos * 10)):
            imagen = np.full((120, 160, 3), (n * 5) % 255, dtype=np.uint8)
            for paquete in video.encode(av.VideoFrame.from_ndarray(imagen, format="rgb24")):
                contenedor.mux(paquete)
        for paquete in video.encode(None):
            contenedor.mux(paquete)
        for i, a in enumerate(flujos):
            datos = voz if i == 0 else np.zeros_like(voz)
            for inicio in range(0, len(datos), 1024):
                bloque = datos[inicio : inicio + 1024].reshape(1, -1)
                if not bloque.shape[1]:
                    continue
                cuadro = av.AudioFrame.from_ndarray(bloque, format="flt", layout="mono")
                cuadro.sample_rate = sr
                for paquete in a.encode(cuadro):
                    contenedor.mux(paquete)
            for paquete in a.encode(None):
                contenedor.mux(paquete)
    return ruta


def test_decodificar_un_video_saca_su_audio(tmp_path: Path) -> None:
    """Lo que pidió el cliente: un vídeo entra igual que una grabación (VOZ-77)."""
    mp4 = _escribir_video(tmp_path / "reunion.mp4", segundos=2.0)
    audio, _avisos = archivos.decodificar(mp4)
    assert audio.sr == SR and audio.muestras.dtype == np.float32
    assert 1.8 < audio.duracion_s < 2.3
    assert float(np.abs(audio.muestras).max()) > 0.05  # trae sonido, no silencio


def test_un_video_sin_sonido_lo_dice_claro(tmp_path: Path) -> None:
    mudo = _escribir_video(tmp_path / "mudo.mp4", segundos=1.0, con_audio=False)
    with pytest.raises(archivos.ArchivoNoLegible, match="sonido|audio"):
        archivos.decodificar(mudo)


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


def test_preguntar_hablantes_se_ve_con_la_raiz_retirada() -> None:
    """La raíz de Voziris está retirada; el diálogo tiene que verse igual (VOZ-72)."""
    import tkinter as tk

    from voziris.ui import transcripcion

    try:
        raiz = tk.Tk()
    except tk.TclError as e:
        pytest.skip(f"sin Tk: {e}")
    raiz.withdraw()
    visto: list[bool] = []

    def comprobar_y_aceptar() -> None:
        dialogos = [w for w in raiz.winfo_children() if isinstance(w, tk.Toplevel)]
        assert dialogos, "no se creó el diálogo"
        v = dialogos[0]
        visto.append(bool(v.winfo_viewable()))
        v.event_generate("<Return>")

    raiz.after(300, comprobar_y_aceptar)
    eleccion = transcripcion.preguntar_hablantes(raiz, "nota.m4a")
    raiz.destroy()
    assert visto == [True]
    assert eleccion == "auto"


# --- vídeos con varias pistas de audio (VOZ-77) ---------------------------------------


def _pista(n: int, titulo=None, idioma=None, canales=1, default=False, secundaria=False):
    from voziris.archivos import Pista

    return Pista(n=n, titulo=titulo, idioma=idioma, canales=canales,
                 predeterminada=default, secundaria=secundaria)


def test_elegir_pista_cuando_hay_doblaje() -> None:
    """Varios idiomas: manda el idioma que se va a transcribir, no el orden ni la marca."""
    from voziris.archivos import elegir_pista

    pistas = [
        _pista(0, "English", "eng", 2, default=True),
        _pista(1, "Español", "spa", 2),
        _pista(2, "Français", "fra", 2),
    ]
    elegida, motivo = elegir_pista(pistas, "es")
    assert elegida.n == 1 and "spa" in motivo
    # Sin idioma que case, se cae a la marcada como predeterminada.
    elegida, motivo = elegir_pista(pistas, "pl")
    assert elegida.n == 0 and "predeterminada" in motivo


def test_elegir_pista_descarta_comentarios_y_audiodescripcion() -> None:
    from voziris.archivos import elegir_pista

    pistas = [
        _pista(0, "Película", "spa", 2),
        _pista(1, "Comentario del director", "spa", 2, secundaria=True),
    ]
    elegida, motivo = elegir_pista(pistas, "es")
    assert elegida.n == 0 and motivo == "única"


def test_elegir_pista_sin_nada_que_la_distinga() -> None:
    from voziris.archivos import elegir_pista

    # OBS: micrófono y sonido del escritorio, sin marcas ni idiomas.
    pistas = [_pista(0, "Mic/Aux", canales=1), _pista(1, "Desktop Audio", canales=2)]
    elegida, motivo = elegir_pista(pistas, "es")
    assert elegida.n == 0 and motivo == "es la primera"
    # Una pista sin canales no cuenta.
    elegida, _ = elegir_pista([_pista(0, canales=0), _pista(1, "buena", canales=2)], "es")
    assert elegida.n == 1


def test_aviso_de_pistas_dice_cual_y_como_cambiarla() -> None:
    from voziris.archivos import aviso_de_pistas

    pistas = [_pista(0, "Español", "spa"), _pista(1, "English", "eng")]
    aviso = aviso_de_pistas(pistas, pistas[0], es_video=True)
    assert aviso is not None
    assert "2 pistas" in aviso and "pista 1" in aviso
    assert "**1: Español · spa**" in aviso and "2: English · eng" in aviso
    assert "--pista" in aviso
    assert aviso_de_pistas(pistas[:1], pistas[0], es_video=True) is None


def test_video_con_dos_pistas_avisa_y_se_puede_elegir(tmp_path: Path) -> None:
    """De punta a punta con un vídeo de dos pistas hecho aquí mismo."""
    mp4 = _escribir_video(tmp_path / "dos.mp4", segundos=1.5, pistas=2)
    disponibles = archivos.pistas(mp4)
    assert len(disponibles) == 2

    audio, avisos = archivos.decodificar(mp4, idioma="es")
    assert audio.duracion_s > 1.0
    assert len(avisos) == 1 and "2 pistas" in avisos[0]

    # La segunda pista de _escribir_video está en silencio: se nota al pedirla.
    silencio, _ = archivos.decodificar(mp4, pista=2)
    assert float(np.abs(silencio.muestras).max()) < 0.01
    voz, _ = archivos.decodificar(mp4, pista=1)
    assert float(np.abs(voz.muestras).max()) > 0.05

    # Sumarlas avisa de que puede salir entremezclado.
    _todas, avisos_todas = archivos.decodificar(mp4, pista="todas")
    assert any("sumado" in a for a in avisos_todas)

    with pytest.raises(archivos.ArchivoNoLegible, match="no tiene la pista 7"):
        archivos.decodificar(mp4, pista=7)


def test_formatos_incluyen_los_videos_que_trae_la_gente() -> None:
    for ext in (".mp4", ".mov", ".mkv", ".avi", ".webm", ".wmv", ".mts"):
        assert ext in archivos.FORMATOS_VIDEO, ext
    assert ".m4a" in archivos.FORMATOS_AUDIO
    assert set(archivos.FORMATOS) == set(archivos.FORMATOS_AUDIO) | set(archivos.FORMATOS_VIDEO)


def test_lo_que_se_dice_al_empezar_segun_lo_que_dure() -> None:
    from voziris.grabaciones import _cuanto_queda

    assert _cuanto_queda(95.0) == "1:35 de audio. Transcribiendo…"
    largo = _cuanto_queda(3.31 * 3600)
    assert largo.startswith("3:18:") and "tarda unos 66 minutos" in largo
    assert "Puedes seguir a lo tuyo" in largo


def test_si_no_hay_memoria_no_se_separan_hablantes() -> None:
    """Más vale decirlo antes que pelearse media hora con un vídeo y morir a medias."""
    from voziris.grabaciones import caben_los_hablantes

    media_hora = Audio(muestras=np.zeros(30 * 60 * SR, dtype=np.float32))
    tres_horas = Audio(muestras=np.zeros(int(3.3 * 3600 * SR), dtype=np.float32))
    assert caben_los_hablantes(media_hora, libre_mb=8000)
    assert not caben_los_hablantes(tres_horas, libre_mb=8000)
    assert caben_los_hablantes(tres_horas, libre_mb=14000)
    assert caben_los_hablantes(tres_horas, libre_mb=None)  # sin dato, se intenta


def test_un_video_largo_se_transcribe_sin_hablantes_en_vez_de_morir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from voziris import grabaciones, winapi

    monkeypatch.setattr(winapi, "memoria_libre_mb", lambda: 300.0)  # equipo sin memoria
    wav = tmp_path / "larga.wav"
    _escribir_wav(wav, 3.0)

    class SeparadorQueNoDeberiaUsarse(hablantes.SeparadorHablantes):
        def __init__(self) -> None:
            super().__init__(Path("no-importa"))

        def disponible(self) -> bool:
            return True

        def separar(self, audio: Audio, hablantes_: int | None = None) -> list[Intervencion]:
            raise AssertionError("no debería intentar separar sin memoria")

    r = grabaciones.transcribir_archivo(
        wav, MotorFalso(), "es", hablantes="auto", separador=SeparadorQueNoDeberiaUsarse()
    )
    assert r.hablantes == 1
    assert any("sin separar a los hablantes" in a for a in r.avisos)
    assert any("GB" in a for a in r.avisos)
