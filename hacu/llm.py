"""Cliente de inferencia local HTTP para el servidor central de AudacIA.

Unico punto de acceso al modelo LLM para el agente visitante y componentes asociados.
Se conecta mediante peticiones HTTP (OpenAI-compatible) al demonio central `llama-server.exe`
(por defecto http://127.0.0.1:8080/v1/chat/completions), evitando cargar pesos duplicados
en la memoria GPU/RAM.

Implementa garantías de resiliencia y gobernanza de sobrecarga:
1. Concurrency Governor con límite de ranuras concurrentes y cola acotada.
2. Circuit Breaker para fallo rápido (0ms) ante caída persistente del servidor.
3. Timeouts estrictos de conexión, espera en cola y streaming.
4. Telemetría y métricas de solicitudes activas, colas, rechazos, timeouts y latencias.
5. Cancelación limpia y cierre de socket ante desconexión del cliente (GeneratorExit).
6. Tolerancia a respuestas vacías, streaming parcial y JSON malformado.

Utiliza exclusivamente la biblioteca estándar de Python (urllib.request, json, time, etc.)
sin requerir dependencias externas adicionales.
"""

from __future__ import annotations

import json
import logging
import os
import re
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .config import PROJECT_ROOT, ModelConfig

Mensaje = dict[str, str]
_BLOQUE_JSON = re.compile(r"\{.*\}", re.DOTALL)


def detectar_gpu() -> str | None:
    """Nombre de la GPU segun nvidia-smi, o None. Evita depender de torch (~2.5 GB)."""
    try:
        salida = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    primera = salida.stdout.strip().splitlines()
    return primera[0].strip() if primera else None


class Aliento(str, Enum):
    """Cuanto aire tiene el turno. Lo decide lo que PIDEN, no lo que escriben.

    Una pregunta de tres palabras puede necesitar seis frases ("¿y Orion?") y un
    parrafo de agradecimiento se contesta con una. Atar el techo a la longitud de
    la entrada es justo el error que hacia que un "gracias, ha sido muy
    interesante" recibiera cuatro parrafos.
    """

    BREVE = "BREVE"      # turno social: cierre, elogio, acuse de recibo
    NORMAL = "NORMAL"    # el caso corriente de tarima
    EXTENSO = "EXTENSO"  # piden desarrollo o catalogo entero


# ------------------------------------------------------------------ excepciones

class LlmSaturadoError(Exception):
    """Lanzada inmediatamente cuando la cola máxima de solicitudes está llena."""
    pass


class LlmColaExpiradaError(TimeoutError):
    """Lanzada cuando una solicitud espera en cola más del tiempo máximo permitido."""
    pass


class LlmNoDisponibleError(ConnectionError):
    """Lanzada cuando el CircuitBreaker está OPEN debido a caídas consecutivas del LLM."""
    pass


# ------------------------------------------------------------------ resiliencia

import collections


class CircuitState(str, Enum):
    CLOSED = "CLOSED"      # Tráfico normal
    OPEN = "OPEN"          # Rechazo inmediato por fallo persistente
    HALF_OPEN = "HALF_OPEN"  # Sondeo de recuperación


class LlmCircuitBreaker:
    """Disyuntor thread-safe para aislar fallos del servidor LLM y evitar contención en cascada.

    Protegido contra carreras concurrentes.
    En estado HALF_OPEN permite estrictamente un único sondeo de recuperación en vuelo.
    No se activa por errores de cliente (código HTTP 4xx).
    """

    def __init__(self, fallos_max: int = 3, tiempo_enfriamiento_s: float = 5.0):
        self._fallos_max = max(1, fallos_max)
        self._tiempo_enfriamiento_s = max(0.1, tiempo_enfriamiento_s)
        self._lock = threading.Lock()
        self._estado = CircuitState.CLOSED
        self._fallos_consecutivos = 0
        self._tiempo_apertura = 0.0
        self._sondeo_en_vuelo = False
        self._propietario_sondeo: object | None = None

    @property
    def estado(self) -> CircuitState:
        with self._lock:
            if self._estado == CircuitState.OPEN:
                if (time.monotonic() - self._tiempo_apertura) >= self._tiempo_enfriamiento_s:
                    self._estado = CircuitState.HALF_OPEN
            return self._estado

    def puede_ejecutar(self, propietario: object | None = None) -> bool:
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
                    self._propietario_sondeo = propietario if propietario is not None else object()
                    return True
                return False

            return False

    def registrar_exito(self, propietario: object | None = None) -> None:
        with self._lock:
            if self._estado == CircuitState.HALF_OPEN:
                if propietario is not None and self._propietario_sondeo is not None and self._propietario_sondeo != propietario:
                    return
            self._estado = CircuitState.CLOSED
            self._fallos_consecutivos = 0
            self._tiempo_apertura = 0.0
            self._sondeo_en_vuelo = False
            self._propietario_sondeo = None

    def registrar_cancelacion(self, propietario: object | None = None) -> None:
        """Libera la ranura de sondeo si el llamador que poseía el sondeo fue cancelado durante HALF_OPEN."""
        with self._lock:
            if self._estado == CircuitState.HALF_OPEN:
                if propietario is None or self._propietario_sondeo is None or self._propietario_sondeo == propietario:
                    self._sondeo_en_vuelo = False
                    self._propietario_sondeo = None

    def registrar_fallo(self, excepcion: BaseException | None = None, propietario: object | None = None) -> None:
        with self._lock:
            if self._estado == CircuitState.HALF_OPEN:
                if propietario is not None and self._propietario_sondeo is not None and self._propietario_sondeo != propietario:
                    return

            # Cancelaciones voluntarias (GeneratorExit) nunca penalizan fallos del motor
            if isinstance(excepcion, GeneratorExit):
                if self._estado == CircuitState.HALF_OPEN:
                    self._sondeo_en_vuelo = False
                    self._propietario_sondeo = None
                return

            if isinstance(excepcion, urllib.error.HTTPError):
                code = excepcion.code
                if code == 429:
                    # 429: Saturación de cola / ranuras ocupadas (backpressure).
                    # No indica caída del software de inferencia.
                    if self._estado == CircuitState.HALF_OPEN:
                        self._sondeo_en_vuelo = False
                        self._propietario_sondeo = None
                    return
                if code in (400, 401, 403, 404, 422):
                    # Errores de cliente / petición / autenticación / ruta: el motor está activo y respondió
                    if self._estado == CircuitState.HALF_OPEN:
                        self._sondeo_en_vuelo = False
                        self._propietario_sondeo = None
                    return
                # 408 (Request Timeout) y 5xx son fallos del servicio

            self._fallos_consecutivos += 1
            if self._estado == CircuitState.HALF_OPEN or self._fallos_consecutivos >= self._fallos_max:
                self._estado = CircuitState.OPEN
                self._tiempo_apertura = time.monotonic()
            self._sondeo_en_vuelo = False
            self._propietario_sondeo = None


class LlmGovernor:
    """Controlador de concurrencia y cola acotada para el cliente LLM con orden FIFO estricto.

    Garantiza que no se desborde la capacidad del servidor LLM.
    Si la cola se llena, rechaza de inmediato con LlmSaturadoError (0ms fail-fast).
    Si la espera supera el límite de tiempo, rechaza con LlmColaExpiradaError.
    Las ranuras liberadas se asignan estrictamente en orden de llegada (FIFO) mediante
    señalización punto a punto con threading.Event, evitando carreras o inanición.
    """

    def __init__(self, max_concurrencia: int = 1, max_cola: int = 5, timeout_cola_s: float = 10.0):
        self.max_concurrencia = max(1, max_concurrencia)
        self.max_cola = max(0, max_cola)
        self.timeout_cola_s = max(0.1, timeout_cola_s)
        self._lock = threading.Lock()
        # Referencia de compatibilidad con codigo que espera self._cond
        self._cond = threading.Condition(self._lock)
        self._activos = 0
        self._cola: collections.deque[threading.Event] = collections.deque()

    @property
    def activos(self) -> int:
        with self._lock:
            return self._activos

    @property
    def en_espera(self) -> int:
        with self._lock:
            return len(self._cola)

    @contextmanager
    def adquirir(self):
        evento_turno: threading.Event | None = None
        with self._lock:
            # 1. Si hay ranura libre y nadie esperando en cola, adquirimos inmediatamente
            if self._activos < self.max_concurrencia and not self._cola:
                self._activos += 1
            else:
                # 2. Verificar si la cola está llena
                if len(self._cola) >= self.max_cola:
                    raise LlmSaturadoError(
                        f"Servicio LLM saturado: {self._activos} activos, {len(self._cola)}/{self.max_cola} en espera."
                    )
                # 3. Encolar en estricto orden de llegada (FIFO)
                evento_turno = threading.Event()
                self._cola.append(evento_turno)

        # 4. Si tuvimos que esperar en cola, esperar asignación con timeout
        if evento_turno is not None:
            concedido = evento_turno.wait(timeout=self.timeout_cola_s)
            if not concedido:
                with self._lock:
                    if not evento_turno.is_set():
                        try:
                            self._cola.remove(evento_turno)
                        except ValueError:
                            pass
                        raise LlmColaExpiradaError(
                            f"Tiempo de espera en cola del LLM agotado ({self.timeout_cola_s:.1f}s)."
                        )
                    # Si se activó justo en el límite, el permiso fue asignado

        try:
            yield
        finally:
            with self._lock:
                # 5. Liberar permiso: si hay hilos esperando en cola, transferir al más antiguo (FIFO)
                if self._cola:
                    siguiente = self._cola.popleft()
                    siguiente.set()
                else:
                    self._activos = max(0, self._activos - 1)


@dataclass
class MetricasData:
    solicitudes_totales: int = 0
    solicitudes_exitosas: int = 0
    solicitudes_fallidas: int = 0
    solicitudes_rechazadas_saturacion: int = 0
    solicitudes_rechazadas_circuito: int = 0
    solicitudes_timeout_cola: int = 0
    solicitudes_timeout_lectura: int = 0
    solicitudes_canceladas: int = 0
    tiempo_total_inferencia_s: float = 0.0


class LlmMetricas:
    """Contador thread-safe de telemetría y salud para el servicio LLM."""

    def __init__(self):
        self._lock = threading.Lock()
        self._d = MetricasData()

    def registrar_inicio(self) -> None:
        with self._lock:
            self._d.solicitudes_totales += 1

    def registrar_exito(self, duracion_s: float) -> None:
        with self._lock:
            self._d.solicitudes_exitosas += 1
            self._d.tiempo_total_inferencia_s += duracion_s

    def registrar_fallo(self) -> None:
        with self._lock:
            self._d.solicitudes_fallidas += 1

    def registrar_rechazo_saturacion(self) -> None:
        with self._lock:
            self._d.solicitudes_rechazadas_saturacion += 1

    def registrar_rechazo_circuito(self) -> None:
        with self._lock:
            self._d.solicitudes_rechazadas_circuito += 1

    def registrar_timeout_cola(self) -> None:
        with self._lock:
            self._d.solicitudes_timeout_cola += 1

    def registrar_timeout_lectura(self) -> None:
        with self._lock:
            self._d.solicitudes_timeout_lectura += 1

    def registrar_cancelacion(self) -> None:
        with self._lock:
            self._d.solicitudes_canceladas += 1

    def obtener_resumen(self, governor: LlmGovernor, breaker: LlmCircuitBreaker) -> dict[str, Any]:
        with self._lock:
            promedio = (
                self._d.tiempo_total_inferencia_s / self._d.solicitudes_exitosas
                if self._d.solicitudes_exitosas > 0
                else 0.0
            )
            return {
                "solicitudes_totales": self._d.solicitudes_totales,
                "solicitudes_exitosas": self._d.solicitudes_exitosas,
                "solicitudes_fallidas": self._d.solicitudes_fallidas,
                "solicitudes_rechazadas_saturacion": self._d.solicitudes_rechazadas_saturacion,
                "solicitudes_rechazadas_circuito": self._d.solicitudes_rechazadas_circuito,
                "solicitudes_timeout_cola": self._d.solicitudes_timeout_cola,
                "solicitudes_timeout_lectura": self._d.solicitudes_timeout_lectura,
                "solicitudes_canceladas": self._d.solicitudes_canceladas,
                "latencia_promedio_s": round(promedio, 4),
                "activos_actuales": governor.activos,
                "en_cola_actuales": governor.en_espera,
                "max_concurrencia": governor.max_concurrencia,
                "max_cola": governor.max_cola,
                "estado_circuito": breaker.estado.value,
            }


def _extraer_socket_de_respuesta(resp: Any) -> socket.socket | None:
    """Extrae de forma segura y encapsulada el socket subyacente de una instancia HTTPResponse."""
    fp = getattr(resp, "fp", None)
    if fp is None:
        return None
    raw = getattr(fp, "raw", None)
    if raw is None:
        return None
    return getattr(raw, "_sock", None)


def _iter_lineas_sse_con_timeout(
    resp: Any,
    timeout_inactividad: float,
    timeout_total: float,
) -> Iterator[str]:
    """Iterador de líneas SSE decodificadas con doble límite de timeout:
    1. Límite de inactividad entre recepciones consecutivas de red (socket timeout).
    2. Plazo total de generación monotónico (corta streams lentos o infinitos).
    3. Inspección en cada lectura de bajo nivel (read1), impidiendo que
       fragmentos parciales sin salto de línea eludan el plazo total.
    """
    deadline = time.monotonic() + timeout_total
    s = _extraer_socket_de_respuesta(resp)
    buffer = bytearray()

    while True:
        ahora = time.monotonic()
        if ahora >= deadline:
            raise TimeoutError(f"Plazo total de generación excedido ({timeout_total:.1f}s)")

        tiempo_restante = deadline - ahora
        if s is not None:
            s.settimeout(min(timeout_inactividad, tiempo_restante))

        read_fn = getattr(resp, "read1", getattr(resp, "read", None))
        if read_fn is None:
            break

        try:
            chunk = read_fn(4096)
        except (TimeoutError, OSError) as e:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Plazo total de generación excedido ({timeout_total:.1f}s)") from e
            raise TimeoutError(f"Inactividad de lectura excedida ({timeout_inactividad:.1f}s)") from e

        if not chunk:
            break

        buffer.extend(chunk)

        while True:
            pos_nl = buffer.find(b"\n")
            if pos_nl == -1:
                break
            linea_bytes = buffer[:pos_nl]
            del buffer[:pos_nl + 1]
            if linea_bytes.endswith(b"\r"):
                linea_bytes = linea_bytes[:-1]
            linea = linea_bytes.decode("utf-8", errors="replace").strip()
            if linea:
                yield linea

    if buffer:
        if buffer.endswith(b"\r"):
            buffer = buffer[:-1]
        linea = buffer.decode("utf-8", errors="replace").strip()
        if linea:
            yield linea


# ------------------------------------------------------------------ servicio

class LlmService:
    """Cliente HTTP hacia el servidor central llama-server con protecciones de resiliencia."""

    def __init__(
        self,
        config: ModelConfig,
        logger: logging.Logger,
        max_concurrencia: int | None = None,
        max_cola: int | None = None,
        timeout_cola: float | None = None,
    ) -> None:
        self._cfg = config
        self._log = logger.getChild("llm")
        self._url, self._health_url = self._resolver_urls()
        self._conectado = False
        self._timeout_conexion = float(os.environ.get("AUDACIA_LLM_CONNECT_TIMEOUT", "5.0"))
        self._timeout_inactividad_lectura = float(os.environ.get("AUDACIA_LLM_READ_TIMEOUT", "15.0"))
        self._timeout_total_generacion = float(os.environ.get("AUDACIA_LLM_TOTAL_TIMEOUT", "60.0"))

        concurrencia = (
            max_concurrencia
            if max_concurrencia is not None
            else int(os.environ.get("AUDACIA_LLM_MAX_CONCURRENCY", "1"))
        )
        cola = (
            max_cola
            if max_cola is not None
            else int(os.environ.get("AUDACIA_LLM_MAX_QUEUE", "5"))
        )
        t_cola = (
            timeout_cola
            if timeout_cola is not None
            else float(os.environ.get("AUDACIA_LLM_QUEUE_TIMEOUT", "10.0"))
        )

        self._governor = LlmGovernor(
            max_concurrencia=concurrencia,
            max_cola=cola,
            timeout_cola_s=t_cola,
        )
        self._circuit_breaker = LlmCircuitBreaker(
            fallos_max=int(os.environ.get("AUDACIA_LLM_CB_FAILURES", "3")),
            tiempo_enfriamiento_s=float(os.environ.get("AUDACIA_LLM_CB_COOLING", "5.0")),
        )
        self._metricas = LlmMetricas()
        # Referencia de compatibilidad con codigo que use self._lock
        self._lock = self._governor._cond

    # ------------------------------------------------------------------ configuracion

    @classmethod
    def _resolver_urls(cls) -> tuple[str, str]:
        """Resuelve el endpoint de chat y el de salud desde variables de entorno, config central o default."""
        env_chat = (
            os.getenv("AUDACIA_LLM_URL")
            or os.getenv("LLAMA_SERVER_URL")
            or os.getenv("HACU_LLM_URL")
        )
        if env_chat:
            chat_url = env_chat.strip()
            if chat_url.endswith("/v1/chat/completions"):
                health_url = chat_url[: -len("/v1/chat/completions")] + "/health"
            elif "/v1/" in chat_url:
                health_url = chat_url.split("/v1/")[0] + "/health"
            else:
                health_url = chat_url
            return chat_url, health_url

        # Intentar cargar desde cerebro_audacia/proveedor_modelo/config_llm.json
        ruta_cfg = PROJECT_ROOT / "cerebro_audacia" / "proveedor_modelo" / "config_llm.json"
        if ruta_cfg.exists():
            try:
                with open(ruta_cfg, "r", encoding="utf-8") as f:
                    cfg_datos = json.load(f)
                    srv = cfg_datos.get("servicio", {})
                    chat_url = srv.get("url_chat")
                    health_url = srv.get("url_salud")
                    if chat_url and health_url:
                        return chat_url.strip(), health_url.strip()
            except Exception:
                pass

        return "http://127.0.0.1:8080/v1/chat/completions", "http://127.0.0.1:8080/health"

    # ------------------------------------------------------------------ ciclo de vida

    def cargar(self) -> None:
        """Verifica la disponibilidad del servidor LLM central. Idempotente.

        No carga un nuevo modelo en VRAM: valida la comunicacion con llama-server.
        """
        if self._conectado:
            return

        req = urllib.request.Request(
            self._health_url,
            headers={"User-Agent": "AudacIA-Hacu-Client/1.0"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout_conexion) as resp:
                if resp.status != 200:
                    self._circuit_breaker.registrar_fallo()
                    raise ConnectionError(
                        f"El servidor LLM central respondio con codigo HTTP {resp.status} en {self._health_url}"
                    )
                cuerpo = resp.read().decode("utf-8", errors="replace")
                try:
                    datos = json.loads(cuerpo)
                    estado = datos.get("status")
                    if estado not in {"ok", "ready"}:
                        self._log.warning("Estado reportado por llama-server: %r", estado)
                except Exception:
                    pass
            self._circuit_breaker.registrar_exito()
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            self._circuit_breaker.registrar_fallo()
            raise ConnectionError(
                f"No se pudo conectar con el servidor LLM central en {self._health_url}: {e}. "
                "Asegurese de que llama-server.exe este iniciado y escuchando."
            ) from e

        self._conectado = True
        self._log.info("Conectado exitosamente al servidor LLM central: %s", self._url)

    @staticmethod
    def _preparar_entorno_windows() -> None:
        """Metodo de compatibilidad hacia atras. No requerido en modo cliente HTTP."""
        pass

    def _opciones_de_cache(self, Llama: type) -> dict[str, object]:  # noqa: N803
        """Metodo de compatibilidad con pruebas unitarias heredadas (`test_unidades.py`)."""
        import inspect  # noqa: PLC0415

        admitidos = inspect.signature(Llama.__init__).parameters
        opciones: dict[str, object] = {}
        if self._cfg.flash_attn and "flash_attn" in admitidos:
            opciones["flash_attn"] = True
        if not self._cfg.kv_8bits:
            return opciones
        if "type_k" not in admitidos or "type_v" not in admitidos:
            self._log.warning(
                "Esta version de llama-cpp-python no admite cache KV cuantizada; "
                "se usa fp16 y el contexto ocupa el doble."
            )
            return opciones
        try:
            from llama_cpp import GGML_TYPE_Q8_0  # noqa: PLC0415
        except ImportError:
            GGML_TYPE_Q8_0 = 8  # noqa: N806
        opciones["type_k"] = GGML_TYPE_Q8_0
        opciones["type_v"] = GGML_TYPE_Q8_0
        return opciones

    def precalentar(self) -> float:
        """Genera un token de descarte para pagar el primer prefill antes de la exhibicion.

        Mide y reporta la latencia del primer viaje HTTP hacia el servidor central.
        """
        inicio = time.perf_counter()
        if not self._circuit_breaker.puede_ejecutar():
            self._log.warning("Precalentamiento omitido: Circuito LLM ABIERTO")
            return 0.0

        try:
            with self._governor.adquirir():
                payload = {
                    "messages": [{"role": "user", "content": "hola"}],
                    "max_tokens": 1,
                    "temperature": 0.0,
                    "stream": False,
                }
                req = urllib.request.Request(
                    self._url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": "AudacIA-Hacu-Client/1.0",
                    },
                )
                with urllib.request.urlopen(req, timeout=self._timeout_conexion) as resp:
                    if resp.status != 200:
                        self._log.warning("Precalentamiento HTTP devolvio status %d", resp.status)
                        return 0.0
                    _ = resp.read()
                self._circuit_breaker.registrar_exito()
        except Exception:
            self._circuit_breaker.registrar_fallo()
            self._log.warning("Fallo el precalentamiento del modelo", exc_info=True)
            return 0.0

        transcurrido = time.perf_counter() - inicio
        self._log.info("Modelo precalentado en %.2fs", transcurrido)
        return transcurrido

    def cerrar(self) -> None:
        """Cierra el cliente local de forma ordenada sin detener el demonio compartido."""
        with self._condicion:
            self._conectado = False

    @property
    def _condicion(self) -> threading.Condition:
        return self._governor._cond

    @property
    def _timeout_lectura(self) -> float:
        return self._timeout_inactividad_lectura

    @_timeout_lectura.setter
    def _timeout_lectura(self, valor: float) -> None:
        self._timeout_inactividad_lectura = valor

    @property
    def governor(self) -> LlmGovernor:
        return self._governor

    @property
    def circuit_breaker(self) -> LlmCircuitBreaker:
        return self._circuit_breaker

    @property
    def metricas(self) -> LlmMetricas:
        return self._metricas

    # ------------------------------------------------------------- inferencia

    def _tope(self, aliento: Aliento | None, extenso: bool) -> int:
        """Techo de tokens del turno. `extenso` es el atajo heredado de EXTENSO."""
        if aliento is None:
            aliento = Aliento.EXTENSO if extenso else Aliento.NORMAL
        return {
            Aliento.BREVE: self._cfg.chat_max_tokens_breve,
            Aliento.NORMAL: self._cfg.chat_max_tokens,
            Aliento.EXTENSO: self._cfg.chat_max_tokens_extenso,
        }[aliento]

    def stream_chat(
        self,
        mensajes: Sequence[Mensaje],
        extenso: bool = False,
        al_terminar: Callable[[str | None], None] | None = None,
        aliento: Aliento | None = None,
    ) -> Iterator[str]:
        """Genera la respuesta de escena token a token consumiendo el stream SSE de llama-server.

        Protegido por ConcurrencyGovernor (cola acotada) y CircuitBreaker (aislamiento de caidas).
        """
        self._metricas.registrar_inicio()

        # 1. Comprobar Circuit Breaker con ticket de sondeo
        ticket_sondeo = object()
        if not self._circuit_breaker.puede_ejecutar(ticket_sondeo):
            self._metricas.registrar_rechazo_circuito()
            self._log.warning("Peticion rechazada tempranamente: Circuito LLM ABIERTO (servicio caido).")
            if al_terminar is not None:
                al_terminar(None)
            return

        motivo: str | None = None
        tope_tokens = self._tope(aliento, extenso)
        if tope_tokens <= 0:
            raise ValueError(f"tope_tokens debe ser un entero positivo, recibido: {tope_tokens}")

        n_ctx_disponible = getattr(self._cfg, "n_ctx", 8192)
        try:
            from .context import ContextoExcedidoError, estimar_tokens_prompt
            tokens_prompt_est = estimar_tokens_prompt(list(mensajes))
            if tokens_prompt_est >= n_ctx_disponible:
                if al_terminar is not None:
                    al_terminar(None)
                raise ContextoExcedidoError(
                    f"El prompt ({tokens_prompt_est} tokens) excede el contexto disponible ({n_ctx_disponible} tokens)."
                )
            MIN_TOKENS_SALIDA = 16
            margen_contexto = n_ctx_disponible - tokens_prompt_est
            if margen_contexto < MIN_TOKENS_SALIDA:
                if al_terminar is not None:
                    al_terminar(None)
                raise ContextoExcedidoError(
                    f"Espacio restante para respuesta ({margen_contexto} tokens) inferior al mínimo operacional ({MIN_TOKENS_SALIDA} tokens)."
                )
            tope_tokens = min(tope_tokens, margen_contexto)
        except ContextoExcedidoError:
            raise
        except Exception:
            pass

        payload = {
            "messages": list(mensajes),
            "max_tokens": tope_tokens,
            "temperature": self._cfg.chat_temperature,
            "repeat_penalty": self._cfg.chat_repeat_penalty,
            "frequency_penalty": self._cfg.chat_frequency_penalty,
            "presence_penalty": self._cfg.chat_presence_penalty,
            "stream": True,
        }
        req = urllib.request.Request(
            self._url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "User-Agent": "AudacIA-Hacu-Client/1.0",
            },
        )

        # 2. Control de admision mediante Governor
        try:
            with self._governor.adquirir():
                t0 = time.perf_counter()
                resp = None
                try:
                    resp = urllib.request.urlopen(req, timeout=self._timeout_conexion)
                    for linea in _iter_lineas_sse_con_timeout(
                        resp,
                        self._timeout_inactividad_lectura,
                        self._timeout_total_generacion,
                    ):
                        if linea.startswith("data: "):
                            datos_txt = linea[6:].strip()
                            if datos_txt == "[DONE]":
                                break
                            try:
                                chunk = json.loads(datos_txt)
                            except json.JSONDecodeError:
                                continue
                            choices = chunk.get("choices", [])
                            if not choices:
                                continue
                            eleccion = choices[0]
                            motivo = eleccion.get("finish_reason") or motivo
                            contenido = eleccion.get("delta", {}).get("content")
                            if contenido:
                                yield contenido

                    self._circuit_breaker.registrar_exito(ticket_sondeo)
                    self._metricas.registrar_exito(time.perf_counter() - t0)

                except GeneratorExit:
                    # Desconexion del cliente o consumidor
                    self._circuit_breaker.registrar_cancelacion(ticket_sondeo)
                    self._metricas.registrar_cancelacion()
                    self._log.info("Consumidor desconectado durante streaming; cancelando socket")
                    raise
                except TimeoutError as e:
                    self._metricas.registrar_timeout_lectura()
                    self._circuit_breaker.registrar_fallo(e, ticket_sondeo)
                    self._log.error("Timeout durante streaming con LLM: %s", e)
                except urllib.error.HTTPError as e:
                    if e.code == 429:
                        self._metricas.registrar_rechazo_saturacion()
                        self._log.warning("Servidor LLM devolvió HTTP 429 (saturación / backpressure)")
                    else:
                        self._metricas.registrar_fallo()
                        self._circuit_breaker.registrar_fallo(e, ticket_sondeo)
                        self._log.error("Error HTTP durante streaming con LLM: %s", e)
                except (urllib.error.URLError, OSError) as e:
                    self._metricas.registrar_fallo()
                    self._circuit_breaker.registrar_fallo(e, ticket_sondeo)
                    self._log.error("Error de conexion durante streaming con LLM: %s", type(e).__name__)
                finally:
                    if resp is not None:
                        try:
                            resp.close()
                        except Exception:
                            pass
                    if al_terminar is not None:
                        al_terminar(motivo)

        except LlmSaturadoError as e:
            self._metricas.registrar_rechazo_saturacion()
            self._log.warning("Peticion rechazada por saturacion de cola: %s", e)
            if al_terminar is not None:
                al_terminar(None)
            return
        except LlmColaExpiradaError as e:
            self._metricas.registrar_timeout_cola()
            self._log.warning("Peticion expiro esperando en cola de admision: %s", e)
            if al_terminar is not None:
                al_terminar(None)
            return

    def completar_json(self, prompt: str, max_tokens: int) -> dict[str, Any] | None:
        """Ejecuta una tarea de utilidad solicitando un objeto JSON estructurado."""
        self._metricas.registrar_inicio()

        if not self._circuit_breaker.puede_ejecutar():
            self._metricas.registrar_rechazo_circuito()
            self._log.warning("Tarea de utilidad rechazada: Circuito LLM ABIERTO")
            return None

        payload = {
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": self._cfg.utility_temperature,
            "response_format": {"type": "json_object"},
            "stream": False,
        }

        try:
            with self._governor.adquirir():
                t0 = time.perf_counter()
                try:
                    respuesta_dict = self._peticion_post(payload)
                except LlmSaturadoError:
                    raise
                except TimeoutError as e:
                    self._circuit_breaker.registrar_fallo(e)
                    self._metricas.registrar_timeout_lectura()
                    self._log.warning("Timeout en tarea de utilidad del LLM: %s", e)
                    return None
                except Exception:
                    respuesta_dict = None

                if respuesta_dict is None:
                    # Reintento degradado en modo texto simple
                    payload_texto = {
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": max_tokens,
                        "temperature": self._cfg.utility_temperature,
                        "stream": False,
                    }
                    try:
                        respuesta_dict = self._peticion_post(payload_texto)
                    except LlmSaturadoError:
                        raise
                    except Exception as e:
                        self._circuit_breaker.registrar_fallo(e)
                        self._metricas.registrar_fallo()
                        self._log.warning("Fallo definitivo en tarea de utilidad del LLM: %s", e)
                        return None

                if respuesta_dict is None:
                    return None

                self._circuit_breaker.registrar_exito()
                self._metricas.registrar_exito(time.perf_counter() - t0)

        except (LlmSaturadoError, LlmColaExpiradaError) as e:
            self._metricas.registrar_rechazo_saturacion()
            self._log.warning("Tarea de utilidad rechazada por sobrecarga: %s", e)
            return None

        choices = respuesta_dict.get("choices", [])
        if not choices:
            return None
        texto = choices[0].get("message", {}).get("content", "").strip()
        return self._parsear_json(texto)

    def _peticion_post(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        """Envia una solicitud POST JSON controlada al endpoint de chat completions."""
        req = urllib.request.Request(
            self._url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "User-Agent": "AudacIA-Hacu-Client/1.0",
            },
        )
        timeout_efectivo = min(self._timeout_conexion, self._timeout_inactividad_lectura)
        try:
            with urllib.request.urlopen(req, timeout=timeout_efectivo) as resp:
                sock = getattr(getattr(resp, "fp", None), "raw", None)
                s = getattr(sock, "_sock", None)
                if s is not None:
                    s.settimeout(self._timeout_inactividad_lectura)

                if resp.status != 200:
                    self._log.warning("Respuesta HTTP no exitosa del LLM: %d", resp.status)
                    return None
                cuerpo = resp.read().decode("utf-8", errors="replace")
                return json.loads(cuerpo)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                # El llamador (create_chat_completion, stream_chat, etc.) registra la saturación al capturar LlmSaturadoError
                raise LlmSaturadoError("El servidor LLM devolvió HTTP 429 (Too Many Requests / Ranuras ocupadas)") from e
            self._log.warning("Error HTTP en peticion hacia servidor LLM: %s", e)
            raise
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
            self._log.warning("Error en peticion HTTP hacia servidor LLM: %s", type(e).__name__)
            raise

    def _parsear_json(self, texto: str) -> dict[str, Any] | None:
        """Tolera el caso en que el modelo envuelva el JSON en texto o vallas markdown."""
        try:
            datos = json.loads(texto)
        except json.JSONDecodeError:
            bloque = _BLOQUE_JSON.search(texto)
            if not bloque:
                self._log.debug("Salida de utilidad no parseable: %r", texto[:200])
                return None
            try:
                datos = json.loads(bloque.group(0))
            except json.JSONDecodeError:
                self._log.debug("Bloque JSON invalido: %r", bloque.group(0)[:200])
                return None
        return datos if isinstance(datos, dict) else None

    # ------------------------------------------------------------- telemetria

    def metricas(self) -> dict[str, Any]:
        """Devuelve una instantanea estructurada de las metricas del servicio."""
        return self._metricas.obtener_resumen(self._governor, self._circuit_breaker)

    # ------------------------------------------------------------- compatibilidad

    @property
    def _modelo(self) -> Any:
        """Propiedad de compatibilidad con codigo que acceda a _modelo."""
        if not self._conectado:
            raise RuntimeError("El modelo no ha sido cargado. Llama a LlmService.cargar() primero.")
        return self

    def create_chat_completion(
        self,
        messages: list[dict[str, Any]],
        max_tokens: int = 512,
        temperature: float = 0.3,
        stream: bool = False,
        response_format: dict[str, Any] | None = None,
        repeat_penalty: float | None = None,
        frequency_penalty: float | None = None,
        presence_penalty: float | None = None,
        **kwargs: Any,
    ) -> Any:
        for param_prohibido in ("model", "modelo", "model_path", "ruta_modelo", "gguf", "gguf_path", "weights"):
            if param_prohibido in kwargs:
                from cerebro_audacia.conexion import ViolacionArquitecturaError
                raise ViolacionArquitecturaError(
                    f"Violación de Arquitectura de AudacIA: el parámetro '{param_prohibido}' está prohibido para agentes. "
                    "Todos los agentes deben consumir exclusivamente el Cerebro Central único. "
                    "Ningún agente puede seleccionar, instanciar o suministrar un modelo de lenguaje propio."
                )

        if stream:
            return self._stream_adapter(
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                repeat_penalty=repeat_penalty,
                frequency_penalty=frequency_penalty,
                presence_penalty=presence_penalty,
            )

        if max_tokens is None or max_tokens <= 0:
            raise ValueError(f"max_tokens debe ser un entero positivo, recibido: {max_tokens}")

        n_ctx_disponible = getattr(self._cfg, "n_ctx", 8192)
        from .context import ContextoExcedidoError, estimar_tokens_prompt
        tokens_prompt_est = estimar_tokens_prompt(messages)
        if tokens_prompt_est >= n_ctx_disponible:
            raise ContextoExcedidoError(
                f"El prompt ({tokens_prompt_est} tokens) excede el contexto disponible ({n_ctx_disponible} tokens)."
            )
        MIN_TOKENS_SALIDA = 16
        margen_contexto = n_ctx_disponible - tokens_prompt_est
        if margen_contexto < MIN_TOKENS_SALIDA:
            raise ContextoExcedidoError(
                f"Espacio restante para respuesta ({margen_contexto} tokens) inferior al mínimo operacional ({MIN_TOKENS_SALIDA} tokens)."
            )
        max_tokens_efectivo = min(max_tokens, margen_contexto)

        self._metricas.registrar_inicio()
        ticket_sondeo = object()
        if not self._circuit_breaker.puede_ejecutar(ticket_sondeo):
            self._metricas.registrar_rechazo_circuito()
            raise LlmNoDisponibleError("Circuito LLM ABIERTO (servicio caido)")

        payload: dict[str, Any] = {
            "messages": messages,
            "max_tokens": max_tokens_efectivo,
            "temperature": temperature,
            "stream": False,
        }
        if response_format:
            payload["response_format"] = response_format
        if repeat_penalty is not None:
            payload["repeat_penalty"] = repeat_penalty
        if frequency_penalty is not None:
            payload["frequency_penalty"] = frequency_penalty
        if presence_penalty is not None:
            payload["presence_penalty"] = presence_penalty
        tools = kwargs.get("tools")
        if tools is not None:
            payload["tools"] = tools
        tool_choice = kwargs.get("tool_choice")
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice

        try:
            with self._governor.adquirir():
                t0 = time.perf_counter()
                try:
                    resp_dict = self._peticion_post(payload)
                except LlmSaturadoError:
                    raise
                except urllib.error.HTTPError as e:
                    self._circuit_breaker.registrar_fallo(e, ticket_sondeo)
                    self._metricas.registrar_fallo()
                    raise
                except Exception as e:
                    self._circuit_breaker.registrar_fallo(e, ticket_sondeo)
                    self._metricas.registrar_fallo()
                    raise

                if resp_dict is None:
                    self._circuit_breaker.registrar_fallo(ticket_sondeo)
                    self._metricas.registrar_fallo()
                    raise RuntimeError("Error en generacion sincrona hacia el servidor LLM")
                self._circuit_breaker.registrar_exito(ticket_sondeo)
                self._metricas.registrar_exito(time.perf_counter() - t0)
                return resp_dict
        except LlmSaturadoError:
            self._metricas.registrar_rechazo_saturacion()
            raise
        except LlmColaExpiradaError:
            self._metricas.registrar_timeout_cola()
            raise

    def _stream_adapter(
        self,
        messages: list[dict[str, Any]],
        max_tokens: int = 512,
        temperature: float = 0.3,
        repeat_penalty: float | None = None,
        frequency_penalty: float | None = None,
        presence_penalty: float | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Generador compatible con el formato de chunks de llama_cpp."""
        if max_tokens is None or max_tokens <= 0:
            raise ValueError(f"max_tokens debe ser un entero positivo, recibido: {max_tokens}")

        n_ctx_disponible = getattr(self._cfg, "n_ctx", 8192)
        from .context import ContextoExcedidoError, estimar_tokens_prompt
        tokens_prompt_est = estimar_tokens_prompt(messages)
        if tokens_prompt_est >= n_ctx_disponible:
            raise ContextoExcedidoError(
                f"El prompt ({tokens_prompt_est} tokens) excede el contexto disponible ({n_ctx_disponible} tokens)."
            )
        MIN_TOKENS_SALIDA = 16
        margen_contexto = n_ctx_disponible - tokens_prompt_est
        if margen_contexto < MIN_TOKENS_SALIDA:
            raise ContextoExcedidoError(
                f"Espacio restante para respuesta ({margen_contexto} tokens) inferior al mínimo operacional ({MIN_TOKENS_SALIDA} tokens)."
            )
        max_tokens_efectivo = min(max_tokens, margen_contexto)

        self._metricas.registrar_inicio()
        ticket_sondeo = object()
        if not self._circuit_breaker.puede_ejecutar(ticket_sondeo):
            self._metricas.registrar_rechazo_circuito()
            raise LlmNoDisponibleError("Circuito LLM ABIERTO (servicio caido)")

        payload: dict[str, Any] = {
            "messages": messages,
            "max_tokens": max_tokens_efectivo,
            "temperature": temperature,
            "stream": True,
        }
        if repeat_penalty is not None:
            payload["repeat_penalty"] = repeat_penalty
        if frequency_penalty is not None:
            payload["frequency_penalty"] = frequency_penalty
        if presence_penalty is not None:
            payload["presence_penalty"] = presence_penalty

        req = urllib.request.Request(
            self._url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "User-Agent": "AudacIA-Hacu-Client/1.0",
            },
        )

        try:
            with self._governor.adquirir():
                t0 = time.perf_counter()
                resp = None
                try:
                    resp = urllib.request.urlopen(req, timeout=self._timeout_conexion)
                    for linea in _iter_lineas_sse_con_timeout(
                        resp,
                        self._timeout_inactividad_lectura,
                        self._timeout_total_generacion,
                    ):
                        if linea.startswith("data: "):
                            datos_txt = linea[6:].strip()
                            if datos_txt == "[DONE]":
                                break
                            try:
                                yield json.loads(datos_txt)
                            except json.JSONDecodeError:
                                continue

                    self._circuit_breaker.registrar_exito(ticket_sondeo)
                    self._metricas.registrar_exito(time.perf_counter() - t0)
                except GeneratorExit:
                    self._circuit_breaker.registrar_cancelacion(ticket_sondeo)
                    self._metricas.registrar_cancelacion()
                    self._log.info("Consumidor desconectado durante streaming adapter; cancelando socket")
                    raise
                except urllib.error.HTTPError as e:
                    if e.code == 429:
                        self._metricas.registrar_rechazo_saturacion()
                        raise LlmSaturadoError("El servidor LLM devolvió HTTP 429 (Too Many Requests / Ranuras ocupadas)") from e
                    self._metricas.registrar_fallo()
                    self._circuit_breaker.registrar_fallo(e, ticket_sondeo)
                    raise
                except TimeoutError as e:
                    self._metricas.registrar_timeout_lectura()
                    self._circuit_breaker.registrar_fallo(e, ticket_sondeo)
                    raise
                except Exception as e:
                    self._metricas.registrar_fallo()
                    self._circuit_breaker.registrar_fallo(e, ticket_sondeo)
                    raise
                finally:
                    if resp is not None:
                        try:
                            resp.close()
                        except Exception:
                            pass
        except LlmSaturadoError:
            self._metricas.registrar_rechazo_saturacion()
            raise
        except LlmColaExpiradaError:
            self._metricas.registrar_timeout_cola()
            raise


# ------------------------------------------------------------- patron singleton

_CLIENTE_GLOBAL: LlmService | None = None
_CLIENTE_GLOBAL_LOCK = threading.Lock()


def get_llm_client(config: ModelConfig | None = None, logger: logging.Logger | None = None) -> LlmService:
    """Devuelve la instancia singleton compartida del cliente LLM central.

    Garantiza que todos los agentes y servicios reutilicen el mismo cliente HTTP,
    el mismo gobernador de concurrencia y el mismo circuit breaker sin crear
    instancias redundantes ni copias del modelo.
    """
    global _CLIENTE_GLOBAL
    with _CLIENTE_GLOBAL_LOCK:
        if _CLIENTE_GLOBAL is None:
            from .config import ModelConfig
            import logging
            cfg = config or ModelConfig()
            log = logger or logging.getLogger("hacu")
            _CLIENTE_GLOBAL = LlmService(cfg, log)
        return _CLIENTE_GLOBAL

