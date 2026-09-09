"""Tests del archivo de diagnóstico. Sin Windows real: se simulan los eventos."""

from __future__ import annotations

from pathlib import Path

import pytest

from voziris import diagnostico


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
