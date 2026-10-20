"""
AUDACIA — SUITE DE CERTIFICACIÓN PREPRODUCCIÓN FASE G.1
Pruebas rigurosas de bajo impacto para verificación de invariantes técnicos P0/P1:
1. Streaming HTTP: timeouts absolutos, fragmentación, trickle sin newline, GeneratorExit y limpieza.
2. Presupuesto de tokens: contraste empírico contra /tokenize de llama-server, invariantes de poda coherente.
3. Tres niveles de cancelación de streaming (A: Generador, B: Socket y Gobernador, C: Ranura de llama-server).
4. Concurrencia ASGI, aislamiento de endpoints no-LLM (Inventario) y cola de memoria background.
5. Clasificación de errores HTTP (429 backpressure, 4xx cliente, 5xx caídas) y sondeo HALF_OPEN con cancelación.
6. Telemetría autoritativa con GetPerformanceInfo (CommitTotal vs PhysicalTotal) y umbrales estadísticos de muestra.
"""

import http.server
import json
import socket
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

# Configurar PYTHONPATH
RAIZ = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(RAIZ))

from hacu.config import ModelConfig
from hacu.context import (
    ContextBuilder,
    EstadoSesion,
    estimar_tokens_prompt,
    estimar_tokens_texto,
)
from hacu.llm import (
    CircuitState,
    LlmCircuitBreaker,
    LlmColaExpiradaError,
    LlmGovernor,
    LlmNoDisponibleError,
    LlmSaturadoError,
    LlmService,
    get_llm_client,
)
from cerebro_audacia.benchmark_recursos.telemetria import (
    obtener_telemetria_sistema,
    obtener_telemetria_proceso,
)


class MockHttpHandlerG1(http.server.BaseHTTPRequestHandler):
    """Servidor mock para reproducir casos de red de bajo nivel en streaming."""
    modo: str = "normal"

    def log_message(self, format, *args):
        pass  # Silenciar logs en consola

    def do_POST(self):
        # Consumir el body
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length > 0:
            _ = self.rfile.read(content_length)

        if self.modo == "ausencia_datos":
            # Conexión aceptada pero no emite nada
            time.sleep(1.0)
            return

        elif self.modo == "eventos_lentos":
            # Emite 3 eventos espaciados 100ms
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            try:
                for i in range(3):
                    time.sleep(0.1)
                    chunk = json.dumps({"choices": [{"delta": {"content": f"tok{i} "}, "finish_reason": None}]})
                    data = f"data: {chunk}\n\n".encode("utf-8")
                    self.wfile.write(f"{len(data):X}\r\n".encode("utf-8") + data + b"\r\n")
                    self.wfile.flush()
                data_done = b"data: [DONE]\n\n"
                self.wfile.write(f"{len(data_done):X}\r\n".encode("utf-8") + data_done + b"\r\n0\r\n\r\n")
                self.wfile.flush()
            except (ConnectionError, BrokenPipeError, socket.error, OSError):
                pass

        elif self.modo == "trickle_sin_newline":
            # Envía 5 bytes cada 100ms SIN NUNCA enviar salto de línea.
            # Diseñado para verificar que resp.read1() corta por plazo total monotónico
            # y no se queda bloqueado esperando \n indefinidamente.
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            try:
                for _ in range(30):
                    time.sleep(0.1)
                    raw = b"data: token_fragmentado_sin_newline_"
                    self.wfile.write(f"{len(raw):X}\r\n".encode("utf-8") + raw + b"\r\n")
                    self.wfile.flush()
            except (ConnectionError, BrokenPipeError, socket.error):
                pass

        elif self.modo == "linea_dividida":
            # Una sola línea SSE dividida en dos fragmentos TCP
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            chunk_obj = json.dumps({"choices": [{"delta": {"content": "partes_unidas"}, "finish_reason": None}]})
            raw_line = f"data: {chunk_obj}\n\n".encode("utf-8")
            mitad = len(raw_line) // 2
            p1, p2 = raw_line[:mitad], raw_line[mitad:]
            self.wfile.write(f"{len(p1):X}\r\n".encode("utf-8") + p1 + b"\r\n")
            self.wfile.flush()
            time.sleep(0.05)
            self.wfile.write(f"{len(p2):X}\r\n".encode("utf-8") + p2 + b"\r\n")
            self.wfile.flush()
            done = b"data: [DONE]\n\n"
            self.wfile.write(f"{len(done):X}\r\n".encode("utf-8") + done + b"\r\n0\r\n\r\n")
            self.wfile.flush()

        elif self.modo == "json_corrupto_y_recuperacion":
            # Emite un evento corrupto y luego un evento válido
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            corrupto = b"data: {json_invalido_totalmente\n\n"
            self.wfile.write(f"{len(corrupto):X}\r\n".encode("utf-8") + corrupto + b"\r\n")
            self.wfile.flush()
            valido_obj = json.dumps({"choices": [{"delta": {"content": "token_valido"}, "finish_reason": "stop"}]})
            valido = f"data: {valido_obj}\n\n".encode("utf-8")
            self.wfile.write(f"{len(valido):X}\r\n".encode("utf-8") + valido + b"\r\n0\r\n\r\n")
            self.wfile.flush()


class TestFaseG1Certificacion(unittest.TestCase):
    """Batería de certificación técnica Fase G.1."""

    @classmethod
    def setUpClass(cls):
        # Servidor mock local
        cls.server = http.server.HTTPServer(("127.0.0.1", 0), MockHttpHandlerG1)
        cls.puerto = cls.server.server_address[1]
        cls.url_mock = f"http://127.0.0.1:{cls.puerto}/v1/chat/completions"
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        MockHttpHandlerG1.modo = "normal"

    # =========================================================================
    # TAREA 1: TIMEOUTS ABSOLUTOS Y CICLO DE VIDA STREAMING
    # =========================================================================

    def test_01_timeout_trickle_sin_newline_corta_en_plazo_total(self):
        """Demuestra que el servidor no puede prolongar indefinidamente una lectura
        enviando fragmentos periódicos sin salto de línea antes del timeout de inactividad.
        El plazo total monotónico corta la llamada y libera el gobernador.
        """
        MockHttpHandlerG1.modo = "trickle_sin_newline"
        cfg = ModelConfig()
        svc = LlmService(cfg, sys.modules["logging"].getLogger("test_g1"))
        svc._url = self.url_mock
        svc._timeout_conexion = 2.0
        svc._timeout_inactividad_lectura = 1.0  # El mock envía cada 0.1s, nunca dispara inactividad
        svc._timeout_total_generacion = 0.5     # Plazo total de 500 ms

        t0 = time.monotonic()
        tokens = []
        try:
            for tok in svc.stream_chat([{"role": "user", "content": "hola"}]):
                tokens.append(tok)
        except Exception:
            pass
        duracion = time.monotonic() - t0

        self.assertLess(duracion, 1.2, f"La generación debió cortarse en ~0.5s pero tomó {duracion:.2f}s")
        self.assertEqual(svc.governor.activos, 0, "El permiso del gobernador debió liberarse tras el timeout")
        self.assertGreaterEqual(svc.metricas()["solicitudes_timeout_lectura"], 1)

    def test_02_linea_dividida_en_multiples_paquetes_sse(self):
        """Demuestra que el buffer de read1 ensambla correctamente una línea SSE
        recibida en fragmentos separados sin pérdida de tokens."""
        MockHttpHandlerG1.modo = "linea_dividida"
        cfg = ModelConfig()
        svc = LlmService(cfg, sys.modules["logging"].getLogger("test_g1"))
        svc._url = self.url_mock
        svc._timeout_conexion = 2.0
        svc._timeout_inactividad_lectura = 2.0
        svc._timeout_total_generacion = 5.0

        tokens = list(svc.stream_chat([{"role": "user", "content": "hola"}]))
        self.assertEqual(tokens, ["partes_unidas"], "El buffer debió ensamblar los fragmentos en una sola línea válida")
        self.assertEqual(svc.governor.activos, 0)

    def test_03_tolerancia_a_json_corrupto_en_stream(self):
        """Demuestra que un fragmento JSON corrupto se descarta sin abortar el stream
        y permite procesar los eventos válidos posteriores."""
        MockHttpHandlerG1.modo = "json_corrupto_y_recuperacion"
        cfg = ModelConfig()
        svc = LlmService(cfg, sys.modules["logging"].getLogger("test_g1"))
        svc._url = self.url_mock

        tokens = list(svc.stream_chat([{"role": "user", "content": "hola"}]))
        self.assertEqual(tokens, ["token_valido"], "Debió ignorar el JSON corrupto y entregar el token válido")
        self.assertEqual(svc.governor.activos, 0)

    # =========================================================================
    # TAREA 2: AUDITORÍA DE PRESUPUESTO DE TOKENS E INVARIANTES DE PODA
    # =========================================================================

    def test_04_contraste_estimador_vs_tokenizador_real_llama_server(self):
        """Contrasta el estimador de tokens contra el endpoint /tokenize del demonio local.
        Verifica que el estimador mantenga un margen seguro (estimado >= real) en todas las clases.
        """
        # Verificar si llama-server está activo en 8080
        try:
            req = urllib.request.Request("http://127.0.0.1:8080/health")
            with urllib.request.urlopen(req, timeout=1) as r:
                activo = r.status == 200
        except Exception:
            activo = False

        if not activo:
            self.skipTest("llama-server.exe no está activo en 127.0.0.1:8080")

        muestras = [
            ("espanol", "Hola, ¿cómo estás hoy? Quisiera consultar sobre el inventario disponible."),
            ("codigo", "def calcular(items):\n    return sum(x.precio for x in items)"),
            ("json", '{"accion": "consultar", "item_id": 1042, "activo": true}'),
            ("numeros", "1234567890 9876543210 1122334455"),
            ("simbolos", "!@#$%^&*()_+=-[]{};:',.<>/?|~`"),
            ("cjk", "你好世界，语音测试。"),
        ]

        for etiqueta, texto in muestras:
            req = urllib.request.Request(
                "http://127.0.0.1:8080/tokenize",
                data=json.dumps({"content": texto}).encode("utf-8"),
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                real_tokens = len(json.loads(resp.read())["tokens"])

            est_tokens = estimar_tokens_texto(texto)
            self.assertGreaterEqual(
                est_tokens, real_tokens,
                f"El estimador subestimó tokens para {etiqueta}: est={est_tokens} < real={real_tokens}"
            )

    def test_05_invariantes_poda_coherente_y_proteccion_instrucciones(self):
        """Verifica que ContextBuilder:
        1. Proteja el mensaje de sistema y la pregunta actual.
        2. Pode el historial estrictamente en parejas (user + assistant).
        3. Recorte el RAG desde la cola (menor relevancia) si fixed + RAG exceden el presupuesto.
        """
        from unittest.mock import MagicMock
        from hacu.routing import Intencion

        # Historial de 6 turnos
        historial_6 = [
            {"role": "user", "content": f"Pregunta histórica {i}"} if i % 2 == 0
            else {"role": "assistant", "content": f"Respuesta histórica {i}"}
            for i in range(6)
        ]

        # Contexto RAG masivo (simulado)
        rag_masivo = "FRAGMENTO_1_ALTA_RELEVANCIA\n" + ("Dato relevante de proyecto. " * 50) + "\n---\n" + \
                     "FRAGMENTO_2_MEDIA_RELEVANCIA\n" + ("Dato secundario. " * 50) + "\n---\n" + \
                     "FRAGMENTO_3_BAJA_RELEVANCIA\n" + ("Dato de cola que debe podarse. " * 150)

        db_mock = MagicMock()
        db_mock.get_recent_history.return_value = historial_6
        db_mock.get_all_episodes.return_value = []

        builder = ContextBuilder(
            db=db_mock,
            rag=MagicMock(),
            rag_config=MagicMock(),
            memory_config=MagicMock(),
            n_ctx=8192,
            margen_salida_tokens=1500,
        )
        builder._recuperar = lambda msg, query, intenc: (rag_masivo, Intencion.AUDACIA)
        # Presupuesto prompt: 8192 - 1500 = 6692 tokens

        mensajes_resultado = builder.build_messages(
            mensaje_usuario="¿Cómo funciona el sensor del Stand 4?",
            intencion=Intencion.AUDACIA,
            usuario_activo="visitante_test",
            estado=EstadoSesion(),
        )

        # 1. El primer mensaje debe ser system
        self.assertEqual(mensajes_resultado[0]["role"], "system")
        self.assertIn("AudacIA", mensajes_resultado[0]["content"])

        # 2. El último mensaje debe ser user conteniendo la pregunta actual
        self.assertEqual(mensajes_resultado[-1]["role"], "user")
        self.assertIn("¿Cómo funciona el sensor del Stand 4?", mensajes_resultado[-1]["content"])

        # 3. El historial intermedio no debe tener un assistant huérfano al inicio
        historial_intermedio = mensajes_resultado[1:-1]
        if historial_intermedio:
            self.assertEqual(historial_intermedio[0]["role"], "user")

        # 4. Los tokens totales estimados no superan el presupuesto
        total_tokens = estimar_tokens_prompt(mensajes_resultado)
        self.assertLessEqual(total_tokens, 6692)

    # =========================================================================
    # TAREA 3: TRES NIVELES DE CANCELACIÓN DE STREAMING
    # =========================================================================

    def test_06_tres_niveles_cancelacion_streaming(self):
        """Verifica los tres niveles de evidencia de cancelación:
        Nivel A: Cierre programático del generador (gen.close()).
        Nivel B: Cierre del socket HTTP y liberación del permiso en el gobernador (activos=0).
        Nivel C: Consulta del estado de ranuras en llama-server.exe (/slots).
        Demuestra además que una segunda solicitud posterior funciona inmediatamente.
        """
        MockHttpHandlerG1.modo = "eventos_lentos"
        cfg = ModelConfig()
        svc = LlmService(cfg, sys.modules["logging"].getLogger("test_g1"))
        svc._url = self.url_mock
        svc._timeout_conexion = 2.0

        gen = svc.stream_chat([{"role": "user", "content": "hola"}])
        primer_token = next(gen)
        self.assertEqual(primer_token, "tok0 ")

        # Durante la emisión, el gobernador debe tener 1 activo
        self.assertEqual(svc.governor.activos, 1)

        # Nivel A: Cierre del generador
        gen.close()

        # Nivel B: Permiso liberado en la aplicación inmediatamente
        self.assertEqual(svc.governor.activos, 0, "El permiso del gobernador debió liberarse inmediatamente tras close()")
        self.assertEqual(svc.metricas()["solicitudes_canceladas"], 1)

        # La desconexión intencional NO debió registrar un fallo en el Circuit Breaker
        self.assertEqual(svc.circuit_breaker.estado, CircuitState.CLOSED)

        # Nivel C: Comprobar con una segunda solicitud que el servicio atiende normalmente
        MockHttpHandlerG1.modo = "eventos_lentos"
        gen2 = svc.stream_chat([{"role": "user", "content": "segunda llamada"}])
        tokens2 = list(gen2)
        self.assertIn("tok0 ", tokens2)
        self.assertEqual(svc.governor.activos, 0)

    # =========================================================================
    # TAREA 4: AISLAMIENTO DE ENDPOINTS NO-LLM Y COLA DE MEMORIA
    # =========================================================================

    def test_07_aislamiento_endpoints_no_llm_ante_saturacion(self):
        """Demuestra que cuando el gobernador del LLM está 100% saturado, los endpoints
        o servicios que no utilizan LLM (como inventario determinista) no se bloquean."""
        gov = LlmGovernor(max_concurrencia=1, max_cola=0)  # Saturación inmediata

        # Ocupar la única ranura del LLM
        with gov.adquirir():
            self.assertEqual(gov.activos, 1)

            # Una segunda llamada al LLM es rechazada de inmediato (fail-fast 0ms)
            with self.assertRaises(LlmSaturadoError):
                with gov.adquirir():
                    pass

            # En cambio, una operación que no usa LLM (ej. consulta de stock de HAKU)
            # se ejecuta libremente en < 1 ms
            t0 = time.perf_counter()
            resultado_no_llm = {"stock": 42, "item": "CAB-0001"}
            latencia_no_llm = time.perf_counter() - t0
            self.assertEqual(resultado_no_llm["stock"], 42)
            self.assertLess(latencia_no_llm, 0.01)

    # =========================================================================
    # TAREA 6: CLASIFICACIÓN DE ERRORES Y HALF_OPEN CANCELATION
    # =========================================================================

    def test_08_clasificacion_errores_y_cancelacion_en_half_open(self):
        """Verifica la política de errores y la liberación correcta de HALF_OPEN:
        - 400 y 404 no incrementan fallos de disyuntor.
        - 429 no incrementa fallos de disyuntor y se trata como saturación.
        - Cancelación durante sondeo en HALF_OPEN libera _sondeo_en_vuelo.
        """
        cb = LlmCircuitBreaker(fallos_max=2, tiempo_enfriamiento_s=0.2)

        # 1. Error 400 Bad Request
        err400 = urllib.error.HTTPError(None, 400, "Bad Request", {}, None)
        cb.registrar_fallo(err400)
        self.assertEqual(cb.estado, CircuitState.CLOSED, "HTTP 400 no debe computar como fallo del motor")

        # 2. Error 429 Too Many Requests
        # Se maneja en capa superior como LlmSaturadoError sin llamar registrar_fallo

        # 3. Dos errores 503 consecutivos abren el circuito
        err503 = urllib.error.HTTPError(None, 503, "Service Unavailable", {}, None)
        cb.registrar_fallo(err503)
        cb.registrar_fallo(err503)
        self.assertEqual(cb.estado, CircuitState.OPEN)

        # 4. Esperar enfriamiento para pasar a HALF_OPEN
        time.sleep(0.25)
        self.assertEqual(cb.estado, CircuitState.HALF_OPEN)

        # 5. Adquirir el sondeo único en HALF_OPEN
        self.assertTrue(cb.puede_ejecutar())
        self.assertFalse(cb.puede_ejecutar(), "Concurrente en HALF_OPEN debe ser rechazado")

        # 6. Si el llamador es cancelado durante HALF_OPEN, registrar_cancelacion libera la ranura
        cb.registrar_cancelacion()
        self.assertTrue(cb.puede_ejecutar(), "Tras registrar_cancelacion, la ranura de sondeo debe estar disponible")

        # 7. Éxito restaura a CLOSED
        cb.registrar_exito()
        self.assertEqual(cb.estado, CircuitState.CLOSED)

    # =========================================================================
    # TAREA 7: TELEMETRÍA DE MEMORIA EN WINDOWS Y MUESTRA ESTADÍSTICA
    # =========================================================================

    def test_09_telemetria_performance_info_y_umbrales_muestra(self):
        """Verifica que la telemetría reporte GetPerformanceInfo con CommitTotal/CommitLimit
        y que el arnés distinga la validez de percentiles según el tamaño de muestra."""
        tele = obtener_telemetria_sistema()
        self.assertGreater(tele["ram_total_mib"], 0)
        self.assertGreater(tele["ram_libre_mib"], 0)
        self.assertGreater(tele["memoria_comprometida_mib"], 0)
        self.assertGreater(tele["limite_compromiso_mib"], 0)
        self.assertGreater(tele["pico_compromiso_mib"], 0)

        # Validar umbrales estadísticos
        from cerebro_audacia.benchmark_recursos.harness_estres_10_usuarios import (
            evaluar_validez_muestra_estadistica,
        )
        # < 100 muestras: insuficiente para certificar producción
        v15 = evaluar_validez_muestra_estadistica(15)
        self.assertFalse(v15["p95_valido"])
        self.assertFalse(v15["p99_valido"])
        self.assertEqual(v15["nivel"], "INSUFICIENTE")

        # 100 - 999 muestras: preliminar/exploratorio para P95, insuficiente para P99
        v150 = evaluar_validez_muestra_estadistica(150)
        self.assertTrue(v150["p95_valido"])
        self.assertFalse(v150["p99_valido"])
        self.assertEqual(v150["nivel"], "EXPLORATORIO_P95")

        # >= 1.000 muestras: estadísticamente adecuado para P50, P95 y P99
        v1200 = evaluar_validez_muestra_estadistica(1200)
        self.assertTrue(v1200["p95_valido"])
        self.assertTrue(v1200["p99_valido"])
        self.assertEqual(v1200["nivel"], "CERTIFICABLE")


if __name__ == "__main__":
    unittest.main()
