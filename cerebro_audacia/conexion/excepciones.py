"""Excepciones de conectividad, concurrencia y gobierno del Cerebro Central de AudacIA."""

from __future__ import annotations


class CerebroCentralError(Exception):
    """Excepción base para fallos relacionados con el Cerebro Central."""
    pass


class ViolacionArquitecturaError(CerebroCentralError):
    """Lanzada cuando un agente intenta violar las reglas de arquitectura del Cerebro Central.
    
    Ejemplos de violación:
    - Intentar pasar un parámetro 'model' o 'modelo' para seleccionar un LLM alternativo.
    - Intentar suministrar una ruta a un archivo '.gguf' o '.safetensors'.
    - Intentar instanciar o registrar un motor de inferencia propio.
    """
    pass


class LlmSaturadoError(CerebroCentralError):
    """Lanzada inmediatamente cuando la cola de concurrencia del Cerebro Central está llena."""
    pass


class LlmColaExpiradaError(CerebroCentralError, TimeoutError):
    """Lanzada cuando una solicitud espera en la cola de admisión más del tiempo máximo permitido."""
    pass


class LlmNoDisponibleError(CerebroCentralError, ConnectionError):
    """Lanzada cuando el Circuit Breaker está en estado OPEN debido a caídas consecutivas del Cerebro Central."""
    pass


class LlmLimiteExcedidoError(CerebroCentralError, ValueError):
    """Lanzada localmente cuando una solicitud excede los límites presupuestados de caracteres o tokens de contexto."""
    pass


class LlmOomError(CerebroCentralError):
    """Lanzada cuando el servidor de inferencia devuelve un fallo inequívoco de memoria GPU (CUDA OOM / allocation failure)."""
    pass


class LlmRespuestaInvalidaError(CerebroCentralError):
    """Lanzada cuando la respuesta del servidor LLM está truncada, malformada o contiene JSON corrupto."""
    pass
