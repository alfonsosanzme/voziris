"""Tests de las comprobaciones de tools/empaquetar.py (VOZ-81), sin compilar nada."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from voziris import integridad

RAIZ = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def empaquetar() -> ModuleType:
    spec = importlib.util.spec_from_file_location("empaquetar", RAIZ / "tools" / "empaquetar.py")
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _dist(carpeta: Path) -> Path:
    dist = carpeta / "voziris"
    (dist / "_internal").mkdir(parents=True)
    (dist / "voziris.exe").write_bytes(b"MZ")
    for dll in ("python313.dll", "python3.dll", "vcruntime140.dll", "VCRUNTIME140_1.dll",
                "msvcp140.dll", "MSVCP140_1.dll"):
        (dist / "_internal" / dll).write_bytes(b"d")
    return dist


def test_la_dll_de_python_sale_del_paquete(empaquetar: ModuleType, tmp_path: Path) -> None:
    dist = _dist(tmp_path)
    assert empaquetar._dll_de_python(dist) == "python313.dll"  # no la del Python que corre
    (dist / "_internal" / "python314.dll").write_bytes(b"d")
    assert empaquetar._dll_de_python(dist) is None  # dos: algo raro, mejor parar
    assert r"_internal\python313.dll" in empaquetar.INSTALAR_CMD.format(python_dll="python313.dll")


def test_runtimes_sin_distinguir_mayusculas(empaquetar: ModuleType, tmp_path: Path) -> None:
    dist = _dist(tmp_path)
    assert empaquetar._faltan_runtimes(dist) == []
    (dist / "_internal" / "MSVCP140_1.dll").unlink()
    assert empaquetar._faltan_runtimes(dist) == ["falta msvcp140_1.dll en _internal"]


def test_rutas_largas(empaquetar: ModuleType, tmp_path: Path) -> None:
    dist = _dist(tmp_path)
    larga = dist / "_internal" / ("x" * 95) / "a.dll"
    larga.parent.mkdir()
    larga.write_bytes(b"d")
    problemas = empaquetar._rutas_largas(dist, integridad.archivos_del_programa(dist))
    assert len(problemas) == 1 and "a.dll" in problemas[0]


def test_en_el_zip_solo_va_el_programa(empaquetar: ModuleType, tmp_path: Path) -> None:
    dist = _dist(tmp_path)
    (dist / "Instalar Voziris.cmd").write_text("@echo off", encoding="ascii")
    (dist / empaquetar.NOMBRE_GUIA).write_text("<html>", encoding="utf-8")
    integridad.escribir_manifiesto(dist)
    for del_usuario in ("config.toml", "voziris.log.1", "voziris-transcribir.log.1",
                        "diagnostico.txt"):
        (dist / del_usuario).write_text("de quien lo probó", encoding="utf-8")
    (dist / "modelos").mkdir()
    (dist / "modelos" / "m.onnx").write_bytes(b"m")
    nombres = {
        p.relative_to(dist).as_posix()
        for p in empaquetar._lo_que_va_en_el_zip(dist, integridad)
    }
    assert "voziris.exe" in nombres and "_internal/voziris-manifiesto.txt" in nombres
    assert "Instalar Voziris.cmd" in nombres and empaquetar.NOMBRE_GUIA in nombres
    de_otros = ("config", "voziris.log", "voziris-transcribir", "diagnostico", "modelos")
    assert not any(n.startswith(de_otros) for n in nombres)
