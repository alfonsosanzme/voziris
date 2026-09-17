"""El audio del dictado, en disco mientras se graba: la caja negra.

Hasta ahora el audio vivía solo en memoria. Si la API se quedaba colgada y
el usuario cancelaba, si el motor fallaba o si el proceso moría, lo dicho
se perdía entero. Ahora cada dictado se va escribiendo a
`historial/pendientes/AAAAMMDD-HHMMSS.f32` según llega del micrófono, y el
archivo solo se borra cuando el texto está a salvo (entregado, o guardado en
el historial). Lo que quede ahí es un dictado que no llegó a texto, y se
puede transcribir después desde la bandeja («Dictados sin transcribir»).

Formato: float32 mono a 16 kHz, sin cabecera. A propósito: un WAV a medio
escribir tiene la cabecera mal y hay que repararlo; un archivo crudo se lee
hasta donde llegue. Es el audio tal como sale del micrófono, sin normalizar:
se normaliza al recuperarlo, igual que hace la captura.

Hilos: `escribir()` se llama desde el hilo de audio y solo encola; un hilo
propio escribe y vuelca a disco cada bloque (32 ms), que es la «segmentación»
que hace que un corte pierda como mucho el último instante.

Privacidad: es audio del usuario en su disco, igual que `historial/`. Se
limpia solo: `dias` de antigüedad y `maximo` archivos como mucho.

Issue: VOZ-74.
"""

from __future__ import annotations

import contextlib
import logging
import queue
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from voziris.tipos import SAMPLE_RATE, Audio

log = logging.getLogger(__name__)

EXTENSION = ".f32"
DURACION_MINIMA_S = 1.0
"""Un pendiente más corto que esto no es un dictado: es un roce de tecla."""


@dataclass
class Pendiente:
    ruta: Path
    momento: datetime
    duracion_s: float

    def etiqueta(self) -> str:
        minutos, segundos = divmod(int(self.duracion_s), 60)
        return f"{self.momento:%d/%m %H:%M} · {minutos}:{segundos:02d}"


class EscritorPendiente:
    """Un dictado en curso yendo a disco. Se crea con `Pendientes.abrir()`."""

    def __init__(self, ruta: Path) -> None:
        self.ruta = ruta
        self._cola: queue.Queue[np.ndarray | None] = queue.Queue()
        self._archivo = open(ruta, "wb")  # noqa: SIM115 — vive lo que el dictado
        self._hilo = threading.Thread(target=self._volcar, name="voziris-pendiente", daemon=True)
        self._hilo.start()

    def escribir(self, bloque: np.ndarray) -> None:
        """Desde el hilo de audio: solo encola."""
        self._cola.put_nowait(bloque)

    def _volcar(self) -> None:
        while True:
            bloque = self._cola.get()
            if bloque is None:
                return
            try:
                self._archivo.write(np.ascontiguousarray(bloque, dtype="<f4").tobytes())
                self._archivo.flush()
            except (OSError, ValueError):  # disco lleno o archivo ya cerrado: no tumba el dictado
                return

    def cerrar(self) -> Path:
        """Espera a que esté todo escrito y cierra. Devuelve la ruta."""
        self._cola.put(None)
        self._hilo.join(timeout=2)
        with contextlib.suppress(OSError):
            self._archivo.close()
        return self.ruta

    def descartar(self) -> None:
        self.cerrar()
        with contextlib.suppress(OSError):
            self.ruta.unlink()


class Pendientes:
    def __init__(self, carpeta: Path, maximo: int = 20, dias: int = 7) -> None:
        self._carpeta = Path(carpeta)
        self._maximo = maximo
        self._dias = dias

    @property
    def carpeta(self) -> Path:
        return self._carpeta

    def abrir(self) -> EscritorPendiente | None:
        """Un escritor nuevo, o None si no se puede escribir ahí (no impide dictar)."""
        try:
            self._carpeta.mkdir(parents=True, exist_ok=True)
            nombre = datetime.now().strftime("%Y%m%d-%H%M%S")
            ruta = self._carpeta / f"{nombre}{EXTENSION}"
            n = 2
            while ruta.exists():
                ruta = self._carpeta / f"{nombre}-{n}{EXTENSION}"
                n += 1
            return EscritorPendiente(ruta)
        except OSError as e:
            log.warning("sin copia en disco del dictado: %s", e)
            return None

    def listar(self, excepto: Path | None = None) -> list[Pendiente]:
        """Del más reciente al más antiguo. `excepto`: el que se está grabando ahora."""
        if not self._carpeta.is_dir():
            return []
        salida = []
        for ruta in self._carpeta.glob(f"*{EXTENSION}"):
            if excepto is not None and ruta == excepto:
                continue
            try:
                info = ruta.stat()
            except OSError:
                continue
            duracion = info.st_size / 4 / SAMPLE_RATE
            if duracion < DURACION_MINIMA_S:
                continue
            salida.append(Pendiente(ruta, _momento_de(ruta, info.st_mtime), duracion))
        return sorted(salida, key=lambda p: p.momento, reverse=True)

    def cargar(self, ruta: Path) -> Audio:
        """El audio de un pendiente, normalizado como lo habría dejado la captura."""
        from voziris.audio.captura import normalizar

        crudo = np.fromfile(ruta, dtype="<f4")
        return Audio(muestras=normalizar(crudo.astype(np.float32)))

    def borrar(self, ruta: Path) -> None:
        with contextlib.suppress(OSError):
            Path(ruta).unlink()

    def limpiar(self, excepto: Path | None = None) -> int:
        """Borra los viejos, los sobrantes y los que no llegan a un dictado. Devuelve cuántos."""
        if not self._carpeta.is_dir():
            return 0
        borrados = 0
        limite = datetime.now() - timedelta(days=self._dias)
        validos = {p.ruta for p in self.listar(excepto)}
        for ruta in self._carpeta.glob(f"*{EXTENSION}"):
            if ruta == excepto:
                continue
            # Los demasiado cortos, solo si llevan un rato quietos: uno recién
            # abierto por otro dictado también pesa cero.
            if ruta not in validos and time.time() - ruta.stat().st_mtime > 60:
                self.borrar(ruta)
                borrados += 1
        for i, pendiente in enumerate(self.listar(excepto)):
            if i >= self._maximo or pendiente.momento < limite:
                self.borrar(pendiente.ruta)
                borrados += 1
        if borrados:
            log.info("pendientes: %d archivo(s) de audio antiguos borrados", borrados)
        return borrados


def _momento_de(ruta: Path, mtime: float) -> datetime:
    try:
        return datetime.strptime(ruta.stem[:15], "%Y%m%d-%H%M%S")
    except ValueError:
        return datetime.fromtimestamp(mtime)
