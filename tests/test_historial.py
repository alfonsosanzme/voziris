"""Tests del historial con reintento (VOZ-52). Archivos en tmp_path, destinos falsos."""

from __future__ import annotations

import json
import wave
from datetime import datetime
from pathlib import Path

import numpy as np

from voziris.errores import EntregaFallida
from voziris.historial import Historial
from voziris.tipos import SAMPLE_RATE, Audio, Contexto, EntradaHistorial, Entrega, Modo, Nivel


class DestinoFalso:
    nombre = "app_activa"

    def __init__(self) -> None:
        self.entregas: list[str] = []
        self.falla = False

    def entregar(self, texto: str, ctx: Contexto) -> Entrega:
        if self.falla:
            raise EntregaFallida("ventana elevada")
        self.entregas.append(texto)
        return Entrega(ok=True, detalle="pegado en notepad.exe")


def _entrada(texto: str, entregado: bool = True, destino: str = "app_activa") -> EntradaHistorial:
    return EntradaHistorial(
        momento=datetime(2026, 9, 4, 19, 42, 5), texto=texto, motor="local", destino=destino,
        entregado=entregado, ms_total=900, duracion_audio_s=4.0,
    )


def _ctx(destino: str) -> Contexto:
    return Contexto(destino, Modo.MANTENER, None, False, Nivel.LITERAL)


def _historial(tmp_path: Path, **kwargs: object) -> tuple[Historial, DestinoFalso]:
    destino = DestinoFalso()
    h = Historial(
        tmp_path / "historial" / "dictados.jsonl", destinos={"app_activa": destino},
        contexto=_ctx, **kwargs,  # type: ignore[arg-type]
    )
    return h, destino


def test_jsonl_junto_al_ejecutable_con_indices(tmp_path: Path) -> None:
    h, _ = _historial(tmp_path)
    h.registrar(_entrada("uno"))
    h.registrar(_entrada("dos"))
    lineas = h.ruta.read_text(encoding="utf-8").splitlines()
    assert len(lineas) == 2
    primera = json.loads(lineas[0])
    assert primera["texto"] == "uno" and primera["indice"] == 1
    assert primera["momento"] == "2026-09-04T19:42:05" and primera["audio"] is None
    assert [e.texto for e in h.ultimas()] == ["dos", "uno"]  # la más nueva primero
    assert h.ultimas(1)[0].indice == 2


def test_recorte_a_entradas(tmp_path: Path) -> None:
    h, _ = _historial(tmp_path, maximo=5)
    for n in range(12):
        h.registrar(_entrada(f"d{n}"))
    todas = h.todas()
    assert len(todas) <= 10 and todas[-1].texto == "d11"
    assert len(h.ultimas(10)) <= 10
    assert h.ultimas(1)[0].indice == 12  # los índices no se reutilizan


def test_archivo_corrupto_no_impide_arrancar(tmp_path: Path) -> None:
    h, _ = _historial(tmp_path)
    h.registrar(_entrada("buena"))
    with open(h.ruta, "a", encoding="utf-8") as f:
        f.write('{"texto": "a medias", "mom')  # corte de luz
        f.write("\n\nno es json\n")
    h.registrar(_entrada("después"))
    assert [e.texto for e in h.ultimas()] == ["después", "buena"]


def test_reintentar_reentrega_el_texto_procesado_sin_transcribir(tmp_path: Path) -> None:
    h, destino = _historial(tmp_path)
    h.registrar(_entrada("Texto ya limpio.", entregado=False))
    indice = h.ultimas(1)[0].indice
    entrega = h.reintentar(indice)
    assert entrega.ok and entrega.detalle == "pegado en notepad.exe"
    assert destino.entregas == ["Texto ya limpio."]
    assert h.ultimas(1)[0].entregado is True  # queda marcada como entregada


def test_reintentar_fallido_o_inexistente(tmp_path: Path) -> None:
    h, destino = _historial(tmp_path)
    h.registrar(_entrada("hola", entregado=False))
    destino.falla = True
    entrega = h.reintentar(1)
    assert not entrega.ok and "elevada" in entrega.detalle
    assert h.ultimas(1)[0].entregado is False
    assert not h.reintentar(99).ok
    h.registrar(_entrada("md", destino="markdown"))
    assert "markdown" in h.reintentar(2).detalle  # sin ese destino configurado


def test_borrar_dictado_sensible(tmp_path: Path) -> None:
    h, _ = _historial(tmp_path)
    h.registrar(_entrada("normal"))
    h.registrar(_entrada("mi contraseña es patata"))
    assert h.borrar(2)
    assert [e.texto for e in h.todas()] == ["normal"]
    assert "patata" not in h.ruta.read_text(encoding="utf-8")
    assert not h.borrar(2)


def test_guardar_audio_deja_el_wav_y_se_borra_con_la_entrada(tmp_path: Path) -> None:
    h, _ = _historial(tmp_path, guardar_audio=True)
    audio = Audio(muestras=np.full(SAMPLE_RATE, 0.25, dtype=np.float32))
    h.registrar(_entrada("con audio"), audio)
    entrada = h.ultimas(1)[0]
    assert entrada.audio == "000001.wav"
    wav = h.ruta.parent / "audio" / "000001.wav"
    with wave.open(str(wav)) as w:
        assert (w.getnchannels(), w.getframerate(), w.getnframes()) == (1, SAMPLE_RATE, SAMPLE_RATE)
    h.borrar(1)
    assert not wav.exists()


def test_por_defecto_no_se_guarda_audio(tmp_path: Path) -> None:
    h, _ = _historial(tmp_path)
    h.registrar(_entrada("sin audio"), Audio(muestras=np.zeros(100, dtype=np.float32)))
    assert h.ultimas(1)[0].audio is None
    assert not (h.ruta.parent / "audio").exists()


def test_nada_registrado(tmp_path: Path) -> None:
    h, _ = _historial(tmp_path)
    assert h.ultimas() == [] and h.buscar(1) is None
