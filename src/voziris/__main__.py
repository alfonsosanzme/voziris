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

Issue: VOZ-04 (orquestación), VOZ-05 (instancia única).
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import logging.handlers
import queue
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from voziris import __version__, red, winapi
from voziris import config as cfg
from voziris.errores import (
    AtajosNoDisponibles,
    ConfigInvalida,
    MicrofonoNoDisponible,
    VozirisError,
)
from voziris.tipos import Modo, Nivel

log = logging.getLogger("voziris")

NOMBRE_LOG = "voziris.log"


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


def configurar_log(carpeta: Path, depurar: bool, a_consola: bool) -> None:
    raiz = logging.getLogger()
    raiz.setLevel(logging.DEBUG if depurar else logging.INFO)
    for ruidoso in ("httpx", "httpcore", "huggingface_hub", "urllib3", "filelock"):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)
    formato = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    try:
        archivo = logging.handlers.RotatingFileHandler(
            carpeta / NOMBRE_LOG, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        archivo.setFormatter(formato)
        raiz.addHandler(archivo)
    except OSError as e:
        print(f"No se puede escribir el log en {carpeta}: {e}", file=sys.stderr)
    if a_consola:
        consola = logging.StreamHandler(sys.stderr)
        consola.setFormatter(formato)
        raiz.addHandler(consola)


def _error_fatal(mensaje: str, con_ventana: bool) -> None:
    log.error(mensaje)
    print(mensaje, file=sys.stderr)
    if con_ventana:
        try:
            import tkinter as tk
            from tkinter import messagebox

            raiz = tk.Tk()
            raiz.withdraw()
            messagebox.showerror("Voziris no puede arrancar", mensaje)
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
    from voziris.motores.api import MotorAPI
    from voziris.motores.local import MotorLocal
    from voziris.motores.selector import Selector

    a = configuracion.audio
    captura = Captura(a.dispositivo, a.ganancia_db, a.buffer_previo_ms)
    ml = configuracion.motor.local
    local = MotorLocal(ml.modelo, ml.carpeta, ml.hilos, ml.cuantizacion, al_progresar)
    ma = configuracion.motor.api
    api = MotorAPI(ma.base_url, ma.modelo, ma.clave, ma.timeout_s)
    motor = Selector(configuracion.general.motor, local, api)
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


# --- aplicación -------------------------------------------------------------------------


def _aplicacion(configuracion: cfg.Config) -> int:
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
        configuracion.carpeta / "historial" / "dictados.jsonl", h.entradas, h.guardar_audio,
        destinos, contexto_de_reintento,
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
    )
    orq.corte_por_silencio = configuracion.audio.corte_por_silencio

    def reintentar(indice: int) -> None:
        """Desde la bandeja: el menú se cierra y el foco vuelve a la app; se espera un poco."""
        import threading
        import time

        def trabajo() -> None:
            time.sleep(0.4)
            entrega = historial.reintentar(indice)
            if entrega.ok:
                hud.aviso(entrega.detalle)
            else:
                hud.aviso(f"No se pudo reentregar: {entrega.detalle}")
            if bandeja is not None:
                bandeja.actualizar_menu()

        threading.Thread(target=trabajo, name="voziris-reintento", daemon=True).start()

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

    ajustes = Ajustes(
        raiz, configuracion, aplicar, nivel_actual=captura.nivel_actual,
        dispositivos=Captura.dispositivos, probar_clave=probar_clave,
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

    acciones = AccionesBandeja(
        dictar_ahora=lambda: orq.alternar_clavar(),
        dictar_markdown=lambda: orq.alternar_clavar("markdown"),
        alternar_corte=alternar_corte,
        abrir_ajustes=lambda: en_hilo_tk(ajustes.abrir),
        cambiar_motor=cambiar_motor,
        reintentar=reintentar,
        borrar_entrada=borrar_entrada,
        salir=lambda: en_hilo_tk(raiz.quit),
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
        log.info("Voziris %s arrancado", __version__)
        raiz.after(16, bombear)
        if winapi.ES_WINDOWS:
            winapi.crear_evento_salida()
            raiz.after(500, vigilar_salida)
        raiz.mainloop()
    finally:
        log.info("cerrando")
        orq.parar()
        atajos.liberar()
        captura.cerrar()
        sonidos.cerrar()
        bandeja.cerrar()
        with contextlib.suppress(Exception):
            hud.destruir()
            raiz.destroy()
    return 0


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
    parser.add_argument("--version", action="version", version=f"voziris {__version__}")
    _sin_consola()
    args = parser.parse_args(argv)

    if args.salir:
        if winapi.ES_WINDOWS and winapi.pedir_salida():
            print("Cierre pedido a la Voziris abierta.")
            return 0
        print("No hay ninguna Voziris abierta.", file=sys.stderr)
        return 1

    consola = args.archivo is not None
    carpeta = args.config.parent if args.config else cfg.carpeta_base()
    configurar_log(carpeta, args.debug, a_consola=consola or args.debug)
    log.info("arrancando Voziris %s", __version__)  # la marca de tiempo del arranque

    if not consola and winapi.ES_WINDOWS and not winapi.instancia_unica():
        _error_fatal("Voziris ya está abierto: mira el icono de la bandeja.", con_ventana=True)
        return 0

    try:
        configuracion = cfg.cargar(args.config)
    except ConfigInvalida as e:
        _error_fatal(str(e), con_ventana=not consola)
        return 2
    logging.getLogger().addFilter(_TacharClave(configuracion.motor.api.clave))

    if consola:
        nivel = Nivel(args.nivel) if args.nivel else configuracion.proceso.nivel
        return _modo_consola(configuracion, args.archivo, args.destino, nivel)
    return _aplicacion(configuracion)


if __name__ == "__main__":
    raise SystemExit(main())
