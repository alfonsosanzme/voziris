"""Tests de carga, validación y guardado de la configuración (VOZ-01).

Sin dependencias de Windows: corren en CI. Cada test trabaja sobre una copia
de `config.ejemplo.toml` en `tmp_path`, nunca sobre el del proyecto.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from voziris import config
from voziris.errores import ConfigInvalida
from voziris.tipos import Nivel

RAIZ = Path(__file__).resolve().parents[1]
EJEMPLO = RAIZ / config.NOMBRE_EJEMPLO


@pytest.fixture
def carpeta(tmp_path: Path) -> Path:
    """Carpeta «del ejecutable» con el ejemplo dentro y un vault para el Markdown."""
    (tmp_path / config.NOMBRE_EJEMPLO).write_text(EJEMPLO.read_text(encoding="utf-8"), "utf-8")
    (tmp_path / "vault").mkdir()
    return tmp_path


@pytest.fixture
def toml(carpeta: Path) -> Path:
    """Un config.toml válido: el ejemplo con la ruta del Markdown apuntando al vault."""
    texto = EJEMPLO.read_text(encoding="utf-8").replace(
        'ruta = "C:/Users/CAMBIAME/vault/entrada.md"', 'ruta = "./vault/entrada.md"'
    )
    ruta = carpeta / config.NOMBRE_ARCHIVO
    ruta.write_text(texto, encoding="utf-8")
    return ruta


def _sin_clave(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(config.VARIABLE_CLAVE, raising=False)


# --- carga -------------------------------------------------------------------


def test_el_ejemplo_coincide_con_los_valores_por_defecto(
    carpeta: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Los defaults de las dataclasses y config.ejemplo.toml no pueden separarse."""
    _sin_clave(monkeypatch)
    cfg = config.cargar(carpeta / config.NOMBRE_ARCHIVO)
    esperado = config.Config()
    # Las rutas del ejemplo se resuelven contra la carpeta del TOML.
    esperado.motor.local.carpeta = (carpeta / "modelos").resolve()
    assert cfg == esperado


def test_un_campo_por_opcion_con_el_mismo_nombre(toml: Path) -> None:
    """Cada opción del TOML existe como atributo con el mismo nombre en Config."""
    import tomlkit

    datos = tomlkit.parse(toml.read_text(encoding="utf-8")).unwrap()
    cfg = config.cargar(toml)

    def recorrer(tabla: dict[str, object], objeto: object, camino: str) -> None:
        for clave, valor in tabla.items():
            assert hasattr(objeto, clave), f"falta {camino}{clave}"
            if isinstance(valor, dict) and camino + clave != "proceso.sustituciones":
                recorrer(valor, getattr(objeto, clave), f"{camino}{clave}.")

    recorrer(datos, cfg, "")


def test_primer_arranque_copia_el_ejemplo(carpeta: Path) -> None:
    ruta = carpeta / config.NOMBRE_ARCHIVO
    assert not ruta.exists()
    cfg = config.cargar(ruta)
    assert ruta.exists()
    assert cfg.general.motor == "auto"
    # La ruta de relleno del ejemplo no impide arrancar: es un aviso (C-1).
    assert any("no existe" in a for a in cfg.avisos)


def test_sin_ejemplo_no_se_puede_arrancar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "carpeta_recursos", lambda: tmp_path)  # tampoco en los recursos
    with pytest.raises(ConfigInvalida, match="ni el ejemplo"):
        config.cargar(tmp_path / config.NOMBRE_ARCHIVO)


def test_rutas_relativas_se_resuelven_desde_la_carpeta_del_toml(toml: Path) -> None:
    cfg = config.cargar(toml)
    assert cfg.motor.local.carpeta == (toml.parent / "modelos").resolve()
    assert cfg.destino.markdown.ruta == (toml.parent / "vault" / "entrada.md").resolve()
    assert cfg.carpeta == toml.parent
    assert not cfg.avisos


def test_carpeta_base_congelado_y_en_desarrollo(monkeypatch: pytest.MonkeyPatch) -> None:
    assert config.carpeta_base() == RAIZ
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Apps\voziris\voziris.exe")
    assert config.carpeta_base() == Path(r"C:\Apps\voziris")


def test_clave_vacia_se_lee_del_entorno(toml: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(config.VARIABLE_CLAVE, "gsk_prueba")
    assert config.cargar(toml).motor.api.clave == "gsk_prueba"


def test_la_clave_del_toml_manda_sobre_el_entorno(
    toml: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(config.VARIABLE_CLAVE, "gsk_entorno")
    toml.write_text(toml.read_text("utf-8").replace('clave = ""', 'clave = "gsk_archivo"'), "utf-8")
    assert config.cargar(toml).motor.api.clave == "gsk_archivo"


def test_atajos_se_normalizan(toml: Path) -> None:
    toml.write_text(toml.read_text("utf-8").replace('"ctrl+win"', '"Win + Ctrl"'), "utf-8")
    cfg = config.cargar(toml)
    assert cfg.atajos.mantener == "ctrl+win"
    assert cfg.atajos.combinaciones()["clavar"].tecla == "space"


def test_toml_malformado(toml: Path) -> None:
    toml.write_text("[general\nmotor = ", encoding="utf-8")
    with pytest.raises(ConfigInvalida, match="no es un TOML válido"):
        config.cargar(toml)


# --- validación: qué opción y qué se admite ----------------------------------


def _con(toml: Path, viejo: str, nuevo: str) -> Path:
    texto = toml.read_text(encoding="utf-8")
    assert viejo in texto, viejo
    toml.write_text(texto.replace(viejo, nuevo), encoding="utf-8")
    return toml


@pytest.mark.parametrize(
    ("viejo", "nuevo", "fragmentos"),
    [
        ('motor = "auto"', 'motor = "nube"', ["[general] motor = 'nube'", "local | api | auto"]),
        (
            'nivel = "limpio"',
            'nivel = "pulido"',
            ["[proceso] nivel", "literal | limpio | reescritura"],
        ),
        ('cuantizacion = "int8"', 'cuantizacion = "int4"', ["cuantizacion", "int8 | fp32"]),
        ('metodo = "portapapeles"', 'metodo = "magia"', ["metodo", "portapapeles | tecleo"]),
        (
            'clavar = "ctrl+shift+space"',
            'clavar = "ctrl+win"',
            ["[atajos] clavar", "repite la combinación de «mantener»"],
        ),
        (
            'cancelar = "esc"',
            'cancelar = "ctrl+patata"',
            ["[atajos] cancelar", "«patata» no es una tecla conocida"],
        ),
        ("ganancia_db = 0", "ganancia_db = 35", ["ganancia_db = 35", "de -20 a 20"]),
        ("entradas = 50", "entradas = 0", ["[historial] entradas", "de 1 a 10000"]),
        ("sonidos = true", 'sonidos = "sí"', ["[audio] sonidos", "true o false"]),
        ("hilos = 0", "hilos = 2.5", ["hilos = 2.5", "número entero"]),
        ('idioma = "es"', 'idioma = "castellano"', ["idioma", "ISO-639-1"]),
        (
            'base_url = "https://api.groq.com/openai/v1"',
            'base_url = "api.groq.com"',
            ["base_url", "no es una URL"],
        ),
        (
            'formato = "- {sello} · {texto}"',
            'formato = "- {sello}"',
            ["formato", "no contiene {texto}"],
        ),
    ],
)
def test_rechaza_y_dice_que_opcion_y_que_admite(
    toml: Path, viejo: str, nuevo: str, fragmentos: list[str]
) -> None:
    _con(toml, viejo, nuevo)
    with pytest.raises(ConfigInvalida) as info:
        config.cargar(toml)
    for fragmento in fragmentos:
        assert fragmento in str(info.value), str(info.value)


def test_acumula_todos_los_errores(toml: Path) -> None:
    _con(toml, 'motor = "auto"', 'motor = "nube"')
    _con(toml, "entradas = 50", "entradas = 0")
    with pytest.raises(ConfigInvalida, match="2 error"):
        config.cargar(toml)


def test_opcion_desconocida_es_aviso_no_error(toml: Path) -> None:
    _con(toml, "sonidos = true", "sonidos = true\nsonido = false")
    cfg = config.cargar(toml)
    assert cfg.audio.sonidos is True
    assert "[audio] sonido: opción desconocida" in cfg.avisos[0]


def test_carpeta_de_markdown_inexistente_es_aviso_al_cargar_y_error_al_guardar(
    toml: Path,
) -> None:
    _con(toml, 'ruta = "./vault/entrada.md"', 'ruta = "./no-existe/entrada.md"')
    cfg = config.cargar(toml)
    assert any("no-existe" in a and "no existe" in a for a in cfg.avisos)
    with pytest.raises(ConfigInvalida, match="no-existe"):
        config.guardar(cfg)
    with pytest.raises(ConfigInvalida):
        config.cargar(toml, estricto=True)


# --- guardado -----------------------------------------------------------------


def test_guardar_conserva_comentarios_orden_y_rutas_relativas(toml: Path) -> None:
    antes = toml.read_text(encoding="utf-8")
    cfg = config.cargar(toml)
    cfg.audio.ganancia_db = 6
    cfg.proceso.nivel = Nivel.REESCRITURA
    config.guardar(cfg)
    despues = toml.read_text(encoding="utf-8")

    cambiadas = [
        (a, d) for a, d in zip(antes.splitlines(), despues.splitlines(), strict=True) if a != d
    ]
    assert len(cambiadas) == 2, cambiadas
    assert cambiadas[0][0].startswith("ganancia_db = 0") and "# -20 a +20" in cambiadas[0][1]
    assert cambiadas[0][1].startswith("ganancia_db = 6")
    assert cambiadas[1][1].startswith('nivel = "reescritura"') and "# C4" in cambiadas[1][1]
    assert 'carpeta = "./modelos"' in despues  # la ruta relativa no se volvió absoluta

    recargada = config.cargar(toml)
    assert recargada.audio.ganancia_db == 6
    assert recargada.proceso.nivel is Nivel.REESCRITURA


def test_guardar_sin_cambios_deja_el_archivo_igual(toml: Path) -> None:
    antes = toml.read_bytes()
    config.guardar(config.cargar(toml))
    assert toml.read_bytes() == antes


def test_guardar_no_escribe_la_clave_del_entorno(
    toml: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(config.VARIABLE_CLAVE, "gsk_secreta")
    cfg = config.cargar(toml)
    cfg.audio.sonidos = False
    config.guardar(cfg)
    assert "gsk_secreta" not in toml.read_text(encoding="utf-8")


def test_guardar_escribe_la_clave_puesta_por_el_usuario(
    toml: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sin_clave(monkeypatch)
    cfg = config.cargar(toml)
    cfg.motor.api.clave = "gsk_nueva"
    config.guardar(cfg)
    assert 'clave = "gsk_nueva"' in toml.read_text(encoding="utf-8")


def test_guardar_sustituciones_y_diccionario(toml: Path) -> None:
    cfg = config.cargar(toml)
    cfg.proceso.sustituciones = {"punto y aparte": "\n\n", "coma": ","}
    cfg.proceso.diccionario = ["Creatics", "Kairis"]
    config.guardar(cfg)
    recargada = config.cargar(toml)
    assert recargada.proceso.sustituciones == {"punto y aparte": "\n\n", "coma": ","}
    assert recargada.proceso.diccionario == ["Creatics", "Kairis"]
    assert "nueva línea" not in toml.read_text(encoding="utf-8")


def test_guardar_anade_opciones_que_faltan_en_un_toml_viejo(toml: Path) -> None:
    """Un config.toml de una versión anterior no tiene `cuantizacion`: se añade."""
    _con(toml, 'cuantizacion = "int8"', "")
    cfg = config.cargar(toml)
    assert cfg.motor.local.cuantizacion == "int8"
    cfg.motor.local.cuantizacion = "fp32"
    config.guardar(cfg)
    assert 'cuantizacion = "fp32"' in toml.read_text(encoding="utf-8")
    assert config.cargar(toml).motor.local.cuantizacion == "fp32"


def test_guardar_es_atomico_no_deja_temporal(toml: Path) -> None:
    cfg = config.cargar(toml)
    cfg.audio.sonidos = False
    config.guardar(cfg)
    assert not list(toml.parent.glob("*.tmp"))
    assert os.path.getsize(toml) > 0


def test_congelado_copia_el_ejemplo_desde_los_recursos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """En el exe, PyInstaller deja config.ejemplo.toml en _internal (sys._MEIPASS),
    no junto al .exe. El primer arranque tiene que encontrarlo ahí."""
    interno = tmp_path / "_internal"
    interno.mkdir()
    (interno / config.NOMBRE_EJEMPLO).write_text(EJEMPLO.read_text(encoding="utf-8"), "utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(interno), raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "voziris.exe"))
    assert config.carpeta_recursos() == interno
    cfg = config.cargar()  # sin ruta: junto al ejecutable
    assert (tmp_path / config.NOMBRE_ARCHIVO).exists()
    assert cfg.general.motor == "auto"


def test_cinco_atajos_y_corte_por_silencio(toml: Path) -> None:
    cfg = config.cargar(toml)
    assert cfg.atajos.clavar_markdown == "ctrl+shift+m"
    assert cfg.audio.corte_por_silencio is True
    assert set(cfg.atajos.combinaciones()) == set(config.NOMBRES_ATAJOS)
    _con(toml, 'clavar_markdown = "ctrl+shift+m"', 'clavar_markdown = ""')
    _con(toml, "corte_por_silencio = true", "corte_por_silencio = false")
    cfg = config.cargar(toml)
    assert cfg.atajos.clavar_markdown == "" and "clavar_markdown" not in cfg.atajos.combinaciones()
    assert cfg.audio.corte_por_silencio is False
