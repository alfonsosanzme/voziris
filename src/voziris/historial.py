"""D3 — historial de dictados con reintento, y la grabación de los últimos.

Marcada como crítica, y con razón: es la red de seguridad de todo lo demás. Si
el pegado falla, si el usuario tenía el foco en la ventana equivocada, si el
destino estaba bloqueado — el texto sigue aquí y se reentrega sin volver a
dictar.

Archivo JSON Lines junto al ejecutable (`historial/dictados.jsonl`), recortado
a `maximo` entradas. JSONL y no una base de datos porque se abre con cualquier
editor si algo va mal, y porque añadir una línea no puede corromper las
anteriores. Una línea ilegible (corte de luz a media escritura) se salta.

El texto se escribe aquí ANTES de pegarlo (VOZ-80). Antes se escribía después,
y un pegado que se colgaba o un proceso que moría a media entrega se llevaba
el dictado entero. Ahora entra sin entregar y `marcar_entregado()` le quita la
marca cuando el pegado va bien.

Con `conservar_audio`, la grabación de cada dictado se queda en
`historial/audio/<indice>.f32` para poder volver a transcribirla desde la
bandeja (VOZ-80). No se copia: el archivo que la captura fue escribiendo
mientras se hablaba (`pendientes/`, VOZ-74) se mueve aquí con un rename. Se
poda sola: la de los `audio_maximo` dictados más recientes, de menos de
`audio_dias` días, y nunca más de `AUDIO_MAXIMO_MB` entre todas. El texto dura
más que el audio: un dictado viejo sigue en el historial, ya sin grabación.
Mismo formato que los pendientes: float32 mono a 16 kHz, crudo del micrófono.

Hilos: el hilo de trabajo escribe, la bandeja lee al abrir el menú, y copiar,
reentregar o volver a transcribir llegan desde otros. Todo pasa por un
cerrojo: sin él, una lectura a mitad de un añadido veía media línea, y un
borrado a la vez que un registro perdía uno de los dos.

Issues: VOZ-52, VOZ-80.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
import time
import wave
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from voziris.destinos.base import Destino
from voziris.errores import EntregaFallida
from voziris.tipos import SAMPLE_RATE, Audio, Contexto, EntradaHistorial, Entrega

log = logging.getLogger(__name__)

EXTENSION_AUDIO = ".f32"
AUDIO_MAXIMO_MB = 1024
"""Tope de toda la grabación conservada junta. Una hora de dictado son 230 MB."""
HUERFANO_S = 60
"""Un archivo de audio que ninguna entrada nombra se borra si lleva esto quieto."""


class Historial:
    def __init__(
        self,
        ruta: Path,
        maximo: int = 50,
        *,
        conservar_audio: bool = False,
        audio_maximo: int = 20,
        audio_dias: int = 7,
        destinos: dict[str, Destino] | None = None,
        contexto: Callable[[str], Contexto] | None = None,
    ) -> None:
        """
        Args:
            ruta: el `dictados.jsonl`. Su carpeta se crea si no existe.
            maximo: entradas que se conservan.
            conservar_audio: quedarse con la grabación de los últimos dictados.
            audio_maximo: de cuántos dictados, como mucho.
            audio_dias: y de cuántos días atrás, como mucho.
            destinos: para `reintentar()`: {"app_activa": ..., "markdown": ...}.
            contexto: construye el `Contexto` de una reentrega dado el destino.
        """
        self._ruta = Path(ruta)
        self._maximo = max(1, maximo)
        self._conservar_audio = conservar_audio
        self._audio_maximo = max(1, audio_maximo)
        self._audio_dias = max(1, audio_dias)
        self._destinos = destinos or {}
        self._contexto = contexto
        self._cerrojo = threading.RLock()
        self._ruta.parent.mkdir(parents=True, exist_ok=True)

    @property
    def ruta(self) -> Path:
        return self._ruta

    @property
    def carpeta_audio(self) -> Path:
        return self._ruta.parent / "audio"

    # --- escritura ------------------------------------------------------------------

    def registrar(
        self,
        entrada: EntradaHistorial,
        audio: Audio | None = None,
        pendiente: Path | None = None,
    ) -> EntradaHistorial:
        """Añade una entrada (le asigna `indice`) y recorta el archivo si hace falta.

        Con `conservar_audio`, la grabación queda enlazada a la entrada:
        `pendiente` (el archivo que se escribió mientras se hablaba) se mueve
        aquí, y si no lo hay se escribe `audio`. Sin `conservar_audio` no se
        toca ninguno de los dos: qué hacer con el pendiente es cosa de quien
        llama.
        """
        with self._cerrojo:
            entradas = self._leer()
            entrada.indice = max((e.indice for e in entradas), default=0) + 1
            if self._conservar_audio:
                entrada.audio = self._guardar_audio(entrada.indice, pendiente, audio)
            with open(self._ruta, "a", encoding="utf-8", newline="\n") as archivo:
                archivo.write(json.dumps(self._a_dict(entrada), ensure_ascii=False) + "\n")
            if len(entradas) + 1 > self._maximo * 2:
                self._reescribir(
                    (entradas + [entrada])[-self._maximo :], entradas[: -self._maximo + 1]
                )
            if entrada.audio:
                self._podar_audio()
        return entrada

    def _guardar_audio(
        self, indice: int, pendiente: Path | None, audio: Audio | None
    ) -> str | None:
        """`audio/<indice>.f32`, o None si no hay grabación o no se puede: el texto manda."""
        nombre = f"{indice:06d}{EXTENSION_AUDIO}"
        destino = self.carpeta_audio / nombre
        try:
            self.carpeta_audio.mkdir(exist_ok=True)
            if pendiente is not None and Path(pendiente).exists():
                os.replace(pendiente, destino)
                return nombre
            if audio is not None and len(audio.muestras) and audio.sr == SAMPLE_RATE:
                np.ascontiguousarray(audio.muestras, dtype="<f4").tofile(destino)
                return nombre
        except OSError as e:
            log.warning("el dictado %d se queda sin grabación guardada: %s", indice, e)
        return None

    def marcar_entregado(self, indice: int) -> bool:
        """El pegado fue bien: quita la marca de «sin entregar»."""
        return self._cambiar(indice, entregado=True)

    def cambiar_texto(self, indice: int, texto: str, motor: str) -> bool:
        """Tras volver a transcribir la grabación: el texto nuevo sustituye al viejo."""
        return self._cambiar(indice, texto=texto, motor=motor)

    def _cambiar(self, indice: int, **campos: Any) -> bool:
        with self._cerrojo:
            entradas = self._leer()
            for entrada in entradas:
                if entrada.indice == indice:
                    for campo, valor in campos.items():
                        setattr(entrada, campo, valor)
                    self._reescribir(entradas, [])
                    return True
        return False

    # --- lectura --------------------------------------------------------------------

    def ultimas(self, n: int = 10) -> list[EntradaHistorial]:
        """Las n más recientes, de la más nueva a la más vieja. Para el menú de bandeja."""
        with self._cerrojo:
            return list(reversed(self._leer()))[:n]

    def todas(self) -> list[EntradaHistorial]:
        with self._cerrojo:
            return self._leer()

    def cargar_audio(self, indice: int) -> Audio | None:
        """La grabación de una entrada, normalizada como la dejaría la captura.

        None si la entrada no existe, no tenía grabación o ya se ha podado.
        """
        entrada = self.buscar(indice)
        if entrada is None or not entrada.audio:
            return None
        ruta = self.carpeta_audio / entrada.audio
        try:
            if ruta.suffix == ".wav":  # los que dejaba `guardar_audio` antes de VOZ-80
                with wave.open(str(ruta), "rb") as w:
                    enteros = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
                crudo = enteros.astype(np.float32) / 32768.0
            else:
                crudo = np.fromfile(ruta, dtype="<f4").astype(np.float32)
        except (OSError, wave.Error, EOFError):
            return None
        if not len(crudo):
            return None
        from voziris.audio.captura import normalizar

        return Audio(muestras=normalizar(crudo))

    def _leer(self) -> list[EntradaHistorial]:
        if not self._ruta.exists():
            return []
        entradas: list[EntradaHistorial] = []
        ilegibles = 0
        with open(self._ruta, encoding="utf-8", errors="replace") as archivo:
            for linea in archivo:
                linea = linea.strip()
                if not linea:
                    continue
                try:
                    entradas.append(self._de_dict(json.loads(linea)))
                except (ValueError, KeyError, TypeError):
                    ilegibles += 1
        if ilegibles:
            log.warning("%d línea(s) ilegible(s) en %s: se ignoran", ilegibles, self._ruta.name)
        return entradas

    @staticmethod
    def _a_dict(entrada: EntradaHistorial) -> dict[str, object]:
        datos = asdict(entrada)
        datos["momento"] = entrada.momento.isoformat(timespec="seconds")
        return datos

    @staticmethod
    def _de_dict(datos: dict[str, object]) -> EntradaHistorial:
        return EntradaHistorial(
            momento=datetime.fromisoformat(str(datos["momento"])),
            texto=str(datos["texto"]),
            motor=str(datos.get("motor", "")),
            destino=str(datos.get("destino", "app_activa")),
            entregado=bool(datos.get("entregado", False)),
            ms_total=int(datos.get("ms_total", 0)),  # type: ignore[call-overload]
            duracion_audio_s=float(datos.get("duracion_audio_s", 0.0)),  # type: ignore[arg-type]
            indice=int(datos.get("indice", 0)),  # type: ignore[call-overload]
            audio=str(datos["audio"]) if datos.get("audio") else None,
        )

    # --- reintento y borrado --------------------------------------------------------------

    def buscar(self, indice: int) -> EntradaHistorial | None:
        with self._cerrojo:
            for entrada in self._leer():
                if entrada.indice == indice:
                    return entrada
        return None

    def reintentar(self, indice: int) -> Entrega:
        """Vuelve a entregar una entrada al destino que tenía.

        Reentrega el texto ya procesado: no se vuelve a transcribir ni a pasar
        por el LLM. Es un reintento de la entrega, no del dictado.
        """
        entrada = self.buscar(indice)
        if entrada is None:
            return Entrega(ok=False, detalle=f"no hay ninguna entrada {indice} en el historial")
        destino = self._destinos.get(entrada.destino)
        if destino is None or self._contexto is None:
            return Entrega(ok=False, detalle=f"el destino «{entrada.destino}» no está disponible")
        try:
            # Sin el cerrojo: pegar tarda, y el menú tiene que poder leer mientras.
            entrega = destino.entregar(entrada.texto, self._contexto(entrada.destino))
        except EntregaFallida as fallo:
            entrega = Entrega(ok=False, detalle=str(fallo))
        if entrega.ok and not entrada.entregado:
            self.marcar_entregado(indice)
        return entrega

    def borrar(self, indice: int) -> bool:
        """Quita una entrada (y su grabación). Para los dictados con información sensible."""
        with self._cerrojo:
            entradas = self._leer()
            quedan = [e for e in entradas if e.indice != indice]
            if len(quedan) == len(entradas):
                return False
            self._reescribir(quedan, [e for e in entradas if e.indice == indice])
        return True

    def limpiar_audio(self) -> int:
        """Al arrancar: poda la grabación sobrante y los archivos que nadie nombra."""
        with self._cerrojo:
            return self._podar_audio()

    def _podar_audio(self) -> int:
        """Deja la grabación de los `audio_maximo` dictados más nuevos y de menos de
        `audio_dias` días, sin pasar de `AUDIO_MAXIMO_MB`. Devuelve cuántas quita.

        Con el cerrojo tomado. La más reciente no se quita nunca, aunque sola
        pase del tope: es la que se acaba de dictar.
        """
        entradas = self._leer()
        limite = datetime.now() - timedelta(days=self._audio_dias)
        tope = AUDIO_MAXIMO_MB * 2**20
        ocupado = 0
        quitadas = 0
        for i, entrada in enumerate(e for e in reversed(entradas) if e.audio):
            assert entrada.audio is not None
            ruta = self.carpeta_audio / entrada.audio
            try:
                peso = ruta.stat().st_size
            except OSError:
                peso = -1  # ya no está: la entrada deja de nombrarla
            sobra = peso < 0 or (
                i > 0
                and (i >= self._audio_maximo or entrada.momento < limite or ocupado + peso > tope)
            )
            if sobra:
                with contextlib.suppress(OSError):
                    ruta.unlink()
                entrada.audio = None
                quitadas += 1
            else:
                ocupado += peso
        if quitadas:
            self._reescribir(entradas, [])
        huerfanos = self._borrar_huerfanos({e.audio for e in entradas if e.audio})
        if quitadas or huerfanos:
            log.info("historial: grabación de %d dictado(s) podada, %d archivo(s) sueltos",
                     quitadas, huerfanos)
        return quitadas

    def _borrar_huerfanos(self, nombrados: set[str]) -> int:
        if not self.carpeta_audio.is_dir():
            return 0
        borrados = 0
        for ruta in self.carpeta_audio.iterdir():
            if ruta.suffix not in (EXTENSION_AUDIO, ".wav") or ruta.name in nombrados:
                continue
            with contextlib.suppress(OSError):
                if time.time() - ruta.stat().st_mtime > HUERFANO_S:
                    ruta.unlink()
                    borrados += 1
        return borrados

    def _reescribir(
        self, entradas: list[EntradaHistorial], borradas: list[EntradaHistorial]
    ) -> None:
        """Escritura atómica: temporal y `os.replace`. Borra la grabación de las que se van."""
        temporal = self._ruta.with_suffix(".jsonl.tmp")
        with open(temporal, "w", encoding="utf-8", newline="\n") as archivo:
            for entrada in entradas:
                archivo.write(json.dumps(self._a_dict(entrada), ensure_ascii=False) + "\n")
        os.replace(temporal, self._ruta)
        for entrada in borradas:
            if entrada.audio:
                with contextlib.suppress(OSError):
                    (self.carpeta_audio / entrada.audio).unlink()
