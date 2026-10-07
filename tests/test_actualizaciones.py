"""Tests de las versiones nuevas (VOZ-82).

GitHub está simulado con `httpx.MockTransport`: la consulta, las
redirecciones al almacén de descargas, un SHA-256 que no cuadra, un ZIP con
rutas tramposas, una descarga cancelada. Ningún test sale a la red.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import threading
import time
import zipfile
from pathlib import Path
from typing import Any

import httpx
import pytest

from voziris import actualizaciones as act
from voziris import config as cfg
from voziris import integridad

RAIZ = Path(__file__).resolve().parents[1]
DESCARGA = act.PREFIJO_DESCARGA + "v0.2.0/"
ALMACEN = "https://release-assets.githubusercontent.com/github-production-release-asset/1/"


# --- versiones ------------------------------------------------------------------------------


def test_versiones() -> None:
    assert act.version_de("v0.1.2") == (0, 1, 2, 0)
    assert act.version_de("0.2") == (0, 2, 0, 0)
    assert act.version_de("1.0.0-beta") is None
    assert act.version_de("última") is None
    assert act.es_mas_nueva("v0.1.10", "0.1.9")  # números, no texto
    assert act.es_mas_nueva("0.2", "0.1.9")
    assert act.es_mas_nueva("0.1.1.1", "0.1.1")
    assert not act.es_mas_nueva("v0.1.1", "0.1.1")
    assert not act.es_mas_nueva("0.1.0", "0.1.1")
    assert not act.es_mas_nueva("raro", "0.1.1")
    assert not act.es_mas_nueva("0.2.0", "0.1.1.dev0")  # la que corre no se entiende: no se toca


# --- la respuesta de GitHub -----------------------------------------------------------------


def _release(version: str = "0.2.0", **cambios: Any) -> dict[str, Any]:
    url = f"{act.PREFIJO_DESCARGA}v{version}/"
    datos: dict[str, Any] = {
        "tag_name": f"v{version}",
        "html_url": f"https://github.com/alfonsosanzme/voziris/releases/tag/v{version}",
        "draft": False,
        "prerelease": False,
        "assets": [
            {"name": "SHA256SUMS.txt", "size": 90,
             "browser_download_url": f"{url}SHA256SUMS.txt"},
            {"name": f"voziris-{version}-win64.zip", "size": 1234,
             "browser_download_url": f"{url}voziris-{version}-win64.zip"},
        ],
    }
    datos.update(cambios)
    return datos


def test_novedad_de_una_release_mas_nueva() -> None:
    n = act.novedad_de(_release(), "0.1.1")
    assert n is not None
    assert n.version == "0.2.0"
    assert n.pagina.endswith("/releases/tag/v0.2.0")
    assert n.zip_nombre == "voziris-0.2.0-win64.zip"
    assert n.zip_url == DESCARGA + "voziris-0.2.0-win64.zip"
    assert n.sumas_url == DESCARGA + "SHA256SUMS.txt"
    assert n.zip_tamano == 1234
    assert n.instalable


def test_nada_nuevo_si_es_la_misma_o_un_borrador() -> None:
    assert act.novedad_de(_release("0.1.1"), "0.1.1") is None
    assert act.novedad_de(_release("0.1.0"), "0.1.1") is None
    assert act.novedad_de(_release(prerelease=True), "0.1.1") is None
    assert act.novedad_de(_release(draft=True), "0.1.1") is None


def test_solo_descargas_de_este_repositorio() -> None:
    datos = _release()
    datos["assets"][1]["browser_download_url"] = "https://ejemplo.com/voziris-0.2.0-win64.zip"
    datos["html_url"] = "https://ejemplo.com/voziris"
    n = act.novedad_de(datos, "0.1.1")
    assert n is not None
    assert n.zip_url == ""
    assert not n.instalable  # se avisa, pero no se descarga de cualquier sitio
    assert n.pagina == act.PAGINA


def test_una_release_sin_zip_se_avisa_sin_poder_instalarla() -> None:
    n = act.novedad_de(_release(assets=[]), "0.1.1")
    assert n is not None and not n.instalable


@pytest.mark.parametrize("datos", [[], {"tag_name": 3}, {"tag_name": "nueva"}, "texto"])
def test_respuestas_raras(datos: Any) -> None:
    with pytest.raises(act.ConsultaFallida):
        act.novedad_de(datos, "0.1.1")


# --- consultar ------------------------------------------------------------------------------


def test_consultar_pregunta_a_la_api_sin_nada_del_usuario() -> None:
    vistas: list[httpx.Request] = []

    def responder(peticion: httpx.Request) -> httpx.Response:
        vistas.append(peticion)
        return httpx.Response(200, json=_release())

    n = act.consultar("0.1.1", transporte=httpx.MockTransport(responder))
    assert n is not None and n.version == "0.2.0"
    assert len(vistas) == 1
    p = vistas[0]
    assert str(p.url) == act.URL_ULTIMA
    assert p.method == "GET" and not p.content
    assert p.headers["user-agent"].startswith("Voziris/")
    assert "authorization" not in p.headers
    assert "cookie" not in p.headers


@pytest.mark.parametrize(
    ("respuesta", "texto"),
    [
        (httpx.Response(404), "ninguna versión"),
        (httpx.Response(403), "limita"),
        (httpx.Response(429), "limita"),
        (httpx.Response(500), "500"),
        (httpx.Response(200, content=b"<html>"), "no se entiende"),
    ],
)
def test_consultar_falla_con_un_mensaje_claro(respuesta: httpx.Response, texto: str) -> None:
    transporte = httpx.MockTransport(lambda _p: respuesta)
    with pytest.raises(act.ConsultaFallida, match=texto):
        act.consultar("0.1.1", transporte=transporte)


def test_consultar_sin_red() -> None:
    def sin_red(peticion: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sin red", request=peticion)

    def lenta(peticion: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("lenta", request=peticion)

    with pytest.raises(act.ConsultaFallida, match="conectar"):
        act.consultar("0.1.1", transporte=httpx.MockTransport(sin_red))
    with pytest.raises(act.ConsultaFallida, match="a tiempo"):
        act.consultar("0.1.1", transporte=httpx.MockTransport(lenta))


# --- descargar ------------------------------------------------------------------------------


def _paquete(tmp_path: Path, extra: dict[str, bytes] | None = None,
             quitar_tras_manifiesto: str | None = None) -> bytes:
    """Un ZIP como el de empaquetar.py: voziris/ con el .exe, _internal/ y su manifiesto."""
    dist = tmp_path / "dist-falso"
    (dist / "_internal" / "av.libs").mkdir(parents=True)
    (dist / "voziris.exe").write_bytes(b"MZ" + os.urandom(3000))
    (dist / "_internal" / "python313.dll").write_bytes(os.urandom(5000))
    (dist / "_internal" / "av.libs" / "avcodec-62.dll").write_bytes(os.urandom(4000))
    integridad.escribir_manifiesto(dist)
    if quitar_tras_manifiesto:
        (dist / quitar_tras_manifiesto).unlink()
    memoria = io.BytesIO()
    with zipfile.ZipFile(memoria, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(dist.rglob("*")):
            if p.is_file():
                z.write(p, Path("voziris") / p.relative_to(dist))
        for nombre, contenido in (extra or {}).items():
            z.writestr(nombre, contenido)
    return memoria.getvalue()


class GitHubFalso:
    """Las dos descargas de una release, con la redirección al almacén como la real."""

    def __init__(self, zip_: bytes, sumas: str | None = None, almacen: str = ALMACEN) -> None:
        self.zip = zip_
        self.sumas = sumas if sumas is not None else (
            f"{hashlib.sha256(zip_).hexdigest()}  voziris-0.2.0-win64.zip\n"
        )
        self.almacen = almacen
        self.pedidas: list[str] = []

    def __call__(self, peticion: httpx.Request) -> httpx.Response:
        url = str(peticion.url)
        self.pedidas.append(url)
        if url == DESCARGA + "SHA256SUMS.txt":
            return httpx.Response(200, text=self.sumas)
        if url == DESCARGA + "voziris-0.2.0-win64.zip":
            return httpx.Response(302, headers={"Location": self.almacen + "zip"})
        if url == self.almacen + "zip":
            return httpx.Response(200, content=self.zip)
        return httpx.Response(404)

    @property
    def transporte(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


def _novedad(tamano: int = 0) -> act.Novedad:
    return act.Novedad(
        "0.2.0", zip_url=DESCARGA + "voziris-0.2.0-win64.zip", zip_tamano=tamano,
        sumas_url=DESCARGA + "SHA256SUMS.txt",
    )


def test_descargar_comprueba_y_descomprime(tmp_path: Path) -> None:
    zip_ = _paquete(tmp_path)
    github = GitHubFalso(zip_)
    avances: list[tuple[str, float | None]] = []
    base = tmp_path / "descargas"

    exe = act.descargar(
        _novedad(len(zip_)), lambda m, f: avances.append((m, f)),
        base=base, transporte=github.transporte,
    )

    assert exe.is_file() and exe.name == "voziris.exe"
    assert exe.parent.name == "voziris" and exe.parent.parent.parent == base
    assert integridad.comprobar(exe.parent, hashes=True).ok
    assert not list(base.glob("*/voziris-*.zip"))  # el ZIP se borra: ya está descomprimido
    assert github.pedidas[0].endswith("SHA256SUMS.txt")  # el hash, antes que nada
    fracciones = [f for m, f in avances if m.startswith("Descargando") and f is not None]
    assert fracciones and fracciones[-1] == pytest.approx(1.0)
    assert any(m.startswith("Descomprimiendo") for m, _ in avances)


def test_un_sha256_que_no_cuadra_no_deja_nada(tmp_path: Path) -> None:
    zip_ = _paquete(tmp_path)
    github = GitHubFalso(zip_, sumas=f"{'0' * 64}  voziris-0.2.0-win64.zip\n")
    base = tmp_path / "descargas"
    with pytest.raises(act.ActualizacionFallida, match="SHA-256"):
        act.descargar(_novedad(), base=base, transporte=github.transporte)
    assert not list(base.iterdir())


def test_sin_el_hash_del_zip_no_se_descarga(tmp_path: Path) -> None:
    github = GitHubFalso(b"zip", sumas=f"{'a' * 64}  otro-archivo.zip\n")
    with pytest.raises(act.ActualizacionFallida, match="no trae el SHA-256"):
        act.descargar(_novedad(), base=tmp_path / "d", transporte=github.transporte)
    assert not any(u.endswith("zip") for u in github.pedidas[1:])  # el ZIP ni se pide


def test_una_redireccion_fuera_de_https_se_rechaza(tmp_path: Path) -> None:
    github = GitHubFalso(_paquete(tmp_path), almacen="http://almacen.inseguro/")
    with pytest.raises(act.ActualizacionFallida, match="HTTPS"):
        act.descargar(_novedad(), base=tmp_path / "d", transporte=github.transporte)


@pytest.mark.parametrize("ruta", ["voziris/../../fuera.txt", "otra-cosa/leeme.txt"])
def test_un_zip_con_rutas_tramposas_no_se_descomprime(tmp_path: Path, ruta: str) -> None:
    github = GitHubFalso(_paquete(tmp_path, extra={ruta: b"malo"}))
    base = tmp_path / "descargas"
    with pytest.raises(act.ActualizacionFallida, match="no es de Voziris"):
        act.descargar(_novedad(), base=base, transporte=github.transporte)
    assert not (tmp_path / "fuera.txt").exists()
    assert not list(base.iterdir())


def test_un_paquete_al_que_le_falta_un_archivo_no_se_instala(tmp_path: Path) -> None:
    zip_ = _paquete(tmp_path, quitar_tras_manifiesto="_internal/av.libs/avcodec-62.dll")
    with pytest.raises(act.ActualizacionFallida, match="avcodec-62.dll"):
        act.descargar(_novedad(), base=tmp_path / "d", transporte=GitHubFalso(zip_).transporte)


def test_cancelar_borra_lo_descargado(tmp_path: Path) -> None:
    from voziris.ui.transcripcion import TranscripcionCancelada

    zip_ = _paquete(tmp_path, extra={"voziris/_internal/grande.bin": os.urandom(3 * 2**20)})
    base = tmp_path / "descargas"

    def cancelar(mensaje: str, fraccion: float | None) -> None:
        if mensaje.startswith("Descargando"):
            raise TranscripcionCancelada("cancelado por el usuario")

    with pytest.raises(TranscripcionCancelada):
        act.descargar(_novedad(), cancelar, base=base, transporte=GitHubFalso(zip_).transporte)
    assert not list(base.iterdir())


def test_una_release_sin_paquete_remite_a_la_pagina(tmp_path: Path) -> None:
    with pytest.raises(act.ActualizacionFallida, match="Descárgala desde"):
        act.descargar(act.Novedad("0.2.0"), base=tmp_path / "d")


def test_un_paquete_desmesurado_ni_se_pide(tmp_path: Path) -> None:
    def no_se_pide(peticion: httpx.Request) -> httpx.Response:
        raise AssertionError(f"no debería pedir {peticion.url}")

    with pytest.raises(act.ActualizacionFallida, match="demasiado"):
        act.descargar(_novedad(act.TAMANO_MAXIMO + 1), base=tmp_path / "d",
                      transporte=httpx.MockTransport(no_se_pide))


def test_limpiar_descargas_respeta_las_recientes(tmp_path: Path) -> None:
    vieja, reciente = tmp_path / "0.1.9-a", tmp_path / "0.2.0-b"
    for carpeta in (vieja, reciente):
        (carpeta / "voziris").mkdir(parents=True)
        (carpeta / "voziris" / "voziris.exe").write_bytes(b"MZ")
    hace_dos_horas = time.time() - 7200
    os.utime(vieja, (hace_dos_horas, hace_dos_horas))

    act.limpiar_descargas(tmp_path, mas_viejas_que_s=3600)
    assert not vieja.exists() and reciente.exists()

    act.limpiar_descargas(tmp_path)
    assert not tmp_path.exists()  # vacía: se quita también la carpeta


# --- estado ---------------------------------------------------------------------------------


def test_estado_que_no_esta_o_esta_roto(tmp_path: Path) -> None:
    assert act.Estado.leer(tmp_path) == act.Estado()
    (tmp_path / act.NOMBRE_ESTADO).write_text("{roto", encoding="utf-8")
    assert act.Estado.leer(tmp_path) == act.Estado()
    (tmp_path / act.NOMBRE_ESTADO).write_text("[1, 2]", encoding="utf-8")
    assert act.Estado.leer(tmp_path) == act.Estado()


def test_estado_ida_y_vuelta(tmp_path: Path) -> None:
    e = act.Estado(comprobada=1_800_000_000.5, ultima="0.2.0", pagina=act.PAGINA, avisada="0.2.0")
    e.guardar(tmp_path)
    assert act.Estado.leer(tmp_path) == e
    guardado = json.loads((tmp_path / act.NOMBRE_ESTADO).read_text(encoding="utf-8"))
    assert guardado["ultima"] == "0.2.0"


def test_toca_una_vez_al_dia() -> None:
    dia = act.CADA_S
    assert act.Estado().toca(1_800_000_000)  # nunca se ha mirado
    assert not act.Estado(comprobada=1000.0).toca(1000.0 + dia - 1)
    assert act.Estado(comprobada=1000.0).toca(1000.0 + dia)
    assert act.Estado(comprobada=1000.0 + dia).toca(1000.0)  # reloj atrasado: se mira


# --- el vigilante ---------------------------------------------------------------------------


class Consultas:
    def __init__(self, respuesta: act.Novedad | None | Exception) -> None:
        self.respuesta = respuesta
        self.veces = 0
        self.hecha = threading.Event()

    def __call__(self, _version: str) -> act.Novedad | None:
        self.veces += 1
        self.hecha.set()
        if isinstance(self.respuesta, Exception):
            raise self.respuesta
        return self.respuesta


def _vigilante(tmp_path: Path, consultas: Consultas, activa: bool = True,
               avisos: list[act.Novedad] | None = None, reloj: float = 2e9) -> act.Vigilante:
    return act.Vigilante(
        tmp_path, lambda: activa, (avisos if avisos is not None else []).append,
        version="0.1.1", consultar=consultas, reloj=lambda: reloj,
        primera_espera_s=0, revisar_cada_s=0.02,
    )


def _esperar(condicion: Any, segundos: float = 3.0) -> None:
    limite = time.monotonic() + segundos
    while not condicion():
        assert time.monotonic() < limite, "no ha pasado a tiempo"
        time.sleep(0.01)


def test_sin_permiso_no_consulta(tmp_path: Path) -> None:
    consultas = Consultas(None)
    v = _vigilante(tmp_path, consultas, activa=False)
    v.arrancar()
    time.sleep(0.2)
    v.parar()
    assert consultas.veces == 0
    assert not (tmp_path / act.NOMBRE_ESTADO).exists()


def test_avisa_una_vez_por_version(tmp_path: Path) -> None:
    nueva = act.Novedad("0.2.0")
    avisos: list[act.Novedad] = []
    consultas = Consultas(nueva)
    v = _vigilante(tmp_path, consultas, avisos=avisos)
    v.arrancar()
    _esperar(lambda: avisos)
    time.sleep(0.15)  # varias vueltas del bucle: dentro del mismo día no se vuelve a mirar
    v.parar()
    assert consultas.veces == 1
    assert avisos == [nueva]
    assert v.novedad == nueva

    # Al día siguiente vuelve a mirar; la misma versión no se avisa otra vez.
    consultas2 = Consultas(nueva)
    otra = _vigilante(tmp_path, consultas2, avisos=avisos, reloj=2e9 + act.CADA_S)
    assert otra.novedad is not None and otra.novedad.version == "0.2.0"  # sin consultar
    otra.arrancar()
    _esperar(lambda: consultas2.veces)
    time.sleep(0.05)
    otra.parar()
    assert avisos == [nueva]


def test_una_consulta_fallida_se_reintenta(tmp_path: Path) -> None:
    consultas = Consultas(act.ConsultaFallida("sin red"))
    v = _vigilante(tmp_path, consultas)
    v.arrancar()
    _esperar(lambda: consultas.veces >= 3)  # no espera un día: lo vuelve a probar
    v.parar()
    assert act.Estado.leer(tmp_path).comprobada == 0.0


def test_buscar_ahora_aunque_no_haya_permiso(tmp_path: Path) -> None:
    consultas = Consultas(None)
    v = _vigilante(tmp_path, consultas, activa=False)
    assert v.buscar_ahora() is None
    assert consultas.veces == 1
    consultas.respuesta = act.ConsultaFallida("sin red")
    with pytest.raises(act.ConsultaFallida):
        v.buscar_ahora()


def test_despues_de_actualizar_no_queda_novedad(tmp_path: Path) -> None:
    act.Estado(comprobada=1.0, ultima="0.2.0", avisada="0.2.0").guardar(tmp_path)
    v = act.Vigilante(tmp_path, lambda: True, lambda n: None, version="0.2.0")
    assert v.novedad is None


def test_despertar_mira_sin_esperar_la_hora(tmp_path: Path) -> None:
    consultas = Consultas(None)
    permiso = [False]
    v = act.Vigilante(
        tmp_path, lambda: permiso[0], lambda n: None, version="0.1.1", consultar=consultas,
        primera_espera_s=0, revisar_cada_s=3600,
    )
    v.arrancar()
    time.sleep(0.1)
    assert consultas.veces == 0
    permiso[0] = True  # el usuario acepta la pregunta
    v.despertar()
    assert consultas.hecha.wait(3)
    v.parar()


# --- la opción en config.toml ---------------------------------------------------------------


def _config(tmp_path: Path, extra: str = "") -> cfg.Config:
    ejemplo = (RAIZ / cfg.NOMBRE_EJEMPLO).read_text(encoding="utf-8")
    (tmp_path / cfg.NOMBRE_ARCHIVO).write_text(ejemplo + extra, encoding="utf-8")
    return cfg.cargar(tmp_path / cfg.NOMBRE_ARCHIVO)


def test_por_defecto_se_pregunta(tmp_path: Path) -> None:
    assert _config(tmp_path).actualizaciones.buscar == "preguntar"


def test_si_sin_tilde_tambien_vale(tmp_path: Path) -> None:
    ejemplo = (RAIZ / cfg.NOMBRE_EJEMPLO).read_text(encoding="utf-8")
    texto = ejemplo.replace('buscar = "preguntar"', 'buscar = "si"')
    (tmp_path / cfg.NOMBRE_ARCHIVO).write_text(texto, encoding="utf-8")
    assert cfg.cargar(tmp_path / cfg.NOMBRE_ARCHIVO).actualizaciones.buscar == "sí"


def test_un_valor_que_no_existe_es_error(tmp_path: Path) -> None:
    from voziris.errores import ConfigInvalida

    ejemplo = (RAIZ / cfg.NOMBRE_EJEMPLO).read_text(encoding="utf-8")
    (tmp_path / cfg.NOMBRE_ARCHIVO).write_text(
        ejemplo.replace('buscar = "preguntar"', 'buscar = "siempre"'), encoding="utf-8"
    )
    with pytest.raises(ConfigInvalida, match=re.escape("preguntar | sí | no")):
        cfg.cargar(tmp_path / cfg.NOMBRE_ARCHIVO)


def test_un_config_de_antes_gana_la_seccion_al_guardar(tmp_path: Path) -> None:
    """La 0.1.1 no la tenía: al contestar la pregunta se añade, con su explicación."""
    ejemplo = (RAIZ / cfg.NOMBRE_EJEMPLO).read_text(encoding="utf-8")
    viejo = ejemplo.split("[actualizaciones]")[0]
    ruta = tmp_path / cfg.NOMBRE_ARCHIVO
    ruta.write_text(viejo, encoding="utf-8")
    c = cfg.cargar(ruta)
    assert c.actualizaciones.buscar == "preguntar"
    c.actualizaciones.buscar = "sí"
    # La plantilla trae una carpeta de Markdown que no existe: desde la bandeja se guarda igual.
    cfg.guardar(c, estricto=False)
    texto = ruta.read_text(encoding="utf-8")
    assert '[actualizaciones]\nbuscar = "sí"' in texto
    assert "una vez al día" in texto
    assert cfg.cargar(ruta).actualizaciones.buscar == "sí"


def test_el_panel_sigue_exigiendo_la_carpeta_del_markdown(tmp_path: Path) -> None:
    from voziris.errores import ConfigInvalida

    c = _config(tmp_path)
    c.actualizaciones.buscar = "no"
    with pytest.raises(ConfigInvalida, match="no existe"):
        cfg.guardar(c)


# --- bandeja --------------------------------------------------------------------------------


def _bandeja(
    version_nueva: str | None, etiqueta: str = "Actualizar a la {}…"
) -> tuple[Any, list[str]]:
    from voziris.ui.bandeja import AccionesBandeja, Bandeja

    pulsadas: list[str] = []
    acciones = AccionesBandeja(
        dictar_ahora=lambda: None, dictar_markdown=lambda: None, alternar_corte=lambda: None,
        abrir_ajustes=lambda: None, cambiar_motor=lambda m: None, reintentar=lambda i: None,
        borrar_entrada=lambda i: None, salir=lambda: None,
        buscar_actualizaciones=lambda: pulsadas.append("buscar"),
        actualizar=lambda: pulsadas.append("actualizar"),
        version_nueva=lambda: version_nueva,
        etiqueta_actualizar=etiqueta,
    )
    return Bandeja(acciones), pulsadas


def _visibles(bandeja: Any) -> dict[str, Any]:
    return {str(i.text): i for i in bandeja.construir_menu().items if i.visible}


def test_menu_sin_version_nueva() -> None:
    bandeja, pulsadas = _bandeja(None)
    items = _visibles(bandeja)
    assert "Buscar actualizaciones" in items
    assert not any(t.startswith("Actualizar") for t in items)
    items["Buscar actualizaciones"](None)
    assert pulsadas == ["buscar"]


def test_menu_con_version_nueva() -> None:
    bandeja, pulsadas = _bandeja("0.2.0")
    items = _visibles(bandeja)
    items["Actualizar a la 0.2.0…"](None)
    assert pulsadas == ["actualizar"]
    textos = list(items)
    assert textos.index("Actualizar a la 0.2.0…") < textos.index("Acerca de Voziris")


def test_menu_portable_descarga() -> None:
    bandeja, _ = _bandeja("0.2.0", etiqueta="Descargar la {}…")
    assert "Descargar la 0.2.0…" in _visibles(bandeja)


def test_avisos() -> None:
    n = act.Novedad("0.2.0")
    assert "«Actualizar a la 0.2.0…»" in act.aviso_de(n, instalada=True)
    assert "«Descargar la 0.2.0…»" in act.aviso_de(n, instalada=False)


# --- panel de ajustes -----------------------------------------------------------------------


@pytest.fixture(scope="module")
def raiz() -> Any:
    import tkinter as tk

    try:
        r = tk.Tk()
    except tk.TclError as e:
        pytest.skip(f"sin Tk: {e}")
    r.withdraw()
    yield r
    r.destroy()


def _casilla(raiz: Any, c: cfg.Config) -> Any:
    from voziris.ui.ajustes import Ajustes

    a = Ajustes(raiz, c, lambda _c: [])
    a.abrir()
    raiz.update()
    return a


def test_panel_preguntar_sin_tocar_sigue_preguntando(raiz: Any, tmp_path: Path) -> None:
    c = _config(tmp_path)
    a = _casilla(raiz, c)
    assert a._vars["actualizaciones.buscar"].get() is False
    assert a.leer().actualizaciones.buscar == "preguntar"
    a._vars["actualizaciones.buscar"].set(True)
    assert a.leer().actualizaciones.buscar == "sí"
    a.cerrar()


def test_panel_desmarcar_es_no(raiz: Any, tmp_path: Path) -> None:
    c = _config(tmp_path)
    c.actualizaciones.buscar = "sí"
    a = _casilla(raiz, c)
    assert a._vars["actualizaciones.buscar"].get() is True
    a._vars["actualizaciones.buscar"].set(False)
    assert a.leer().actualizaciones.buscar == "no"
    a.cerrar()


def test_panel_no_pisa_la_respuesta_dada_con_el_panel_abierto(raiz: Any, tmp_path: Path) -> None:
    c = _config(tmp_path)
    a = _casilla(raiz, c)
    c.actualizaciones.buscar = "sí"  # contesta la pregunta del arranque con el panel abierto
    assert a.leer().actualizaciones.buscar == "sí"
    a.cerrar()


# --- voziris --actualizar -------------------------------------------------------------------


@pytest.fixture
def cli(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    from voziris import __main__ as principal

    hecho: dict[str, list[Any]] = {"mensajes": [], "errores": [], "lanzadas": [], "paginas": []}
    monkeypatch.setattr(principal, "_mensaje", lambda t, x: hecho["mensajes"].append((t, x)))
    monkeypatch.setattr(principal, "_error_fatal",
                        lambda m, con_ventana, titulo="": hecho["errores"].append(m))
    monkeypatch.setattr(principal, "_lanzar_desatendido",
                        lambda orden, cwd=None: hecho["lanzadas"].append(orden))
    monkeypatch.setattr(principal, "_abrir_pagina", hecho["paginas"].append)
    monkeypatch.setattr(principal, "_esta_instalada", lambda: True)
    monkeypatch.setattr(principal.winapi, "tomar_mutex", lambda nombre: True)
    monkeypatch.setattr("voziris.ui.transcripcion.ejecutar_con_progreso",
                        lambda titulo, detalle, trabajo: trabajo(lambda m, f: None))
    return hecho


def test_dos_actualizaciones_a_la_vez_no(
    cli: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    from voziris import __main__ as principal

    monkeypatch.setattr(principal.winapi, "ES_WINDOWS", True)
    monkeypatch.setattr(principal.winapi, "tomar_mutex", lambda nombre: False)
    monkeypatch.setattr(act, "consultar", lambda: pytest.fail("no debería consultar"))
    assert principal._actualizar_cli() == 0
    assert cli == {"mensajes": [], "errores": [], "lanzadas": [], "paginas": []}


@pytest.mark.skipif(os.name != "nt", reason="mutex de Windows")
def test_tomar_mutex_no_toca_el_de_la_instancia() -> None:
    import subprocess
    import sys

    from voziris import winapi

    nombre = f"Local\\Voziris.pruebas-{os.getpid()}"
    antes = winapi._mutex_instancia
    assert winapi.tomar_mutex(nombre)
    assert winapi.tomar_mutex(nombre)  # el mismo proceso, otra vez: sigue siendo suyo
    assert winapi._mutex_instancia is antes
    otro = subprocess.run(
        [sys.executable, "-c",
         f"from voziris import winapi; print(winapi.tomar_mutex({nombre!r}))"],
        capture_output=True, text=True, timeout=60,
        env={**os.environ, "PYTHONPATH": str(RAIZ / "src")},
    )
    assert otro.stdout.strip() == "False", otro.stderr


def test_actualizar_arranca_el_instalador_nuevo(
    cli: dict[str, list[Any]], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from voziris import __main__ as principal

    exe = tmp_path / "0.2.0-x" / "voziris" / "voziris.exe"
    monkeypatch.setattr(act, "consultar", lambda: act.Novedad("0.2.0"))
    monkeypatch.setattr(act, "descargar", lambda n, progreso: exe)
    assert principal._actualizar_cli() == 0
    assert cli["lanzadas"] == [[str(exe), "--instalar"]]
    assert cli["errores"] == [] and cli["mensajes"] == []


def test_actualizar_al_dia(cli: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    from voziris import __main__ as principal

    monkeypatch.setattr(act, "consultar", lambda: None)
    assert principal._actualizar_cli() == 0
    assert cli["mensajes"][0][0] == "Voziris está al día"
    assert cli["lanzadas"] == []


def test_actualizar_si_falla_no_instala_nada(
    cli: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    from voziris import __main__ as principal

    def descargar(n: act.Novedad, progreso: Any) -> Path:
        raise act.ActualizacionFallida("El paquete descargado no coincide con su SHA-256")

    monkeypatch.setattr(act, "consultar", lambda: act.Novedad("0.2.0"))
    monkeypatch.setattr(act, "descargar", descargar)
    assert principal._actualizar_cli() == 1
    assert cli["errores"] == ["El paquete descargado no coincide con su SHA-256"]
    assert cli["lanzadas"] == []


def test_actualizar_bloqueado_por_windows(
    cli: dict[str, list[Any]], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from voziris import __main__ as principal

    exe = tmp_path / "voziris" / "voziris.exe"
    monkeypatch.setattr(act, "consultar", lambda: act.Novedad("0.2.0"))
    monkeypatch.setattr(act, "descargar", lambda n, progreso: exe)

    def bloqueado(orden: list[str], cwd: Path | None = None) -> None:
        raise OSError(22, "Una directiva de control de aplicaciones bloqueó este archivo",
                      None, 4551)

    monkeypatch.setattr(principal, "_lanzar_desatendido", bloqueado)
    assert principal._actualizar_cli() == 1
    assert "Control inteligente" in cli["errores"][0]
    assert str(exe.parent) in cli["errores"][0]  # dice dónde está, para instalarla a mano


def test_actualizar_una_copia_portable_abre_la_pagina(
    cli: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    from voziris import __main__ as principal

    monkeypatch.setattr(principal, "_esta_instalada", lambda: False)
    monkeypatch.setattr(act, "consultar", lambda: pytest.fail("no debería consultar"))
    assert principal._actualizar_cli() == 1
    assert cli["paginas"] == [act.PAGINA]


def test_instalar_encima_de_otra_version_dice_actualizado(
    cli: dict[str, list[Any]], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from voziris import __main__ as principal
    from voziris import __version__, instalador

    monkeypatch.setattr(instalador, "version_instalada", lambda: "0.0.9")
    monkeypatch.setattr(instalador, "instalar", lambda **k: tmp_path)
    monkeypatch.setattr(principal, "_abrir_la_instalada", lambda destino: None)
    assert principal._instalar_cli() == 0
    titulo, texto = cli["mensajes"][-1]
    assert titulo == "Voziris actualizado"
    assert f"la versión {__version__} (antes, la 0.0.9)" in texto

    monkeypatch.setattr(instalador, "version_instalada", lambda: None)
    assert principal._instalar_cli() == 0
    assert cli["mensajes"][-1][0] == "Voziris instalado"
