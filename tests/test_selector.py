"""Tests del selector de motor y del respaldo (VOZ-31). Todo con motores falsos."""

from __future__ import annotations

import time

import numpy as np
import pytest

from voziris.errores import MotorNoDisponible, TranscripcionFallida
from voziris.motores.selector import Selector
from voziris.tipos import SAMPLE_RATE, Audio, Transcripcion

AUDIO = Audio(muestras=np.zeros(SAMPLE_RATE, dtype=np.float32))


class MotorFalso:
    requiere_red = False

    def __init__(self, nombre: str, disponible: bool = True) -> None:
        self.nombre = nombre
        self._disponible = disponible
        self.precalentado = 0
        self.llamadas = 0
        self.falla: Exception | None = None
        self.tarda_s = 0.0

    def precalentar(self) -> None:
        time.sleep(self.tarda_s)
        self.precalentado += 1

    def disponible(self) -> bool:
        return self._disponible

    def transcribir(self, audio: Audio, idioma: str) -> Transcripcion:
        self.llamadas += 1
        if self.falla is not None:
            raise self.falla
        return Transcripcion(f"texto de {self.nombre}", idioma, self.nombre, 10, audio.duracion_s)


def _par(
    api_disponible: bool = True, local_disponible: bool = True
) -> tuple[MotorFalso, MotorFalso]:
    return MotorFalso("local", local_disponible), MotorFalso("api:groq", api_disponible)


def test_local_siempre_local_y_nunca_toca_la_api() -> None:
    local, api = _par()
    s = Selector("local", local, api)
    s.precalentar()
    assert local.precalentado == 1 and api.precalentado == 0
    assert s.transcribir(AUDIO, "es").motor == "local"
    assert api.llamadas == 0
    # El local falla: error visible, NUNCA respaldo hacia la API.
    local.falla = MotorNoDisponible("modelo no cargado")
    with pytest.raises(MotorNoDisponible, match="modelo no cargado"):
        s.transcribir(AUDIO, "es")
    assert api.llamadas == 0


def test_api_usa_la_api_y_cae_al_local_sin_red() -> None:
    local, api = _par(api_disponible=True)
    s = Selector("api", local, api)
    s.precalentar()
    assert local.precalentado == 1 and api.precalentado == 1
    t = s.transcribir(AUDIO, "es")
    assert t.motor == "api:groq" and t.avisos == []
    api._disponible = False
    t = s.transcribir(AUDIO, "es")
    assert t.motor == "local" and t.avisos == ["Sin conexión con la API: transcrito en local"]
    assert api.llamadas == 1


def test_api_que_falla_a_media_peticion_cae_al_local_con_aviso() -> None:
    local, api = _par()
    api.falla = MotorNoDisponible("la API no respondió en 15 s")
    s = Selector("api", local, api)
    t = s.transcribir(AUDIO, "es")
    assert t.motor == "local"
    assert t.avisos == ["La API falló (la API no respondió en 15 s): transcrito en local"]


def test_auto_prefiere_la_api_si_esta_disponible() -> None:
    local, api = _par()
    s = Selector("auto", local, api)
    assert s.transcribir(AUDIO, "es").motor == "api:groq"
    api._disponible = False
    assert s.transcribir(AUDIO, "es").motor == "local"


def test_sin_motor_de_api_configurado() -> None:
    local = MotorFalso("local")
    s = Selector("auto", local, None)
    s.precalentar()
    assert s.transcribir(AUDIO, "es").motor == "local"
    assert s.disponible()


def test_ninguno_disponible() -> None:
    local, api = _par(api_disponible=False, local_disponible=False)
    s = Selector("auto", local, api)
    assert not s.disponible()
    with pytest.raises(MotorNoDisponible, match="ni la API ni el motor local"):
        s.transcribir(AUDIO, "es")


def test_transcripcion_fallida_de_la_api_no_se_reintenta_en_local() -> None:
    """Silencio es silencio: no vale la pena mandarlo también al modelo local."""
    local, api = _par()
    api.falla = TranscripcionFallida("no se oyó nada")
    with pytest.raises(TranscripcionFallida):
        Selector("api", local, api).transcribir(AUDIO, "es")
    assert local.llamadas == 0


def test_cambio_en_caliente_vale_para_el_dictado_siguiente() -> None:
    local, api = _par()
    s = Selector("local", local, api)
    s.precalentar()
    assert api.precalentado == 0
    s.preferencia = "api"
    time.sleep(0.1)  # el precalentado de la API va en su hilo
    assert api.precalentado == 1
    assert s.transcribir(AUDIO, "es").motor == "api:groq"
    s.preferencia = "local"
    assert s.transcribir(AUDIO, "es").motor == "local"
    s.preferencia = "auto"
    time.sleep(0.1)
    assert api.precalentado == 1  # no se precalienta dos veces
    with pytest.raises(ValueError):
        s.preferencia = "nube"
    with pytest.raises(ValueError):
        Selector("nube", local, api)


def test_disponible_segun_la_preferencia() -> None:
    local, api = _par(api_disponible=True, local_disponible=False)
    assert not Selector("local", local, api).disponible()
    assert Selector("api", local, api).disponible()
    assert Selector("auto", local, api).disponible()


def test_precalienta_en_paralelo_y_espera_a_los_dos() -> None:
    local, api = _par()
    local.tarda_s = api.tarda_s = 0.2
    s = Selector("auto", local, api)
    t0 = time.perf_counter()
    s.precalentar()
    assert time.perf_counter() - t0 < 0.35  # en paralelo, no 0,4 s
    assert local.precalentado == 1 and api.precalentado == 1
