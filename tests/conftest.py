"""Ajustes comunes de la batería de pruebas.

Lo único que hay aquí es un seguro contra una trampa de Tkinter que mataba la
suite entera: si un objeto `Tk` queda atrapado en un ciclo de referencias,
solo lo libera el recolector de basura, y si el recolector salta en un hilo
que no es el dueño del intérprete Tcl, Tcl entra en pánico («async handler
deleted by the wrong thread») y mata el proceso con un 0x80000003, sin
resumen de tests ni nada. Pasaba en 9 de cada 11 ejecuciones.

Recogiendo al final de cada test, en el hilo principal, la basura de Tk nunca
llega a manos de un hilo de fondo. Ver bugs.python.org/issue39093.
"""

from __future__ import annotations

import gc
from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _recoger_en_el_hilo_principal() -> Iterator[None]:
    yield
    gc.collect()
