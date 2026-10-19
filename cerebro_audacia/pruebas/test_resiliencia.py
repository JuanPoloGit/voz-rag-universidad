"""Suite de pruebas unitarias exhaustivas para la política de resiliencia y gobernanza de recursos.

Verifica de forma aislada, determinista y sin GPU/red:
1. Circuit Breaker (estados CLOSED, OPEN, HALF_OPEN, recuperación y fallo temprano).
2. Concurrency Governor y saturación de cola (QUEUE_FULL).
3. Resource Tracker (detección de saturación de VRAM/GPU, aislamiento de fallos).
4. Timeout real en hilos desacoplados.
5. Detección de operaciones ambiguas en mutaciones no idempotentes (OPERATION_AMBIGUOUS).
6. Política de reintentos consciente de idempotencia (prohibición estricta en NON_IDEMPOTENT).
7. Cancelación temprana por cliente desconectado (CANCELLED).
8. Inmutabilidad profunda con MappingProxyType.
9. Sanitización de información sensible en detalles de error.
10. Aislamiento de fallos entre agentes independientes.
"""

from __future__ import annotations

import threading
import time
import unittest
from types import MappingProxyType
from typing import Any, Optional

from cerebro_audacia.agentes import (
    AgentContext,
    AgentContract,
    AgentError,
    AgentErrorCode,
    AgentExecutionError,
    AgentInput,
    AgentMetadata,
    AgentOutput,
    AgentResult,
    CircuitBreaker,
    CircuitState,
    ConcurrencyGovernor,
    ContractValidationError,
    DataAutonomyLevel,
    ExecutionState,
    IdempotencyMode,
    ResilientAgentExecutor,
    ResourceKind,
    ResourceProfile,
    ResourceTracker,
    RetryPolicy,
)


# ─────────────────────────────────────────────────────────────────────────────
# AGENTES FALSOS PARA PRUEBAS DE RESILIENCIA
# ─────────────────────────────────────────────────────────────────────────────


class AgenteLentoMock(AgentContract):
    """Agente que simula una operación de larga duración para probar timeouts reales."""

    def __init__(self, retardo_segundos: float = 0.5, timeout_segundos: float = 5.0):
        self.retardo_segundos = retardo_segundos
        self.timeout_segundos = timeout_segundos

    @property
    def metadata(self) -> AgentMetadata:
        return AgentMetadata(
            id="mock_lento",
            nombre="Agente Lento Mock",
            version="1.0.0",
            descripcion="Simula llamadas lentas a inferencia o I/O.",
            nivel_autonomia=DataAutonomyLevel.READ_ONLY,
            canales_permitidos=("web_lan",),
            requiere_autenticacion=False,
            perfil_recursos=ResourceProfile(
                recursos_requeridos=(ResourceKind.CPU,),
                timeout_segundos=self.timeout_segundos,
            ),
            modo_idempotencia=IdempotencyMode.READ_ONLY,
        )

    def _ejecutar_interno(self, entrada: AgentInput) -> AgentOutput:
        time.sleep(self.retardo_segundos)
        return AgentOutput(respuesta="Operación lenta finalizada con éxito.")


class AgenteMutacionNoIdempotenteLentaMock(AgentContract):
    """Agente transaccional que muta estado y tarda; ideal para probar estados ambiguos."""

    def __init__(self, retardo_segundos: float = 0.5, timeout_segundos: float = 5.0):
        self.retardo_segundos = retardo_segundos
        self.timeout_segundos = timeout_segundos

    @property
    def metadata(self) -> AgentMetadata:
        return AgentMetadata(
            id="mock_mutacion_lenta",
            nombre="Agente Mutación Lenta Mock",
            version="1.0.0",
            descripcion="Muta inventario de forma no idempotente.",
            nivel_autonomia=DataAutonomyLevel.PROPOSAL_BASED,
            canales_permitidos=("web_lan",),
            requiere_autenticacion=True,
            perfil_recursos=ResourceProfile(
                recursos_requeridos=(ResourceKind.CPU, ResourceKind.DISK),
                timeout_segundos=self.timeout_segundos,
            ),
            modo_idempotencia=IdempotencyMode.NON_IDEMPOTENT,
        )

    def _ejecutar_interno(self, entrada: AgentInput) -> AgentOutput:
        time.sleep(self.retardo_segundos)
        return AgentOutput(
            respuesta="Movimiento registrado.",
            propuesta={"accion": "descontar_stock", "cantidad": 5},
        )


class AgenteMutacionNoCooperativaMock(AgentContract):
    """Agente no cooperativo que simula una mutación tardía en segundo plano.

    Demuestra la limitación física de los hilos de Python:
    1. No comprueba el cancel_token (ignora la cancelación cooperativa).
    2. Continúa ejecutándose en el hilo de fondo tras el timeout del llamador.
    3. Produce una mutación tardía en su estado interno.
    4. El ConcurrencyGovernor retiene la ranura de concurrencia ocupada hasta que el hilo
       termina realmente, impidiendo sobrecargas o colisiones.
    """

    def __init__(self, retardo_segundos: float = 0.20, timeout_segundos: float = 0.04):
        self.retardo_segundos = retardo_segundos
        self.timeout_segundos = timeout_segundos
        self.mutacion_efectuada = False
        self.hilo_terminado = False
        self.intentos_recibidos = 0

    @property
    def metadata(self) -> AgentMetadata:
        return AgentMetadata(
            id="mock_no_cooperativo",
            nombre="Agente No Cooperativo Mock",
            version="1.0.0",
            descripcion="Simula mutación tardía post-timeout.",
            nivel_autonomia=DataAutonomyLevel.PROPOSAL_BASED,
            canales_permitidos=("web_lan",),
            requiere_autenticacion=True,
            perfil_recursos=ResourceProfile(
                recursos_requeridos=(ResourceKind.CPU,),
                max_concurrencia=1,
                timeout_segundos=self.timeout_segundos,
            ),
            modo_idempotencia=IdempotencyMode.NON_IDEMPOTENT,
        )

    def _ejecutar_interno(self, entrada: AgentInput) -> AgentOutput:
        self.intentos_recibidos += 1
        # No consulta cancel_token: simula código no cooperativo
        time.sleep(self.retardo_segundos)
        # Mutación tardía simulada
        self.mutacion_efectuada = True
        self.hilo_terminado = True
        return AgentOutput(
            respuesta="Mutación tardía ejecutada.",
            propuesta={"accion": "mutar_estado_tardio", "valor": 42},
        )


class AgenteGPUMock(AgentContract):

    """Agente que declara requerir GPU y VRAM."""

    @property
    def metadata(self) -> AgentMetadata:
        return AgentMetadata(
            id="mock_gpu_llm",
            nombre="Agente GPU LLM Mock",
            version="1.0.0",
            descripcion="Simula un agente que ejecuta inferencia en GPU.",
            nivel_autonomia=DataAutonomyLevel.READ_ONLY,
            canales_permitidos=("web_lan",),
            requiere_autenticacion=False,
            perfil_recursos=ResourceProfile(
                recursos_requeridos=(ResourceKind.GPU, ResourceKind.VRAM),
                max_concurrencia=1,
                timeout_segundos=5.0,
            ),
            modo_idempotencia=IdempotencyMode.READ_ONLY,
        )

    def _ejecutar_interno(self, entrada: AgentInput) -> AgentOutput:
        return AgentOutput(respuesta="Inferencia GPU completada.")


class AgenteFallaIntermitenteMock(AgentContract):
    """Agente que falla un número determinado de veces antes de tener éxito."""

    def __init__(self, fallos_restantes: int = 2):
        self.fallos_restantes = fallos_restantes
        self.intentos_recibidos = 0

    @property
    def metadata(self) -> AgentMetadata:
        return AgentMetadata(
            id="mock_intermitente",
            nombre="Agente Intermitente Mock",
            version="1.0.0",
            descripcion="Simula fallos transitorios en dependencias.",
            nivel_autonomia=DataAutonomyLevel.READ_ONLY,
            canales_permitidos=("web_lan",),
            requiere_autenticacion=False,
            modo_idempotencia=IdempotencyMode.IDEMPOTENT,
        )

    def _ejecutar_interno(self, entrada: AgentInput) -> AgentOutput:
        self.intentos_recibidos += 1
        if self.fallos_restantes > 0:
            self.fallos_restantes -= 1
            raise AgentExecutionError(
                mensaje_seguro="Servicio LLM temporalmente no disponible.",
                codigo=AgentErrorCode.DEPENDENCY_UNAVAILABLE,
            )
        return AgentOutput(respuesta="Éxito tras recuperación.")


# ─────────────────────────────────────────────────────────────────────────────
# SUITE DE PRUEBAS DE RESILIENCIA
# ─────────────────────────────────────────────────────────────────────────────


class TestCircuitBreaker(unittest.TestCase):
    """Pruebas unitarias de la máquina de estados del Circuit Breaker."""

    def test_circuito_inicia_cerrado(self):
        cb = CircuitBreaker(fallos_consecutivos_max=3, nombre="test_cb")
        self.assertEqual(cb.estado, CircuitState.CLOSED)
        self.assertTrue(cb.puede_ejecutar())

    def test_circuito_se_abre_tras_max_fallos_consecutivos(self):
        cb = CircuitBreaker(fallos_consecutivos_max=3, tiempo_recuperacion_segundos=10.0)

        # 2 fallos: sigue CLOSED
        cb.registrar_fallo(AgentErrorCode.TIMEOUT)
        cb.registrar_fallo(AgentErrorCode.DEPENDENCY_UNAVAILABLE)
        self.assertEqual(cb.estado, CircuitState.CLOSED)
        self.assertTrue(cb.puede_ejecutar())

        # 3er fallo: pasa a OPEN
        cb.registrar_fallo(AgentErrorCode.INTERNAL_ERROR)
        self.assertEqual(cb.estado, CircuitState.OPEN)
        self.assertFalse(cb.puede_ejecutar())

    def test_errores_de_cliente_no_abren_el_circuito(self):
        cb = CircuitBreaker(fallos_consecutivos_max=2)
        # Errores de cliente (INVALID_INPUT, UNAUTHORIZED) no indican servicio caído
        cb.registrar_fallo(AgentErrorCode.INVALID_INPUT)
        cb.registrar_fallo(AgentErrorCode.INVALID_INPUT)
        cb.registrar_fallo(AgentErrorCode.CHANNEL_NOT_ALLOWED)
        self.assertEqual(cb.estado, CircuitState.CLOSED)
        self.assertTrue(cb.puede_ejecutar())

    def test_transicion_a_half_open_tras_enfriamiento(self):
        cb = CircuitBreaker(fallos_consecutivos_max=1, tiempo_recuperacion_segundos=0.05)
        cb.registrar_fallo(AgentErrorCode.TIMEOUT)
        self.assertEqual(cb.estado, CircuitState.OPEN)
        self.assertFalse(cb.puede_ejecutar())

        # Esperar enfriamiento
        time.sleep(0.06)
        self.assertEqual(cb.estado, CircuitState.HALF_OPEN)
        self.assertTrue(cb.puede_ejecutar())

    def test_recuperacion_completa_en_half_open(self):
        cb = CircuitBreaker(fallos_consecutivos_max=1, tiempo_recuperacion_segundos=0.05)
        cb.registrar_fallo(AgentErrorCode.TIMEOUT)
        time.sleep(0.06)
        self.assertEqual(cb.estado, CircuitState.HALF_OPEN)

        # Sondeo exitoso
        cb.registrar_exito()
        self.assertEqual(cb.estado, CircuitState.CLOSED)
        self.assertTrue(cb.puede_ejecutar())

    def test_reapertura_inmediata_si_falla_en_half_open(self):
        cb = CircuitBreaker(fallos_consecutivos_max=2, tiempo_recuperacion_segundos=0.05)
        cb.registrar_fallo(AgentErrorCode.TIMEOUT)
        cb.registrar_fallo(AgentErrorCode.TIMEOUT)
        self.assertEqual(cb.estado, CircuitState.OPEN)

        time.sleep(0.06)
        self.assertEqual(cb.estado, CircuitState.HALF_OPEN)

        # Sondeo fallido: debe reabrir inmediatamente
        cb.registrar_fallo(AgentErrorCode.TIMEOUT)
        self.assertEqual(cb.estado, CircuitState.OPEN)
        self.assertFalse(cb.puede_ejecutar())


class TestConcurrencyGovernor(unittest.TestCase):
    """Pruebas del control de concurrencia y límites de cola."""

    def test_adquisicion_y_liberacion_normal(self):
        gov = ConcurrencyGovernor(max_concurrencia=2)
        self.assertTrue(gov.intentar_adquirir())
        self.assertEqual(gov.en_ejecucion, 1)
        self.assertTrue(gov.intentar_adquirir())
        self.assertEqual(gov.en_ejecucion, 2)

        # Sin cupo
        self.assertFalse(gov.intentar_adquirir())
        self.assertEqual(gov.en_ejecucion, 2)

        # Liberar un slot
        gov.liberar()
        self.assertEqual(gov.en_ejecucion, 1)
        self.assertTrue(gov.intentar_adquirir())


class TestResourceTracker(unittest.TestCase):
    """Pruebas de la gobernanza de recursos (GPU, VRAM, RAM, CPU)."""

    def test_marcar_y_liberar_saturacion(self):
        tracker = ResourceTracker()
        self.assertFalse(tracker.esta_saturado(ResourceKind.VRAM))

        # Simular OOM de CUDA
        tracker.marcar_saturado(ResourceKind.VRAM, "CUDA Out of Memory: 8192MB/8192MB asignados")
        self.assertTrue(tracker.esta_saturado(ResourceKind.VRAM))
        self.assertIn("CUDA Out of Memory", tracker.motivo_saturacion(ResourceKind.VRAM))

        # Otros recursos siguen no saturados (aislamiento)
        self.assertFalse(tracker.esta_saturado(ResourceKind.CPU))

        # Liberación tras limpieza
        tracker.liberar_saturacion(ResourceKind.VRAM)
        self.assertFalse(tracker.esta_saturado(ResourceKind.VRAM))


class TestRetryPolicy(unittest.TestCase):
    """Pruebas de la política de reintentos consciente de idempotencia."""

    def setUp(self):
        self.policy = RetryPolicy(max_reintentos=2)

    def test_reintento_prohibido_en_operaciones_no_idempotentes(self):
        err_transitorio = AgentError(codigo=AgentErrorCode.TIMEOUT, mensaje="Timeout")
        # NUNCA se debe reintentar automáticamente una mutación transaccional
        self.assertFalse(
            self.policy.es_reintentable(err_transitorio, IdempotencyMode.NON_IDEMPOTENT)
        )

    def test_reintento_permitido_en_operaciones_idempotentes_o_read_only(self):
        err_transitorio = AgentError(codigo=AgentErrorCode.TIMEOUT, mensaje="Timeout")
        self.assertTrue(self.policy.es_reintentable(err_transitorio, IdempotencyMode.READ_ONLY))
        self.assertTrue(self.policy.es_reintentable(err_transitorio, IdempotencyMode.IDEMPOTENT))

        err_dep = AgentError(codigo=AgentErrorCode.DEPENDENCY_UNAVAILABLE, mensaje="LLM caído")
        self.assertTrue(self.policy.es_reintentable(err_dep, IdempotencyMode.IDEMPOTENT))

    def test_errores_deterministas_no_se_reintentan_jamas(self):
        errores_no_reintentables = [
            AgentErrorCode.INVALID_INPUT,
            AgentErrorCode.UNAUTHORIZED,
            AgentErrorCode.CHANNEL_NOT_ALLOWED,
            AgentErrorCode.INTENT_NOT_SUPPORTED,
            AgentErrorCode.CIRCUIT_OPEN,
            AgentErrorCode.CANCELLED,
        ]
        for codigo in errores_no_reintentables:
            with self.subTest(codigo=codigo):
                err = AgentError(codigo=codigo, mensaje="Error determinista")
                self.assertFalse(self.policy.es_reintentable(err, IdempotencyMode.READ_ONLY))


class TestResilientAgentExecutor(unittest.TestCase):
    """Pruebas de integración del ejecutor resiliente con agentes dobles."""

    def test_ejecutor_rechaza_inmediatamente_si_circuito_abierto(self):
        agente = AgenteGPUMock()
        cb = CircuitBreaker(fallos_consecutivos_max=1, tiempo_recuperacion_segundos=10.0)
        cb.registrar_fallo(AgentErrorCode.TIMEOUT)  # Circuito OPEN

        ejecutor = ResilientAgentExecutor(agente=agente, circuit_breaker=cb)
        ctx = AgentContext.crear(canal="web_lan", correlation_id="tr-cb-open")
        inp = AgentInput(contenido="Consulta", contexto=ctx)

        res = ejecutor.ejecutar(inp)

        self.assertFalse(res.exitoso)
        self.assertEqual(res.error.codigo, AgentErrorCode.CIRCUIT_OPEN)
        self.assertIn("está ABIERTO", res.error.mensaje)
        self.assertEqual(res.correlation_id, "tr-cb-open")

    def test_ejecutor_rechaza_por_saturacion_de_vram_solo_agentes_gpu(self):
        agente_gpu = AgenteGPUMock()
        tracker = ResourceTracker()
        tracker.marcar_saturado(ResourceKind.VRAM, "OOM Crítico")

        ejecutor_gpu = ResilientAgentExecutor(agente=agente_gpu, tracker=tracker)
        ctx = AgentContext.crear(canal="web_lan", correlation_id="tr-oom")
        inp = AgentInput(contenido="Consulta GPU", contexto=ctx)

        res = ejecutor_gpu.ejecutar(inp)

        self.assertFalse(res.exitoso)
        self.assertEqual(res.error.codigo, AgentErrorCode.RESOURCE_EXHAUSTED)
        self.assertIn("VRAM", res.error.mensaje)

        # AISLAMIENTO: Un agente que no requiere VRAM (CPU puro) debe operar con normalidad
        agente_cpu = AgenteLentoMock(retardo_segundos=0.01)
        ejecutor_cpu = ResilientAgentExecutor(agente=agente_cpu, tracker=tracker)
        res_cpu = ejecutor_cpu.ejecutar(inp)
        self.assertTrue(res_cpu.exitoso)

    def test_ejecutor_rechaza_por_cola_llena_exceso_concurrencia(self):
        agente = AgenteLentoMock(retardo_segundos=0.1)
        gov = ConcurrencyGovernor(max_concurrencia=1)
        ejecutor = ResilientAgentExecutor(agente=agente, governor=gov)

        ctx = AgentContext.crear(canal="web_lan", correlation_id="tr-queue")
        inp = AgentInput(contenido="Consulta", contexto=ctx)

        # Ocupar el slot artificialmente
        self.assertTrue(gov.intentar_adquirir())

        res = ejecutor.ejecutar(inp)
        self.assertFalse(res.exitoso)
        self.assertEqual(res.error.codigo, AgentErrorCode.QUEUE_FULL)
        self.assertIn("Límite de concurrencia excedido", res.error.mensaje)

        # Liberar slot
        gov.liberar()

    def test_timeout_real_en_agente_lento(self):
        # Agente tarda 0.3s pero configuramos timeout de 0.05s
        agente = AgenteLentoMock(retardo_segundos=0.3)
        ejecutor = ResilientAgentExecutor(agente=agente)

        ctx = AgentContext.crear(canal="web_lan", correlation_id="tr-timeout-real")
        inp = AgentInput(contenido="Consulta", contexto=ctx)

        res = ejecutor.ejecutar(inp, timeout_override=0.05)

        self.assertFalse(res.exitoso)
        self.assertEqual(res.error.codigo, AgentErrorCode.TIMEOUT)
        self.assertEqual(res.estado, ExecutionState.TIMED_OUT)
        self.assertEqual(res.correlation_id, "tr-timeout-real")
        self.assertIn("agotado", res.error.mensaje)

    def test_timeout_desacoplado_no_bloquea_al_llamador(self):
        """Demuestra que el llamador retorna dentro del presupuesto temporal (< 0.20s)
        a pesar de que el agente en el hilo tarde significativamente más (0.8s).
        Verifica:
        1. El llamador no sufre el bloqueo de salida de contexto de ThreadPoolExecutor.
        2. El resultado reporta TIMED_OUT y AgentErrorCode.TIMEOUT.
        3. Se reporta explícitamente 'tarea_abandonada_en_hilo' sin afirmar falsamente cancelación.
        4. El ConcurrencyGovernor protege la ranura mientras el hilo sigue ejecutando.
        5. Una vez finalizado el hilo, la ranura se libera limpiamente sin fuga de capacidad.
        """
        # Agente tarda 0.8s en hilo; timeout de 0.05s
        agente = AgenteLentoMock(retardo_segundos=0.8)
        gov = ConcurrencyGovernor(max_concurrencia=1)
        ejecutor = ResilientAgentExecutor(agente=agente, governor=gov)

        ctx = AgentContext.crear(canal="web_lan", correlation_id="tr-no-block")
        inp = AgentInput(contenido="Consulta lenta", contexto=ctx)

        inicio = time.perf_counter()
        res = ejecutor.ejecutar(inp, timeout_override=0.05)
        duracion_retorno = time.perf_counter() - inicio

        # 1. El llamador retorna rápidamente (< 0.20s), muy por debajo de los 0.8s del hilo
        self.assertLess(
            duracion_retorno,
            0.20,
            f"El llamador fue bloqueado {duracion_retorno:.3f}s por la salida del context manager."
        )

        # 2. Estado correcto
        self.assertFalse(res.exitoso)
        self.assertEqual(res.error.codigo, AgentErrorCode.TIMEOUT)
        self.assertEqual(res.estado, ExecutionState.TIMED_OUT)
        self.assertEqual(res.correlation_id, "tr-no-block")

        # 3. Honestidad de estado: no se afirma falsamente cancelación
        self.assertNotEqual(res.estado, ExecutionState.CANCELLED)
        self.assertTrue(res.error.detalles.get("tarea_abandonada_en_hilo"))

        # 4. Protección de concurrencia: el governor mantiene ocupada la ranura mientras corre el hilo
        self.assertEqual(gov.en_ejecucion, 1)
        # Una petición simultánea es rechazada para no colisionar en recursos
        res_rechazo = ejecutor.ejecutar(inp)
        self.assertFalse(res_rechazo.exitoso)
        self.assertEqual(res_rechazo.error.codigo, AgentErrorCode.QUEUE_FULL)

        # 5. Liberación limpia: al concluir el hilo en segundo plano, se libera la ranura
        time.sleep(0.85)
        self.assertEqual(gov.en_ejecucion, 0)

    def test_timeout_en_mutacion_no_idempotente_marca_estado_ambiguo(self):
        # Operación no idempotente (mutación de inventario) que excede timeout
        agente = AgenteMutacionNoIdempotenteLentaMock(retardo_segundos=0.3)
        ejecutor = ResilientAgentExecutor(agente=agente)

        ctx = AgentContext.crear(canal="web_lan", usuario_id="operador_01", correlation_id="tr-ambiguo")
        inp = AgentInput(contenido="Mover stock", contexto=ctx, intencion="mover_stock")

        res = ejecutor.ejecutar(inp, timeout_override=0.05)

        self.assertFalse(res.exitoso)
        # CRÍTICO: Debe reportar OPERATION_AMBIGUOUS y AMBIGUOUS para impedir duplicaciones
        self.assertEqual(res.error.codigo, AgentErrorCode.OPERATION_AMBIGUOUS)
        self.assertEqual(res.estado, ExecutionState.AMBIGUOUS)
        self.assertEqual(res.correlation_id, "tr-ambiguo")
        self.assertIn("no reintentar automáticamente", res.error.mensaje)

    def test_efectos_secundarios_post_timeout_en_agente_no_cooperativo(self):
        """Prueba controlada de efectos secundarios tras timeout (Punto 4 auditoría):
        - Qué respuesta recibe el llamador (OPERATION_AMBIGUOUS, ExecutionState.AMBIGUOUS).
        - Si el hilo continúa ejecutándose (sí, en segundo plano).
        - Si el token de cancelación se comprueba (se activa por el ejecutor, pero el agente lo ignora).
        - Si se produce una mutación tardía (sí, cuando el hilo concluye).
        - Si se repite automáticamente la operación (no, NON_IDEMPOTENT prohíbe reintentos).
        - Si la ranura de concurrencia se mantiene ocupada hasta finalizar realmente el trabajo.
        """
        agente = AgenteMutacionNoCooperativaMock(retardo_segundos=0.18, timeout_segundos=0.04)
        gov = ConcurrencyGovernor(max_concurrencia=1, max_cola=0)
        ejecutor = ResilientAgentExecutor(agente=agente, governor=gov)
        token_cancelacion = threading.Event()

        ctx = AgentContext.crear(canal="web_lan", usuario_id="admin_01", correlation_id="tr-side-effect")
        inp = AgentInput(contenido="Mutar inventario", contexto=ctx, intencion="mutar")

        t_inicio = time.perf_counter()
        # 1. Ejecutar con reintentos para verificar tanto respuesta como ausencia de reintentos
        resultado = ejecutor.ejecutar_con_reintentos(
            inp,
            timeout_override=0.04,
            cancel_token=token_cancelacion,
        )
        t_llamador = time.perf_counter() - t_inicio

        # 1. Qué respuesta recibe el llamador:
        # Retorno rápido antes de que termine el hilo de fondo
        self.assertLess(t_llamador, 0.12)
        self.assertFalse(resultado.exitoso)
        self.assertEqual(resultado.error.codigo, AgentErrorCode.OPERATION_AMBIGUOUS)
        self.assertEqual(resultado.estado, ExecutionState.AMBIGUOUS)
        self.assertTrue(resultado.error.detalles.get("tarea_abandonada_en_hilo"))

        # 2. Token de cancelación:
        # El ejecutor lo activó al dispararse el timeout
        self.assertTrue(token_cancelacion.is_set())

        # 3. Estado inmediato del agente y del governor:
        # En el momento en que el llamador recibe la respuesta de timeout,
        # la mutación aún NO ocurrió porque el hilo sigue durmiendo
        self.assertFalse(agente.mutacion_efectuada)
        # La ranura de concurrencia SIGUE OCUPADA protegiendo el recurso
        self.assertEqual(gov.en_ejecucion, 1)

        # 4. Solicitud concurrente entrante en este instante:
        # Es rechazada inmediatamente porque la ranura sigue retenida por el hilo abandonado
        inp_concurrente = AgentInput(contenido="Segunda petición", contexto=ctx)
        res_concurrente = ejecutor.ejecutar(inp_concurrente)
        self.assertFalse(res_concurrente.exitoso)
        self.assertEqual(res_concurrente.error.codigo, AgentErrorCode.QUEUE_FULL)

        # 5. Ausencia de repetición automática:
        # Por ser NON_IDEMPOTENT, el ejecutor no intentó reintentar (intentos == 1)
        self.assertEqual(agente.intentos_recibidos, 1)

        # 6. Esperar a que el hilo en segundo plano complete su ejecución real
        time.sleep(0.20)

        # 7. Mutación tardía consumada:
        # El hilo continuó hasta el final y produjo el efecto secundario tardío
        self.assertTrue(agente.mutacion_efectuada)
        self.assertTrue(agente.hilo_terminado)

        # 8. Liberación diferida de la ranura de concurrencia:
        # Al finalizar el hilo, el callback liberó el governor limpiamente
        self.assertEqual(gov.en_ejecucion, 0)


    def test_cancelacion_temprana_por_cliente(self):
        agente = AgenteLentoMock(retardo_segundos=0.1)
        ejecutor = ResilientAgentExecutor(agente=agente)

        cancel_token = threading.Event()
        cancel_token.set()  # El cliente se desconectó antes de ejecutar

        ctx = AgentContext.crear(canal="web_lan", correlation_id="tr-cancel")
        inp = AgentInput(contenido="Consulta", contexto=ctx)

        res = ejecutor.ejecutar(inp, cancel_token=cancel_token)

        self.assertFalse(res.exitoso)
        self.assertEqual(res.error.codigo, AgentErrorCode.CANCELLED)
        self.assertEqual(res.estado, ExecutionState.CANCELLED)

    def test_ejecucion_con_reintentos_recupera_servicio_transitorio(self):
        # Falla 1 vez y en el reintento tiene éxito
        agente = AgenteFallaIntermitenteMock(fallos_restantes=1)
        retry_policy = RetryPolicy(max_reintentos=2, espera_base_segundos=0.01)
        ejecutor = ResilientAgentExecutor(agente=agente, retry_policy=retry_policy)

        ctx = AgentContext.crear(canal="web_lan", correlation_id="tr-retry-ok")
        inp = AgentInput(contenido="Consulta", contexto=ctx)

        res = ejecutor.ejecutar_con_reintentos(inp)

        self.assertTrue(res.exitoso)
        self.assertEqual(agente.intentos_recibidos, 2)
        self.assertIn("Éxito tras recuperación", res.salida.respuesta)

    def test_ejecucion_con_reintentos_no_reintenta_mutacion_no_idempotente(self):
        # Si un agente no idempotente falla, no se debe reintentar
        agente = AgenteMutacionNoIdempotenteLentaMock(retardo_segundos=0.3)
        # Forzamos un fallo
        ejecutor = ResilientAgentExecutor(agente=agente, retry_policy=RetryPolicy(max_reintentos=3))

        ctx = AgentContext.crear(canal="web_lan", usuario_id="usr_01")
        inp = AgentInput(contenido="Consulta", contexto=ctx)

        # Ejecutamos con timeout ultracorto para provocar fallo
        res = ejecutor.ejecutar_con_reintentos(inp, timeout_override=0.05)

        self.assertFalse(res.exitoso)
        self.assertEqual(res.error.codigo, AgentErrorCode.OPERATION_AMBIGUOUS)


class TestDeepImmutabilityAndSanitization(unittest.TestCase):
    """Pruebas de inmutabilidad real y sanitización de secretos en esquemas."""

    def test_inmutabilidad_profunda_en_parametros_y_metadatos(self):
        ctx = AgentContext.crear(canal="web_lan", metadatos={"clave": "valor_original"})
        inp = AgentInput(contenido="Texto", contexto=ctx, parametros={"p1": 100})

        # Comprobar que son MappingProxyType
        self.assertIsInstance(inp.parametros, MappingProxyType)
        self.assertIsInstance(ctx.metadatos, MappingProxyType)

        # Modificación externa debe lanzar TypeError
        with self.assertRaises(TypeError):
            inp.parametros["hack"] = "nuevo"  # type: ignore

        with self.assertRaises(TypeError):
            ctx.metadatos["hack"] = "nuevo"  # type: ignore

    def test_sanitizacion_de_claves_sensibles_en_errores(self):
        err = AgentError(
            codigo=AgentErrorCode.EXECUTION_FAILED,
            mensaje="Fallo de conexión",
            detalles={
                "db_host": "127.0.0.1",
                "db_password": "super_secret_password_123",
                "api_token": "sk-proj-xyz987654321",
                "anidado": {"client_secret": "my_secret_key"},
            },
        )
        dict_err = err.to_dict()
        self.assertEqual(dict_err["detalles"]["db_host"], "127.0.0.1")
        self.assertEqual(dict_err["detalles"]["db_password"], "[PROTEGIDO]")
        self.assertEqual(dict_err["detalles"]["api_token"], "[PROTEGIDO]")
        self.assertEqual(dict_err["detalles"]["anidado"]["client_secret"], "[PROTEGIDO]")

    def test_deserializacion_rechaza_datos_truncados_o_inconsistentes(self):
        # Diccionario con exitoso=True pero sin salida
        with self.assertRaises(ContractValidationError):
            AgentResult.from_dict({"exitoso": True, "correlation_id": "c-1", "salida": None})

        # Diccionario con exitoso=False pero sin error
        with self.assertRaises(ContractValidationError):
            AgentResult.from_dict({"exitoso": False, "correlation_id": "c-1", "error": None})

        # Diccionario con exitoso=True y error presente al mismo tiempo
        with self.assertRaises(ContractValidationError):
            AgentResult.from_dict({
                "exitoso": True,
                "correlation_id": "c-1",
                "salida": {"respuesta": "OK", "datos": {}, "modalidad": "texto"},
                "error": {"codigo": "INTERNAL_ERROR", "mensaje": "Fallo"},
            })

    def test_retry_policy_edge_cases_y_seguridad_no_idempotente(self):
        """Verifica que la política de reintentos maneje de forma segura:
        1. error=None sin generar excepciones AttributeError.
        2. Prohibición absoluta de reintentos en NON_IDEMPOTENT.
        3. Errores no reintentables (UNAUTHORIZED, CIRCUIT_OPEN, etc.).
        """
        policy = RetryPolicy(max_reintentos=3)

        # 1. error=None no debe lanzar excepción
        self.assertFalse(policy.es_reintentable(None, IdempotencyMode.READ_ONLY))  # type: ignore

        # 2. Operación no idempotente NUNCA se reintenta, incluso ante timeout
        err_timeout = AgentError(codigo=AgentErrorCode.TIMEOUT, mensaje="Timeout transitorio")
        self.assertFalse(policy.es_reintentable(err_timeout, IdempotencyMode.NON_IDEMPOTENT))

        # 3. Operación de solo lectura sí admite reintento ante timeout transitorio
        self.assertTrue(policy.es_reintentable(err_timeout, IdempotencyMode.READ_ONLY))

        # 4. Errores de seguridad o sobrecarga no se reintentan
        err_auth = AgentError(codigo=AgentErrorCode.UNAUTHORIZED, mensaje="No auth")
        self.assertFalse(policy.es_reintentable(err_auth, IdempotencyMode.READ_ONLY))

        err_cb = AgentError(codigo=AgentErrorCode.CIRCUIT_OPEN, mensaje="Circuito abierto")
        self.assertFalse(policy.es_reintentable(err_cb, IdempotencyMode.READ_ONLY))


if __name__ == "__main__":
    unittest.main()
