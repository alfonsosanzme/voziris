"""F4 — panel de configuración.

Para no tener que editar el TOML a mano. Es lo que hace publicable el proyecto:
sin esto, solo lo usa quien lo ha escrito.

Pestañas, en este orden — el usuario lo abre por los atajos y por el micrófono:

    Atajos   las cuatro combinaciones, capturadas al pulsar, no escritas
    Audio    micrófono, ganancia con medidor en vivo, sonidos
    Motor    local/API/auto, clave de API, botón «probar»
    Texto    nivel, diccionario, sustituciones
    Destinos ruta del Markdown con selector de archivo, formato, auto-Enter
    Acerca de  versión, licencias y **la atribución a NVIDIA (obligatoria)**

Tkinter, que viene con Python: nada de Qt para seis pestañas. Añadiría decenas
de megas al paquete portable.

Guardar reescribe el TOML con tomlkit (conserva los comentarios) y aplica en
caliente lo que se pueda. Cambiar de atajo o de micrófono se aplica al
momento; cambiar de modelo local exige reiniciar, y hay que decirlo.

Issue: VOZ-60.
"""

from __future__ import annotations


class Ajustes:
    def abrir(self) -> None:
        raise NotImplementedError("VOZ-60")
