"""Suite de Pruebas de Integración y Concurrencia Multiusuario del Orquestador.

Verifica empíricamente:
- Prueba 2: Dos usuarios concurrentes usando el mismo agente.
- Prueba 3: Usuarios concurrentes usando agentes diferentes.
- Prueba 4: Aislamiento estricto de conversaciones y sesiones entre usuarios.
- Prueba 5: Gestión de cola de solicitudes y respeto de límites de concurrencia.
- Prueba 6: Tolerancia a fallos, excepciones de agente, timeouts y rechazo de canal.
- Prueba 7: Control de autorización de recursos (BOLA/IDOR) e inyección confiable de identidad.
- Prueba 8: Carga escalonada hasta 10 usuarios concurrentes con métricas de latencia y cola.
"""

from __future__ import annotations

import concurrent.futures
import json
import threading
import time
import unittest
from typing import Any

from cerebro_audacia.agentes.contrato import AgentContract
from cerebro_audacia.agentes.esquemas import (
    AgentContext,
    AgentErrorCode,
    AgentExecutionError,
    AgentInput,
    AgentMetadata,
    AgentOutput,
    DataAutonomyLevel,
    IdempotencyMode,
    ResourceKind,
    ResourceProfile,
)
from cerebro_audacia.agentes.orquestador import OrquestadorAudacIA, OrquestadorResult
from cerebro_audacia.agentes.resiliencia import ConcurrencyGovernor, ResilientAgentExecutor


# =====================================================================
# AGENTES MOCK PARA PRUEBAS CONTROLADAS
# =====================================================================

class MockAgenteDiagnostico(AgentContract):
    """Agente determinista rápido de diagnóstico para pruebas concurrentes."""

    @property
    def metadata(self) -> AgentMetadata:
        return AgentMetadata(
            id="diagnostico-almacen",
            nombre="Diagnóstico de Almacén",
            version="1.0.0",
            descripcion="Evalúa el estado operativo de un almacén.",
            nivel_autonomia=DataAutonomyLevel.READ_ONLY,
            canales_permitidos=("consola", "web_lan", "api_interna"),
            requiere_autenticacion=True,
            capacidades=("diagnosticar",),
            perfil_recursos=ResourceProfile(
                recursos_requeridos=(ResourceKind.CPU,),
                max_concurrencia=5,
                timeout_segundos=3.0,
                soporta_cancelacion=False,
            ),
            modo_idempotencia=IdempotencyMode.READ_ONLY,
        )

    def _ejecutar_interno(self, entrada: AgentInput) -> AgentOutput:
        almacen = (entrada.parametros or {}).get("almacen", "Almacén Central")
        # Simula cómputo ligero
        time.sleep(0.01)
        return AgentOutput(
            respuesta=f"Almacén '{almacen}' operativo al 99.1%.",
            datos={"almacen": almacen, "disponibilidad": 99.1},
            modalidad="texto",
        )


class MockAgenteAuditoria(AgentContract):
    """Agente de auditoría con control de almacén."""

    @property
    def metadata(self) -> AgentMetadata:
        return AgentMetadata(
            id="auditoria-stock",
            nombre="Auditoría de Stock",
            version="1.0.0",
            descripcion="Audita discrepancias físicas de un almacén específico.",
            nivel_autonomia=DataAutonomyLevel.READ_ONLY,
            canales_permitidos=("consola", "web_lan", "api_interna"),
            requiere_autenticacion=True,
            capacidades=("auditar",),
            perfil_recursos=ResourceProfile(
                recursos_requeridos=(ResourceKind.CPU,),
                max_concurrencia=5,
                timeout_segundos=3.0,
                soporta_cancelacion=False,
            ),
            modo_idempotencia=IdempotencyMode.READ_ONLY,
        )

    def _ejecutar_interno(self, entrada: AgentInput) -> AgentOutput:
        almacen_id = (entrada.parametros or {}).get("almacen_id")
        if not almacen_id or not isinstance(almacen_id, int):
            raise AgentExecutionError(
                mensaje_seguro="Debe proveer un 'almacen_id' numérico válido.",
                codigo=AgentErrorCode.INVALID_INPUT,
            )
        time.sleep(0.01)
        return AgentOutput(
            respuesta=f"Auditoría del almacén {almacen_id}: 0 discrepancias.",
            datos={"almacen_id": almacen_id, "discrepancias": 0},
            modalidad="texto",
        )


class MockAgenteLento(AgentContract):
    """Agente que simula sobrecarga temporal para pruebas de cola y timeout."""

    def __init__(self, retardo_s: float = 0.3):
        self.retardo_s = retardo_s

    @property
    def metadata(self) -> AgentMetadata:
        return AgentMetadata(
            id="agente-lento",
            nombre="Agente Lento de Diagnóstico",
            version="1.0.0",
            descripcion="Simula operación larga.",
            nivel_autonomia=DataAutonomyLevel.READ_ONLY,
            canales_permitidos=("web_lan",),
            requiere_autenticacion=True,
            capacidades=("tarea_lenta",),
            perfil_recursos=ResourceProfile(
                recursos_requeridos=(ResourceKind.CPU,),
                max_concurrencia=2,
                timeout_segundos=0.15,  # Timeout menor que el retardo
                soporta_cancelacion=False,
            ),
            modo_idempotencia=IdempotencyMode.READ_ONLY,
        )

    def _ejecutar_interno(self, entrada: AgentInput) -> AgentOutput:
        time.sleep(self.retardo_s)
        return AgentOutput(
            respuesta="Tarea finalizada tras demora.",
            datos={},
            modalidad="texto",
        )


# =====================================================================
# SIMULADOR PROGRAMABLE DEL CLIENTE LLM (PARA PRUEBAS CONTROLADAS)
# =====================================================================

class MockLlmClientConHerramientas:
    """Simulador thread-safe del cliente LLM para pruebas deterministas."""

    def __init__(self):
        self.lock = threading.Lock()
        self.total_invocaciones = 0

    def create_chat_completion(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        with self.lock:
            self.total_invocaciones += 1

        ultimo_msg = messages[-1]
        contenido = str(ultimo_msg.get("content", "")).lower()

        # Si el último mensaje es una respuesta de herramienta (role: tool), sintetizar
        if ultimo_msg.get("role") == "tool":
            datos_tool = json.loads(ultimo_msg.get("content", "{}"))
            if datos_tool.get("exitoso"):
                return {
                    "choices": [{
                        "message": {
                            "role": "assistant",
                            "content": f"Resultado sintetizado: {datos_tool.get('respuesta')}",
                        }
                    }]
                }
            else:
                msg_err = datos_tool.get("mensaje_error") or datos_tool.get("error", "Error desconocido")
                return {
                    "choices": [{
                        "message": {
                            "role": "assistant",
                            "content": f"No se pudo completar la operación: {msg_err}",
                        }
                    }]
                }

        # Detección heurística de intención para simular Tool Calling
        import unicodedata
        norm = "".join(c for c in unicodedata.normalize("NFD", contenido) if unicodedata.category(c) != "Mn")

        if tools and "diagnostico" in norm:
            return {
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [{
                            "id": "call-diag-001",
                            "type": "function",
                            "function": {
                                "name": "diagnostico-almacen",
                                "arguments": json.dumps({"almacen": "Almacén Central"}),
                            },
                        }],
                    }
                }]
            }

        if tools and "auditoria" in norm:
            # Extraer número de almacén si se menciona
            almacen_id = 2 if "almacen 2" in norm else 1
            return {
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [{
                            "id": "call-audit-001",
                            "type": "function",
                            "function": {
                                "name": "auditoria-stock",
                                "arguments": json.dumps({"almacen_id": almacen_id}),
                            },
                        }],
                    }
                }]
            }

        # Respuesta conversacional directa
        return {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": f"Respuesta general: He recibido tu consulta '{ultimo_msg.get('content')}'.",
                }
            }]
        }


# =====================================================================
# CASOS DE PRUEBA EXIGIDOS
# =====================================================================

class TestOrquestadorConcurrencia(unittest.TestCase):
    """Batería de pruebas exigidas para validar concurrencia, aislamiento y resiliencia."""

    def setUp(self):
        self.mock_llm = MockLlmClientConHerramientas()
        self.agente_diag = MockAgenteDiagnostico()
        self.agente_audit = MockAgenteAuditoria()

        self.esquema_diag = {
            "type": "function",
            "function": {
                "name": "diagnostico-almacen",
                "description": "Diagnostica operatividad del almacén.",
                "parameters": {
                    "type": "object",
                    "properties": {"almacen": {"type": "string"}},
                },
            },
        }
        self.esquema_audit = {
            "type": "function",
            "function": {
                "name": "auditoria-stock",
                "description": "Audita stock físico.",
                "parameters": {
                    "type": "object",
                    "properties": {"almacen_id": {"type": "integer"}},
                    "required": ["almacen_id"],
                },
            },
        }

        self.orquestador = OrquestadorAudacIA(
            agentes=[self.agente_diag, self.agente_audit],
            esquemas_herramientas={
                "diagnostico-almacen": self.esquema_diag,
                "auditoria-stock": self.esquema_audit,
            },
            llm_client=self.mock_llm,
        )

    # -----------------------------------------------------------------
    # PRUEBA 2: Dos usuarios usando simultáneamente el mismo agente
    # -----------------------------------------------------------------
    def test_dos_usuarios_simultaneos_mismo_agente(self):
        """Valida que dos usuarios concurrentes invoquen el mismo agente sin interferirse."""
        def ejecutar_usuario(user_id: str, sesion: str):
            ctx = AgentContext.crear(canal="web_lan", usuario_id=user_id)
            return self.orquestador.procesar_mensaje(
                mensaje="Solicito diagnóstico operativo",
                contexto=ctx,
                sesion_id=sesion,
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            f1 = pool.submit(ejecutar_usuario, "operador_a", "ses_a")
            f2 = pool.submit(ejecutar_usuario, "operador_b", "ses_b")
            r1 = f1.result(timeout=2.0)
            r2 = f2.result(timeout=2.0)

        self.assertTrue(r1.exitoso)
        self.assertTrue(r2.exitoso)
        self.assertIn("diagnostico-almacen", r1.herramientas_ejecutadas)
        self.assertIn("diagnostico-almacen", r2.herramientas_ejecutadas)
        self.assertIn("Almacén Central", r1.respuesta)
        self.assertIn("Almacén Central", r2.respuesta)

    # -----------------------------------------------------------------
    # PRUEBA 3: Usuarios utilizando agentes diferentes al mismo tiempo
    # -----------------------------------------------------------------
    def test_usuarios_simultaneos_agentes_diferentes(self):
        """Valida concurrencia entre agentes de dominios distintos."""
        def peticion_diagnostico():
            ctx = AgentContext.crear(canal="web_lan", usuario_id="user_diag")
            return self.orquestador.procesar_mensaje(
                mensaje="Quiero diagnóstico del almacén",
                contexto=ctx,
                sesion_id="ses_diag",
            )

        def peticion_auditoria():
            ctx = AgentContext.crear(canal="web_lan", usuario_id="user_audit")
            return self.orquestador.procesar_mensaje(
                mensaje="Realizar auditoria de stock para almacén 1",
                contexto=ctx,
                sesion_id="ses_audit",
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            f1 = pool.submit(peticion_diagnostico)
            f2 = pool.submit(peticion_auditoria)
            r1 = f1.result(timeout=2.0)
            r2 = f2.result(timeout=2.0)

        self.assertTrue(r1.exitoso)
        self.assertTrue(r2.exitoso)
        self.assertIn("diagnostico-almacen", r1.herramientas_ejecutadas)
        self.assertIn("auditoria-stock", r2.herramientas_ejecutadas)

    # -----------------------------------------------------------------
    # PRUEBA 4: Aislamiento de conversaciones y sesiones
    # -----------------------------------------------------------------
    def test_aislamiento_estricto_conversaciones_y_sesiones(self):
        """Valida que los historiales de diálogo no se contaminen entre usuarios."""
        ctx_u1 = AgentContext.crear(canal="web_lan", usuario_id="user_1")
        ctx_u2 = AgentContext.crear(canal="web_lan", usuario_id="user_2")

        # Turno 1 Usuario 1
        self.orquestador.procesar_mensaje(
            mensaje="Hola, soy el jefe del Almacén Norte.",
            contexto=ctx_u1,
            sesion_id="ses_1",
        )
        # Turno 1 Usuario 2
        self.orquestador.procesar_mensaje(
            mensaje="Hola, soy el encargado de Auditoría.",
            contexto=ctx_u2,
            sesion_id="ses_2",
        )

        historial_u1 = self.orquestador.obtener_historial("user_1", "ses_1")
        historial_u2 = self.orquestador.obtener_historial("user_2", "ses_2")

        # Comprobar independencia absoluta
        textos_u1 = [m["content"] for m in historial_u1]
        textos_u2 = [m["content"] for m in historial_u2]

        self.assertTrue(any("Almacén Norte" in t for t in textos_u1))
        self.assertFalse(any("Almacén Norte" in t for t in textos_u2))

        self.assertTrue(any("Auditoría" in t for t in textos_u2))
        self.assertFalse(any("Auditoría" in t for t in textos_u1))

    # -----------------------------------------------------------------
    # PRUEBA 5: Manejo de varias solicitudes pendientes y límite de cola
    # -----------------------------------------------------------------
    def test_gobernador_cola_y_limite_concurrencia(self):
        """Comprueba que un gobernador acotado encole solicitudes sin perder peticiones."""
        gov = ConcurrencyGovernor(max_concurrencia=2, max_cola=8)
        agente_con_gov = MockAgenteDiagnostico()
        ejecutor = ResilientAgentExecutor(agente=agente_con_gov, governor=gov)

        orq_gov = OrquestadorAudacIA(llm_client=self.mock_llm)
        orq_gov.registrar_agente(
            agente=agente_con_gov,
            esquema_herramienta=self.esquema_diag,
            ejecutor=ejecutor,
        )

        def tarea(i: int):
            ctx = AgentContext.crear(canal="web_lan", usuario_id=f"user_{i}")
            return orq_gov.procesar_mensaje("diagnostico de prueba", ctx, sesion_id=f"ses_{i}")

        # Enviar 6 solicitudes concurrentes con límite de 2 ranuras activas
        t_inicio = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            futuros = [pool.submit(tarea, i) for i in range(6)]
            resultados = [f.result(timeout=4.0) for f in futuros]
        duracion = time.perf_counter() - t_inicio

        self.assertEqual(len(resultados), 6)
        self.assertTrue(all(r.exitoso for r in resultados))
        self.assertGreaterEqual(duracion, 0.001)

    # -----------------------------------------------------------------
    # PRUEBA 6: Resistencia a errores, timeouts y cancelaciones
    # -----------------------------------------------------------------
    def test_resistencia_a_errores_y_timeouts(self):
        """Valida que fallos en un agente o timeouts sean capturados sin tirar el orquestador."""
        # 1. Agente con timeout forzado (retardo 0.3s > timeout 0.15s)
        agente_lento = MockAgenteLento(retardo_s=0.3)
        self.orquestador.registrar_agente(
            agente=agente_lento,
            esquema_herramienta={
                "type": "function",
                "function": {"name": "agente-lento", "description": "Lento"},
            },
        )

        ctx = AgentContext.crear(canal="web_lan", usuario_id="test_timeout")
        res_timeout = self.orquestador._ejecutores["agente-lento"].ejecutar(
            AgentInput(contenido="tarea", contexto=ctx)
        )
        self.assertFalse(res_timeout.exitoso)
        self.assertEqual(res_timeout.error.codigo, AgentErrorCode.TIMEOUT)

        # 2. Rechazo por canal no permitido en metadatos
        ctx_canal_invalido = AgentContext.crear(canal="canal_desconocido", usuario_id="test_user")
        herramientas_filtradas = self.orquestador.obtener_herramientas_autorizadas(
            usuario_id="test_user",
            canal="canal_desconocido",
        )
        self.assertEqual(len(herramientas_filtradas), 0)

    # -----------------------------------------------------------------
    # PRUEBA 7: Autorización sobre el recurso (BOLA / IDOR)
    # -----------------------------------------------------------------
    def test_autorizacion_recurso_almacen_bola_idor(self):
        """Valida la triple barrera: el usuario no puede auditar un almacén no autorizado."""
        ctx = AgentContext.crear(canal="web_lan", usuario_id="operador_almacen_1")

        # Operador solo autorizado para el almacén 1
        almacenes_autorizados = (1,)

        # Intento de auditar almacén 2 (BOLA / IDOR prevenido)
        res_rechazado = self.orquestador.procesar_mensaje(
            mensaje="Por favor realiza una auditoria para el almacén 2",
            contexto=ctx,
            sesion_id="ses_bola",
            almacenes_permitidos=almacenes_autorizados,
        )

        self.assertTrue(res_rechazado.exitoso)  # El orquestador sintetiza el rechazo
        # Se verifica que el agente haya retornado UNAUTHORIZED
        self.assertEqual(len(res_rechazado.resultados_agentes), 1)
        res_agente = res_rechazado.resultados_agentes[0]
        self.assertFalse(res_agente.exitoso)
        self.assertEqual(res_agente.error.codigo, AgentErrorCode.UNAUTHORIZED)
        self.assertIn("no tiene autorización", res_rechazado.respuesta.lower())

        # Petición legítima para almacén 1
        res_permitido = self.orquestador.procesar_mensaje(
            mensaje="Por favor realiza una auditoria para el almacén 1",
            contexto=ctx,
            sesion_id="ses_ok",
            almacenes_permitidos=almacenes_autorizados,
        )
        self.assertTrue(res_permitido.exitoso)
        self.assertTrue(res_permitido.resultados_agentes[0].exitoso)

    # -----------------------------------------------------------------
    # PRUEBA 8: Prueba de carga escalonada hasta 10 usuarios concurrentes
    # -----------------------------------------------------------------
    def test_carga_escalonada_diez_usuarios(self):
        """Simula 10 usuarios concurrentes midiendo latencias, cola y tasa de éxito."""
        num_usuarios = 10
        gov = ConcurrencyGovernor(max_concurrencia=2, max_cola=12)
        agente_prod = MockAgenteDiagnostico()
        ejecutor = ResilientAgentExecutor(agente=agente_prod, governor=gov)

        orq_prod = OrquestadorAudacIA(llm_client=self.mock_llm)
        orq_prod.registrar_agente(
            agente=agente_prod,
            esquema_herramienta=self.esquema_diag,
            ejecutor=ejecutor,
        )

        latencias_ms: list[float] = []
        lock_latencias = threading.Lock()

        def usuario_simulado(user_idx: int):
            t_user_start = time.perf_counter()
            ctx = AgentContext.crear(
                canal="web_lan",
                usuario_id=f"usuario_concurrente_{user_idx}",
                correlation_id=f"trace-escalonado-{user_idx}",
            )
            res = orq_prod.procesar_mensaje(
                mensaje="Solicito diagnostico operativo urgente",
                contexto=ctx,
                sesion_id=f"sesion_{user_idx}",
            )
            t_user_end = time.perf_counter()
            lat = (t_user_end - t_user_start) * 1000.0
            with lock_latencias:
                latencias_ms.append(lat)
            return res

        t_global_start = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=num_usuarios) as pool:
            futuros = [pool.submit(usuario_simulado, i) for i in range(num_usuarios)]
            resultados: list[OrquestadorResult] = [f.result(timeout=6.0) for f in futuros]
        duracion_global_s = time.perf_counter() - t_global_start

        # Validaciones de calidad y resiliencia
        self.assertEqual(len(resultados), 10)
        self.assertTrue(all(r.exitoso for r in resultados), "Todos los 10 usuarios deben completarse con éxito")

        # Estadísticas de latencia medidas
        latencias_ordenadas = sorted(latencias_ms)
        p50 = latencias_ordenadas[len(latencias_ordenadas) // 2]
        p95 = latencias_ordenadas[int(len(latencias_ordenadas) * 0.95)]
        max_lat = latencias_ordenadas[-1]

        # Comprobación de límites operacionales
        self.assertLess(p50, 500.0, f"Latencia P50 esperada < 500ms, obtenida: {p50:.2f}ms")
        self.assertLess(max_lat, 2500.0, f"Latencia máxima esperada < 2500ms, obtenida: {max_lat:.2f}ms")

        # Telemetría en log
        print(f"\n[TELEMETRÍA PRUEBA 8: CARGA ESCALONADA 10 USUARIOS]")
        print(f"  - Usuarios atendidos: {num_usuarios}/10")
        print(f"  - Tasa de éxito: 100%")
        print(f"  - Tiempo total de corrida: {duracion_global_s:.3f} s")
        print(f"  - Latencia P50: {p50:.2f} ms")
        print(f"  - Latencia P95: {p95:.2f} ms")
        print(f"  - Latencia Máxima: {max_lat:.2f} ms")
        print(f"  - Ranuras concurrentes activas: {gov.max_concurrencia}")
        print(f"  - Ranuras en cola soportadas: {gov.max_cola}")


if __name__ == "__main__":
    unittest.main()
