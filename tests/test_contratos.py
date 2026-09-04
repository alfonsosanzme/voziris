"""Tests de contrato: que cada implementación cumple su protocolo.

Estos tests pasan desde el primer día, con los stubs sin implementar, y su
utilidad es justo esa: si alguien cambia una firma o se le olvida un atributo
de clase, salta aquí y no tres módulos más abajo.

Se comprueba la FORMA, no el comportamiento. Los tests de comportamiento van
en el test de cada módulo, y cada issue del backlog pide los suyos.
"""

from __future__ import annotations

import inspect

from voziris.destinos.app_activa import AppActiva
from voziris.destinos.archivo_md import ArchivoMarkdown
from voziris.destinos.base import Destino
from voziris.motores.api import MotorAPI
from voziris.motores.base import MotorSTT
from voziris.motores.local import MotorLocal
from voziris.proceso.base import PostProceso
from voziris.proceso.diccionario import Diccionario
from voziris.proceso.llm import LimpiezaLLM
from voziris.proceso.sustituciones import Sustituciones


def test_motores_cumplen_el_protocolo() -> None:
    for clase in (MotorLocal, MotorAPI):
        for metodo in ("precalentar", "disponible", "transcribir"):
            assert callable(getattr(clase, metodo)), f"{clase.__name__} sin {metodo}"
        assert hasattr(clase, "requiere_red"), f"{clase.__name__} sin requiere_red"


def test_postprocesos_cumplen_el_protocolo() -> None:
    for clase in (Diccionario, Sustituciones, LimpiezaLLM):
        assert callable(clase.aplicar)
        assert isinstance(clase.nombre, str)
        assert isinstance(clase.requiere_red, bool)


def test_destinos_cumplen_el_protocolo() -> None:
    for clase in (AppActiva, ArchivoMarkdown):
        assert callable(clase.entregar)
        assert isinstance(clase.nombre, str)


def test_firmas_de_transcribir_coinciden() -> None:
    """Las dos implementaciones deben ser intercambiables para el selector."""
    esperada = inspect.signature(MotorSTT.transcribir)
    for clase in (MotorLocal, MotorAPI):
        assert inspect.signature(clase.transcribir) == esperada, clase.__name__


def test_firmas_de_aplicar_coinciden() -> None:
    esperada = inspect.signature(PostProceso.aplicar)
    for clase in (Diccionario, Sustituciones, LimpiezaLLM):
        assert inspect.signature(clase.aplicar) == esperada, clase.__name__


def test_firmas_de_entregar_coinciden() -> None:
    esperada = inspect.signature(Destino.entregar)
    for clase in (AppActiva, ArchivoMarkdown):
        assert inspect.signature(clase.entregar) == esperada, clase.__name__
