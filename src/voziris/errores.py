"""Errores del dominio.

Regla que sostiene el diseño: **ninguna etapa del pipeline propaga
excepciones hacia arriba**. Un post-proceso que falla devuelve su entrada
intacta más un aviso; un motor que falla se lo dice al selector para que
pruebe el otro. El dictado del usuario no se pierde nunca por un fallo de
una etapa intermedia, y si todo falla queda en el historial.

Estas excepciones existen para comunicarse *dentro* de una etapa y con el
selector, no para escapar al bucle principal.
"""

from __future__ import annotations


class VozirisError(Exception):
    """Raíz de todos los errores del proyecto."""


class MotorNoDisponible(VozirisError):
    """El motor no puede atender: falta el modelo, la clave o la red.

    El selector la captura y prueba el motor alternativo (B3).
    """


class TranscripcionFallida(VozirisError):
    """El motor respondió pero no produjo texto utilizable."""


class EntregaFallida(VozirisError):
    """No se pudo entregar el texto en el destino.

    El texto sigue en el historial, así que el usuario puede reintentar (D3).
    """


class ConfigInvalida(VozirisError):
    """El config.toml no es válido. Único error que sí detiene el arranque."""


class MicrofonoNoDisponible(VozirisError):
    """No se puede abrir el micrófono, o dejó de entregar audio a media grabación.

    El orquestador descarta el dictado en curso y avisa; la aplicación sigue
    viva y el usuario puede elegir otro micrófono en los ajustes.
    """


class AtajosNoDisponibles(VozirisError):
    """No se pudo instalar el hook de teclado.

    La aplicación arranca igual, con el icono en error: se puede dictar desde
    el menú de bandeja («Dictar ahora») y reintentar desde los ajustes.
    """
