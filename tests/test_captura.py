"""Tests de la captura con búfer previo (VOZ-10).

La mayoría usan un flujo falso que entrega bloques a mano: así se comprueba el
anillo, la acumulación y la normalización sin micrófono y en CI. El último,
marcado `audio`, abre el micrófono real y se salta si no hay ninguno.
"""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from voziris.audio import captura as mod
from voziris.audio.captura import BLOQUE, RMS_OBJETIVO, Captura, aplicar_ganancia, normalizar
from voziris.errores import MicrofonoNoDisponible
from voziris.tipos import SAMPLE_RATE


class FlujoFalso:
    """Sustituye a `sounddevice.InputStream`: guarda el callback y deja alimentarlo."""

    instancias: list[FlujoFalso] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.callback = kwargs["callback"]
        self.finished_callback = kwargs.get("finished_callback")
        self.device = kwargs.get("device")
        self.activo = False
        FlujoFalso.instancias.append(self)

    def start(self) -> None:
        self.activo = True

    def stop(self) -> None:
        self.activo = False

    def close(self) -> None:
        pass

    def alimentar(self, bloque: np.ndarray) -> None:
        self.callback(bloque.reshape(-1, 1).astype(np.float32), len(bloque), None, None)


@pytest.fixture
def sd_falso(monkeypatch: pytest.MonkeyPatch) -> type[FlujoFalso]:
    import sounddevice as sd

    FlujoFalso.instancias.clear()
    monkeypatch.setattr(sd, "InputStream", FlujoFalso)
    monkeypatch.setattr(sd, "query_devices", lambda *_: {"name": "Micro falso"})
    monkeypatch.setattr(
        Captura, "dispositivos", staticmethod(lambda: [(3, "Micro falso"), (7, "Auriculares USB")])
    )
    return FlujoFalso


def _bloques(n: int, valor: float = 0.0) -> list[np.ndarray]:
    return [np.full(BLOQUE, valor, dtype=np.float32) for _ in range(n)]


# --- funciones puras --------------------------------------------------------


def test_normalizar_lleva_al_rms_objetivo_y_recorta() -> None:
    senal = np.sin(np.linspace(0, 200 * math.pi, SAMPLE_RATE)).astype(np.float32) * 0.02
    salida = normalizar(senal)
    assert salida.dtype == np.float32
    assert np.abs(salida).max() <= 1.0
    # Una senoide a RMS 0,30 tiene pico 0,42: no se recorta y el RMS es exacto.
    assert math.isclose(float(np.sqrt(np.mean(salida**2))), RMS_OBJETIVO, rel_tol=0.01)
    picuda = np.zeros(SAMPLE_RATE, dtype=np.float32)
    picuda[::100] = 1.0  # cresta enorme: la normalización recorta, no se rinde
    assert np.abs(normalizar(picuda)).max() == 1.0


def test_normalizar_deja_el_silencio_en_paz() -> None:
    assert not normalizar(np.zeros(100, dtype=np.float32)).any()
    assert len(normalizar(np.zeros(0, dtype=np.float32))) == 0


def test_ganancia_con_recorte() -> None:
    senal = np.full(10, 0.5, dtype=np.float32)
    assert np.allclose(aplicar_ganancia(senal, 0), senal)
    assert np.allclose(aplicar_ganancia(senal, -6.02), 0.25, atol=1e-3)
    assert np.all(aplicar_ganancia(senal, 20) == 1.0)


# --- anillo y acumulación ----------------------------------------------------


def test_el_audio_devuelto_empieza_antes_de_la_pulsacion(sd_falso: type[FlujoFalso]) -> None:
    c = Captura(buffer_previo_ms=500)
    assert c.abrir() is None
    flujo = sd_falso.instancias[-1]
    # Un segundo de «historia» con una marca única en cada bloque.
    for i in range(32):
        flujo.alimentar(np.full(BLOQUE, 0.001 * (i + 1), dtype=np.float32))
    c.empezar_dictado()
    for i in range(10):
        flujo.alimentar(np.full(BLOQUE, 0.5 + 0.01 * i, dtype=np.float32))
    audio = c.terminar_dictado()

    previas = int(SAMPLE_RATE * 0.5)
    assert len(audio.muestras) == previas + 10 * BLOQUE
    # Los primeros 500 ms son los ÚLTIMOS bloques anteriores a la pulsación, en orden.
    crudo_previo = np.concatenate(
        [np.full(BLOQUE, 0.001 * (i + 1), dtype=np.float32) for i in range(32)]
    )[-previas:]
    # La normalización escala todo por el mismo factor: se comprueban las proporciones.
    factor = audio.muestras[previas] / 0.5
    assert np.allclose(audio.muestras[:previas], crudo_previo * factor, rtol=1e-3)
    assert audio.duracion_s == pytest.approx(0.5 + 10 * BLOQUE / SAMPLE_RATE)


def test_sin_historia_el_previo_esta_vacio(sd_falso: type[FlujoFalso]) -> None:
    c = Captura(buffer_previo_ms=500)
    c.abrir()
    c.empezar_dictado()
    sd_falso.instancias[-1].alimentar(np.full(BLOQUE, 0.1, dtype=np.float32))
    assert len(c.terminar_dictado().muestras) == BLOQUE


def test_el_anillo_da_la_vuelta_muchas_veces_sin_perder_el_orden(
    sd_falso: type[FlujoFalso],
) -> None:
    c = Captura(buffer_previo_ms=100)  # 1600 muestras: 4 bloques + margen
    c.abrir()
    flujo = sd_falso.instancias[-1]
    for i in range(1000):
        flujo.alimentar(np.full(BLOQUE, (i % 50) * 0.001, dtype=np.float32))
    c.empezar_dictado()
    audio = c.terminar_dictado()
    previo = audio.muestras[: int(SAMPLE_RATE * 0.1)]
    # Los últimos bloques fueron i = 996..999 → valores 46, 47, 48, 49 (‰), crecientes.
    valores = np.unique(np.round(previo / previo.max() * 49))
    assert valores.tolist() == [46, 47, 48, 49] or valores.tolist() == [47, 48, 49]


def test_el_callback_no_acumula_fuera_del_dictado(sd_falso: type[FlujoFalso]) -> None:
    c = Captura()
    c.abrir()
    flujo = sd_falso.instancias[-1]
    for b in _bloques(100, 0.1):
        flujo.alimentar(b)
    assert c._bloques == []  # nada guardado: el anillo es de tamaño fijo
    c.empezar_dictado()
    flujo.alimentar(_bloques(1, 0.1)[0])
    assert c.grabando
    c.cancelar_dictado()
    assert not c.grabando and c._bloques == []


def test_oyente_de_bloques_solo_mientras_graba(sd_falso: type[FlujoFalso]) -> None:
    c = Captura()
    c.abrir()
    recibidos: list[np.ndarray] = []
    c.oyente_bloques = recibidos.append
    flujo = sd_falso.instancias[-1]
    flujo.alimentar(_bloques(1)[0])
    c.empezar_dictado()
    flujo.alimentar(_bloques(1)[0])
    flujo.alimentar(_bloques(1)[0])
    c.terminar_dictado()
    flujo.alimentar(_bloques(1)[0])
    assert len(recibidos) == 2


def test_nivel_en_escala_de_decibelios(sd_falso: type[FlujoFalso]) -> None:
    c = Captura()
    c.abrir()
    flujo = sd_falso.instancias[-1]
    assert c.nivel_actual() == 0.0
    flujo.alimentar(np.full(BLOQUE, 0.001, dtype=np.float32))  # −60 dBFS
    assert c.nivel_actual() == pytest.approx(0.0, abs=0.01)
    flujo.alimentar(np.full(BLOQUE, 0.0316, dtype=np.float32))  # −30 dBFS: voz normal
    assert c.nivel_actual() == pytest.approx(0.5, abs=0.01)
    flujo.alimentar(np.full(BLOQUE, 1.0, dtype=np.float32))
    assert c.nivel_actual() == 1.0


# --- dispositivos y errores ---------------------------------------------------


def test_dispositivo_por_nombre_y_respaldo_al_predeterminado(
    sd_falso: type[FlujoFalso],
) -> None:
    c = Captura(dispositivo="usb")
    assert c.abrir() is None
    assert sd_falso.instancias[-1].device == 7

    c2 = Captura(dispositivo="Yeti")
    aviso = c2.abrir()
    assert aviso is not None and "Yeti" in aviso and "predeterminado" in aviso
    assert sd_falso.instancias[-1].device is None

    assert c2.cambiar_dispositivo("micro falso") is None
    assert sd_falso.instancias[-1].device == 3
    assert not sd_falso.instancias[-2].activo  # el anterior se cerró


def test_sin_microfono_lanza(monkeypatch: pytest.MonkeyPatch) -> None:
    import sounddevice as sd

    def revienta(**_: Any) -> None:
        raise sd.PortAudioError("Error querying device -1")

    monkeypatch.setattr(sd, "InputStream", revienta)
    with pytest.raises(MicrofonoNoDisponible, match="No se puede abrir"):
        Captura().abrir()


def test_microfono_desconectado_a_media_grabacion(
    sd_falso: type[FlujoFalso], monkeypatch: pytest.MonkeyPatch
) -> None:
    c = Captura()
    c.abrir()
    flujo = sd_falso.instancias[-1]
    c.empezar_dictado()
    flujo.alimentar(_bloques(1, 0.1)[0])
    # Los auriculares USB se desconectan: PortAudio deja de llamar al callback.
    ahora = time.monotonic()
    monkeypatch.setattr(mod.time, "monotonic", lambda: ahora + mod.SIN_BLOQUES_S + 0.1)
    with pytest.raises(MicrofonoNoDisponible, match="sin entregar audio"):
        c.terminar_dictado()
    assert not c.grabando and c._bloques == []  # el dictado se descartó


def test_flujo_terminado_por_portaudio(sd_falso: type[FlujoFalso]) -> None:
    c = Captura()
    c.abrir()
    flujo = sd_falso.instancias[-1]
    assert flujo.finished_callback is not None
    flujo.finished_callback()
    with pytest.raises(MicrofonoNoDisponible, match="dejó de entregar"):
        c.comprobar()
    c.cerrar()
    c.comprobar()  # cerrado a propósito: no es un error


def test_excepcion_en_el_callback_no_lo_mata(sd_falso: type[FlujoFalso]) -> None:
    c = Captura()
    c.abrir()
    flujo = sd_falso.instancias[-1]
    c.oyente_bloques = lambda _b: 1 / 0
    c.empezar_dictado()
    flujo.alimentar(_bloques(1, 0.1)[0])  # no propaga
    with pytest.raises(MicrofonoNoDisponible, match="callback"):
        c.terminar_dictado()


# --- micrófono real -----------------------------------------------------------


@pytest.mark.audio
def test_microfono_real_graba_medio_segundo() -> None:
    import sounddevice as sd

    try:
        if sd.query_devices(kind="input")["max_input_channels"] < 1:
            pytest.skip("sin micrófono")
    except Exception:  # noqa: BLE001
        pytest.skip("sin micrófono")
    c = Captura(buffer_previo_ms=500)
    try:
        c.abrir()
        time.sleep(0.7)
        c.empezar_dictado()
        time.sleep(0.5)
        audio = c.terminar_dictado()
    finally:
        c.cerrar()
    assert 0.9 <= audio.duracion_s <= 1.2
    assert c.nombre_dispositivo


def test_el_dictado_va_a_disco_segun_se_graba(sd_falso: type[FlujoFalso], tmp_path: Path) -> None:
    """VOZ-74: búfer previo + bloques en `pendientes/`, y descartado si se cancela."""
    from voziris.pendientes import Pendientes

    pendientes = Pendientes(tmp_path / "pendientes")
    c = Captura(buffer_previo_ms=500)
    c.pendientes = pendientes
    c.abrir()
    flujo = sd_falso.instancias[-1]
    for _ in range(32):
        flujo.alimentar(np.full(BLOQUE, 0.01, dtype=np.float32))
    c.empezar_dictado()
    for _ in range(40):
        flujo.alimentar(np.full(BLOQUE, 0.2, dtype=np.float32))
    audio = c.terminar_dictado()
    assert c.ultimo_pendiente is not None and c.ultimo_pendiente.exists()
    crudo = np.fromfile(c.ultimo_pendiente, dtype="<f4")
    assert len(crudo) == len(audio.muestras)  # lo mismo que se devolvió, sin normalizar
    assert crudo[-1] == pytest.approx(0.2) and crudo[0] == pytest.approx(0.01)

    c.empezar_dictado()
    flujo.alimentar(np.full(BLOQUE, 0.2, dtype=np.float32))
    c.cancelar_dictado()
    assert len(list((tmp_path / "pendientes").glob("*.f32"))) == 1  # el cancelado no queda
