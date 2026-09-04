#!/usr/bin/env python3
"""Banco de pruebas del hito 0: ¿aguanta el motor local en ESTE portátil?

Los RTF que circulan (0,033 para Parakeet, 0,13 para Whisper small) están
medidos en un i7-12700KF de SOBREMESA. En un portátil sin GPU van a ser
peores, y no sabemos cuánto. Esta es la única decisión del proyecto que no se
puede tomar leyendo: hay que medirla.

Uso:

    # 1. Grabar 10 dictados reales, de 10-20 s, en español
    python tools/banco_h0.py grabar --n 10 --segundos 15

    # 2. Medir los dos motores sobre esas grabaciones
    pip install -e ".[banco]"
    python tools/banco_h0.py medir

Genera `resultados-h0.md` con RTF por muestra, mediana, RAM y el texto de cada
transcripción para comparar la calidad a ojo.

Si existe `banco/referencias.md` con bloques `# N` seguidos del texto real de
la muestra N, calcula además el WER de cada motor (palabras, sin puntuación ni
mayúsculas) y una tasa de error que sí cuenta la puntuación, para ver quién
puntúa mejor.

El modelo de Parakeet se descarga a `./modelos/`, la misma carpeta que usará
la aplicación, en int8: es lo que mide la especificación y lo que se va a
distribuir. Sin `quantization="int8"`, onnx-asr descargaría el fp32.

Criterio de decisión (definido antes de medir, para no racionalizar después):

    RTF mediano <= 0,10  →  Parakeet vale. Un dictado de 15 s se resuelve en
                            1,5 s. Adelante con B1 según la especificación.
    0,10 < RTF <= 0,25   →  Zona incómoda. Mirar la calidad del texto y
                            plantearse arrancar en modo API por defecto, con
                            el local como respaldo sin red.
    RTF > 0,25           →  No sirve para dictado interactivo. Pasar a Whisper
                            small int8, o dejar el local solo para offline.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
import unicodedata
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
CARPETA = RAIZ / "banco"
MODELOS = RAIZ / "modelos"
REFERENCIAS = CARPETA / "referencias.md"
SR = 16_000


def grabar(n: int, segundos: int) -> None:
    """Graba n muestras de audio con el micrófono predeterminado."""
    import sounddevice as sd
    import soundfile as sf

    CARPETA.mkdir(exist_ok=True)
    print(f"Voy a grabar {n} muestras de {segundos} s en {CARPETA}")
    print("Dicta con normalidad, como usarías la aplicación. En español.\n")

    for i in range(1, n + 1):
        input(f"[{i}/{n}] Enter para empezar a grabar…")
        print("  grabando…", end="", flush=True)
        audio = sd.rec(int(segundos * SR), samplerate=SR, channels=1, dtype="float32")
        sd.wait()
        destino = CARPETA / f"muestra-{i:02d}.wav"
        sf.write(destino, audio, SR)
        print(f" hecho → {destino.name}")

    print(f"\nListo. Ahora: python {Path(__file__).name} medir")


def _muestras() -> list[Path]:
    if not CARPETA.exists():
        sys.exit(f"No existe {CARPETA}. Ejecuta primero: {Path(__file__).name} grabar")
    wavs = sorted(CARPETA.glob("*.wav"))
    if not wavs:
        sys.exit(f"No hay .wav en {CARPETA}.")
    return wavs


def _duracion(wav: Path) -> float:
    import soundfile as sf

    info = sf.info(str(wav))
    return float(info.frames) / info.samplerate


# La normalización es la MISMA que aplica la aplicación en cada dictado
# (`Captura.terminar_dictado()`); la medición que justifica RMS_OBJETIVO está
# en el docstring de esa constante y en docs/H0.md.
from voziris.audio.captura import normalizar  # noqa: E402


def _cargar(wav: Path):
    """Devuelve el audio como float32 mono a 16 kHz, sin tratar.

    Se pasa el array al motor en vez de la ruta para que el RTF mida solo la
    inferencia, igual que hará `MotorLocal.transcribir()`.
    """
    import numpy as np
    import soundfile as sf

    datos, sr = sf.read(str(wav), dtype="float32", always_2d=True)
    if sr != SR:
        sys.exit(f"{wav.name} está a {sr} Hz; el banco espera {SR} Hz.")
    return np.ascontiguousarray(datos.mean(axis=1))


# --- WER -------------------------------------------------------------------


def _normalizar(texto: str, con_puntuacion: bool) -> list[str]:
    texto = unicodedata.normalize("NFC", texto).lower().replace("\n", " ")
    if con_puntuacion:
        # Separa la puntuación como fichas propias para que cuente en la distancia.
        texto = re.sub(r"([.,;:!?¿¡«»\"()—–-])", r" \1 ", texto)
    else:
        texto = re.sub(r"[^\w\s]", " ", texto)
    return texto.split()


def _distancia(a: list[str], b: list[str]) -> int:
    """Distancia de Levenshtein entre listas de fichas."""
    fila = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        anterior, fila[0] = fila[0], i
        for j, y in enumerate(b, 1):
            actual = fila[j]
            fila[j] = min(fila[j] + 1, fila[j - 1] + 1, anterior + (x != y))
            anterior = actual
    return fila[-1]


def wer(referencia: str, hipotesis: str, con_puntuacion: bool = False) -> float:
    ref = _normalizar(referencia, con_puntuacion)
    hip = _normalizar(hipotesis, con_puntuacion)
    if not ref:
        return 0.0
    return _distancia(ref, hip) / len(ref)


def _referencias() -> dict[str, str]:
    """{"muestra-01.wav": "texto real", ...} a partir de banco/referencias.md."""
    if not REFERENCIAS.exists():
        return {}
    bloques = re.split(r"^#\s*(\d+)\s*$", REFERENCIAS.read_text(encoding="utf-8"), flags=re.M)
    # re.split deja [prefacio, num, texto, num, texto, ...]
    return {
        f"muestra-{int(num):02d}.wav": texto.strip()
        for num, texto in zip(bloques[1::2], bloques[2::2], strict=True)
        if texto.strip()
    }


def _ram_mb() -> float:
    import psutil

    return psutil.Process().memory_info().rss / 1024 / 1024


def medir_parakeet(wavs: list[Path], normalizado: bool = True) -> dict:
    """Parakeet TDT 0.6B v3 en ONNX int8, vía onnx-asr.

    Con `normalizado`, el audio pasa por `normalizar()` antes del motor, como
    en la aplicación. Sin él se mide el audio crudo, para ver cuánto aporta.
    """
    import onnx_asr

    # Comportamiento de onnx-asr 0.12 (resolver.py), verificado leyendo el código:
    # si la carpeta `path` EXISTE, pasa a modo offline y solo busca archivos en
    # ella; si NO existe, descarga el modelo ahí con snapshot_download. Por eso
    # no se crea la carpeta antes. Una descarga interrumpida deja la carpeta
    # creada e incompleta, y la carga siguiente falla con ModelFileNotFoundError:
    # VOZ-11 tiene que detectarlo y borrar la carpeta para volver a descargar.
    carpeta = MODELOS / "parakeet-tdt-0.6b-v3"
    print(f"Cargando Parakeet TDT v3 int8 desde {carpeta} (la primera vez descarga ~680 MB)…")
    ram_antes = _ram_mb()
    t0 = time.perf_counter()
    modelo = onnx_asr.load_model("nemo-parakeet-tdt-0.6b-v3", path=carpeta, quantization="int8")
    carga_s = time.perf_counter() - t0
    print(f"  cargado en {carga_s:.1f} s")

    # Primera pasada en vacío: la inferencia inicial siempre es más lenta y
    # falsearía la mediana.
    modelo.recognize(_cargar(wavs[0]), sample_rate=SR)

    filas = []
    for wav in wavs:
        dur = _duracion(wav)
        audio = _cargar(wav)
        if normalizado:
            audio = normalizar(audio)
        t0 = time.perf_counter()
        texto = modelo.recognize(audio, sample_rate=SR)
        proceso = time.perf_counter() - t0
        filas.append({
            "muestra": wav.name,
            "duracion_s": round(dur, 2),
            "proceso_s": round(proceso, 3),
            "rtf": round(proceso / dur, 4),
            "texto": texto if isinstance(texto, str) else str(texto),
        })
        print(f"  {wav.name}: RTF {filas[-1]['rtf']:.3f}")

    return {
        "motor": "parakeet-tdt-0.6b-v3-int8" + ("" if normalizado else " (audio crudo)"),
        "carga_s": round(carga_s, 1),
        "ram_mb": round(_ram_mb() - ram_antes, 1),
        "filas": filas,
    }


def medir_whisper(wavs: list[Path]) -> dict:
    """Whisper small int8, vía faster-whisper. El plan B."""
    from faster_whisper import WhisperModel

    print("Cargando Whisper small int8…")
    ram_antes = _ram_mb()
    t0 = time.perf_counter()
    modelo = WhisperModel("small", device="cpu", compute_type="int8")
    carga_s = time.perf_counter() - t0
    print(f"  cargado en {carga_s:.1f} s")

    list(modelo.transcribe(_cargar(wavs[0]), language="es")[0])  # calentamiento

    filas = []
    for wav in wavs:
        dur = _duracion(wav)
        audio = normalizar(_cargar(wav))
        t0 = time.perf_counter()
        segmentos, _ = modelo.transcribe(audio, language="es")
        texto = "".join(s.text for s in segmentos).strip()
        proceso = time.perf_counter() - t0
        filas.append({
            "muestra": wav.name,
            "duracion_s": round(dur, 2),
            "proceso_s": round(proceso, 3),
            "rtf": round(proceso / dur, 4),
            "texto": texto,
        })
        print(f"  {wav.name}: RTF {filas[-1]['rtf']:.3f}")

    return {
        "motor": "whisper-small-int8",
        "carga_s": round(carga_s, 1),
        "ram_mb": round(_ram_mb() - ram_antes, 1),
        "filas": filas,
    }


def _veredicto(rtf: float) -> str:
    if rtf <= 0.10:
        return "**Vale.** Adelante con Parakeet como motor local (B1)."
    if rtf <= 0.25:
        return (
            "**Zona incómoda.** Revisar la calidad del texto y valorar arrancar "
            "en modo API por defecto, con el local solo como respaldo sin red."
        )
    return "**No sirve** para dictado interactivo. Pasar a Whisper small int8 como local."


def _anotar_wer(resultados: list[dict], referencias: dict[str, str]) -> None:
    """Añade wer y wer_punt a cada fila que tenga referencia."""
    for r in resultados:
        for f in r["filas"]:
            ref = referencias.get(f["muestra"])
            if ref is None:
                continue
            f["wer"] = round(wer(ref, f["texto"]), 4)
            f["wer_punt"] = round(wer(ref, f["texto"], con_puntuacion=True), 4)


def informe(resultados: list[dict], referencias: dict[str, str]) -> str:
    import os
    import platform

    hay_wer = bool(referencias)
    lineas = [
        "# Resultados del hito 0",
        "",
        f"- Equipo: {platform.processor() or 'desconocido'} · {os.cpu_count()} hilos",
        f"- Sistema: {platform.platform()}",
        f"- Python: {platform.python_version()}",
        f"- Fecha: {time.strftime('%Y-%m-%d %H:%M')}",
        f"- Muestras: {len(resultados[0]['filas'])}"
        + (f", {len(referencias)} con transcripción de referencia" if hay_wer else ""),
        "",
        "## Resumen",
        "",
    ]
    cab = "| Motor | RTF mediano | RTF peor | Carga (s) | RAM (MB) |"
    sep = "|---|---|---|---|---|"
    if hay_wer:
        cab += " WER medio | WER con puntuación |"
        sep += "---|---|"
    lineas += [cab, sep]
    for r in resultados:
        rtfs = [f["rtf"] for f in r["filas"]]
        fila = (
            f"| {r['motor']} | {statistics.median(rtfs):.3f} | {max(rtfs):.3f} "
            f"| {r['carga_s']} | {r['ram_mb']} |"
        )
        if hay_wer:
            wers = [f["wer"] for f in r["filas"] if "wer" in f]
            werp = [f["wer_punt"] for f in r["filas"] if "wer_punt" in f]
            fila += (
                f" {statistics.mean(wers) * 100:.1f} % | {statistics.mean(werp) * 100:.1f} % |"
                if wers
                else " — | — |"
            )
        lineas.append(fila)

    principal = next((r for r in resultados if "parakeet" in r["motor"]), resultados[0])
    mediana = statistics.median([f["rtf"] for f in principal["filas"]])
    lineas += [
        "",
        f"RTF mediano de {principal['motor']}: **{mediana:.3f}** "
        f"→ un dictado de 15 s tarda ~{mediana * 15:.1f} s.",
        "",
        f"Veredicto: {_veredicto(mediana)}",
        "",
        "## Transcripciones",
        "",
        "Compara la calidad a ojo, sobre todo con nombres propios y jerga técnica.",
        "",
    ]
    if hay_wer:
        lineas += [
            "WER: errores de palabra sobre el texto de referencia, sin contar puntuación "
            "ni mayúsculas. «Con puntuación»: cada signo cuenta como una ficha más.",
            "",
        ]
    for r in resultados:
        lineas += [f"### {r['motor']}", ""]
        for f in r["filas"]:
            etiqueta = f"**{f['muestra']}** · RTF {f['rtf']:.3f}"
            if "wer" in f:
                etiqueta += (
                    f" · WER {f['wer'] * 100:.1f} %"
                    f" · con puntuación {f['wer_punt'] * 100:.1f} %"
                )
            lineas += [etiqueta, "", f"> {f['texto']}", ""]

    if hay_wer:
        lineas += ["### Referencias", ""]
        for nombre, texto in sorted(referencias.items()):
            lineas += [f"**{nombre}**", "", f"> {texto}", ""]

    return "\n".join(lineas)


def medir() -> None:
    wavs = _muestras()
    total = sum(_duracion(w) for w in wavs)
    print(f"{len(wavs)} muestras, {total:.1f} s de audio en total.\n")

    resultados = []
    motores = (
        ("Parakeet", medir_parakeet),
        ("Parakeet crudo", lambda w: medir_parakeet(w, normalizado=False)),
        ("Whisper small", medir_whisper),
    )
    for nombre, fn in motores:
        try:
            resultados.append(fn(wavs))
        except ImportError as e:
            print(f"[aviso] {nombre} no medido: falta {e.name}. pip install -e '.[banco]'")
        except Exception as e:  # noqa: BLE001 — el banco no debe morir por un motor
            print(f"[aviso] {nombre} falló: {e}")
        print()

    if not resultados:
        sys.exit("No se pudo medir ningún motor.")

    referencias = _referencias()
    _anotar_wer(resultados, referencias)

    salida = RAIZ / "resultados-h0.md"
    salida.write_text(informe(resultados, referencias), encoding="utf-8")
    salida.with_suffix(".json").write_text(
        json.dumps(resultados, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Informe en {salida}")


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("grabar", help="graba muestras de dictado con el micrófono")
    g.add_argument("--n", type=int, default=10)
    g.add_argument("--segundos", type=int, default=15)

    sub.add_parser("medir", help="mide los motores sobre las muestras grabadas")

    args = p.parse_args()
    if args.cmd == "grabar":
        grabar(args.n, args.segundos)
    else:
        medir()


if __name__ == "__main__":
    main()
