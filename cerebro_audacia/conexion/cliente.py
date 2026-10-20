"""Cliente Oficial Único del Cerebro Central de AudacIA.

Este módulo es la ÚNICA interfaz autorizada para que los agentes y el orquestador
consuman inferencia de lenguaje natural.

Garantías de Arquitectura:
1. Principio de Proveedor Único: Todo agente consume exclusivamente este cliente.
2. Ocultamiento de Infraestructura: Los agentes desconocen rutas .gguf, capas GPU,
   parámetros CUDA y configuración de llama-server.
3. Bloqueo de Modelos Secundarios: Queda terminantemente prohibido que un agente
   especifique un modelo alternativo. Cualquier intento de pasar 'model', 'modelo',
   'gguf_path' o similares lanza ViolacionArquitecturaError.
4. Gobernanza de Recursos: Control centralizado de concurrencia, colas y Circuit Breaker.
"""

from __future__ import annotations

import collections
import json
import logging
import math
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

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

logger = logging.getLogger("cerebro_audacia.conexion")

# Parámetros estrictamente prohibidos para los llamadores (agentes)
PARAMETROS_PROHIBIDOS = frozenset({
    "model",
    "modelo",
    "model_path",
    "ruta_modelo",
    "modelo_ruta",
    "gguf",
    "gguf_path",
    "ruta_gguf",
    "weights",
    "pesos",
    "provider",
    "proveedor",
    "backend",
    "n_gpu_layers",
    "n_ctx",
})

_BLOQUE_JSON = re.compile(r"\{.*\}", re.DOTALL)


class CircuitState(str, Enum):
    CLOSED = "CLOSED"        # Tráfico normal
    OPEN = "OPEN"            # Rechazo inmediato por fallo persistente
    HALF_OPEN = "HALF_OPEN"  # Sondeo de prueba


@dataclass(frozen=True)
class RespuestaCerebroCentral:
    """Respuesta normalizada emitida por el Cerebro Central hacia los agentes."""

    texto: str
    tool_calls: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    datos_json: dict[str, Any] | None = None
    finish_reason: str = "stop"
    modelo_reportado: str = ""
    duracion_ms: float = 0.0
    tokens_completados: int = 0


class LlmCircuitBreaker:
    """Disyuntor thread-safe para aislar fallos del servidor LLM central."""

    def __init__(self, fallos_max: int = 3, tiempo_enfriamiento_s: float = 5.0):
        self._fallos_max = max(1, fallos_max)
        self._tiempo_enfriamiento_s = max(0.1, tiempo_enfriamiento_s)
        self._lock = threading.Lock()
        self._estado = CircuitState.CLOSED
        self._fallos_consecutivos = 0
        self._tiempo_apertura = 0.0
        self._sondeo_en_vuelo = False

    @property
    def estado(self) -> CircuitState:
        with self._lock:
            if self._estado == CircuitState.OPEN:
                if (time.monotonic() - self._tiempo_apertura) >= self._tiempo_enfriamiento_s:
                    self._estado = CircuitState.HALF_OPEN
            return self._estado

    def puede_ejecutar(self) -> bool:
        with self._lock:
            if self._estado == CircuitState.OPEN:
                if (time.monotonic() - self._tiempo_apertura) >= self._tiempo_enfriamiento_s:
                    self._estado = CircuitState.HALF_OPEN
                else:
                    return False

            if self._estado == CircuitState.CLOSED:
                return True

            if self._estado == CircuitState.HALF_OPEN:
                if not self._sondeo_en_vuelo:
                    self._sondeo_en_vuelo = True
                    return True
                return False

            return False

    def registrar_exito(self) -> None:
        with self._lock:
            self._fallos_consecutivos = 0
            self._estado = CircuitState.CLOSED
            self._sondeo_en_vuelo = False

    def registrar_fallo(self, exc: BaseException | None = None) -> None:
        with self._lock:
            self._sondeo_en_vuelo = False
            self._fallos_consecutivos += 1
            if self._fallos_consecutivos >= self._fallos_max or self._estado == CircuitState.HALF_OPEN:
                self._estado = CircuitState.OPEN
                self._tiempo_apertura = time.monotonic()


class LlmGovernor:
    """Gobernador central de concurrencia y cola de admisión para el Cerebro Central."""

    def __init__(self, max_concurrencia: int = 1, max_cola: int = 5, timeout_cola_s: float = 10.0):
        self._max_concurrencia = max(1, max_concurrencia)
        self._max_cola = max(0, max_cola)
        self._timeout_cola_s = max(0.1, timeout_cola_s)
        self._activas = 0
        self._cola = 0
        self._cond = threading.Condition(threading.Lock())

    @property
    def activas(self) -> int:
        with self._cond:
            return self._activas

    @property
    def en_cola(self) -> int:
        with self._cond:
            return self._cola

    @property
    def max_concurrencia(self) -> int:
        return self._max_concurrencia

    @contextmanager
    def adquirir(self) -> Iterator[None]:
        with self._cond:
            if self._activas < self._max_concurrencia:
                self._activas += 1
            else:
                if self._cola >= self._max_cola:
                    raise LlmSaturadoError(
                        f"Cerebro Central saturado: cola de admisión llena ({self._cola}/{self._max_cola} en espera)."
                    )

                self._cola += 1
                inicio_espera = time.monotonic()
                deadline = inicio_espera + self._timeout_cola_s

                while self._activas >= self._max_concurrencia:
                    restante = deadline - time.monotonic()
                    if restante <= 0:
                        self._cola -= 1
                        raise LlmColaExpiradaError(
                            f"Tiempo de espera en cola del Cerebro Central excedido ({self._timeout_cola_s:.1f}s)."
                        )
                    self._cond.wait(timeout=restante)

                self._cola -= 1
                self._activas += 1

        try:
            yield
        finally:
            with self._cond:
                self._activas -= 1
                self._cond.notify()


class ConfiguracionCerebroCentral:
    """Fuente única de configuración para la conexión con el Cerebro Central."""

    def __init__(self) -> None:
        self.url_chat = "http://127.0.0.1:8080/v1/chat/completions"
        self.url_salud = "http://127.0.0.1:8080/health"
        self.url_modelos = "http://127.0.0.1:8080/v1/models"
        self.timeout_conexion = 5.0
        self.timeout_lectura = 20.0
        self.timeout_cola = 10.0
        self.max_concurrencia = 1
        self.max_cola = 5
        self.modelo_por_defecto = "Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf"
        self.max_caracteres_prompt = 100_000
        self.n_ctx = 16384
        self.parallel = 2
        self.margen_seguridad_tokens = 128
        self._cargar_desde_infraestructura()

    @property
    def ventana_contexto_por_ranura(self) -> int:
        """Contexto utilizable asignado a cada ranura concurrente (n_ctx // parallel)."""
        ranuras = max(1, self.parallel)
        return max(512, self.n_ctx // ranuras)

    def _cargar_desde_infraestructura(self) -> None:
        # 1. Intentar leer config_llm.json oficial
        raiz = Path(__file__).resolve().parent.parent.parent
        ruta_cfg = raiz / "cerebro_audacia" / "proveedor_modelo" / "config_llm.json"

        if ruta_cfg.exists():
            try:
                with open(ruta_cfg, "r", encoding="utf-8") as f:
                    datos = json.load(f)
                    srv = datos.get("servicio", {})
                    self.url_chat = srv.get("url_chat", self.url_chat)
                    self.url_salud = srv.get("url_salud", self.url_salud)
                    self.url_modelos = srv.get("url_modelos", self.url_modelos)

                    perfiles = datos.get("perfiles", {})
                    perfil = perfiles.get("servidor_rtx5070ti") or perfiles.get("desarrollo_rtx2000ada") or {}
                    if perfil:
                        self.max_concurrencia = int(perfil.get("parallel", self.max_concurrencia))
                        self.parallel = self.max_concurrencia
                        self.n_ctx = int(perfil.get("n_ctx", self.n_ctx))
                        self.modelo_por_defecto = perfil.get("modelo_archivo", self.modelo_por_defecto)
            except Exception as e:
                logger.warning("No se pudo cargar config_llm.json (%s). Usando valores predeterminados.", e)

        # 2. Variables de entorno institucionales
        env_url = os.getenv("AUDACIA_LLM_URL") or os.getenv("LLAMA_SERVER_URL")
        if env_url:
            self.url_chat = env_url.strip()
            if self.url_chat.endswith("/v1/chat/completions"):
                base = self.url_chat[:-len("/v1/chat/completions")]
                self.url_salud = base + "/health"
                self.url_modelos = base + "/v1/models"

        if os.getenv("AUDACIA_LLM_MAX_CONCURRENCY"):
            try:
                self.max_concurrencia = int(os.environ["AUDACIA_LLM_MAX_CONCURRENCY"])
                self.parallel = self.max_concurrencia
            except ValueError:
                pass

        if os.getenv("AUDACIA_LLM_QUEUE_TIMEOUT"):
            try:
                self.timeout_cola = float(os.environ["AUDACIA_LLM_QUEUE_TIMEOUT"])
            except ValueError:
                pass

        if os.getenv("AUDACIA_LLM_MAX_CHARS"):
            try:
                self.max_caracteres_prompt = int(os.environ["AUDACIA_LLM_MAX_CHARS"])
            except ValueError:
                pass


class ClienteCerebroCentral:
    """Cliente oficial único para interactuar con el Cerebro Central de AudacIA."""

    def __init__(
        self,
        config: ConfiguracionCerebroCentral | None = None,
        max_concurrencia: int | None = None,
        max_cola: int | None = None,
        timeout_cola: float | None = None,
        resource_tracker: Any | None = None,
    ) -> None:
        self._cfg = config or ConfiguracionCerebroCentral()
        conc = max_concurrencia if max_concurrencia is not None else self._cfg.max_concurrencia
        cola = max_cola if max_cola is not None else self._cfg.max_cola
        t_cola = timeout_cola if timeout_cola is not None else self._cfg.timeout_cola

        self._governor = LlmGovernor(max_concurrencia=conc, max_cola=cola, timeout_cola_s=t_cola)
        self._circuit_breaker = LlmCircuitBreaker(fallos_max=3, tiempo_enfriamiento_s=5.0)
        self._modelo_activo_cache: str | None = None
        self._modelo_cache_lock = threading.Lock()
        self._resource_tracker = resource_tracker

        self._metricas_lock = threading.Lock()
        self._metricas = {
            "total_solicitudes": 0,
            "solicitudes_exitosas": 0,
            "rechazos_saturacion": 0,
            "limites_excedidos": 0,
            "timeouts": 0,
            "fallos_conexion": 0,
            "errores_oom": 0,
            "respuestas_invalidas": 0,
        }

    @property
    def governor(self) -> LlmGovernor:
        return self._governor

    @property
    def circuit_breaker(self) -> LlmCircuitBreaker:
        return self._circuit_breaker

    @property
    def config(self) -> ConfiguracionCerebroCentral:
        return self._cfg

    @property
    def resource_tracker(self) -> Any | None:
        return self._resource_tracker

    @resource_tracker.setter
    def resource_tracker(self, tracker: Any | None) -> None:
        self._resource_tracker = tracker

    def obtener_metricas(self) -> dict[str, Any]:
        """Retorna una instantánea thread-safe de las métricas acumuladas en memoria.
        
        Nota: Son contadores volátiles en memoria del proceso que se reinician al reiniciar la aplicación.
        """
        with self._metricas_lock:
            copia = dict(self._metricas)
        copia["circuit_breaker_estado"] = self._circuit_breaker.estado.value
        copia["ranuras_activas"] = self._governor.activas
        copia["en_cola"] = self._governor.en_cola
        copia["max_concurrencia"] = self._governor.max_concurrencia
        return copia

    def _validar_ausencia_parametros_prohibidos(self, kwargs: Mapping[str, Any]) -> None:
        """Asegura que ningún agente intente especificar un modelo o proveedor propio."""
        for clave in kwargs:
            if clave.lower() in PARAMETROS_PROHIBIDOS:
                raise ViolacionArquitecturaError(
                    f"Violación de Arquitectura de AudacIA: el parámetro '{clave}' está prohibido para agentes. "
                    "Todos los agentes deben consumir exclusivamente el Cerebro Central único. "
                    "Ningún agente puede seleccionar, instanciar o suministrar un modelo de lenguaje propio."
                )

    def obtener_modelo_activo(self, forzar_refresco: bool = False) -> str:
        """Consulta el identificador del modelo actualmente activo en el Cerebro Central.
        
        El modelo se descubre dinámicamente desde el servidor; ningún agente lo impone.
        """
        with self._modelo_cache_lock:
            if self._modelo_activo_cache and not forzar_refresco:
                return self._modelo_activo_cache

        try:
            req = urllib.request.Request(
                self._cfg.url_modelos,
                headers={"User-Agent": "AudacIA-CerebroCentral-Client/1.0"},
            )
            with urllib.request.urlopen(req, timeout=self._cfg.timeout_conexion) as resp:
                if resp.status == 200:
                    datos = json.loads(resp.read().decode("utf-8", errors="replace"))
                    modelos = datos.get("data", [])
                    if modelos and isinstance(modelos, list):
                        id_modelo = modelos[0].get("id")
                        if id_modelo:
                            with self._modelo_cache_lock:
                                self._modelo_activo_cache = id_modelo
                            return id_modelo
        except Exception:
            pass

        return self._cfg.modelo_por_defecto

    def comprobar_salud(self) -> dict[str, Any]:
        """Consulta el estado operativo del Cerebro Central."""
        req = urllib.request.Request(
            self._cfg.url_salud,
            headers={"User-Agent": "AudacIA-CerebroCentral-Client/1.0"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self._cfg.timeout_conexion) as resp:
                cuerpo = resp.read().decode("utf-8", errors="replace")
                datos = json.loads(cuerpo)
                return {
                    "disponible": resp.status == 200,
                    "estado": datos.get("status", "ok"),
                    "circuit_breaker": self._circuit_breaker.estado.value,
                    "ranuras_activas": self._governor.activas,
                    "en_cola": self._governor.en_cola,
                    "max_concurrencia": self._governor.max_concurrencia,
                }
        except Exception as e:
            return {
                "disponible": False,
                "estado": f"No disponible: {type(e).__name__}",
                "circuit_breaker": self._circuit_breaker.estado.value,
                "ranuras_activas": self._governor.activas,
                "en_cola": self._governor.en_cola,
                "max_concurrencia": self._governor.max_concurrencia,
            }

    def generar(
        self,
        mensajes: Sequence[dict[str, Any]],
        herramientas: Sequence[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        max_tokens: int = 512,
        temperatura: float = 0.3,
        formato_json: bool = False,
        **kwargs: Any,
    ) -> RespuestaCerebroCentral:
        """Punto de entrada universal para que los agentes soliciten razonamiento o lenguaje.
        
        Lanza ViolacionArquitecturaError si se intenta suministrar un modelo propio.
        Lanza LlmLimiteExcedidoError si el prompt o tokens exceden el presupuesto seguro.
        Lanza LlmSaturadoError si la cola está llena.
        LlmColaExpiradaError si expira la espera en cola.
        LlmNoDisponibleError si el circuit breaker está abierto.
        LlmOomError si ocurre fallo de memoria GPU en inferencia.
        LlmRespuestaInvalidaError si la respuesta está truncada o malformada.
        """
        with self._metricas_lock:
            self._metricas["total_solicitudes"] += 1

        # 1. Blindaje: rechazar tajantemente cualquier parámetro de modelo suministrado por el agente
        self._validar_ausencia_parametros_prohibidos(kwargs)

        if not mensajes:
            with self._metricas_lock:
                self._metricas["limites_excedidos"] += 1
            raise ValueError("La lista de mensajes no puede estar vacía.")

        # 2. Validación estricta de max_tokens
        if not isinstance(max_tokens, int) or max_tokens <= 0:
            with self._metricas_lock:
                self._metricas["limites_excedidos"] += 1
            raise ValueError(f"max_tokens debe ser un entero positivo mayor a cero (recibido: {max_tokens}).")

        # 3. Validación de límite de caracteres del prompt (primera barrera)
        total_caracteres = sum(len(str(m.get("content") or "")) for m in mensajes)
        if herramientas:
            total_caracteres += sum(len(json.dumps(h, ensure_ascii=False)) for h in herramientas)

        if total_caracteres > self._cfg.max_caracteres_prompt:
            with self._metricas_lock:
                self._metricas["limites_excedidos"] += 1
            raise LlmLimiteExcedidoError(
                f"El tamaño del prompt ({total_caracteres} caracteres) excede el límite máximo permitido "
                f"({self._cfg.max_caracteres_prompt} caracteres)."
            )

        # 4. Validación de presupuesto de contexto por ranura (segunda barrera)
        ventana_ranura = self._cfg.ventana_contexto_por_ranura
        presupuesto_util = max(256, ventana_ranura - self._cfg.margen_seguridad_tokens)

        if max_tokens > presupuesto_util:
            with self._metricas_lock:
                self._metricas["limites_excedidos"] += 1
            raise LlmLimiteExcedidoError(
                f"max_tokens solicitado ({max_tokens}) excede la ventana máxima utilizable por ranura "
                f"({presupuesto_util} tokens tras margen de seguridad de {self._cfg.margen_seguridad_tokens} tokens)."
            )

        # Estimación conservadora de tokens de entrada (~3.2 caracteres por token)
        tokens_entrada_estimados = math.ceil(total_caracteres / 3.2)
        if tokens_entrada_estimados + max_tokens > presupuesto_util:
            with self._metricas_lock:
                self._metricas["limites_excedidos"] += 1
            raise LlmLimiteExcedidoError(
                f"El presupuesto de contexto estimado ({tokens_entrada_estimados} tokens entrada + "
                f"{max_tokens} tokens salida = {tokens_entrada_estimados + max_tokens}) excede la ventana "
                f"utilizable por ranura ({presupuesto_util} tokens)."
            )

        # 5. Comprobar Circuit Breaker
        if not self._circuit_breaker.puede_ejecutar():
            with self._metricas_lock:
                self._metricas["fallos_conexion"] += 1
            raise LlmNoDisponibleError("Cerebro Central no disponible: Circuit Breaker en estado OPEN.")

        modelo_central = self.obtener_modelo_activo()

        payload: dict[str, Any] = {
            "model": modelo_central,
            "messages": list(mensajes),
            "max_tokens": max(16, max_tokens),
            "temperature": max(0.0, min(2.0, temperatura)),
            "stream": False,
        }

        if formato_json:
            payload["response_format"] = {"type": "json_object"}

        if herramientas:
            payload["tools"] = list(herramientas)

        if tool_choice:
            payload["tool_choice"] = tool_choice

        # Enviar petición bajo gobierno de concurrencia
        try:
            with self._governor.adquirir():
                t0 = time.perf_counter()
                req = urllib.request.Request(
                    self._cfg.url_chat,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": "AudacIA-CerebroCentral-Client/1.0",
                    },
                )

                try:
                    with urllib.request.urlopen(req, timeout=self._cfg.timeout_lectura) as resp:
                        if resp.status != 200:
                            self._circuit_breaker.registrar_fallo()
                            with self._metricas_lock:
                                self._metricas["fallos_conexion"] += 1
                            raise RuntimeError(f"Cerebro Central devolvió código HTTP {resp.status}")

                        cuerpo = resp.read().decode("utf-8", errors="replace")
                        try:
                            resp_json = json.loads(cuerpo)
                        except json.JSONDecodeError as jde:
                            self._circuit_breaker.registrar_fallo(jde)
                            with self._metricas_lock:
                                self._metricas["respuestas_invalidas"] += 1
                            raise LlmRespuestaInvalidaError(
                                f"Respuesta del Cerebro Central truncada o JSON malformado: {jde}"
                            ) from jde

                        self._circuit_breaker.registrar_exito()
                        with self._metricas_lock:
                            self._metricas["solicitudes_exitosas"] += 1

                except urllib.error.HTTPError as e:
                    cuerpo_error = ""
                    try:
                        cuerpo_error = e.read().decode("utf-8", errors="replace")
                    except Exception:
                        pass

                    if e.code == 429:
                        raise LlmSaturadoError("Cerebro Central saturado: servidor HTTP 429 Too Many Requests") from e

                    # Clasificación rigurosa de errores de memoria (CUDA OOM)
                    cuerpo_lower = cuerpo_error.lower()
                    es_oom = any(patron in cuerpo_lower for patron in [
                        "out of memory",
                        "cuda out of memory",
                        "failed to allocate",
                        "cudamalloc failed",
                        "not enough memory",
                        "out_of_memory",
                    ])

                    if es_oom:
                        self._circuit_breaker.registrar_fallo(e)
                        with self._metricas_lock:
                            self._metricas["errores_oom"] += 1
                        if self._resource_tracker is not None:
                            try:
                                from cerebro_audacia.agentes.esquemas import ResourceKind
                                self._resource_tracker.marcar_saturado(
                                    ResourceKind.VRAM,
                                    f"CUDA OOM detectado en respuesta HTTP {e.code}: {cuerpo_error[:200]}"
                                )
                            except Exception:
                                pass
                        raise LlmOomError(
                            f"Fallo de memoria en inferencia (CUDA OOM) [HTTP {e.code}]: {cuerpo_error[:300]}"
                        ) from e

                    self._circuit_breaker.registrar_fallo(e)
                    with self._metricas_lock:
                        self._metricas["fallos_conexion"] += 1
                    raise RuntimeError(f"Cerebro Central devolvió código HTTP {e.code}: {cuerpo_error[:300]}") from e

                except urllib.error.URLError as e:
                    if isinstance(e.reason, (TimeoutError, socket.timeout)) or "timed out" in str(e).lower():
                        self._circuit_breaker.registrar_fallo(e)
                        with self._metricas_lock:
                            self._metricas["timeouts"] += 1
                        raise TimeoutError(f"Timeout de conexión con el Cerebro Central: {e}") from e
                    self._circuit_breaker.registrar_fallo(e)
                    with self._metricas_lock:
                        self._metricas["fallos_conexion"] += 1
                    raise ConnectionError(f"Error de conexión con el Cerebro Central en {self._cfg.url_chat}: {e}") from e

                except (TimeoutError, socket.timeout) as e:
                    self._circuit_breaker.registrar_fallo(e)
                    with self._metricas_lock:
                        self._metricas["timeouts"] += 1
                    raise TimeoutError(f"Timeout de lectura/conexión con el Cerebro Central ({self._cfg.timeout_lectura}s): {e}") from e

                except (ConnectionError, OSError) as e:
                    self._circuit_breaker.registrar_fallo(e)
                    with self._metricas_lock:
                        self._metricas["fallos_conexion"] += 1
                    raise ConnectionError(f"Error de conexión con el Cerebro Central en {self._cfg.url_chat}: {e}") from e

        except LlmSaturadoError:
            with self._metricas_lock:
                self._metricas["rechazos_saturacion"] += 1
            raise
        except LlmColaExpiradaError:
            with self._metricas_lock:
                self._metricas["timeouts"] += 1
            raise

        duracion_ms = (time.perf_counter() - t0) * 1000.0
        choices = resp_json.get("choices", [])
        if not choices:
            raise RuntimeError("El Cerebro Central no devolvió opciones de respuesta.")

        eleccion = choices[0]
        mensaje_resp = eleccion.get("message", {})
        texto = mensaje_resp.get("content") or ""
        finish_reason = eleccion.get("finish_reason", "stop")
        tool_calls_raw = mensaje_resp.get("tool_calls", [])
        tokens_completados = resp_json.get("usage", {}).get("completion_tokens", 0)

        datos_json = None
        if formato_json and texto:
            try:
                datos_json = json.loads(texto)
            except json.JSONDecodeError:
                bloque = _BLOQUE_JSON.search(texto)
                if bloque:
                    try:
                        datos_json = json.loads(bloque.group(0))
                    except Exception:
                        pass

        return RespuestaCerebroCentral(
            texto=texto,
            tool_calls=tuple(tool_calls_raw) if tool_calls_raw else (),
            datos_json=datos_json,
            finish_reason=finish_reason,
            modelo_reportado=modelo_central,
            duracion_ms=duracion_ms,
            tokens_completados=tokens_completados,
        )

    def create_chat_completion(
        self,
        messages: list[dict[str, Any]],
        max_tokens: int = 512,
        temperature: float = 0.3,
        stream: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Método de compatibilidad con interfaz OpenAI / OrquestadorAudacIA."""
        self._validar_ausencia_parametros_prohibidos(kwargs)

        herramientas = kwargs.get("tools")
        tool_choice = kwargs.get("tool_choice")
        formato = kwargs.get("response_format", {})
        formato_json = bool(formato and formato.get("type") == "json_object")

        respuesta = self.generar(
            mensajes=messages,
            herramientas=herramientas,
            tool_choice=tool_choice,
            max_tokens=max_tokens,
            temperatura=temperature,
            formato_json=formato_json,
        )

        msg_dict: dict[str, Any] = {"role": "assistant", "content": respuesta.texto}
        if respuesta.tool_calls:
            msg_dict["tool_calls"] = list(respuesta.tool_calls)

        return {
            "id": f"chatcmpl-audacia-{int(time.time()*1000)}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": respuesta.modelo_reportado,
            "choices": [
                {
                    "index": 0,
                    "message": msg_dict,
                    "finish_reason": respuesta.finish_reason,
                }
            ],
            "usage": {
                "completion_tokens": respuesta.tokens_completados,
                "total_tokens": respuesta.tokens_completados,
            },
        }


# Instancia singleton compartida
_CLIENTE_CENTRAL_GLOBAL: ClienteCerebroCentral | None = None
_CLIENTE_CENTRAL_LOCK = threading.Lock()


def obtener_cliente_cerebro_central(config: ConfiguracionCerebroCentral | None = None) -> ClienteCerebroCentral:
    """Retorna la instancia global compartida del Cliente del Cerebro Central."""
    global _CLIENTE_CENTRAL_GLOBAL
    with _CLIENTE_CENTRAL_LOCK:
        if _CLIENTE_CENTRAL_GLOBAL is None:
            _CLIENTE_CENTRAL_GLOBAL = ClienteCerebroCentral(config=config)
        return _CLIENTE_CENTRAL_GLOBAL
