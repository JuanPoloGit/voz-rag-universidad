"""Redireccion de compatibilidad: la deteccion de idioma se mudo a `hacu.idioma`.

Vivia aqui porque solo lo usaba `voz/__init__.py`, para elegir con que voz de
Piper leer cada frase. Ahora tambien lo necesita `context.py`, que instruye al
modelo en que idioma responder (ver `ContextBuilder._recordatorio_de_idioma`) -
y `context.py` no puede importar de `hacu.voz` sin arrastrar sounddevice,
piper y faster-whisper, las dependencias pesadas de toda la capa de voz, que
un arranque sin microfono ni tarjeta de sonido no tiene por que pagar.

La logica -sin cambios- vive ahora en `hacu/idioma.py`, en la raiz del
paquete, que ambos consumidores pueden importar sin tocarse entre si. Este
modulo se deja como redireccion para no romper ningun `from .idioma import
detectar_idioma` que ya exista dentro de `hacu.voz`.
"""

from __future__ import annotations

from ..idioma import detectar_idioma

__all__ = ["detectar_idioma"]
