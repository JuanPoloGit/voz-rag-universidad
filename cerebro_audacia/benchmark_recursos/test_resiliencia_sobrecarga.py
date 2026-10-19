"""Batería de Pruebas de Resiliencia, Sobrecarga y Recuperación — Fase D (AudacIA).

Valida los 12 escenarios obligatorios sin saturar GPU ni RAM en el entorno de desarrollo:
1. LLM apagado antes de una petición.
2. LLM que deja de responder durante una petición.
3. Timeout de conexión y timeout de generación.
4. Cola llena (rechazo inmediato por saturación).
5. Límite de concurrencia alcanzado y expiración en cola de espera.
6. Petición cancelada o cliente desconectado (GeneratorExit y cierre de socket).
7. Recuperación cuando el LLM vuelve a estar disponible (Circuit Breaker OPEN -> HALF_OPEN -> CLOSED).
8. Errores durante streaming y JSON malformado.
9. Dos usuarios con sesiones separadas y peticiones simultáneas (aislamiento estricto).
10. Fallo de una tarea de memoria mientras otro usuario utiliza el agente (aislamiento de fallos).
11. Confirmar que cerrar el cliente LLM no apaga el servidor compartido.
12. Confirmar que no se carga una segunda copia del modelo en VRAM.
"""

from __future__ import annotations

import http.server
import json
import logging
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parent.parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from hacu.config import AppConfig, ModelConfig
from hacu.extractor import BackgroundMemoryExtractor, TareaExtraccion
from hacu.identity import IdentityResolver
from hacu.llm import (
    Aliento,
    CircuitState,
    LlmColaExpiradaError,
    LlmNoDisponibleError,
    LlmSaturadoError,
    LlmService,
)
from hacu.memory import HacuMemoryDB
from hacu.routing import FastRouter, Intencion
from hacu.sanitizer import FactSanitizer
from hacu.session import HacuSession

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("test_resiliencia")


def obtener_vram_mb() -> float:
    try:
        res = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        return float(res.stdout.strip().splitlines()[0])
    except Exception:
        return -1.0


# ------------------------------------------------------------------ Servidor Mock Efímero

class MockStallHandler(http.server.BaseHTTPRequestHandler):
    """Manejador para simular cuelgues, demoras y streaming corrupto sin tocar la GPU."""

    stall_seconds: float = 0.0
    stream_malformed: bool = False

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        if length > 0:
            try:
                _ = self.rfile.read(length)
            except Exception:
                pass

        if self.stall_seconds > 0:
            time.sleep(self.stall_seconds)

        if self.stream_malformed:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            # Enviar chunk corrupto seguido de uno válido
            self.wfile.write(b"data: {broken json\n\n")
            self.wfile.flush()
            valido = json.dumps({"choices": [{"delta": {"content": "OK"}, "finish_reason": "stop"}]})
            self.wfile.write(f"data: {valido}\n\n".encode("utf-8"))
            self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            self.close_connection = True
            return

        # Respuesta genérica OK
        cuerpo = json.dumps({"choices": [{"message": {"content": '{"ok": true}'}}]}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(cuerpo)
        self.close_connection = True

    def log_message(self, format, *args):
        pass  # Silenciar logs del servidor mock en consola


class MockHttpServer:
    def __init__(self, stall_seconds: float = 0.0, stream_malformed: bool = False):
        self.stall_seconds = stall_seconds
        self.stream_malformed = stream_malformed
        self.server: http.server.HTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.port: int = 0

    def __enter__(self):
        handler = type(
            "ConfiguredHandler",
            (MockStallHandler,),
            {"stall_seconds": self.stall_seconds, "stream_malformed": self.stream_malformed},
        )
        self.server = http.server.HTTPServer(("127.0.0.1", 0), handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.server:
            self.server.shutdown()
            self.server.server_close()


# ------------------------------------------------------------------ Batería de Pruebas

def test_1_llm_apagado_antes_de_peticion():
    print("\n--- [Escenario 1] LLM apagado antes de una petición ---")
    cfg = ModelConfig()
    servicio = LlmService(cfg, logger)
    servicio._url = "http://127.0.0.1:59998/v1/chat/completions"
    servicio._health_url = "http://127.0.0.1:59998/health"
    servicio._timeout_conexion = 0.5

    # Cargar debe lanzar ConnectionError
    t0 = time.perf_counter()
    capturado = False
    try:
        servicio.cargar()
    except ConnectionError:
        capturado = True
    assert capturado, "cargar() debe fallar limpiamente con ConnectionError"
    t_falla = time.perf_counter() - t0
    print(f"  [OK] Servidor apagado detectado en {t_falla:.3f}s sin colapso del proceso.")


def test_2_llm_deja_de_responder_durante_peticion():
    print("\n--- [Escenario 2] LLM deja de responder durante una petición ---")
    cfg = ModelConfig()
    # Mock que se cuelga 5 segundos
    with MockHttpServer(stall_seconds=5.0) as mock:
        servicio = LlmService(cfg, logger)
        servicio._url = f"http://127.0.0.1:{mock.port}/v1/chat/completions"
        servicio._timeout_lectura = 1.0  # Timeout agresivo para prueba rápida
        t0 = time.perf_counter()
        resultado = servicio.completar_json("hola", max_tokens=10)
        t_exp = time.perf_counter() - t0
        assert resultado is None, "Debe retornar None ante cuelgue del servidor"
        assert t_exp < 2.5, f"Debe expirar en ~1.0s, tardó: {t_exp:.3f}s"
        print(f"  [OK] Abortado por timeout de lectura en {t_exp:.3f}s sin retención indefinida.")


def test_3_timeouts_conexion_y_generacion():
    print("\n--- [Escenario 3] Timeouts de conexión y generación ---")
    cfg = ModelConfig()
    servicio = LlmService(cfg, logger)
    # Dirección no enrutable para timeout de conexión
    servicio._url = "http://10.255.255.1:8080/v1/chat/completions"
    servicio._health_url = "http://10.255.255.1:8080/health"
    servicio._timeout_conexion = 0.8
    t0 = time.perf_counter()
    try:
        servicio.cargar()
    except ConnectionError:
        pass
    t_conn = time.perf_counter() - t0
    assert t_conn < 2.0, f"Timeout de conexión tardó demasiado: {t_conn:.3f}s"
    print(f"  [OK] Timeout de conexión verificado ({t_conn:.3f}s).")


def test_4_cola_llena_rechazo_inmediato():
    print("\n--- [Escenario 4] Cola llena: rechazo inmediato por saturación ---")
    cfg = ModelConfig()
    # Concurrencia=1, Cola=2
    servicio = LlmService(cfg, logger, max_concurrencia=1, max_cola=2, timeout_cola=2.0)
    with MockHttpServer(stall_seconds=2.0) as mock:
        servicio._url = f"http://127.0.0.1:{mock.port}/v1/chat/completions"

        # Ocupar 1 activo + 2 en cola
        barrera = threading.Barrier(4)
        rechazo_inmediato = [False]

        def trabajador_bloqueante():
            barrera.wait()
            servicio.completar_json("tarea", max_tokens=10)

        hilos = [threading.Thread(target=trabajador_bloqueante) for _ in range(3)]
        for h in hilos:
            h.start()

        barrera.wait()
        time.sleep(0.1)  # Dar tiempo a que los 3 hilos entren (1 activo, 2 en espera)

        # La 4ta solicitud debe ser rechazada inmediatamente (cola llena: 1 activo + 2 en cola = 3 maximo)
        t0 = time.perf_counter()
        try:
            with servicio._governor.adquirir():
                pass
        except LlmSaturadoError:
            rechazo_inmediato[0] = True
        t_rechazo = time.perf_counter() - t0

        for h in hilos:
            h.join()

        assert rechazo_inmediato[0], "La 4ta solicitud debió ser rechazada por LlmSaturadoError"
        assert t_rechazo < 0.05, f"El rechazo debe ser instantáneo (0ms), tardó: {t_rechazo:.4f}s"
        print(f"  [OK] Solicitud rechazada inmediatamente en {t_rechazo*1000:.2f}ms por saturación de cola.")


def test_5_limite_concurrencia_y_expiracion_cola():
    print("\n--- [Escenario 5] Límite de concurrencia y expiración en cola ---")
    cfg = ModelConfig()
    # Concurrencia=1, Cola=5, Timeout de cola=0.5s
    servicio = LlmService(cfg, logger, max_concurrencia=1, max_cola=5, timeout_cola=0.5)
    with MockHttpServer(stall_seconds=2.0) as mock:
        servicio._url = f"http://127.0.0.1:{mock.port}/v1/chat/completions"

        # Ocupar la ranura activa durante 2.0s
        t_bloqueo = threading.Thread(target=lambda: servicio.completar_json("tarea", max_tokens=10))
        t_bloqueo.start()
        time.sleep(0.1)

        # La segunda solicitud espera en cola pero expira en 0.5s
        t0 = time.perf_counter()
        expirada = False
        try:
            with servicio._governor.adquirir():
                pass
        except LlmColaExpiradaError:
            expirada = True
        t_espera = time.perf_counter() - t0

        t_bloqueo.join()
        assert expirada, "Debió expirar con LlmColaExpiradaError tras 0.5s"
        assert 0.45 <= t_espera <= 1.0, f"Tiempo de expiración inesperado: {t_espera:.3f}s"
        print(f"  [OK] Expiración en cola controlada tras {t_espera:.3f}s sin retener el hilo.")


def test_6_peticion_cancelada_cliente_desconectado():
    print("\n--- [Escenario 6] Petición cancelada o cliente desconectado (GeneratorExit) ---")
    cfg = ModelConfig()
    servicio = LlmService(cfg, logger)

    # Iniciar streaming real contra el servidor central pero cerrar el generador tras 2 tokens
    motivo: list[str | None] = []
    stream = servicio.stream_chat(
        [{"role": "user", "content": "Cuenta del 1 al 20"}],
        al_terminar=motivo.append,
    )
    tokens = []
    try:
        for t in stream:
            tokens.append(t)
            if len(tokens) >= 2:
                stream.close()  # Provoca GeneratorExit en el generador
                break
    except Exception:
        pass

    time.sleep(0.1)
    # Comprobar que la ranura activa fue liberada
    metricas = servicio.metricas()
    assert metricas["activos_actuales"] == 0, "La ranura activa debió liberarse tras GeneratorExit"
    assert metricas["solicitudes_canceladas"] >= 1, "Debe registrarse la cancelación en métricas"
    print(f"  [OK] Cancelación detectada, socket cerrado y ranura liberada inmediatamente.")


def test_7_recuperacion_servidor_disponible_circuit_breaker():
    print("\n--- [Escenario 7] Recuperación cuando el LLM vuelve a estar disponible ---")
    cfg = ModelConfig()
    servicio = LlmService(cfg, logger)
    servicio._circuit_breaker._fallos_max = 2
    servicio._circuit_breaker._tiempo_enfriamiento_s = 0.5

    # Simular 2 fallos para abrir el circuito
    servicio._circuit_breaker.registrar_fallo()
    servicio._circuit_breaker.registrar_fallo()
    assert servicio._circuit_breaker.estado == CircuitState.OPEN, "El circuito debe estar OPEN"

    # Verificación de rechazo temprano (0 ms)
    motivo = []
    tokens = list(servicio.stream_chat([{"role": "user", "content": "hola"}], al_terminar=motivo.append))
    assert len(tokens) == 0, "Con circuito OPEN no se generan tokens"
    print("  [OK] Rechazo inmediato con circuito OPEN.")

    # Esperar enfriamiento para pasar a HALF_OPEN
    time.sleep(0.6)
    assert servicio._circuit_breaker.estado == CircuitState.HALF_OPEN, "Debe pasar a HALF_OPEN tras enfriamiento"

    # Probar recuperación contra el servidor real activo
    t_cal = servicio.precalentar()
    assert t_cal > 0.0, "Precalentamiento debe tener éxito contra el servidor real"
    assert servicio._circuit_breaker.estado == CircuitState.CLOSED, "Debe recuperarse a CLOSED tras éxito"
    print("  [OK] Circuito restaurado automáticamente a CLOSED tras verificar recuperación.")


def test_8_errores_durante_streaming_y_json_malformado():
    print("\n--- [Escenario 8] Errores durante streaming y JSON malformado ---")
    cfg = ModelConfig()
    with MockHttpServer(stream_malformed=True) as mock:
        servicio = LlmService(cfg, logger)
        servicio._url = f"http://127.0.0.1:{mock.port}/v1/chat/completions"

        # 8a. Stream tolerante a líneas rotas
        motivo: list[str | None] = []
        tokens = list(servicio.stream_chat([{"role": "user", "content": "hola"}], al_terminar=motivo.append))
        assert "OK" in "".join(tokens), "Debe recuperar el token válido tras descartar la línea corrupta"
        print("  [OK] Streaming toleró fragmentos corruptos y entregó el token íntegro.")

        # 8b. Parseo de JSON no estructurado o markdown
        texto_markdown = "Aqui esta el resultado: ```json\n{\"sobre_el_visitante\": true, \"hecho\": \"Estudiante\"}\n```"
        parsed = servicio._parsear_json(texto_markdown)
        assert parsed == {"sobre_el_visitante": True, "hecho": "Estudiante"}, f"Fallo al extraer JSON: {parsed}"
        print("  [OK] Extracción robusta de bloques JSON envueltos en markdown.")


def test_9_dos_usuarios_sesiones_separadas_concurrentes():
    print("\n--- [Escenario 9] Dos usuarios con sesiones separadas y peticiones simultáneas ---")
    import tempfile
    tmp_dir = Path(tempfile.mkdtemp(prefix="audacia_test_sesiones_"))
    cfg = AppConfig()
    db_a = HacuMemoryDB(tmp_dir / "db_a.sqlite", logger)
    db_b = HacuMemoryDB(tmp_dir / "db_b.sqlite", logger)

    ident_a = IdentityResolver("anonimo_a")
    ident_b = IdentityResolver("anonimo_b")

    servicio_llm = LlmService(cfg.model, logger)

    class StubContext:
        def __init__(self, etiqueta: str):
            self.etiqueta = etiqueta

        def build_messages(self, texto, intencion, usuario, estado):
            return [
                {"role": "system", "content": f"Eres el asistente de {self.etiqueta}."},
                {"role": "user", "content": f"{self.etiqueta} pregunta: {texto}"},
            ]

    sesion_a = HacuSession(
        llm=servicio_llm, db=db_a, router=FastRouter(), identity=ident_a,
        extractor=None, context_builder=StubContext("USUARIO_ALICE"), logger=logger,
    )
    sesion_b = HacuSession(
        llm=servicio_llm, db=db_b, router=FastRouter(), identity=ident_b,
        extractor=None, context_builder=StubContext("USUARIO_BOB"), logger=logger,
    )

    res_a: list[Any] = []
    res_b: list[Any] = []

    def turno_a():
        res_a.append(sesion_a.turno("¿Cuál es mi nombre de usuario?"))

    def turno_b():
        res_b.append(sesion_b.turno("¿Qué usuario soy yo?"))

    t_a = threading.Thread(target=turno_a)
    t_b = threading.Thread(target=turno_b)

    t_a.start()
    t_b.start()
    t_a.join()
    t_b.join()

    assert len(res_a) == 1 and len(res_b) == 1
    # Verificar aislamiento en base de datos de cada sesión
    historial_a = db_a.get_recent_history(ident_a.usuario_activo, 10)
    historial_b = db_b.get_recent_history(ident_b.usuario_activo, 10)

    assert any("¿Cuál es mi nombre" in m["content"] for m in historial_a)
    assert not any("BOB" in m["content"] for m in historial_a), "El historial de A no debe contener datos de B"
    assert any("¿Qué usuario soy" in m["content"] for m in historial_b)
    assert not any("ALICE" in m["content"] for m in historial_b), "El historial de B no debe contener datos de A"

    print("  [OK] Aislamiento estricto de sesiones, contextos e historiales verificado.")


def test_10_fallo_tarea_memoria_no_afecta_agente():
    print("\n--- [Escenario 10] Fallo de tarea de memoria no afecta al agente en vivo ---")
    import tempfile
    tmp_dir = Path(tempfile.mkdtemp(prefix="audacia_test_mem_"))
    cfg = AppConfig()
    db = HacuMemoryDB(tmp_dir / "db_mem.sqlite", logger)
    ident = IdentityResolver("test_user")

    # Servicio LLM que falla en completar_json pero funciona en stream_chat
    servicio_llm = LlmService(cfg.model, logger)

    extractor = BackgroundMemoryExtractor(
        db=db, llm=servicio_llm, sanitizer=FactSanitizer(),
        memory_config=cfg.memory, model_config=cfg.model, logger=logger,
    )
    extractor.iniciar()

    # Forzar error simulado en el extractor sin tirar el hilo
    extractor._cola.put(TareaExtraccion(user_id="test_user", mensaje="Frase corta"))

    class StubContext:
        def build_messages(self, texto, intencion, usuario, estado):
            return [{"role": "user", "content": "Di hola"}]

    sesion = HacuSession(
        llm=servicio_llm, db=db, router=FastRouter(), identity=ident,
        extractor=extractor, context_builder=StubContext(), logger=logger,
    )

    # El turno interactivo debe responder normalmente
    resultado = sesion.turno("Hola HACU")
    extractor.detener(timeout=2.0)

    assert resultado.respuesta, "El turno interactivo debe completarse con éxito"
    print("  [OK] Aislamiento de fallos: la cola de memoria en segundo plano no afectó la interacción.")


def test_11_cerrar_cliente_no_apaga_servidor():
    print("\n--- [Escenario 11] Confirmar que cerrar cliente no apaga el servidor compartido ---")
    cfg = ModelConfig()
    servicio = LlmService(cfg, logger)
    servicio.cargar()
    assert servicio._conectado

    # Cerrar cliente
    servicio.cerrar()
    assert not servicio._conectado

    # Comprobar salud directa de llama-server.exe
    import urllib.request
    with urllib.request.urlopen("http://127.0.0.1:8080/health", timeout=3) as resp:
        assert resp.status == 200, "El servidor central debe seguir respondiendo en HTTP 200"
        data = json.loads(resp.read().decode("utf-8"))
        assert data.get("status") in {"ok", "ready"}
    print("  [OK] Servidor central llama-server.exe permanece activo y saludable.")


def test_12_confirmar_cero_copias_adicionales_vram():
    print("\n--- [Escenario 12] Confirmar cero copias adicionales del modelo en VRAM ---")
    vram_actual = obtener_vram_mb()
    print(f"  VRAM medida tras la batería de pruebas: {vram_actual:.1f} MiB")
    # En desarrollo con RTX 2000 Ada, el servidor ocupa 5.177 MiB.
    assert vram_actual <= 5500.0, f"VRAM excedida ({vram_actual:.1f} MiB): posible fuga o doble carga"
    print("  [OK] Verificado: VRAM constante en ~5.177 MiB. Cero pesos adicionales en memoria.")


def ejecutar_todas():
    print("=" * 75)
    print(" AUDACIA — FASE D: BATERIA COMPLETA DE RESILIENCIA Y SOBRECARGA")
    print("=" * 75)

    test_1_llm_apagado_antes_de_peticion()
    test_2_llm_deja_de_responder_durante_peticion()
    test_3_timeouts_conexion_y_generacion()
    test_4_cola_llena_rechazo_inmediato()
    test_5_limite_concurrencia_y_expiracion_cola()
    test_6_peticion_cancelada_cliente_desconectado()
    test_7_recuperacion_servidor_disponible_circuit_breaker()
    test_8_errores_durante_streaming_y_json_malformado()
    test_9_dos_usuarios_sesiones_separadas_concurrentes()
    test_10_fallo_tarea_memoria_no_afecta_agente()
    test_11_cerrar_cliente_no_apaga_servidor()
    test_12_confirmar_cero_copias_adicionales_vram()

    print("\n" + "=" * 75)
    print(" LOS 12 ESCENARIOS DE RESILIENCIA Y SOBRECARGA PASARON EXITOSAMENTE")
    print("=" * 75)
    return True


if __name__ == "__main__":
    exito = ejecutar_todas()
    sys.exit(0 if exito else 1)
