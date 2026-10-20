"""Capa de normalización lingüística regional y detección de intenciones para Haku Inventario.

Responsabilidades:
1. Mapeo fonético de siglas técnicas habladas ("erre jota cuarenta y cinco" -> "RJ45").
2. Interpretación de modismos y expresiones costeñas ("esa vaina", "cuánto queda", etc.).
3. Detección estricta de ambigüedad ("pásame ese cable" -> solicita aclaración).
4. Resolución no presuntiva de códigos (desambiguación de "CAB-001").
5. Mantenimiento de contexto conversacional temporal (máximo 30 min, usuario autenticado, memoria efímera).
"""

from .costeno import InterpretadorCosteno
from .normalizador import (
    IntencionResultado,
    IntencionTipo,
    NormalizadorLenguajeHaku,
)
from .siglas import NormalizadorSiglasTecnicas

__all__ = [
    "IntencionTipo",
    "IntencionResultado",
    "NormalizadorLenguajeHaku",
    "InterpretadorCosteno",
    "NormalizadorSiglasTecnicas",
]
