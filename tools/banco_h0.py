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
import statistics
import sys
import time
from pathlib import Path

CARPETA = Path(__file__).resolve().parents[1] / "banco"
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


def _ram_mb() -> float:
    import psutil

    return psutil.Process().memory_info().rss / 1024 / 1024


def medir_parakeet(wavs: list[Path]) -> dict:
    """Parakeet TDT 0.6B v3 en ONNX int8, vía onnx-asr."""
    import onnx_asr

    print("Cargando Parakeet TDT v3 (la primera vez descarga ~680 MB)…")
    ram_antes = _ram_mb()
    t0 = time.perf_counter()
    modelo = onnx_asr.load_model("nemo-parakeet-tdt-0.6b-v3")
    carga_s = time.perf_counter() - t0
    print(f"  cargado en {carga_s:.1f} s")

    # Primera pasada en vacío: la inferencia inicial siempre es más lenta y
    # falsearía la mediana.
    modelo.recognize(str(wavs[0]))

    filas = []
    for wav in wavs:
        dur = _duracion(wav)
        t0 = time.perf_counter()
        texto = modelo.recognize(str(wav))
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
        "motor": "parakeet-tdt-0.6b-v3-int8",
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

    list(modelo.transcribe(str(wavs[0]), language="es")[0])  # calentamiento

    filas = []
    for wav in wavs:
        dur = _duracion(wav)
        t0 = time.perf_counter()
        segmentos, _ = modelo.transcribe(str(wav), language="es")
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


def informe(resultados: list[dict]) -> str:
    import platform

    lineas = [
        "# Resultados del hito 0",
        "",
        f"- Equipo: {platform.processor() or 'desconocido'}",
        f"- Sistema: {platform.platform()}",
        f"- Python: {platform.python_version()}",
        f"- Fecha: {time.strftime('%Y-%m-%d %H:%M')}",
        "",
        "## Resumen",
        "",
        "| Motor | RTF mediano | RTF peor | Carga (s) | RAM (MB) |",
        "|---|---|---|---|---|",
    ]
    for r in resultados:
        rtfs = [f["rtf"] for f in r["filas"]]
        lineas.append(
            f"| {r['motor']} | {statistics.median(rtfs):.3f} | {max(rtfs):.3f} "
            f"| {r['carga_s']} | {r['ram_mb']} |"
        )

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
    for r in resultados:
        lineas += [f"### {r['motor']}", ""]
        for f in r["filas"]:
            lineas += [f"**{f['muestra']}** · RTF {f['rtf']:.3f}", "", f"> {f['texto']}", ""]

    return "\n".join(lineas)


def medir() -> None:
    wavs = _muestras()
    total = sum(_duracion(w) for w in wavs)
    print(f"{len(wavs)} muestras, {total:.1f} s de audio en total.\n")

    resultados = []
    for nombre, fn in (("Parakeet", medir_parakeet), ("Whisper small", medir_whisper)):
        try:
            resultados.append(fn(wavs))
        except ImportError as e:
            print(f"[aviso] {nombre} no medido: falta {e.name}. pip install -e '.[banco]'")
        except Exception as e:  # noqa: BLE001 — el banco no debe morir por un motor
            print(f"[aviso] {nombre} falló: {e}")
        print()

    if not resultados:
        sys.exit("No se pudo medir ningún motor.")

    salida = Path(__file__).resolve().parents[1] / "resultados-h0.md"
    salida.write_text(informe(resultados), encoding="utf-8")
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
