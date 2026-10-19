"""Contrato formal base (AgentContract) para los agentes de AudacIA.

Define la interfaz común, las reglas de validación en tiempo de ejecución,
la contención de errores y las garantías de interoperabilidad sin depender
de implementaciones internas de modelos, bases de datos o frameworks.
"""

from __future__ import annotations

import abc
import logging
import time
from typing import Any, Optional

from .esquemas import (
    AgentContext,
    AgentError,
    AgentErrorCode,
    AgentExecutionError,
    AgentInput,
    AgentMetadata,
    AgentOutput,
    AgentResult,
    ContractValidationError,
    DataAutonomyLevel,
)

logger = logging.getLogger("cerebro_audacia.agentes")


class AgentContract(abc.ABC):
    """Contrato base abstracto que todo agente de AudacIA debe implementar.

    Sigue el patrón Template Method:
    - `metadata`: Propiedad abstracta que define la identidad, canales y capacidades.
    - `validar_entrada`: Hook de validación previa que verifica canal, autenticación e intención.
    - `_ejecutar_interno`: Método abstracto donde el agente implementa su lógica propia.
    - `ejecutar`: Método público sellado que orquesta validación, medición de latencia,
      contención de excepciones y aseguramiento de invariantes del resultado.
    """

    @property
    @abc.abstractmethod
    def metadata(self) -> AgentMetadata:
        """Metadatos inmutables del agente."""
        ...

    @abc.abstractmethod
    def _ejecutar_interno(self, entrada: AgentInput) -> AgentOutput:
        """Lógica interna de ejecución del agente.

        Debe ser implementada por cada adaptador o agente concreto.
        Retorna `AgentOutput` en caso de éxito.
        Si la operación falla de manera prevista, debe lanzar `AgentExecutionError`.
        """
        ...

    def validar_entrada(self, entrada: AgentInput) -> None:
        """Valida que la entrada cumpla estrictamente los requisitos declarados por el contrato.

        Lanza `ContractValidationError` si alguna regla se incumple.
        """
        if not isinstance(entrada, AgentInput):
            raise ContractValidationError(
                f"Entrada inválida: se esperaba AgentInput, recibido {type(entrada).__name__}.",
                codigo=AgentErrorCode.INVALID_INPUT,
            )

        # 1. Validación de contenido
        if not isinstance(entrada.contenido, str) or not entrada.contenido.strip():
            raise ContractValidationError(
                "El contenido de la entrada no puede estar vacío.",
                codigo=AgentErrorCode.INVALID_INPUT,
            )

        # 2. Validación de canal de comunicación
        canal = entrada.contexto.canal
        if canal not in self.metadata.canales_permitidos:
            raise ContractValidationError(
                f"El canal '{canal}' no está autorizado para el agente '{self.metadata.id}'. "
                f"Canales permitidos: {list(self.metadata.canales_permitidos)}.",
                codigo=AgentErrorCode.CHANNEL_NOT_ALLOWED,
                detalles={
                    "canal_solicitado": canal,
                    "canales_permitidos": list(self.metadata.canales_permitidos),
                },
            )

        # 3. Validación de autenticación si el agente la exige
        if self.metadata.requiere_autenticacion:
            if not entrada.contexto.usuario_id or not str(entrada.contexto.usuario_id).strip():
                raise ContractValidationError(
                    f"El agente '{self.metadata.id}' requiere una sesión con usuario autenticado.",
                    codigo=AgentErrorCode.UNAUTHORIZED,
                )

        # 4. Validación de intención si fue provista
        if entrada.intencion is not None and self.metadata.capacidades:
            if entrada.intencion not in self.metadata.capacidades:
                raise ContractValidationError(
                    f"La intención '{entrada.intencion}' no está entre las capacidades declaradas "
                    f"del agente '{self.metadata.id}'. Capacidades disponibles: {list(self.metadata.capacidades)}.",
                    codigo=AgentErrorCode.INTENT_NOT_SUPPORTED,
                    detalles={
                        "intencion": entrada.intencion,
                        "capacidades_declaradas": list(self.metadata.capacidades),
                    },
                )

        # 5. Validación de ausencia de parámetros prohibidos de LLM (Regla de Cerebro Central único)
        if entrada.parametros:
            CLAVES_PROHIBIDAS_LLM = {"model", "modelo", "model_path", "ruta_modelo", "gguf", "gguf_path", "weights", "provider"}
            for clave in entrada.parametros:
                if str(clave).lower() in CLAVES_PROHIBIDAS_LLM:
                    raise ContractValidationError(
                        f"Parámetro prohibido '{clave}' en solicitud al agente '{self.metadata.id}': "
                        "los agentes de AudacIA no pueden recibir ni propagar especificaciones de modelo LLM. "
                        "El modelo es provisto y gobernado exclusivamente por el Cerebro Central.",
                        codigo=AgentErrorCode.INVALID_INPUT,
                    )

    def validar_aislamiento_arquitectonico(self) -> None:
        """Verifica que el agente no contenga atributos o instancias prohibidas de LLM local."""
        ATRIBUTOS_PROHIBIDOS = ("_llama", "llama_server", "model_path", "gguf_path", "modelo_archivo", "_llm_propio")
        for attr in ATRIBUTOS_PROHIBIDOS:
            if hasattr(self, attr):
                raise ContractValidationError(
                    f"Violación de Arquitectura de AudacIA en agente '{self.metadata.id}': "
                    f"posee el atributo prohibido '{attr}'. Los agentes tienen terminantemente prohibido "
                    "albergar o instanciar modelos de lenguaje locales.",
                    codigo=AgentErrorCode.INTERNAL_ERROR,
                )

    def ejecutar(self, entrada: AgentInput) -> AgentResult:
        """Punto de entrada universal y protegido para la ejecución del agente.

        Garantías del contrato:
        1. Trazabilidad: El `correlation_id` de la entrada se preserva intacto en el resultado.
        2. Latencia: Se mide y registra con precisión `duracion_ms`.
        3. Seguridad: Las excepciones no controladas se capturan y sanitizan para
           no exponer detalles sensibles ni stack traces al llamador.
        4. Invariante: Siempre retorna un `AgentResult` válido (o éxito estricto o fallo estricto).
        """
        inicio = time.perf_counter()

        # Extraer correlation_id de forma segura
        correlation_id = "unknown"
        if isinstance(entrada, AgentInput) and hasattr(entrada, "contexto") and entrada.contexto:
            correlation_id = entrada.contexto.correlation_id or "unknown"

        # Paso 1: Validación previa y aislamiento arquitectónico
        try:
            self.validar_aislamiento_arquitectonico()
            self.validar_entrada(entrada)
        except ContractValidationError as exc:
            duracion = (time.perf_counter() - inicio) * 1000.0
            error = AgentError(
                codigo=exc.codigo,
                mensaje=str(exc),
                detalles=exc.detalles if exc.detalles else None,
            )
            return AgentResult.fallo(
                error=error,
                correlation_id=correlation_id,
                duracion_ms=duracion,
            )

        # Paso 2: Ejecución de la lógica del agente
        try:
            salida = self._ejecutar_interno(entrada)

            if not isinstance(salida, AgentOutput):
                duracion = (time.perf_counter() - inicio) * 1000.0
                error = AgentError(
                    codigo=AgentErrorCode.INTERNAL_ERROR,
                    mensaje=f"Violación de contrato interno: el agente retornó {type(salida).__name__} en lugar de AgentOutput.",
                )
                return AgentResult.fallo(
                    error=error,
                    correlation_id=correlation_id,
                    duracion_ms=duracion,
                )

            # Paso 3: Validación de nivel de autonomía
            # Si el agente es READ_ONLY, está estrictamente prohibido que genere propuestas de mutación
            if self.metadata.nivel_autonomia == DataAutonomyLevel.READ_ONLY and salida.propuesta is not None:
                duracion = (time.perf_counter() - inicio) * 1000.0
                error = AgentError(
                    codigo=AgentErrorCode.EXECUTION_FAILED,
                    mensaje=f"Violación de autonomía: el agente '{self.metadata.id}' es READ_ONLY y no puede generar propuestas.",
                )
                return AgentResult.fallo(
                    error=error,
                    correlation_id=correlation_id,
                    duracion_ms=duracion,
                )

            duracion = (time.perf_counter() - inicio) * 1000.0
            return AgentResult.exito(
                salida=salida,
                correlation_id=correlation_id,
                duracion_ms=duracion,
            )

        except AgentExecutionError as exc:
            duracion = (time.perf_counter() - inicio) * 1000.0
            error = AgentError(
                codigo=exc.codigo,
                mensaje=exc.mensaje_seguro,
                detalles=exc.detalles if exc.detalles else None,
            )
            return AgentResult.fallo(
                error=error,
                correlation_id=correlation_id,
                duracion_ms=duracion,
            )

        except Exception as exc:
            # Fallo no controlado en la implementación del agente
            duracion = (time.perf_counter() - inicio) * 1000.0
            logger.exception(
                "Error no controlado en el agente '%s' (correlation_id=%s): %s",
                self.metadata.id,
                correlation_id,
                exc,
            )
            error = AgentError(
                codigo=AgentErrorCode.INTERNAL_ERROR,
                mensaje=f"Error interno durante la ejecución del agente '{self.metadata.id}'.",
                detalles=None,  # No filtrar stack traces hacia el exterior
            )
            return AgentResult.fallo(
                error=error,
                correlation_id=correlation_id,
                duracion_ms=duracion,
            )

    def __call__(self, entrada: AgentInput) -> AgentResult:
        """Permite invocar el agente directamente como un callable."""
        return self.ejecutar(entrada)
