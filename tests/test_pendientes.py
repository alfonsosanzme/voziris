"""Tests de VOZ-74: el audio del dictado en disco, su recuperación, y lo que lo rodea."""

from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from voziris.errores import MotorNoDisponible
from voziris.motores.selector import Selector
from voziris.orquestador import Estado, Orquestador
from voziris.pendientes import EXTENSION, Pendientes
from voziris.tipos import SAMPLE_RATE, Audio, EntradaHistorial, Entrega, Modo, Transcripcion
from voziris.ui.bandeja import ETIQUETA_REENTREGAR, AccionesBandeja, Bandeja

# --- el archivo ------------------------------------------------------------------------------


def _grabar(pendientes: Pendientes, segundos: float, valor: float = 0.1) -> Path:
    escritor = pendientes.abrir()
    assert escritor is not None
    bloque = np.full(512, valor, dtype=np.float32)
    for _ in range(int(np.ceil(segundos * SAMPLE_RATE / 512))):
        escritor.escribir(bloque)
    return escritor.cerrar()


def test_escribe_segun_llega_y_se_lee_aunque_nadie_lo_cierre(tmp_path: Path) -> None:
    pendientes = Pendientes(tmp_path / "pendientes")
    escritor = pendientes.abrir()
    assert escritor is not None
    for _ in range(100):  # 3,2 s
        escritor.escribir(np.full(512, 0.25, dtype=np.float32))
    # Sin cerrar: como si el proceso hubiera muerto a media grabación.
    limite = time.monotonic() + 2
    while escritor.ruta.stat().st_size < 100 * 512 * 4 and time.monotonic() < limite:
        time.sleep(0.01)
    assert escritor.ruta.stat().st_size == 100 * 512 * 4
    audio = pendientes.cargar(escritor.ruta)
    assert audio.duracion_s == pytest.approx(3.2) and audio.muestras.dtype == np.float32
    assert float(np.sqrt(np.mean(audio.muestras**2))) == pytest.approx(0.30, abs=0.01)
    escritor.cerrar()


def test_listar_limpiar_y_borrar(tmp_path: Path) -> None:
    pendientes = Pendientes(tmp_path / "p", maximo=2, dias=7)
    assert pendientes.listar() == [] and pendientes.limpiar() == 0
    rutas = [_grabar(pendientes, 2.0) for _ in range(3)]
    roce = _grabar(pendientes, 0.2)  # no llega a un dictado
    viejo = _grabar(pendientes, 2.0)
    hace_un_mes = time.time() - 30 * 86400
    os.utime(roce, (hace_un_mes, hace_un_mes))
    viejo_nuevo = viejo.with_name(f"20200101-000000{EXTENSION}")
    viejo.rename(viejo_nuevo)

    lista = pendientes.listar()
    assert roce not in [p.ruta for p in lista] and len(lista) == 4
    assert lista[-1].ruta == viejo_nuevo and lista[-1].momento == datetime(2020, 1, 1)
    assert "· 0:02" in lista[0].etiqueta()
    assert pendientes.listar(excepto=rutas[0])[0].ruta != rutas[0] or len(lista) == 4

    assert pendientes.limpiar() == 3  # el roce, el de 2020 y el que sobra de dos
    assert len(pendientes.listar()) == 2
    pendientes.borrar(pendientes.listar()[0].ruta)
    assert len(pendientes.listar()) == 1


def test_descartar_borra_el_archivo(tmp_path: Path) -> None:
    pendientes = Pendientes(tmp_path)
    escritor = pendientes.abrir()
    assert escritor is not None
    escritor.escribir(np.zeros(512, dtype=np.float32))
    escritor.descartar()
    assert not escritor.ruta.exists()


# --- el orquestador decide cuándo se borra -----------------------------------------------------


class CapturaConDisco:
    """Como la captura real: deja `ultimo_pendiente` al terminar."""

    oyente_bloques: Any = None

    def __init__(self, pendientes: Pendientes) -> None:
        self._pendientes = pendientes
        self.ultimo_pendiente: Path | None = None

    def empezar_dictado(self) -> None: ...

    def terminar_dictado(self) -> Audio:
        self.ultimo_pendiente = _grabar(self._pendientes, 2.0)
        return Audio(muestras=np.full(SAMPLE_RATE * 2, 0.1, dtype=np.float32))

    def cancelar_dictado(self) -> None: ...


class Motor:
    nombre = "local"
    requiere_red = False

    def __init__(self) -> None:
        self.falla: Exception | None = None
        self.tarda_s = 0.0

    def precalentar(self) -> None: ...

    def disponible(self) -> bool:
        return True

    def transcribir(self, audio: Audio, idioma: str) -> Transcripcion:
        time.sleep(self.tarda_s)
        if self.falla is not None:
            raise self.falla
        return Transcripcion("Hola, mundo.", idioma, "local", 5, audio.duracion_s)


class Destino:
    nombre = "app_activa"

    def __init__(self) -> None:
        self.textos: list[str] = []

    def entregar(self, texto: str, ctx: object) -> Entrega:
        self.textos.append(texto)
        return Entrega(ok=True, detalle="pegado")


class Historial:
    def __init__(self) -> None:
        self.entradas: list[EntradaHistorial] = []

    def registrar(
        self, entrada: EntradaHistorial, audio: Audio | None = None, pendiente: Path | None = None
    ) -> EntradaHistorial:
        entrada.indice = len(self.entradas) + 1
        self.entradas.append(entrada)
        return entrada

    def marcar_entregado(self, indice: int) -> bool:
        for entrada in self.entradas:
            if entrada.indice == indice:
                entrada.entregado = True
                return True
        return False

    def cargar_audio(self, indice: int) -> Audio | None:
        return None

    def cambiar_texto(self, indice: int, texto: str, motor: str) -> bool:
        return False


def _montar(tmp_path: Path) -> tuple[Orquestador, Pendientes, Motor, Destino, Historial, list[str]]:
    pendientes = Pendientes(tmp_path / "pendientes")
    motor, destino, historial, avisos = Motor(), Destino(), Historial(), []
    orq = Orquestador(
        CapturaConDisco(pendientes), motor, {"app_activa": destino}, [],
        al_aviso=avisos.append, historial=historial, pendientes=pendientes,
    )
    orq.arrancar()
    assert orq.motor_listo.wait(3)
    return orq, pendientes, motor, destino, historial, avisos


def _dictar(orq: Orquestador) -> None:
    assert orq.empezar(Modo.MANTENER, "app_activa")
    orq.terminar(1500)
    limite = time.monotonic() + 3
    while orq.estado is Estado.PROCESANDO and time.monotonic() < limite:
        time.sleep(0.01)
    time.sleep(0.05)


def test_entregado_se_borra_el_audio(tmp_path: Path) -> None:
    orq, pendientes, _motor, destino, _historial, _avisos = _montar(tmp_path)
    _dictar(orq)
    orq.parar()
    assert destino.textos == ["Hola, mundo."] and pendientes.listar() == []


def test_si_el_motor_falla_el_audio_se_queda_y_se_recupera_despues(tmp_path: Path) -> None:
    orq, pendientes, motor, destino, historial, avisos = _montar(tmp_path)
    motor.falla = MotorNoDisponible("ni la API ni el local")
    _dictar(orq)
    assert destino.textos == [] and len(pendientes.listar()) == 1
    assert any("Dictados sin transcribir" in a for a in avisos)

    motor.falla = None
    texto = orq.recuperar(pendientes.listar()[0].ruta)
    orq.parar()
    assert texto == "Hola, mundo."
    assert destino.textos == []  # recuperar no pega en ninguna ventana
    assert [e.texto for e in historial.entradas] == ["Hola, mundo."]
    assert historial.entradas[0].entregado is False
    assert pendientes.listar() == []  # a salvo en el historial: el audio ya sobra


def test_cancelar_mientras_procesa_guarda_el_texto_sin_pegarlo(tmp_path: Path) -> None:
    orq, pendientes, motor, destino, historial, _avisos = _montar(tmp_path)
    motor.tarda_s = 0.3
    assert orq.empezar(Modo.MANTENER, "app_activa")
    orq.terminar(1500)
    orq.cancelar()
    time.sleep(0.7)
    orq.parar()
    assert destino.textos == []
    assert [(e.texto, e.entregado) for e in historial.entradas] == [("Hola, mundo.", False)]
    assert pendientes.listar() == []


# --- la API con paciencia -----------------------------------------------------------------------


class MotorLento:
    requiere_red = False

    def __init__(self, nombre: str, tarda_s: float) -> None:
        self.nombre = nombre
        self.tarda_s = tarda_s

    def precalentar(self) -> None: ...

    def disponible(self) -> bool:
        return True

    def transcribir(self, audio: Audio, idioma: str) -> Transcripcion:
        time.sleep(self.tarda_s)
        return Transcripcion(f"de {self.nombre}", idioma, self.nombre, 1, audio.duracion_s)


def test_api_que_no_contesta_a_tiempo_cae_al_local() -> None:
    audio = Audio(muestras=np.zeros(SAMPLE_RATE, dtype=np.float32))
    s = Selector("api", MotorLento("local", 0.0), MotorLento("api:groq", 1.0),
                 paciencia_base_s=0.2)
    t0 = time.perf_counter()
    t = s.transcribir(audio, "es")
    assert time.perf_counter() - t0 < 0.8  # no esperó el segundo entero
    assert t.motor == "local" and any("no contestó" in a for a in t.avisos)
    # Y si contesta a tiempo, es la API.
    rapido = Selector("api", MotorLento("local", 0.0), MotorLento("api:groq", 0.05),
                      paciencia_base_s=0.5)
    assert rapido.transcribir(audio, "es").motor == "api:groq"


# --- el menú --------------------------------------------------------------------------------------


def _acciones(llamadas: list[tuple[str, object]], pendientes: list[Any]) -> AccionesBandeja:
    return AccionesBandeja(
        dictar_ahora=lambda: None, dictar_markdown=lambda: None, alternar_corte=lambda: None,
        abrir_ajustes=lambda: None, cambiar_motor=lambda m: None,
        reintentar=lambda i: llamadas.append(("reintentar", i)),
        borrar_entrada=lambda i: None, salir=lambda: None,
        copiar=lambda i: llamadas.append(("copiar", i)),
        pendientes=lambda: pendientes,
        recuperar=lambda p: llamadas.append(("recuperar", p)),
        borrar_pendiente=lambda p: llamadas.append(("borrar_pendiente", p)),
    )


def test_ultimos_dictados_se_pueden_copiar(tmp_path: Path) -> None:
    llamadas: list[tuple[str, object]] = []
    entrada = EntradaHistorial(
        momento=datetime(2026, 9, 17, 20, 48), texto="No se entregó.", motor="local",
        destino="app_activa", entregado=False, ms_total=1, duracion_audio_s=1.0, indice=7,
    )
    bandeja = Bandeja(_acciones(llamadas, []), ultimas=lambda: [entrada])
    items = {str(i.text): i for i in bandeja.construir_menu().items}
    assert not items["Dictados sin transcribir (0)"].visible
    ultimos = list(items["Últimos dictados"].submenu.items)
    acciones = list(ultimos[0].submenu.items)
    assert [str(a.text) for a in acciones] == [
        "Copiar al portapapeles", ETIQUETA_REENTREGAR, "Borrar del historial",
    ]
    acciones[0](None)
    assert llamadas == [("copiar", 7)]


def test_dictados_sin_transcribir_en_el_menu(tmp_path: Path) -> None:
    pendientes = Pendientes(tmp_path)
    _grabar(pendientes, 4.0)
    llamadas: list[tuple[str, object]] = []
    bandeja = Bandeja(_acciones(llamadas, pendientes.listar()))
    items = {str(i.text): i for i in bandeja.construir_menu().items}
    entrada = items["Dictados sin transcribir (1)"]
    assert entrada.visible
    pendiente = list(entrada.submenu.items)[0]
    assert "· 0:04" in str(pendiente.text)
    transcribir, borrar = list(pendiente.submenu.items)
    assert str(transcribir.text) == "Transcribir y copiar"
    transcribir(None)
    borrar(None)
    assert [ll[0] for ll in llamadas] == ["recuperar", "borrar_pendiente"]


def test_listar_puede_dejar_fuera_varios(tmp_path: Path) -> None:
    """El que se graba y el que se transcribe no son «sin transcribir»."""
    pendientes = Pendientes(tmp_path / "p")
    uno, dos, tres = (_grabar(pendientes, 1.2) for _ in range(3))
    assert {p.ruta for p in pendientes.listar(excepto=[uno, dos])} == {tres}
    assert {p.ruta for p in pendientes.listar(excepto=uno)} == {dos, tres}
