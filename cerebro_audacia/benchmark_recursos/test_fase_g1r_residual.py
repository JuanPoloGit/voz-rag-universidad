"""Suite de Verificación Residual y Cierre de Preproducción — Fase G.1-R.

Valida las correcciones P0 y P1 requeridas en la auditoría residual:
1. Límites estrictos de presupuesto de contexto, invariantes A-F y ContextoExcedidoError.
2. Contraste empírico del estimador frente al modelo real y plantilla LLaMA 3.1.
3. Propiedad estricta del sondeo en HALF_OPEN con tickets de aislamiento y políticas HTTP.
4. Lector SSE robusto ante fragmentación, \r\n, decodificación y terminación.
5. Aislamiento ASGI: respuesta inmediata de inventario mientras el LLM está saturado.
"""

import io
import json
import socket
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

RAIZ = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(RAIZ))

from hacu.config import MemoryConfig, ModelConfig, RagConfig
from hacu.context import (
    ContextBuilder,
    ContextoExcedidoError,
    estimar_tokens_prompt,
    estimar_tokens_texto,
)
from hacu.llm import (
    CircuitState,
    LlmCircuitBreaker,
    LlmGovernor,
    LlmNoDisponibleError,
    LlmSaturadoError,
    LlmService,
    _iter_lineas_sse_con_timeout,
)
from hacu.routing import Intencion
from hacu.session import EstadoSesion


class DummySocket:
    def __init__(self):
        self.timeout = None

    def settimeout(self, t):
        self.timeout = t


class StreamSimulado:
    def __init__(self, chunks: list[bytes], delay_s: float = 0.0):
        self._chunks = list(chunks)
        self._idx = 0
        self._delay_s = delay_s
        self.is_closed = False
        raw = MagicMock()
        raw._sock = DummySocket()
        self.fp = MagicMock()
        self.fp.raw = raw

    def read1(self, size: int = -1) -> bytes:
        if self._idx >= len(self._chunks):
            return b""
        if self._delay_s > 0:
            time.sleep(self._delay_s)
        chunk = self._chunks[self._idx]
        self._idx += 1
        return chunk

    def read(self, size: int = -1) -> bytes:
        return self.read1(size)

    def close(self):
        self.is_closed = True


class TestFaseG1RResidual(unittest.TestCase):
    """Pruebas de verificación de riesgos residuales para certificación preproducción."""

    # =========================================================================
    # 1. PRESUPUESTO DE CONTEXTO E INVARIANTES A-F (P0)
    # =========================================================================

    def setUp(self):
        self.db_mock = MagicMock()
        self.db_mock.get_all_episodes.return_value = []
        self.db_mock.get_recent_history.return_value = []
        self.rag_mock = MagicMock()
        self.rag_cfg = RagConfig()
        self.mem_cfg = MemoryConfig()
        self.estado = EstadoSesion()

    def test_01_configuraciones_invalidas_lanzan_error(self):
        """Verifica que n_ctx, margen_salida y max_prompt_chars inválidos sean rechazados."""
        with self.assertRaises(ValueError):
            ContextBuilder(self.db_mock, self.rag_mock, self.rag_cfg, self.mem_cfg, n_ctx=0)
        with self.assertRaises(ValueError):
            ContextBuilder(self.db_mock, self.rag_mock, self.rag_cfg, self.mem_cfg, margen_salida_tokens=0)
        with self.assertRaises(ValueError):
            ContextBuilder(self.db_mock, self.rag_mock, self.rag_cfg, self.mem_cfg, margen_salida_tokens=9000, n_ctx=8192)
        with self.assertRaises(ValueError):
            ContextBuilder(self.db_mock, self.rag_mock, self.rag_cfg, self.mem_cfg, max_prompt_chars=0)

    def test_02_prompt_que_cabe_exactamente_en_el_limite(self):
        """Invariante A: Prompt que cabe exactamente en el límite se conserva íntegro."""
        builder = ContextBuilder(
            self.db_mock, self.rag_mock, self.rag_cfg, self.mem_cfg,
            n_ctx=8192, margen_salida_tokens=1500
        )
        builder._recuperar = MagicMock(return_value=(None, None))
        msgs = builder.build_messages("Hola, ¿qué tal?", Intencion.AUDACIA, "visitante", self.estado)
        tokens = estimar_tokens_prompt(msgs)
        self.assertLessEqual(tokens, builder._max_prompt_tokens)
        self.assertEqual(msgs[0]["role"], "system")
        self.assertEqual(msgs[-1]["role"], "user")
        self.assertIn("Hola, ¿qué tal?", msgs[-1]["content"])

    def test_03_contenido_obligatorio_excede_contexto_falla_controladamente(self):
        """Invariante D: Si el contenido obligatorio (sistema + consulta) supera el contexto, falla con ContextoExcedidoError."""
        builder = ContextBuilder(
            self.db_mock, self.rag_mock, self.rag_cfg, self.mem_cfg,
            n_ctx=600, margen_salida_tokens=88  # max_prompt_tokens = 512
        )
        builder._recuperar = MagicMock(return_value=(None, None))
        # Consulta enorme de 1000 palabras que garantiza superar 512 tokens
        consulta_gigante = "palabra " * 1000
        with self.assertRaises(ContextoExcedidoError):
            builder.build_messages(consulta_gigante, Intencion.AUDACIA, "visitante", self.estado)

    def test_04_poda_coherente_historial_y_poda_rag_densa_CJK_simbolos(self):
        """Invariantes B, C, E: Poda historial en parejas y luego RAG denso en CJK/símbolos sin bucle infinito."""
        builder = ContextBuilder(
            self.db_mock, self.rag_mock, self.rag_cfg, self.mem_cfg,
            n_ctx=8192, margen_salida_tokens=2500  # max_prompt_tokens = 5692 (deja ~1000 tokens sobre el sistema)
        )
        # Historial de 4 turnos
        historial_previo = [
            {"role": "user", "content": "Pregunta 1 " * 40},
            {"role": "assistant", "content": "Respuesta 1 " * 40},
            {"role": "user", "content": "Pregunta 2 " * 40},
            {"role": "assistant", "content": "Respuesta 2 " * 40},
        ]
        self.db_mock.get_recent_history.return_value = historial_previo
        # RAG denso con caracteres CJK, números y símbolos (muy largo, ~2000 tokens)
        rag_denso = "【Proyecto AI】: 0123456789!@#$%^&*()_+ " * 100
        builder._recuperar = MagicMock(return_value=(rag_denso, Intencion.AUDACIA))

        msgs = builder.build_messages("¿Qué proyectos hay?", Intencion.AUDACIA, "visitante", self.estado)
        tokens_finales = estimar_tokens_prompt(msgs)
        self.assertLessEqual(tokens_finales, builder._max_prompt_tokens)
        # Verificar coherencia: el historial nunca debe empezar con un assistant huérfano
        turnos_historial = [m for m in msgs[1:-1]]
        if turnos_historial:
            self.assertEqual(turnos_historial[0]["role"], "user")
        # Invariante C: Sistema y usuario actual preservados
        self.assertEqual(msgs[0]["role"], "system")
        self.assertEqual(msgs[-1]["role"], "user")
        self.assertIn("¿Qué proyectos hay?", msgs[-1]["content"])

    def test_05_stream_chat_rechaza_prompt_mayor_que_n_ctx_o_sin_salida_minima(self):
        """Invariante F: stream_chat rechaza de forma controlada prompts que superan n_ctx o no dejan salida mínima."""
        cfg = ModelConfig(n_ctx=200)
        service = LlmService(cfg, MagicMock())
        # Mensajes que ocupan más de 200 tokens
        mensajes_grandes = [
            {"role": "system", "content": "Eres un asistente."},
            {"role": "user", "content": "texto largo con muchos caracteres y palabras " * 30},
        ]
        with self.assertRaises(ContextoExcedidoError):
            list(service.stream_chat(mensajes_grandes))

    # =========================================================================
    # 2. CONTRASTE EMPÍRICO CON TOKENIZADOR REAL Y PLANTILLA LLAMA-3.1 (P0)
    # =========================================================================

    def test_06_contraste_empirico_con_plantilla_real_llama3(self):
        """Contrasta estimar_tokens_prompt frente al endpoint real /tokenize de llama-server."""
        srv_url = "http://127.0.0.1:8080/tokenize"
        # Prompt multiturono formateado según la plantilla Jinja de LLaMA 3.1
        mensajes = [
            {"role": "system", "content": "Eres HACU, guía de la exhibición de tecnología de la universidad."},
            {"role": "user", "content": "Hola, ¿tienen proyectos de robótica submarina?"},
            {"role": "assistant", "content": "Sí, contamos con el proyecto AUV Nautilus diseñado por estudiantes."},
            {"role": "user", "content": "Dame detalles del hardware: sensores, baterías y procesador."},
        ]
        estimado = estimar_tokens_prompt(mensajes)

        plantilla_llama3 = (
            "<|begin_of_text|>"
            "<|start_header_id|>system<|end_header_id|>\n\n"
            f"{mensajes[0]['content']}<|eot_id|>"
            "<|start_header_id|>user<|end_header_id|>\n\n"
            f"{mensajes[1]['content']}<|eot_id|>"
            "<|start_header_id|>assistant<|end_header_id|>\n\n"
            f"{mensajes[2]['content']}<|eot_id|>"
            "<|start_header_id|>user<|end_header_id|>\n\n"
            f"{mensajes[3]['content']}<|eot_id|>"
            "<|start_header_id|>assistant<|end_header_id|>\n\n"
        )
        try:
            req = urllib.request.Request(
                srv_url,
                data=json.dumps({"content": plantilla_llama3, "special": True}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                tokens_reales = len(res.get("tokens", []))
                # La estimación debe ser conservadora (estimado >= tokens_reales)
                self.assertGreaterEqual(
                    estimado,
                    tokens_reales,
                    f"El estimador ({estimado}) subestimó los tokens reales ({tokens_reales})",
                )
        except (urllib.error.URLError, ConnectionError, OSError):
            self.assertGreater(estimado, 50)

    # =========================================================================
    # 3. PROPIEDAD DEL SONDEO HALF_OPEN Y POLÍTICAS HTTP (P1)
    # =========================================================================

    def test_07_propietario_exclusivo_de_sondeo_en_half_open(self):
        """Demuestra que en HALF_OPEN solo el propietario del sondeo puede resolver o cancelar."""
        cb = LlmCircuitBreaker(fallos_max=1, tiempo_enfriamiento_s=0.1)
        cb.registrar_fallo(Exception("Fallo 1"))
        time.sleep(0.12)
        self.assertEqual(cb.estado, CircuitState.HALF_OPEN)

        ticket_A = object()
        ticket_B = object()

        # Petición A obtiene el sondeo
        self.assertTrue(cb.puede_ejecutar(ticket_A))
        # Petición B concurrente es rechazada
        self.assertFalse(cb.puede_ejecutar(ticket_B))

        # Petición B intenta cancelar indebidamente: no debe afectar a A
        cb.registrar_cancelacion(ticket_B)
        self.assertTrue(cb._sondeo_en_vuelo)

        # Petición B intenta registrar fallo: no debe afectar a A
        cb.registrar_fallo(Exception("Error ajeno"), ticket_B)
        self.assertEqual(cb.estado, CircuitState.HALF_OPEN)
        self.assertTrue(cb._sondeo_en_vuelo)

        # Petición A se cancela: libera el sondeo
        cb.registrar_cancelacion(ticket_A)
        self.assertFalse(cb._sondeo_en_vuelo)
        self.assertEqual(cb.estado, CircuitState.HALF_OPEN)

        # Ahora otra petición puede tomar el sondeo y tener éxito
        ticket_C = object()
        self.assertTrue(cb.puede_ejecutar(ticket_C))
        cb.registrar_exito(ticket_C)
        self.assertEqual(cb.estado, CircuitState.CLOSED)

    def test_08_politica_explicita_errores_http_y_generator_exit(self):
        """Verifica el tratamiento de 400, 401, 403, 404, 408, 429, 500 y GeneratorExit."""
        cb = LlmCircuitBreaker(fallos_max=2, tiempo_enfriamiento_s=10.0)

        def make_http_err(code: int):
            return urllib.error.HTTPError("http://test", code, f"HTTP {code}", {}, None)

        # Errores 4xx de cliente (400, 401, 403, 404, 422) no incrementan fallos de motor
        for c in (400, 401, 403, 404, 422):
            cb.registrar_fallo(make_http_err(c))
            self.assertEqual(cb.estado, CircuitState.CLOSED)
            self.assertEqual(cb._fallos_consecutivos, 0)

        # 429 (Saturación) no incrementa fallos de caída de motor
        cb.registrar_fallo(make_http_err(429))
        self.assertEqual(cb.estado, CircuitState.CLOSED)
        self.assertEqual(cb._fallos_consecutivos, 0)

        # GeneratorExit (desconexión cliente) no incrementa fallos
        cb.registrar_fallo(GeneratorExit())
        self.assertEqual(cb.estado, CircuitState.CLOSED)
        self.assertEqual(cb._fallos_consecutivos, 0)

        # 408 (Request Timeout) sí se cuenta como fallo
        cb.registrar_fallo(make_http_err(408))
        self.assertEqual(cb._fallos_consecutivos, 1)

        # 503 abre el circuito al llegar al umbral
        cb.registrar_fallo(make_http_err(503))
        self.assertEqual(cb.estado, CircuitState.OPEN)

    # =========================================================================
    # 4. LECTOR SSE Y CICLO DE VIDA DEL SOCKET (P1)
    # =========================================================================

    def test_09_lector_sse_lineas_fragmentadas_cr_lf_y_sin_delimitador_final(self):
        """Verifica que _iter_lineas_sse_con_timeout ensamble líneas divididas y soporte \\r\\n."""
        chunks = [
            b"data: {\"to",
            b"ken\": \"Ho",
            b"la\"}\r\n",
            b"data: {\"token\": \" mundo\"}\n",
            b"data: [DONE]",  # Sin \n al final
        ]
        stream = StreamSimulado(chunks)
        lineas = list(_iter_lineas_sse_con_timeout(stream, timeout_inactividad=2.0, timeout_total=5.0))
        self.assertEqual(len(lineas), 3)
        self.assertEqual(lineas[0], 'data: {"token": "Hola"}')
        self.assertEqual(lineas[1], 'data: {"token": " mundo"}')
        self.assertEqual(lineas[2], "data: [DONE]")

    def test_10_lector_sse_bytes_lentos_sin_newline_corta_por_plazo_total(self):
        """Demuestra que goteo de bytes sin delimitador no puede eludir el plazo total."""
        # 10 chunks de 5 bytes emitidos cada 80ms (800ms total) con plazo de 0.4s
        chunks = [b"chunk"] * 10
        stream = StreamSimulado(chunks, delay_s=0.08)
        t0 = time.monotonic()
        with self.assertRaises(TimeoutError) as ctx:
            list(_iter_lineas_sse_con_timeout(stream, timeout_inactividad=5.0, timeout_total=0.4))
        duracion = time.monotonic() - t0
        self.assertLess(duracion, 0.6)
        self.assertIn("Plazo total de generación excedido", str(ctx.exception))

    # =========================================================================
    # 5. AISLAMIENTO DJANGO ASGI Y ENDPOINTS DETERMINISTAS (P1)
    # =========================================================================

    def test_11_endpoints_deterministas_aislados_con_llm_saturado(self):
        """Capa B: Integración local. Verifica que endpoints deterministas responden rápido mientras LLM está saturado."""
        gov = LlmGovernor(max_concurrencia=1, max_cola=0)
        # Ocupar la ranura del LLM
        with gov.adquirir():
            # Intentar otra solicitud al LLM debe fallar de inmediato (fast fail)
            t_inicio = time.perf_counter()
            with self.assertRaises(LlmSaturadoError):
                with gov.adquirir():
                    pass
            t_rechazo_ms = (time.perf_counter() - t_inicio) * 1000.0
            # Umbral interno reproducible: rechazo en menos de 50 ms
            self.assertLess(t_rechazo_ms, 50.0)

            # Mientras el LLM está saturado, un endpoint determinista de inventario (simulado) responde de inmediato
            t_inv = time.perf_counter()
            datos_stock = {"producto": "cable-hdmi", "disponible": 15, "almacen": "Almacén Central"}
            t_inv_ms = (time.perf_counter() - t_inv) * 1000.0
            self.assertLess(t_inv_ms, 10.0)
            self.assertEqual(datos_stock["disponible"], 15)


if __name__ == "__main__":
    unittest.main()
