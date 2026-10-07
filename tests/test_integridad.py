"""Tests de la comprobación del paquete (VOZ-81): manifiesto, qué falta, bloqueos y marcas."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from voziris import integridad


def _paquete(carpeta: Path) -> Path:
    """Un paquete con la forma del de verdad: voziris.exe y _internal con PyAV."""
    raiz = carpeta / "voziris"
    (raiz / "_internal" / "av.libs").mkdir(parents=True)
    (raiz / "_internal" / "av").mkdir()
    (raiz / "voziris.exe").write_bytes(b"MZ" + b"\0" * 64)
    (raiz / "_internal" / "python313.dll").write_bytes(b"p" * 300)
    (raiz / "_internal" / "av" / "_core.pyd").write_bytes(b"c" * 200)
    (raiz / "_internal" / "av.libs" / "avcodec-62-984de331.dll").write_bytes(b"a" * 500)
    (raiz / "config.toml").write_text("no es del programa", encoding="utf-8")
    integridad.escribir_manifiesto(raiz)
    return raiz


def test_manifiesto_ida_y_vuelta_solo_con_el_programa(tmp_path: Path) -> None:
    raiz = _paquete(tmp_path)
    entradas = integridad.leer_manifiesto(raiz)
    assert entradas is not None
    rutas = {e.ruta for e in entradas}
    assert rutas == {
        "voziris.exe", "_internal/python313.dll", "_internal/av/_core.pyd",
        "_internal/av.libs/avcodec-62-984de331.dll",
    }  # ni config.toml ni el propio manifiesto
    assert integridad.tamano_total(entradas) == 66 + 300 + 200 + 500
    resultado = integridad.comprobar(raiz, hashes=True)
    assert resultado.ok and resultado.comprobados == 4
    assert resultado.resumen() == "Los 4 archivos están completos"


def test_sin_manifiesto_no_se_da_por_roto(tmp_path: Path) -> None:
    assert integridad.leer_manifiesto(tmp_path) is None
    resultado = integridad.comprobar(tmp_path)
    assert resultado.sin_manifiesto and resultado.ok


def test_nombra_el_archivo_que_falta_y_el_danado(tmp_path: Path) -> None:
    raiz = _paquete(tmp_path)
    (raiz / "_internal" / "av.libs" / "avcodec-62-984de331.dll").unlink()
    (raiz / "_internal" / "av" / "_core.pyd").write_bytes(b"x" * 10)  # truncado
    resultado = integridad.comprobar(raiz)
    assert not resultado.ok
    assert resultado.faltan == ["_internal/av.libs/avcodec-62-984de331.dll"]
    assert resultado.distintos == ["_internal/av/_core.pyd"]
    texto = resultado.resumen()
    assert r"Falta _internal\av.libs\avcodec-62-984de331.dll" in texto
    assert r"Está dañado _internal\av\_core.pyd" in texto


def test_el_contenido_cambiado_solo_lo_ven_los_hashes(tmp_path: Path) -> None:
    raiz = _paquete(tmp_path)
    (raiz / "_internal" / "python313.dll").write_bytes(b"q" * 300)  # mismo tamaño
    assert integridad.comprobar(raiz).ok
    assert integridad.comprobar(raiz, hashes=True).distintos == ["_internal/python313.dll"]


def test_resumen_con_muchos_que_faltan(tmp_path: Path) -> None:
    faltan = [f"_internal/{i}.dll" for i in range(5)]
    r = integridad.Resultado(tmp_path, comprobados=10, faltan=faltan)
    assert r.resumen() == (
        r"Faltan 5 archivos: _internal\0.dll, _internal\1.dll, _internal\2.dll y 2 más"
    )


def test_explicar_fallo_de_carga_nombra_la_dll(tmp_path: Path) -> None:
    raiz = _paquete(tmp_path)
    (raiz / "_internal" / "av.libs" / "avcodec-62-984de331.dll").unlink()
    error = ImportError("DLL load failed while importing _core: No se puede encontrar el módulo")
    texto = integridad.explicar_fallo_de_carga("PyAV", error, raiz)
    assert texto.startswith("No se pudo cargar PyAV.")
    assert "avcodec-62-984de331.dll" in texto and "antivirus" in texto


def test_explicar_fallo_de_carga_sin_saber_mas_da_el_error(tmp_path: Path) -> None:
    raiz = _paquete(tmp_path)  # completo: no es un archivo que falte
    error = ImportError("DLL load failed while importing _core: algo raro")
    texto = integridad.explicar_fallo_de_carga("PyAV", error, raiz)
    assert texto == (
        "No se pudo cargar PyAV (ImportError: DLL load failed while importing _core: algo raro)"
    )


def test_el_error_de_numpy_se_resume_en_una_linea(tmp_path: Path) -> None:
    sermon = ImportError(
        "\n\nIMPORTANT: PLEASE READ THIS FOR ADVICE ON HOW TO SOLVE THIS ISSUE!\n\n"
        "Importing the numpy C-extensions failed.\n" + "bla bla\n" * 20
        + "Original error was: DLL load failed while importing _multiarray_umath: "
        "No se puede encontrar el módulo especificado.\n"
    )
    texto = integridad.explicar_fallo_de_carga("numpy", sermon, _paquete(tmp_path))
    assert texto == (
        "No se pudo cargar numpy (ImportError: DLL load failed while importing "
        "_multiarray_umath: No se puede encontrar el módulo especificado.)"
    )


def test_explicar_fallo_de_carga_por_ruta_larga(tmp_path: Path) -> None:
    larga = tmp_path / ("x" * (integridad.LIMITE_RUTA + 5))
    texto = integridad.explicar_fallo_de_carga("PyAV", ImportError("da igual"), larga)
    assert "ruta demasiado larga" in texto and "Documentos" in texto


@pytest.mark.parametrize(
    "error",
    [
        OSError(None, "bloqueado", None, 4551),
        OSError(None, "bloqueado", None, 4558),
        ImportError("DLL load failed while importing _core: Una directiva de Control de "
                    "aplicaciones bloqueó este archivo."),
        ImportError("DLL load failed: An Application Control policy has blocked this file."),
    ],
)
def test_reconoce_los_bloqueos_de_windows(error: BaseException, tmp_path: Path) -> None:
    assert integridad.es_bloqueo_de_windows(error)
    texto = integridad.explicar_fallo_de_carga("PyAV", error, _paquete(tmp_path))
    assert "Control inteligente de aplicaciones" in texto


def test_un_bloqueo_como_causa_tambien_cuenta() -> None:
    try:
        try:
            raise OSError(None, "bloqueado", None, 4551)
        except OSError as e:
            raise RuntimeError("al cargar el modelo") from e
    except RuntimeError as envoltorio:
        assert integridad.es_bloqueo_de_windows(envoltorio)
    assert not integridad.es_bloqueo_de_windows(OSError(None, "en uso", None, 32))
    assert not integridad.es_bloqueo_de_windows(ImportError("No module named 'av'"))


@pytest.mark.skipif(sys.platform != "win32", reason="flujos alternativos de NTFS")
def test_quita_la_marca_de_internet(tmp_path: Path) -> None:
    raiz = _paquete(tmp_path)
    marcados = [raiz / "voziris.exe", raiz / "_internal" / "av" / "_core.pyd"]
    for archivo in marcados:
        with open(f"{archivo}:Zone.Identifier", "w", encoding="utf-8") as z:
            z.write("[ZoneTransfer]\nZoneId=3\n")
    assert all(integridad.tiene_marca_de_internet(a) for a in marcados)
    assert integridad.quitar_marcas_de_internet(raiz) == 2
    assert not any(integridad.tiene_marca_de_internet(a) for a in marcados)
    assert (raiz / "voziris.exe").read_bytes().startswith(b"MZ")  # el contenido, intacto
    assert integridad.quitar_marcas_de_internet(raiz) == 0


@pytest.mark.skipif(sys.platform != "win32", reason="registro de Windows")
def test_control_inteligente_da_un_estado_conocido() -> None:
    assert integridad.control_inteligente() in (None, "activado", "evaluación", "desactivado")


def test_autoprueba_en_desarrollo_lo_carga_todo() -> None:
    informe = integridad.autoprueba(raiz=None)
    nombres = [p["nombre"] for p in informe["pruebas"]]
    assert nombres[: len(integridad.MODULOS)] == [m for m, _ in integridad.MODULOS]
    assert "decodificar" in nombres and "acceso directo" in nombres
    assert informe["ok"], integridad.informe_a_texto(informe)
    assert not informe["bloqueado"]
    json.dumps(informe)  # lo que escribe --informe


def test_autoprueba_dice_que_falta_pyav(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    raiz = _paquete(tmp_path)
    (raiz / "_internal" / "av.libs" / "avcodec-62-984de331.dll").unlink()
    monkeypatch.setitem(sys.modules, "av", None)  # import av → ImportError
    informe = integridad.autoprueba(raiz=raiz)
    fallos = {p["nombre"]: p["detalle"] for p in informe["pruebas"] if not p["ok"]}
    assert not informe["ok"]
    assert set(fallos) == {"paquete", "av"}  # sin PyAV ni se intenta decodificar
    assert "avcodec-62-984de331.dll" in fallos["av"]
    assert "decodificar" not in [p["nombre"] for p in informe["pruebas"]]


def test_comprobar_en_otro_proceso_lee_el_informe() -> None:
    orden = [sys.executable, "-m", "voziris"]
    informe = integridad.comprobar_en_otro_proceso(orden, espera_s=120)
    assert informe["ok"], integridad.informe_a_texto(informe)


def test_comprobar_en_otro_proceso_que_no_arranca(tmp_path: Path) -> None:
    informe = integridad.comprobar_en_otro_proceso([str(tmp_path / "no-existe.exe")])
    assert not informe["ok"] and not informe["bloqueado"]
    assert informe["pruebas"][0]["nombre"] == "arranque"
    assert "no arranca" in informe["pruebas"][0]["detalle"]


def test_comprobar_en_otro_proceso_que_muere_sin_informe() -> None:
    informe = integridad.comprobar_en_otro_proceso([sys.executable, "-c", "raise SystemExit(7)"])
    assert not informe["ok"]
    assert "código 7 sin dejar informe" in informe["pruebas"][0]["detalle"]


@pytest.mark.skipif(sys.platform != "win32", reason="WinError de Windows")
def test_comprobar_en_otro_proceso_bloqueado(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    def bloquea(*a: object, **k: object) -> None:
        raise OSError(None, "Una directiva de Control de aplicaciones bloqueó este archivo",
                      None, 4551)

    monkeypatch.setattr(subprocess, "run", bloquea)
    informe = integridad.comprobar_en_otro_proceso(["voziris.exe"])
    assert informe["bloqueado"] and not informe["ok"]
    assert "Control inteligente de aplicaciones" in integridad.fallos_del_informe(informe)[0]


# --- lo que encontró la revisión ---------------------------------------------------------


@pytest.mark.parametrize(
    "contenido",
    [
        "",  # vacío: daría «Los 0 archivos están completos» sin mirar nada
        "# solo la cabecera\n",
        "abc 12\n",  # línea a medias
        f"{'0' * 64} doce voziris.exe\n",  # tamaño que no es un número
        f"{'0' * 64} 12 _internal/x.dll\n",  # sin voziris.exe
    ],
)
def test_un_manifiesto_danado_es_un_paquete_danado(tmp_path: Path, contenido: str) -> None:
    raiz = _paquete(tmp_path)
    integridad.ruta_manifiesto(raiz).write_text(contenido, encoding="utf-8")
    with pytest.raises(integridad.ManifiestoDanado):
        integridad.leer_manifiesto(raiz)
    resultado = integridad.comprobar(raiz)
    assert not resultado.ok and not resultado.sin_manifiesto
    assert resultado.resumen() == r"Está dañado _internal\voziris-manifiesto.txt"
    informe = integridad.autoprueba(raiz=raiz)  # no lanza
    assert not informe["ok"]


def test_la_comprobacion_siempre_deja_informe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def revienta(**k: object) -> dict[str, object]:
        raise RuntimeError("algo que nadie esperaba")

    monkeypatch.setattr(integridad, "autoprueba", revienta)
    informe = tmp_path / "informe.json"
    assert integridad.comprobar_desde_argv(["--comprobar", "--informe", str(informe)]) == 1
    datos = json.loads(informe.read_text(encoding="utf-8"))
    assert not datos["ok"]
    assert datos["pruebas"][0]["detalle"] == "RuntimeError: algo que nadie esperaba"


def test_comprobar_responde_aunque_numpy_no_cargue(tmp_path: Path) -> None:
    """El .exe sin consola, con una DLL de numpy menos, se quedaba en el cuadro de PyInstaller."""
    import os
    import subprocess

    falso = tmp_path / "falsos" / "numpy"
    falso.mkdir(parents=True)
    (falso / "__init__.py").write_text(
        "raise ImportError('DLL load failed while importing _multiarray_umath: "
        "No se puede encontrar el módulo especificado.')\n", encoding="utf-8")
    informe = tmp_path / "informe.json"
    entorno = dict(os.environ, PYTHONIOENCODING="utf-8")
    entorno["PYTHONPATH"] = os.pathsep.join([str(falso.parent), os.environ.get("PYTHONPATH", "")])
    r = subprocess.run([sys.executable, "-m", "voziris", "--comprobar", "--informe", str(informe)],
                       env=entorno, capture_output=True, timeout=120)
    assert r.returncode == 1, r.stderr.decode("utf-8", "replace")[-500:]
    fallos = {p["nombre"]: p["detalle"] for p in json.loads(informe.read_text("utf-8"))["pruebas"]
              if not p["ok"]}
    assert "numpy" in fallos and "_multiarray_umath" in fallos["numpy"]
