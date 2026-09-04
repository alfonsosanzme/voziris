"""Tests del motor local (VOZ-11).

Casi todos sustituyen `onnx_asr.load_model` por un doble que crea la carpeta
del modelo y devuelve un objeto con `recognize()`: así se prueba la carga, la
descarga interrumpida, el progreso y la transcripción sin red y en CI. El
último, marcado `modelo`, usa el Parakeet real si está descargado.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from voziris.errores import MotorNoDisponible, TranscripcionFallida
from voziris.motores.local import MotorLocal
from voziris.tipos import SAMPLE_RATE, Audio

RAIZ = Path(__file__).resolve().parents[1]


class ModeloFalso:
    def __init__(self, texto: str = "Hola, mundo.", tarda_s: float = 0.0) -> None:
        self.texto = texto
        self.tarda_s = tarda_s
        self.llamadas: list[tuple[np.ndarray, int]] = []

    def recognize(self, waveform: np.ndarray, *, sample_rate: int = 16_000) -> str:
        self.llamadas.append((waveform, sample_rate))
        time.sleep(self.tarda_s)
        return self.texto


@pytest.fixture
def onnx_falso(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """`load_model` falso: apunta lo que recibe, «descarga» un .onnx y devuelve ModeloFalso."""
    import onnx_asr

    registro: dict[str, Any] = {"llamadas": [], "modelo": ModeloFalso(), "falla": None}

    def load_model(model: str, path: Path, *, quantization: str | None, sess_options: Any) -> Any:
        registro["llamadas"].append({"model": model, "path": Path(path),
                                     "quantization": quantization, "sess_options": sess_options})
        if registro["falla"] is not None:
            raise registro["falla"]
        if Path(path).exists() and not (Path(path) / "vocab.txt").exists():
            # La carpeta existe: onnx-asr entra en modo offline y no descarga.
            raise FileNotFoundError("vocab.txt")
        Path(path).mkdir(parents=True, exist_ok=True)
        (Path(path) / "encoder-model.int8.onnx").write_bytes(b"x" * 1000)
        (Path(path) / "vocab.txt").write_text("<blk>\n")
        return registro["modelo"]

    monkeypatch.setattr(onnx_asr, "load_model", load_model)
    return registro


def _audio(segundos: float = 1.0) -> Audio:
    return Audio(muestras=np.zeros(int(SAMPLE_RATE * segundos), dtype=np.float32))


def test_no_disponible_antes_de_precalentar(tmp_path: Path) -> None:
    motor = MotorLocal("nemo-parakeet-tdt-0.6b-v3", tmp_path)
    assert not motor.disponible()
    with pytest.raises(MotorNoDisponible, match="todavía no está cargado"):
        motor.transcribir(_audio(), "es")


def test_precalentar_descarga_en_su_carpeta_y_transcribe(
    tmp_path: Path, onnx_falso: dict[str, Any]
) -> None:
    motor = MotorLocal("nemo-parakeet-tdt-0.6b-v3", tmp_path, hilos=0, cuantizacion="int8")
    motor.precalentar()
    assert motor.disponible() and motor.error is None
    llamada = onnx_falso["llamadas"][0]
    assert llamada["path"] == tmp_path / "nemo-parakeet-tdt-0.6b-v3-int8"
    assert llamada["quantization"] == "int8"
    assert llamada["sess_options"] is None  # hilos = 0: onnxruntime decide

    audio = _audio(2.0)
    t = motor.transcribir(audio, "es")
    assert t.texto == "Hola, mundo." and t.motor == "local" and t.idioma == "es"
    assert t.duracion_audio_s == 2.0
    calentamiento, real = onnx_falso["modelo"].llamadas
    assert len(calentamiento[0]) == SAMPLE_RATE  # un segundo de silencio al precalentar
    muestras, sr = real
    assert sr == SAMPLE_RATE and muestras.dtype == np.float32
    assert len(muestras) == len(audio.muestras)


def test_ms_proceso_mide_solo_la_inferencia(tmp_path: Path, onnx_falso: dict[str, Any]) -> None:
    onnx_falso["modelo"] = ModeloFalso(tarda_s=0.05)
    motor = MotorLocal("nemo-parakeet-tdt-0.6b-v3", tmp_path)
    motor.precalentar()
    t = motor.transcribir(_audio(10.0), "es")
    assert 40 <= t.ms_proceso < 400
    assert t.rtf == pytest.approx(t.ms_proceso / 1000 / 10, rel=1e-6)


def test_fp32_no_pasa_cuantizacion_y_usa_otra_carpeta(
    tmp_path: Path, onnx_falso: dict[str, Any]
) -> None:
    motor = MotorLocal("nemo-parakeet-tdt-0.6b-v3", tmp_path, cuantizacion="fp32")
    motor.precalentar()
    llamada = onnx_falso["llamadas"][0]
    assert llamada["quantization"] is None
    assert llamada["path"].name == "nemo-parakeet-tdt-0.6b-v3-fp32"


def test_hilos_fijan_las_opciones_de_sesion(tmp_path: Path, onnx_falso: dict[str, Any]) -> None:
    MotorLocal("nemo-parakeet-tdt-0.6b-v3", tmp_path, hilos=3).precalentar()
    assert onnx_falso["llamadas"][0]["sess_options"].intra_op_num_threads == 3


def test_descarga_interrumpida_se_borra_y_se_repite(
    tmp_path: Path, onnx_falso: dict[str, Any]
) -> None:
    carpeta = tmp_path / "nemo-parakeet-tdt-0.6b-v3-int8"
    resto = carpeta / ".cache" / "huggingface" / "download" / "encoder.onnx.incomplete"
    resto.parent.mkdir(parents=True)
    resto.write_bytes(b"a medias")
    motor = MotorLocal("nemo-parakeet-tdt-0.6b-v3", tmp_path)
    motor.precalentar()
    assert motor.disponible()
    assert not resto.exists()
    assert (carpeta / "encoder-model.int8.onnx").exists()
    assert len(onnx_falso["llamadas"]) == 1  # se borró ANTES de llamar: sin reintento


def test_carpeta_sin_onnx_se_borra_antes_de_cargar(
    tmp_path: Path, onnx_falso: dict[str, Any]
) -> None:
    carpeta = tmp_path / "nemo-parakeet-tdt-0.6b-v3-int8"
    carpeta.mkdir()
    (carpeta / "config.json").write_text("{}")  # ni .incomplete ni .onnx: a medias
    motor = MotorLocal("nemo-parakeet-tdt-0.6b-v3", tmp_path)
    motor.precalentar()
    assert motor.disponible()
    assert len(onnx_falso["llamadas"]) == 1
    assert (carpeta / "vocab.txt").exists()


def test_archivo_que_falta_provoca_un_reintento(
    tmp_path: Path, onnx_falso: dict[str, Any]
) -> None:
    """Hay .onnx pero falta otro archivo: onnx-asr, en modo offline, no lo encuentra."""
    carpeta = tmp_path / "nemo-parakeet-tdt-0.6b-v3-int8"
    carpeta.mkdir()
    (carpeta / "encoder-model.int8.onnx").write_bytes(b"x")  # parece completa, no lo está
    motor = MotorLocal("nemo-parakeet-tdt-0.6b-v3", tmp_path)
    motor.precalentar()
    assert motor.disponible()
    assert len(onnx_falso["llamadas"]) == 2  # una fallida, se borra, otra buena
    assert (carpeta / "vocab.txt").exists()


def test_fallo_de_carga_no_lanza_y_se_explica(tmp_path: Path, onnx_falso: dict[str, Any]) -> None:
    onnx_falso["falla"] = RuntimeError("onnxruntime no encuentra la DLL")
    motor = MotorLocal("nemo-parakeet-tdt-0.6b-v3", tmp_path)
    motor.precalentar()  # no lanza
    assert not motor.disponible()
    assert motor.error is not None and "no encuentra la DLL" in motor.error
    with pytest.raises(MotorNoDisponible, match="no encuentra la DLL"):
        motor.transcribir(_audio(), "es")


def test_precalentar_dos_veces_carga_una(tmp_path: Path, onnx_falso: dict[str, Any]) -> None:
    motor = MotorLocal("nemo-parakeet-tdt-0.6b-v3", tmp_path)
    motor.precalentar()
    motor.precalentar()
    assert len(onnx_falso["llamadas"]) == 1


def test_progreso_de_descarga(tmp_path: Path, onnx_falso: dict[str, Any]) -> None:
    mensajes: list[tuple[str, float | None]] = []
    motor = MotorLocal(
        "nemo-parakeet-tdt-0.6b-v3", tmp_path, al_progresar=lambda m, f: mensajes.append((m, f))
    )
    motor.precalentar()
    assert mensajes[0][0].startswith("Descargando el modelo local")
    assert mensajes[-1] == ("Modelo local descargado", 1.0)
    # Con el modelo ya en disco no hay descarga ni mensajes.
    mensajes.clear()
    otro = MotorLocal(
        "nemo-parakeet-tdt-0.6b-v3", tmp_path, al_progresar=lambda m, f: mensajes.append((m, f))
    )
    otro.precalentar()
    assert mensajes == []


def test_texto_vacio_y_audio_corto(tmp_path: Path, onnx_falso: dict[str, Any]) -> None:
    onnx_falso["modelo"] = ModeloFalso(texto="   ")
    motor = MotorLocal("nemo-parakeet-tdt-0.6b-v3", tmp_path)
    motor.precalentar()
    with pytest.raises(TranscripcionFallida, match="no se oyó nada"):
        motor.transcribir(_audio(), "es")
    with pytest.raises(TranscripcionFallida, match="demasiado corto"):
        motor.transcribir(_audio(0.05), "es")
    # Calentamiento + la vacía: el audio corto no llega al modelo.
    assert len(onnx_falso["modelo"].llamadas) == 2


def test_excepcion_del_modelo_es_transcripcion_fallida(
    tmp_path: Path, onnx_falso: dict[str, Any]
) -> None:
    class Revienta:
        llamadas = 0

        def recognize(self, *_: Any, **__: Any) -> str:
            self.llamadas += 1
            if self.llamadas > 1:  # el calentamiento pasa; el dictado real revienta
                raise ValueError("forma incorrecta")
            return ""

    onnx_falso["modelo"] = Revienta()
    motor = MotorLocal("nemo-parakeet-tdt-0.6b-v3", tmp_path)
    motor.precalentar()
    with pytest.raises(TranscripcionFallida, match="forma incorrecta"):
        motor.transcribir(_audio(), "es")


def test_fallo_en_el_calentamiento_deja_el_motor_no_disponible(
    tmp_path: Path, onnx_falso: dict[str, Any]
) -> None:
    class Revienta:
        def recognize(self, *_: Any, **__: Any) -> str:
            raise RuntimeError("DLL de onnxruntime")

    onnx_falso["modelo"] = Revienta()
    motor = MotorLocal("nemo-parakeet-tdt-0.6b-v3", tmp_path)
    motor.precalentar()
    assert not motor.disponible()
    assert motor.error is not None and "DLL" in motor.error


# --- modelo real ----------------------------------------------------------------


@pytest.mark.modelo
def test_parakeet_real_puntua_en_espanol() -> None:
    carpeta = RAIZ / "modelos"
    if not (carpeta / "nemo-parakeet-tdt-0.6b-v3-int8").exists():
        pytest.skip("modelo no descargado")
    wav = RAIZ / "banco" / "muestra-08.wav"
    if not wav.exists():
        pytest.skip("sin muestra de audio")
    import soundfile as sf

    datos, sr = sf.read(str(wav), dtype="float32")
    # Normalización a RMS 0,30 con recorte, como hace Captura (VOZ-10, docs/H0.md).
    datos = np.clip(datos * (0.30 / np.sqrt(np.mean(datos**2))), -1, 1).astype(np.float32)
    motor = MotorLocal("nemo-parakeet-tdt-0.6b-v3", carpeta)
    motor.precalentar()
    assert motor.disponible(), motor.error
    t = motor.transcribir(Audio(muestras=datos, sr=sr), "es")
    assert "sábado" in t.texto.lower()
    assert t.texto[0].isupper() and t.texto.rstrip().endswith(".")
    assert t.rtf < 0.25, t.rtf
