"""Tests de los tipos base. Sin dependencias de Windows: corren en CI."""

from __future__ import annotations

import numpy as np

from voziris.tipos import SAMPLE_RATE, Audio, Nivel, Transcripcion


def test_duracion_de_audio() -> None:
    audio = Audio(muestras=np.zeros(SAMPLE_RATE * 3, dtype=np.float32))
    assert audio.duracion_s == 3.0


def test_rtf() -> None:
    t = Transcripcion(
        texto="hola", idioma="es", motor="local", ms_proceso=500, duracion_audio_s=15.0
    )
    assert abs(t.rtf - 0.0333) < 0.001


def test_rtf_no_divide_por_cero() -> None:
    t = Transcripcion(texto="", idioma="es", motor="local", ms_proceso=10, duracion_audio_s=0.0)
    assert t.rtf == 0.0


def test_los_niveles_son_los_tres_acordados() -> None:
    assert {n.value for n in Nivel} == {"literal", "limpio", "reescritura"}
