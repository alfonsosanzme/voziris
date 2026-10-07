"""Punto de entrada: arranque, cableado y bucle principal.

Este archivo es el único sitio donde se conocen todas las piezas. Todo lo
demás depende solo de los tres protocolos y de `tipos.py`. La máquina de
estados vive en `orquestador.py`.

Orden de arranque (importa):

    1. Mutex de instancia única. Si ya hay otra Voziris, aviso y salir (VOZ-05).
    2. Log rotativo junto al ejecutable, con la clave de API tachada.
    3. Cargar y validar la configuración. Si falla, mensaje claro y salir: es
       el único error fatal.
    4. Abrir la captura de audio y poner a girar el búfer previo. Sin
       micrófono se arranca igual, con el icono en error.
    5. Precalentar el motor en un hilo aparte. La interfaz no se bloquea; si
       el usuario dicta antes de que termine, espera en «procesando».
    6. Registrar los atajos. Si uno está tomado, decir cuál.
    7. Mostrar el icono de bandeja (en su hilo) y entrar en el bucle de Tk,
       que se queda con el hilo principal para el HUD y los ajustes.

Modo consola, para probar el pipeline sin teclado ni bandeja:

    python -m voziris --archivo muestra.wav [--destino app_activa] [--nivel literal]

Y `python -m voziris --salir` cierra limpiamente la instancia abierta, para
scripts y para la verificación en máquina virtual (VOZ-61).

Transcribir una grabación entera a Markdown (VOZ-70/71/72), sin bandeja ni
mutex, en un proceso aparte de la Voziris que esté dictando:

    voziris.exe --transcribir reunion.m4a --hablantes auto [--salida x.md] [--ventana]

Vale igual para un vídeo: se le saca la pista de sonido y sigue el mismo
camino. Si el vídeo trae varias pistas, `--pista auto|N|todas` elige cuál.

`--ventana` muestra el progreso en una ventana en vez de en la consola; es lo
que usan el menú contextual del Explorador y la entrada de la bandeja.

Issue: VOZ-04 (orquestación), VOZ-05 (instancia única).
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import logging.handlers
import os
import queue
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from voziris import integridad

if __name__ == "__main__" and "--comprobar" in sys.argv[1:]:
    # Antes de importar nada con código nativo (numpy entra con config). Si
    # faltara una DLL de numpy, el ejecutable sin consola enseñaría el cuadro
    # de error de PyInstaller y se quedaría esperando a que alguien lo cerrara,
    # y quien lanzó la comprobación, esperando con él (VOZ-81).
    raise SystemExit(integridad.comprobar_desde_argv(sys.argv[1:]))

from voziris import __version__, red, winapi  # noqa: E402
from voziris import config as cfg  # noqa: E402
from voziris.errores import (  # noqa: E402
    AtajosNoDisponibles,
    ConfigInvalida,
    MicrofonoNoDisponible,
    VozirisError,
)
from voziris.tipos import Modo, Nivel  # noqa: E402

log = logging.getLogger("voziris")

NOMBRE_LOG = "voziris.log"
NOMBRE_LOG_TRANSCRIBIR = "voziris-transcribir.log"
"""El de los procesos `--transcribir`. Ver `configurar_log`."""


# --- registro ---------------------------------------------------------------------


class _TacharClave(logging.Filter):
    """Ninguna clave de API acaba en el log, ni entera ni por partes."""

    def __init__(self, clave: str) -> None:
        super().__init__()
        self._clave = clave

    def filter(self, record: logging.LogRecord) -> bool:
        if self._clave and len(self._clave) >= 8:
            record.msg = str(record.msg).replace(self._clave, "***")
            if record.args:
                record.args = tuple(
                    a.replace(self._clave, "***") if isinstance(a, str) else a
                    for a in record.args
                )
        return True


def _sin_consola() -> None:
    """En el ejecutable congelado (sin consola) stdout y stderr son None.

    Cualquier biblioteca que escriba en ellos (tqdm, huggingface_hub) revienta.
    Se redirigen a la nada y se apagan las barras de progreso: el progreso de
    descarga ya lo da `MotorLocal` por su callback.
    """
    import os

    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115 — vive lo que el proceso
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    # Lanzado desde una consola en cp1252, un «→» en un print tumbaba el
    # instalador con UnicodeEncodeError. Lo que no se pueda mostrar, se sustituye.
    for flujo in (sys.stdout, sys.stderr):
        reconfigurar = getattr(flujo, "reconfigure", None)
        if reconfigurar is not None:
            with contextlib.suppress(Exception):
                reconfigurar(errors="replace")


_archivo_fallos: Any = None


def activar_faulthandler(carpeta: Path) -> None:
    """Traza de Python en `voziris-fallos.log` si el proceso muere por un fallo nativo.

    No lo ve todo: una corrupción del montón (0xc0000374) mata el proceso sin
    pasar por aquí, y para eso están los volcados de Windows (ver
    `diagnostico.py`). Pero una violación de acceso o un desbordamiento de
    pila sí dejan aquí qué hilo y qué línea de Python estaban en marcha.
    """
    global _archivo_fallos
    import faulthandler

    try:
        _archivo_fallos = open(carpeta / "voziris-fallos.log", "a", encoding="utf-8")  # noqa: SIM115
        faulthandler.enable(_archivo_fallos, all_threads=True)
    except OSError as e:
        log.warning("sin voziris-fallos.log: %s", e)


def anotar_arranque_en_fallos(modo: str = "bandeja") -> None:
    """Una línea con la fecha, el modo y el pid en `voziris-fallos.log`.

    Los volcados de faulthandler no llevan fecha ni dicen de qué proceso son:
    sin esta línea no hay forma de saber a qué arranque pertenece cada uno
    (VOZ-80). Cada proceso la suya, también «Transcribir» del Explorador, que
    es el de más código nativo. El diagnóstico se salta las marcas que no
    tienen un volcado detrás, así que no echan de la vista los de verdad.
    """
    if _archivo_fallos is None:
        return
    try:
        _archivo_fallos.write(f"\n=== {_ahora()} · arranque de Voziris {__version__}: {modo} "
                              f"(pid {os.getpid()}) ===\n")
        _archivo_fallos.flush()
    except (OSError, ValueError) as e:
        log.warning("no se pudo anotar el arranque en voziris-fallos.log: %s", e)


def _ahora() -> str:
    from datetime import datetime

    return f"{datetime.now():%Y-%m-%d %H:%M:%S}"


def volcar_hilos(motivo: str) -> None:
    """Deja en `voziris-fallos.log` dónde está cada hilo, sin matar nada. Para los atascos."""
    if _archivo_fallos is None:
        return
    import faulthandler

    try:
        _archivo_fallos.write(f"\n=== {_ahora()} · {motivo} (el proceso sigue vivo) ===\n")
        _archivo_fallos.flush()
        faulthandler.dump_traceback(_archivo_fallos, all_threads=True)
        _archivo_fallos.flush()
    except (OSError, ValueError) as e:
        log.warning("no se pudo volcar dónde estaba cada hilo: %s", e)


@contextlib.contextmanager
def volcados_en_pausa() -> Iterator[None]:
    """faulthandler apagado mientras dura el bloque. Para las llamadas COM.

    Al crear un acceso directo, COM provoca violaciones de acceso que él mismo
    resuelve. faulthandler las ve antes que nadie y las anota en
    voziris-fallos.log como «Windows fatal exception: access violation», y
    luego el diagnóstico las enseña como fallos graves (visto en VOZ-81, en una
    instalación que terminó bien).
    """
    import faulthandler

    activo = _archivo_fallos is not None and faulthandler.is_enabled()
    if activo:
        faulthandler.disable()
    try:
        yield
    finally:
        if activo:
            with contextlib.suppress(OSError, ValueError, RuntimeError):
                faulthandler.enable(_archivo_fallos, all_threads=True)


def configurar_log(
    carpeta: Path, depurar: bool, a_consola: bool, nombre: str | None = NOMBRE_LOG
) -> None:
    """El log de la bandeja rota a 1 MB; el de los procesos sueltos, solo al arrancar.

    `--transcribir` corre en un proceso aparte mientras la bandeja tiene
    voziris.log abierto. Si le tocaba rotarlo, Windows no le dejaba renombrar
    un archivo abierto (WinError 32) y se perdía TODO su registro, justo el
    del proceso que más falla (VOZ-81). Por eso escribe en el suyo. Con
    `nombre` None no se escribe a archivo (`--comprobar`).
    """
    raiz = logging.getLogger()
    raiz.setLevel(logging.DEBUG if depurar else logging.INFO)
    for ruidoso in ("httpx", "httpcore", "huggingface_hub", "urllib3", "filelock"):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)
    formato = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    if nombre is not None:
        try:
            archivo: logging.Handler
            if nombre == NOMBRE_LOG:
                archivo = logging.handlers.RotatingFileHandler(
                    carpeta / nombre, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
                )
            else:
                _rotar_si_crecio(carpeta / nombre)
                archivo = logging.FileHandler(carpeta / nombre, encoding="utf-8")
            archivo.setFormatter(formato)
            raiz.addHandler(archivo)
        except OSError as e:
            print(f"No se puede escribir el log en {carpeta}: {e}", file=sys.stderr)
    if a_consola:
        consola = logging.StreamHandler(sys.stderr)
        consola.setFormatter(formato)
        raiz.addHandler(consola)


def _rotar_si_crecio(ruta: Path, limite: int = 1_000_000) -> None:
    """Si pasa de `limite`, se aparta como .1. Si otro proceso lo tiene abierto, se sigue igual."""
    try:
        if ruta.stat().st_size > limite:
            os.replace(ruta, ruta.with_name(ruta.name + ".1"))
    except OSError:
        pass


def _error_fatal(
    mensaje: str, con_ventana: bool, titulo: str = "Voziris no puede arrancar"
) -> None:
    log.error(mensaje)
    print(mensaje, file=sys.stderr)
    if con_ventana:
        try:
            import tkinter as tk
            from tkinter import messagebox

            raiz = tk.Tk()
            raiz.withdraw()
            messagebox.showerror(titulo, mensaje)
            raiz.destroy()
        except Exception:  # noqa: BLE001 — sin Tk, ya está en el log y en stderr
            pass


# --- construcción ---------------------------------------------------------------------


def _construir(
    configuracion: cfg.Config, al_progresar: Callable[[str, float | None], None]
) -> tuple[Any, Any, dict[str, Any]]:
    """Crea las piezas a partir de la configuración. Devuelve (captura, motor, destinos)."""
    from voziris.audio.captura import Captura
    from voziris.destinos.app_activa import AppActiva

    a = configuracion.audio
    captura = Captura(a.dispositivo, a.ganancia_db, a.buffer_previo_ms)
    motor = _motor(configuracion, al_progresar)
    from voziris.destinos.archivo_md import ArchivoMarkdown

    d = configuracion.destino.app_activa
    destinos: dict[str, Any] = {
        "app_activa": AppActiva(d.metodo, d.restaurar_portapapeles, d.auto_enter),
    }
    md = configuracion.destino.markdown
    if md.ruta.parent.is_dir():
        destinos["markdown"] = ArchivoMarkdown(md.ruta, md.formato, md.sello, md.separador)
    else:
        # C-1: la ruta de relleno del ejemplo no impide arrancar; el atajo de
        # Markdown responde con tono de error y aviso hasta que se corrija.
        log.warning("destino Markdown desactivado: la carpeta %s no existe", md.ruta.parent)
    return captura, motor, destinos


def _motor(configuracion: cfg.Config, al_progresar: Callable[[str, float | None], None]) -> Any:
    """El selector con sus dos motores, tal como lo usa el dictado y la transcripción."""
    return _motores(configuracion, al_progresar)[0]


def _motores(
    configuracion: cfg.Config, al_progresar: Callable[[str, float | None], None]
) -> tuple[Any, Any]:
    """(el selector, el motor local). El local, para contar por qué no está disponible."""
    from voziris.motores.api import MotorAPI
    from voziris.motores.local import MotorLocal
    from voziris.motores.selector import Selector

    ml = configuracion.motor.local
    local = MotorLocal(ml.modelo, ml.carpeta, ml.hilos, ml.cuantizacion, al_progresar)
    ma = configuracion.motor.api
    api = MotorAPI(
        ma.base_url, ma.modelo, ma.clave, ma.timeout_s,
        vocabulario=configuracion.proceso.diccionario,
    )
    return Selector(configuracion.general.motor, local, api), local


def _postprocesos(configuracion: cfg.Config) -> list[Any]:
    """La cadena en su orden fijo: diccionario → sustituciones → LLM (si hay modelo)."""
    from voziris.proceso.diccionario import Diccionario
    from voziris.proceso.llm import LimpiezaLLM
    from voziris.proceso.sustituciones import Sustituciones

    p = configuracion.proceso
    cadena: list[Any] = [Diccionario(p.diccionario), Sustituciones(p.sustituciones)]
    if p.llm_modelo:
        ma = configuracion.motor.api
        cadena.append(LimpiezaLLM(ma.base_url, p.llm_modelo, ma.clave))
    return cadena


# --- modo consola ---------------------------------------------------------------------


class _DestinoNulo:
    """`--destino ninguno`: solo imprime. Para probar sin pegar en ninguna ventana."""

    nombre = "ninguno"

    def entregar(self, texto: str, ctx: object) -> object:
        from voziris.tipos import Entrega

        return Entrega(ok=True, detalle="no entregado (modo consola)")


def _modo_consola(configuracion: cfg.Config, archivo: Path, destino: str, nivel: Nivel) -> int:
    import soundfile as sf

    from voziris.audio.captura import normalizar
    from voziris.orquestador import Orquestador
    from voziris.tipos import SAMPLE_RATE, Audio

    datos, sr = sf.read(str(archivo), dtype="float32", always_2d=True)
    if sr != SAMPLE_RATE:
        print(f"{archivo} está a {sr} Hz; hace falta {SAMPLE_RATE} Hz mono", file=sys.stderr)
        return 2
    audio = Audio(muestras=normalizar(datos.mean(axis=1)))

    def progreso(mensaje: str, fraccion: float | None) -> None:
        print(f"  {mensaje}" + (f" ({fraccion:.0%})" if fraccion is not None else ""))

    _captura, motor, destinos = _construir(configuracion, progreso)
    destinos["ninguno"] = _DestinoNulo()

    class SinCaptura:
        oyente_bloques: Any = None

        def empezar_dictado(self) -> None: ...

        def terminar_dictado(self) -> Audio:
            return audio

        def cancelar_dictado(self) -> None: ...

    orq = Orquestador(
        SinCaptura(), motor, destinos, _postprocesos(configuracion),
        configuracion.general.idioma, nivel,
    )
    print("Cargando el motor…")
    orq.arrancar()
    try:
        resultado = orq.dictar_audio(audio, destino)
    except VozirisError as e:
        # Sin traza: en el ejecutable congelado (sin consola) una excepción sin
        # capturar acaba en un diálogo de PyInstaller que bloquea.
        log.error("consola: %s", e)
        print(f"No se pudo: {e}", file=sys.stderr)
        orq.parar()
        return 1
    orq.parar()
    # También al log: el ejecutable congelado no tiene consola y es la forma
    # de verificar el modo consola en el paquete (VOZ-61).
    log.info(
        "consola: motor=%s rtf=%.3f entregado=%s texto=%d caracteres",
        resultado["motor"], resultado["rtf"], resultado["entregado"], len(resultado["texto"]),
    )
    print(f"\n{resultado['texto']}\n")
    print(f"motor {resultado['motor']} · RTF {resultado['rtf']:.3f} · "
          f"{resultado['ms_motor']} ms motor · {resultado['ms_postproceso']} ms post-proceso · "
          f"{resultado['ms_entrega']} ms entrega")
    print(f"entrega: {'ok' if resultado['entregado'] else 'FALLÓ'} — {resultado['detalle']}")
    for aviso in resultado["avisos"]:
        print(f"aviso: {aviso}")
    return 0


# --- transcribir una grabación -----------------------------------------------------------


def _transcribir_grabacion(
    configuracion: cfg.Config,
    ruta: Path,
    hablantes: str,
    salida: Path | None,
    con_ventana: bool,
    pista: int | str = "auto",
) -> int:
    """VOZ-72: de un archivo de audio a un .md al lado, con ventana de progreso o consola."""
    from voziris import archivos, grabaciones
    from voziris.archivos import ArchivoNoLegible
    from voziris.errores import MotorNoDisponible
    from voziris.hablantes import SeparadorHablantes
    from voziris.ui.transcripcion import TranscripcionCancelada, ejecutar_con_progreso

    Progreso = Callable[[str, float | None], None]

    def trabajo(progreso: Progreso) -> tuple[grabaciones.Resultado, Path]:
        # El archivo (y PyAV) antes que el modelo: si algo de eso falla, que se
        # sepa ya y no después de cargar, o descargar, 640 MB (VOZ-81).
        progreso("Abriendo el archivo…", None)
        archivos.pistas(ruta)
        motor, local = _motores(configuracion, progreso)
        progreso("Cargando el motor…", None)
        motor.precalentar()
        if not motor.disponible():
            raise MotorNoDisponible(local.error or "el motor de voz no está disponible")
        separador: SeparadorHablantes | None = None
        if hablantes != "1":
            separador = SeparadorHablantes(configuracion.motor.local.carpeta, progreso)
            separador.precalentar()
            if not separador.disponible():
                log.warning("sin separador de hablantes: %s", separador.error)
                separador = None
        resultado = grabaciones.transcribir_archivo(
            ruta, motor, configuracion.general.idioma, hablantes, separador, progreso, pista
        )
        return resultado, grabaciones.guardar(resultado, ruta, salida)

    def en_consola(mensaje: str, fraccion: float | None) -> None:
        print(f"  {mensaje}" + (f" ({fraccion:.0%})" if fraccion is not None else ""))

    try:
        if con_ventana:
            resultado, destino = ejecutar_con_progreso(
                "Transcribiendo con Voziris", ruta.name, trabajo
            )
        else:
            resultado, destino = trabajo(en_consola)
    except TranscripcionCancelada:
        log.info("transcripción de %s cancelada", ruta.name)
        return 3
    except (ArchivoNoLegible, VozirisError, OSError) as e:
        log.error("transcripción de %s: %s", ruta.name, e, exc_info=True)
        _error_fatal(
            f"No se pudo transcribir {ruta.name}: {e}", con_ventana,
            titulo="Voziris no pudo transcribir",
        )
        return 1
    log.info(
        "transcripción: %s → %s · %.0f s de audio · %d líneas · %d hablantes · motor %s · %d ms",
        ruta.name, destino, resultado.duracion_s, len(resultado.lineas), resultado.hablantes,
        resultado.motor or "ninguno", resultado.ms_proceso,
    )
    if con_ventana:
        _abrir_con_windows(destino)
    else:
        print(f"\n{destino}")
        partes = [f"{len(resultado.lineas)} líneas", f"{resultado.hablantes} hablante(s)"]
        if resultado.motor:
            partes.append(f"motor {resultado.motor}")
        partes.append(f"{resultado.ms_proceso} ms de motor")
        print(" · ".join(partes))
        for aviso in resultado.avisos:
            print(f"aviso: {aviso}")
    return 0


def _pedir_transcripcion(raiz: Any, carpeta_config: Path | None) -> None:
    """Desde la bandeja, en el hilo de Tk: pregunta y lanza el proceso aparte."""
    from voziris.ui.transcripcion import elegir_grabacion

    eleccion = elegir_grabacion(raiz)
    if eleccion is None:
        return
    ruta, hablantes = eleccion
    extra = ["--config", str(carpeta_config)] if carpeta_config else []
    _lanzar_desatendido(_comando_propio(
        *extra, "--transcribir", str(ruta), "--hablantes", hablantes, "--ventana",
    ))
    log.info("transcripción de %s (%s hablantes) lanzada en otro proceso", ruta.name, hablantes)


# --- aplicación -------------------------------------------------------------------------


def _aplicacion(configuracion: cfg.Config, ruta_config: Path | None = None) -> int:
    import tkinter as tk
    from tkinter import messagebox

    from voziris.atajos import Atajos
    from voziris.orquestador import Orquestador
    from voziris.ui.bandeja import AccionesBandeja, Bandeja, texto_acerca_de

    raiz = tk.Tk()
    raiz.withdraw()
    cola_ui: queue.Queue[Callable[[], object]] = queue.Queue()

    def en_hilo_tk(fn: Callable[[], object]) -> None:
        cola_ui.put(fn)

    def bombear() -> None:
        while True:
            try:
                fn = cola_ui.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except Exception:  # noqa: BLE001
                log.exception("fallo en el hilo de interfaz")
        raiz.after(16, bombear)

    def vigilar_salida() -> None:
        if winapi.salida_pedida():
            log.info("salida pedida desde fuera (voziris --salir)")
            raiz.quit()
            return
        raiz.after(500, vigilar_salida)

    bandeja: Bandeja | None = None
    orq: Orquestador | None = None

    def avisar(texto: str) -> None:
        """Notificación del sistema: para lo que el usuario debe saber aunque no mire."""
        if bandeja is not None:
            bandeja.avisar(texto)

    captura, motor, destinos = _construir(configuracion, lambda m, f: avisar_progreso(m, f))
    ultimo_progreso = [0.0]

    def avisar_progreso(mensaje: str, fraccion: float | None) -> None:
        # Una notificación cada 10 % o al terminar: sin aturdir.
        if fraccion is None or fraccion >= 1.0 or fraccion - ultimo_progreso[0] >= 0.1:
            ultimo_progreso[0] = fraccion or 0.0
            avisar(mensaje)

    from voziris.ui.hud import Hud

    hud = Hud(raiz, nivel=captura.nivel_actual, en_hilo_tk=en_hilo_tk)

    def cambiar_estado(estado: str) -> None:
        if bandeja is not None:
            bandeja.estado(estado)
            if estado in ("reposo", "error"):
                # Un dictado ha terminado: el menú tiene que enseñarlo. Si la
                # bandeja ya lo rehace al abrirse (Windows), esto no hace nada.
                bandeja.actualizar_menu()
        if estado == "grabando":
            hud.mostrar_grabando(orq.modo or Modo.MANTENER if orq is not None else Modo.MANTENER)
        elif estado == "procesando":
            hud.mostrar_procesando()
        else:
            hud.ocultar()

    def aviso_de_dictado(texto: str) -> None:
        """Los avisos del pipeline van al HUD (1,5 s); los errores, también a la bandeja."""
        hud.aviso(texto)
        if orq is not None and orq.estado.value == "error":
            avisar(texto)

    from voziris.audio.sonidos import Sonidos
    from voziris.audio.vad import DetectorSilencio

    def hay_red_si_hace_falta() -> bool:
        """VOZ-32: con motor local y nivel literal no se sondea nada: no sale ni un paquete."""
        if motor.preferencia == "local" and configuracion.proceso.nivel is Nivel.LITERAL:
            return False
        url = configuracion.motor.api.base_url
        return red.hay_red(red.host_de(url), red.puerto_de(url))

    from voziris.historial import Historial
    from voziris.tipos import Contexto

    def contexto_de_reintento(destino: str) -> Contexto:
        return Contexto(
            destino=destino, modo=Modo.CLAVAR,
            app_activa=destinos["app_activa"].app_en_primer_plano(),
            hay_red=hay_red_si_hace_falta(), nivel=configuracion.proceso.nivel,
        )

    h = configuracion.historial
    historial = Historial(
        configuracion.carpeta / "historial" / "dictados.jsonl", h.entradas,
        conservar_audio=h.conservar_audio,
        audio_maximo=h.audio_dictados, audio_dias=h.audio_dias,
        destinos=destinos, contexto=contexto_de_reintento,
    )

    from voziris.audio.mezclador import Mezclador, control_del_sistema
    from voziris.pendientes import Pendientes

    pendientes = Pendientes(configuracion.carpeta / "historial" / "pendientes")
    captura.pendientes = pendientes
    mezclador = Mezclador(
        configuracion.audio.al_dictar,
        control_del_sistema(),
        configuracion.carpeta / "historial" / "audio-rescate.json",
    )
    sonidos = Sonidos(configuracion.audio.sonidos)
    vad = DetectorSilencio(configuracion.audio.silencio_corte_ms, configuracion.motor.local.carpeta)
    orq = Orquestador(
        captura, motor, destinos, _postprocesos(configuracion),
        idioma=configuracion.general.idioma,
        nivel=configuracion.proceso.nivel,
        al_estado=cambiar_estado,
        al_aviso=aviso_de_dictado,
        sonidos=sonidos,
        historial=historial,
        vad=vad,
        hay_red=hay_red_si_hace_falta,
        app_en_primer_plano=destinos["app_activa"].app_en_primer_plano,
        pendientes=pendientes,
        mezclador=mezclador,
        al_atasco=lambda fase, hay_copia: al_atasco(fase, hay_copia),
    )
    orq.corte_por_silencio = configuracion.audio.corte_por_silencio

    def reintentar(indice: int) -> None:
        """Desde la bandeja: el menú se cierra y el foco vuelve a la app; se espera un poco."""
        import threading
        import time

        from voziris.ui.bandeja import ESPERA_REENTREGA_S

        def trabajo() -> None:
            # Tres segundos: el clic en la bandeja se ha llevado el foco, y así
            # da tiempo a ponerlo donde se quiere el texto.
            hud.aviso(f"Haz clic donde lo quieras: se pega en {ESPERA_REENTREGA_S} s")
            time.sleep(ESPERA_REENTREGA_S)
            entrega = historial.reintentar(indice)
            if entrega.ok:
                hud.aviso(entrega.detalle)
            else:
                hud.aviso(f"No se pudo reentregar: {entrega.detalle}")
            if bandeja is not None:
                bandeja.actualizar_menu()

        threading.Thread(target=trabajo, name="voziris-reintento", daemon=True).start()

    def cambiar_al_dictar(modo: str) -> None:
        """Desde la bandeja: vale para el dictado siguiente y se intenta guardar."""
        mezclador.modo = modo
        configuracion.audio.al_dictar = modo
        try:
            cfg.guardar(configuracion)
        except ConfigInvalida as e:
            avisar(f"Cambio aplicado hasta reiniciar; no se pudo guardar config.toml: {e}")
        if bandeja is not None:
            bandeja.actualizar_menu()

    def copiar_entrada(indice: int) -> None:
        entrada = historial.buscar(indice)
        if entrada is None:
            return
        try:
            winapi.copiar(entrada.texto)
            # Con la hora: «Copiar el último» tras un fallo copia el anterior, y
            # tiene que verse cuál es antes de pegarlo en ningún sitio.
            hud.aviso(f"Copiado el de las {entrada.momento:%H:%M}: pégalo con Ctrl+V")
        except OSError as e:
            avisar(f"No se pudo copiar: {e}")

    def copiar_anterior(indice: int) -> None:
        """El texto que había antes de volver a transcribir, por si el nuevo salió peor."""
        entrada = historial.buscar(indice)
        if entrada is None or not entrada.texto_anterior:
            return
        try:
            winapi.copiar(entrada.texto_anterior)
            hud.aviso("Copiado el texto de antes: pégalo con Ctrl+V")
        except OSError as e:
            avisar(f"No se pudo copiar: {e}")

    import threading as _hilos

    recuperando: set[Path] = set()
    cerrojo_recuperando = _hilos.Lock()

    def en_curso_ahora() -> list[Path]:
        """Los archivos que no son «sin transcribir» aunque estén en pendientes/: el que
        se está grabando, el que se está transcribiendo y los que se están recuperando.
        Si no, el menú los ofrecería y recuperarlos los transcribiría dos veces."""
        escritor = getattr(captura, "_escritor", None)
        en_uso = [escritor.ruta] if escritor is not None else []
        if orq.pendiente_en_proceso is not None:
            en_uso.append(orq.pendiente_en_proceso)
        with cerrojo_recuperando:
            en_uso.extend(recuperando)
        return en_uso

    def al_atasco(fase: str, hay_copia: bool) -> None:
        """Un dictado lleva demasiado procesándose: rastro para diagnosticar y aviso."""
        volcar_hilos(f"dictado atascado ({fase})")
        if hay_copia:
            avisar(
                f"Voziris se ha atascado {fase}. Lo dicho está guardado: si no se "
                "desatasca, sal desde la bandeja y vuelve a abrirlo"
            )
        else:
            # Sin copia en disco (no se pudo abrir el archivo del dictado), salir
            # lo perdería: mejor esperar.
            avisar(f"Voziris lleva mucho {fase}. Espera un poco antes de salir: "
                   "este dictado no tiene copia en disco")

    def retranscribir_entrada(indice: int) -> None:
        """Desde «Últimos dictados»: vuelve a transcribir la grabación y deja el texto copiado."""
        import threading

        from voziris.errores import TranscripcionFallida

        def trabajo() -> None:
            hud.aviso("Transcribiendo otra vez la grabación…")
            try:
                transcripcion = orq.retranscribir(indice)
            except TranscripcionFallida as e:
                avisar(f"No salió texto al volver a transcribir ({e}); el de antes se queda")
                return
            except VozirisError as e:
                avisar(f"No se pudo volver a transcribir: {e}")
                return
            except Exception as e:  # noqa: BLE001
                log.exception("fallo volviendo a transcribir el dictado %d", indice)
                avisar(f"No se pudo volver a transcribir: {e}")
                return
            if transcripcion is None:
                avisar("Ese dictado ya no está o ya no tiene la grabación guardada")
                return
            for aviso in transcripcion.avisos:  # p. ej., que cayó al motor local
                avisar(aviso)
            try:
                winapi.copiar(transcripcion.texto)
                avisar("Vuelto a transcribir: está copiado (Ctrl+V). "
                       "El texto de antes sigue en su menú")
            except OSError:
                avisar("Vuelto a transcribir: está en Últimos dictados")
            if bandeja is not None:
                bandeja.actualizar_menu()

        threading.Thread(target=trabajo, name="voziris-retranscribir", daemon=True).start()

    def recuperar_pendiente(pendiente: Any) -> None:
        """Transcribe un audio que se quedó sin texto, lo copia y lo deja en el historial."""
        import threading

        from voziris.errores import TranscripcionFallida

        with cerrojo_recuperando:
            if pendiente.ruta in recuperando:
                # Un segundo clic mientras se transcribe: dos llamadas al motor y
                # dos entradas con el mismo texto.
                hud.aviso("Ese audio ya se está transcribiendo")
                return
            recuperando.add(pendiente.ruta)

        def trabajo() -> None:
            try:
                recuperar_ya()
            finally:
                with cerrojo_recuperando:
                    recuperando.discard(pendiente.ruta)

        def recuperar_ya() -> None:
            hud.aviso("Transcribiendo el audio guardado…")
            try:
                texto = orq.recuperar(pendiente.ruta)
            except TranscripcionFallida as e:
                avisar(f"Tampoco ahora salió texto ({e}). La grabación sigue guardada: "
                       "prueba con otro motor desde el menú")
                return
            except VozirisError as e:
                avisar(f"No se pudo transcribir el audio guardado: {e}")
                return
            except Exception as e:  # noqa: BLE001
                log.exception("fallo recuperando %s", pendiente.ruta)
                avisar(f"No se pudo transcribir el audio guardado: {e}")
                return
            if texto:
                try:
                    winapi.copiar(texto)
                    avisar("Dictado recuperado: está copiado (Ctrl+V) y en Últimos dictados")
                except OSError:
                    avisar("Dictado recuperado: está en Últimos dictados")
            else:
                avisar("En ese audio no había nada que transcribir")
            if bandeja is not None:
                bandeja.actualizar_menu()

        threading.Thread(target=trabajo, name="voziris-recuperar", daemon=True).start()

    def borrar_pendiente(pendiente: Any) -> None:
        pendientes.borrar(pendiente.ruta)
        if bandeja is not None:
            bandeja.actualizar_menu()

    def borrar_entrada(indice: int) -> None:
        historial.borrar(indice)
        if bandeja is not None:
            bandeja.actualizar_menu()

    def cambiar_motor(preferencia: str) -> None:
        """Desde la bandeja: vale para el dictado siguiente y se intenta guardar."""
        motor.preferencia = preferencia
        configuracion.general.motor = preferencia
        try:
            cfg.guardar(configuracion)
        except ConfigInvalida as e:
            avisar(f"Motor cambiado hasta reiniciar; no se pudo guardar config.toml: {e}")
        if bandeja is not None:
            bandeja.actualizar_menu()

    from voziris.audio.captura import Captura
    from voziris.destinos.app_activa import AppActiva
    from voziris.destinos.archivo_md import ArchivoMarkdown
    from voziris.motores.api import MotorAPI
    from voziris.ui.ajustes import Ajustes

    def aplicar(nueva: cfg.Config) -> list[str]:
        """Aplica en caliente lo que se pueda; devuelve lo que exige reiniciar."""
        nonlocal configuracion
        vieja, pendientes = configuracion, []
        if vars(nueva.atajos) != vars(vieja.atajos):
            for aviso in atajos.registrar(vars(nueva.atajos)):
                avisar(aviso)
        if nueva.audio.dispositivo != vieja.audio.dispositivo:
            if orq.en_curso():
                pendientes.append("micrófono (hay un dictado en curso)")
            else:
                aviso_mic = captura.cambiar_dispositivo(nueva.audio.dispositivo)
                if aviso_mic:
                    avisar(aviso_mic)
        captura.ganancia_db = nueva.audio.ganancia_db
        if nueva.audio.buffer_previo_ms != vieja.audio.buffer_previo_ms:
            pendientes.append("búfer previo")
        vad.silencio_corte_ms = nueva.audio.silencio_corte_ms
        orq.corte_por_silencio = nueva.audio.corte_por_silencio
        sonidos.activos = nueva.audio.sonidos
        mezclador.modo = nueva.audio.al_dictar
        motor.preferencia = nueva.general.motor
        if vars(nueva.motor.local) != vars(vieja.motor.local):
            pendientes.append("motor local (modelo, carpeta, hilos o cuantización)")
        if vars(nueva.motor.api) != vars(vieja.motor.api):
            pendientes.append("motor por API (URL, modelo, clave o timeout)")
        orq.idioma = nueva.general.idioma
        orq.nivel = nueva.proceso.nivel
        orq.postprocesos = _postprocesos(nueva)
        d = nueva.destino.app_activa
        destinos["app_activa"] = AppActiva(d.metodo, d.restaurar_portapapeles, d.auto_enter)
        md = nueva.destino.markdown
        if md.ruta.parent.is_dir():
            destinos["markdown"] = ArchivoMarkdown(md.ruta, md.formato, md.sello, md.separador)
        else:
            destinos.pop("markdown", None)
        if vars(nueva.historial) != vars(vieja.historial):
            pendientes.append("historial (entradas o audio)")
        configuracion = nueva
        _ajustar_arranque_con_windows(nueva, avisar)
        if bandeja is not None:
            bandeja.actualizar_menu()
        log.info("ajustes aplicados; pendientes de reinicio: %s", pendientes or "ninguno")
        return pendientes

    def probar_clave(base_url: str, clave: str) -> tuple[bool, str]:
        return MotorAPI(base_url, configuracion.motor.api.modelo, clave).probar_clave()

    def listar_modelos(base_url: str, clave: str) -> tuple[list[str], list[str]]:
        return MotorAPI(base_url, configuracion.motor.api.modelo, clave).listar_modelos()

    ajustes = Ajustes(
        raiz, configuracion, aplicar, nivel_actual=captura.nivel_actual,
        dispositivos=Captura.dispositivos, probar_clave=probar_clave,
        listar_modelos=listar_modelos,
    )

    def alternar_corte() -> None:
        """Desde la bandeja: se aplica al siguiente dictado clavado y se intenta guardar."""
        configuracion.audio.corte_por_silencio = not configuracion.audio.corte_por_silencio
        orq.corte_por_silencio = configuracion.audio.corte_por_silencio
        try:
            cfg.guardar(configuracion)
        except ConfigInvalida as e:
            avisar(f"Cambio aplicado hasta reiniciar; no se pudo guardar config.toml: {e}")
        if bandeja is not None:
            bandeja.actualizar_menu()

    def guardar_diagnostico() -> None:
        # Desde VOZ-81 arranca voziris.exe --comprobar: son unos segundos sin nada en pantalla.
        avisar("Preparando el diagnóstico: tarda unos segundos")
        _abrir_con_windows(_generar_diagnostico(configuracion.carpeta))

    acciones = AccionesBandeja(
        dictar_ahora=lambda: orq.alternar_clavar(),
        dictar_markdown=lambda: orq.alternar_clavar("markdown"),
        transcribir=lambda: en_hilo_tk(lambda: _pedir_transcripcion(raiz, ruta_config)),
        alternar_corte=alternar_corte,
        abrir_ajustes=lambda: en_hilo_tk(ajustes.abrir),
        cambiar_motor=cambiar_motor,
        reintentar=reintentar,
        borrar_entrada=borrar_entrada,
        copiar=copiar_entrada,
        cambiar_al_dictar=cambiar_al_dictar,
        al_dictar_actual=lambda: mezclador.modo,
        devolver_sonido=mezclador.devolver_el_sonido,
        hay_sonido_bajado=mezclador.hay_sonido_bajado,
        pendientes=lambda: pendientes.listar(excepto=en_curso_ahora()),
        recuperar=recuperar_pendiente,
        borrar_pendiente=borrar_pendiente,
        retranscribir=retranscribir_entrada,
        copiar_anterior=copiar_anterior,
        salir=lambda: en_hilo_tk(raiz.quit),
        ver_registro=lambda: _abrir_con_windows(configuracion.carpeta / NOMBRE_LOG),
        diagnostico=guardar_diagnostico,
        instalar=_accion_instalar(),
        acerca_de=lambda: en_hilo_tk(
            lambda: messagebox.showinfo("Acerca de Voziris", texto_acerca_de(__version__))
        ),
    )
    bandeja = Bandeja(
        acciones, ultimas=historial.ultimas, motor_actual=lambda: motor.preferencia,
        corte_activo=lambda: orq.corte_por_silencio,
    )
    atajos = Atajos(
        orq.al_empezar_atajo, orq.terminar, orq.cancelar,
        en_curso=orq.en_curso, al_cambiar=orq.cambiar_destino,
    )

    try:
        try:
            aviso_mic = captura.abrir()
        except MicrofonoNoDisponible as e:
            aviso_mic = str(e)
        bandeja.mostrar_en_hilo()
        log.info("icono de bandeja visible")
        # Antes que nada: si la vez anterior quedó audio bajado, se devuelve ya.
        mezclador.arrancar()
        # El precalentado del modelo (~4 s de CPU) va DESPUÉS de que el icono
        # esté en pantalla: si arranca antes, compite con Tk y pystray y el
        # arranque visible pasa de 1,5 s a casi 4 (medido en VOZ-61).
        orq.arrancar()
        sonidos.precalentar()
        for aviso in configuracion.avisos:
            log.warning(aviso)
        if aviso_mic:
            avisar(aviso_mic)
            if not getattr(captura, "nombre_dispositivo", None):
                bandeja.estado("error")
        try:
            for aviso in atajos.registrar(vars(configuracion.atajos)):
                avisar(aviso)
        except AtajosNoDisponibles as e:
            avisar(f"{e}. Puedes dictar desde el menú de la bandeja")
            bandeja.estado("error")
        _ajustar_arranque_con_windows(configuracion, avisar)
        _revisar_paquete(avisar)
        for limpiar in (pendientes.limpiar, historial.limpiar_audio):
            try:
                limpiar()
            except Exception:  # noqa: BLE001 — mantenimiento: la app tiene que arrancar igual
                log.warning("limpieza al arrancar fallida", exc_info=True)
        sin_texto = pendientes.listar()
        if sin_texto:
            avisar(
                f"Hay {len(sin_texto)} dictado(s) cuyo audio se guardó sin llegar a texto: "
                "bandeja → Dictados sin transcribir"
            )
            log.info("pendientes al arrancar: %d", len(sin_texto))
        if configuracion.general.motor == "api" and not configuracion.motor.api.clave:
            # Sin esto, el usuario elige «api», todo se transcribe en local y lo
            # único que lo delata es un aviso efímero en el indicador flotante.
            avisar(
                "Motor por API elegido pero sin clave: se dicta en local. "
                "Pega tu clave de Groq en Ajustes → Motor."
            )
            log.warning("motor = api sin clave: todos los dictados irán al motor local")
        log.info("Voziris %s arrancado", __version__)
        raiz.after(16, bombear)
        if winapi.ES_WINDOWS:
            winapi.crear_evento_salida()
            raiz.after(500, vigilar_salida)
        raiz.mainloop()
    finally:
        log.info("cerrando")
        _forzar_salida_en(10)
        mezclador.cerrar()  # lo primero: nadie se queda sin sonido
        orq.parar()
        atajos.liberar()
        captura.cerrar()
        sonidos.cerrar()
        bandeja.cerrar()
        with contextlib.suppress(Exception):
            hud.destruir()
            raiz.destroy()
    return 0


def _forzar_salida_en(segundos: float) -> None:
    """Si el cierre limpio se atasca (un hilo nativo que no vuelve), el proceso muere igual.

    Visto en VOZ-72: `--instalar` pidió salir a la Voziris abierta, escribió
    «cerrando» y se quedó viva sin bucle de eventos, con el hook de teclado
    puesto. Un proceso zombi es peor que uno que se va sin recoger.
    """
    import threading

    def matar() -> None:
        log.error("el cierre no terminó en %.0f s: salida forzada", segundos)
        logging.shutdown()
        os._exit(0)

    temporizador = threading.Timer(segundos, matar)
    temporizador.daemon = True
    temporizador.start()


def _generar_diagnostico(carpeta: Path) -> Path:
    from voziris import diagnostico

    return diagnostico.generar(carpeta)


def _accion_instalar() -> Callable[[], None] | None:
    """La entrada «Instalar en este equipo…» solo cuando se corre portable."""
    from voziris import instalador

    if not getattr(sys, "frozen", False) or instalador.esta_instalado():
        return None

    def instalar() -> None:
        # En otro proceso: el instalador cierra esta instancia y arranca la nueva.
        _lanzar_desatendido(_comando_propio("--instalar"))

    return instalar


def _revisar_paquete(avisar: Callable[[str], None]) -> None:
    """Si al paquete le falta un archivo, decirlo al arrancar y no al primer archivo transcrito.

    PyAV solo se carga al transcribir: sin esto, una DLL en cuarentena no se
    nota hasta días después, con un mensaje que no dice cuál (VOZ-81). Solo
    se miran tamaños, en un hilo: unos milisegundos.
    """
    import threading

    carpeta = integridad.carpeta_del_paquete()
    if carpeta is None:
        return

    def revisar() -> None:
        if integridad.ruta_demasiado_larga(carpeta):
            log.warning("ruta demasiado larga: %s (%d caracteres)", carpeta, len(str(carpeta)))
            avisar(integridad.aviso_de_ruta_larga(carpeta))
        resultado = integridad.comprobar(carpeta)
        if resultado.sin_manifiesto:
            return
        if resultado.ok:
            log.info("paquete completo: %d archivos", resultado.comprobados)
            return
        log.error("al paquete le faltan archivos: faltan %s · dañados %s",
                  resultado.faltan[:20], resultado.distintos[:20])
        avisar(f"A Voziris le faltan archivos ({resultado.resumen(1)}). "
               "Vuelve a extraer el ZIP e instálalo; ¿lo ha retirado el antivirus?")

    threading.Thread(target=revisar, name="voziris-paquete", daemon=True).start()


def _ajustar_arranque_con_windows(configuracion: cfg.Config, avisar: Callable[[str], None]) -> None:
    """C-4: el acceso directo se crea o borra solo cuando el valor cambia respecto al real."""
    from voziris.ui.bandeja import Bandeja

    try:
        if Bandeja.configurar_arranque(configuracion.general.arranque_con_windows):
            if configuracion.general.arranque_con_windows:
                avisar("Voziris arrancará con Windows. Se cambia en config.toml o en los ajustes")
            else:
                avisar("Voziris ya no arrancará con Windows")
    except Exception as e:  # noqa: BLE001 — no es motivo para no arrancar
        log.warning("no se pudo ajustar el arranque con Windows: %s", e)


# --- instalación y diagnóstico desde la línea de comandos --------------------------------------


def _comando_propio(*argumentos: str) -> list[str]:
    """Cómo volver a lanzar Voziris: el .exe congelado o `python -m voziris`."""
    if getattr(sys, "frozen", False):
        return [sys.executable, *argumentos]
    return [sys.executable, "-m", "voziris", *argumentos]


def _lanzar_desatendido(orden: list[str], cwd: Path | None = None) -> None:
    import subprocess

    subprocess.Popen(  # noqa: S603 — orden construida aquí, no por el usuario
        orden,
        cwd=str(cwd) if cwd else None,
        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        close_fds=True,
    )


def _abrir_con_windows(ruta: Path) -> None:
    try:
        os.startfile(str(ruta))
    except (OSError, AttributeError) as e:
        log.warning("no se pudo abrir %s: %s", ruta, e)


def _mensaje(titulo: str, texto: str) -> None:
    """Un cuadro de diálogo cuando no hay consola que leer."""
    print(f"{titulo}: {texto}")
    if getattr(sys, "frozen", False):
        try:
            import tkinter as tk
            from tkinter import messagebox

            raiz = tk.Tk()
            raiz.withdraw()
            messagebox.showinfo(titulo, texto)
            raiz.destroy()
        except Exception:  # noqa: BLE001 — ya está impreso y en el log
            pass


def _cerrar_la_abierta() -> None:
    if winapi.ES_WINDOWS and winapi.pedir_salida():
        import time

        for _ in range(30):
            time.sleep(0.2)
            if not winapi.hay_instancia_abierta():
                break


def _instalar_cli() -> int:
    """Instala con una ventana de progreso. Si algo falla, no se cambia nada y se dice qué."""
    from voziris import instalador
    from voziris.ui.transcripcion import TranscripcionCancelada, ejecutar_con_progreso

    titulo_error = "Voziris no se ha instalado"
    cerrada: list[bool] = []

    def cerrar_la_abierta() -> None:
        cerrada.append(winapi.ES_WINDOWS and winapi.hay_instancia_abierta())
        _cerrar_la_abierta()

    def trabajo(progreso: Callable[[str, float | None], None]) -> Path:
        return instalador.instalar(
            comprobar_copia=(
                instalador.comprobar_arrancando if getattr(sys, "frozen", False) else None
            ),
            # La Voziris abierta se cierra justo antes de cambiar el programa, no al
            # empezar: si la instalación no sigue, el usuario no se queda sin ella.
            antes_de_sustituir=cerrar_la_abierta,
            al_progresar=progreso,
            pausar_volcados=volcados_en_pausa,
        )

    try:
        destino = ejecutar_con_progreso(
            "Instalando Voziris", f"En {instalador.carpeta_instalacion()}", trabajo
        )
    except TranscripcionCancelada:
        log.info("instalación cancelada")
        _mensaje("Instalación cancelada", "No se ha cambiado nada.")
        return 3
    except instalador.InstalacionFallida as e:
        _reabrir_si_se_cerro(any(cerrada), instalador.carpeta_instalacion())
        _error_fatal(str(e), con_ventana=True, titulo=titulo_error)
        return 1
    except Exception as e:  # noqa: BLE001 — sin esto, el cuadro de PyInstaller con la traza
        log.exception("instalación fallida")
        _reabrir_si_se_cerro(any(cerrada), instalador.carpeta_instalacion())
        _error_fatal(f"No se pudo instalar Voziris: {e}", con_ventana=True, titulo=titulo_error)
        return 1
    log.info("instalado en %s", destino)
    aviso = _abrir_la_instalada(destino)
    _mensaje(
        "Voziris instalado",
        "Ya puedes escribir «Voziris» en la búsqueda de Windows.\n\n"
        f"Carpeta: {destino}\n"
        "Para quitarlo: Configuración → Aplicaciones → Voziris → Desinstalar."
        + (f"\n\n{aviso}" if aviso else ""),
    )
    return 0


def _reabrir_si_se_cerro(se_cerro: bool, destino: Path) -> None:
    """La instalación cerró la Voziris abierta y luego falló: que el usuario no se quede sin ella.

    El programa de antes sigue en su sitio (la sustitución se deshace). Se
    abre la instalada si está, y si no, esta misma copia.
    """
    if not se_cerro:
        return
    exe = destino / "voziris.exe"
    orden = [str(exe)] if exe.is_file() else _comando_propio()
    try:
        _lanzar_desatendido(orden, cwd=exe.parent if exe.is_file() else None)
        log.info("se vuelve a abrir la Voziris que se cerró para instalar: %s", orden[0])
    except OSError as e:
        log.warning("no se pudo volver a abrir Voziris: %s", e)


def _abrir_la_instalada(destino: Path) -> str | None:
    """Arranca la copia instalada por su acceso del menú Inicio, como lo hará el usuario.

    Por el acceso directo y no por el .exe: Control inteligente de
    aplicaciones puede bloquear un acceso directo que considere «de
    Internet» aunque el programa arranque bien (VOZ-81). Si el acceso no la
    arranca, se arranca el .exe y se devuelve el aviso para el usuario.

    No basta con que `os.startfile` no lance: cuando Windows bloquea, puede
    enseñar su aviso y dar la llamada por buena. Lo que cuenta es que aparezca
    el mutex de la instancia.
    """
    from voziris import instalador

    acceso = instalador.carpeta_menu_inicio() / instalador.ACCESO_MENU
    try:
        os.startfile(str(acceso))
    except OSError as e:
        log.warning("no se pudo abrir el acceso del menú Inicio (%s): %s", acceso, e)
    else:
        if not winapi.ES_WINDOWS or _espera_a_la_instancia(ESPERA_ARRANQUE_S):
            return None
        log.warning("la instalada no arrancó por su acceso del menú Inicio en %d s",
                    ESPERA_ARRANQUE_S)
    if winapi.ES_WINDOWS and winapi.hay_instancia_abierta():
        return None
    try:
        _lanzar_desatendido([str(destino / "voziris.exe")], cwd=destino)
    except OSError as e:
        log.warning("tampoco arranca el .exe instalado: %s", e)
        if integridad.es_bloqueo_de_windows(e):
            return integridad.explicacion_de_bloqueo("Voziris")
        return f"Voziris está instalado, pero no se ha podido abrir: {e}"
    # En «evaluación» Control inteligente de aplicaciones solo observa: no bloquea.
    if integridad.control_inteligente() == "activado":
        return (
            "Windows no ha dejado abrir el acceso directo del menú Inicio (lo bloquea "
            "«Control inteligente de aplicaciones»). Voziris funciona: ve a la carpeta de "
            "arriba, botón derecho en voziris.exe → Anclar a Inicio."
        )
    return (
        "No se pudo abrir el acceso directo del menú Inicio; se ha abierto Voziris "
        "directamente. Si no aparece en la búsqueda, ve a la carpeta de arriba y usa "
        "botón derecho en voziris.exe → Anclar a Inicio."
    )


ESPERA_ARRANQUE_S = 30
"""Lo que puede tardar el primer arranque, con el antivirus mirando cada DLL nueva."""


def _espera_a_la_instancia(segundos: float) -> bool:
    import time

    limite = time.monotonic() + segundos
    while time.monotonic() < limite:
        if winapi.hay_instancia_abierta():
            return True
        time.sleep(0.25)
    return False


def _desinstalar_cli() -> int:
    from voziris import instalador

    _cerrar_la_abierta()
    try:
        instalador.desinstalar()
    except OSError as e:
        _error_fatal(f"No se pudo desinstalar del todo: {e}", con_ventana=True)
        return 1
    _mensaje("Voziris desinstalado", "La carpeta se borra en unos segundos. Hasta otra.")
    return 0


def _menu_contextual_cli(activar: bool) -> int:
    from voziris import instalador

    if activar:
        instalador.registrar_menu_contextual(_comando_propio())
        _mensaje(
            "Menú contextual activado",
            "Botón derecho sobre una grabación → «Transcribir con Voziris».",
        )
    else:
        instalador.quitar_menu_contextual()
        _mensaje("Menú contextual quitado", "Ya no aparece «Transcribir con Voziris».")
    return 0


def _diagnostico_cli(carpeta: Path) -> int:
    from voziris import diagnostico

    ruta = diagnostico.generar(carpeta)
    print(f"Diagnóstico en {ruta}")
    _abrir_con_windows(ruta)
    return 0


# --- entrada ---------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Arranca la aplicación. Devuelve el código de salida."""
    parser = argparse.ArgumentParser(prog="voziris", description="Dictado por voz para Windows")
    parser.add_argument(
        "--config", type=Path, help="ruta del config.toml (por defecto, junto al ejecutable)"
    )
    parser.add_argument("--archivo", type=Path, help="modo consola: transcribe este WAV y sale")
    parser.add_argument(
        "--destino", choices=("app_activa", "markdown", "ninguno"), default="ninguno",
        help="modo consola: dónde entregar el texto (por defecto solo se imprime)",
    )
    parser.add_argument("--nivel", choices=[n.value for n in Nivel])
    parser.add_argument("--debug", action="store_true", help="log en nivel DEBUG")
    parser.add_argument(
        "--salir", action="store_true", help="cierra limpiamente la Voziris que esté abierta"
    )
    parser.add_argument(
        "--instalar", action="store_true",
        help="copia Voziris a Programs, lo pone en el menú Inicio y en Aplicaciones instaladas",
    )
    parser.add_argument(
        "--desinstalar", action="store_true", help="deshace --instalar (accesos, registro, carpeta)"
    )
    parser.add_argument(
        "--diagnostico", action="store_true",
        help="escribe diagnostico.txt con el registro y los fallos que anotó Windows, y lo abre",
    )
    parser.add_argument(
        "--transcribir", type=Path, metavar="GRABACION",
        help="transcribe una grabación (m4a, mp3, wav…) a un .md junto a ella y sale",
    )
    parser.add_argument(
        "--hablantes", default="auto", metavar="auto|1|N",
        help="con --transcribir: 1 (una voz), auto (varias, las distingue) o cuántas son",
    )
    parser.add_argument("--salida", type=Path, help="con --transcribir: el .md de destino")
    parser.add_argument(
        "--pista", default="auto", metavar="auto|N|todas",
        help="con --transcribir: qué pista de audio usar si el vídeo trae varias",
    )
    parser.add_argument(
        "--ventana", action="store_true",
        help="con --transcribir: progreso en una ventana (lo usan la bandeja y el Explorador)",
    )
    parser.add_argument(
        "--menu-contextual", action="store_true",
        help="añade «Transcribir con Voziris» al botón derecho de las grabaciones (sin instalar)",
    )
    parser.add_argument(
        "--sin-menu-contextual", action="store_true", help="quita ese menú contextual"
    )
    # Ocultas: para el instalador, el empaquetado y el diagnóstico (VOZ-81).
    parser.add_argument("--comprobar", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--informe", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--version", action="version", version=f"voziris {__version__}")
    _sin_consola()
    args = parser.parse_args(argv)

    if args.comprobar:
        # Desde `python -m voziris` y los tests; el .exe ni llega aquí (ver arriba).
        return integridad.ejecutar_comprobacion(args.informe)

    if args.salir:
        if winapi.ES_WINDOWS and winapi.pedir_salida():
            print("Cierre pedido a la Voziris abierta.")
            return 0
        print("No hay ninguna Voziris abierta.", file=sys.stderr)
        return 1

    consola = args.archivo is not None
    transcribir = args.transcribir is not None
    if transcribir and not _hablantes_validos(args.hablantes):
        parser.error("--hablantes tiene que ser auto, 1 o un número de 2 a 50")
    if transcribir and not _pista_valida(args.pista):
        parser.error("--pista tiene que ser auto, todas, o el número de una pista")
    con_ventana = transcribir and (args.ventana or bool(getattr(sys, "frozen", False)))
    carpeta = args.config.parent if args.config else cfg.carpeta_base()
    configurar_log(
        carpeta, args.debug, a_consola=consola or (transcribir and not con_ventana) or args.debug,
        nombre=NOMBRE_LOG_TRANSCRIBIR if transcribir else NOMBRE_LOG,
    )
    activar_faulthandler(carpeta)
    anotar_arranque_en_fallos(
        "instalar" if args.instalar else "desinstalar" if args.desinstalar
        else "diagnóstico" if args.diagnostico else "consola" if consola
        else "transcribir" if transcribir else "bandeja"
    )
    log.info("arrancando Voziris %s", __version__)  # la marca de tiempo del arranque

    if args.instalar:
        return _instalar_cli()
    if args.desinstalar:
        return _desinstalar_cli()
    if args.diagnostico:
        return _diagnostico_cli(carpeta)
    if args.menu_contextual or args.sin_menu_contextual:
        return _menu_contextual_cli(activar=args.menu_contextual)

    if not consola and not transcribir and winapi.ES_WINDOWS and not winapi.instancia_unica():
        _error_fatal("Voziris ya está abierto: mira el icono de la bandeja.", con_ventana=True)
        return 0

    try:
        configuracion = cfg.cargar(args.config)
    except ConfigInvalida as e:
        _error_fatal(str(e), con_ventana=not consola)
        return 2
    logging.getLogger().addFilter(_TacharClave(configuracion.motor.api.clave))

    if transcribir:
        return _transcribir_grabacion(
            configuracion, args.transcribir, args.hablantes, args.salida, con_ventana, args.pista
        )
    if consola:
        nivel = Nivel(args.nivel) if args.nivel else configuracion.proceso.nivel
        return _modo_consola(configuracion, args.archivo, args.destino, nivel)
    return _aplicacion(configuracion, args.config)


def _hablantes_validos(valor: str) -> bool:
    return valor in ("auto", "1") or (valor.isdigit() and 2 <= int(valor) <= 50)


def _pista_valida(valor: str) -> bool:
    return valor in ("auto", "todas") or (valor.isdigit() and 1 <= int(valor) <= 64)


if __name__ == "__main__":
    raise SystemExit(main())
