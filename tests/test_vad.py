"""Tests del corte por silencio (VOZ-20).

La lógica de histéresis y conteo se prueba con una sesión ONNX falsa que
devuelve probabilidades programadas. El modelo real, con voz real del
cliente, en los tests marcados `modelo`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from voziris.audio import vad as mod
from voziris.audio.vad import BLOQUE, CONTEXTO, MS_POR_BLOQUE, DetectorSilencio
from voziris.tipos import SAMPLE_RATE

RAIZ = Path(__file__).resolve().parents[1]


class SesionFalsa:
    """Devuelve las probabilidades de la lista, en orden, y apunta las entradas."""

    def __init__(self, probabilidades: list[float]) -> None:
        self.probabilidades = list(probabilidades)
        self.entradas: list[dict[str, Any]] = []

    def run(self, _salidas: list[str], entradas: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
        self.entradas.append(entradas)
        p = self.probabilidades.pop(0) if self.probabilidades else 0.0
        return np.array([[p]], np.float32), np.ones((2, 1, 128), np.float32)


def _detector(
    probabilidades: list[float], corte_ms: int = 1200
) -> tuple[DetectorSilencio, SesionFalsa]:
    d = DetectorSilencio(corte_ms)
    sesion = SesionFalsa(probabilidades)
    d._sesion = sesion
    return d, sesion


def _bloques(n: int, valor: float = 0.1) -> list[np.ndarray]:
    return [np.full(BLOQUE, valor, np.float32) for _ in range(n)]


# --- lógica -----------------------------------------------------------------------


def test_sin_modelo_nunca_corta() -> None:
    d = DetectorSilencio(1200)
    assert not d.disponible
    assert all(not d.alimentar(b) for b in _bloques(100))


def test_corta_tras_silencio_continuo() -> None:
    bloques_corte = int(1200 / MS_POR_BLOQUE)  # 37,5 → corta en el 38
    d, _ = _detector([0.9] * 10 + [0.0] * 60)
    resultados = [d.alimentar(b) for b in _bloques(70)]
    assert not any(resultados[: 10 + bloques_corte])
    assert resultados[10 + bloques_corte]
    assert d.hubo_voz


def test_una_pausa_de_700_ms_no_corta() -> None:
    pausa = int(700 / MS_POR_BLOQUE)  # 21 bloques
    d, _ = _detector([0.9] * 10 + [0.0] * pausa + [0.9] * 10 + [0.0] * 60)
    resultados = [d.alimentar(b) for b in _bloques(10 + pausa + 10 + 60)]
    primer_corte = resultados.index(True)
    assert primer_corte >= 10 + pausa + 10 + int(1200 / MS_POR_BLOQUE)
    assert not any(resultados[: 10 + pausa + 10])


def test_histeresis_entre_umbrales_no_suma_ni_resta() -> None:
    d, _ = _detector([0.0] * 20 + [0.45] * 100 + [0.0] * 20)
    resultados = [d.alimentar(b) for b in _bloques(140)]
    # 20 + 20 bloques de silencio = 1280 ms > 1200: corta en el último tramo, no antes.
    assert not any(resultados[:120])
    assert any(resultados[120:])


def test_reiniciar_olvida_el_silencio_anterior() -> None:
    d, sesion = _detector([0.0] * 30 + [0.0] * 30)
    for b in _bloques(30):
        d.alimentar(b)
    assert d.silencio_ms > 900
    d.reiniciar()
    assert d.silencio_ms == 0 and not d.hubo_voz
    assert not any(d.alimentar(b) for b in _bloques(30))  # 960 ms: aún no
    # El estado del modelo también se reinició: la siguiente llamada lleva ceros.
    assert not sesion.entradas[30]["state"].any()


def test_contexto_y_forma_de_entrada() -> None:
    d, sesion = _detector([0.0] * 3)
    a, b = np.full(BLOQUE, 0.1, np.float32), np.full(BLOQUE, 0.2, np.float32)
    d.alimentar(a)
    d.alimentar(b)
    primera, segunda = sesion.entradas[0], sesion.entradas[1]
    assert primera["input"].shape == (1, CONTEXTO + BLOQUE)
    assert not primera["input"][0, :CONTEXTO].any()  # sin contexto al principio
    assert np.all(segunda["input"][0, :CONTEXTO] == 0.1)  # cola del bloque anterior
    assert np.all(segunda["input"][0, CONTEXTO:] == 0.2)
    assert int(primera["sr"]) == SAMPLE_RATE
    assert np.all(segunda["state"] == 1)  # el estado devuelto se realimenta


def test_bloques_de_otro_tamano_se_trocean() -> None:
    d, sesion = _detector([0.0] * 10)
    d.alimentar(np.zeros(BLOQUE * 2 + 100, np.float32))
    assert len(sesion.entradas) == 3  # 2 enteros + 1 rellenado


def test_fallo_del_modelo_no_corta_ni_revienta(caplog: Any) -> None:
    class Revienta:
        def run(self, *_: Any, **__: Any) -> None:
            raise RuntimeError("shape mismatch")

    d = DetectorSilencio(1200)
    d._sesion = Revienta()
    assert not any(d.alimentar(b) for b in _bloques(50))
    assert sum("Silero" in r.message for r in caplog.records) == 1


def test_corte_configurable_en_caliente() -> None:
    d, _ = _detector([0.0] * 20)
    d.silencio_corte_ms = 300
    resultados = [d.alimentar(b) for b in _bloques(20)]
    assert resultados.index(True) == int(300 / MS_POR_BLOQUE)


# --- modelo real ----------------------------------------------------------------------


def _voz_real(d: DetectorSilencio) -> np.ndarray:
    """Tres segundos de la muestra 08, recortados al primer y último bloque con voz.

    La grabación empieza con medio segundo de silencio de sala; sin recortar,
    la «pausa» del test sería más larga de lo que dice.
    """
    wav = RAIZ / "banco" / "muestra-08.wav"
    if not wav.exists():
        pytest.skip("sin muestra de voz")
    import soundfile as sf

    datos, sr = sf.read(str(wav), dtype="float32")
    assert sr == SAMPLE_RATE
    datos = np.clip(datos * (0.30 / np.sqrt(np.mean(datos**2))), -1, 1).astype(np.float32)
    trozo = datos[: SAMPLE_RATE * 5]
    d.reiniciar()
    probabilidades = []
    for i in range(0, len(trozo) - BLOQUE + 1, BLOQUE):
        d.alimentar(trozo[i : i + BLOQUE])
        probabilidades.append(d.ultima_probabilidad)
    # Voz sostenida: 8 bloques seguidos (256 ms) por encima del umbral, para no
    # empezar en una respiración ni acabar en una.
    sostenida = [
        i for i in range(len(probabilidades) - 8)
        if all(p >= mod.UMBRAL_VOZ for p in probabilidades[i : i + 8])
    ]
    assert sostenida, "la muestra no tiene voz sostenida según Silero"
    inicio, fin = sostenida[0], sostenida[-1] + 8
    return trozo[inicio * BLOQUE : fin * BLOQUE]


@pytest.mark.modelo
def test_silero_real_distingue_voz_de_silencio() -> None:
    if not (RAIZ / "modelos" / "silero-vad" / mod.ARCHIVO).exists():
        pytest.skip("Silero no descargado")
    d = DetectorSilencio(1200, RAIZ / "modelos")
    d.precalentar()
    assert d.disponible
    voz = _voz_real(d)
    assert len(voz) > SAMPLE_RATE * 2
    silencio = np.zeros(SAMPLE_RATE * 2, np.float32)
    pausa = np.zeros(int(SAMPLE_RATE * 0.7), np.float32)

    d.reiniciar()
    cortes: list[int] = []
    probabilidades: list[float] = []
    audio = np.concatenate((voz, pausa, voz, silencio))
    for i in range(0, len(audio) - BLOQUE + 1, BLOQUE):
        if d.alimentar(audio[i : i + BLOQUE]):
            cortes.append(i)
        probabilidades.append(round(d.ultima_probabilidad, 2))
    print("\nprobabilidades por bloque:", probabilidades)
    assert d.hubo_voz
    assert cortes, "no cortó tras dos segundos de silencio"
    primer_corte_s = cortes[0] / SAMPLE_RATE
    inicio_silencio_s = (2 * len(voz) + len(pausa)) / SAMPLE_RATE
    # No corta durante la voz ni en la pausa de 700 ms; corta ~1,2 s después de
    # callar (más lo que Silero tarde en dejar de ver voz en la última sílaba).
    assert inicio_silencio_s + 1.0 <= primer_corte_s <= inicio_silencio_s + 1.8, primer_corte_s
