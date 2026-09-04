"""Tests del destino Markdown (VOZ-50). Archivos en tmp_path; el bloqueo se simula con msvcrt."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import pytest

from voziris.destinos import archivo_md as mod
from voziris.destinos.archivo_md import ArchivoMarkdown
from voziris.errores import EntregaFallida
from voziris.tipos import Contexto, Modo, Nivel

CTX = Contexto("markdown", Modo.MANTENER, None, False, Nivel.LITERAL)
SELLO = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}")


def _destino(tmp_path: Path, **kwargs: Any) -> ArchivoMarkdown:
    return ArchivoMarkdown(tmp_path / "entrada.md", **kwargs)


def test_tres_dictados_tres_lineas_con_sello(tmp_path: Path) -> None:
    d = _destino(tmp_path)
    for texto in ("Llamar a la gestoría.", "Mirar Parakeet.", "Idea para Kairis."):
        entrega = d.entregar(texto, CTX)
        assert entrega.ok and entrega.detalle == "añadido a entrada.md"
    lineas = (tmp_path / "entrada.md").read_text(encoding="utf-8").splitlines()
    assert len(lineas) == 3
    esperados = ("Llamar a la gestoría.", "Mirar Parakeet.", "Idea para Kairis.")
    for linea, texto in zip(lineas, esperados, strict=True):
        assert SELLO.match(linea[2:]) and linea.startswith("- ") and linea.endswith(" · " + texto)


def test_utf8_sin_bom_y_saltos_lf(tmp_path: Path) -> None:
    _destino(tmp_path).entregar("ñandú «cita»", CTX)
    datos = (tmp_path / "entrada.md").read_bytes()
    assert not datos.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" not in datos and datos.endswith(b"\n")
    assert "ñandú «cita»" in datos.decode("utf-8")


def test_garantiza_el_salto_final_antes_de_anadir(tmp_path: Path) -> None:
    ruta = tmp_path / "entrada.md"
    ruta.write_bytes(b"- linea sin salto final")
    _destino(tmp_path).entregar("nueva", CTX)
    lineas = ruta.read_text(encoding="utf-8").splitlines()
    assert lineas[0] == "- linea sin salto final" and lineas[1].endswith("· nueva")
    ruta.write_bytes(b"")  # vacío: sin salto de más
    _destino(tmp_path).entregar("primera", CTX)
    assert not ruta.read_text(encoding="utf-8").startswith("\n")


def test_funciona_con_el_archivo_abierto_por_otro(tmp_path: Path) -> None:
    """Obsidian lo tiene abierto para leer: no se abre en exclusiva."""
    ruta = tmp_path / "entrada.md"
    ruta.write_text("- antes\n", encoding="utf-8")
    with open(ruta, encoding="utf-8") as obsidian:
        _destino(tmp_path).entregar("mientras está abierto", CTX)
        obsidian.seek(0)
        contenido = obsidian.read()
    assert contenido.splitlines()[-1].endswith("· mientras está abierto")


def test_separador_y_formato_propios(tmp_path: Path) -> None:
    d = _destino(tmp_path, formato="{texto} ({sello})", sello="%H:%M", separador="---")
    d.entregar("uno", CTX)
    d.entregar("dos", CTX)
    lineas = (tmp_path / "entrada.md").read_text(encoding="utf-8").splitlines()
    assert lineas[0] == "---" and re.fullmatch(r"uno \(\d{2}:\d{2}\)", lineas[1])
    assert lineas[2] == "---" and lineas[3].startswith("dos (")


def test_multilinea_se_aplana_salvo_formato_con_salto(tmp_path: Path) -> None:
    _destino(tmp_path).entregar("primera línea\n\nsegunda   línea\tfin", CTX)
    assert (tmp_path / "entrada.md").read_text(encoding="utf-8").count("\n") == 1
    assert "primera línea segunda línea fin" in (tmp_path / "entrada.md").read_text("utf-8")
    d = _destino(tmp_path, formato="## {sello}\n{texto}\n")
    d.entregar("a\nb", CTX)
    assert "a\nb" in (tmp_path / "entrada.md").read_text(encoding="utf-8")


def test_marcador_desconocido_no_revienta(tmp_path: Path, caplog: Any) -> None:
    d = _destino(tmp_path, formato="- {sello} {quien} · {texto}")
    assert d.entregar("hola", CTX).ok
    assert "{quien}" in (tmp_path / "entrada.md").read_text(encoding="utf-8")
    assert any("quien" in r.message for r in caplog.records)


def test_carpeta_inexistente_no_se_crea(tmp_path: Path) -> None:
    d = ArchivoMarkdown(tmp_path / "no" / "existe" / "entrada.md")
    with pytest.raises(EntregaFallida, match="no existe"):
        d.entregar("hola", CTX)
    assert not (tmp_path / "no").exists()


def test_archivo_inexistente_se_crea(tmp_path: Path) -> None:
    assert not (tmp_path / "entrada.md").exists()
    _destino(tmp_path).entregar("hola", CTX)
    assert (tmp_path / "entrada.md").exists()


@pytest.mark.skipif(sys.platform != "win32", reason="bloqueo de archivo de Windows")
def test_archivo_bloqueado_reintenta_y_luego_falla(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import msvcrt

    monkeypatch.setattr(mod, "ESPERA_REINTENTO_S", 0.05)
    ruta = tmp_path / "entrada.md"
    ruta.write_text("- antes\n", encoding="utf-8")
    d = _destino(tmp_path)
    with open(ruta, "r+b") as bloqueado:
        msvcrt.locking(bloqueado.fileno(), msvcrt.LK_NBLCK, 1024)
        try:
            with pytest.raises(EntregaFallida, match="No se pudo escribir"):
                d.entregar("durante el bloqueo", CTX)
        finally:
            bloqueado.seek(0)
            msvcrt.locking(bloqueado.fileno(), msvcrt.LK_UNLCK, 1024)
    # Suelto el bloqueo: la siguiente entrega funciona.
    assert d.entregar("después", CTX).ok
    assert "después" in ruta.read_text(encoding="utf-8")


@pytest.mark.skipif(sys.platform != "win32", reason="bloqueo de archivo de Windows")
def test_bloqueo_breve_se_resuelve_en_el_reintento(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import msvcrt
    import threading

    monkeypatch.setattr(mod, "ESPERA_REINTENTO_S", 0.15)
    ruta = tmp_path / "entrada.md"
    ruta.write_text("- antes\n", encoding="utf-8")
    bloqueado = open(ruta, "r+b")  # noqa: SIM115 — se cierra en el hilo
    msvcrt.locking(bloqueado.fileno(), msvcrt.LK_NBLCK, 1024)

    def soltar() -> None:
        import time

        time.sleep(0.05)
        bloqueado.seek(0)
        msvcrt.locking(bloqueado.fileno(), msvcrt.LK_UNLCK, 1024)
        bloqueado.close()

    threading.Thread(target=soltar).start()
    assert _destino(tmp_path).entregar("tras el reintento", CTX).ok
