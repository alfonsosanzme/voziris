"""Los iconos de la bandeja, dibujados con Pillow. Sin archivos de imagen.

Cuatro estados, distinguibles de un vistazo y también en escala de grises
(hay quien no distingue rojo de verde), por la FORMA y no por el color:

    reposo        micrófono en contorno
    grabando      micrófono relleno
    procesando    micrófono relleno con un punto abajo a la derecha
    error         micrófono en contorno con una cruz abajo a la derecha

El color base sigue el tema de la barra de tareas: blanco sobre barra oscura,
negro sobre barra clara. El color solo añade: rojo al grabar, ámbar al
procesar, rojo en la cruz de error.

`assets/voziris.ico` se genera desde aquí (`python -m voziris.ui.iconos`) y
es el icono del ejecutable; viaja en el repositorio para que PyInstaller lo
encuentre sin ejecutar nada.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

ESTADOS = ("reposo", "grabando", "procesando", "error")

ROJO = (204, 58, 40, 255)
AMBAR = (240, 170, 30, 255)
CLARO = (245, 245, 245, 255)
OSCURO = (32, 32, 32, 255)

_SUPERMUESTREO = 4


def barra_de_tareas_clara() -> bool:
    """True si Windows usa el tema claro para la barra de tareas (icono negro)."""
    if sys.platform != "win32":
        return False
    try:
        import winreg

        clave = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        )
        valor, _ = winreg.QueryValueEx(clave, "SystemUsesLightTheme")
        return bool(valor)
    except OSError:
        return False


def color_base() -> tuple[int, int, int, int]:
    return OSCURO if barra_de_tareas_clara() else CLARO


def imagen(
    estado: str, tamano: int = 64, base: tuple[int, int, int, int] | None = None
) -> Image.Image:
    """El icono de un estado, RGBA con fondo transparente."""
    if estado not in ESTADOS:
        raise ValueError(f"estado desconocido: {estado!r}")
    base = base or color_base()
    s = tamano * _SUPERMUESTREO
    lienzo = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(lienzo)
    grosor = max(1, s // 12)

    # Cápsula del micrófono.
    cx = s / 2
    capsula = (cx - s * 0.17, s * 0.08, cx + s * 0.17, s * 0.55)
    relleno = estado in ("grabando", "procesando")
    color_capsula = ROJO if estado == "grabando" else base
    if relleno:
        d.rounded_rectangle(capsula, radius=s * 0.17, fill=color_capsula)
    else:
        d.rounded_rectangle(capsula, radius=s * 0.17, outline=base, width=grosor)

    # Arco, pie y base, siempre en contorno.
    d.arc((cx - s * 0.30, s * 0.22, cx + s * 0.30, s * 0.72), 0, 180, fill=base, width=grosor)
    d.line((cx, s * 0.72, cx, s * 0.86), fill=base, width=grosor)
    d.line((cx - s * 0.18, s * 0.87, cx + s * 0.18, s * 0.87), fill=base, width=grosor)

    # Marca abajo a la derecha: punto (procesando) o cruz (error).
    if estado in ("procesando", "error"):
        r = s * 0.16
        mx, my = s - r - grosor, s - r - grosor
        if estado == "procesando":
            d.ellipse((mx - r, my - r, mx + r, my + r), fill=AMBAR)
        else:
            d.ellipse((mx - r, my - r, mx + r, my + r), fill=ROJO)
            k = r * 0.5
            d.line((mx - k, my - k, mx + k, my + k), fill=CLARO, width=grosor)
            d.line((mx - k, my + k, mx + k, my - k), fill=CLARO, width=grosor)

    return lienzo.resize((tamano, tamano), Image.Resampling.LANCZOS)


def guardar_ico(ruta: Path) -> None:
    """Escribe el .ico del ejecutable con el icono de reposo en varios tamaños."""
    tamanos = (16, 24, 32, 48, 64, 128, 256)
    principal = imagen("reposo", 256, OSCURO)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    principal.save(ruta, format="ICO", sizes=[(t, t) for t in tamanos])


if __name__ == "__main__":
    destino = Path(__file__).resolve().parents[3] / "assets" / "voziris.ico"
    guardar_ico(destino)
    print(f"escrito {destino}")
