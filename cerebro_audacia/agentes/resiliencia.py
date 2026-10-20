"""Primitivas de resiliencia, tolerancia a fallos y gobernanza de recursos para AudacIA.

Implementa patrones fundamentales sin dependencias externas:
1. CircuitBreaker (aislamiento de fallos y prevención de cascadas).
2. ConcurrencyGovernor (control de concurrencia y límites de cola).
3. ResourceTracker (salud y gobernanza de recursos CPU, GPU, VRAM, memoria).
4. RetryPolicy (reintentos seguros conscientes de idempotencia).
5. ResilientAgentExecutor (ejecutor con timeout real, contención y estados ambiguos).
"""

from __future__ import annotations

import concurrent.futures
import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping, Optional, Sequence

from .contrato import AgentContract
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
    ExecutionState,
    IdempotencyMode,
    ResourceKind,
)

logger = logging.getLogger("cerebro_audacia.resiliencia")


class CircuitState(str, Enum):
    """Estados del Circuit Breaker."""

    CLOSED = "CLOSED"      # Operación normal: el tráfico pasa
    OPEN = "OPEN"          # Fallo persistente: tráfico rechazado inmediatamente
    HALF_OPEN = "HALF_OPEN"  # Prueba de recuperación: se permite una ejecución de sondeo


class CircuitBreaker:
    """Implementación thread-safe del patrón Circuit Breaker para agentes y servicios.

    Previene que fallos repetidos saturen el sistema o degraden servicios compartidos.
    """

    def __init__(
        self,
        fallos_consecutivos_max: int = 3,
        tiempo_recuperacion_segundos: float = 5.0,
        nombre: str = "default",
    ):
        self.fallos_consecutivos_max = max(1, fallos_consecutivos_max)
        self.tiempo_recuperacion_segundos = max(0.001, tiempo_recuperacion_segundos)
        self.nombre = nombre

        self._lock = threading.Lock()
        self._estado: CircuitState = CircuitState.CLOSED
        self._fallos_consecutivos: int = 0
        self._tiempo_apertura: float = 0.0

    @property
    def estado(self) -> CircuitState:
        with self._lock:
            if self._estado == CircuitState.OPEN:
                # Comprobar si transcurrió el tiempo de enfriamiento para pasar a HALF_OPEN
                ahora = time.monotonic()
                if ahora - self._tiempo_apertura >= self.tiempo_recuperacion_segundos:
                    self._estado = CircuitState.HALF_OPEN
                    logger.info("CircuitBreaker '%s' pasó a HALF_OPEN tras enfriamiento.", self.nombre)
            return self._estado

    def puede_ejecutar(self) -> bool:
        """Determina si una solicitud puede proceder según el estado actual."""
        st = self.estado
        return st in (CircuitState.CLOSED, CircuitState.HALF_OPEN)

    def registrar_exito(self) -> None:
        """Registra una ejecución exitosa, restableciendo el circuito a CLOSED."""
        with self._lock:
            if self._estado != CircuitState.CLOSED:
                logger.info("CircuitBreaker '%s' se recuperó: pasando a CLOSED.", self.nombre)
            self._estado = CircuitState.CLOSED
            self._fallos_consecutivos = 0
            self._tiempo_apertura = 0.0

    def registrar_fallo(self, codigo: Optional[AgentErrorCode] = None) -> None:
        """Registra un fallo. No todos los errores disparan el breaker (ej. INVALID_INPUT no es fallo de servicio)."""
        # Errores que representan indisponibilidad o fallo del servicio
        ERRORES_CIRCUITO = {
            AgentErrorCode.TIMEOUT,
            AgentErrorCode.RESOURCE_UNAVAILABLE,
            AgentErrorCode.RESOURCE_EXHAUSTED,
            AgentErrorCode.DEPENDENCY_UNAVAILABLE,
            AgentErrorCode.INTERNAL_ERROR,
            AgentErrorCode.EXECUTION_FAILED,
        }

        if codigo is not None and codigo not in ERRORES_CIRCUITO:
            return  # Errores del cliente (ej. canal no permitido) no abren el circuito del agente

        with self._lock:
            self._fallos_consecutivos += 1
            if self._estado == CircuitState.HALF_OPEN:
                # Falló la prueba en HALF_OPEN: vuelve a abrir inmediatamente
                self._estado = CircuitState.OPEN
                self._tiempo_apertura = time.monotonic()
                logger.warning("CircuitBreaker '%s' falló en HALF_OPEN: reabriendo circuito.", self.nombre)
            elif self._fallos_consecutivos >= self.fallos_consecutivos_max:
                self._estado = CircuitState.OPEN
                self._tiempo_apertura = time.monotonic()
                logger.warning(
                    "CircuitBreaker '%s' superó %d fallos consecutivos: pasando a OPEN.",
                    self.nombre, self.fallos_consecutivos_max
                )


class ConcurrencyGovernor:
    """Controlador de concurrencia y tamaño de cola para un agente o recurso.

    Impide que solicitudes concurrentes desborden la memoria o la GPU.
    """

    def __init__(self, max_concurrencia: int = 1, max_cola: int = 5, nombre: str = "default"):
        self.max_concurrencia = max(1, max_concurrencia)
        self.max_cola = max(0, max_cola)
        self.nombre = nombre

        self._lock = threading.Lock()
        self._en_ejecucion: int = 0
        self._en_espera: int = 0

    @property
    def en_ejecucion(self) -> int:
        with self._lock:
            return self._en_ejecucion

    @property
    def en_espera(self) -> int:
        with self._lock:
            return self._en_espera

    def intentar_adquirir(self) -> bool:
        """Intenta adquirir slot de ejecución inmediatamente. Si no hay cupo, rechaza."""
        with self._lock:
            if self._en_ejecucion < self.max_concurrencia:
                self._en_ejecucion += 1
                return True
            return False

    def liberar(self) -> None:
        """Libera un slot de ejecución."""
        with self._lock:
            if self._en_ejecucion > 0:
                self._en_ejecucion -= 1


class ResourceTracker:
    """Rastreador de salud y disponibilidad de recursos de hardware y servicios.

    Permite distinguir entre utilización elevada (normal) y saturación/agotamiento crítico (VRAM OOM).
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._saturaciones: dict[ResourceKind, str] = {}

    def marcar_saturado(self, recurso: ResourceKind, motivo: str) -> None:
        """Marca un recurso como saturado (ej. VRAM agotada tras error CUDA OOM)."""
        with self._lock:
            self._saturaciones[recurso] = motivo
            logger.warning("Recurso %s marcado como SATURADO: %s", recurso.value, motivo)

    def liberar_saturacion(self, recurso: ResourceKind) -> None:
        """Restaura el estado del recurso tras liberación o enfriamiento."""
        with self._lock:
            if recurso in self._saturaciones:
                del self._saturaciones[recurso]
                logger.info("Recurso %s liberado de saturación.", recurso.value)

    def esta_saturado(self, recurso: ResourceKind) -> bool:
        with self._lock:
            return recurso in self._saturaciones

    def motivo_saturacion(self, recurso: ResourceKind) -> Optional[str]:
        with self._lock:
            return self._saturaciones.get(recurso)


class RetryPolicy:
    """Política de reintentos consciente de idempotencia y criticidad."""

    def __init__(self, max_reintentos: int = 2, espera_base_segundos: float = 0.5):
        self.max_reintentos = max(0, max_reintentos)
        self.espera_base_segundos = max(0.01, espera_base_segundos)

    def es_reintentable(self, error: AgentError, modo: IdempotencyMode) -> bool:
        """Determina si un fallo puede ser reintentado de forma segura y automática."""
        # 1. Si la operación es NON_IDEMPOTENT, ESTÁ TERMINANTEMENTE PROHIBIDO reintentar
        # ante errores de timeout o ambiguos para evitar duplicar mutaciones en el inventario
        if modo == IdempotencyMode.NON_IDEMPOTENT:
            return False

        # 2. Errores no reintentables bajo ninguna circunstancia
        ERRORES_NO_REINTENTABLES = {
            AgentErrorCode.INVALID_INPUT,
            AgentErrorCode.UNAUTHORIZED,
            AgentErrorCode.CHANNEL_NOT_ALLOWED,
            AgentErrorCode.INTENT_NOT_SUPPORTED,
            AgentErrorCode.CANCELLED,
            AgentErrorCode.CIRCUIT_OPEN,  # Si el circuito está abierto, reintentar agrava la cascada
        }

        if error is None or error.codigo in ERRORES_NO_REINTENTABLES:
            return False

        # 3. Errores transitorios que sí admiten reintento en operaciones READ_ONLY o IDEMPOTENT
        ERRORES_TRANSITORIOS = {
            AgentErrorCode.TIMEOUT,
            AgentErrorCode.RESOURCE_UNAVAILABLE,
            AgentErrorCode.DEPENDENCY_UNAVAILABLE,
            AgentErrorCode.RESOURCE_EXHAUSTED,
        }

        return error.codigo in ERRORES_TRANSITORIOS


class ResilientAgentExecutor:
    """Ejecutor protegido que envuelve cualquier AgentContract con garantías de resiliencia:

    1. Detección y fallo rápido por CircuitBreaker.
    2. Control de concurrencia y desborde de cola con ConcurrencyGovernor.
    3. Verificación de salud de recursos (CPU, GPU, VRAM) con ResourceTracker.
    4. Timeout real en hilo desacoplado.
    5. Identificación de operaciones ambiguas en mutaciones no idempotentes ante timeouts.
    """

    def __init__(
        self,
        agente: AgentContract,
        circuit_breaker: Optional[CircuitBreaker] = None,
        governor: Optional[ConcurrencyGovernor] = None,
        tracker: Optional[ResourceTracker] = None,
        retry_policy: Optional[RetryPolicy] = None,
    ):
        self.agente = agente
        self.circuit_breaker = circuit_breaker or CircuitBreaker(nombre=agente.metadata.id)
        self.governor = governor or ConcurrencyGovernor(
            max_concurrencia=agente.metadata.perfil_recursos.max_concurrencia,
            nombre=agente.metadata.id,
        )
        self.tracker = tracker or ResourceTracker()
        self.retry_policy = retry_policy or RetryPolicy()

    def ejecutar(
        self,
        entrada: AgentInput,
        timeout_override: Optional[float] = None,
        cancel_token: Optional[threading.Event] = None,
    ) -> AgentResult:
        """Ejecuta el agente aplicando las políticas de resiliencia."""
        inicio = time.perf_counter()
        correlation_id = entrada.contexto.correlation_id if (entrada and hasattr(entrada, "contexto")) else "unknown"

        # 1. Comprobar cancelación temprana
        if cancel_token is not None and cancel_token.is_set():
            duracion = (time.perf_counter() - inicio) * 1000.0
            error = AgentError(
                codigo=AgentErrorCode.CANCELLED,
                mensaje=f"Operación cancelada por el cliente antes de iniciar en agente '{self.agente.metadata.id}'.",
            )
            return AgentResult.fallo(
                error=error,
                correlation_id=correlation_id,
                duracion_ms=duracion,
                estado=ExecutionState.CANCELLED,
            )

        # 2. Comprobar Circuit Breaker
        if not self.circuit_breaker.puede_ejecutar():
            duracion = (time.perf_counter() - inicio) * 1000.0
            error = AgentError(
                codigo=AgentErrorCode.CIRCUIT_OPEN,
                mensaje=(
                    f"El circuito para el agente '{self.agente.metadata.id}' está ABIERTO "
                    f"debido a fallos persistentes. Solicitud rechazada tempranamente."
                ),
            )
            return AgentResult.fallo(
                error=error,
                correlation_id=correlation_id,
                duracion_ms=duracion,
            )

        # 3. Comprobar salud de recursos requeridos
        for recurso in self.agente.metadata.perfil_recursos.recursos_requeridos:
            if self.tracker.esta_saturado(recurso):
                duracion = (time.perf_counter() - inicio) * 1000.0
                motivo = self.tracker.motivo_saturacion(recurso)
                error = AgentError(
                    codigo=AgentErrorCode.RESOURCE_EXHAUSTED,
                    mensaje=(
                        f"El recurso '{recurso.value}' requerido por el agente '{self.agente.metadata.id}' "
                        f"se encuentra saturado o agotado ({motivo})."
                    ),
                    detalles={"recurso": recurso.value, "motivo": motivo},
                )
                return AgentResult.fallo(
                    error=error,
                    correlation_id=correlation_id,
                    duracion_ms=duracion,
                )

        # 4. Comprobar Concurrency Governor
        if not self.governor.intentar_adquirir():
            duracion = (time.perf_counter() - inicio) * 1000.0
            error = AgentError(
                codigo=AgentErrorCode.QUEUE_FULL,
                mensaje=(
                    f"Límite de concurrencia excedido para el agente '{self.agente.metadata.id}'. "
                    f"Ejecuciones activas: {self.governor.en_ejecucion}/{self.governor.max_concurrencia}."
                ),
                detalles={
                    "max_concurrencia": self.governor.max_concurrencia,
                    "en_ejecucion": self.governor.en_ejecucion,
                },
            )
            return AgentResult.fallo(
                error=error,
                correlation_id=correlation_id,
                duracion_ms=duracion,
            )

        # 5. Ejecutar con Timeout Real
        timeout = (
            timeout_override
            if timeout_override is not None and timeout_override > 0
            else self.agente.metadata.perfil_recursos.timeout_segundos
        )

        executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix=f"resilient-agent-{self.agente.metadata.id}",
        )
        liberar_en_finally = True

        try:
            futuro = executor.submit(self.agente.ejecutar, entrada)
            try:
                resultado = futuro.result(timeout=timeout)
            except concurrent.futures.TimeoutError:
                duracion = (time.perf_counter() - inicio) * 1000.0

                # 1. Señalizar cancelación cooperativa si se proporcionó un token
                if cancel_token is not None:
                    cancel_token.set()

                # 2. Desacoplar ejecutor sin bloquear al llamador en la salida
                executor.shutdown(wait=False, cancel_futures=True)

                # 3. Determinar si el timeout deja un resultado ambiguo (mutaciones no idempotentes)
                es_no_idempotente = (
                    self.agente.metadata.modo_idempotencia == IdempotencyMode.NON_IDEMPOTENT
                )

                codigo_error = (
                    AgentErrorCode.OPERATION_AMBIGUOUS
                    if es_no_idempotente
                    else AgentErrorCode.TIMEOUT
                )
                estado_final = (
                    ExecutionState.AMBIGUOUS
                    if es_no_idempotente
                    else ExecutionState.TIMED_OUT
                )

                msg = (
                    f"Tiempo de ejecución ({timeout}s) agotado en agente '{self.agente.metadata.id}'. "
                    f"{'ADVERTENCIA: Operación no idempotente de resultado ambiguo; no reintentar automáticamente.' if es_no_idempotente else ''}"
                )

                detalles = {
                    "timeout_segundos": timeout,
                    "modo_idempotencia": self.agente.metadata.modo_idempotencia.value,
                    "tarea_abandonada_en_hilo": True,
                }

                error = AgentError(
                    codigo=codigo_error,
                    mensaje=msg.strip(),
                    detalles=detalles,
                )

                self.circuit_breaker.registrar_fallo(codigo_error)

                # IMPORTANTE: En Python los hilos en ejecución no pueden ser cancelados forzosamente.
                # El hilo continúa ejecutándose en segundo plano hasta finalizar o alcanzar un timeout I/O.
                # Para evitar que solicitudes concurrentes desborden el recurso mientras el hilo abandonado
                # sigue activo, la ranura del governor se mantiene ocupada y se libera automáticamente
                # mediante callback cuando el hilo termine su ejecución real.
                liberar_en_finally = False
                futuro.add_done_callback(lambda _: self.governor.liberar())

                return AgentResult.fallo(
                    error=error,
                    correlation_id=correlation_id,
                    duracion_ms=duracion,
                    estado=estado_final,
                )

            # 6. Registrar en CircuitBreaker
            executor.shutdown(wait=False)
            if resultado.exitoso:
                self.circuit_breaker.registrar_exito()
            else:
                self.circuit_breaker.registrar_fallo(resultado.error.codigo if resultado.error else None)

            return resultado

        finally:
            if liberar_en_finally:
                self.governor.liberar()

    def ejecutar_con_reintentos(
        self,
        entrada: AgentInput,
        timeout_override: Optional[float] = None,
        cancel_token: Optional[threading.Event] = None,
    ) -> AgentResult:
        """Ejecuta aplicando reintentos si y solo si la política lo autoriza."""
        modo = self.agente.metadata.modo_idempotencia
        intentos = 0
        max_intentos = 1 + (self.retry_policy.max_reintentos if modo != IdempotencyMode.NON_IDEMPOTENT else 0)

        ultimo_resultado: Optional[AgentResult] = None

        while intentos < max_intentos:
            intentos += 1
            res = self.ejecutar(entrada, timeout_override=timeout_override, cancel_token=cancel_token)
            if res.exitoso:
                return res

            ultimo_resultado = res
            if not self.retry_policy.es_reintentable(res.error, modo):  # type: ignore
                break

            # Backoff simple
            time.sleep(self.retry_policy.espera_base_segundos * intentos)

        return ultimo_resultado  # type: ignore
