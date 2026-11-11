"""Tests de la instalación por usuario. Carpetas temporales y una clave de registro de pruebas."""

from __future__ import annotations

import contextlib
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

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
    with pytest.raises(instalador.InstalacionFallida, match="Algo tiene abierta la carpeta"):
        instalador.instalar(origen, destino, **_kwargs(tmp_path, registro))  # type: ignore[arg-type]
    monkeypatch.undo()
    assert _es_la_v1(destino) and _sin_restos(destino)


# --- lo que impide cambiar el programa (VOZ-85) -------------------------------------------


def test_lo_que_sigue_vivo_de_la_carpeta_se_cierra_sin_preguntar_y_se_cambia_el_programa(
    tmp_path: Path, registro: int, monkeypatch: pytest.MonkeyPatch, sin_esperas: None,
    copia_viva: Any,
) -> None:
    """Como el duplicado del 31 de octubre. El directorio de trabajo es lo que retiene aquí."""
    import os

    origen, destino = _instalacion_anterior(tmp_path, registro)
    interno = destino / "_internal"
    duplicado = copia_viva(interno / "duplicado.exe", cwd=interno)
    with pytest.raises(PermissionError):  # lo que fallaba
        os.replace(interno, destino / "_internal.apartado")
    monkeypatch.setattr(instalador, "ESPERA_CIERRE_S", 0.1)
    cerrada: list[bool] = []

    instalador.instalar(
        origen, destino,
        antes_de_sustituir=lambda: cerrada.append(duplicado.poll() is None),
        preguntar_cerrar=lambda p: pytest.fail(f"no debería preguntar: {p}"),
        preguntar_reintento=lambda m: pytest.fail(f"no debería hacer falta: {m}"),
        **_kwargs(tmp_path, registro),  # type: ignore[arg-type]
    )

    assert duplicado.wait(timeout=5) == 1
    assert cerrada == [True]  # primero la bandeja, por si lo que sigue vivo se va con ella
    assert (destino / "voziris.exe").read_bytes() == b"MZ v2"
    assert _sin_restos(destino)


def test_lo_que_transcribe_no_se_cierra_sin_preguntar(
    tmp_path: Path, registro: int, monkeypatch: pytest.MonkeyPatch, sin_esperas: None,
    copia_viva: Any,
) -> None:
    origen, destino = _instalacion_anterior(tmp_path, registro)
    exe = destino / "_internal" / "transcribe.exe"
    proceso = copia_viva(exe, "--transcribir", r"C:\mis cosas\reunión 1.m4a", "--ventana")
    monkeypatch.setattr(instalador, "ESPERA_CIERRE_S", 0.1)
    preguntas: list[str] = []
    bandeja: list[str] = []

    def no(pregunta: str) -> bool:
        preguntas.append(pregunta)
        return False

    with pytest.raises(instalador.InstalacionCancelada):
        instalador.instalar(origen, destino, preguntar_cerrar=no,
                            antes_de_sustituir=lambda: bandeja.append("cerrada"),
                            **_kwargs(tmp_path, registro))  # type: ignore[arg-type]
    assert proceso.poll() is None  # sigue transcribiendo
    assert bandeja == []  # y la bandeja, abierta: se pregunta antes de cerrar nada
    assert "«reunión 1.m4a»" in preguntas[0] and f"PID {proceso.pid}" in preguntas[0]
    assert str(exe) in preguntas[0] and "se perdería" in preguntas[0]
    assert _es_la_v1(destino) and _sin_restos(destino)

    with pytest.raises(instalador.InstalacionCancelada):  # sin a quién preguntar, tampoco
        instalador.instalar(origen, destino, **_kwargs(tmp_path, registro))  # type: ignore[arg-type]
    assert proceso.poll() is None

    instalador.instalar(origen, destino, preguntar_cerrar=lambda p: True,
                        antes_de_sustituir=lambda: bandeja.append("cerrada"),
                        **_kwargs(tmp_path, registro))  # type: ignore[arg-type]
    assert proceso.wait(timeout=5) == 1
    assert bandeja == ["cerrada"]
    assert (destino / "voziris.exe").read_bytes() == b"MZ v2"


def test_si_no_se_puede_leer_su_orden_tambien_se_pregunta(
    tmp_path: Path, registro: int, monkeypatch: pytest.MonkeyPatch, copia_viva: Any
) -> None:
    origen, destino = _instalacion_anterior(tmp_path, registro)
    proceso = copia_viva(destino / "_internal" / "raro.exe")
    monkeypatch.setattr(instalador.winapi, "orden_de_proceso", lambda pid: None)
    preguntas: list[str] = []

    def no(pregunta: str) -> bool:
        preguntas.append(pregunta)
        return False

    with pytest.raises(instalador.InstalacionCancelada):
        instalador.instalar(origen, destino, preguntar_cerrar=no,
                            **_kwargs(tmp_path, registro))  # type: ignore[arg-type]
    assert "puede estar transcribiendo" in preguntas[0] and f"PID {proceso.pid}" in preguntas[0]


def test_si_algo_lo_retiene_dice_quien_y_reintenta_sin_volver_a_copiar(
    tmp_path: Path, registro: int, monkeypatch: pytest.MonkeyPatch, sin_esperas: None,
    hijo_que_retiene: Any,
) -> None:
    from voziris import winapi

    origen, destino = _instalacion_anterior(tmp_path, registro)
    hijo = hijo_que_retiene(destino / "_internal" / "av.libs" / "avcodec-62.dll")
    ruta = winapi.ruta_de_proceso(hijo.pid)
    assert ruta is not None
    copiar = instalador._copiar_archivo
    copiados: list[Path] = []

    def contar(fuente: Path, copia: Path) -> None:
        copiados.append(fuente)
        copiar(fuente, copia)

    monkeypatch.setattr(instalador, "_copiar_archivo", contar)
    explicaciones: list[tuple[str, int]] = []

    def reintentar(explicacion: str) -> bool:
        explicaciones.append((explicacion, len(copiados)))
        assert _es_la_v1(destino)  # mientras se pregunta, lo de antes sigue en su sitio
        hijo.soltar()
        return True

    instalador.instalar(origen, destino, preguntar_reintento=reintentar,
                        **_kwargs(tmp_path, registro))  # type: ignore[arg-type]

    assert len(explicaciones) == 1
    explicacion, copiados_al_preguntar = explicaciones[0]
    assert f"PID {hijo.pid}" in explicacion and "pulsa Reintentar" in explicacion
    assert ruta in explicacion and "Administrador de tareas" in explicacion
    assert explicacion.endswith("No se ha cambiado nada: la versión anterior sigue como estaba.")
    assert len(copiados) == copiados_al_preguntar  # con lo copiado, sin copiar otra vez
    assert (destino / "voziris.exe").read_bytes() == b"MZ v2"
    assert _sin_restos(destino)


def test_si_lo_retenido_es_la_copia_nueva_tambien_se_dice_quien(
    tmp_path: Path, registro: int, sin_esperas: None, hijo_que_retiene: Any
) -> None:
    """Lo que haría el antivirus con la copia recién hecha. Y lo anterior vuelve a su sitio."""
    origen, destino = _instalacion_anterior(tmp_path, registro)
    hijos: list[Any] = []
    explicaciones: list[str] = []

    def comprobar(exe: Path) -> None:  # lo último que se hace con la copia antes del cambio
        hijos.append(hijo_que_retiene(exe.parent / "_internal" / "av.libs" / "avcodec-62.dll"))

    def reintentar(explicacion: str) -> bool:
        explicaciones.append(explicacion)
        assert _es_la_v1(destino)
        hijos[0].soltar()
        return True

    instalador.instalar(origen, destino, comprobar_copia=comprobar,
                        preguntar_reintento=reintentar,
                        **_kwargs(tmp_path, registro))  # type: ignore[arg-type]

    assert len(explicaciones) == 1 and f"PID {hijos[0].pid}" in explicaciones[0]
    assert (destino / "voziris.exe").read_bytes() == b"MZ v2"
    assert _sin_restos(destino)


def test_cancelar_en_el_reintento_deja_la_anterior(
    tmp_path: Path, registro: int, sin_esperas: None, hijo_que_retiene: Any
) -> None:
    origen, destino = _instalacion_anterior(tmp_path, registro)
    hijo_que_retiene(destino / "_internal" / "av.libs" / "avcodec-62.dll")
    with pytest.raises(instalador.InstalacionCancelada):
        instalador.instalar(origen, destino, preguntar_reintento=lambda m: False,
                            **_kwargs(tmp_path, registro))  # type: ignore[arg-type]
    assert _es_la_v1(destino) and _sin_restos(destino)


def test_lo_que_se_retiene_al_poner_lo_nuevo_tambien_se_reintenta(
    tmp_path: Path, registro: int, monkeypatch: pytest.MonkeyPatch, sin_esperas: None
) -> None:
    """Falla con voziris.exe ya puesto: tiene que volver a la copia para poder reintentar."""
    import os

    origen, destino = _instalacion_anterior(tmp_path, registro)
    reemplazar = os.replace
    arreglado: list[bool] = []

    def falla_al_poner_internal(fuente: object, copia: object) -> None:
        ruta = Path(str(fuente))
        if not arreglado and ruta.parent.name == "Voziris.nuevo" and ruta.name == "_internal":
            raise PermissionError(13, "Acceso denegado", str(fuente), 5)
        reemplazar(fuente, copia)  # type: ignore[arg-type]

    def reintentar(explicacion: str) -> bool:
        assert _es_la_v1(destino)
        assert (destino.with_name("Voziris.nuevo") / "voziris.exe").read_bytes() == b"MZ v2"
        arreglado.append(True)
        return True

    monkeypatch.setattr(instalador.os, "replace", falla_al_poner_internal)
    instalador.instalar(origen, destino, preguntar_reintento=reintentar,
                        **_kwargs(tmp_path, registro))  # type: ignore[arg-type]
    monkeypatch.undo()
    assert (destino / "voziris.exe").read_bytes() == b"MZ v2"
    assert _sin_restos(destino)


def test_un_error_que_no_es_de_retencion_no_se_reintenta(
    tmp_path: Path, registro: int, monkeypatch: pytest.MonkeyPatch, sin_esperas: None
) -> None:
    import os

    origen, destino = _instalacion_anterior(tmp_path, registro)
    reemplazar = os.replace

    def disco_lleno(fuente: object, copia: object) -> None:
        if Path(str(fuente)).parent.name == "Voziris.nuevo":
            raise OSError(28, "No queda espacio", str(fuente), 112)
        reemplazar(fuente, copia)  # type: ignore[arg-type]

    monkeypatch.setattr(instalador.os, "replace", disco_lleno)
    with pytest.raises(instalador.InstalacionFallida, match="disco está lleno"):
        instalador.instalar(origen, destino, preguntar_reintento=lambda m: pytest.fail(m),
                            **_kwargs(tmp_path, registro))  # type: ignore[arg-type]
    monkeypatch.undo()
    assert _es_la_v1(destino) and _sin_restos(destino)


class _Cancelado(Exception):
    """Lo que lanza la ventana de progreso cuando se ha pulsado Cancelar."""


def _ventana() -> tuple[list[bool], Any]:
    """Como `VentanaProgreso`: Cancelar solo se apunta, y lo que lanza es el siguiente aviso."""
    cancelado: list[bool] = []

    def al_progresar(mensaje: str, fraccion: float | None) -> None:
        if cancelado:
            raise _Cancelado

    return cancelado, al_progresar


def test_cancelar_con_la_pregunta_abierta_no_cierra_ni_cambia_nada(
    tmp_path: Path, registro: int, monkeypatch: pytest.MonkeyPatch, sin_esperas: None,
    copia_viva: Any,
) -> None:
    """Se pulsa Cancelar en la ventana de detrás y luego se contesta que sí."""
    origen, destino = _instalacion_anterior(tmp_path, registro)
    proceso = copia_viva(destino / "_internal" / "transcribe.exe", "--transcribir", "reunion.m4a")
    monkeypatch.setattr(instalador, "ESPERA_CIERRE_S", 0.1)
    cancelado, al_progresar = _ventana()
    bandeja: list[str] = []

    def si_pero_ya_cancelado(pregunta: str) -> bool:
        cancelado.append(True)
        return True

    with pytest.raises(_Cancelado):
        instalador.instalar(origen, destino, al_progresar=al_progresar,
                            preguntar_cerrar=si_pero_ya_cancelado,
                            antes_de_sustituir=lambda: bandeja.append("cerrada"),
                            **_kwargs(tmp_path, registro))  # type: ignore[arg-type]
    assert proceso.poll() is None  # sigue transcribiendo
    assert bandeja == []  # y la bandeja, abierta
    assert _es_la_v1(destino) and _sin_restos(destino)


def test_cancelar_con_el_reintento_abierto_no_cambia_nada(
    tmp_path: Path, registro: int, sin_esperas: None, hijo_que_retiene: Any
) -> None:
    origen, destino = _instalacion_anterior(tmp_path, registro)
    hijo = hijo_que_retiene(destino / "_internal" / "av.libs" / "avcodec-62.dll")
    cancelado, al_progresar = _ventana()

    def reintentar_pero_ya_cancelado(explicacion: str) -> bool:
        hijo.soltar()  # ya no hay nada que lo impida: si no se mira Cancelar, se instala
        cancelado.append(True)
        return True

    with pytest.raises(_Cancelado):
        instalador.instalar(origen, destino, al_progresar=al_progresar,
                            preguntar_reintento=reintentar_pero_ya_cancelado,
                            **_kwargs(tmp_path, registro))  # type: ignore[arg-type]
    assert _es_la_v1(destino) and _sin_restos(destino)


def test_cancelar_mientras_se_cierra_la_bandeja_no_cambia_nada(
    tmp_path: Path, registro: int
) -> None:
    origen, destino = _instalacion_anterior(tmp_path, registro)
    cancelado, al_progresar = _ventana()
    with pytest.raises(_Cancelado):
        instalador.instalar(origen, destino, al_progresar=al_progresar,
                            antes_de_sustituir=lambda: cancelado.append(True),
                            **_kwargs(tmp_path, registro))  # type: ignore[arg-type]
    assert _es_la_v1(destino) and _sin_restos(destino)


@pytest.mark.parametrize("hay_a_quien_preguntar", [True, False])
def test_lo_que_empieza_a_transcribir_despues_de_la_pregunta_tampoco_se_cierra_sin_preguntar(
    tmp_path: Path, registro: int, monkeypatch: pytest.MonkeyPatch, sin_esperas: None,
    copia_viva: Any, hay_a_quien_preguntar: bool,
) -> None:
    """«Transcribir con Voziris» lanzado mientras se cierra la bandeja: hasta 6 s."""
    origen, destino = _instalacion_anterior(tmp_path, registro)
    monkeypatch.setattr(instalador, "ESPERA_CIERRE_S", 0.1)
    lanzados: list[Any] = []
    preguntas: list[str] = []

    def cerrar_la_bandeja() -> None:
        lanzados.append(copia_viva(destino / "_internal" / "transcribe.exe", "--transcribir",
                                   r"C:\mis cosas\recién llegada.m4a", "--ventana"))

    def no(pregunta: str) -> bool:
        preguntas.append(pregunta)
        return False

    with pytest.raises(instalador.InstalacionCancelada):
        instalador.instalar(origen, destino, antes_de_sustituir=cerrar_la_bandeja,
                            preguntar_cerrar=no if hay_a_quien_preguntar else None,
                            **_kwargs(tmp_path, registro))  # type: ignore[arg-type]

    assert lanzados[0].poll() is None  # sigue transcribiendo
    assert _es_la_v1(destino) and _sin_restos(destino)
    if hay_a_quien_preguntar:
        assert len(preguntas) == 1 and "«recién llegada.m4a»" in preguntas[0]
        assert f"PID {lanzados[0].pid}" in preguntas[0]


def test_si_se_acepta_lo_que_empezo_despues_se_cierra_e_instala(
    tmp_path: Path, registro: int, monkeypatch: pytest.MonkeyPatch, sin_esperas: None,
    copia_viva: Any,
) -> None:
    origen, destino = _instalacion_anterior(tmp_path, registro)
    monkeypatch.setattr(instalador, "ESPERA_CIERRE_S", 0.1)
    lanzados: list[Any] = []
    preguntas: list[str] = []

    def si(pregunta: str) -> bool:
        preguntas.append(pregunta)
        return True

    def cerrar_la_bandeja() -> None:
        lanzados.append(copia_viva(destino / "_internal" / "transcribe.exe",
                                   "--transcribir", "reunion.m4a"))

    instalador.instalar(origen, destino, antes_de_sustituir=cerrar_la_bandeja,
                        preguntar_cerrar=si,
                        **_kwargs(tmp_path, registro))  # type: ignore[arg-type]
    assert len(preguntas) == 1
    assert lanzados[0].wait(timeout=5) == 1
    assert (destino / "voziris.exe").read_bytes() == b"MZ v2"


def test_cancelar_con_la_segunda_pregunta_abierta_no_la_cierra(
    tmp_path: Path, registro: int, monkeypatch: pytest.MonkeyPatch, sin_esperas: None,
    copia_viva: Any,
) -> None:
    origen, destino = _instalacion_anterior(tmp_path, registro)
    monkeypatch.setattr(instalador, "ESPERA_CIERRE_S", 0.1)
    cancelado, al_progresar = _ventana()
    lanzados: list[Any] = []

    def cerrar_la_bandeja() -> None:
        lanzados.append(copia_viva(destino / "_internal" / "transcribe.exe",
                                   "--transcribir", "reunion.m4a"))

    def si_pero_ya_cancelado(pregunta: str) -> bool:
        cancelado.append(True)
        return True

    with pytest.raises(_Cancelado):
        instalador.instalar(origen, destino, al_progresar=al_progresar,
                            antes_de_sustituir=cerrar_la_bandeja,
                            preguntar_cerrar=si_pero_ya_cancelado,
                            **_kwargs(tmp_path, registro))  # type: ignore[arg-type]
    assert lanzados[0].poll() is None
    assert _es_la_v1(destino) and _sin_restos(destino)


def test_lo_aceptado_sigue_vivo_si_se_cancela_mientras_se_cierra_la_bandeja(
    tmp_path: Path, registro: int, monkeypatch: pytest.MonkeyPatch, sin_esperas: None,
    copia_viva: Any,
) -> None:
    origen, destino = _instalacion_anterior(tmp_path, registro)
    proceso = copia_viva(destino / "_internal" / "transcribe.exe", "--transcribir", "reunion.m4a")
    monkeypatch.setattr(instalador, "ESPERA_CIERRE_S", 0.1)
    cancelado, al_progresar = _ventana()
    with pytest.raises(_Cancelado):
        instalador.instalar(origen, destino, al_progresar=al_progresar,
                            preguntar_cerrar=lambda p: True,
                            antes_de_sustituir=lambda: cancelado.append(True),
                            **_kwargs(tmp_path, registro))  # type: ignore[arg-type]
    assert proceso.poll() is None
    assert _es_la_v1(destino) and _sin_restos(destino)


def test_decir_no_a_la_segunda_pregunta_no_deja_cerrada_la_primera(
    tmp_path: Path, registro: int, monkeypatch: pytest.MonkeyPatch, sin_esperas: None,
    copia_viva: Any,
) -> None:
    """Aceptada una y empezada otra después: el «No» tiene que llegar antes de cerrar nada."""
    origen, destino = _instalacion_anterior(tmp_path, registro)
    primera = copia_viva(destino / "_internal" / "primera.exe", "--transcribir", "a.m4a")
    monkeypatch.setattr(instalador, "ESPERA_CIERRE_S", 0.1)
    respuestas = iter([True, False])
    segunda: list[Any] = []

    def cerrar_la_bandeja() -> None:
        segunda.append(copia_viva(destino / "_internal" / "segunda.exe",
                                  "--transcribir", "b.m4a"))

    with pytest.raises(instalador.InstalacionCancelada):
        instalador.instalar(origen, destino, antes_de_sustituir=cerrar_la_bandeja,
                            preguntar_cerrar=lambda p: next(respuestas),
                            **_kwargs(tmp_path, registro))  # type: ignore[arg-type]
    assert segunda[0].poll() is None
    assert primera.poll() is None
    assert _es_la_v1(destino) and _sin_restos(destino)


def test_lo_que_empieza_despues_se_pregunta_todo_junto(
    tmp_path: Path, registro: int, monkeypatch: pytest.MonkeyPatch, sin_esperas: None,
    copia_viva: Any,
) -> None:
    origen, destino = _instalacion_anterior(tmp_path, registro)
    monkeypatch.setattr(instalador, "ESPERA_CIERRE_S", 0.1)
    lanzados: list[Any] = []
    preguntas: list[str] = []

    def cerrar_la_bandeja() -> None:
        for nombre in ("una", "otra"):
            lanzados.append(copia_viva(destino / "_internal" / f"{nombre}.exe",
                                       "--transcribir", f"{nombre}.m4a"))

    def si(pregunta: str) -> bool:
        preguntas.append(pregunta)
        return True

    instalador.instalar(origen, destino, antes_de_sustituir=cerrar_la_bandeja,
                        preguntar_cerrar=si,
                        **_kwargs(tmp_path, registro))  # type: ignore[arg-type]
    assert len(preguntas) == 1
    assert "«una.m4a»" in preguntas[0] and "«otra.m4a»" in preguntas[0]
    assert all(p.wait(timeout=5) == 1 for p in lanzados)
    assert (destino / "voziris.exe").read_bytes() == b"MZ v2"


def test_cancelar_mientras_se_cierra_uno_no_cierra_el_siguiente(
    tmp_path: Path, registro: int, monkeypatch: pytest.MonkeyPatch, sin_esperas: None,
    copia_viva: Any,
) -> None:
    """Cada uno puede tardar 5 s en cerrarse: Cancelar no puede esperar a que acaben todos."""
    origen, destino = _instalacion_anterior(tmp_path, registro)
    lanzados = [copia_viva(destino / "_internal" / f"{nombre}.exe") for nombre in ("uno", "dos")]
    monkeypatch.setattr(instalador, "ESPERA_CIERRE_S", 0.1)
    cancelado, al_progresar = _ventana()
    cerrar = instalador.winapi.cerrar_proceso
    cerrados: list[int] = []

    def cerrar_y_cancelar(proceso: Any, espera_s: float) -> str:
        cerrados.append(proceso.pid)
        cancelado.append(True)
        return cerrar(proceso, espera_s)

    monkeypatch.setattr(instalador.winapi, "cerrar_proceso", cerrar_y_cancelar)
    with pytest.raises(_Cancelado):
        instalador.instalar(origen, destino, al_progresar=al_progresar,
                            **_kwargs(tmp_path, registro))  # type: ignore[arg-type]
    assert len(cerrados) == 1
    assert [p.pid for p in lanzados if p.poll() is None] != []  # el otro sigue vivo
    assert cerrados[0] not in {p.pid for p in lanzados if p.poll() is None}
    assert _es_la_v1(destino) and _sin_restos(destino)


def test_lo_ya_aceptado_no_se_vuelve_a_preguntar(
    tmp_path: Path, registro: int, monkeypatch: pytest.MonkeyPatch, sin_esperas: None,
    copia_viva: Any,
) -> None:
    origen, destino = _instalacion_anterior(tmp_path, registro)
    proceso = copia_viva(destino / "_internal" / "transcribe.exe", "--transcribir", "reunion.m4a")
    monkeypatch.setattr(instalador, "ESPERA_CIERRE_S", 0.1)
    preguntas: list[str] = []

    def si(pregunta: str) -> bool:
        preguntas.append(pregunta)
        return True

    instalador.instalar(origen, destino, preguntar_cerrar=si,
                        **_kwargs(tmp_path, registro))  # type: ignore[arg-type]
    assert len(preguntas) == 1
    assert proceso.wait(timeout=5) == 1
    assert (destino / "voziris.exe").read_bytes() == b"MZ v2"


@pytest.mark.parametrize("en_que_repaso", [1, 2])
def test_lo_que_se_va_entre_la_lista_y_su_orden_no_se_pregunta(
    tmp_path: Path, registro: int, monkeypatch: pytest.MonkeyPatch, sin_esperas: None,
    copia_viva: Any, en_que_repaso: int,
) -> None:
    """Como la bandeja que está saliendo: su orden ya no se lee, pero no es que trabaje.

    Contarlo como «puede estar transcribiendo» sacaría una pregunta falsa, con el «No» por
    defecto, justo después de cerrar la bandeja.
    """
    origen, destino = _instalacion_anterior(tmp_path, registro)
    proceso = copia_viva(destino / "_internal" / "saliendo.exe")
    listar = instalador.winapi.procesos_en_carpeta
    repasos: list[int] = []

    def lista_y_se_va(carpeta: Path) -> list[Any]:
        vivos = listar(carpeta)
        repasos.append(1)
        if len(repasos) == en_que_repaso:
            assert proceso.pid in {p.pid for p in vivos}
            proceso.kill()
            proceso.wait(timeout=5)
        return vivos

    monkeypatch.setattr(instalador.winapi, "procesos_en_carpeta", lista_y_se_va)
    instalador.instalar(origen, destino,
                        preguntar_cerrar=lambda p: pytest.fail(f"pregunta falsa: {p}"),
                        **_kwargs(tmp_path, registro))  # type: ignore[arg-type]
    assert (destino / "voziris.exe").read_bytes() == b"MZ v2"


@pytest.mark.parametrize(("orden", "grabacion"), [
    (["voziris.exe"], None),
    (["voziris.exe", "--config", "x.toml"], None),
    (["voziris.exe", "--transcribir", r"C:\a b\reunión.m4a", "--ventana"], "reunión.m4a"),
    (["voziris.exe", "--transcribir=C:\\a\\reunión.m4a"], "reunión.m4a"),
    (["voziris.exe", "--archivo", "nota.wav"], "nota.wav"),
    (["voziris.exe", "--transcribir"], ""),
    ([r"C:\--transcribir\voziris.exe"], None),  # el propio ejecutable no cuenta
])
def test_grabacion_de_una_orden(orden: list[str], grabacion: str | None) -> None:
    assert instalador._grabacion_de(orden) == grabacion


def _retiene(ruta: str, pid: int = 1234) -> Any:
    from voziris import winapi

    return winapi.Proceso(pid, ruta)


def test_el_mensaje_dice_quien_retiene_y_que_hacer() -> None:
    cmd = r"C:\Windows\System32\cmd.exe"
    texto = instalador.mensaje_de_retencion([_retiene(cmd)], "pulsa Reintentar")
    assert texto == (
        rf"Otro programa tiene abierta la carpeta de Voziris: cmd.exe, PID 1234 ({cmd}). "
        "Ciérralo y pulsa Reintentar. Si no ves ninguna ventana suya, en el Administrador de "
        "tareas → Detalles, busca ese PID y elige Finalizar tarea.\n\n"
        "No se ha cambiado nada: la versión anterior sigue como estaba."
    )


def test_el_mensaje_con_varios_o_sin_ruta() -> None:
    texto = instalador.mensaje_de_retencion(
        [_retiene(r"C:\a\uno.exe", 1), _retiene("Antivirus de ejemplo", 2), _retiene("", 3)],
        "pulsa Reintentar",
    )
    assert texto.startswith(
        r"Otros programas tienen abierta la carpeta de Voziris: uno.exe, PID 1 (C:\a\uno.exe); "
        "Antivirus de ejemplo, PID 2; un programa que no se deja mirar, PID 3. Ciérralos"
    )
    assert "busca esos PID" in texto


def test_el_mensaje_no_manda_finalizar_el_explorador() -> None:
    explorador = r"C:\Windows\explorer.exe"
    solo = instalador.mensaje_de_retencion([_retiene(explorador)], "pulsa Reintentar")
    assert solo.startswith("Hay una ventana del Explorador abierta dentro de la carpeta")
    assert "Finalizar tarea" not in solo and "pulsa Reintentar" in solo
    con_otro = instalador.mensaje_de_retencion(
        [_retiene(explorador, 1), _retiene(r"C:\a\uno.exe", 2)], "pulsa Reintentar"
    )
    assert "ventana del Explorador" in con_otro and "PID 2" in con_otro
    assert "explorer.exe" not in con_otro


def test_el_mensaje_sin_saber_quien_y_sin_decir_que_esperes() -> None:
    texto = instalador.mensaje_de_retencion([], "pulsa Reintentar")
    assert texto.startswith("Algo tiene abierta la carpeta de Voziris y no se ha podido saber qué.")
    assert "pulsa Reintentar" in texto and "reinicia Windows" in texto
    assert texto.endswith("No se ha cambiado nada: la versión anterior sigue como estaba.")


def test_ningun_mensaje_manda_esperar_un_minuto() -> None:
    copia = instalador.explicar_error_de_copia(PermissionError(13, "en uso", "x.dll", 32))
    assert "antivirus" in copia and "reinicia Windows" in copia
    assert "minuto" not in copia and "Voziris que sigue abierta" not in copia
    assert "minuto" not in instalador._causa("", PermissionError(13, "denegado", "x", 5))


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
             "detalle": rf"Falta _internal\av.libs\x.dll. {integridad.POR_QUE_FALTAN}"},
            {"nombre": "av", "ok": False, "detalle": "No se pudo cargar PyAV. Falta x.dll"},
        ],
    }
    monkeypatch.setattr(integridad, "comprobar_en_otro_proceso", lambda orden, *a: informe)
    with pytest.raises(instalador.InstalacionFallida) as fallo:
        instalador.comprobar_arrancando(tmp_path / "Voziris.nuevo" / "voziris.exe")
    mensaje = str(fallo.value)
    assert r"Falta _internal\av.libs\x.dll." in mensaje
    assert "ZIP" not in mensaje and ".msi" not in mensaje  # el origen estaba bien: es la copia
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
