"""VOZ-82 — avisar de las versiones nuevas e instalarlas desde GitHub.

Nada sale del equipo sin permiso. `[actualizaciones] buscar` empieza en
«preguntar»: la primera vez que arranca, Voziris pregunta si quieres que lo
mire. Con «sí», una vez al día pide a la API de GitHub cuál es la última
versión publicada. Es una petición HTTPS que no lleva nada tuyo: lo que viaja
es lo que lleva cualquier conexión (la IP) y la versión de Voziris en el
User-Agent. Con «no», no se conecta nunca por su cuenta. «Buscar
actualizaciones», en la bandeja, consulta cuando se pulsa, diga lo que diga
la opción.

Instalar la versión nueva es un proceso aparte (`voziris --actualizar`), con
ventana de progreso y Cancelar:

1. descarga el ZIP de la release y su `SHA256SUMS.txt`;
2. comprueba el SHA-256 y lo descomprime en una carpeta temporal;
3. comprueba el paquete con su manifiesto (VOZ-81);
4. arranca el instalador de la versión nueva (`voziris.exe --instalar`). Es
   el de siempre: verifica la copia, cierra esta Voziris justo antes de
   cambiar el programa y abre la nueva.

Lo que protege y lo que no: el SHA-256 sale de la misma release que el ZIP.
Detecta una descarga cortada o dañada, no una release falsa. Sin firma de
código, la confianza es la de la cuenta de GitHub. Solo se descarga de las
URL de releases de este repositorio, y solo por HTTPS.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import threading
import time
import zipfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from voziris import __version__, integridad
from voziris.errores import VozirisError

log = logging.getLogger(__name__)

REPOSITORIO = "alfonsosanzme/voziris"
URL_ULTIMA = f"https://api.github.com/repos/{REPOSITORIO}/releases/latest"
PAGINA = f"https://github.com/{REPOSITORIO}/releases/latest"
"""Donde se descarga a mano. Para las copias portables y si algo falla."""
PREFIJO_DESCARGA = f"https://github.com/{REPOSITORIO}/releases/download/"
PREFIJO_PAGINA = f"https://github.com/{REPOSITORIO}/"
NOMBRE_SUMAS = "SHA256SUMS.txt"

CADA_S = 24 * 3600
TIMEOUT_S = 15.0
LECTURA_DESCARGA_S = 60.0
"""Sin recibir nada en este tiempo, la descarga se da por cortada."""
TAMANO_MAXIMO = 600 * 2**20
"""El ZIP de la 0.1.1 pesa 95 MB. Más de esto no es un Voziris."""
ESPACIO_POR_MB = 4
"""Lo que hay que tener libre por cada MB del ZIP: el ZIP y lo descomprimido."""
NOMBRE_ESTADO = "actualizaciones.json"
CARPETA_DESCARGAS = "voziris-actualizacion"
LIMPIAR_TRAS_S = 3600
"""Lo descargado se borra pasada una hora: antes, su instalador puede seguir trabajando."""

TITULO_PREGUNTA = "Versiones nuevas de Voziris"
PREGUNTA = (
    "¿Quieres que Voziris mire una vez al día si hay una versión nueva?\n\n"
    "Solo pregunta a GitHub cuál es la última versión publicada: no envía nada "
    "tuyo. Si hay una nueva, te avisa, y no descarga ni instala nada hasta que "
    "tú se lo pidas.\n\n"
    "Puedes cambiarlo cuando quieras en Ajustes → Acerca de."
)


class ConsultaFallida(VozirisError):
    """No se pudo saber cuál es la última versión (sin red, GitHub no responde…)."""


class ActualizacionFallida(VozirisError):
    """No se pudo descargar o comprobar la versión nueva. No se ha cambiado nada."""


# --- versiones ----------------------------------------------------------------------------

_VERSION = re.compile(r"[vV]?(\d{1,6}(?:\.\d{1,6}){0,3})")


def version_de(texto: str) -> tuple[int, ...] | None:
    """«v0.1.2» → (0, 1, 2, 0). None si no es una versión de números con puntos."""
    m = _VERSION.fullmatch(texto.strip())
    if m is None:
        return None
    partes = tuple(int(p) for p in m.group(1).split("."))
    return partes + (0,) * (4 - len(partes))


def es_mas_nueva(candidata: str, actual: str) -> bool:
    """True si `candidata` es posterior a `actual`. Una versión que no se entiende, nunca."""
    a, b = version_de(candidata), version_de(actual)
    return a is not None and b is not None and a > b


@dataclass(frozen=True)
class Novedad:
    """Una versión publicada más nueva que la que corre."""

    version: str
    """Sin la «v»: «0.1.2»."""
    pagina: str = PAGINA
    """La página de la release: qué trae y descarga a mano."""
    zip_url: str = ""
    """El ZIP de Windows. Vacío si la release no lo trae (o si viene del estado guardado)."""
    zip_tamano: int = 0
    sumas_url: str = ""

    @property
    def zip_nombre(self) -> str:
        return f"voziris-{self.version}-win64.zip"

    @property
    def instalable(self) -> bool:
        return bool(self.zip_url and self.sumas_url)


def aviso_de(novedad: Novedad, instalada: bool) -> str:
    """La notificación: qué versión hay y dónde se pulsa."""
    if instalada:
        return (f"Hay una versión nueva de Voziris, la {novedad.version}. Para instalarla: "
                f"bandeja → «Actualizar a la {novedad.version}…»")
    return (f"Hay una versión nueva de Voziris, la {novedad.version}. Para descargarla: "
            f"bandeja → «Descargar la {novedad.version}…»")


# --- consulta -----------------------------------------------------------------------------


HOSTS_DE_GITHUB = ("github.com", "githubusercontent.com")
"""Adónde puede llevar una redirección: GitHub y su almacén de descargas, y nada más."""


def _cliente(transporte: Any, timeout_s: float) -> Any:
    import httpx

    return httpx.Client(
        transport=transporte,
        timeout=timeout_s,
        follow_redirects=True,
        headers={"User-Agent": f"Voziris/{__version__} (+https://kairis.es/blog/voziris.html)"},
        event_hooks={"response": [_vigilar_redireccion]},
    )


def _vigilar_redireccion(respuesta: Any) -> None:
    """Cada salto de una redirección, no solo el último: por HTTPS y dentro de GitHub.

    Un salto intermedio por HTTP saldría en claro, y desde ahí se podría
    llevar la descarga (y su SHA256SUMS.txt) a cualquier otro sitio.
    """
    if not respuesta.has_redirect_location:
        return
    # httpx llama a los ganchos antes de construir la petición siguiente: el
    # destino se calcula aquí, como lo hará él, desde la cabecera Location.
    destino = respuesta.request.url.join(respuesta.headers["Location"])
    host = (destino.host or "").lower()
    seguro = destino.scheme == "https" and any(
        host == h or host.endswith("." + h) for h in HOSTS_DE_GITHUB
    )
    if not seguro:
        raise ActualizacionFallida(
            f"GitHub ha redirigido a un sitio que no es suyo o no es seguro ({destino}): "
            "no se sigue"
        )


def consultar(
    actual: str = __version__, *, transporte: Any = None, timeout_s: float = TIMEOUT_S
) -> Novedad | None:
    """La última versión publicada si es más nueva que `actual`; None si no.

    Raises:
        ConsultaFallida: sin red, GitHub no responde o responde algo raro.
    """
    import httpx

    try:
        with _cliente(transporte, timeout_s) as cliente:
            r = cliente.get(URL_ULTIMA, headers={
                "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
            })
    except httpx.TimeoutException as e:
        raise ConsultaFallida("GitHub no ha contestado a tiempo") from e
    except httpx.HTTPError as e:
        raise ConsultaFallida(f"no se pudo conectar con GitHub ({type(e).__name__})") from e
    except ActualizacionFallida as e:  # una redirección fuera de GitHub
        raise ConsultaFallida(str(e)) from e
    if r.status_code == 404:
        raise ConsultaFallida("no hay ninguna versión publicada en GitHub")
    if r.status_code in (403, 429):
        raise ConsultaFallida("GitHub limita las consultas desde esta conexión; prueba más tarde")
    if r.status_code != 200:
        raise ConsultaFallida(f"GitHub ha respondido con un error ({r.status_code})")
    try:
        datos = r.json()
    except ValueError as e:
        raise ConsultaFallida("GitHub ha respondido algo que no se entiende") from e
    return novedad_de(datos, actual)


def novedad_de(datos: Any, actual: str) -> Novedad | None:
    """Lee la respuesta de `releases/latest`. None si no hay nada más nuevo que `actual`."""
    if not isinstance(datos, dict) or not isinstance(datos.get("tag_name"), str):
        raise ConsultaFallida("GitHub ha respondido algo que no se entiende")
    if datos.get("draft") or datos.get("prerelease"):
        return None
    etiqueta = datos["tag_name"].strip()
    if version_de(etiqueta) is None:
        raise ConsultaFallida(f"la última versión publicada tiene un nombre raro: {etiqueta!r}")
    version = etiqueta.lstrip("vV")
    if not es_mas_nueva(version, actual):
        return None
    pagina = datos.get("html_url")
    if not isinstance(pagina, str) or not pagina.startswith(PREFIJO_PAGINA):
        pagina = PAGINA
    archivos = {
        a["name"]: a for a in datos.get("assets") or []
        if isinstance(a, dict) and isinstance(a.get("name"), str)
    }
    zip_ = archivos.get(f"voziris-{version}-win64.zip")
    tamano = zip_.get("size") if zip_ else 0
    return Novedad(
        version=version,
        pagina=pagina,
        zip_url=_url_de_descarga(zip_),
        zip_tamano=tamano if isinstance(tamano, int) and tamano > 0 else 0,
        sumas_url=_url_de_descarga(archivos.get(NOMBRE_SUMAS)),
    )


def _url_de_descarga(archivo: dict[str, Any] | None) -> str:
    """Solo las URL de descarga de las releases de este repositorio."""
    url = archivo.get("browser_download_url") if archivo else None
    return url if isinstance(url, str) and url.startswith(PREFIJO_DESCARGA) else ""


# --- descarga -----------------------------------------------------------------------------

Progreso = Callable[[str, float | None], None]


def carpeta_de_descargas() -> Path:
    return Path(tempfile.gettempdir()) / CARPETA_DESCARGAS


def limpiar_descargas(base: Path | None = None, mas_viejas_que_s: float = 0.0) -> None:
    """Borra lo que dejaron las actualizaciones anteriores.

    El instalador de la versión nueva corre desde su carpeta temporal y no
    puede borrarse a sí mismo: lo hace la siguiente Voziris que arranca. Con
    `mas_viejas_que_s`, solo las carpetas de hace más de ese tiempo: nada más
    actualizar, el instalador puede seguir abierto enseñando su mensaje.
    """
    base = base or carpeta_de_descargas()
    if not base.is_dir():
        return
    limite = time.time() - mas_viejas_que_s
    for carpeta in base.iterdir():
        try:
            if mas_viejas_que_s and carpeta.stat().st_mtime > limite:
                continue
        except OSError:
            continue
        shutil.rmtree(carpeta, ignore_errors=True)
    with contextlib.suppress(OSError):
        base.rmdir()  # solo si ha quedado vacía


def descargar(
    novedad: Novedad,
    al_progresar: Progreso | None = None,
    *,
    base: Path | None = None,
    transporte: Any = None,
) -> Path:
    """Descarga, comprueba y descomprime la versión nueva. Devuelve su `voziris.exe`.

    Todo va a una carpeta nueva dentro de `base` (por defecto, en la carpeta
    temporal del usuario). `al_progresar` puede lanzar para cancelar: lo
    descargado se borra y la excepción sigue su camino.

    Raises:
        ActualizacionFallida: la release no trae el paquete, la descarga se
            cortó, no coincide con su SHA-256 o le faltan archivos.
    """
    progreso: Progreso = al_progresar or (lambda _m, _f: None)
    if not novedad.instalable:
        raise ActualizacionFallida(
            f"La versión {novedad.version} no trae el paquete de Windows o su "
            f"{NOMBRE_SUMAS}. Descárgala desde {novedad.pagina}"
        )
    if novedad.zip_tamano > TAMANO_MAXIMO:
        raise ActualizacionFallida(
            f"El paquete de la {novedad.version} pesa {novedad.zip_tamano >> 20} MB: "
            "demasiado para ser Voziris. No se descarga."
        )
    base = base or carpeta_de_descargas()
    # Solo lo viejo: una carpeta reciente puede ser de la que está instalando
    # ahora mismo el instalador de una actualización anterior.
    limpiar_descargas(base, mas_viejas_que_s=LIMPIAR_TRAS_S)
    try:
        base.mkdir(parents=True, exist_ok=True)
        carpeta = Path(tempfile.mkdtemp(prefix=f"{novedad.version}-", dir=base))
    except OSError as e:
        raise ActualizacionFallida(f"No se puede crear la carpeta de la descarga: {e}") from e
    try:
        _comprobar_espacio(carpeta, novedad.zip_tamano)
        with _cliente(transporte, TIMEOUT_S) as cliente:
            progreso("Comprobando la versión publicada…", None)
            esperado = _hash_publicado(cliente, novedad)
            zip_ = carpeta / novedad.zip_nombre
            obtenido = _bajar(cliente, novedad, zip_, progreso)
        if obtenido != esperado:
            raise ActualizacionFallida(
                "El paquete descargado no coincide con su SHA-256 publicado: se ha "
                "descargado mal. Prueba otra vez dentro de un rato."
            )
        log.info("descargada la %s (%s), SHA-256 correcto", novedad.version, zip_.name)
        raiz = _descomprimir(zip_, carpeta, progreso)
        with contextlib.suppress(OSError):
            # Si el antivirus aún lo tiene abierto, se queda: la carpeta entera se
            # borra en el siguiente arranque. No es motivo para no actualizar.
            zip_.unlink()
        progreso("Comprobando los archivos…", None)
        resultado = integridad.comprobar(raiz, hashes=True)
        if resultado.sin_manifiesto or not resultado.ok:
            raise ActualizacionFallida(
                f"Al paquete descargado le faltan archivos ({resultado.resumen(3)}). "
                "¿Los ha retirado el antivirus? No se ha cambiado nada."
            )
        # La comprobación tarda unos segundos sin avisar de nada: un Cancelar
        # pulsado durante ella se atiende aquí, antes de devolver el .exe.
        progreso("Paquete comprobado", 1.0)
        return raiz / "voziris.exe"
    except BaseException:
        shutil.rmtree(carpeta, ignore_errors=True)
        raise


def _comprobar_espacio(carpeta: Path, tamano: int) -> None:
    necesario = max(tamano, 100 * 2**20) * ESPACIO_POR_MB
    try:
        libre = shutil.disk_usage(carpeta).free
    except OSError:
        return
    if libre < necesario:
        raise ActualizacionFallida(
            f"No hay sitio para descargar la versión nueva: hacen falta {necesario >> 20} MB "
            f"libres en {carpeta.anchor or carpeta} y hay {libre >> 20} MB."
        )


def _hash_publicado(cliente: Any, novedad: Novedad) -> str:
    """El SHA-256 del ZIP según el `SHA256SUMS.txt` de la release."""
    texto = _pedir_texto(cliente, novedad.sumas_url)
    for linea in texto.splitlines():
        partes = linea.split()
        if len(partes) == 2 and partes[1].lstrip("*") == novedad.zip_nombre:
            valor = partes[0].lower()
            if re.fullmatch(r"[0-9a-f]{64}", valor):
                return valor
    raise ActualizacionFallida(
        f"El {NOMBRE_SUMAS} de la {novedad.version} no trae el SHA-256 de "
        f"{novedad.zip_nombre}. No se descarga."
    )


def _pedir_texto(cliente: Any, url: str, maximo: int = 64 * 1024) -> str:
    import httpx

    try:
        r = cliente.get(url)
    except httpx.HTTPError as e:
        raise ActualizacionFallida(f"No se pudo conectar con GitHub ({type(e).__name__})") from e
    _exigir_https(r)
    if r.status_code != 200:
        raise ActualizacionFallida(f"GitHub ha respondido con un error ({r.status_code})")
    if len(r.content) > maximo:
        raise ActualizacionFallida(f"{NOMBRE_SUMAS} demasiado grande: no es el de Voziris")
    return str(r.content.decode("utf-8", errors="replace"))


def _exigir_https(respuesta: Any) -> None:
    """Las redirecciones de GitHub van a su almacén por HTTPS; otra cosa no se acepta."""
    if respuesta.url.scheme != "https":
        raise ActualizacionFallida("La descarga se ha redirigido fuera de HTTPS: no se sigue")


def _bajar(cliente: Any, novedad: Novedad, destino: Path, progreso: Progreso) -> str:
    """Descarga el ZIP a `destino` y devuelve su SHA-256."""
    import httpx

    h = hashlib.sha256()
    recibido = 0
    mb_anunciado = -1
    try:
        with cliente.stream(
            "GET", novedad.zip_url, timeout=httpx.Timeout(TIMEOUT_S, read=LECTURA_DESCARGA_S)
        ) as r:
            _exigir_https(r)
            if r.status_code != 200:
                raise ActualizacionFallida(
                    f"GitHub ha respondido con un error ({r.status_code}) al descargar"
                )
            total = _entero(r.headers.get("Content-Length")) or novedad.zip_tamano
            if total > TAMANO_MAXIMO:
                raise ActualizacionFallida("El paquete es demasiado grande: no se descarga")
            with destino.open("wb") as f:
                for trozo in r.iter_bytes(1 << 16):
                    recibido += len(trozo)
                    if recibido > TAMANO_MAXIMO:
                        raise ActualizacionFallida("El paquete es demasiado grande: se para")
                    f.write(trozo)
                    h.update(trozo)
                    if recibido >> 20 != mb_anunciado:
                        mb_anunciado = recibido >> 20
                        de = f" de {total >> 20}" if total else ""
                        progreso(
                            f"Descargando Voziris {novedad.version}: {mb_anunciado}{de} MB",
                            min(recibido / total, 1.0) if total else None,
                        )
    except httpx.TimeoutException as e:
        raise ActualizacionFallida("La descarga se ha quedado parada. Prueba otra vez.") from e
    except httpx.HTTPError as e:
        raise ActualizacionFallida(
            f"Se ha cortado la descarga ({type(e).__name__}). Prueba otra vez."
        ) from e
    except OSError as e:
        raise ActualizacionFallida(f"No se pudo guardar la descarga: {e}") from e
    if total and recibido != total:
        raise ActualizacionFallida("La descarga ha llegado incompleta. Prueba otra vez.")
    return h.hexdigest()


def _entero(texto: str | None) -> int:
    try:
        return max(int(texto or 0), 0)
    except ValueError:
        return 0


def _descomprimir(zip_: Path, carpeta: Path, progreso: Progreso) -> Path:
    """Saca `voziris/` del ZIP dentro de `carpeta`. Nada fuera de ella."""
    destino = carpeta.resolve()
    try:
        with zipfile.ZipFile(zip_) as archivo:
            miembros = archivo.infolist()
            for m in miembros:
                ruta = (destino / m.filename).resolve()
                if not m.filename.startswith("voziris/") or not ruta.is_relative_to(destino):
                    raise ActualizacionFallida(
                        f"El paquete trae una ruta que no es de Voziris ({m.filename}): "
                        "no se descomprime"
                    )
            total = sum(m.file_size for m in miembros) or 1
            hecho = 0
            for m in miembros:
                archivo.extract(m, destino)
                hecho += m.file_size
                progreso("Descomprimiendo…", hecho / total)
    except zipfile.BadZipFile as e:
        raise ActualizacionFallida(f"El paquete descargado no es un ZIP válido: {e}") from e
    except OSError as e:
        raise ActualizacionFallida(f"No se pudo descomprimir la versión nueva: {e}") from e
    raiz = destino / "voziris"
    if not (raiz / "voziris.exe").is_file():
        raise ActualizacionFallida("El paquete descargado no trae voziris.exe")
    return raiz


# --- estado y comprobación diaria ----------------------------------------------------------


@dataclass
class Estado:
    """Lo que se recuerda entre arranques, en `actualizaciones.json` junto al config.toml."""

    comprobada: float = 0.0
    """Cuándo se consultó con éxito por última vez (segundos desde 1970)."""
    ultima: str = ""
    """La última versión publicada, si era más nueva que la que corría."""
    pagina: str = ""
    avisada: str = ""
    """La versión de la que ya se avisó: un aviso por versión, no uno al día."""

    @classmethod
    def leer(cls, carpeta: Path) -> Estado:
        """Lo guardado, o un estado vacío si no está o no se entiende. Nunca lanza.

        Se lee al arrancar la bandeja: un archivo roto, aunque sea a mano (un
        número de 400 cifras, un JSON anidado mil veces), no puede impedirlo.
        """
        try:
            datos = json.loads((carpeta / NOMBRE_ESTADO).read_text(encoding="utf-8"))
            if not isinstance(datos, dict):
                return cls()
            comprobada = datos.get("comprobada")
            return cls(
                comprobada=float(comprobada) if isinstance(comprobada, int | float) else 0.0,
                **{c: str(datos.get(c) or "")[:200] for c in ("ultima", "pagina", "avisada")},
            )
        except Exception:  # noqa: BLE001 — ver arriba
            return cls()

    def guardar(self, carpeta: Path) -> None:
        ruta = carpeta / NOMBRE_ESTADO
        temporal = ruta.with_suffix(".tmp")
        try:
            temporal.write_text(json.dumps(asdict(self), indent=1), encoding="utf-8")
            os.replace(temporal, ruta)
        except OSError as e:
            log.warning("no se pudo guardar %s: %s", ruta, e)

    def toca(self, ahora: float, cada_s: float = CADA_S) -> bool:
        """¿Ha pasado un día desde la última consulta? (Con el reloj atrasado, también.)"""
        return not 0 <= ahora - self.comprobada < cada_s


class Vigilante:
    """Mira en segundo plano, una vez al día, si hay versión nueva. Solo mientras `activa()`.

    Una consulta que falla (sin red, por ejemplo) no cuenta: se vuelve a
    probar a la hora, no al día siguiente.
    """

    def __init__(
        self,
        carpeta: Path,
        activa: Callable[[], bool],
        al_encontrar: Callable[[Novedad], None],
        *,
        version: str = __version__,
        consultar: Callable[[str], Novedad | None] = consultar,
        reloj: Callable[[], float] = time.time,
        primera_espera_s: float = 60.0,
        revisar_cada_s: float = 3600.0,
        cada_s: float = CADA_S,
    ) -> None:
        self._carpeta = carpeta
        self._activa = activa
        self._al_encontrar = al_encontrar
        self._version = version
        self._consultar = consultar
        self._reloj = reloj
        self._primera_espera_s = primera_espera_s
        self._revisar_cada_s = revisar_cada_s
        self._cada_s = cada_s
        self._estado = Estado.leer(carpeta)
        self._cerrojo = threading.Lock()
        self._despierta = threading.Event()
        self._parada = threading.Event()
        self._hilo: threading.Thread | None = None
        self.novedad: Novedad | None = None
        """La versión nueva conocida. Al arrancar, la de la última consulta, sin consultar."""
        if es_mas_nueva(self._estado.ultima, version):
            self.novedad = Novedad(self._estado.ultima, self._estado.pagina or PAGINA)

    def arrancar(self) -> None:
        self._hilo = threading.Thread(target=self._bucle, name="voziris-versiones", daemon=True)
        self._hilo.start()

    def parar(self) -> None:
        self._parada.set()
        self._despierta.set()

    def despertar(self) -> None:
        """Que mire ya si toca (p. ej., al aceptar la pregunta), sin esperar a la hora."""
        self._despierta.set()

    def buscar_ahora(self) -> Novedad | None:
        """Consulta ahora, toque o no. Para «Buscar actualizaciones».

        Raises:
            ConsultaFallida: no se pudo consultar.
        """
        novedad, _ = self._comprobar()
        return novedad

    def _bucle(self) -> None:
        espera = self._primera_espera_s
        while not self._parada.is_set():
            self._despierta.wait(espera)
            self._despierta.clear()
            if self._parada.is_set():
                return
            espera = self._revisar_cada_s
            try:
                if not self._activa() or not self._estado.toca(self._reloj(), self._cada_s):
                    continue
                novedad, es_nueva = self._comprobar()
            except ConsultaFallida as e:
                log.info("no se pudo mirar si hay versión nueva: %s", e)
                continue
            except Exception:  # noqa: BLE001 — el hilo no se muere por esto
                log.exception("fallo mirando si hay versión nueva")
                continue
            if novedad is not None and es_nueva:
                try:
                    self._al_encontrar(novedad)
                except Exception:  # noqa: BLE001
                    log.exception("fallo avisando de la versión nueva")

    def _comprobar(self) -> tuple[Novedad | None, bool]:
        """(novedad, es la primera vez que se ve). Una consulta a la vez."""
        with self._cerrojo:
            novedad = self._consultar(self._version)
            e = self._estado
            e.comprobada = self._reloj()
            e.ultima = novedad.version if novedad else ""
            e.pagina = novedad.pagina if novedad else ""
            es_nueva = novedad is not None and novedad.version != e.avisada
            if novedad is not None:
                e.avisada = novedad.version
            e.guardar(self._carpeta)
            self.novedad = novedad
        log.info("versión publicada: %s", novedad.version if novedad else "ninguna más nueva")
        return novedad, es_nueva
