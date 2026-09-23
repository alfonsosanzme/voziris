"""D3 — historial de dictados con reintento, y la grabación de los últimos.

Marcada como crítica, y con razón: es la red de seguridad de todo lo demás. Si
el pegado falla, si el usuario tenía el foco en la ventana equivocada, si el
destino estaba bloqueado — el texto sigue aquí y se reentrega sin volver a
dictar.

Archivo JSON Lines junto al ejecutable (`historial/dictados.jsonl`), recortado
a `maximo` entradas. JSONL y no una base de datos porque se abre con cualquier
editor si algo va mal, y porque añadir una línea no puede corromper las
anteriores. Una línea ilegible (corte de luz a media escritura) se salta al
leer, y al reescribir el archivo se aparta a `dictados.ilegibles.jsonl` en
vez de perderse: puede ser la única copia de un dictado.

El texto se escribe aquí ANTES de pegarlo (VOZ-80). Antes se escribía después,
y un pegado que se colgaba o un proceso que moría a media entrega se llevaba
el dictado entero. Ahora entra sin entregar y `marcar_entregado()` le quita la
marca cuando el pegado va bien.

Con `conservar_audio`, la grabación de cada dictado se queda en
`historial/audio/<indice>.f32` para poder volver a transcribirla desde la
bandeja (VOZ-80). No se copia: el archivo que la captura fue escribiendo
mientras se hablaba (`pendientes/`, VOZ-74) se mueve aquí con un rename, y si
ese archivo quedó corto o no se puede mover, se escribe el audio que hay en
memoria. Si la línea del texto no llega a escribirse, el archivo vuelve a su
sitio: nunca se queda una grabación sin el texto que la nombra.

Se poda sola (`podar_audio()`, después de pegar, no antes): la de los
`audio_maximo` dictados más recientes, de menos de `audio_dias` días, y nunca
más de `AUDIO_MAXIMO_MB` entre todas. El texto dura más que el audio: un
dictado viejo sigue en el historial, ya sin grabación. Mismo formato que los
pendientes: float32 mono a 16 kHz.

Volver a transcribir guarda el texto que había en `texto_anterior`: el
resultado nuevo puede ser peor (la API sin cuota, el motor local de respaldo)
y no debe poder machacar uno bueno sin vuelta atrás.

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
import math
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
COMPLETO = 0.99
"""Un pendiente que no llega a esto del audio en memoria se quedó corto al cerrarse."""


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
        self._ilegibles: list[str] = []
        """Las líneas que no se pudieron leer en la última lectura, tal cual."""
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
        aquí, y si no lo hay o quedó corto se escribe `audio`. Sin
        `conservar_audio` no se toca ninguno de los dos: qué hacer con el
        pendiente es cosa de quien llama.

        Va antes de pegar, así que hace lo mínimo: sin podar (`podar_audio()`).
        Si la línea no se puede escribir, lanza, y el pendiente vuelve a su
        sitio para que no se pierda ni el audio.
        """
        with self._cerrojo:
            entradas = self._leer()
            entrada.indice = max((e.indice for e in entradas), default=0) + 1
            movido: Path | None = None
            if self._conservar_audio:
                entrada.audio, movido = self._guardar_audio(entrada.indice, pendiente, audio)
            try:
                self._anadir(entrada)
            except BaseException:
                self._deshacer_audio(entrada, movido)
                raise
            if len(entradas) + 1 > self._maximo * 2:
                quedan = (entradas + [entrada])[-self._maximo :]
                indices = {e.indice for e in quedan}
                self._reescribir(quedan, [e for e in entradas if e.indice not in indices])
        return entrada

    def _anadir(self, entrada: EntradaHistorial) -> None:
        """Una línea al final. Si la anterior quedó sin salto (corte de luz), se lo pone."""
        salto = ""
        with contextlib.suppress(OSError), open(self._ruta, "rb") as archivo:
            archivo.seek(0, os.SEEK_END)
            if archivo.tell():
                archivo.seek(-1, os.SEEK_END)
                salto = "" if archivo.read(1) == b"\n" else "\n"
        linea = salto + json.dumps(self._a_dict(entrada), ensure_ascii=False) + "\n"
        with open(self._ruta, "a", encoding="utf-8", newline="\n") as archivo:
            archivo.write(linea)

    def _guardar_audio(
        self, indice: int, pendiente: Path | None, audio: Audio | None
    ) -> tuple[str | None, Path | None]:
        """Deja la grabación en `audio/<indice>.f32`.

        Devuelve (nombre, pendiente movido): el nombre es None si no hay
        grabación o no se puede guardar, y el texto sigue adelante igual.
        """
        nombre = f"{indice:06d}{EXTENSION_AUDIO}"
        destino = self.carpeta_audio / nombre
        en_memoria = audio is not None and len(audio.muestras) and audio.sr == SAMPLE_RATE
        try:
            self.carpeta_audio.mkdir(exist_ok=True)
        except OSError as e:
            log.warning("el dictado %d se queda sin grabación guardada: %s", indice, e)
            return None, None
        if pendiente is not None and Path(pendiente).exists():
            try:
                esperado = len(audio.muestras) * 4 if audio is not None and en_memoria else 0
                if Path(pendiente).stat().st_size >= esperado * COMPLETO:
                    os.replace(pendiente, destino)
                    return nombre, Path(pendiente)
                log.warning("el audio en disco del dictado %d quedó corto: se usa el de memoria",
                            indice)
            except OSError as e:
                log.warning("no se pudo mover el audio del dictado %d (%s): se usa el de memoria",
                            indice, e)
        if en_memoria:
            assert audio is not None
            try:
                np.ascontiguousarray(audio.muestras, dtype="<f4").tofile(destino)
                return nombre, None
            except OSError as e:
                log.warning("el dictado %d se queda sin grabación guardada: %s", indice, e)
        return None, None

    def _deshacer_audio(self, entrada: EntradaHistorial, movido: Path | None) -> None:
        """La línea no se escribió: la grabación no se puede quedar huérfana."""
        if entrada.audio:
            destino = self.carpeta_audio / entrada.audio
            with contextlib.suppress(OSError):
                if movido is not None:
                    os.replace(destino, movido)  # vuelve a «Dictados sin transcribir»
                else:
                    destino.unlink()
        entrada.audio = None

    def marcar_entregado(self, indice: int) -> bool:
        """El pegado fue bien: quita la marca de «sin entregar»."""
        return self._cambiar(indice, None, entregado=True)

    def cambiar_texto(
        self, indice: int, texto: str, motor: str, momento: datetime | None = None
    ) -> bool:
        """Tras volver a transcribir: el texto nuevo pasa delante y el de antes se guarda.

        Con `momento`, solo si la entrada es la misma que se cargó. Los índices
        se reutilizan (un borrado del más nuevo deja su número libre), y sin
        esto una retranscripción lenta acababa escrita encima de otro dictado.
        """
        def cambiar(entrada: EntradaHistorial) -> None:
            entrada.texto_anterior = entrada.texto
            entrada.texto = texto
            entrada.motor = motor

        return self._cambiar(indice, momento, cambiar)

    def _cambiar(
        self,
        indice: int,
        momento: datetime | None,
        cambio: Callable[[EntradaHistorial], None] | None = None,
        **campos: Any,
    ) -> bool:
        with self._cerrojo:
            entradas = self._leer()
            for entrada in entradas:
                if entrada.indice != indice:
                    continue
                if momento is not None and entrada.momento != momento:
                    return False
                if cambio is not None:
                    cambio(entrada)
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
        self._ilegibles = []
        if not self._ruta.exists():
            return []
        entradas: list[EntradaHistorial] = []
        with open(self._ruta, encoding="utf-8", errors="replace") as archivo:
            for linea in archivo:
                linea = linea.strip()
                if not linea.strip("\x00"):  # vacía, o la cola de ceros de un corte de luz
                    continue
                try:
                    entradas.append(self._de_dict(json.loads(linea)))
                except (ValueError, KeyError, TypeError):
                    self._ilegibles.append(linea)
        if self._ilegibles:
            log.warning("%d línea(s) ilegible(s) en %s: se ignoran", len(self._ilegibles),
                        self._ruta.name)
        return entradas

    @staticmethod
    def _a_dict(entrada: EntradaHistorial) -> dict[str, object]:
        datos = asdict(entrada)
        datos["momento"] = entrada.momento.isoformat(timespec="seconds")
        return datos

    @staticmethod
    def _de_dict(datos: dict[str, object]) -> EntradaHistorial:
        duracion = float(datos.get("duracion_audio_s", 0.0))  # type: ignore[arg-type]
        return EntradaHistorial(
            momento=datetime.fromisoformat(str(datos["momento"])),
            texto=str(datos["texto"]),
            motor=str(datos.get("motor", "")),
            destino=str(datos.get("destino", "app_activa")),
            entregado=bool(datos.get("entregado", False)),
            ms_total=int(datos.get("ms_total", 0)),  # type: ignore[call-overload]
            duracion_audio_s=duracion if math.isfinite(duracion) else 0.0,
            indice=int(datos.get("indice", 0)),  # type: ignore[call-overload]
            audio=str(datos["audio"]) if datos.get("audio") else None,
            texto_anterior=(
                str(datos["texto_anterior"]) if datos.get("texto_anterior") else None
            ),
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
            self._cambiar(indice, entrada.momento, entregado=True)
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

    # --- poda de la grabación ----------------------------------------------------------------

    def podar_audio(self) -> int:
        """Tras cada dictado, ya pegado: quita la grabación que sobra. Devuelve cuántas."""
        with self._cerrojo:
            return self._podar_audio()

    def limpiar_audio(self) -> int:
        """Al arrancar: poda, y borra los archivos que nadie nombra.

        Con `conservar_audio` apagado se borra toda la grabación guardada: quien
        lo apaga no quiere que siga ahí siete días más.
        """
        with self._cerrojo:
            if not self._conservar_audio:
                return self._quitar_audio(lambda _i, _e, _p: True, huerfanos_s=0)
            return self._podar_audio()

    def _podar_audio(self) -> int:
        """Con el cerrojo tomado. La más reciente no se quita nunca, aunque sola pase
        del tope o de los días: es la que se acaba de dictar."""
        limite = datetime.now() - timedelta(days=self._audio_dias)
        tope = AUDIO_MAXIMO_MB * 2**20
        ocupado = [0]

        def sobra(i: int, entrada: EntradaHistorial, peso: int) -> bool:
            if i > 0 and (
                i >= self._audio_maximo or entrada.momento < limite or ocupado[0] + peso > tope
            ):
                return True
            ocupado[0] += peso
            return False

        return self._quitar_audio(sobra, huerfanos_s=HUERFANO_S)

    def _quitar_audio(
        self, sobra: Callable[[int, EntradaHistorial, int], bool], huerfanos_s: float
    ) -> int:
        """Recorre las entradas con grabación, de la más nueva a la más vieja, y quita
        las que `sobra(i, entrada, peso)` diga. Luego, los archivos sin entrada."""
        entradas = self._leer()
        quitadas = 0
        for i, entrada in enumerate(e for e in reversed(entradas) if e.audio):
            assert entrada.audio is not None
            ruta = self.carpeta_audio / entrada.audio
            try:
                peso = ruta.stat().st_size
            except OSError:
                peso = -1  # ya no está: la entrada deja de nombrarla
            if peso < 0 or sobra(i, entrada, peso):
                with contextlib.suppress(OSError):
                    ruta.unlink()
                entrada.audio = None
                quitadas += 1
        if quitadas:
            self._reescribir(entradas, [])
        huerfanos = self._borrar_huerfanos({e.audio for e in entradas if e.audio}, huerfanos_s)
        if quitadas or huerfanos:
            log.info("historial: grabación de %d dictado(s) quitada, %d archivo(s) sueltos",
                     quitadas, huerfanos)
        return quitadas

    def _borrar_huerfanos(self, nombrados: set[str], quieto_s: float) -> int:
        if not self.carpeta_audio.is_dir():
            return 0
        borrados = 0
        for ruta in self.carpeta_audio.iterdir():
            if ruta.suffix not in (EXTENSION_AUDIO, ".wav") or ruta.name in nombrados:
                continue
            with contextlib.suppress(OSError):
                if time.time() - ruta.stat().st_mtime >= quieto_s:
                    ruta.unlink()
                    borrados += 1
        return borrados

    def _reescribir(
        self, entradas: list[EntradaHistorial], borradas: list[EntradaHistorial]
    ) -> None:
        """Escritura atómica: temporal y `os.replace`. Borra la grabación de las que se van.

        Las líneas ilegibles de la última lectura no se tiran: se apartan a
        `dictados.ilegibles.jsonl`, porque pueden ser la única copia de un
        dictado y un editor a veces las arregla.
        """
        sin_apartar: list[str] = []
        if self._ilegibles:
            apartado = self._ruta.with_name(self._ruta.stem + ".ilegibles.jsonl")
            try:
                with open(apartado, "a", encoding="utf-8", newline="\n") as archivo:
                    archivo.write("\n".join(self._ilegibles) + "\n")
            except OSError:
                sin_apartar = self._ilegibles  # si no se pueden apartar, se quedan donde estaban
            self._ilegibles = []
        temporal = self._ruta.with_suffix(".jsonl.tmp")
        with open(temporal, "w", encoding="utf-8", newline="\n") as archivo:
            for linea in sin_apartar:
                archivo.write(linea + "\n")
            for entrada in entradas:
                archivo.write(json.dumps(self._a_dict(entrada), ensure_ascii=False) + "\n")
            archivo.flush()
            os.fsync(archivo.fileno())
        os.replace(temporal, self._ruta)
        for entrada in borradas:
            if entrada.audio:
                with contextlib.suppress(OSError):
                    (self.carpeta_audio / entrada.audio).unlink()
