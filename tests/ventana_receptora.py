"""Ventana de destino para los tests de inserción, en un proceso aparte.

Una aplicación real vive en otro proceso: si Tk corre dentro del proceso del
test, su lectura del portapapeles y la restauración de Voziris se pisan (el
portapapeles se abre y se cierra por proceso) y el resultado tiene ruido.

Uso: python ventana_receptora.py <archivo_salida> <segundos>

Muestra un Text con el foco, vuelca su contenido al archivo cada 50 ms y se
cierra cuando pasan los segundos o cuando aparece un archivo «parar» al lado.
"""

from __future__ import annotations

import os
import sys
import time
import tkinter as tk
from pathlib import Path

TITULO = "Voziris receptor"


def main() -> None:
    salida = Path(sys.argv[1])
    segundos = float(sys.argv[2])
    parar = salida.parent / "parar"

    raiz = tk.Tk()
    raiz.title(TITULO)
    raiz.geometry("420x200+240+240")
    raiz.attributes("-topmost", True)
    texto = tk.Text(raiz, font=("Segoe UI", 12))
    texto.pack(fill="both", expand=True)
    raiz.lift()
    raiz.focus_force()
    texto.focus_set()
    fin = time.monotonic() + segundos

    temporal = salida.with_suffix(".tmp")

    def volcar() -> None:
        # Escritura atómica: quien lea nunca verá el archivo a medio escribir.
        # Si el lector lo tiene abierto justo ahora, Windows deniega el
        # reemplazo: se reintenta en el siguiente tick, sin romper el bucle.
        try:
            temporal.write_text(texto.get("1.0", "end-1c"), encoding="utf-8")
            os.replace(temporal, salida)
        except OSError:
            pass
        if time.monotonic() > fin or parar.exists():
            raiz.destroy()
        else:
            raiz.after(50, volcar)

    raiz.after(50, volcar)
    raiz.mainloop()


if __name__ == "__main__":
    main()
