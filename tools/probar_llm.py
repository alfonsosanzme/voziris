#!/usr/bin/env python3
"""Verificación en castellano de la limpieza con LLM (VOZ-42).

Pasa dictados reales por uno o varios modelos de Groq en los niveles
«limpio» y «reescritura», imprime el antes y el después, y dice si la
comprobación anti-invención (`es_sospechosa`) los habría rechazado. Es la
prueba que cierra la elección de modelo y el prompt: casi todo lo publicado
sobre esto está probado en inglés.

Uso (con GROQ_API_KEY en el entorno; cuesta céntimos):

    python tools/probar_llm.py                       # muestras de banco/ con Parakeet
    python tools/probar_llm.py --texto "eh, el martes, no, el jueves, vamos"
    python tools/probar_llm.py --modelos llama-3.3-70b-versatile openai/gpt-oss-20b

Genera `resultados-llm.md` (fuera del repositorio) con todo.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
MODELOS_CANDIDATOS = ["llama-3.3-70b-versatile", "openai/gpt-oss-20b"]

FRASES_DE_PRUEBA = [
    "eh, recuérdame llamar a la gestoría, o sea, mañana por la mañana, bueno, mejor el martes, "
    "no, el jueves",
    "apunta esto: primero comprar pan, segundo, eh, ir al banco, y tercero, este, volver a casa "
    "antes de las seis",
    "hola marta, te escribo para, mmm, confirmar la reunión del viernes a las diez, si te viene "
    "bien contesta a este correo, un saludo, alfonso",
]


def _transcripciones(n: int) -> list[str]:
    """Las muestras del banco pasadas por Parakeet, o las frases de prueba."""
    wavs = sorted((RAIZ / "banco").glob("muestra-*.wav"))[:n]
    if not wavs:
        return FRASES_DE_PRUEBA
    import soundfile as sf

    from voziris.audio.captura import normalizar
    from voziris.motores.local import MotorLocal
    from voziris.tipos import Audio

    motor = MotorLocal("nemo-parakeet-tdt-0.6b-v3", RAIZ / "modelos")
    print("Cargando Parakeet…", flush=True)
    motor.precalentar()
    if not motor.disponible():
        sys.exit(motor.error)
    textos = []
    for wav in wavs:
        datos, sr = sf.read(str(wav), dtype="float32")
        textos.append(motor.transcribir(Audio(muestras=normalizar(datos), sr=sr), "es").texto)
    return textos + FRASES_DE_PRUEBA


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--modelos", nargs="+", default=MODELOS_CANDIDATOS)
    p.add_argument("--texto", action="append", help="frase a probar (se puede repetir)")
    p.add_argument("--muestras", type=int, default=8)
    args = p.parse_args()

    clave = os.environ.get("GROQ_API_KEY", "")
    if not clave:
        sys.exit("Falta GROQ_API_KEY en el entorno.")

    from voziris.proceso.llm import LimpiezaLLM, es_sospechosa
    from voziris.tipos import Contexto, Modo, Nivel, Transcripcion

    textos = args.texto or _transcripciones(args.muestras)
    lineas = [
        "# Verificación del LLM en castellano", "", f"Fecha: {time.strftime('%Y-%m-%d %H:%M')}", "",
    ]
    for modelo in args.modelos:
        llm = LimpiezaLLM("https://api.groq.com/openai/v1", modelo, clave)
        lineas += [f"## {modelo}", ""]
        for nivel in (Nivel.LIMPIO, Nivel.REESCRITURA):
            lineas += [f"### {nivel.value}", ""]
            rechazos = 0
            tiempos = []
            for texto in textos:
                ctx = Contexto("app_activa", Modo.MANTENER, None, True, nivel)
                t0 = time.perf_counter()
                salida = llm.aplicar(Transcripcion(texto, "es", "local", 0, 10.0), ctx)
                tiempos.append(time.perf_counter() - t0)
                sospechosa = salida.avisos and any("alteró" in a for a in salida.avisos)
                rechazos += bool(sospechosa)
                marca = " ⚠ RECHAZADA" if sospechosa else ""
                lineas += [f"**Entrada:** {texto}", "", f"**Salida{marca}:** {salida.texto}", ""]
                if salida.avisos:
                    lineas += [f"_avisos: {' · '.join(salida.avisos)}_", ""]
                print(f"[{modelo} · {nivel.value}] {tiempos[-1]*1000:.0f} ms{marca}")
                print(f"  < {texto[:110]}")
                print(f"  > {salida.texto[:110]}\n")
            media = sum(tiempos) / len(tiempos) * 1000
            lineas += [
                f"_Media {media:.0f} ms · {rechazos}/{len(textos)} rechazadas por sospechosas_",
                "",
            ]
        llm.cerrar()
    salida_md = RAIZ / "resultados-llm.md"
    salida_md.write_text("\n".join(lineas), encoding="utf-8")
    print(f"Informe en {salida_md}")
    print("\nComprueba a ojo: ¿ha inventado algo?")
    print("¿Ha respetado 'el martes, no, el jueves' → 'el jueves'?")
    print("es_sospechosa() es la red de seguridad, no el criterio: lo decide la lectura.")
    _ = es_sospechosa


if __name__ == "__main__":
    main()
