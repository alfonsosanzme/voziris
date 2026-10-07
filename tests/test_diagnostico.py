"""Tests del archivo de diagnóstico. Sin Windows real: se simulan los eventos."""

from __future__ import annotations

from pathlib import Path

import pytest

from voziris import diagnostico


@pytest.fixture(autouse=True)
def sin_procesos(monkeypatch: pytest.MonkeyPatch) -> None:
    """La comprobación del paquete lanza otro proceso: aquí se sustituye por su resultado."""
    monkeypatch.setattr(
        diagnostico, "comprobacion_del_paquete", lambda espera_s=120.0: "todo carga"
    )
    monkeypatch.setattr(diagnostico, "bloqueos_de_codigo", lambda dias=30: "(ninguno)")


def test_genera_con_log_y_tacha_la_clave(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "voziris.log").write_text(
        "\n".join(f"2026-09-09 07:{i:02d} INFO linea {i}" for i in range(200))
        + "\n2026-09-09 08:00 INFO clave gsk_abcdefghijklmnop1234 usada\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        diagnostico, "eventos_windows", lambda dias=30: "Event[0] APPCRASH voziris.exe"
    )
    monkeypatch.setattr(diagnostico, "informes_wer", lambda: ["2026-09-09 07:58  AppCrash_voziris"])
    ruta = diagnostico.generar(tmp_path, version="1.2.3")
    texto = ruta.read_text(encoding="utf-8")
    assert ruta.name == "diagnostico.txt"
    assert "Diagnóstico de Voziris 1.2.3" in texto
    assert "gsk_***" in texto and "gsk_abcdefghijklmnop1234" not in texto
    assert "linea 199" in texto and "linea 10 " not in texto  # solo la cola
    assert "APPCRASH voziris.exe" in texto and "AppCrash_voziris" in texto
    assert "0xc0000374" in texto or "reg add" in texto  # cómo activar los volcados


def test_sin_log_ni_eventos_no_revienta(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(diagnostico, "eventos_windows", lambda dias=30: "(ninguno)")
    monkeypatch.setattr(diagnostico, "informes_wer", lambda: [])
    texto = diagnostico.generar(tmp_path).read_text(encoding="utf-8")
    assert "no existe voziris.log" in texto and "(ninguno accesible)" in texto


def test_eventos_windows_filtra_por_voziris(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess
    import sys

    if sys.platform != "win32":
        pytest.skip("wevtutil")
    salida = (
        "Event[0]:\n  Nombre: otra.exe\n\nEvent[1]:\n  Nombre: voziris.exe c0000374\n\n"
        "Event[2]:\n  Nombre: tercera.exe\n"
    )

    class R:
        returncode = 0
        stdout = salida
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())
    texto = diagnostico.eventos_windows()
    assert "voziris.exe c0000374" in texto and "otra.exe" not in texto


def test_incluye_la_comprobacion_y_el_control_de_aplicaciones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from voziris import integridad

    (tmp_path / "voziris-transcribir.log").write_text("ERROR No se pudo cargar PyAV\n",
                                                      encoding="utf-8")
    monkeypatch.setattr(diagnostico, "eventos_windows", lambda dias=30: "(ninguno)")
    monkeypatch.setattr(diagnostico, "informes_wer", lambda: [])
    monkeypatch.setattr(integridad, "control_inteligente", lambda: "activado")
    monkeypatch.setattr(diagnostico, "bloqueos_de_codigo",
                        lambda dias=30: "Event[3] voziris.exe bloqueado")
    texto = diagnostico.generar(tmp_path).read_text(encoding="utf-8")
    assert "== Comprobación del paquete (voziris.exe --comprobar) ==\ntodo carga" in texto
    assert "Control inteligente de aplicaciones: activado" in texto
    assert "Event[3] voziris.exe bloqueado" in texto
    assert "No se pudo cargar PyAV" in texto  # el log de los procesos de transcribir
    assert "Windows: " in texto


def test_bloqueos_de_codigo_filtra_por_voziris(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess
    import sys

    if sys.platform != "win32":
        pytest.skip("wevtutil")
    monkeypatch.undo()  # sin el sustituto del fixture
    salida = (  # como lo escribe wevtutil: CRLF y «Event[0]» sin dos puntos
        "Event[0]\r\n  Description: chrome.exe attempted to load x.dll\r\n\r\n"
        "Event[1]\r\n  Description: voziris.exe attempted to load avcodec-62.dll López\r\n"
    ).encode("mbcs")

    class R:
        returncode = 0
        stdout = salida
        stderr = b""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())
    texto = diagnostico.bloqueos_de_codigo()
    assert "avcodec-62.dll" in texto and "chrome" not in texto
    assert "López" in texto  # wevtutil escribe en ANSI, no en UTF-8


def test_comprobacion_del_paquete_resume_el_informe(monkeypatch: pytest.MonkeyPatch) -> None:
    from voziris import integridad

    monkeypatch.undo()
    monkeypatch.setattr(
        integridad, "comprobar_en_otro_proceso",
        lambda orden, espera_s: {"ok": False, "pruebas": [
            {"nombre": "av", "ok": False, "detalle": "Falta avcodec.dll"}]},
    )
    texto = diagnostico.comprobacion_del_paquete()
    assert texto.startswith("HAY PROBLEMAS") and "MAL   av: Falta avcodec.dll" in texto
