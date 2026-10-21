"""Tests del selector de motor y del respaldo (VOZ-31). Todo con motores falsos."""

from __future__ import annotations

import logging
import time

import numpy as np
import pytest

from voziris.errores import LimiteDeTasa, MotorNoDisponible, TranscripcionFallida
from voziris.motores.selector import MOTIVO_LIMITE, Selector
from voziris.tipos import SAMPLE_RATE, Audio, Transcripcion

AUDIO = Audio(muestras=np.zeros(SAMPLE_RATE, dtype=np.float32))
AVISO_LIMITE = f"{MOTIVO_LIMITE}: transcrito en local"


class MotorFalso:
    requiere_red = False

    def __init__(self, nombre: str, disponible: bool = True) -> None:
        self.nombre = nombre
        self._disponible = disponible
        self.precalentado = 0
        self.llamadas = 0
        self.falla: Exception | None = None
        self.tarda_s = 0.0
        self.tarda_transcribiendo_s = 0.0
        """Para el trozo que sube tan despacio que la paciencia se agota antes."""

    def precalentar(self) -> None:
        time.sleep(self.tarda_s)
        self.precalentado += 1

    def disponible(self) -> bool:
        return self._disponible

    def transcribir(self, audio: Audio, idioma: str) -> Transcripcion:
        self.llamadas += 1
        time.sleep(self.tarda_transcribiendo_s)
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


def test_el_aviso_dice_si_falta_la_clave() -> None:
    from voziris.motores.api import MotorAPI

    local = MotorFalso("local")
    sin_clave = MotorAPI("https://api.groq.com/openai/v1", "m", "")
    t = Selector("api", local, sin_clave).transcribir(AUDIO, "es")
    assert t.avisos == ["Sin clave de API: transcrito en local"]


# --- cortacircuitos del 429 (VOZ-78) ------------------------------------------------------


def test_dos_429_seguidos_cortan_y_dejan_de_llamar_a_la_api() -> None:
    """El caso del vídeo de 105 minutos: 210 trozos y 210 subidas tiradas."""
    local, api = _par()
    api.falla = LimiteDeTasa("la API respondió 429 (límite de peticiones): rate limit")
    s = Selector("api", local, api)

    # El primero puede ser mala suerte: se intenta igual y se cae al local.
    assert s.transcribir(AUDIO, "es").motor == "local"
    assert api.llamadas == 1
    # El segundo ya es el límite: se corta.
    t = s.transcribir(AUDIO, "es")
    assert t.motor == "local" and t.avisos == [AVISO_LIMITE]
    assert api.llamadas == 2
    # Y los trozos siguientes van directos al local, sin tocar la API.
    for _ in range(20):
        assert s.transcribir(AUDIO, "es").avisos == [AVISO_LIMITE]
    assert api.llamadas == 2


def test_un_429_con_retry_after_corta_al_primero() -> None:
    """Si el servidor dice cuánto esperar, no hay nada que adivinar."""
    local, api = _par()
    api.falla = LimiteDeTasa("429", espera_s=5.0)
    s = Selector("api", local, api)
    assert s.transcribir(AUDIO, "es").motor == "local"
    assert s.transcribir(AUDIO, "es").avisos == [AVISO_LIMITE]
    assert api.llamadas == 1


def test_el_corte_se_cierra_solo_y_se_vuelve_a_intentar() -> None:
    local, api = _par()
    api.falla = LimiteDeTasa("429", espera_s=0.05)
    s = Selector("api", local, api)
    s.transcribir(AUDIO, "es")
    assert api.llamadas == 1
    s.transcribir(AUDIO, "es")  # dentro del corte: ni se intenta
    assert api.llamadas == 1
    time.sleep(0.1)
    api.falla = None
    t = s.transcribir(AUDIO, "es")  # pasado el corte: la API vuelve
    assert t.motor == "api:groq" and t.avisos == [] and api.llamadas == 2


def test_un_acierto_borra_la_racha_de_429() -> None:
    local, api = _par()
    api.falla = LimiteDeTasa("429")
    s = Selector("api", local, api)
    s.transcribir(AUDIO, "es")  # racha: 1
    api.falla = None
    assert s.transcribir(AUDIO, "es").motor == "api:groq"  # racha a cero
    api.falla = LimiteDeTasa("429")
    s.transcribir(AUDIO, "es")  # vuelve a ser el primero: no corta
    assert api.llamadas == 3
    assert s.transcribir(AUDIO, "es").avisos == [AVISO_LIMITE]  # este sí
    assert api.llamadas == 4


def test_un_fallo_de_red_no_corta_nada() -> None:
    """Sin red o sin clave el reintento es barato y no se arregla esperando: no se corta."""
    local, api = _par()
    api.falla = MotorNoDisponible("sin conexión con la API: getaddrinfo failed")
    s = Selector("api", local, api)
    for _ in range(5):
        t = s.transcribir(AUDIO, "es")
        assert t.motor == "local" and t.avisos != [AVISO_LIMITE]
    assert api.llamadas == 5


def test_el_429_que_llega_tarde_tambien_corta() -> None:
    """El caso de verdad en grabaciones largas: subir el trozo tarda más que la paciencia.

    La petición se da por perdida antes de que conteste, pero su hilo sigue
    vivo y el 429 acaba llegando. Si se tirase, el corte no se abriría nunca.
    """
    local, api = _par()
    api.falla = LimiteDeTasa("429", espera_s=5.0)
    api.tarda_transcribiendo_s = 0.3
    s = Selector("api", local, api, paciencia_base_s=0.02)  # paciencia: 0,12 s

    t = s.transcribir(AUDIO, "es")
    assert t.motor == "local"
    assert t.avisos == ["La API falló (la API no contestó en 0 s): transcrito en local"]
    assert api.llamadas == 1

    time.sleep(0.4)  # lo que tarda el hilo huérfano en recibir su 429
    assert s.transcribir(AUDIO, "es").avisos == [AVISO_LIMITE]
    assert api.llamadas == 1  # el corte ya estaba abierto


def test_el_corte_se_dice_una_vez_en_el_log_no_una_por_trozo(
    caplog: pytest.LogCaptureFixture,
) -> None:
    local, api = _par()
    api.falla = LimiteDeTasa("429", espera_s=5.0)
    s = Selector("api", local, api)
    with caplog.at_level(logging.WARNING, logger="voziris.motores.selector"):
        for _ in range(30):
            s.transcribir(AUDIO, "es")
    avisos = [r for r in caplog.records if "límite de peticiones" in r.message]
    assert len(avisos) == 1
    assert "no se vuelve a intentar" in avisos[0].getMessage()


def test_sin_local_al_que_caer_el_corte_no_se_aplica() -> None:
    """Una espera larga es mejor que quedarse sin transcripción."""
    local, api = _par(local_disponible=False)
    api.falla = LimiteDeTasa("429", espera_s=5.0)
    s = Selector("api", local, api)
    for _ in range(3):
        with pytest.raises(MotorNoDisponible, match="ni la API ni el motor local"):
            s.transcribir(AUDIO, "es")
    assert api.llamadas == 3


def test_el_dictado_corto_no_pierde_el_respaldo_ni_se_frena() -> None:
    """Con el corte cerrado, la ruta del dictado suelto es exactamente la de antes."""
    local, api = _par()
    s = Selector("api", local, api)
    t0 = time.perf_counter()
    for _ in range(50):
        assert s.transcribir(AUDIO, "es").motor == "api:groq"
    assert time.perf_counter() - t0 < 1.0  # mirar el reloj bajo un lock no cuesta nada
    assert api.llamadas == 50 and local.llamadas == 0
