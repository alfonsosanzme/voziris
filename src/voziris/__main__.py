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

from voziris import __version__, winapi
from voziris import config as cfg
from voziris.errores import AtajosNoDisponibles, ConfigInvalida, MicrofonoNoDisponible
from voziris.tipos import Nivel

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


def configurar_log(carpeta: Path, depurar: bool, a_consola: bool) -> None:
    raiz = logging.getLogger()
    raiz.setLevel(logging.DEBUG if depurar else logging.INFO)
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
    from voziris.motores.local import MotorLocal

    a = configuracion.audio
    captura = Captura(a.dispositivo, a.ganancia_db, a.buffer_previo_ms)
    ml = configuracion.motor.local
    motor = MotorLocal(ml.modelo, ml.carpeta, ml.hilos, ml.cuantizacion, al_progresar)
    d = configuracion.destino.app_activa
    destinos = {"app_activa": AppActiva(d.metodo, d.restaurar_portapapeles, d.auto_enter)}
    # VOZ-50 añadirá "markdown"; hasta entonces el atajo avisa de que no está disponible.
    return captura, motor, destinos


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
        def empezar_dictado(self) -> None: ...

        def terminar_dictado(self) -> Audio:
            return audio

        def cancelar_dictado(self) -> None: ...

    orq = Orquestador(SinCaptura(), motor, destinos, [], configuracion.general.idioma, nivel)
    print("Cargando el motor…")
    orq.arrancar()
    resultado = orq.dictar_audio(audio, destino)
    orq.parar()
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

    def avisar(texto: str) -> None:
        if bandeja is not None:
            bandeja.avisar(texto)

    def cambiar_estado(estado: str) -> None:
        if bandeja is not None:
            bandeja.estado(estado)

    captura, motor, destinos = _construir(configuracion, lambda m, f: avisar_progreso(m, f))
    ultimo_progreso = [0.0]

    def avisar_progreso(mensaje: str, fraccion: float | None) -> None:
        # Una notificación cada 10 % o al terminar: sin aturdir.
        if fraccion is None or fraccion >= 1.0 or fraccion - ultimo_progreso[0] >= 0.1:
            ultimo_progreso[0] = fraccion or 0.0
            avisar(mensaje)

    from voziris.audio.sonidos import Sonidos

    sonidos = Sonidos(configuracion.audio.sonidos)
    orq = Orquestador(
        captura, motor, destinos, [],
        idioma=configuracion.general.idioma,
        nivel=configuracion.proceso.nivel,
        al_estado=cambiar_estado,
        al_aviso=avisar,
        sonidos=sonidos,
        app_en_primer_plano=destinos["app_activa"].app_en_primer_plano,
    )

    acciones = AccionesBandeja(
        dictar_ahora=lambda: orq.alternar_clavar(),
        abrir_ajustes=lambda: avisar("Los ajustes llegan en VOZ-60; edita config.toml"),
        cambiar_motor=lambda m: avisar(f"El cambio de motor llega en VOZ-31 (pedido: {m})"),
        reintentar=lambda _i: avisar("El historial llega en VOZ-52"),
        borrar_entrada=lambda _i: avisar("El historial llega en VOZ-52"),
        salir=lambda: en_hilo_tk(raiz.quit),
        acerca_de=lambda: en_hilo_tk(
            lambda: messagebox.showinfo("Acerca de Voziris", texto_acerca_de(__version__))
        ),
    )
    bandeja = Bandeja(acciones, motor_actual=lambda: configuracion.general.motor)
    atajos = Atajos(orq.al_empezar_atajo, orq.terminar, orq.cancelar, en_curso=orq.en_curso)

    try:
        try:
            aviso_mic = captura.abrir()
        except MicrofonoNoDisponible as e:
            aviso_mic = str(e)
        orq.arrancar()
        sonidos.precalentar()
        bandeja.mostrar_en_hilo()
        log.info("icono de bandeja visible")
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
