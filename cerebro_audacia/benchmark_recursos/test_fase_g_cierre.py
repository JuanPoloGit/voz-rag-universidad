"""Pruebas de verificación y regresión para Fase G (Cierre técnico de concurrencia, timeouts y telemetría).

Verifica exhaustivamente:
1. Ciclo de vida del permiso streaming y prevención de doble adquisición (max_concurrencia=1).
2. Retención del permiso durante streaming y liberación en GeneratorExit (desconexión temprana).
3. Límite de inactividad vs plazo total monotónico de generación (servidor de goteo / trickle).
4. Conexiones no establecidas y sockets que dejan de emitir datos.
5. Clasificación de errores HTTP: HTTP 429 como backpressure, 400 sin disparo de circuito, 503 como fallo de motor.
6. Aislamiento de sondeo único en HALF_OPEN y recuperación limpia del Circuit Breaker.
7. Presupuesto de contexto en tokens, reserva de salida y poda coherente (sin turnos huérfanos).
8. Telemetría de memoria comprometida autoritativa en Windows vía GetPerformanceInfo.
"""

from __future__ import annotations

import http.server
import json
import logging
import math
import os
import socket
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parent.parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from hacu.config import ModelConfig, MemoryConfig, RagConfig
from hacu.context import (
    ContextBuilder,
    EstadoSesion,
    estimar_tokens_texto,
    estimar_tokens_prompt,
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
from hacu.routing import Intencion
from cerebro_audacia.benchmark_recursos.telemetria import (
    obtener_telemetria_sistema,
    PERFORMANCE_INFORMATION,
)


class MockDb:
    def __init__(self, historial=None, episodios=None):
        self._historial = list(historial or [])
        self._episodios = list(episodios or [])

    def get_recent_history(self, usuario, limite):
        return self._historial[-limite:] if limite else self._historial

    def get_all_episodes(self, usuario):
        return self._episodios


class MockRag:
    def __init__(self, texto=""):
        self._texto = texto

    def buscar(self, intencion, consulta, n_results=4, **kwargs):
        return self._texto

    def cargar_indice(self, intencion):
        return self._texto

    def buscar_relevante(self, consulta, n_results=4, umbral=0.75):
        return self._texto, Intencion.AUDACIA


class TestFaseGCierreTecnico(unittest.TestCase):
    def setUp(self):
        self.logger = logging.getLogger("test_fase_g")
        self.logger.setLevel(logging.DEBUG)

    # -------------------------------------------------------------------------
    # TEST 1: Ciclo de vida de streaming y prevención de doble adquisición
    # -------------------------------------------------------------------------
    def test_01_streaming_permiso_ciclo_vida_y_no_doble_adquisicion(self):
        """Demuestra que con max_concurrencia=1 no existe doble adquisición ni deadlock."""
        # Levantar servidor mock SSE
        class SseHandler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()
                for i in range(3):
                    time.sleep(0.05)
                    data = json.dumps({"choices": [{"delta": {"content": f"tok_{i} "}}]})
                    self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
                    self.wfile.flush()
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
                time.sleep(0.15)

            def log_message(self, *args):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), SseHandler)
        puerto = srv.server_port
        t_srv = threading.Thread(target=srv.serve_forever, daemon=True)
        t_srv.start()

        cfg = ModelConfig()
        os.environ["AUDACIA_LLM_URL"] = f"http://127.0.0.1:{puerto}/v1/chat/completions"

        try:
            # max_concurrencia=1 estricta: si hubiera doble adquisición entre
            # create_chat_completion(stream=True) y _stream_adapter, se congelaría en deadlock.
            servicio = LlmService(cfg, self.logger, max_concurrencia=1, max_cola=2, timeout_cola=2.0)

            # 1. Antes de iterar: el generador existe pero NO ha consumido el permiso
            gen = servicio.create_chat_completion(
                messages=[{"role": "user", "content": "hola"}],
                stream=True,
            )
            self.assertEqual(servicio._governor.activos, 0, "No debe consumir permiso antes de iniciar la iteración")

            # 2. Durante la iteración: el permiso se retiene
            chunk_1 = next(gen)
            self.assertIn("choices", chunk_1)
            self.assertEqual(servicio._governor.activos, 1, "Debe retener exactamente 1 permiso durante la iteración")

            # Demostrar que un segundo hilo compitiendo se encola (cola=1) y no puede entrar
            cola_bloqueada = []
            def intentar_segundo():
                try:
                    with servicio._governor.adquirir():
                        cola_bloqueada.append("entro")
                except Exception as e:
                    cola_bloqueada.append(type(e).__name__)

            t2 = threading.Thread(target=intentar_segundo, daemon=True)
            t2.start()
            time.sleep(0.05)
            self.assertEqual(servicio._governor.en_espera, 1, "La segunda solicitud debe estar en cola de espera")
            self.assertEqual(cola_bloqueada, [], "La segunda solicitud no debe haber entrado todavía")

            # 3. Consumir el resto del stream
            restantes = list(gen)
            self.assertEqual(len(restantes), 2)

            # Esperar a que el hilo 2 tome el permiso liberado y termine
            t2.join(timeout=2.0)
            self.assertEqual(cola_bloqueada, ["entro"], "El hilo en cola debió recibir el permiso liberado")
            self.assertEqual(servicio._governor.activos, 0, "Al finalizar todos los turnos, activos debe volver a 0")
            self.assertEqual(servicio._governor.en_espera, 0, "La cola debe estar en 0")

        finally:
            srv.shutdown()
            srv.server_close()

    # -------------------------------------------------------------------------
    # TEST 2: Liberación en desconexión temprana (GeneratorExit / close)
    # -------------------------------------------------------------------------
    def test_02_streaming_liberacion_en_generator_exit(self):
        """Demuestra que abandonar un generador a mitad de camino libera el permiso limpiamente."""
        class InfiniteSseHandler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                while True:
                    try:
                        self.wfile.write(b"data: {\"choices\": [{\"delta\": {\"content\": \"inf \"}}]}\n\n")
                        self.wfile.flush()
                        time.sleep(0.05)
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        break

            def log_message(self, *args):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), InfiniteSseHandler)
        puerto = srv.server_port
        t_srv = threading.Thread(target=srv.serve_forever, daemon=True)
        t_srv.start()

        cfg = ModelConfig()
        os.environ["AUDACIA_LLM_URL"] = f"http://127.0.0.1:{puerto}/v1/chat/completions"

        try:
            servicio = LlmService(cfg, self.logger, max_concurrencia=1, max_cola=2)
            gen = servicio.stream_chat([{"role": "user", "content": "hola"}])

            # Iniciar iteración y leer un único token
            primer_tok = next(gen)
            self.assertEqual(primer_tok, "inf ")
            self.assertEqual(servicio._governor.activos, 1, "Debe tener el permiso retenido")

            # El cliente web se desconecta (cierra el generador)
            gen.close()

            # Verificar que el permiso se liberó de inmediato
            self.assertEqual(servicio._governor.activos, 0, "GeneratorExit debió liberar el permiso del gobernador")
            metricas = servicio.metricas()
            self.assertEqual(metricas["solicitudes_canceladas"], 1, "Debe registrar la cancelación en telemetría")

        finally:
            srv.shutdown()
            srv.server_close()

    # -------------------------------------------------------------------------
    # TEST 3: Límite de inactividad vs Plazo Total Monotónico (Trickle Server)
    # -------------------------------------------------------------------------
    def test_03_plazo_total_monotonico_corta_generacion_goteada(self):
        """Demuestra que un stream que gotea tokens dentro del timeout de lectura pero supera el total es terminado."""
        class TrickleSseHandler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                # Envía un token cada 0.15s durante 10 segundos
                for i in range(50):
                    time.sleep(0.15)
                    try:
                        self.wfile.write(b"data: {\"choices\": [{\"delta\": {\"content\": \"tok \"}}]}\n\n")
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        break

            def log_message(self, *args):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), TrickleSseHandler)
        puerto = srv.server_port
        t_srv = threading.Thread(target=srv.serve_forever, daemon=True)
        t_srv.start()

        cfg = ModelConfig()
        os.environ["AUDACIA_LLM_URL"] = f"http://127.0.0.1:{puerto}/v1/chat/completions"
        os.environ["AUDACIA_LLM_READ_TIMEOUT"] = "2.0"   # Inactividad permisiva (2s)
        os.environ["AUDACIA_LLM_TOTAL_TIMEOUT"] = "0.5"  # Plazo total estricto (0.5s)

        try:
            servicio = LlmService(cfg, self.logger, max_concurrencia=1)
            # Verificar configuración efectiva
            self.assertEqual(servicio._timeout_inactividad_lectura, 2.0)
            self.assertEqual(servicio._timeout_total_generacion, 0.5)

            t0 = time.monotonic()
            tokens_obtenidos = []

            # stream_chat atrapa el TimeoutError de forma segura y corta el stream
            for tok in servicio.stream_chat([{"role": "user", "content": "dime algo largo"}]):
                tokens_obtenidos.append(tok)

            duracion = time.monotonic() - t0

            # Debe haber obtenido algunos tokens antes del corte
            self.assertGreater(len(tokens_obtenidos), 0, "Debió emitir tokens antes del timeout")
            # Pero debe haberse cortado cerca del plazo total (0.5s), no durar 10s
            self.assertLess(duracion, 1.2, f"La generación debió ser terminada por plazo total, tardó {duracion:.2f}s")
            self.assertEqual(servicio._governor.activos, 0, "El permiso debió ser liberado tras el timeout")
            metricas = servicio.metricas()
            self.assertEqual(metricas["solicitudes_timeout_lectura"], 1, "Debe registrar timeout de lectura/total")

        finally:
            srv.shutdown()
            srv.server_close()

    # -------------------------------------------------------------------------
    # TEST 4: Conexiones no establecidas y sockets que cesan transmisión
    # -------------------------------------------------------------------------
    def test_04_conexion_no_establecida_y_silencio_de_socket(self):
        """Verifica el fallo controlado ante puerto cerrado y socket que se congela."""
        cfg = ModelConfig()
        # Puerto local que nadie escucha
        os.environ["AUDACIA_LLM_URL"] = "http://127.0.0.1:59999/v1/chat/completions"
        os.environ["AUDACIA_LLM_CONNECT_TIMEOUT"] = "0.3"

        servicio = LlmService(cfg, self.logger, max_concurrencia=1)
        tokens = list(servicio.stream_chat([{"role": "user", "content": "hola"}]))
        self.assertEqual(tokens, [], "No debe emitir tokens si la conexión no se establece")
        self.assertEqual(servicio._governor.activos, 0, "El permiso debe ser 0 tras fallo de conexión")

        # Ahora probar un socket que acepta TCP pero nunca emite datos (silencio)
        class SilentHandler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                # Duerme más que el timeout de inactividad
                time.sleep(1.0)

            def log_message(self, *args):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), SilentHandler)
        puerto = srv.server_port
        t_srv = threading.Thread(target=srv.serve_forever, daemon=True)
        t_srv.start()

        os.environ["AUDACIA_LLM_URL"] = f"http://127.0.0.1:{puerto}/v1/chat/completions"
        os.environ["AUDACIA_LLM_READ_TIMEOUT"] = "0.3"
        os.environ["AUDACIA_LLM_TOTAL_TIMEOUT"] = "5.0"

        try:
            servicio2 = LlmService(cfg, self.logger, max_concurrencia=1)
            t0 = time.monotonic()
            tokens2 = list(servicio2.stream_chat([{"role": "user", "content": "hola"}]))
            dur = time.monotonic() - t0
            self.assertEqual(tokens2, [])
            self.assertLess(dur, 0.9, f"El socket inactivo debió expirar en ~0.3s, tardó {dur:.2f}s")
            self.assertEqual(servicio2._governor.activos, 0, "El permiso debe liberarse tras inactividad")
        finally:
            srv.shutdown()
            srv.server_close()

    # -------------------------------------------------------------------------
    # TEST 5: Clasificación de errores HTTP: 429 backpressure, 400 y 503
    # -------------------------------------------------------------------------
    def test_05_clasificacion_errores_http_429_400_503(self):
        """Demuestra que 429 se gestiona como backpressure, 400 no abre circuito y 503 sí abre circuito."""
        # 1. HTTP 429 (Too Many Requests / Server Slots Busy)
        class StatusHandler(http.server.BaseHTTPRequestHandler):
            status_code = 429
            protocol_version = "HTTP/1.1"

            def do_POST(self):
                content_len = int(self.headers.get("Content-Length", 0))
                if content_len > 0:
                    self.rfile.read(content_len)
                self.send_response(self.status_code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(b"{\"error\": {\"message\": \"slots busy\"}}")

            def log_message(self, *args):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), StatusHandler)
        puerto = srv.server_port
        t_srv = threading.Thread(target=srv.serve_forever, daemon=True)
        t_srv.start()

        cfg = ModelConfig()
        os.environ["AUDACIA_LLM_URL"] = f"http://127.0.0.1:{puerto}/v1/chat/completions"

        try:
            servicio = LlmService(cfg, self.logger, max_concurrencia=1)

            # En create_chat_completion: HTTP 429 debe lanzar LlmSaturadoError
            StatusHandler.status_code = 429
            with self.assertRaises(LlmSaturadoError):
                servicio.create_chat_completion(messages=[{"role": "user", "content": "test"}])

            # El circuito debe seguir CLOSED porque 429 es saturación/backpressure, no caída de motor
            self.assertEqual(servicio._circuit_breaker.estado, CircuitState.CLOSED)
            self.assertEqual(servicio._metricas.obtener_resumen(servicio._governor, servicio._circuit_breaker)["solicitudes_rechazadas_saturacion"], 1)

            # 2. HTTP 400 (Bad Request de cliente)
            StatusHandler.status_code = 400
            with self.assertRaises(urllib.error.HTTPError):
                servicio.create_chat_completion(messages=[{"role": "user", "content": "test"}])

            # El circuito sigue CLOSED porque errores 4xx son de cliente
            self.assertEqual(servicio._circuit_breaker.estado, CircuitState.CLOSED)

            # 3. HTTP 503 (Server Unavailable)
            StatusHandler.status_code = 503
            # Configurado con fallos_max=2
            servicio._circuit_breaker._fallos_max = 2

            with self.assertRaises(urllib.error.HTTPError):
                servicio.create_chat_completion(messages=[{"role": "user", "content": "test"}])
            self.assertEqual(servicio._circuit_breaker.estado, CircuitState.CLOSED)

            with self.assertRaises(urllib.error.HTTPError):
                servicio.create_chat_completion(messages=[{"role": "user", "content": "test"}])

            # Tras 2 fallos consecutivos 503, el circuito debe dispararse a OPEN
            self.assertEqual(servicio._circuit_breaker.estado, CircuitState.OPEN)

            # La siguiente petición debe fallar de inmediato (0ms) con LlmNoDisponibleError
            with self.assertRaises(LlmNoDisponibleError):
                servicio.create_chat_completion(messages=[{"role": "user", "content": "test"}])

        finally:
            srv.shutdown()
            srv.server_close()

    # -------------------------------------------------------------------------
    # TEST 6: Sondeo único en HALF_OPEN y recuperación limpia
    # -------------------------------------------------------------------------
    def test_06_circuit_breaker_sondeo_unico_en_half_open(self):
        """Demuestra que en HALF_OPEN solo 1 sondeo concurrente pasa y el éxito restablece a CLOSED."""
        cb = LlmCircuitBreaker(fallos_max=1, tiempo_enfriamiento_s=0.1)
        cb.registrar_fallo(Exception("caída"))
        self.assertEqual(cb.estado, CircuitState.OPEN)
        self.assertFalse(cb.puede_ejecutar())

        # Esperar enfriamiento para entrar en HALF_OPEN
        time.sleep(0.15)
        self.assertEqual(cb.estado, CircuitState.HALF_OPEN)

        # Primer hilo obtiene permiso para el sondeo
        permitido_1 = cb.puede_ejecutar()
        self.assertTrue(permitido_1, "El primer sondeo en HALF_OPEN debe ser autorizado")

        # Segundo hilo compitiendo en paralelo: debe ser RECHAZADO (solo 1 sondeo en vuelo)
        permitido_2 = cb.puede_ejecutar()
        self.assertFalse(permitido_2, "El segundo sondeo concurrente debe ser rechazado en HALF_OPEN")

        # El sondeo exitoso restablece el circuito a CLOSED
        cb.registrar_exito()
        self.assertEqual(cb.estado, CircuitState.CLOSED)
        self.assertTrue(cb.puede_ejecutar(), "Restablecido a CLOSED, el tráfico debe reanudarse normalmente")

    # -------------------------------------------------------------------------
    # TEST 7: Presupuesto de contexto en tokens y poda sin turnos huérfanos
    # -------------------------------------------------------------------------
    def test_07_presupuesto_contexto_tokens_y_poda_coherente(self):
        """Verifica que el presupuesto acote tokens reales y no deje turnos huérfanos."""
        # 1. Probar el estimador de tokens con texto denso en caracteres/símbolos
        texto_denso = "1234567890!@#$%^&*()_+{}[]:;\"'<>,.?/~`"
        tokens_denso = estimar_tokens_texto(texto_denso)
        self.assertGreaterEqual(tokens_denso, 20, "Los símbolos deben computar cerca de 1 token por símbolo")

        texto_cjk = "日本語のテスト文です。これはトークン数の確認です。"
        tokens_cjk = estimar_tokens_texto(texto_cjk)
        self.assertGreaterEqual(tokens_cjk, 25, "El texto CJK debe computar múltiples tokens por carácter")

        # 2. ContextBuilder con historial extenso
        db = MockDb(historial=[
            {"role": "user", "content": "Pregunta 1: cuéntame sobre el tanque"},
            {"role": "assistant", "content": "Respuesta 1: El tanque es un proyecto de robótica submarina."},
            {"role": "user", "content": "Pregunta 2: qué sensores usa"},
            {"role": "assistant", "content": "Respuesta 2: Usa sensores de presión y cámaras estéreo."},
            {"role": "user", "content": "Pregunta 3: cuál es su autonomía"},
            {"role": "assistant", "content": "Respuesta 3: Tiene 4 horas de autonomía continua."},
        ])
        rag = MockRag(texto="Documentación de AudacIA sobre proyectos.")
        builder = ContextBuilder(
            db=db,
            rag=rag,
            rag_config=RagConfig(),
            memory_config=MemoryConfig(),
            n_ctx=8192,
            margen_salida_tokens=1500,
        )
        self.assertEqual(builder._max_prompt_tokens, 6692)

        # Probar con un presupuesto de tokens deliberadamente pequeño para forzar poda
        builder._max_prompt_tokens = 350  # Muy ajustado

        msgs = builder.build_messages(
            mensaje_usuario="¿Y el peso?",
            intencion=Intencion.AUDACIA,
            usuario_activo="visitante_test",
            estado=EstadoSesion(),
        )

        # Debe conservar system prompt y user prompt final
        self.assertEqual(msgs[0]["role"], "system")
        self.assertEqual(msgs[-1]["role"], "user")
        self.assertIn("¿Y el peso?", msgs[-1]["content"])

        # Verificar que el historial restante no empiece con un assistant descolgado
        historial_intermedio = msgs[1:-1]
        if historial_intermedio:
            self.assertEqual(
                historial_intermedio[0]["role"],
                "user",
                "El historial recortado NUNCA debe comenzar con un mensaje huérfano de assistant"
            )

        # 3. Caso extremo: RAG masivo que supera el presupuesto por sí solo
        # 800 repeticiones son ~22,400 caracteres (~9,000 tokens solo de RAG)
        rag_gigante = MockRag(texto="Información masiva repetida " * 800)
        builder_gigante = ContextBuilder(
            db=MockDb(),
            rag=rag_gigante,
            rag_config=RagConfig(),
            memory_config=MemoryConfig(),
            n_ctx=8192,
            margen_salida_tokens=1500,
        )
        # El system prompt base ocupa ~4,500 tokens. Con 5,000 de tope, el RAG masivo (9,000 tokens)
        # debe ser podado automáticamente a ~400 tokens sin tocar el system prompt ni la pregunta.
        builder_gigante._max_prompt_tokens = 5000

        msgs_gigante = builder_gigante.build_messages(
            mensaje_usuario="Resumen",
            intencion=Intencion.AUDACIA,
            usuario_activo="visitante_test",
            estado=EstadoSesion(),
        )

        # Debe podar el contexto RAG de las notas sin eliminar las instrucciones del sistema ni la pregunta
        self.assertEqual(msgs_gigante[0]["role"], "system")
        self.assertIn("Comentario del visitante: Resumen", msgs_gigante[-1]["content"])
        self.assertIn("[...fragmentos adicionales omitidos por presupuesto de contexto]", msgs_gigante[-1]["content"])
        tokens_finales = estimar_tokens_prompt(msgs_gigante)
        self.assertLessEqual(tokens_finales, 5050, f"El prompt total no debe desbordar el presupuesto ({tokens_finales} tokens)")

    # -------------------------------------------------------------------------
    # TEST 8: Telemetría de memoria comprometida autoritativa en Windows
    # -------------------------------------------------------------------------
    def test_08_telemetria_get_performance_info_autoritativa(self):
        """Demuestra que obtener_telemetria_sistema() calcula CommitTotal/Limit con GetPerformanceInfo."""
        tele = obtener_telemetria_sistema()
        self.assertIn("ram_total_mib", tele)
        self.assertIn("ram_usada_mib", tele)
        self.assertIn("ram_libre_mib", tele)
        self.assertIn("memoria_comprometida_mib", tele)
        self.assertIn("limite_compromiso_mib", tele)
        self.assertIn("pico_compromiso_mib", tele)
        self.assertIn("ratio_compromiso_pct", tele)

        # Verificar consistencia matemática física
        self.assertGreater(tele["ram_total_mib"], 0.0)
        self.assertGreater(tele["ram_usada_mib"], 0.0)
        self.assertGreater(tele["memoria_comprometida_mib"], 0.0)
        self.assertGreater(tele["limite_compromiso_mib"], tele["memoria_comprometida_mib"])
        self.assertLessEqual(tele["ratio_compromiso_pct"], 100.0)
        self.assertGreaterEqual(tele["ratio_compromiso_pct"], 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
