"""Módulo de conexión y cliente oficial para el Cerebro Central de AudacIA."""

from .cliente import (
    ClienteCerebroCentral,
    ConfiguracionCerebroCentral,
    RespuestaCerebroCentral,
    obtener_cliente_cerebro_central,
)
from .excepciones import (
    CerebroCentralError,
    LlmColaExpiradaError,
    LlmLimiteExcedidoError,
    LlmNoDisponibleError,
    LlmOomError,
    LlmRespuestaInvalidaError,
    LlmSaturadoError,
    ViolacionArquitecturaError,
)

__all__ = [
    "ClienteCerebroCentral",
    "ConfiguracionCerebroCentral",
    "RespuestaCerebroCentral",
    "obtener_cliente_cerebro_central",
    "CerebroCentralError",
    "ViolacionArquitecturaError",
    "LlmSaturadoError",
    "LlmColaExpiradaError",
    "LlmNoDisponibleError",
    "LlmLimiteExcedidoError",
    "LlmOomError",
    "LlmRespuestaInvalidaError",
]
