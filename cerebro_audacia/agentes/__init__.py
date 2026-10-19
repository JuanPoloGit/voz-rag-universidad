"""Subpaquete de agentes de Cerebro AudacIA.

Exporta el contrato base formal, esquemas tipados y primitivas de resiliencia.
"""

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
    DataAutonomyLevel,
    ExecutionState,
    IdempotencyMode,
    ResourceKind,
    ResourceProfile,
)
from .orquestador import OrquestadorAudacIA, OrquestadorResult
from .resiliencia import (
    CircuitBreaker,
    CircuitState,
    ConcurrencyGovernor,
    ResilientAgentExecutor,
    ResourceTracker,
    RetryPolicy,
)

__all__ = [
    # Orquestador y coordinación
    "OrquestadorAudacIA",
    "OrquestadorResult",
    # Contrato y datos
    "AgentContract",
    "AgentContext",
    "AgentError",
    "AgentErrorCode",
    "AgentExecutionError",
    "AgentInput",
    "AgentMetadata",
    "AgentOutput",
    "AgentResult",
    "ContractValidationError",
    "DataAutonomyLevel",
    "ExecutionState",
    "IdempotencyMode",
    "ResourceKind",
    "ResourceProfile",
    # Primitivas de resiliencia
    "CircuitBreaker",
    "CircuitState",
    "ConcurrencyGovernor",
    "ResilientAgentExecutor",
    "ResourceTracker",
    "RetryPolicy",
]
