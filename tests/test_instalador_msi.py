"""Tests del instalador de un solo archivo (VOZ-88): el MSI, las condiciones y las guías.

Nada de esto abre ventanas ni instala nada. El MSI de verdad (230 MB) se prueba
al publicar, con `tools/probar_msi.py`; aquí se vigilan la receta, lo que la
rodea y, si hay WiX en el equipo, que la receta compila con un paquete de mentira.
"""

from __future__ import annotations

import html
import importlib.util
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from voziris import __main__ as principal
from voziris import config as cfg
from voziris import instalador, integridad
from voziris.ui.bandeja import AYUDA, AccionesBandeja, Bandeja, texto_acerca_de

RAIZ = Path(__file__).resolve().parents[1]
WXS = "{http://wixtoolset.org/schemas/v4/wxs}"
WXL = "{http://wixtoolset.org/schemas/v4/wxl}"


def _herramienta(nombre: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(nombre, RAIZ / "tools" / f"{nombre}.py")
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


@pytest.fixture(scope="module")
def hacer_msi() -> ModuleType:
    return _herramienta("hacer_msi")


@pytest.fixture(scope="module")
def empaquetar() -> ModuleType:
    return _herramienta("empaquetar")


# --- la receta --------------------------------------------------------------------------


def test_la_receta_no_registra_el_msi_y_lanza_el_instalador(hacer_msi: ModuleType) -> None:
    """Las cuatro decisiones de la cabecera de build/voziris.wxs, escritas en la receta."""
    paquete = ET.parse(RAIZ / "build" / "voziris.wxs").getroot().find(f"{WXS}Package")
    assert paquete is not None
    assert paquete.get("Scope") == "perUser"  # sin administrador
    assert paquete.get("UpgradeStrategy") == "none"

    secuencia = paquete.find(f"{WXS}InstallExecuteSequence")
    assert secuencia is not None
    suprimidas = {e.tag.removeprefix(WXS) for e in secuencia if e.get("Suppress") == "yes"}
    assert suprimidas == set(hacer_msi.SIN_REGISTRO)
    lanzar = secuencia.find(f"{WXS}Custom")
    assert lanzar is not None and lanzar.get("Action") == hacer_msi.ACCION
    assert lanzar.get("After") == "InstallFinalize"
    assert lanzar.get("Condition") == 'NOT (LANZAR = "0")'  # la puerta de tools/probar_msi.py

    accion = paquete.find(f"{WXS}CustomAction")
    assert accion is not None and accion.get("Return") == "asyncNoWait"
    orden = accion.get("ExeCommand") or ""
    assert orden.startswith('"[PAQUETE]voziris.exe" ') and orden.endswith(" --instalar")
    # El registro, a la carpeta de la instalación: la misma que usa instalador.py.
    instalacion = rf"[LocalAppDataFolder]Programs\{instalador.NOMBRE}\{cfg.NOMBRE_ARCHIVO}"
    assert f'--config "{instalacion}"' in orden

    carpeta = paquete.find(f"{WXS}SetProperty")
    assert carpeta is not None and carpeta.get("Id") == "PAQUETE"
    assert carpeta.get("Sequence") == "execute" and carpeta.get("Before") == "CostFinalize"
    valor = carpeta.get("Value") or ""
    # Dentro de la carpeta que limpia la siguiente Voziris, y distinta en cada ejecución.
    from voziris import actualizaciones

    assert valor.startswith(rf"[TempFolder]{actualizaciones.CARPETA_DESCARGAS}\msi-")
    assert "[CLIENTPROCESSID]" in valor and valor.endswith("\\voziris\\")


def test_los_textos_del_asistente_no_dicen_que_ha_terminado() -> None:
    """Cuando sale el último diálogo, el instalador de Voziris acaba de empezar."""
    raiz = ET.parse(RAIZ / "build" / "voziris.es-es.wxl").getroot()
    assert raiz.get("Culture") == "es-ES"
    textos = {s.get("Id"): s.get("Value") or "" for s in raiz.iter(f"{WXL}String")}
    assert "terminando de instalar" in textos["ExitDialogTitle"]
    assert "«Instalando Voziris»" in textos["ExitDialogDescription"]
    assert "acepto estas condiciones" in textos["WelcomeEulaDlgLicenseAcceptedCheckBox"]
    assert "VozirisDespues" in textos  # lo que enseña WIXUI_EXITDIALOGOPTIONALTEXT
    for texto in textos.values():
        assert "completad" not in texto.lower()


def test_version_del_zip(hacer_msi: ModuleType) -> None:
    assert hacer_msi.version_del_zip("voziris-0.1.4-win64.zip") == "0.1.4"
    for malo in ("voziris-0.1-win64.zip", "voziris-0.1.4-win64.msi", "otro-0.1.4-win64.zip",
                 "voziris-0.1.4b1-win64.zip"):
        with pytest.raises(hacer_msi.MsiFallido):
            hacer_msi.version_del_zip(malo)


def test_la_extension_de_interfaz_tiene_que_estar_sana(hacer_msi: ModuleType) -> None:
    assert hacer_msi.tiene_extension("WixToolset.UI.wixext 5.0.2\n")
    assert not hacer_msi.tiene_extension("")
    assert not hacer_msi.tiene_extension("WixToolset.Util.wixext 5.0.2\n")
    assert not hacer_msi.tiene_extension("WixToolset.UI.wixext 4.0.5\n")
    assert not hacer_msi.tiene_extension("WixToolset.UI.wixext 5.0.2 (damaged)\n")


def test_extraer_no_saca_nada_fuera_de_voziris(hacer_msi: ModuleType, tmp_path: Path) -> None:
    bueno = tmp_path / "bueno.zip"
    with zipfile.ZipFile(bueno, "w") as z:
        z.writestr("voziris/voziris.exe", b"MZ")
        z.writestr("voziris/_internal/a.txt", b"a")
    paquete = hacer_msi.extraer(bueno, tmp_path / "sale")
    assert (paquete / "voziris.exe").is_file() and (paquete / "_internal" / "a.txt").is_file()

    for nombre in ("../fuera.txt", "otra/cosa.txt", "voziris/../../fuera.txt"):
        malo = tmp_path / "malo.zip"
        with zipfile.ZipFile(malo, "w") as z:
            z.writestr(nombre, b"x")
        with pytest.raises(hacer_msi.MsiFallido, match="no es de Voziris"):
            hacer_msi.extraer(malo, tmp_path / "no")
        assert not (tmp_path / "fuera.txt").exists()

    incompleto = tmp_path / "incompleto.zip"
    with zipfile.ZipFile(incompleto, "w") as z:
        z.writestr("voziris/voziris.exe", b"MZ")
    with pytest.raises(hacer_msi.MsiFallido, match="_internal"):
        hacer_msi.extraer(incompleto, tmp_path / "medio")


def test_comprobar_msi_dice_lo_que_no_cuadra(
    hacer_msi: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paquete = tmp_path / "voziris"
    (paquete / "_internal").mkdir(parents=True)
    (paquete / "voziris.exe").write_bytes(b"MZ")
    (paquete / "_internal" / integridad.NOMBRE_MANIFIESTO).write_bytes(b"abc")
    orden = ('"[PAQUETE]voziris.exe" --config "[LocalAppDataFolder]Programs\\Voziris\\config.toml"'
             " --instalar")
    tablas: dict[str, list[tuple[str, ...]]] = {
        "File": [("voziris.exe", "2"), ("x9k2~1.txt|voziris-manifiesto.txt", "3")],
        "InstallExecuteSequence": [("InstallFinalize", "6600"), (hacer_msi.ACCION, "6601")],
        "CustomAction": [(hacer_msi.ACCION, orden)],
        "Property": [("ProductVersion", "0.1.4")],
    }

    def leer(_msi: Path, consulta: str, _columnas: int) -> list[tuple[str, ...]]:
        return tablas[consulta.split("FROM `")[1].rstrip("`")]

    monkeypatch.setattr(hacer_msi, "leer_tabla", leer)
    assert hacer_msi.comprobar_msi(tmp_path / "x.msi", paquete, "0.1.4") == []

    tablas["InstallExecuteSequence"].append(("RegisterProduct", "6100"))
    tablas["Property"] += [("ALLUSERS", "1")]
    tablas["CustomAction"] = [(hacer_msi.ACCION, '"[PAQUETE]voziris.exe" --salir')]
    tablas["File"].pop()
    problemas = "\n".join(hacer_msi.comprobar_msi(tmp_path / "x.msi", paquete, "0.1.5"))
    for esperado in ("lleva 1 archivos y el ZIP trae 2", "voziris-manifiesto.txt",
                     "RegisterProduct", "ALLUSERS", "no lanza", "0.1.4 y es la 0.1.5"):
        assert esperado in problemas


@pytest.mark.skipif(sys.platform != "win32", reason="un MSI solo se hace y se lee en Windows")
def test_la_receta_compila_con_un_paquete_de_mentira(
    hacer_msi: ModuleType, tmp_path: Path
) -> None:
    """WiX de verdad, con cuatro archivos: lo que sale lleva lo mismo y no se registra.

    No instala nada: solo construye el .msi y lee sus tablas.
    """
    try:
        hacer_msi.comprobar_wix(hacer_msi.buscar_wix())
    except hacer_msi.MsiFallido as e:
        pytest.skip(f"sin WiX 5 en este equipo: {str(e).splitlines()[0]}")
    zip_ = tmp_path / "voziris-9.8.7-win64.zip"
    with zipfile.ZipFile(zip_, "w") as z:
        z.writestr("voziris/voziris.exe", b"MZ" + b"\0" * 62)
        z.writestr(f"voziris/_internal/{integridad.NOMBRE_MANIFIESTO}", b"manifiesto")
        z.writestr("voziris/_internal/sub/carpeta/dato.bin", b"x" * 1000)
        z.writestr("voziris/LÉEME - Instalar Voziris.html", "<p>ñ</p>".encode())

    msi = hacer_msi.construir(zip_)  # ya llama a comprobar_msi: si no cuadra, lanza

    assert msi == tmp_path / "voziris-9.8.7-win64.msi" and msi.stat().st_size > 100_000
    assert not (tmp_path / "msi-trabajo").exists()  # no deja la copia extraída
    propiedades = dict(hacer_msi.leer_tabla(msi, "SELECT `Property`, `Value` FROM `Property`", 2))
    assert propiedades["ProductVersion"] == "9.8.7" and propiedades["ProductName"] == "Voziris"
    assert propiedades["Manufacturer"] == instalador.EDITOR
    assert "ALLUSERS" not in propiedades
    acciones = {a for (a,) in hacer_msi.leer_tabla(
        msi, "SELECT `Action` FROM `InstallExecuteSequence`", 1)}
    assert hacer_msi.ACCION in acciones and not acciones & set(hacer_msi.SIN_REGISTRO)
    largos = {n.split("|")[-1] for (n,) in hacer_msi.leer_tabla(
        msi, "SELECT `FileName` FROM `File`", 1)}
    assert largos == {"voziris.exe", integridad.NOMBRE_MANIFIESTO, "dato.bin",
                      "LÉEME - Instalar Voziris.html"}


# --- las condiciones --------------------------------------------------------------------


def _parrafos_de_condiciones() -> list[str]:
    """Los párrafos de CONDICIONES.md sin su marcado, desde «Última revisión»."""
    parrafos = []
    for linea in (RAIZ / "CONDICIONES.md").read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("# "):
            continue
        linea = re.sub(r"^(#+|-) ", "", linea)
        parrafos.append(re.sub(r"\*\*|`", "", linea))
    return parrafos


def test_las_condiciones_dicen_lo_que_se_pidio() -> None:
    """Sin recogida de datos, gratis y por qué. Si se quita algo de esto, que se note."""
    texto = " ".join(_parrafos_de_condiciones())
    for imprescindible in (
        "no recoge ningún dato tuyo", "telemetría", "ningún servidor nuestro",
        "Lo que dictas se queda en tu equipo", "Es gratis y de código abierto",
        "uso personal", "nos gusta enseñar lo que vamos haciendo", "licencia MIT",
        "sin garantía", "Hugging Face", "GitHub", "Groq",
    ):
        assert imprescindible in texto, imprescindible


def test_la_guia_lleva_las_mismas_condiciones() -> None:
    """La guía las repite en su apartado 6: tienen que ser las mismas, párrafo a párrafo."""
    guia = (RAIZ / "docs" / cfg.GUIA_DE_USO).read_text(encoding="utf-8")
    apartado = guia.split('<h2 id="condiciones">', 1)[1].split("</main>", 1)[0]
    bloques = re.findall(r"<(?:p|li|h3)\b[^>]*>(.*?)</(?:p|li|h3)>", apartado, flags=re.S)
    en_la_guia = [" ".join(html.unescape(re.sub(r"<[^>]+>", "", b)).split()) for b in bloques]
    assert en_la_guia == _parrafos_de_condiciones()


def test_el_rtf_de_las_condiciones_es_ascii_y_esta_entero(hacer_msi: ModuleType) -> None:
    rtf = hacer_msi.rtf_de_condiciones((RAIZ / "CONDICIONES.md").read_text(encoding="utf-8"))
    rtf.encode("ascii")  # el cuadro de Windows Installer no entiende otra cosa
    assert rtf.startswith(r"{\rtf1\ansi\ansicpg1252") and rtf.rstrip().endswith("}")
    assert rtf.count("{") == rtf.count("}")
    assert r"ning\'fan dato tuyo" in rtf and r"telemetr\'eda" in rtf
    assert r"{\b Voziris no recoge" in rtf  # la negrita de Markdown
    assert "**" not in rtf and "`" not in rtf and "#" not in rtf
    assert rtf.count(r"\par}") == len(_parrafos_de_condiciones()) + 1  # más el título


def test_el_rtf_escapa_lo_que_rtf_usa(hacer_msi: ModuleType) -> None:
    rtf = hacer_msi.rtf_de_condiciones("# T\n\nllaves {x} y \\barra\n\n- «ñ» → fin\n")
    assert r"llaves \{x\} y \\barra" in rtf
    assert r"\'ab\'f1\'bb" in rtf  # «ñ» en cp1252
    assert r"\u8594?" in rtf  # la flecha no está en cp1252: va por su número
    assert r"\'95\tab" in rtf  # la viñeta


def test_las_imagenes_del_asistente_tienen_las_medidas_de_wixui(
    hacer_msi: ModuleType, tmp_path: Path
) -> None:
    from PIL import Image

    dialogo, banner = hacer_msi.dibujar_imagenes(tmp_path)
    with Image.open(dialogo) as d, Image.open(banner) as b:
        assert d.format == "BMP" and d.size == (493, 312)
        assert b.format == "BMP" and b.size == (493, 58)
        assert d.getpixel((10, 10)) == hacer_msi.ACENTO  # el lateral, de color
        assert d.getpixel((300, 150)) == (255, 255, 255)  # donde escribe el asistente, blanco


# --- las guías --------------------------------------------------------------------------


def test_las_guias_van_en_el_paquete_y_se_encuentran() -> None:
    receta = (RAIZ / "build" / "voziris.spec").read_text(encoding="utf-8")
    for nombre in (cfg.GUIA_DE_USO, cfg.GUIA_API):
        assert f'os.path.join(RAIZ, "docs", "{nombre}"), "{cfg.CARPETA_GUIAS}"' in receta
        assert cfg.ruta_de_guia(nombre).is_file()  # en desarrollo, la del repositorio
    assert 'os.path.join(RAIZ, "CONDICIONES.md"), "."' in receta
    assert cfg.ruta_de_guia(cfg.GUIA_API, Path("C:/x/_internal")) == Path(
        "C:/x/_internal/docs/GUIA-api.html"
    )


def test_la_guia_de_la_api_se_abre_sin_conexion() -> None:
    """Va dentro del paquete y se abre desde un archivo: nada de fuera, ni scripts."""
    guia = (RAIZ / "docs" / cfg.GUIA_API).read_text(encoding="utf-8")
    assert '<html lang="es">' in guia and "<script" not in guia.lower()
    assert not re.search(r'\b(?:src|data)\s*=\s*["\']https?:', guia)
    assert not re.search(r"@import|url\(\s*['\"]?https?:", guia)
    assert not re.search(r'<link\b[^>]*href\s*=\s*["\']https?:', guia)
    for enlace in re.findall(r'href="(https?://[^"]+)"', guia):
        assert enlace.startswith("https://console.groq.com"), enlace
    # Las etiquetas que el usuario tiene que encontrar, tal como están en la app y en Groq.
    for etiqueta in ("Ajustes…", "Probar la clave y ver modelos", "✓ La clave es válida",
                     "API Keys", "Create API Key", "Guardar", "Automático",
                     "Modelo de transcripción", "whisper-large-v3"):
        assert etiqueta in guia, etiqueta
    for svg in re.findall(r"<svg\b.*?</svg>", guia, flags=re.S):
        assert 'role="img"' in svg and "<title" in svg
        ET.fromstring(svg)  # bien formado


def test_la_guia_de_uso_enlaza_la_de_la_api_y_empaquetar_arregla_el_enlace(
    empaquetar: ModuleType,
) -> None:
    guia = (RAIZ / "docs" / cfg.GUIA_DE_USO).read_text(encoding="utf-8")
    assert f'href="{cfg.GUIA_API}"' in guia  # vecinas en docs/ y en _internal/docs/
    junto_al_exe = empaquetar.guia_junto_al_exe(guia)
    assert f'href="{cfg.GUIA_API}"' not in junto_al_exe
    assert f'href="_internal/{cfg.CARPETA_GUIAS}/{cfg.GUIA_API}"' in junto_al_exe
    assert set(empaquetar.GUIAS_DENTRO) == {cfg.GUIA_DE_USO, cfg.GUIA_API}
    assert 'href="#condiciones"' in junto_al_exe  # las anclas no se tocan


def test_empaquetar_no_sigue_sin_las_guias(empaquetar: ModuleType, tmp_path: Path) -> None:
    dist = tmp_path / "voziris"
    (dist / "_internal" / "docs").mkdir(parents=True)
    assert len(empaquetar._faltan_guias(dist)) == 2
    for nombre in empaquetar.GUIAS_DENTRO:
        (dist / "_internal" / "docs" / nombre).write_text("x", encoding="utf-8")
    assert empaquetar._faltan_guias(dist) == []


def test_los_hashes_de_las_notas_van_cada_uno_bajo_su_paquete(empaquetar: ModuleType) -> None:
    notas = (
        "SHA-256  voziris-0.1.4-win64.msi\n" + "0" * 64 + "\n\n"
        "SHA-256  voziris-0.1.4-win64.zip\n" + "0" * 64 + "\n"
    )
    puestas = empaquetar.poner_hashes(
        notas, {"voziris-0.1.4-win64.zip": "a" * 64, "voziris-0.1.4-win64.msi": "b" * 64}
    )
    assert "win64.msi\n" + "b" * 64 in puestas and "win64.zip\n" + "a" * 64 in puestas
    assert "0" * 64 not in puestas
    # Sin la línea del MSI en las notas, no se inventa nada.
    solo_zip = "SHA-256  voziris-0.1.3-win64.zip\n" + "0" * 64 + "\n"
    assert empaquetar.poner_hashes(solo_zip, {"x.msi": "b" * 64}) == solo_zip


def test_el_sha256sums_con_dos_lineas_lo_siguen_leyendo_las_versiones_instaladas() -> None:
    """«Actualizar» busca la línea del ZIP por su nombre: otra línea no le estorba."""
    import httpx

    from voziris import actualizaciones

    class Respuesta:
        status_code = 200
        url = httpx.URL("https://github.com/x")
        content = ("a" * 64 + "  voziris-0.1.4-win64.zip\n"
                   + "b" * 64 + "  voziris-0.1.4-win64.msi\n").encode()

    class Cliente:
        def get(self, _url: str) -> Respuesta:
            return Respuesta()

    novedad = actualizaciones.Novedad(
        version="0.1.4", pagina="p", zip_url="https://github.com/z", zip_tamano=1,
        sumas_url="https://github.com/s",
    )
    assert actualizaciones._hash_publicado(Cliente(), novedad) == "a" * 64


# --- el instalador, cuando lo lanza el MSI ------------------------------------------------


def test_el_registro_del_instalador_tiene_donde_escribirse(tmp_path: Path) -> None:
    carpeta = tmp_path / "Programs" / "Voziris"
    principal._crear_carpeta_del_registro(carpeta)
    assert carpeta.is_dir()
    principal._crear_carpeta_del_registro(carpeta)  # si ya está, nada


def test_no_crear_la_carpeta_del_registro_no_es_motivo_para_parar(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "ocupado").write_text("soy un archivo", encoding="utf-8")
    principal._crear_carpeta_del_registro(tmp_path / "ocupado" / "Voziris")
    assert "No se puede crear" in capsys.readouterr().err


def test_al_terminar_se_ofrece_la_api_solo_con_la_pregunta_al_final() -> None:
    destino = Path("C:/Users/x/AppData/Local/Programs/Voziris")
    sin_oferta = principal.texto_de_instalado(destino, None, ofrecer_api=False)
    assert "API" not in sin_oferta and "¿" not in sin_oferta
    assert str(destino) in sin_oferta and "Desinstalar" in sin_oferta

    con_oferta = principal.texto_de_instalado(destino, "No se pudo abrir.", ofrecer_api=True)
    assert con_oferta.endswith("¿Abrir ahora la guía para conectarla?")  # es un sí o no
    assert "más limitado" in con_oferta and "API de Groq" in con_oferta and "gratis" in con_oferta
    assert con_oferta.index("No se pudo abrir.") < con_oferta.index("API de Groq")


def test_hay_clave_de_api_solo_mira(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert not principal._hay_clave_de_api(tmp_path)  # sin config.toml: no, y no lo crea
    assert list(tmp_path.iterdir()) == []
    config = tmp_path / cfg.NOMBRE_ARCHIVO
    config.write_text('[motor.api]\nclave = ""\n', encoding="utf-8")
    assert not principal._hay_clave_de_api(tmp_path)
    config.write_text('[motor.api]\nclave = "gsk_de_mentira"\n', encoding="utf-8")
    assert principal._hay_clave_de_api(tmp_path)
    config.write_text("esto no es TOML [", encoding="utf-8")
    assert not principal._hay_clave_de_api(tmp_path)
    monkeypatch.setenv("GROQ_API_KEY", "gsk_de_mentira")
    assert principal._hay_clave_de_api(tmp_path)


def test_la_despedida_no_pregunta_nada_fuera_del_exe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Desde `python -m voziris` no hay cuadro: se imprime, sin pregunta colgando."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setattr(principal.winapi, "cuadro", lambda *a, **k: pytest.fail("sin cuadros"))
    abiertas: list[str] = []
    monkeypatch.setattr(principal, "_abrir_guia", lambda *a: abiertas.append("guía") or True)
    principal._despedir_instalacion(tmp_path, None)
    assert "Voziris instalado" in capsys.readouterr().out and abiertas == []


def test_la_despedida_abre_la_guia_solo_con_un_si(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(principal.winapi, "ES_WINDOWS", True)
    guia = cfg.ruta_de_guia(cfg.GUIA_API, tmp_path / "_internal")
    guia.parent.mkdir(parents=True)
    guia.write_text("<p>guía</p>", encoding="utf-8")
    cuadros: list[tuple[str, int]] = []
    abiertas: list[Path] = []
    respuesta = [principal.winapi.IDYES]

    def cuadro(titulo: str, texto: str, tipo: int = 0, segundos: float | None = None) -> int:
        cuadros.append((texto, tipo))
        return respuesta[0]

    monkeypatch.setattr(principal.winapi, "cuadro", cuadro)
    monkeypatch.setattr(principal, "_abrir_con_windows", abiertas.append)

    principal._despedir_instalacion(tmp_path, None)
    assert abiertas == [guia]  # la de la copia instalada, no la del instalador
    assert cuadros[0][0].endswith("¿Abrir ahora la guía para conectarla?")
    assert cuadros[0][1] & principal.winapi.MB_YESNO

    respuesta[0] = 7  # No
    principal._despedir_instalacion(tmp_path, None)
    assert len(abiertas) == 1

    # Con clave ya puesta no se insiste: el aviso de siempre, sin pregunta.
    (tmp_path / cfg.NOMBRE_ARCHIVO).write_text('[motor.api]\nclave = "gsk_x"\n', encoding="utf-8")
    principal._despedir_instalacion(tmp_path, None)
    assert "¿" not in cuadros[-1][0] and not cuadros[-1][1] & principal.winapi.MB_YESNO
    assert len(abiertas) == 1


def test_bloqueado_desde_la_carpeta_temporal_manda_al_zip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """«Sigue usando Voziris desde esta carpeta» no vale si la carpeta se borra en una hora."""
    from voziris import actualizaciones

    descargas = tmp_path / actualizaciones.CARPETA_DESCARGAS
    monkeypatch.setattr(actualizaciones, "carpeta_de_descargas", lambda: descargas)
    bloqueo = instalador.InstalacionFallida(instalador.MENSAJE_BLOQUEO, bloqueado=True)
    otro = instalador.InstalacionFallida("No hay sitio en el disco")

    dentro = descargas / "msi-0.1.4-1234" / "voziris" / "voziris.exe"
    monkeypatch.setattr(instalador, "ejecutable_actual", lambda: dentro)
    texto = principal._texto_de_fallo_al_instalar(bloqueo)
    assert texto == instalador.MENSAJE_BLOQUEO_TEMPORAL
    assert "desde esta carpeta" not in texto and "releases/latest" in texto
    assert "Control inteligente de aplicaciones" in texto and "No se ha instalado nada" in texto
    assert principal._texto_de_fallo_al_instalar(otro) == "No hay sitio en el disco"

    fuera = tmp_path / "Documentos" / "voziris" / "voziris.exe"
    monkeypatch.setattr(instalador, "ejecutable_actual", lambda: fuera)
    assert principal._texto_de_fallo_al_instalar(bloqueo) == instalador.MENSAJE_BLOQUEO


def test_los_avisos_de_paquete_incompleto_valen_para_el_msi_y_para_el_zip() -> None:
    assert "instalador (.msi)" in integridad.POR_QUE_FALTAN and "ZIP" in integridad.POR_QUE_FALTAN
    assert "antivirus" in integridad.POR_QUE_FALTAN


def test_abrir_guia_dice_si_no_esta(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    abiertas: list[Path] = []
    monkeypatch.setattr(principal, "_abrir_con_windows", abiertas.append)
    assert not principal._abrir_guia(cfg.GUIA_API, tmp_path)
    guia = cfg.ruta_de_guia(cfg.GUIA_API, tmp_path)
    guia.parent.mkdir(parents=True)
    guia.write_text("x", encoding="utf-8")
    assert principal._abrir_guia(cfg.GUIA_API, tmp_path) and abiertas == [guia]


# --- la bandeja: «Ayuda» ------------------------------------------------------------------


def _acciones(**mas: Any) -> AccionesBandeja:
    nada = lambda *_a: None  # noqa: E731
    return AccionesBandeja(
        dictar_ahora=nada, dictar_markdown=nada, alternar_corte=nada, abrir_ajustes=nada,
        cambiar_motor=nada, reintentar=nada, borrar_entrada=nada, salir=nada, **mas,
    )


def _textos(menu: Any) -> list[str]:
    return [str(i.text) for i in menu.items]


def test_ayuda_abre_las_guias_que_van_con_el_programa() -> None:
    pedidas: list[str] = []
    menu = Bandeja(_acciones(abrir_guia=pedidas.append)).construir_menu()
    textos = _textos(menu)
    assert textos.index("Ayuda") == textos.index("Acerca de Voziris") - 1
    ayuda = next(i for i in menu.items if str(i.text) == "Ayuda").submenu
    assert _textos(ayuda) == ["Guía de uso", "Conectar la API de Groq (recomendado)"]
    for entrada in ayuda.items:
        entrada(None)
    assert pedidas == [cfg.GUIA_DE_USO, cfg.GUIA_API] == [guia for guia, _ in AYUDA]


def test_sin_cablear_no_hay_ayuda() -> None:
    assert "Ayuda" not in _textos(Bandeja(_acciones()).construir_menu())


def test_acerca_de_dice_que_no_recoge_datos() -> None:
    texto = texto_acerca_de("1.2.3")
    assert "sin recogida de datos" in texto and "telemetría" in texto
    assert "NVIDIA" in texto and "CC-BY-4.0" in texto  # la atribución sigue


def test_el_motor_lleva_la_recomendacion_junto_a_la_clave() -> None:
    """El texto de la pestaña Motor. El botón, con su ventana, se prueba en test_ajustes."""
    from voziris.ui import ajustes  # importar el módulo no abre nada

    assert "Recomendado" in ajustes.RECOMENDACION_API and "Groq" in ajustes.RECOMENDACION_API
    assert "motor local" in ajustes.RECOMENDACION_API
