"""Tests de VOZ-81 en el arranque: --comprobar, transcribir sin esperas inútiles, logs, mensajes."""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from voziris import __main__ as principal
from voziris import archivos
from voziris import config as cfg


@pytest.fixture
def sin_handlers() -> Iterator[None]:
    """configurar_log añade handlers a la raíz: se quitan al acabar."""
    raiz = logging.getLogger()
    antes = list(raiz.handlers), raiz.level
    yield
    for h in list(raiz.handlers):
        if h not in antes[0]:
            raiz.removeHandler(h)
            h.close()
    raiz.setLevel(antes[1])


def test_comprobar_escribe_el_informe_y_sale_bien(tmp_path: Path, sin_handlers: None) -> None:
    informe = tmp_path / "informe.json"
    assert principal.main(["--comprobar", "--informe", str(informe)]) == 0
    datos = json.loads(informe.read_text(encoding="utf-8"))
    assert datos["ok"] and {p["nombre"] for p in datos["pruebas"]} >= {"av", "onnxruntime"}
    assert not list(tmp_path.glob("voziris.log*"))  # no deja log donde no se le pide


def test_comprobar_sale_con_1_si_algo_no_carga(
    tmp_path: Path, sin_handlers: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "sherpa_onnx", None)
    informe = tmp_path / "informe.json"
    assert principal.main(["--comprobar", "--informe", str(informe)]) == 1
    datos = json.loads(informe.read_text(encoding="utf-8"))
    fallos = [p for p in datos["pruebas"] if not p["ok"]]
    assert [p["nombre"] for p in fallos] == ["sherpa_onnx"]
    assert "separación de hablantes" in fallos[0]["detalle"]


def test_comprobar_no_sale_en_la_ayuda(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        principal.main(["--help"])
    ayuda = capsys.readouterr().out
    assert "--comprobar" not in ayuda and "--informe" not in ayuda
    assert "--instalar" in ayuda


def test_transcribir_abre_el_archivo_antes_de_cargar_el_modelo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Con PyAV roto o el archivo dañado no se esperan 640 MB de modelo para saberlo."""
    roto = tmp_path / "reunion.m4a"
    roto.write_bytes(b"esto no es audio" * 100)

    def no_se_llega(*a: Any, **k: Any) -> Any:
        raise AssertionError("se cargó el modelo antes de mirar el archivo")

    titulos: list[str] = []
    monkeypatch.setattr(principal, "_motores", no_se_llega)
    monkeypatch.setattr(
        principal, "_error_fatal",
        lambda mensaje, con_ventana, titulo="": titulos.append(titulo),
    )
    assert principal._transcribir_grabacion(cfg.Config(), roto, "1", None, False) == 1
    assert titulos == ["Voziris no pudo transcribir"]  # no «no puede arrancar»


def test_transcribir_sin_motor_dice_por_que(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import numpy as np
    import soundfile

    wav = tmp_path / "nota.wav"
    soundfile.write(wav, np.zeros(16_000, dtype=np.float32), 16_000)

    class Local:
        error = "No hay conexión para descargar el modelo de voz (unos 640 MB)"

    class Selector:
        def precalentar(self) -> None: ...

        def disponible(self) -> bool:
            return False

    mensajes: list[str] = []
    monkeypatch.setattr(principal, "_motores", lambda c, p: (Selector(), Local()))
    monkeypatch.setattr(
        principal, "_error_fatal",
        lambda mensaje, con_ventana, titulo="": mensajes.append(mensaje),
    )
    assert principal._transcribir_grabacion(cfg.Config(), wav, "1", None, False) == 1
    assert mensajes == [f"No se pudo transcribir nota.wav: {Local.error}"]


def test_pyav_que_no_carga_dice_que_archivo_falta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from voziris import integridad

    paquete = tmp_path / "voziris"
    (paquete / "_internal" / "av.libs").mkdir(parents=True)
    (paquete / "voziris.exe").write_bytes(b"MZ")
    (paquete / "_internal" / "av.libs" / "avutil-60-cc17.dll").write_bytes(b"u" * 100)
    integridad.escribir_manifiesto(paquete)
    (paquete / "_internal" / "av.libs" / "avutil-60-cc17.dll").unlink()
    monkeypatch.setattr(integridad, "carpeta_del_paquete", lambda: paquete)
    monkeypatch.setitem(sys.modules, "av", None)
    audio = tmp_path / "voz.m4a"
    audio.write_bytes(b"x")
    with pytest.raises(archivos.ArchivoNoLegible) as fallo:
        archivos.decodificar(audio)
    assert "Falta PyAV" not in str(fallo.value)
    assert r"Falta _internal\av.libs\avutil-60-cc17.dll" in str(fallo.value)
    with pytest.raises(archivos.ArchivoNoLegible, match="avutil-60-cc17.dll"):
        archivos.pistas(audio)
    assert any(r.exc_info for r in caplog.records if "PyAV no carga" in r.getMessage())


def test_transcribir_escribe_en_su_propio_log(tmp_path: Path, sin_handlers: None) -> None:
    """La bandeja tiene voziris.log abierto: si --transcribir lo rotaba, perdía todo su log."""
    (tmp_path / principal.NOMBRE_LOG_TRANSCRIBIR).write_bytes(b"x" * 1_000_001)
    principal.configurar_log(tmp_path, False, False, nombre=principal.NOMBRE_LOG_TRANSCRIBIR)
    logging.getLogger("voziris.prueba").info("una línea de transcribir")
    for h in logging.getLogger().handlers:
        h.flush()
    texto = (tmp_path / principal.NOMBRE_LOG_TRANSCRIBIR).read_text(encoding="utf-8")
    assert "una línea de transcribir" in texto and len(texto) < 1000  # rotado al arrancar
    assert (tmp_path / f"{principal.NOMBRE_LOG_TRANSCRIBIR}.1").stat().st_size == 1_000_001
    assert not (tmp_path / principal.NOMBRE_LOG).exists()


def test_volcados_en_pausa_apaga_y_vuelve_a_encender(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import faulthandler

    archivo = open(tmp_path / "fallos.log", "w", encoding="utf-8")  # noqa: SIM115
    monkeypatch.setattr(principal, "_archivo_fallos", archivo)
    estaba = faulthandler.is_enabled()
    try:
        faulthandler.enable(archivo, all_threads=True)
        with principal.volcados_en_pausa():
            assert not faulthandler.is_enabled()
        assert faulthandler.is_enabled()
    finally:
        faulthandler.disable()
        archivo.close()
        if estaba:
            faulthandler.enable()
    monkeypatch.setattr(principal, "_archivo_fallos", None)
    with principal.volcados_en_pausa():  # sin archivo de fallos no toca nada
        pass


def test_comprobar_no_abre_ningun_log(tmp_path: Path, sin_handlers: None) -> None:
    antes = list(logging.getLogger().handlers)
    principal.main(["--comprobar", "--informe", str(tmp_path / "informe.json")])
    nuevos = [h for h in logging.getLogger().handlers if h not in antes]
    assert not any(isinstance(h, logging.FileHandler) for h in nuevos)


@pytest.fixture
def instalada(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    """`_abrir_la_instalada` sin abrir nada: se apunta qué se intentó."""
    from voziris import instalador, integridad, winapi

    estado: dict[str, Any] = {"arranca": True, "lanzadas": [], "sac": "desactivado",
                              "abiertas": []}
    monkeypatch.setattr(instalador, "carpeta_menu_inicio", lambda: tmp_path / "menu")
    monkeypatch.setattr(integridad, "control_inteligente", lambda: estado["sac"])
    monkeypatch.setattr(principal, "ESPERA_ARRANQUE_S", 0.3)
    monkeypatch.setattr(principal.os, "startfile", estado["abiertas"].append, raising=False)
    monkeypatch.setattr(winapi, "ES_WINDOWS", True)
    monkeypatch.setattr(winapi, "hay_instancia_abierta",
                        lambda: estado["arranca"] and bool(estado["abiertas"]))
    monkeypatch.setattr(principal, "_lanzar_desatendido",
                        lambda orden, cwd=None: estado["lanzadas"].append(orden))
    return estado


def test_abrir_la_instalada_por_su_acceso(instalada: dict[str, Any], tmp_path: Path) -> None:
    assert principal._abrir_la_instalada(tmp_path / "Voziris") is None
    assert instalada["abiertas"] == [str(tmp_path / "menu" / "Voziris.lnk")]
    assert instalada["lanzadas"] == []


def test_si_el_acceso_no_la_arranca_se_abre_el_exe_y_se_explica(
    instalada: dict[str, Any], tmp_path: Path
) -> None:
    instalada["arranca"] = False  # Windows enseñó su aviso y no arrancó nada
    instalada["sac"] = "activado"
    aviso = principal._abrir_la_instalada(tmp_path / "Voziris")
    assert instalada["lanzadas"] == [[str(tmp_path / "Voziris" / "voziris.exe")]]
    assert aviso is not None and "Control inteligente de aplicaciones" in aviso
    instalada["sac"] = "evaluación"  # en evaluación no bloquea: no se le echa la culpa
    aviso = principal._abrir_la_instalada(tmp_path / "Voziris")
    assert aviso is not None and "Control inteligente" not in aviso


def test_revisar_paquete_avisa_de_lo_que_falta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    from voziris import integridad

    paquete = tmp_path / "voziris"
    (paquete / "_internal" / "av.libs").mkdir(parents=True)
    (paquete / "voziris.exe").write_bytes(b"MZ")
    (paquete / "_internal" / "av.libs" / "avcodec-62.dll").write_bytes(b"a" * 10)
    integridad.escribir_manifiesto(paquete)
    (paquete / "_internal" / "av.libs" / "avcodec-62.dll").unlink()
    monkeypatch.setattr(integridad, "carpeta_del_paquete", lambda: paquete)
    avisos: list[str] = []
    principal._revisar_paquete(avisos.append)
    for hilo in threading.enumerate():
        if hilo.name == "voziris-paquete":
            hilo.join(5)
    assert len(avisos) == 1
    assert r"Falta _internal\av.libs\avcodec-62.dll" in avisos[0] and "antivirus" in avisos[0]
