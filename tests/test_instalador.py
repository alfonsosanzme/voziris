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


def test_actualizar_no_pisa_el_historial_de_la_instalada(tmp_path: Path, registro: int) -> None:
    """Quien abre la voziris.exe nueva antes de instalarla deja en ella un historial de una línea.

    Copiarlo encima borraba el de la instalada (visto al verificar la 0.1.1).
    """
    origen = _paquete(tmp_path)
    destino = tmp_path / "Programs" / "Voziris"
    kwargs = dict(menu_inicio=tmp_path / "menu", startup=tmp_path / "st",
                  raiz_registro=registro, clave_registro=CLAVE_PRUEBAS,
                  base_menu=BASE_MENU_PRUEBAS)
    instalador.instalar(origen, destino, **kwargs)  # type: ignore[arg-type]
    (destino / "historial").mkdir()
    meses = "".join(f'{{"texto": "dictado {i}"}}\n' for i in range(300))
    (destino / "historial" / "dictados.jsonl").write_text(meses, encoding="utf-8")
    (origen / "historial").mkdir()
    (origen / "historial" / "dictados.jsonl").write_text('{"texto": "prueba"}\n', encoding="utf-8")
    (origen / "historial" / "solo-en-la-nueva.txt").write_text("x", encoding="utf-8")
    instalador.instalar(origen, destino, **kwargs)  # type: ignore[arg-type]
    assert (destino / "historial" / "dictados.jsonl").read_text(encoding="utf-8") == meses
    assert (destino / "historial" / "solo-en-la-nueva.txt").exists()  # lo que no está, sí viaja


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


# --- VOZ-81: nada se da por bueno sin comprobarlo ----------------------------------------


def _kwargs(tmp_path: Path, registro: int) -> dict[str, object]:
    return dict(menu_inicio=tmp_path / "menu", startup=tmp_path / "st",
                raiz_registro=registro, clave_registro=CLAVE_PRUEBAS,
                base_menu=BASE_MENU_PRUEBAS)


def _paquete_con_manifiesto(carpeta: Path) -> Path:
    from voziris import integridad

    origen = _paquete(carpeta)
    (origen / "_internal" / "av.libs").mkdir()
    (origen / "_internal" / "av.libs" / "avcodec-62.dll").write_bytes(b"a" * 4096)
    integridad.escribir_manifiesto(origen)
    return origen


@pytest.fixture
def sin_esperas(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(instalador, "ESPERA_REINTENTO_S", 0.0)


def _instalacion_anterior(tmp_path: Path, registro: int) -> tuple[Path, Path]:
    """Una instalación v1 hecha y un origen v2 listo para reinstalar encima."""
    from voziris import integridad

    origen = _paquete_con_manifiesto(tmp_path)
    destino = tmp_path / "Programs" / "Voziris"
    instalador.instalar(origen, destino, **_kwargs(tmp_path, registro))  # type: ignore[arg-type]
    (destino / "config.toml").write_text("mio", encoding="utf-8")
    (origen / "voziris.exe").write_bytes(b"MZ v2")
    (origen / "_internal" / "av.libs" / "avcodec-62.dll").write_bytes(b"b" * 5000)
    integridad.escribir_manifiesto(origen)
    return origen, destino


def _es_la_v1(destino: Path) -> bool:
    return (
        (destino / "voziris.exe").read_bytes() == b"MZ falso"
        and (destino / "_internal" / "av.libs" / "avcodec-62.dll").read_bytes() == b"a" * 4096
        and (destino / "config.toml").read_text(encoding="utf-8") == "mio"
    )


def _sin_restos(destino: Path) -> bool:
    nuevo = destino.with_name(destino.name + instalador.SUFIJO_NUEVO)
    return not nuevo.exists() and not list(destino.glob(f"*{instalador.SUFIJO_VIEJO}*"))


def test_origen_incompleto_no_instala_nada_y_dice_que_falta(
    tmp_path: Path, registro: int
) -> None:
    import winreg

    origen = _paquete_con_manifiesto(tmp_path)
    (origen / "_internal" / "av.libs" / "avcodec-62.dll").unlink()  # el antivirus
    destino = tmp_path / "Programs" / "Voziris"
    vistos: list[str] = []
    with pytest.raises(instalador.InstalacionFallida) as fallo:
        instalador.instalar(origen, destino, al_progresar=lambda m, f: vistos.append(m),
                            **_kwargs(tmp_path, registro))  # type: ignore[arg-type]
    assert str(fallo.value).startswith("La carpeta desde la que instalas está incompleta")
    assert r"Falta _internal\av.libs\avcodec-62.dll" in str(fallo.value)
    assert "Copiando el programa…" not in vistos  # se para antes de copiar nada
    assert not destino.exists()
    assert not (tmp_path / "menu" / instalador.ACCESO_MENU).exists()
    with pytest.raises(FileNotFoundError):
        winreg.OpenKey(registro, CLAVE_PRUEBAS)


def test_reinstalar_con_la_copia_rota_deja_la_anterior(
    tmp_path: Path, registro: int, monkeypatch: pytest.MonkeyPatch, sin_esperas: None
) -> None:
    origen, destino = _instalacion_anterior(tmp_path, registro)
    copiar = instalador._copiar_archivo

    def antivirus(fuente: Path, copia: Path) -> None:
        if fuente.name == "avcodec-62.dll":
            raise PermissionError(13, "El proceso no tiene acceso al archivo", str(copia), 32)
        copiar(fuente, copia)

    monkeypatch.setattr(instalador, "_copiar_archivo", antivirus)
    with pytest.raises(instalador.InstalacionFallida) as fallo:
        instalador.instalar(origen, destino, **_kwargs(tmp_path, registro))  # type: ignore[arg-type]
    mensaje = str(fallo.value)
    assert "avcodec-62.dll" in mensaje and "antivirus" in mensaje
    assert "sigue como estaba" in mensaje
    assert "[WinError" not in mensaje and "\\\\" not in mensaje  # nada del repr de shutil.Error
    assert _es_la_v1(destino) and _sin_restos(destino)


def test_si_la_copia_no_arranca_no_se_cambia_nada(
    tmp_path: Path, registro: int
) -> None:
    origen, destino = _instalacion_anterior(tmp_path, registro)
    probadas: list[Path] = []

    def no_arranca(exe: Path) -> None:
        probadas.append(exe)
        assert exe.read_bytes() == b"MZ v2"  # se prueba la copia nueva, no la vieja
        raise instalador.InstalacionFallida("no carga PyAV")

    with pytest.raises(instalador.InstalacionFallida, match="no carga PyAV"):
        instalador.instalar(origen, destino, comprobar_copia=no_arranca,
                            **_kwargs(tmp_path, registro))  # type: ignore[arg-type]
    assert probadas == [destino.with_name("Voziris.nuevo") / "voziris.exe"]
    assert _es_la_v1(destino) and _sin_restos(destino)


def test_reinstalar_bien_cambia_el_programa_y_no_deja_restos(
    tmp_path: Path, registro: int
) -> None:
    origen, destino = _instalacion_anterior(tmp_path, registro)
    (destino / "_internal" / "sobra-de-la-v1.dll").write_bytes(b"v1")
    instalador.instalar(origen, destino, comprobar_copia=lambda exe: None,
                        **_kwargs(tmp_path, registro))  # type: ignore[arg-type]
    assert (destino / "voziris.exe").read_bytes() == b"MZ v2"
    assert (destino / "_internal" / "av.libs" / "avcodec-62.dll").read_bytes() == b"b" * 5000
    assert not (destino / "_internal" / "sobra-de-la-v1.dll").exists()  # _internal entero nuevo
    assert (destino / "config.toml").read_text(encoding="utf-8") == "mio"
    assert _sin_restos(destino)


def test_si_falla_al_sustituir_vuelve_la_anterior(
    tmp_path: Path, registro: int, monkeypatch: pytest.MonkeyPatch, sin_esperas: None
) -> None:
    import os

    origen, destino = _instalacion_anterior(tmp_path, registro)
    reemplazar = os.replace

    def falla_al_poner_internal(fuente: object, copia: object) -> None:
        ruta = Path(str(fuente))
        if ruta.parent.name == "Voziris.nuevo" and ruta.name == "_internal":
            raise PermissionError(13, "Acceso denegado", str(copia), 5)
        reemplazar(fuente, copia)  # type: ignore[arg-type]

    monkeypatch.setattr(instalador.os, "replace", falla_al_poner_internal)
    with pytest.raises(instalador.InstalacionFallida, match="otro programa tiene abierto"):
        instalador.instalar(origen, destino, **_kwargs(tmp_path, registro))  # type: ignore[arg-type]
    monkeypatch.undo()
    assert _es_la_v1(destino) and _sin_restos(destino)


def test_limpia_los_restos_de_una_instalacion_interrumpida(
    tmp_path: Path, registro: int
) -> None:
    origen, destino = _instalacion_anterior(tmp_path, registro)
    nuevo = destino.with_name("Voziris.nuevo")
    (nuevo / "_internal").mkdir(parents=True)
    (nuevo / "_internal" / "a-medias.dll").write_bytes(b"x")
    (destino / "_internal.viejo-20261001080000").mkdir()
    instalador.instalar(origen, destino, **_kwargs(tmp_path, registro))  # type: ignore[arg-type]
    assert _sin_restos(destino)
    assert (destino / "voziris.exe").read_bytes() == b"MZ v2"


def test_sin_sitio_en_el_disco_no_empieza(
    tmp_path: Path, registro: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil
    from collections import namedtuple

    Uso = namedtuple("Uso", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda ruta: Uso(10**9, 10**9, 10 * 2**20))
    origen = _paquete_con_manifiesto(tmp_path)
    destino = tmp_path / "Programs" / "Voziris"
    with pytest.raises(instalador.InstalacionFallida, match="No hay sitio en el disco"):
        instalador.instalar(origen, destino, **_kwargs(tmp_path, registro))  # type: ignore[arg-type]
    assert not destino.exists()


def test_los_errores_pasajeros_se_reintentan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sin_esperas: None
) -> None:
    import shutil

    intentos: list[int] = []
    copiar = shutil.copy2

    def ocupado_dos_veces(fuente: object, copia: object) -> object:
        intentos.append(1)
        if len(intentos) <= 2:
            raise PermissionError(13, "en uso", str(copia), 32)
        return copiar(fuente, copia)  # type: ignore[arg-type]

    monkeypatch.setattr(shutil, "copy2", ocupado_dos_veces)
    (tmp_path / "a.dll").write_bytes(b"dll")
    instalador._copiar_archivo(tmp_path / "a.dll", tmp_path / "b.dll")
    assert (tmp_path / "b.dll").read_bytes() == b"dll" and len(intentos) == 3

    def lleno(fuente: object, copia: object) -> object:
        raise OSError(28, "No queda espacio", str(copia), 112)

    monkeypatch.setattr(shutil, "copy2", lleno)
    with pytest.raises(OSError) as e:
        instalador._copiar_archivo(tmp_path / "a.dll", tmp_path / "c.dll")
    assert "disco está lleno" in instalador.explicar_error_de_copia(e.value)


def test_explicar_error_de_copia_de_shutil() -> None:
    import shutil

    fallos = [
        (rf"C:\\x\\_internal\\av.libs\\lib{i}.dll", rf"C:\\y\\lib{i}.dll",
         f"[WinError 206] El nombre del archivo o la extensión es demasiado largo: 'lib{i}'")
        for i in range(5)
    ]
    texto = instalador.explicar_error_de_copia(shutil.Error(fallos))
    assert texto.startswith(
        "No se pudieron copiar 5 archivos (lib0.dll, lib1.dll, lib2.dll y 2 más)"
    )
    assert "ruta es demasiado larga" in texto


@pytest.mark.parametrize("bloqueado", [True, False])
def test_comprobar_arrancando(monkeypatch: pytest.MonkeyPatch, bloqueado: bool) -> None:
    from voziris import integridad

    informe = {
        "ok": False, "bloqueado": bloqueado,
        "pruebas": [
            {"nombre": "av", "ok": False, "detalle": "No se pudo cargar PyAV. Falta x.dll"},
            {"nombre": "numpy", "ok": True, "detalle": "2.5"},
            {"nombre": "onnxruntime", "ok": False, "detalle": "otro"},
        ],
    }
    monkeypatch.setattr(integridad, "comprobar_en_otro_proceso", lambda orden, *a: informe)
    with pytest.raises(instalador.InstalacionFallida) as fallo:
        instalador.comprobar_arrancando(Path("voziris.exe"))
    assert fallo.value.bloqueado is bloqueado
    if bloqueado:
        assert "Control inteligente de aplicaciones" in str(fallo.value)
        assert "Anclar a Inicio" in str(fallo.value)
    else:
        assert "Falta x.dll" in str(fallo.value) and "1 problema(s) más" in str(fallo.value)
    monkeypatch.setattr(integridad, "comprobar_en_otro_proceso",
                        lambda orden, *a: {"ok": True, "pruebas": []})
    instalador.comprobar_arrancando(Path("voziris.exe"))  # no lanza


def test_comprobar_arrancando_con_un_archivo_que_falta_da_solo_la_causa(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from voziris import integridad

    informe = {
        "ok": False, "bloqueado": False,
        "pruebas": [
            {"nombre": "paquete", "ok": False, "resumen": r"Falta _internal\av.libs\x.dll",
             "detalle": r"Falta _internal\av.libs\x.dll. Vuelve a extraer el ZIP entero"},
            {"nombre": "av", "ok": False, "detalle": "No se pudo cargar PyAV. Falta x.dll"},
        ],
    }
    monkeypatch.setattr(integridad, "comprobar_en_otro_proceso", lambda orden, *a: informe)
    with pytest.raises(instalador.InstalacionFallida) as fallo:
        instalador.comprobar_arrancando(tmp_path / "Voziris.nuevo" / "voziris.exe")
    mensaje = str(fallo.value)
    assert r"Falta _internal\av.libs\x.dll." in mensaje
    assert "extraer el ZIP" not in mensaje  # el origen estaba bien: es la copia
    assert f"excepción para {tmp_path / 'Voziris'} " in mensaje
    assert "problema(s) más" not in mensaje


def test_la_instalacion_quita_la_marca_de_internet(tmp_path: Path, registro: int) -> None:
    from voziris import integridad

    origen = _paquete_con_manifiesto(tmp_path)
    for archivo in (origen / "voziris.exe", origen / "_internal" / "av.libs" / "avcodec-62.dll"):
        with open(f"{archivo}:Zone.Identifier", "w", encoding="utf-8") as z:
            z.write("[ZoneTransfer]\nZoneId=3\n")
    destino = tmp_path / "Programs" / "Voziris"
    instalador.instalar(origen, destino, **_kwargs(tmp_path, registro))  # type: ignore[arg-type]
    instalados = [p for p in destino.rglob("*") if p.is_file()]
    assert instalados and not any(integridad.tiene_marca_de_internet(p) for p in instalados)
    assert not integridad.tiene_marca_de_internet(tmp_path / "menu" / instalador.ACCESO_MENU)
    assert integridad.tiene_marca_de_internet(origen / "voziris.exe")  # el origen no se toca


def test_avisa_del_progreso_y_cancelar_antes_de_sustituir_no_cambia_nada(
    tmp_path: Path, registro: int
) -> None:
    origen, destino = _instalacion_anterior(tmp_path, registro)
    vistos: list[str] = []

    class Cancelado(Exception):
        pass

    def progreso(mensaje: str, fraccion: float | None) -> None:
        vistos.append(mensaje)
        if mensaje.startswith("Sustituyendo"):
            raise Cancelado

    with pytest.raises(Cancelado):
        instalador.instalar(origen, destino, al_progresar=progreso,
                            **_kwargs(tmp_path, registro))  # type: ignore[arg-type]
    assert vistos[0].startswith("Comprobando que el paquete")
    assert "Copiando el programa…" in vistos
    assert _es_la_v1(destino) and _sin_restos(destino)


# --- lo que encontró la revisión ---------------------------------------------------------


def test_origen_con_el_manifiesto_danado_no_instala(tmp_path: Path, registro: int) -> None:
    from voziris import integridad

    origen = _paquete_con_manifiesto(tmp_path)
    integridad.ruta_manifiesto(origen).write_text("abc 12\n", encoding="utf-8")  # a medias
    destino = tmp_path / "Programs" / "Voziris"
    with pytest.raises(instalador.InstalacionFallida, match="voziris-manifiesto.txt"):
        instalador.instalar(origen, destino, **_kwargs(tmp_path, registro))  # type: ignore[arg-type]
    assert not destino.exists()


def test_la_abierta_se_cierra_solo_si_se_va_a_sustituir(tmp_path: Path, registro: int) -> None:
    origen, destino = _instalacion_anterior(tmp_path, registro)
    cerradas: list[int] = []

    def no_arranca(exe: Path) -> None:
        raise instalador.InstalacionFallida("no carga PyAV")

    with pytest.raises(instalador.InstalacionFallida):
        instalador.instalar(origen, destino, comprobar_copia=no_arranca,
                            antes_de_sustituir=lambda: cerradas.append(1),
                            **_kwargs(tmp_path, registro))  # type: ignore[arg-type]
    assert cerradas == []  # el usuario sigue con su Voziris abierta
    instalador.instalar(origen, destino, antes_de_sustituir=lambda: cerradas.append(1),
                        **_kwargs(tmp_path, registro))  # type: ignore[arg-type]
    assert cerradas == [1]


def test_si_no_se_puede_deshacer_lo_dice_y_la_siguiente_lo_recupera(
    tmp_path: Path, registro: int, monkeypatch: pytest.MonkeyPatch, sin_esperas: None
) -> None:
    import os

    origen, destino = _instalacion_anterior(tmp_path, registro)
    reemplazar = os.replace

    def bloqueado(fuente: object, copia: object) -> None:
        ruta, a = Path(str(fuente)), Path(str(copia))
        if ruta.parent.name == "Voziris.nuevo" and ruta.name == "_internal":
            raise PermissionError(13, "Acceso denegado", str(copia), 5)
        if ".viejo-" in ruta.name and a.name == "_internal":  # y tampoco se deja devolver
            raise PermissionError(13, "Acceso denegado", str(copia), 5)
        reemplazar(fuente, copia)  # type: ignore[arg-type]

    monkeypatch.setattr(instalador.os, "replace", bloqueado)
    with pytest.raises(instalador.InstalacionFallida) as fallo:
        instalador.instalar(origen, destino, **_kwargs(tmp_path, registro))  # type: ignore[arg-type]
    monkeypatch.undo()
    mensaje = str(fallo.value)
    assert "tampoco dejarla como estaba" in mensaje and "sigue como estaba" not in mensaje
    assert not (destino / "_internal").exists()
    assert len(list(destino.glob("_internal.viejo-*"))) == 1  # apartado, no perdido

    def no_arranca(exe: Path) -> None:
        raise instalador.InstalacionFallida("bloqueado por Windows")

    with pytest.raises(instalador.InstalacionFallida):  # la siguiente también falla…
        instalador.instalar(origen, destino, comprobar_copia=no_arranca,
                            **_kwargs(tmp_path, registro))  # type: ignore[arg-type]
    assert _es_la_v1(destino) and _sin_restos(destino)  # …pero la anterior ha vuelto
