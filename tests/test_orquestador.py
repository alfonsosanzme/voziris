"""Tests de la máquina de estados del dictado (VOZ-04) y de la instancia única (VOZ-05).

Todo con dobles: captura, motor, destino y sonidos falsos. Sin Windows salvo
el test del mutex.
"""

from __future__ import annotations

import sys
import threading
import time
from typing import Any

import numpy as np
import pytest

from voziris.errores import EntregaFallida, MicrofonoNoDisponible, TranscripcionFallida
from voziris.orquestador import PULSACION_MINIMA_MS, Estado, Orquestador
from voziris.tipos import (
    SAMPLE_RATE,
    Audio,
    Contexto,
    EntradaHistorial,
    Entrega,
    Modo,
    Transcripcion,
)


class CapturaFalsa:
    def __init__(self) -> None:
        self.grabando = False
        self.cancelados = 0
        self.falla_al_terminar = False
        self.oyente_bloques: Any = None

    def bloque(self) -> None:
        """Simula al hilo de audio entregando un bloque de 32 ms."""
        if self.oyente_bloques is not None:
            self.oyente_bloques(np.zeros(512, dtype=np.float32))

    def empezar_dictado(self) -> None:
        self.grabando = True

    def terminar_dictado(self) -> Audio:
        self.grabando = False
        if self.falla_al_terminar:
            raise MicrofonoNoDisponible("los auriculares se fueron")
        return Audio(muestras=np.zeros(SAMPLE_RATE * 2, dtype=np.float32))

    def cancelar_dictado(self) -> None:
        self.grabando = False
        self.cancelados += 1


class MotorFalso:
    nombre = "local"
    requiere_red = False

    def __init__(self, texto: str = "Hola, mundo.", tarda_s: float = 0.0) -> None:
        self.texto = texto
        self.tarda_s = tarda_s
        self.cargado = False
        self.falla: Exception | None = None
        self.llamadas = 0

    def precalentar(self) -> None:
        self.cargado = True

    def disponible(self) -> bool:
        return self.cargado

    def transcribir(self, audio: Audio, idioma: str) -> Transcripcion:
        self.llamadas += 1
        time.sleep(self.tarda_s)
        if self.falla is not None:
            raise self.falla
        return Transcripcion(self.texto, idioma, "local", 40, audio.duracion_s)


class DestinoFalso:
    nombre = "app_activa"

    def __init__(self) -> None:
        self.entregas: list[tuple[str, Contexto]] = []
        self.falla = False
        self.hay = threading.Condition()

    def entregar(self, texto: str, ctx: Contexto) -> Entrega:
        if self.falla:
            raise EntregaFallida("no se pudo pegar en chrome.exe")
        with self.hay:
            self.entregas.append((texto, ctx))
            self.hay.notify_all()
        return Entrega(ok=True, detalle="pegado en notepad.exe")

    def esperar(self, n: int = 1, timeout: float = 3.0) -> list[tuple[str, Contexto]]:
        with self.hay:
            self.hay.wait_for(lambda: len(self.entregas) >= n, timeout=timeout)
            return list(self.entregas)


class SonidosFalsos:
    def __init__(self) -> None:
        self.tonos: list[str] = []

    def inicio(self) -> None:
        self.tonos.append("inicio")

    def fin(self) -> None:
        self.tonos.append("fin")

    def error(self) -> None:
        self.tonos.append("error")


class HistorialFalso:
    def __init__(self) -> None:
        self.entradas: list[EntradaHistorial] = []

    def registrar(self, entrada: EntradaHistorial, audio: Audio | None = None) -> None:
        self.entradas.append(entrada)


class VadFalso:
    """Corta en el bloque `corta_en` (contando desde reiniciar)."""

    def __init__(self, corta_en: int = 3) -> None:
        self.corta_en = corta_en
        self.bloques = 0
        self.reinicios = 0

    def precalentar(self) -> None: ...

    def reiniciar(self) -> None:
        self.reinicios += 1
        self.bloques = 0

    def alimentar(self, bloque: np.ndarray) -> bool:
        self.bloques += 1
        return self.bloques >= self.corta_en


class Banco:
    """Todas las piezas falsas juntas y el orquestador arrancado."""

    def __init__(
        self, motor: MotorFalso | None = None, precalentado: bool = True,
        vad: VadFalso | None = None,
    ) -> None:
        self.captura = CapturaFalsa()
        self.motor = motor or MotorFalso()
        self.destino = DestinoFalso()
        self.sonidos = SonidosFalsos()
        self.historial = HistorialFalso()
        self.vad = vad
        self.estados: list[str] = []
        self.avisos: list[str] = []
        self.orq = Orquestador(
            self.captura, self.motor, {"app_activa": self.destino}, [],
            al_estado=self.estados.append, al_aviso=self.avisos.append,
            sonidos=self.sonidos, historial=self.historial, vad=vad,
            app_en_primer_plano=lambda: "notepad.exe",
        )
        self.orq.arrancar()
        if precalentado:
            assert self.orq.motor_listo.wait(3)

    def esperar_reposo(self, timeout: float = 3.0) -> None:
        limite = time.monotonic() + timeout
        while self.orq.estado in (Estado.PROCESANDO,) and time.monotonic() < limite:
            time.sleep(0.01)

    def parar(self) -> None:
        self.orq.parar()


@pytest.fixture
def banco() -> Banco:
    b = Banco()
    yield b  # type: ignore[misc]
    b.parar()


# --- flujo normal --------------------------------------------------------------------


def test_mantener_de_principio_a_fin(banco: Banco) -> None:
    assert banco.orq.empezar(Modo.MANTENER, "app_activa")
    assert banco.orq.estado is Estado.GRABANDO and banco.captura.grabando
    assert banco.orq.en_curso()
    banco.orq.terminar(duracion_ms=1500)
    entregas = banco.destino.esperar()
    banco.esperar_reposo()
    assert entregas[0][0] == "Hola, mundo."
    ctx = entregas[0][1]
    assert ctx.destino == "app_activa" and ctx.modo is Modo.MANTENER
    assert ctx.app_activa == "notepad.exe"
    assert banco.estados == ["grabando", "procesando", "reposo"]
    assert banco.sonidos.tonos == ["inicio", "fin"]
    assert banco.historial.entradas[0].texto == "Hola, mundo."
    assert banco.historial.entradas[0].entregado
    assert not banco.orq.en_curso()


def test_pulsacion_corta_se_descarta_sin_transcribir(banco: Banco) -> None:
    banco.orq.empezar(Modo.MANTENER, "app_activa")
    banco.orq.terminar(duracion_ms=PULSACION_MINIMA_MS - 1)
    time.sleep(0.1)
    assert banco.orq.estado is Estado.REPOSO
    assert banco.captura.cancelados == 1 and banco.motor.llamadas == 0
    assert banco.estados == ["grabando", "reposo"]
    assert banco.sonidos.tonos == ["inicio"]  # sin tono de fin: no pasó nada


def test_clavar_alterna_y_dictar_ahora_es_lo_mismo(banco: Banco) -> None:
    banco.orq.al_empezar_atajo(Modo.CLAVAR, "app_activa")
    assert banco.orq.estado is Estado.GRABANDO
    banco.orq.al_empezar_atajo(Modo.CLAVAR, "app_activa")  # segunda pulsación: cierra
    assert banco.destino.esperar()[0][1].modo is Modo.CLAVAR
    banco.esperar_reposo()
    banco.orq.alternar_clavar()  # «Dictar ahora» desde la bandeja
    assert banco.orq.estado is Estado.GRABANDO
    banco.orq.alternar_clavar()
    assert len(banco.destino.esperar(2)) == 2


def test_mantener_suelto_no_cierra_un_clavar(banco: Banco) -> None:
    banco.orq.alternar_clavar()
    banco.orq.al_empezar_atajo(Modo.MANTENER, "app_activa")  # ignorado: ya hay dictado
    assert banco.sonidos.tonos[-1] == "error"
    assert banco.orq.estado is Estado.GRABANDO


def test_un_dictado_nuevo_mientras_hay_otro_se_ignora(banco: Banco) -> None:
    banco.orq.empezar(Modo.MANTENER, "app_activa")
    assert not banco.orq.empezar(Modo.MANTENER, "app_activa")
    assert banco.sonidos.tonos == ["inicio", "error"]
    banco.motor.tarda_s = 0.3
    banco.orq.terminar(1000)
    assert banco.orq.estado is Estado.PROCESANDO
    assert not banco.orq.empezar(Modo.MANTENER, "app_activa")
    banco.destino.esperar()
    banco.esperar_reposo()
    assert banco.motor.llamadas == 1


# --- cancelar ----------------------------------------------------------------------------


def test_cancelar_grabando_descarta(banco: Banco) -> None:
    banco.orq.empezar(Modo.MANTENER, "app_activa")
    banco.orq.cancelar()
    assert banco.orq.estado is Estado.REPOSO
    assert banco.captura.cancelados == 1 and banco.motor.llamadas == 0
    banco.orq.terminar(1000)  # ya no hay nada que terminar
    time.sleep(0.05)
    assert banco.destino.entregas == []


def test_cancelar_procesando_no_entrega(banco: Banco) -> None:
    banco.motor.tarda_s = 0.3
    banco.orq.empezar(Modo.MANTENER, "app_activa")
    banco.orq.terminar(1000)
    banco.orq.cancelar()
    time.sleep(0.6)
    assert banco.destino.entregas == []
    assert banco.orq.estado is Estado.REPOSO
    # Cancelar es «no lo pegues», no «tíralo»: el texto queda sin entregar (VOZ-74).
    assert [(e.texto, e.entregado) for e in banco.historial.entradas] == [("Hola, mundo.", False)]


def test_terminar_en_reposo_no_hace_nada(banco: Banco) -> None:
    banco.orq.terminar(1000)
    banco.orq.cancelar()
    assert banco.orq.estado is Estado.REPOSO and banco.estados == []


# --- motor -----------------------------------------------------------------------------


def test_dictar_antes_del_precalentado_espera() -> None:
    class MotorLento(MotorFalso):
        def precalentar(self) -> None:
            time.sleep(0.4)
            self.cargado = True

    b = Banco(MotorLento(), precalentado=False)
    try:
        b.orq.empezar(Modo.MANTENER, "app_activa")
        b.orq.terminar(1000)
        assert b.orq.estado is Estado.PROCESANDO
        assert b.destino.esperar(timeout=3)[0][0] == "Hola, mundo."
    finally:
        b.parar()


def test_motor_no_disponible_es_error_y_se_recupera() -> None:
    motor = MotorFalso()
    motor.precalentar = lambda: None  # type: ignore[method-assign]  # nunca carga
    b = Banco(motor)
    try:
        b.orq.empezar(Modo.MANTENER, "app_activa")
        b.orq.terminar(1000)
        b.esperar_reposo()
        time.sleep(0.05)
        assert b.orq.estado is Estado.ERROR
        assert any("no está disponible" in a for a in b.avisos)
        assert "error" in b.sonidos.tonos
        # El siguiente evento sale del error y vuelve a intentarlo.
        motor.cargado = True
        assert b.orq.empezar(Modo.MANTENER, "app_activa")
        b.orq.terminar(1000)
        assert b.destino.esperar()
    finally:
        b.parar()


def test_transcripcion_vacia_avisa_y_vuelve_a_reposo(banco: Banco) -> None:
    banco.motor.falla = TranscripcionFallida("no se oyó nada")
    banco.orq.empezar(Modo.MANTENER, "app_activa")
    banco.orq.terminar(1000)
    banco.esperar_reposo()
    time.sleep(0.05)
    assert banco.orq.estado is Estado.REPOSO
    assert any("no se oyó nada" in a for a in banco.avisos)
    assert banco.destino.entregas == [] and banco.historial.entradas == []


# --- entrega y fallos ------------------------------------------------------------------


def test_entrega_fallida_deja_error_y_el_texto_en_el_historial(banco: Banco) -> None:
    banco.destino.falla = True
    banco.orq.empezar(Modo.MANTENER, "app_activa")
    banco.orq.terminar(1000)
    banco.esperar_reposo()
    time.sleep(0.05)
    assert banco.orq.estado is Estado.ERROR
    assert banco.historial.entradas[0].entregado is False
    assert banco.historial.entradas[0].texto == "Hola, mundo."
    assert any("chrome.exe" in a and "historial" in a for a in banco.avisos)


def test_excepcion_inesperada_no_mata_el_hilo_de_trabajo(banco: Banco) -> None:
    banco.motor.falla = RuntimeError("kaboom")
    banco.orq.empezar(Modo.MANTENER, "app_activa")
    banco.orq.terminar(1000)
    banco.esperar_reposo()
    time.sleep(0.05)
    assert banco.orq.estado is Estado.ERROR
    assert any("kaboom" in a for a in banco.avisos)
    banco.motor.falla = None
    banco.orq.empezar(Modo.MANTENER, "app_activa")
    banco.orq.terminar(1000)
    assert banco.destino.esperar()


def test_destino_desconocido(banco: Banco) -> None:
    assert not banco.orq.empezar(Modo.MANTENER, "markdown")
    assert any("markdown" in a for a in banco.avisos)
    assert banco.orq.estado is Estado.REPOSO


def test_microfono_perdido_al_terminar(banco: Banco) -> None:
    banco.orq.empezar(Modo.MANTENER, "app_activa")
    banco.captura.falla_al_terminar = True
    banco.orq.terminar(1000)
    assert banco.orq.estado is Estado.ERROR
    assert any("auriculares" in a for a in banco.avisos)
    assert banco.motor.llamadas == 0


def test_postproceso_que_lanza_no_pierde_el_dictado(banco: Banco) -> None:
    class Roto:
        nombre = "roto"
        requiere_red = False

        def aplicar(self, t: Transcripcion, ctx: Contexto) -> Transcripcion:
            raise ValueError("bum")

    class ConRed:
        nombre = "llm"
        requiere_red = True

        def aplicar(self, t: Transcripcion, ctx: Contexto) -> Transcripcion:
            t.texto = "nunca"
            return t

    banco.orq._postprocesos = [Roto(), ConRed()]  # sin red: el segundo se omite
    banco.orq.empezar(Modo.MANTENER, "app_activa")
    banco.orq.terminar(1000)
    texto, _ = banco.destino.esperar()[0]
    assert texto == "Hola, mundo."
    banco.esperar_reposo()
    assert any("roto" in a for a in banco.avisos) and any("Sin red" in a for a in banco.avisos)


# --- modo clavar con VAD (VOZ-24) ---------------------------------------------------------


def _bloques(banco: Banco, n: int, espera_s: float = 0.0) -> None:
    for _ in range(n):
        banco.captura.bloque()
        if espera_s:
            time.sleep(espera_s)


def test_el_vad_cierra_el_dictado_clavado() -> None:
    b = Banco(vad=VadFalso(corta_en=3))
    try:
        assert b.captura.oyente_bloques is not None  # el orquestador se enganchó a la captura
        b.orq.alternar_clavar()
        assert b.orq.modo is Modo.CLAVAR and b.vad is not None and b.vad.reinicios == 1
        _bloques(b, 2, 0.02)
        time.sleep(0.05)
        assert b.orq.estado is Estado.GRABANDO  # dos bloques: aún no
        _bloques(b, 1)
        assert b.destino.esperar()[0][1].modo is Modo.CLAVAR
        b.esperar_reposo()
        assert b.orq.estado is Estado.REPOSO and b.orq.modo is None
    finally:
        b.parar()


def test_en_mantener_el_vad_no_interviene() -> None:
    b = Banco(vad=VadFalso(corta_en=1))
    try:
        b.orq.empezar(Modo.MANTENER, "app_activa")
        _bloques(b, 20, 0.005)
        time.sleep(0.1)
        assert b.orq.estado is Estado.GRABANDO
        assert b.vad is not None and b.vad.bloques == 0 and b.vad.reinicios == 0
        b.orq.terminar(1000)
        assert b.destino.esperar()
    finally:
        b.parar()


def test_los_dos_atajos_conviven_con_vad() -> None:
    """H2: clavar, cierre por VAD, y luego mantener, sin interferencias."""
    b = Banco(vad=VadFalso(corta_en=2))
    try:
        b.orq.alternar_clavar()
        _bloques(b, 2, 0.02)
        b.destino.esperar(1)
        b.esperar_reposo()
        b.orq.empezar(Modo.MANTENER, "app_activa")
        _bloques(b, 5, 0.005)  # bloques durante mantener: el VAD no los cuenta
        b.orq.terminar(1000)
        entregas = b.destino.esperar(2)
        assert [e[1].modo for e in entregas] == [Modo.CLAVAR, Modo.MANTENER]
        assert b.vad is not None and b.vad.reinicios == 1
    finally:
        b.parar()


def test_cerrar_a_mano_antes_del_vad_y_cancelar_desconectan_el_vad() -> None:
    b = Banco(vad=VadFalso(corta_en=100))
    try:
        b.orq.alternar_clavar()
        _bloques(b, 3, 0.005)
        b.orq.alternar_clavar()  # segunda pulsación: cierra sin esperar al VAD
        b.destino.esperar()
        b.esperar_reposo()
        b.orq.alternar_clavar()
        b.orq.cancelar()
        assert b.orq.estado is Estado.REPOSO
        _bloques(b, 5, 0.005)
        time.sleep(0.05)
        assert b.orq.estado is Estado.REPOSO
    finally:
        b.parar()


def test_la_cola_del_vad_no_bloquea_al_hilo_de_audio() -> None:
    class VadLento(VadFalso):
        def alimentar(self, bloque: np.ndarray) -> bool:
            time.sleep(0.05)
            return False

    b = Banco(vad=VadLento())
    try:
        b.orq.alternar_clavar()
        t0 = time.perf_counter()
        _bloques(b, 500)  # muchos más de los que caben en la cola
        assert time.perf_counter() - t0 < 0.5  # put_nowait: se descartan, no se espera
        b.orq.alternar_clavar()
        assert b.destino.esperar()
    finally:
        b.parar()


# --- modo consola ------------------------------------------------------------------------


def test_dictar_audio_devuelve_tiempos(banco: Banco) -> None:
    audio = Audio(muestras=np.zeros(SAMPLE_RATE * 3, dtype=np.float32))
    r = banco.orq.dictar_audio(audio, "app_activa")
    assert r["texto"] == "Hola, mundo." and r["entregado"]
    assert r["detalle"] == "pegado en notepad.exe"
    assert r["ms_motor"] >= 0 and r["rtf"] > 0


# --- instancia única -------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="mutex de Windows")
def test_instancia_unica() -> None:
    import subprocess

    from voziris import winapi

    nombre = "Local\\VozirisTest"
    assert winapi.instancia_unica(nombre)
    assert winapi.instancia_unica(nombre)  # el mismo proceso: sigue siendo la primera
    programa = (
        "from voziris import winapi; import sys; "
        f"sys.exit(0 if winapi.instancia_unica({nombre!r}) else 3)"
    )
    otro = subprocess.run([sys.executable, "-c", programa], check=False)
    assert otro.returncode == 3  # el segundo proceso ve el mutex y lo dice


def test_cambiar_destino_durante_la_grabacion(banco: Banco) -> None:
    md = DestinoFalso()
    banco.orq._destinos["markdown"] = md
    assert not banco.orq.cambiar_destino("markdown")  # en reposo no hay nada que cambiar
    banco.orq.empezar(Modo.MANTENER, "app_activa")
    assert banco.orq.cambiar_destino("markdown")
    assert not banco.orq.cambiar_destino("inexistente") and "inexistente" in banco.avisos[-1]
    banco.orq.terminar(1000)
    assert md.esperar()[0][1].destino == "markdown"
    banco.esperar_reposo()
    assert banco.destino.entregas == []


def test_clavar_markdown_y_corte_por_silencio_opcional() -> None:
    b = Banco(vad=VadFalso(corta_en=2))
    md = DestinoFalso()
    b.orq._destinos["markdown"] = md
    try:
        b.orq.corte_por_silencio = False
        b.orq.alternar_clavar("markdown")  # «Dictar al Markdown» o el quinto atajo
        assert b.orq.estado is Estado.GRABANDO and b.vad is not None and b.vad.reinicios == 0
        _bloques(b, 10, 0.005)
        time.sleep(0.05)
        assert b.orq.estado is Estado.GRABANDO  # sin corte por silencio
        b.orq.alternar_clavar("markdown")  # segunda pulsación: cierra
        assert md.esperar()[0][1].destino == "markdown"
        b.esperar_reposo()
        # Clavar a la app y luego clavar al Markdown: redirige, no cierra.
        b.orq.corte_por_silencio = True
        b.orq.alternar_clavar()
        b.orq.alternar_clavar("markdown")
        assert b.orq.estado is Estado.GRABANDO and b.orq._dictado is not None
        assert b.orq._dictado.destino == "markdown"
        _bloques(b, 2, 0.02)
        assert len(md.esperar(2)) == 2
    finally:
        b.parar()
