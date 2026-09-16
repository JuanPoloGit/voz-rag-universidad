"""Interfaz grafica de exhibicion (PySide6).

Se importa perezosamente desde `run_hacu.py`: quien arranque HACU en consola no
tiene por que tener Qt instalado.
"""

from __future__ import annotations

from .estilos import EstadoUI
from .ventana import VentanaHacu, lanzar

__all__ = ["EstadoUI", "VentanaHacu", "lanzar"]
