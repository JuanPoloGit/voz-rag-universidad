"""Esquemas tipados, modelos de datos y garantías de resiliencia para AudacIA.

Utiliza exclusivamente la biblioteca estándar de Python (dataclasses, enum, typing, uuid, datetime, types).
Garantiza validación estricta en tiempo de ejecución, inmutabilidad profunda (deep immutability)
con MappingProxyType, sanitización de datos sensibles y serialización robusta.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Optional, Sequence, Union

# Expresión regular para identificadores válidos de agente: minúsculas, números, guiones y guiones bajos
_SLUG_REGEX = re.compile(r"^[a-z0-9_-]+$")

# Palabras clave sensibles para sanitización automática de detalles de error
_SENSITIVE_KEYS = frozenset({
    "password", "token", "secret", "key", "dsn", "authorization",
    "auth", "credential", "private", "access_key", "api_key"
})


def _make_readonly_mapping(data: Optional[Mapping[str, Any]]) -> MappingProxyType:
    """Convierte un diccionario o mapeo en una vista inmutable de sólo lectura."""
    if data is None:
        return MappingProxyType({})
    if not isinstance(data, (dict, MappingProxyType)):
        raise ContractValidationError(f"Se esperaba dict o Mapping, recibido {type(data).__name__}.")
    return MappingProxyType(dict(data))


def _sanitize_details(detalles: Optional[Mapping[str, Any]]) -> Optional[dict[str, Any]]:
    """Sanitiza diccionarios de detalles eliminando o enmascarando información sensible."""
    if detalles is None:
        return None
    sanitizado: dict[str, Any] = {}
    for k, v in detalles.items():
        k_str = str(k).lower()
        if any(sens in k_str for sens in _SENSITIVE_KEYS):
            sanitizado[str(k)] = "[PROTEGIDO]"
        elif isinstance(v, Mapping):
            sanitizado[str(k)] = _sanitize_details(v)
        else:
            sanitizado[str(k)] = v
    return sanitizado


class DataAutonomyLevel(str, Enum):
    """Nivel de autonomía de acceso y modificación de datos para un agente.

    Conforme a ADR-001 (sección 5.2), ningún agente puede tener autonomía
    de mutación arbitraria ('ARBITRARY_MUTATION').
    """

    READ_ONLY = "READ_ONLY"
    PROPOSAL_BASED = "PROPOSAL_BASED"


class IdempotencyMode(str, Enum):
    """Semántica de repetición segura de una operación.

    Crucial para la gobernanza de reintentos y tolerancia a timeouts ambiguos:
    - READ_ONLY: Libre de efectos secundarios. Reintento automático seguro.
    - IDEMPOTENT: Modifica estado pero repetir la misma operación produce el mismo resultado. Reintento seguro.
    - NON_IDEMPOTENT: Mutación transaccional única. NUNCA reintentar automáticamente ante fallos ambiguos.
    """

    READ_ONLY = "READ_ONLY"
    IDEMPOTENT = "IDEMPOTENT"
    NON_IDEMPOTENT = "NON_IDEMPOTENT"


class ResourceKind(str, Enum):
    """Clase de recursos del sistema requeridos por un agente."""

    CPU = "CPU"
    GPU = "GPU"
    RAM = "RAM"
    VRAM = "VRAM"
    DISK = "DISK"
    NETWORK = "NETWORK"


class ExecutionState(str, Enum):
    """Estados del ciclo de vida de ejecución de una tarea entre agentes."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"
    AMBIGUOUS = "AMBIGUOUS"  # Desconexión o timeout durante mutación de resultado desconocido


class AgentErrorCode(str, Enum):
    """Códigos de error estructurados, normalizados y seguros de la plataforma."""

    INVALID_INPUT = "INVALID_INPUT"
    UNAUTHORIZED = "UNAUTHORIZED"
    CHANNEL_NOT_ALLOWED = "CHANNEL_NOT_ALLOWED"
    INTENT_NOT_SUPPORTED = "INTENT_NOT_SUPPORTED"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    TIMEOUT = "TIMEOUT"
    RESOURCE_UNAVAILABLE = "RESOURCE_UNAVAILABLE"
    RESOURCE_EXHAUSTED = "RESOURCE_EXHAUSTED"  # GPU VRAM o memoria RAM agotada
    QUEUE_FULL = "QUEUE_FULL"  # Saturación de concurrencia
    CIRCUIT_OPEN = "CIRCUIT_OPEN"  # Circuit breaker activo para contener cascadas
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"  # STT, LLM u otro agente no responde
    OPERATION_AMBIGUOUS = "OPERATION_AMBIGUOUS"  # Pérdida de enlace durante mutación no idempotente
    CANCELLED = "CANCELLED"  # Tarea abortada por desconexión de cliente
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ContractValidationError(ValueError):
    """Excepción lanzada cuando los datos de entrada o metadatos violan el contrato."""

    def __init__(
        self,
        mensaje: str,
        codigo: AgentErrorCode = AgentErrorCode.INVALID_INPUT,
        detalles: Optional[Mapping[str, Any]] = None,
    ):
        super().__init__(mensaje)
        self.codigo = codigo
        self.detalles = _sanitize_details(detalles) or {}


class AgentExecutionError(RuntimeError):
    """Excepción controlada dentro de la lógica del agente."""

    def __init__(
        self,
        mensaje_seguro: str,
        codigo: AgentErrorCode = AgentErrorCode.EXECUTION_FAILED,
        detalles: Optional[Mapping[str, Any]] = None,
    ):
        super().__init__(mensaje_seguro)
        self.mensaje_seguro = mensaje_seguro
        self.codigo = codigo
        self.detalles = _sanitize_details(detalles) or {}


@dataclass(frozen=True)
class ResourceProfile:
    """Perfil declarativo de consumo de recursos y límites operativos de un agente."""

    recursos_requeridos: tuple[ResourceKind, ...] = (ResourceKind.CPU,)
    max_concurrencia: int = 1
    max_tamano_entrada_bytes: int = 65536  # 64 KB por defecto
    timeout_segundos: float = 30.0
    soporta_cancelacion: bool = False

    def __post_init__(self):
        if not self.recursos_requeridos:
            raise ContractValidationError("recursos_requeridos no puede estar vacío.")

        if self.max_concurrencia < 1:
            raise ContractValidationError("max_concurrencia debe ser al menos 1.")

        if self.max_tamano_entrada_bytes < 1:
            raise ContractValidationError("max_tamano_entrada_bytes debe ser mayor que 0.")

        if self.timeout_segundos <= 0:
            raise ContractValidationError("timeout_segundos debe ser mayor que 0.")

        # Invariante arquitectónica: ningún agente puede reservar VRAM para un LLM propio
        if ResourceKind.VRAM in self.recursos_requeridos and self.max_concurrencia > 2:
            raise ContractValidationError(
                "Violación de Arquitectura de AudacIA: la memoria VRAM es administrada centralmente. "
                "Ningún agente individual puede declarar alta concurrencia o reserva privada sobre VRAM."
            )

        object.__setattr__(
            self,
            "recursos_requeridos",
            tuple(r if isinstance(r, ResourceKind) else ResourceKind(str(r)) for r in self.recursos_requeridos)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "recursos_requeridos": [r.value for r in self.recursos_requeridos],
            "max_concurrencia": self.max_concurrencia,
            "max_tamano_entrada_bytes": self.max_tamano_entrada_bytes,
            "timeout_segundos": self.timeout_segundos,
            "soporta_cancelacion": self.soporta_cancelacion,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ResourceProfile:
        try:
            return cls(
                recursos_requeridos=tuple(ResourceKind(r) for r in data["recursos_requeridos"]),
                max_concurrencia=int(data.get("max_concurrencia", 1)),
                max_tamano_entrada_bytes=int(data.get("max_tamano_entrada_bytes", 65536)),
                timeout_segundos=float(data.get("timeout_segundos", 30.0)),
                soporta_cancelacion=bool(data.get("soporta_cancelacion", False)),
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise ContractValidationError(f"Datos inválidos para ResourceProfile: {exc}") from exc


@dataclass(frozen=True)
class AgentMetadata:
    """Metadatos declarativos de identidad, versión, capacidades y gobernanza del agente."""

    id: str
    nombre: str
    version: str
    descripcion: str
    nivel_autonomia: DataAutonomyLevel
    canales_permitidos: tuple[str, ...]
    requiere_autenticacion: bool
    capacidades: tuple[str, ...] = field(default_factory=tuple)
    modalidades_salida: tuple[str, ...] = field(default_factory=lambda: ("texto",))
    perfil_recursos: ResourceProfile = field(default_factory=ResourceProfile)
    modo_idempotencia: IdempotencyMode = IdempotencyMode.READ_ONLY
    dependencias: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self):
        # 1. Validar identificador único
        if not isinstance(self.id, str) or not _SLUG_REGEX.match(self.id):
            raise ContractValidationError(
                f"El ID del agente '{self.id}' es inválido. Debe ser una cadena en minúsculas (a-z, 0-9, _, -).",
                codigo=AgentErrorCode.INVALID_INPUT,
            )

        # 2. Validar nombre y versión
        if not isinstance(self.nombre, str) or not self.nombre.strip():
            raise ContractValidationError("El nombre del agente no puede estar vacío.")

        if not isinstance(self.version, str) or not self.version.strip():
            raise ContractValidationError("La versión del agente no puede estar vacía.")

        # 3. Validar nivel de autonomía
        if not isinstance(self.nivel_autonomia, DataAutonomyLevel):
            raise ContractValidationError(
                f"Nivel de autonomía inválido: {self.nivel_autonomia}. Debe ser DataAutonomyLevel."
            )

        # 4. Validar canales
        if not self.canales_permitidos or not isinstance(self.canales_permitidos, (tuple, list)):
            raise ContractValidationError("El agente debe declarar al menos un canal permitido.")

        # 5. Validar coherencia entre autonomía e idempotencia
        if self.nivel_autonomia == DataAutonomyLevel.READ_ONLY and self.modo_idempotencia == IdempotencyMode.NON_IDEMPOTENT:
            raise ContractValidationError(
                "Incoherencia arquitectónica: un agente READ_ONLY no puede declararse NON_IDEMPOTENT."
            )

        # 6. Blindaje: rechazar dependencias que intenten cargar LLMs locales independientes
        PATRONES_PROHIBIDOS_LLM = ("llama-server", ".gguf", "llama_cpp", "automodelforcausallm", "transformers.pipeline")
        for dep in self.dependencias:
            dep_limpia = str(dep).lower()
            if any(pat in dep_limpia for pat in PATRONES_PROHIBIDOS_LLM):
                raise ContractValidationError(
                    f"Violación de Arquitectura de AudacIA en agente '{self.id}': "
                    f"la dependencia declarada '{dep}' viola la regla de Cerebro Central único. "
                    "Los agentes no pueden cargar ni administrar modelos de lenguaje locales."
                )

        # Asegurar conversión a tuplas inmutables
        object.__setattr__(self, "canales_permitidos", tuple(str(c).strip() for c in self.canales_permitidos if str(c).strip()))
        object.__setattr__(self, "capacidades", tuple(str(c).strip() for c in self.capacidades if str(c).strip()))
        object.__setattr__(self, "modalidades_salida", tuple(str(m).strip() for m in self.modalidades_salida if str(m).strip()))
        object.__setattr__(self, "dependencias", tuple(str(d).strip() for d in self.dependencias if str(d).strip()))


@dataclass(frozen=True)
class AgentContext:
    """Contexto operativo y de trazabilidad que acompaña a cada solicitud."""

    correlation_id: str
    canal: str
    usuario_id: Optional[str] = None
    roles: tuple[str, ...] = field(default_factory=tuple)
    timestamp_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadatos: Mapping[str, Any] = field(default_factory=dict)
    es_confiable: bool = False  # True únicamente cuando el contexto fue emitido por gateway autenticado

    def __post_init__(self):
        if not isinstance(self.correlation_id, str) or not self.correlation_id.strip():
            raise ContractValidationError("correlation_id no puede estar vacío.")

        if not isinstance(self.canal, str) or not self.canal.strip():
            raise ContractValidationError("canal no puede estar vacío.")

        if isinstance(self.roles, list):
            object.__setattr__(self, "roles", tuple(self.roles))

        # Inmutabilidad profunda para metadatos
        object.__setattr__(self, "metadatos", _make_readonly_mapping(self.metadatos))

    @classmethod
    def crear(
        cls,
        canal: str,
        usuario_id: Optional[str] = None,
        roles: Sequence[str] = (),
        correlation_id: Optional[str] = None,
        metadatos: Optional[Mapping[str, Any]] = None,
        es_confiable: bool = False,
    ) -> AgentContext:
        """Fábrica conveniente que genera correlation_id y timestamp_utc automáticamente."""
        cid = correlation_id.strip() if correlation_id and correlation_id.strip() else str(uuid.uuid4())
        return cls(
            correlation_id=cid,
            canal=canal.strip(),
            usuario_id=str(usuario_id).strip() if usuario_id else None,
            roles=tuple(roles),
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            metadatos=dict(metadatos or {}),
            es_confiable=es_confiable,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "correlation_id": self.correlation_id,
            "canal": self.canal,
            "usuario_id": self.usuario_id,
            "roles": list(self.roles),
            "timestamp_utc": self.timestamp_utc,
            "metadatos": dict(self.metadatos),
            "es_confiable": self.es_confiable,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> AgentContext:
        try:
            return cls(
                correlation_id=str(data["correlation_id"]).strip(),
                canal=str(data["canal"]).strip(),
                usuario_id=str(data["usuario_id"]).strip() if data.get("usuario_id") else None,
                roles=tuple(data.get("roles", ())),
                timestamp_utc=str(data.get("timestamp_utc", "")),
                metadatos=dict(data.get("metadatos", {})),
                es_confiable=bool(data.get("es_confiable", False)),
            )
        except (KeyError, TypeError) as exc:
            raise ContractValidationError(f"Datos inválidos para AgentContext: campo faltante o erróneo {exc}") from exc


@dataclass(frozen=True)
class AgentInput:
    """Mensaje estructurado de entrada para la ejecución de un agente."""

    contenido: str
    contexto: AgentContext
    intencion: Optional[str] = None
    parametros: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.contenido, str):
            raise ContractValidationError(f"contenido debe ser str, recibido {type(self.contenido).__name__}.")

        if not isinstance(self.contexto, AgentContext):
            raise ContractValidationError(f"contexto debe ser AgentContext, recibido {type(self.contexto).__name__}.")

        if self.intencion is not None and not isinstance(self.intencion, str):
            raise ContractValidationError("intencion debe ser str o None.")

        # Inmutabilidad profunda para parametros
        object.__setattr__(self, "parametros", _make_readonly_mapping(self.parametros))

    def to_dict(self) -> dict[str, Any]:
        return {
            "contenido": self.contenido,
            "intencion": self.intencion,
            "parametros": dict(self.parametros),
            "contexto": self.contexto.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> AgentInput:
        try:
            return cls(
                contenido=str(data["contenido"]),
                intencion=str(data["intencion"]) if data.get("intencion") else None,
                parametros=dict(data.get("parametros", {})),
                contexto=AgentContext.from_dict(data["contexto"]),
            )
        except (KeyError, TypeError) as exc:
            raise ContractValidationError(f"Datos inválidos para AgentInput: {exc}") from exc


@dataclass(frozen=True)
class AgentError:
    """Representación formal y segura de un fallo en la ejecución del agente."""

    codigo: AgentErrorCode
    mensaje: str
    detalles: Optional[Mapping[str, Any]] = None

    def __post_init__(self):
        if not isinstance(self.codigo, AgentErrorCode):
            raise ContractValidationError(f"codigo debe ser AgentErrorCode, recibido {type(self.codigo).__name__}.")

        if not isinstance(self.mensaje, str) or not self.mensaje.strip():
            raise ContractValidationError("El mensaje de error no puede estar vacío.")

        # Sanitización y congelamiento de detalles
        sanitizado = _sanitize_details(self.detalles)
        object.__setattr__(self, "detalles", _make_readonly_mapping(sanitizado) if sanitizado is not None else None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "codigo": self.codigo.value,
            "mensaje": self.mensaje,
            "detalles": dict(self.detalles) if self.detalles is not None else None,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> AgentError:
        try:
            return cls(
                codigo=AgentErrorCode(data["codigo"]),
                mensaje=str(data["mensaje"]),
                detalles=dict(data["detalles"]) if data.get("detalles") is not None else None,
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise ContractValidationError(f"Datos inválidos para AgentError: {exc}") from exc


@dataclass(frozen=True)
class AgentOutput:
    """Respuesta generada por la ejecución exitosa de un agente."""

    respuesta: str
    datos: Mapping[str, Any] = field(default_factory=dict)
    modalidad: str = "texto"
    propuesta: Optional[Mapping[str, Any]] = None

    def __post_init__(self):
        if not isinstance(self.respuesta, str):
            raise ContractValidationError(f"respuesta debe ser str, recibido {type(self.respuesta).__name__}.")

        if not isinstance(self.modalidad, str) or not self.modalidad.strip():
            raise ContractValidationError("modalidad no puede estar vacía.")

        # Inmutabilidad profunda
        object.__setattr__(self, "datos", _make_readonly_mapping(self.datos))
        object.__setattr__(self, "propuesta", _make_readonly_mapping(self.propuesta) if self.propuesta is not None else None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "respuesta": self.respuesta,
            "datos": dict(self.datos),
            "modalidad": self.modalidad,
            "propuesta": dict(self.propuesta) if self.propuesta is not None else None,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> AgentOutput:
        try:
            return cls(
                respuesta=str(data["respuesta"]),
                datos=dict(data.get("datos", {})),
                modalidad=str(data.get("modalidad", "texto")),
                propuesta=dict(data["propuesta"]) if data.get("propuesta") is not None else None,
            )
        except (KeyError, TypeError) as exc:
            raise ContractValidationError(f"Datos inválidos para AgentOutput: {exc}") from exc


@dataclass(frozen=True)
class AgentResult:
    """Resultado unificado e inequívoco de la ejecución de un agente.

    Invariante crítico:
    - Si exitoso es True: salida DEBE ser AgentOutput y error DEBE ser None.
    - Si exitoso es False: error DEBE ser AgentError y salida DEBE ser None.
    """

    exitoso: bool
    correlation_id: str
    estado: ExecutionState = ExecutionState.COMPLETED
    salida: Optional[AgentOutput] = None
    error: Optional[AgentError] = None
    duracion_ms: float = 0.0

    def __post_init__(self):
        if not isinstance(self.correlation_id, str) or not self.correlation_id.strip():
            raise ContractValidationError("correlation_id no puede estar vacío.")

        if not isinstance(self.estado, ExecutionState):
            raise ContractValidationError(f"estado debe ser ExecutionState, recibido {type(self.estado).__name__}.")

        if self.exitoso:
            if self.salida is None or not isinstance(self.salida, AgentOutput):
                raise ContractValidationError("Un AgentResult exitoso exige una salida de tipo AgentOutput.")
            if self.error is not None:
                raise ContractValidationError("Un AgentResult exitoso no puede contener un error.")
            if self.estado != ExecutionState.COMPLETED:
                object.__setattr__(self, "estado", ExecutionState.COMPLETED)
        else:
            if self.error is None or not isinstance(self.error, AgentError):
                raise ContractValidationError("Un AgentResult fallido exige un error de tipo AgentError.")
            if self.salida is not None:
                raise ContractValidationError("Un AgentResult fallido no puede contener una salida.")
            if self.estado == ExecutionState.COMPLETED:
                # Inferir estado adecuado según el código de error
                if self.error.codigo == AgentErrorCode.TIMEOUT:
                    object.__setattr__(self, "estado", ExecutionState.TIMED_OUT)
                elif self.error.codigo == AgentErrorCode.CANCELLED:
                    object.__setattr__(self, "estado", ExecutionState.CANCELLED)
                elif self.error.codigo == AgentErrorCode.OPERATION_AMBIGUOUS:
                    object.__setattr__(self, "estado", ExecutionState.AMBIGUOUS)
                else:
                    object.__setattr__(self, "estado", ExecutionState.FAILED)

        if self.duracion_ms < 0:
            raise ContractValidationError("duracion_ms no puede ser negativa.")

    @classmethod
    def exito(
        cls,
        salida: AgentOutput,
        correlation_id: str,
        duracion_ms: float = 0.0,
    ) -> AgentResult:
        """Crea un resultado exitoso que garantiza la invariante."""
        return cls(
            exitoso=True,
            correlation_id=correlation_id,
            estado=ExecutionState.COMPLETED,
            salida=salida,
            error=None,
            duracion_ms=max(0.0, float(duracion_ms)),
        )

    @classmethod
    def fallo(
        cls,
        error: AgentError,
        correlation_id: str,
        duracion_ms: float = 0.0,
        estado: Optional[ExecutionState] = None,
    ) -> AgentResult:
        """Crea un resultado fallido que garantiza la invariante."""
        if estado is None:
            if error.codigo == AgentErrorCode.TIMEOUT:
                estado = ExecutionState.TIMED_OUT
            elif error.codigo == AgentErrorCode.CANCELLED:
                estado = ExecutionState.CANCELLED
            elif error.codigo == AgentErrorCode.OPERATION_AMBIGUOUS:
                estado = ExecutionState.AMBIGUOUS
            else:
                estado = ExecutionState.FAILED

        return cls(
            exitoso=False,
            correlation_id=correlation_id,
            estado=estado,
            salida=None,
            error=error,
            duracion_ms=max(0.0, float(duracion_ms)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "exitoso": self.exitoso,
            "correlation_id": self.correlation_id,
            "estado": self.estado.value,
            "salida": self.salida.to_dict() if self.salida else None,
            "error": self.error.to_dict() if self.error else None,
            "duracion_ms": self.duracion_ms,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> AgentResult:
        try:
            exitoso = bool(data["exitoso"])
            cid = str(data["correlation_id"]).strip()
            dur = float(data.get("duracion_ms", 0.0))
            estado = ExecutionState(data["estado"]) if "estado" in data else None

            # Validación estricta de coherencia en el diccionario
            if exitoso:
                if data.get("salida") is None:
                    raise ContractValidationError("Diccionario inválido: exitoso=True pero 'salida' es None.")
                if data.get("error") is not None:
                    raise ContractValidationError("Diccionario inválido: exitoso=True pero 'error' está presente.")
                return cls.exito(
                    salida=AgentOutput.from_dict(data["salida"]),
                    correlation_id=cid,
                    duracion_ms=dur,
                )
            else:
                if data.get("error") is None:
                    raise ContractValidationError("Diccionario inválido: exitoso=False pero 'error' es None.")
                if data.get("salida") is not None:
                    raise ContractValidationError("Diccionario inválido: exitoso=False pero 'salida' está presente.")
                return cls.fallo(
                    error=AgentError.from_dict(data["error"]),
                    correlation_id=cid,
                    duracion_ms=dur,
                    estado=estado,
                )
        except (KeyError, TypeError) as exc:
            raise ContractValidationError(f"Datos inválidos para AgentResult: {exc}") from exc
