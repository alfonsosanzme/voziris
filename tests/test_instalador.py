"""Tests de la instalación por usuario. Carpetas temporales y una clave de registro de pruebas."""

from __future__ import annotations

import contextlib
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from voziris import instalador

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="accesos directos y registro")

CLAVE_PRUEBAS = r"Software\Voziris-pruebas\Uninstall\Voziris"
BASE_MENU_PRUEBAS = r"Software\Voziris-pruebas\SystemFileAssociations"


@pytest.fixture
def registro() -> Iterator[int]:
    import winreg

    yield winreg.HKEY_CURRENT_USER
    instalador.quitar_menu_contextual(raiz=winreg.HKEY_CURRENT_USER, base=BASE_MENU_PRUEBAS)
    for clave in (
        CLAVE_PRUEBAS,
        r"Software\Voziris-pruebas\Uninstall",
        *(f"{BASE_MENU_PRUEBAS}\\{ext}\\shell" for ext in _EXTENSIONES),
        *(f"{BASE_MENU_PRUEBAS}\\{ext}" for ext in _EXTENSIONES),
        BASE_MENU_PRUEBAS,
        r"Software\Voziris-pruebas",
    ):
        with contextlib.suppress(FileNotFoundError, OSError):
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, clave)


def _extensiones() -> tuple[str, ...]:
    from voziris.archivos import FORMATOS

    return FORMATOS


_EXTENSIONES = _extensiones()


def _paquete(carpeta: Path) -> Path:
    origen = carpeta / "descomprimido"
    (origen / "_internal" / "numpy").mkdir(parents=True)
    (origen / "voziris.exe").write_bytes(b"MZ falso")
    (origen / "_internal" / "numpy" / "x.pyd").write_bytes(b"x" * 2048)
    (origen / "config.toml").write_text('[general]\nmotor = "local"\n', encoding="utf-8")
    (origen / "modelos" / "silero-vad").mkdir(parents=True)
    (origen / "modelos" / "silero-vad" / "silero_vad.onnx").write_bytes(b"onnx")
    return origen


def _leer_valor(raiz: int, clave: str, nombre: str) -> object:
    import winreg

    with winreg.OpenKey(raiz, clave) as k:
        return winreg.QueryValueEx(k, nombre)[0]


def test_instalar_copia_programa_y_datos_y_registra(tmp_path: Path, registro: int) -> None:
    origen = _paquete(tmp_path)
    destino, menu, startup = tmp_path / "Programs" / "Voziris", tmp_path / "menu", tmp_path / "st"
    startup.mkdir()
    (startup / instalador.ACCESO_STARTUP).write_bytes(b"lnk viejo hacia la copia portable")

    instalado = instalador.instalar(
        origen, destino, menu_inicio=menu, startup=startup,
        raiz_registro=registro, clave_registro=CLAVE_PRUEBAS, base_menu=BASE_MENU_PRUEBAS,
        version="9.9.9",
    )
    assert instalado == destino.resolve()
    assert (destino / "voziris.exe").read_bytes() == b"MZ falso"
    assert (destino / "_internal" / "numpy" / "x.pyd").exists()
    assert (destino / "config.toml").exists()  # la configuración viaja
    assert (destino / "modelos" / "silero-vad" / "silero_vad.onnx").exists()  # y el modelo
    assert (menu / instalador.ACCESO_MENU).exists()  # es lo que encuentra la búsqueda
    lnk = (startup / instalador.ACCESO_STARTUP).read_bytes()
    assert lnk != b"lnk viejo hacia la copia portable"  # reescrito hacia la instalada
    assert _leer_valor(registro, CLAVE_PRUEBAS, "DisplayName") == "Voziris"
    assert _leer_valor(registro, CLAVE_PRUEBAS, "DisplayVersion") == "9.9.9"
    assert str(destino) in str(_leer_valor(registro, CLAVE_PRUEBAS, "UninstallString"))
    assert _leer_valor(registro, CLAVE_PRUEBAS, "NoModify") == 1
    assert int(_leer_valor(registro, CLAVE_PRUEBAS, "EstimatedSize")) >= 2

    assert instalador.esta_instalado(destino / "voziris.exe", destino)
    assert not instalador.esta_instalado(origen / "voziris.exe", destino)
    # Y el botón derecho del Explorador (VOZ-72), en cascada con dos verbos.
    assert instalador.hay_menu_contextual(registro, BASE_MENU_PRUEBAS)
    clave = f"{BASE_MENU_PRUEBAS}\\.m4a\\shell\\Voziris"
    assert _leer_valor(registro, clave, "MUIVerb") == "Transcribir con Voziris"
    assert _leer_valor(registro, clave, "SubCommands") == ""
    mandato = str(_leer_valor(registro, clave + r"\shell\02varios\command", ""))
    assert mandato.startswith(f'"{destino / "voziris.exe"}" --transcribir "%1" ')
    assert "--hablantes auto --ventana" in mandato
    mandato_uno = str(_leer_valor(registro, clave + r"\shell\01uno\command", ""))
    assert "--hablantes 1 --ventana" in mandato_uno


def test_reinstalar_no_pisa_la_configuracion_ni_el_modelo(tmp_path: Path, registro: int) -> None:
    origen = _paquete(tmp_path)
    destino = tmp_path / "Programs" / "Voziris"
    kwargs = dict(menu_inicio=tmp_path / "menu", startup=tmp_path / "st",
                  raiz_registro=registro, clave_registro=CLAVE_PRUEBAS,
                  base_menu=BASE_MENU_PRUEBAS)
    instalador.instalar(origen, destino, **kwargs)  # type: ignore[arg-type]
    (destino / "config.toml").write_text("mio", encoding="utf-8")
    (origen / "_internal" / "nuevo.dll").write_bytes(b"v2")
    (origen / "voziris.exe").write_bytes(b"MZ v2")
    instalador.instalar(origen, destino, **kwargs)  # type: ignore[arg-type]
    assert (destino / "config.toml").read_text(encoding="utf-8") == "mio"
    assert (destino / "voziris.exe").read_bytes() == b"MZ v2"  # el programa sí se actualiza
    assert (destino / "_internal" / "nuevo.dll").exists()


def test_desinstalar_quita_todo(tmp_path: Path, registro: int) -> None:
    import winreg

    origen = _paquete(tmp_path)
    destino, menu, startup = tmp_path / "Programs" / "Voziris", tmp_path / "menu", tmp_path / "st"
    instalador.instalar(origen, destino, menu_inicio=menu, startup=startup,
                        raiz_registro=registro, clave_registro=CLAVE_PRUEBAS,
                        base_menu=BASE_MENU_PRUEBAS)
    assert instalador.hay_menu_contextual(registro, BASE_MENU_PRUEBAS)
    instalador.desinstalar(destino, menu_inicio=menu, startup=startup,
                           raiz_registro=registro, clave_registro=CLAVE_PRUEBAS,
                           base_menu=BASE_MENU_PRUEBAS, aplazado=False)
    assert not (menu / instalador.ACCESO_MENU).exists()
    assert not destino.exists()
    with pytest.raises(FileNotFoundError):
        winreg.OpenKey(registro, CLAVE_PRUEBAS)
    assert not instalador.hay_menu_contextual(registro, BASE_MENU_PRUEBAS)
    # Desinstalar dos veces no revienta.
    instalador.desinstalar(destino, menu_inicio=menu, startup=startup,
                           raiz_registro=registro, clave_registro=CLAVE_PRUEBAS,
                           base_menu=BASE_MENU_PRUEBAS, aplazado=False)


def test_menu_contextual_portable_con_python(registro: int) -> None:
    """Sin instalar (`--menu-contextual`): la orden puede ser `python -m voziris`."""
    instalador.registrar_menu_contextual(
        [r"C:\Program Files\Python\python.exe", "-m", "voziris"],
        extensiones=(".mp3",), raiz=registro, base=BASE_MENU_PRUEBAS,
    )
    mandato = str(_leer_valor(
        registro, f"{BASE_MENU_PRUEBAS}\\.mp3\\shell\\Voziris\\shell\\01uno\\command", ""
    ))
    assert mandato.startswith('"C:\\Program Files\\Python\\python.exe" -m voziris --transcribir')
    assert not instalador.hay_menu_contextual(registro, BASE_MENU_PRUEBAS)  # .m4a no se pidió
    instalador.quitar_menu_contextual((".mp3",), raiz=registro, base=BASE_MENU_PRUEBAS)
    with pytest.raises(FileNotFoundError):
        _leer_valor(registro, f"{BASE_MENU_PRUEBAS}\\.mp3\\shell\\Voziris", "MUIVerb")


def test_origen_que_no_es_un_paquete(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="voziris.exe"):
        instalador.instalar(tmp_path, tmp_path / "destino")
