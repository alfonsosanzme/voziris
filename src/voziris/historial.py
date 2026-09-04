"""D3 — historial de dictados con reintento.

Marcada como crítica, y con razón: es la red de seguridad de todo lo demás. Si
el pegado falla, si el usuario tenía el foco en la ventana equivocada, si el
destino estaba bloqueado — el texto sigue aquí y se reentrega sin volver a
dictar.

Archivo JSON Lines junto al ejecutable (`historial/dictados.jsonl`), recortado
a `maximo` entradas. JSONL y no una base de datos porque se abre con cualquier
editor si algo va mal, y porque añadir una línea no puede corromper las
anteriores. Una línea ilegible (corte de luz a media escritura) se salta.

`guardar_audio` deja el WAV al lado (`historial/audio/<indice>.wav`). Solo
para depurar: ocupa mucho y es información sensible. Por defecto,
desactivado.

Issue: VOZ-52.
"""

from __future__ import annotations

import json
import logging
import os
import wave
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import numpy as np

from voziris.destinos.base import Destino
from voziris.errores import EntregaFallida
from voziris.tipos import Audio, Contexto, EntradaHistorial, Entrega

log = logging.getLogger(__name__)


class Historial:
    def __init__(
        self,
        ruta: Path,
        maximo: int = 50,
        guardar_audio: bool = False,
        destinos: dict[str, Destino] | None = None,
        contexto: Callable[[str], Contexto] | None = None,
    ) -> None:
        """
        Args:
            ruta: el `dictados.jsonl`. Su carpeta se crea si no existe.
            maximo: entradas que se conservan.
            guardar_audio: escribir el WAV de cada dictado junto al historial.
            destinos: para `reintentar()`: {"app_activa": ..., "markdown": ...}.
            contexto: construye el `Contexto` de una reentrega dado el destino.
        """
        self._ruta = Path(ruta)
        self._maximo = max(1, maximo)
        self._guardar_audio = guardar_audio
        self._destinos = destinos or {}
        self._contexto = contexto
        self._ruta.parent.mkdir(parents=True, exist_ok=True)

    @property
    def ruta(self) -> Path:
        return self._ruta

    # --- escritura ------------------------------------------------------------------

    def registrar(self, entrada: EntradaHistorial, audio: Audio | None = None) -> None:
        """Añade una entrada (le asigna `indice`) y recorta el archivo si hace falta."""
        entradas = self._leer()
        entrada.indice = (max((e.indice for e in entradas), default=0)) + 1
        if self._guardar_audio and audio is not None and len(audio.muestras):
            entrada.audio = self._guardar_wav(entrada.indice, audio)
        with open(self._ruta, "a", encoding="utf-8", newline="\n") as archivo:
            archivo.write(json.dumps(self._a_dict(entrada), ensure_ascii=False) + "\n")
        if len(entradas) + 1 > self._maximo * 2:
            self._reescribir((entradas + [entrada])[-self._maximo :], entradas[: -self._maximo + 1])

    def _guardar_wav(self, indice: int, audio: Audio) -> str:
        carpeta = self._ruta.parent / "audio"
        carpeta.mkdir(exist_ok=True)
        nombre = f"{indice:06d}.wav"
        enteros = (np.clip(audio.muestras, -1, 1) * 32767).astype("<i2")
        with wave.open(str(carpeta / nombre), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(audio.sr)
            w.writeframes(enteros.tobytes())
        return nombre

    # --- lectura --------------------------------------------------------------------

    def ultimas(self, n: int = 10) -> list[EntradaHistorial]:
        """Las n más recientes, de la más nueva a la más vieja. Para el menú de bandeja."""
        return list(reversed(self._leer()))[:n]

    def todas(self) -> list[EntradaHistorial]:
        return self._leer()

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
            entrega = destino.entregar(entrada.texto, self._contexto(entrada.destino))
        except EntregaFallida as fallo:
            entrega = Entrega(ok=False, detalle=str(fallo))
        if entrega.ok and not entrada.entregado:
            entradas = self._leer()
            for otra in entradas:
                if otra.indice == indice:
                    otra.entregado = True
            self._reescribir(entradas, [])
        return entrega

    def borrar(self, indice: int) -> bool:
        """Quita una entrada (y su WAV). Para los dictados con información sensible."""
        entradas = self._leer()
        quedan = [e for e in entradas if e.indice != indice]
        if len(quedan) == len(entradas):
            return False
        self._reescribir(quedan, [e for e in entradas if e.indice == indice])
        return True

    def _reescribir(
        self, entradas: list[EntradaHistorial], borradas: list[EntradaHistorial]
    ) -> None:
        """Escritura atómica: temporal y `os.replace`. Borra los WAV de las que se van."""
        temporal = self._ruta.with_suffix(".jsonl.tmp")
        with open(temporal, "w", encoding="utf-8", newline="\n") as archivo:
            for entrada in entradas:
                archivo.write(json.dumps(self._a_dict(entrada), ensure_ascii=False) + "\n")
        os.replace(temporal, self._ruta)
        for entrada in borradas:
            if entrada.audio:
                wav = self._ruta.parent / "audio" / entrada.audio
                if wav.exists():
                    wav.unlink()
