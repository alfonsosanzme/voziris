"""Nombres de teclas y análisis de combinaciones.

Lo comparten `config.py` (validar la sintaxis de los atajos del TOML) y
`atajos.py` (traducir cada nombre a los códigos de tecla virtual de Windows
que verá el hook). Vive aparte para que la configuración no dependa de
Win32 y pueda validarse en cualquier sistema.

Sintaxis: nombres separados por «+», sin distinguir mayúsculas. Los
modificadores son `ctrl`, `alt`, `shift` y `win`. Una combinación puede ser
solo de modificadores (`ctrl+win` es el atajo «mantener» por defecto) y como
mucho lleva una tecla que no lo sea.
"""

from __future__ import annotations

from dataclasses import dataclass

MODIFICADORES: tuple[str, ...] = ("ctrl", "alt", "shift", "win")
"""Orden canónico: así se escriben las combinaciones normalizadas."""

_ALIAS: dict[str, str] = {
    "control": "ctrl",
    "windows": "win",
    "super": "win",
    "meta": "win",
    "espacio": "space",
    "escape": "esc",
    "return": "enter",
    "intro": "enter",
    "supr": "delete",
    "del": "delete",
    "inicio": "home",
    "fin": "end",
    "ins": "insert",
    "pgup": "pageup",
    "pgdn": "pagedown",
    "arriba": "up",
    "abajo": "down",
    "izquierda": "left",
    "derecha": "right",
    "bloqmayus": "capslock",
    "imprpant": "printscreen",
    "pausa": "pause",
}

# Códigos de tecla virtual de Windows (winuser.h). Un nombre puede
# corresponder a varios códigos: el hook ve LCONTROL o RCONTROL, nunca
# CONTROL a secas, y ambos cuentan como «ctrl».
VK: dict[str, frozenset[int]] = {
    "ctrl": frozenset({0x11, 0xA2, 0xA3}),
    "alt": frozenset({0x12, 0xA4, 0xA5}),
    "shift": frozenset({0x10, 0xA0, 0xA1}),
    "win": frozenset({0x5B, 0x5C}),
    "space": frozenset({0x20}),
    "esc": frozenset({0x1B}),
    "enter": frozenset({0x0D}),
    "tab": frozenset({0x09}),
    "backspace": frozenset({0x08}),
    "delete": frozenset({0x2E}),
    "insert": frozenset({0x2D}),
    "home": frozenset({0x24}),
    "end": frozenset({0x23}),
    "pageup": frozenset({0x21}),
    "pagedown": frozenset({0x22}),
    "up": frozenset({0x26}),
    "down": frozenset({0x28}),
    "left": frozenset({0x25}),
    "right": frozenset({0x27}),
    "capslock": frozenset({0x14}),
    "printscreen": frozenset({0x2C}),
    "pause": frozenset({0x13}),
    "scrolllock": frozenset({0x91}),
    "numlock": frozenset({0x90}),
    **{f"f{n}": frozenset({0x70 + n - 1}) for n in range(1, 25)},
    **{c: frozenset({ord(c.upper())}) for c in "abcdefghijklmnopqrstuvwxyz"},
    **{d: frozenset({ord(d)}) for d in "0123456789"},
    **{f"numpad{d}": frozenset({0x60 + d}) for d in range(10)},
}

NOMBRES: frozenset[str] = frozenset(VK)
"""Todos los nombres canónicos admitidos."""


@dataclass(frozen=True)
class Combinacion:
    """Una combinación ya analizada y normalizada."""

    teclas: tuple[str, ...]
    """Nombres canónicos: modificadores en orden canónico y, al final, la tecla."""

    @property
    def texto(self) -> str:
        """Forma normalizada, la que se escribe en el TOML: «ctrl+shift+space»."""
        return "+".join(self.teclas)

    @property
    def modificadores(self) -> frozenset[str]:
        return frozenset(t for t in self.teclas if t in MODIFICADORES)

    @property
    def tecla(self) -> str | None:
        """La tecla que no es modificador, o None si solo hay modificadores."""
        for t in self.teclas:
            if t not in MODIFICADORES:
                return t
        return None

    @property
    def codigos(self) -> tuple[frozenset[int], ...]:
        """Códigos de tecla virtual, en el mismo orden que `teclas`."""
        return tuple(VK[t] for t in self.teclas)


def analizar(texto: str) -> Combinacion:
    """Convierte «Ctrl + Win» en `Combinacion(("ctrl", "win"))`.

    Raises:
        ValueError: con un mensaje que dice qué parte está mal y qué se admite.
    """
    partes = [p.strip().lower() for p in texto.split("+")]
    if not texto.strip() or any(not p for p in partes):
        raise ValueError(
            f"«{texto}» no es una combinación: se esperan nombres de tecla separados por «+»"
        )

    nombres: list[str] = []
    for parte in partes:
        nombre = _ALIAS.get(parte, parte)
        if nombre not in NOMBRES:
            raise ValueError(
                f"«{parte}» no es una tecla conocida. Se admiten {', '.join(MODIFICADORES)}, "
                "letras, cifras, f1-f24, space, esc, enter, tab y las de navegación"
            )
        if nombre in nombres:
            raise ValueError(f"«{texto}» repite la tecla «{nombre}»")
        nombres.append(nombre)

    no_modificadores = [n for n in nombres if n not in MODIFICADORES]
    if len(no_modificadores) > 1:
        raise ValueError(
            f"«{texto}» lleva más de una tecla que no es modificador: "
            f"{', '.join(no_modificadores)}. Como mucho una"
        )

    ordenados = [m for m in MODIFICADORES if m in nombres] + no_modificadores
    return Combinacion(tuple(ordenados))
