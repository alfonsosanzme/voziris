"""Carga, validación y guardado del config.toml.

Dos reglas que vienen del requisito de portabilidad (F1):

  - La configuración vive JUNTO AL EJECUTABLE, no en %APPDATA%. Es lo que
    permite llevar la carpeta en un USB con los ajustes dentro.
  - Las rutas relativas se resuelven desde la carpeta del config.toml, que es
    la del ejecutable, nunca desde el directorio de trabajo: Voziris arranca
    con Windows y ese directorio no es predecible.

`Config` refleja el TOML sección a sección y opción a opción, con los mismos
nombres, para que el panel de ajustes (VOZ-60) pueda recorrerlo sin una tabla
de correspondencias. Los valores por defecto de las dataclasses son los del
`config.ejemplo.toml`; un test comprueba que no se separan.

`ConfigInvalida` es el único error que detiene el arranque. Con una
excepción decidida en el plan (C-1): una ruta de Markdown cuya carpeta no
existe es un aviso al cargar, porque el ejemplo trae una ruta de relleno y el
primer arranque no debe fallar; solo `guardar()` la rechaza.

Issue: VOZ-01.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, TypeVar

import tomlkit
from tomlkit.exceptions import TOMLKitError

from voziris import teclas
from voziris.errores import ConfigInvalida
from voziris.tipos import Nivel

NOMBRE_ARCHIVO = "config.toml"
NOMBRE_EJEMPLO = "config.ejemplo.toml"
VARIABLE_CLAVE = "GROQ_API_KEY"
"""Variable de entorno de la que se lee la clave de API si el TOML la deja vacía."""

MOTORES = ("local", "api", "auto")
CUANTIZACIONES = ("int8", "fp32")
METODOS = ("portapapeles", "tecleo")
NIVELES = tuple(n.value for n in Nivel)
AL_DICTAR = ("nada", "atenuar", "silenciar")
"""[audio].al_dictar — qué pasa con la música mientras se dicta. Ver audio/mezclador.py."""
NOMBRES_ATAJOS = ("mantener", "clavar", "markdown", "clavar_markdown", "cancelar")


def carpeta_base() -> Path:
    """Carpeta donde viven config.toml, modelos/ e historial/.

    Congelado por PyInstaller, `sys.executable` apunta al .exe; en desarrollo,
    a python.exe, así que se usa la raíz del proyecto. `sys._MEIPASS` NO sirve:
    en modo onefile apunta a la carpeta temporal de extracción, que se borra.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parents[2]


def carpeta_recursos() -> Path:
    """Donde viven los datos que viajan con el programa: config.ejemplo.toml, assets/.

    Congelado en modo onedir, PyInstaller los deja en `_internal/` (que es
    `sys._MEIPASS`), no junto al .exe. En desarrollo, en la raíz del proyecto.
    Lo que el usuario edita (config.toml, modelos/, historial/) sigue en
    `carpeta_base()`.
    """
    base = getattr(sys, "_MEIPASS", None)
    if getattr(sys, "frozen", False) and base:
        return Path(base)
    return carpeta_base()


def _ejemplo_junto_a(carpeta: Path) -> Path:
    """El config.ejemplo.toml: junto al config.toml si está, o en los recursos."""
    candidato = carpeta / NOMBRE_EJEMPLO
    if candidato.is_file():
        return candidato
    return carpeta_recursos() / NOMBRE_EJEMPLO


# ---------------------------------------------------------------------------
# Secciones. Un campo por opción, con el mismo nombre que en el TOML.
# Los valores por defecto son los de config.ejemplo.toml.
# ---------------------------------------------------------------------------


@dataclass
class SeccionGeneral:
    idioma: str = "es"
    motor: str = "auto"
    arranque_con_windows: bool = True


@dataclass
class SeccionAtajos:
    mantener: str = "ctrl+win"
    clavar: str = "ctrl+shift+space"
    markdown: str = "ctrl+alt+m"
    clavar_markdown: str = "ctrl+shift+m"
    cancelar: str = "esc"

    def combinaciones(self) -> dict[str, teclas.Combinacion]:
        """Las que están configuradas, ya analizadas. Solo válido tras `cargar()`."""
        return {
            nombre: teclas.analizar(getattr(self, nombre))
            for nombre in NOMBRES_ATAJOS
            if getattr(self, nombre)
        }


@dataclass
class SeccionAudio:
    dispositivo: str = ""
    ganancia_db: float = 0.0
    buffer_previo_ms: int = 500
    silencio_corte_ms: int = 2000
    corte_por_silencio: bool = True
    sonidos: bool = True
    al_dictar: str = "silenciar"
    """Qué hacer con el audio de otras aplicaciones mientras se dicta (VOZ-75)."""


@dataclass
class SeccionMotorLocal:
    modelo: str = "nemo-parakeet-tdt-0.6b-v3"
    carpeta: Path = Path("./modelos")
    hilos: int = 0
    cuantizacion: str = "int8"


@dataclass
class SeccionMotorApi:
    base_url: str = "https://api.groq.com/openai/v1"
    modelo: str = "whisper-large-v3-turbo"
    clave: str = ""
    timeout_s: int = 15


@dataclass
class SeccionMotor:
    local: SeccionMotorLocal = field(default_factory=SeccionMotorLocal)
    api: SeccionMotorApi = field(default_factory=SeccionMotorApi)


@dataclass
class SeccionProceso:
    nivel: Nivel = Nivel.LIMPIO
    diccionario: list[str] = field(
        default_factory=lambda: ["Creatics", "Kairis", "Voziris", "Obsidian", "Valladolid"]
    )
    llm_modelo: str = ""
    sustituciones: dict[str, str] = field(
        default_factory=lambda: {"punto y aparte": "\n\n", "nueva línea": "\n"}
    )


@dataclass
class SeccionAppActiva:
    metodo: str = "portapapeles"
    restaurar_portapapeles: bool = True
    auto_enter: bool = False


@dataclass
class SeccionMarkdown:
    ruta: Path = Path("C:/Users/CAMBIAME/vault/entrada.md")
    formato: str = "- {sello} · {texto}"
    sello: str = "%Y-%m-%d %H:%M"
    separador: str = ""


@dataclass
class SeccionDestino:
    app_activa: SeccionAppActiva = field(default_factory=SeccionAppActiva)
    markdown: SeccionMarkdown = field(default_factory=SeccionMarkdown)


@dataclass
class SeccionHistorial:
    entradas: int = 50
    guardar_audio: bool = False


@dataclass
class Config:
    """Configuración completa, ya validada y con rutas absolutas.

    Se construye una vez al arrancar y se pasa hacia abajo. Ningún módulo lee
    el TOML por su cuenta. Los dos últimos campos no son opciones del TOML.
    """

    general: SeccionGeneral = field(default_factory=SeccionGeneral)
    atajos: SeccionAtajos = field(default_factory=SeccionAtajos)
    audio: SeccionAudio = field(default_factory=SeccionAudio)
    motor: SeccionMotor = field(default_factory=SeccionMotor)
    proceso: SeccionProceso = field(default_factory=SeccionProceso)
    destino: SeccionDestino = field(default_factory=SeccionDestino)
    historial: SeccionHistorial = field(default_factory=SeccionHistorial)

    ruta_archivo: Path = field(
        default=Path(NOMBRE_ARCHIVO), compare=False, metadata={"toml": False}
    )
    """De dónde se cargó. `guardar()` escribe ahí por defecto."""
    avisos: list[str] = field(default_factory=list, compare=False, metadata={"toml": False})
    """Lo que no impidió cargar pero el usuario debe saber."""

    @property
    def carpeta(self) -> Path:
        """Carpeta del config.toml: base para modelos/, historial/ y el log."""
        return self.ruta_archivo.parent


# ---------------------------------------------------------------------------
# Carga
# ---------------------------------------------------------------------------

T = TypeVar("T")


class _Errores:
    """Acumula los errores para lanzar un solo ConfigInvalida con todos."""

    def __init__(self) -> None:
        self.lista: list[str] = []

    def anotar(self, seccion: str, opcion: str, valor: object, motivo: str, admite: str) -> None:
        self.lista.append(f"[{seccion}] {opcion} = {valor!r}: {motivo}. Admite: {admite}")

    def lanzar_si_hay(self, ruta: Path) -> None:
        if self.lista:
            cuerpo = "\n  - ".join(self.lista)
            raise ConfigInvalida(f"{ruta} tiene {len(self.lista)} error(es):\n  - {cuerpo}")


def _es_tipo(valor: object, tipo: type) -> bool:
    # bool es subclase de int en Python; aquí no vale como número.
    if tipo is int:
        return isinstance(valor, int) and not isinstance(valor, bool)
    if tipo is float:
        return isinstance(valor, int | float) and not isinstance(valor, bool)
    return isinstance(valor, tipo)


_NOMBRES_TIPO: dict[type, str] = {
    str: "texto entre comillas",
    int: "número entero",
    float: "número",
    bool: "true o false",
    list: "lista de textos",
    dict: "tabla de texto = texto",
}


def _leer(
    datos: dict[str, Any],
    seccion: str,
    opcion: str,
    tipo: type,
    por_defecto: T,
    errores: _Errores,
) -> T:
    """Lee una opción de la sección, con el tipo comprobado.

    Si falta, vale el valor por defecto. Si el tipo no cuadra, se anota el
    error y se sigue con el valor por defecto para poder acumular más errores.
    """
    if opcion not in datos:
        return por_defecto
    valor: Any = datos[opcion]
    if not _es_tipo(valor, tipo):
        errores.anotar(seccion, opcion, valor, "tipo incorrecto", _NOMBRES_TIPO[tipo])
        return por_defecto
    if tipo is float and isinstance(valor, int):
        valor = float(valor)
    resultado: T = valor
    return resultado


def _elegir(
    datos: dict[str, Any],
    seccion: str,
    opcion: str,
    admitidos: Iterable[str],
    por_defecto: str,
    errores: _Errores,
) -> str:
    valor = _leer(datos, seccion, opcion, str, por_defecto, errores)
    admitidos = tuple(admitidos)
    if valor not in admitidos:
        errores.anotar(seccion, opcion, valor, "valor no reconocido", " | ".join(admitidos))
        return por_defecto
    return valor


def _rango(
    datos: dict[str, Any],
    seccion: str,
    opcion: str,
    tipo: type,
    minimo: float,
    maximo: float,
    por_defecto: T,
    errores: _Errores,
) -> T:
    valor = _leer(datos, seccion, opcion, tipo, por_defecto, errores)
    numero: float = valor  # type: ignore[assignment]
    if not (minimo <= numero <= maximo):
        errores.anotar(seccion, opcion, valor, "fuera de rango", f"de {minimo:g} a {maximo:g}")
        return por_defecto
    return valor


def _seccion(datos: dict[str, Any], nombre: str, errores: _Errores) -> dict[str, Any]:
    """Devuelve la tabla de una sección («a.b» para anidadas), o {} si no está."""
    actual: Any = datos
    for parte in nombre.split("."):
        if not isinstance(actual, dict) or parte not in actual:
            return {}
        actual = actual[parte]
    if not isinstance(actual, dict):
        padre, _, hijo = nombre.rpartition(".")
        errores.anotar(padre or hijo, hijo, actual, "debería ser una sección", f"[{nombre}]")
        return {}
    tabla: dict[str, Any] = actual
    return tabla


def _resolver(base: Path, texto: str) -> Path:
    ruta = Path(texto).expanduser()
    return ruta if ruta.is_absolute() else (base / ruta).resolve()


_CONOCIDAS: dict[str, tuple[str, ...]] = {
    "general": ("idioma", "motor", "arranque_con_windows"),
    "atajos": NOMBRES_ATAJOS,
    "audio": (
        "dispositivo", "ganancia_db", "buffer_previo_ms", "silencio_corte_ms",
        "corte_por_silencio", "sonidos", "al_dictar",
    ),
    "motor": (),
    "motor.local": ("modelo", "carpeta", "hilos", "cuantizacion"),
    "motor.api": ("base_url", "modelo", "clave", "timeout_s"),
    "proceso": ("nivel", "diccionario", "llm_modelo"),
    "proceso.sustituciones": (),
    "destino": (),
    "destino.app_activa": ("metodo", "restaurar_portapapeles", "auto_enter"),
    "destino.markdown": ("ruta", "formato", "sello", "separador"),
    "historial": ("entradas", "guardar_audio"),
}


def _desconocidas(datos: dict[str, Any], avisos: list[str]) -> None:
    """Opciones o secciones que no existen: aviso, no error. Suelen ser erratas."""
    for seccion, opciones in _CONOCIDAS.items():
        tabla: Any = datos
        for parte in seccion.split("."):
            tabla = tabla.get(parte, {}) if isinstance(tabla, dict) else {}
        if not isinstance(tabla, dict) or seccion == "proceso.sustituciones":
            continue
        profundidad = len(seccion.split("."))
        hijas = {
            s.split(".")[profundidad] for s in _CONOCIDAS if s.startswith(seccion + ".")
        }
        for clave in tabla:
            if clave not in opciones and clave not in hijas:
                avisos.append(f"[{seccion}] {clave}: opción desconocida, se ignora")
    raices = {s.split(".")[0] for s in _CONOCIDAS}
    for clave in datos:
        if clave not in raices:
            avisos.append(f"[{clave}]: sección desconocida, se ignora")


def _construir(datos: dict[str, Any], ruta: Path, estricto: bool) -> Config:
    """Convierte el diccionario del TOML en `Config`, validando todo."""
    errores = _Errores()
    avisos: list[str] = []
    base = ruta.parent

    g = _seccion(datos, "general", errores)
    dg = SeccionGeneral()
    general = SeccionGeneral(
        idioma=_leer(g, "general", "idioma", str, dg.idioma, errores),
        motor=_elegir(g, "general", "motor", MOTORES, dg.motor, errores),
        arranque_con_windows=_leer(
            g, "general", "arranque_con_windows", bool, dg.arranque_con_windows, errores
        ),
    )
    if len(general.idioma) != 2 or not general.idioma.isalpha():
        errores.anotar(
            "general", "idioma", general.idioma, "no es un código ISO-639-1",
            "dos letras, p. ej. es, en, fr",
        )

    a = _seccion(datos, "atajos", errores)
    da = SeccionAtajos()
    atajos = SeccionAtajos(
        **{n: _leer(a, "atajos", n, str, getattr(da, n), errores) for n in NOMBRES_ATAJOS}
    )
    vistos: dict[str, str] = {}
    for nombre in NOMBRES_ATAJOS:
        texto = getattr(atajos, nombre)
        if not texto.strip():
            setattr(atajos, nombre, "")  # vacío: ese atajo queda desactivado
            continue
        try:
            combo = teclas.analizar(texto)
        except ValueError as e:
            errores.anotar("atajos", nombre, texto, str(e), "teclas separadas por «+»")
            continue
        setattr(atajos, nombre, combo.texto)
        if combo.texto in vistos:
            errores.anotar(
                "atajos", nombre, texto, f"repite la combinación de «{vistos[combo.texto]}»",
                "combinaciones distintas entre sí",
            )
        vistos[combo.texto] = nombre

    au = _seccion(datos, "audio", errores)
    dau = SeccionAudio()
    audio = SeccionAudio(
        dispositivo=_leer(au, "audio", "dispositivo", str, dau.dispositivo, errores),
        ganancia_db=_rango(au, "audio", "ganancia_db", float, -20, 20, dau.ganancia_db, errores),
        buffer_previo_ms=_rango(
            au, "audio", "buffer_previo_ms", int, 0, 5000, dau.buffer_previo_ms, errores
        ),
        silencio_corte_ms=_rango(
            au, "audio", "silencio_corte_ms", int, 200, 10000, dau.silencio_corte_ms, errores
        ),
        corte_por_silencio=_leer(
            au, "audio", "corte_por_silencio", bool, dau.corte_por_silencio, errores
        ),
        sonidos=_leer(au, "audio", "sonidos", bool, dau.sonidos, errores),
        al_dictar=_elegir(au, "audio", "al_dictar", AL_DICTAR, dau.al_dictar, errores),
    )

    ml = _seccion(datos, "motor.local", errores)
    dml = SeccionMotorLocal()
    local = SeccionMotorLocal(
        modelo=_leer(ml, "motor.local", "modelo", str, dml.modelo, errores),
        carpeta=_resolver(
            base, _leer(ml, "motor.local", "carpeta", str, str(dml.carpeta), errores)
        ),
        hilos=_rango(ml, "motor.local", "hilos", int, 0, 64, dml.hilos, errores),
        cuantizacion=_elegir(
            ml, "motor.local", "cuantizacion", CUANTIZACIONES, dml.cuantizacion, errores
        ),
    )

    ma = _seccion(datos, "motor.api", errores)
    dma = SeccionMotorApi()
    api = SeccionMotorApi(
        base_url=_leer(ma, "motor.api", "base_url", str, dma.base_url, errores).rstrip("/"),
        modelo=_leer(ma, "motor.api", "modelo", str, dma.modelo, errores),
        clave=_leer(ma, "motor.api", "clave", str, dma.clave, errores),
        timeout_s=_rango(ma, "motor.api", "timeout_s", int, 1, 120, dma.timeout_s, errores),
    )
    if not api.base_url.startswith(("http://", "https://")):
        errores.anotar(
            "motor.api", "base_url", api.base_url, "no es una URL",
            "https://... compatible con la API de OpenAI",
        )
    if not api.clave:
        api.clave = os.environ.get(VARIABLE_CLAVE, "")

    p = _seccion(datos, "proceso", errores)
    dp = SeccionProceso()
    nivel_texto = _elegir(p, "proceso", "nivel", NIVELES, dp.nivel.value, errores)
    diccionario: list[Any] = _leer(p, "proceso", "diccionario", list, dp.diccionario, errores)
    if not all(isinstance(x, str) for x in diccionario):
        errores.anotar(
            "proceso", "diccionario", diccionario, "hay elementos que no son texto",
            "lista de textos entre comillas",
        )
        diccionario = list(dp.diccionario)
    sustituciones: dict[str, Any] = _seccion(datos, "proceso.sustituciones", errores)
    if "sustituciones" not in p:
        sustituciones = dict(dp.sustituciones)
    if not all(isinstance(v, str) for v in sustituciones.values()):
        errores.anotar(
            "proceso.sustituciones", "*", sustituciones, "hay valores que no son texto",
            '"frase" = "texto"',
        )
        sustituciones = dict(dp.sustituciones)
    proceso = SeccionProceso(
        nivel=Nivel(nivel_texto),
        diccionario=[str(x) for x in diccionario],
        llm_modelo=_leer(p, "proceso", "llm_modelo", str, dp.llm_modelo, errores),
        sustituciones={str(k): str(v) for k, v in sustituciones.items()},
    )

    aa = _seccion(datos, "destino.app_activa", errores)
    daa = SeccionAppActiva()
    app_activa = SeccionAppActiva(
        metodo=_elegir(aa, "destino.app_activa", "metodo", METODOS, daa.metodo, errores),
        restaurar_portapapeles=_leer(
            aa, "destino.app_activa", "restaurar_portapapeles", bool,
            daa.restaurar_portapapeles, errores,
        ),
        auto_enter=_leer(aa, "destino.app_activa", "auto_enter", bool, daa.auto_enter, errores),
    )

    md = _seccion(datos, "destino.markdown", errores)
    dmd = SeccionMarkdown()
    markdown = SeccionMarkdown(
        ruta=_resolver(base, _leer(md, "destino.markdown", "ruta", str, str(dmd.ruta), errores)),
        formato=_leer(md, "destino.markdown", "formato", str, dmd.formato, errores),
        sello=_leer(md, "destino.markdown", "sello", str, dmd.sello, errores),
        separador=_leer(md, "destino.markdown", "separador", str, dmd.separador, errores),
    )
    if "{texto}" not in markdown.formato:
        errores.anotar(
            "destino.markdown", "formato", markdown.formato, "no contiene {texto}",
            "un texto con los marcadores {sello} y {texto}",
        )
    if not markdown.ruta.parent.is_dir():
        mensaje = (
            f"[destino.markdown] ruta = {str(markdown.ruta)!r}: "
            f"la carpeta {markdown.ruta.parent} no existe"
        )
        if estricto:
            errores.lista.append(mensaje + ". Admite: un archivo .md en una carpeta existente")
        else:
            avisos.append(mensaje + "; el atajo de Markdown no funcionará hasta corregirla")

    h = _seccion(datos, "historial", errores)
    dh = SeccionHistorial()
    historial = SeccionHistorial(
        entradas=_rango(h, "historial", "entradas", int, 1, 10000, dh.entradas, errores),
        guardar_audio=_leer(h, "historial", "guardar_audio", bool, dh.guardar_audio, errores),
    )

    _desconocidas(datos, avisos)
    errores.lanzar_si_hay(ruta)

    return Config(
        general=general,
        atajos=atajos,
        audio=audio,
        motor=SeccionMotor(local=local, api=api),
        proceso=proceso,
        destino=SeccionDestino(app_activa=app_activa, markdown=markdown),
        historial=historial,
        ruta_archivo=ruta,
        avisos=avisos,
    )


def _leer_documento(ruta: Path) -> tomlkit.TOMLDocument:
    try:
        return tomlkit.parse(ruta.read_text(encoding="utf-8"))
    except TOMLKitError as e:
        raise ConfigInvalida(f"{ruta} no es un TOML válido: {e}") from e
    except OSError as e:
        raise ConfigInvalida(f"No se puede leer {ruta}: {e}") from e


def cargar(ruta: Path | None = None, ejemplo: Path | None = None, estricto: bool = False) -> Config:
    """Lee y valida el TOML.

    Args:
        ruta: el config.toml. Por defecto, el de la carpeta del ejecutable.
        ejemplo: el config.ejemplo.toml que se copia si `ruta` no existe. Por
            defecto, el que está junto a `ruta`.
        estricto: si es True, una carpeta de Markdown inexistente es error y no
            aviso. Lo usa `guardar()`; al arrancar es False (C-1 del plan).

    Raises:
        ConfigInvalida: con TODOS los errores encontrados, cada uno con la
            opción, el valor recibido y lo que se admite.
    """
    ruta = (ruta or carpeta_base() / NOMBRE_ARCHIVO).resolve()
    if not ruta.exists():
        ejemplo = ejemplo or _ejemplo_junto_a(ruta.parent)
        if not ejemplo.is_file():
            raise ConfigInvalida(
                f"No existe {ruta} ni el ejemplo {ejemplo} del que copiarlo. "
                "Vuelve a descomprimir Voziris o crea el archivo a mano."
            )
        shutil.copyfile(ejemplo, ruta)
    documento = _leer_documento(ruta)
    return _construir(documento.unwrap(), ruta, estricto)


# ---------------------------------------------------------------------------
# Guardado
# ---------------------------------------------------------------------------


def _campos(seccion: object) -> dict[str, Any]:
    salida: dict[str, Any] = {}
    for f in fields(seccion):  # type: ignore[arg-type]
        valor = getattr(seccion, f.name)
        if isinstance(valor, Nivel):
            valor = valor.value
        elif isinstance(valor, Path):
            valor = str(valor)
        salida[f.name] = valor
    return salida


def _valores_toml(config: Config) -> dict[str, dict[str, Any]]:
    """{"seccion.subseccion": {opcion: valor}} con los valores tal como van al TOML."""
    proceso = _campos(config.proceso)
    sustituciones = proceso.pop("sustituciones")
    return {
        "general": _campos(config.general),
        "atajos": _campos(config.atajos),
        "audio": _campos(config.audio),
        "motor.local": _campos(config.motor.local),
        "motor.api": _campos(config.motor.api),
        "proceso": proceso,
        "proceso.sustituciones": dict(sustituciones),
        "destino.app_activa": _campos(config.destino.app_activa),
        "destino.markdown": _campos(config.destino.markdown),
        "historial": _campos(config.historial),
    }


def _a_dict(valores: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Deshace «a.b» en diccionarios anidados, para validar lo que se va a guardar."""
    salida: dict[str, Any] = {}
    for seccion, opciones in valores.items():
        actual = salida
        for parte in seccion.split("."):
            actual = actual.setdefault(parte, {})
        actual.update(opciones)
    return salida


def _tabla(documento: Any, nombre: str) -> Any:
    """La tabla tomlkit de «a.b», creándola si no existe."""
    actual = documento
    for parte in nombre.split("."):
        if parte not in actual:
            actual[parte] = tomlkit.table()
        actual = actual[parte]
    return actual


def _mismo_valor(base: Path, actual: object, nuevo: object) -> bool:
    if isinstance(actual, str) and isinstance(nuevo, str) and actual != nuevo:
        # Una ruta relativa del archivo no se convierte en absoluta si apunta al
        # mismo sitio: así «./modelos» sigue siendo portable después de guardar.
        parece_ruta = any(c in actual for c in "/\\") and any(c in nuevo for c in "/\\")
        if parece_ruta:
            return _resolver(base, actual) == _resolver(base, nuevo)
    return bool(actual == nuevo)


def guardar(config: Config, ruta: Path | None = None) -> None:
    """Reescribe el TOML conservando comentarios y orden. Lo usa VOZ-60.

    Solo toca las opciones cuyo valor cambia, así una ruta relativa sigue
    relativa y los comentarios de cada línea se quedan donde estaban. La clave
    de API que vino de la variable de entorno no se escribe.

    Raises:
        ConfigInvalida: la configuración no pasa la validación estricta (por
            ejemplo, la carpeta del Markdown no existe).
    """
    ruta = (ruta or config.ruta_archivo).resolve()
    origen = ruta if ruta.exists() else _ejemplo_junto_a(ruta.parent)
    documento = _leer_documento(origen) if origen.exists() else tomlkit.document()
    base = ruta.parent

    nuevos = _valores_toml(config)
    clave = config.motor.api.clave
    if clave and clave == os.environ.get(VARIABLE_CLAVE) and not _clave_en(documento):
        nuevos["motor.api"]["clave"] = ""

    # Validar ANTES de escribir, con lo que de verdad va a quedar en el archivo.
    _construir(_a_dict(nuevos), ruta, estricto=True)

    for seccion, opciones in nuevos.items():
        tabla = _tabla(documento, seccion)
        if seccion == "proceso.sustituciones":
            for sobrante in [k for k in tabla if k not in opciones]:
                del tabla[sobrante]
        for opcion, valor in opciones.items():
            if opcion in tabla and _mismo_valor(base, _plano(tabla[opcion]), valor):
                continue
            tabla[opcion] = valor

    texto = tomlkit.dumps(documento)
    if origen.exists() and b"\r\n" in origen.read_bytes():
        # Respetar los finales de línea del archivo: tomlkit siempre emite LF.
        texto = texto.replace("\n", "\r\n")
    temporal = ruta.with_suffix(ruta.suffix + ".tmp")
    try:
        temporal.write_text(texto, encoding="utf-8", newline="")
        os.replace(temporal, ruta)
    except OSError as e:
        raise ConfigInvalida(f"No se puede escribir {ruta}: {e}") from e


def _plano(item: Any) -> Any:
    """Valor Python de un elemento tomlkit. Los booleanos ya vienen planos."""
    return item.unwrap() if hasattr(item, "unwrap") else item


def _clave_en(documento: Any) -> bool:
    """True si el archivo ya trae una clave de API escrita."""
    try:
        return bool(documento["motor"]["api"]["clave"])
    except (KeyError, TypeError):
        return False
