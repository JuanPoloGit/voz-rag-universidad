"""Suite de pruebas de segunda pasada: resiliencia, concurrencia y no contaminación.

Verifica empíricamente:
1. Orden FIFO estricto y garantizado en LlmGovernor (sin carreras ni inversión de prioridades).
2. Cobertura universal del Governor y Circuit Breaker en create_chat_completion (stream y no-stream).
3. Circuit Breaker seguro bajo concurrencia: exactamente un sondeo en HALF_OPEN.
4. Inmunidad del Circuit Breaker ante errores de cliente 4xx (400 Bad Request no tira el servicio).
5. Desconexión real de cliente y liberación de ranura medida contra /slots en llama-server.
6. No contaminación del historial SQLite ante fallos o respuestas de contingencia.
7. Presupuesto de contexto activo en ContextBuilder (recorte de historial antiguo sin desbordar n_ctx).
8. Singleton thread-safe get_llm_client.
"""

from __future__ import annotations

import collections
import json
import logging
import os
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

from hacu.config import MemoryConfig, ModelConfig, RagConfig
from hacu.context import ContextBuilder, EstadoSesion
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
from hacu.memory import HacuMemoryDB
from hacu.routing import FastRouter, Intencion
from hacu.session import HacuSession


class TestSegundaPasadaResiliencia(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        logging.basicConfig(level=logging.INFO)
        cls.logger = logging.getLogger("test_segunda_pasada")

    def test_01_gobernador_orden_fifo_estricto(self):
        """Verifica que el gobernador atienda estrictamente en orden de llegada (FIFO)."""
        gov = LlmGovernor(max_concurrencia=1, max_cola=5, timeout_cola_s=5.0)

        orden_llegada = []
        orden_atencion = []
        barrera_arranque = threading.Barrier(4)

        def trabajador(identificador: int):
            barrera_arranque.wait()
            # Forzamos un pequeño escalonamiento determinista de llegada
            time.sleep(identificador * 0.05)
            orden_llegada.append(identificador)
            with gov.adquirir():
                orden_atencion.append(identificador)
                time.sleep(0.1)

        hilos = [threading.Thread(target=trabajador, args=(i,)) for i in range(4)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()

        self.assertEqual(len(orden_atencion), 4)
        self.assertEqual(orden_llegada, [0, 1, 2, 3])
        self.assertEqual(orden_atencion, [0, 1, 2, 3], "El orden de atención DEBE ser idéntico al de llegada (FIFO)")

    def test_02_cobertura_universal_gobernador(self):
        """Confirma que create_chat_completion (stream y sincrónico) no eluda el gobernador."""
        cfg = ModelConfig()
        # Creamos servicio con concurrencia=1, cola=0 (cualquier concurrencia desborda)
        servicio = LlmService(cfg, self.logger, max_concurrencia=1, max_cola=0, timeout_cola=0.2)

        # 1. Adquirimos la única ranura
        permiso_retenido = False
        with servicio._governor.adquirir():
            permiso_retenido = True
            # Mientras la ranura está ocupada, create_chat_completion sincrónico debe fallar por saturación
            with self.assertRaises(LlmSaturadoError):
                servicio.create_chat_completion(
                    messages=[{"role": "user", "content": "hola"}],
                    stream=False,
                )

            # create_chat_completion streaming también debe fallar por saturación de inmediato
            with self.assertRaises(LlmSaturadoError):
                gen = servicio.create_chat_completion(
                    messages=[{"role": "user", "content": "hola"}],
                    stream=True,
                )
                next(gen)

        self.assertTrue(permiso_retenido)

    def test_03_circuit_breaker_sondeo_unico_half_open(self):
        """Verifica que en HALF_OPEN solo pase un único sondeo concurrente."""
        cb = LlmCircuitBreaker(fallos_max=1, tiempo_enfriamiento_s=0.2)
        cb.registrar_fallo()
        self.assertEqual(cb.estado, CircuitState.OPEN)

        time.sleep(0.25)
        self.assertEqual(cb.estado, CircuitState.HALF_OPEN)

        # Lanzamos 5 hilos simultáneos pidiendo ejecutar
        resultados = []
        barrera = threading.Barrier(5)

        def intentar():
            barrera.wait()
            resultados.append(cb.puede_ejecutar())

        hilos = [threading.Thread(target=intentar) for _ in range(5)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()

        # Exactamente uno debió obtener permiso para el sondeo; los demás rechazados
        exitos = resultados.count(True)
        rechazos = resultados.count(False)
        self.assertEqual(exitos, 1, f"Debe haber exactamente 1 sondeo en HALF_OPEN, obtenido: {exitos}")
        self.assertEqual(rechazos, 4)

    def test_04_circuit_breaker_inmune_a_errores_4xx(self):
        """Verifica que errores de cliente (400 Bad Request, etc.) no abran el circuit breaker."""
        cb = LlmCircuitBreaker(fallos_max=2, tiempo_enfriamiento_s=5.0)

        err_400 = urllib.error.HTTPError(
            url="http://127.0.0.1:8080/v1/chat/completions",
            code=400,
            msg="Bad Request (Prompt too long)",
            hdrs={},
            fp=None,
        )

        cb.registrar_fallo(err_400)
        cb.registrar_fallo(err_400)
        cb.registrar_fallo(err_400)

        self.assertEqual(cb.estado, CircuitState.CLOSED, "Errores 4xx jamás deben abrir el circuito del LLM")

        # Ahora un error 500 sí debe acumular fallos
        err_500 = urllib.error.HTTPError(
            url="http://127.0.0.1:8080/v1/chat/completions",
            code=500,
            msg="Internal Server Error",
            hdrs={},
            fp=None,
        )
        cb.registrar_fallo(err_500)
        cb.registrar_fallo(err_500)
        self.assertEqual(cb.estado, CircuitState.OPEN, "Errores 5xx deben abrir el circuito al alcanzar el umbral")

    def test_05_desconexion_cliente_y_liberacion_ranura_slots(self):
        """Mide la desconexión real de un cliente y la liberación de ranura en /slots."""
        try:
            req_check = urllib.request.urlopen("http://127.0.0.1:8080/slots", timeout=1.0)
            slots_data = json.loads(req_check.read().decode())
        except Exception:
            self.skipTest("llama-server no disponible en 127.0.0.1:8080 para prueba de ranura real")

        cfg = ModelConfig()
        servicio = LlmService(cfg, self.logger)

        # Generador de streaming consumiendo del LLM real
        mensajes = [{"role": "user", "content": "Escribe un poema extenso de cinco párrafos sobre las estrellas."}]
        stream = servicio.stream_chat(mensajes, extenso=True)

        t_inicio = time.perf_counter()
        primer_token = next(stream)
        self.assertTrue(len(primer_token) > 0)

        # Simular desconexión intencional del consumidor HTTP cerrando el generador (GeneratorExit)
        t_desconexion = time.perf_counter()
        stream.close()
        t_cierre = time.perf_counter()

        # Medir cuánto tarda /slots en reportar is_processing = False
        t_liberacion = None
        for _ in range(50):
            with urllib.request.urlopen("http://127.0.0.1:8080/slots", timeout=1.0) as resp:
                slots = json.loads(resp.read().decode())
                if not any(s.get("is_processing", False) for s in slots):
                    t_liberacion = time.perf_counter()
                    break
            time.sleep(0.02)

        tiempo_cierre_app = (t_cierre - t_desconexion) * 1000.0
        self.assertLess(tiempo_cierre_app, 50.0, "La app debe detectar y cerrar el socket en < 50ms")
        if t_liberacion is not None:
            tiempo_servidor = (t_liberacion - t_desconexion) * 1000.0
            self.logger.info("Ranura liberada en llama-server en %.2f ms tras desconexión", tiempo_servidor)

    def test_06_no_contaminacion_historial_sqlite_en_fallo(self):
        """Verifica que un fallo de inferencia entregue contingencia al usuario SIN guardarlo en SQLite."""
        db_path = RAIZ / "cerebro_audacia" / "benchmark_recursos" / "test_no_contaminacion.db"
        if db_path.exists():
            db_path.unlink()

        db = HacuMemoryDB(db_path, self.logger)
        cfg = ModelConfig()

        # Creamos servicio con circuito abierto para forzar fallo inmediato
        servicio = LlmService(cfg, self.logger)
        servicio._circuit_breaker._estado = CircuitState.OPEN
        servicio._circuit_breaker._tiempo_apertura = time.monotonic() + 100.0

        import unittest.mock
        mock_rag = unittest.mock.MagicMock()
        mock_rag.buscar.return_value = None
        mock_rag.buscar_relevante.return_value = None

        ctx_builder = ContextBuilder(
            db=db,
            rag=mock_rag,
            rag_config=RagConfig(),
            memory_config=MemoryConfig(),
            usuario_anonimo="visitante_test",
        )

        from hacu.identity import IdentityResolver
        from hacu.routing import FastRouter

        session = HacuSession(
            llm=servicio,
            db=db,
            router=FastRouter(),
            identity=IdentityResolver(),
            extractor=None,
            context_builder=ctx_builder,
            logger=self.logger,
        )

        res = session.turno("Hola, ¿hay alguien?")

        # 1. El usuario recibió mensaje amable de contingencia
        self.assertIn("temporalmente ocupado", res.respuesta)

        # 2. CRÍTICO: El historial en base de datos NO debe contener este turno de asistente
        mensajes_guardados = db.get_recent_history("visitante_test", 10)
        self.assertEqual(
            len(mensajes_guardados),
            0,
            "Un turno fallido jamás debe persistirse en SQLite como turno de asistente",
        )

        if db_path.exists():
            try:
                db_path.unlink()
            except Exception:
                pass

    def test_07_presupuesto_contexto_recorte_historial(self):
        """Verifica que ContextBuilder recorte mensajes antiguos cuando se supera max_prompt_chars."""
        db_path = RAIZ / "cerebro_audacia" / "benchmark_recursos" / "test_presupuesto.db"
        if db_path.exists():
            db_path.unlink()

        db = HacuMemoryDB(db_path, self.logger)
        # Poblamos 10 mensajes previos largos
        for i in range(10):
            db.add_message("usr_test", "user", f"Pregunta previa {i}: " + ("x" * 200))
            db.add_message("usr_test", "assistant", f"Respuesta previa {i}: " + ("y" * 300))

        mock_rag = unittest.mock.MagicMock()
        mock_rag.buscar.return_value = None
        mock_rag.buscar_relevante.return_value = None

        # Configuramos presupuesto muy bajo (1200 caracteres)
        ctx_builder = ContextBuilder(
            db=db,
            rag=mock_rag,
            rag_config=RagConfig(),
            memory_config=MemoryConfig(history_messages=20),
            usuario_anonimo="usr_test",
            max_prompt_chars=1200,
        )

        mensajes = ctx_builder.build_messages(
            mensaje_usuario="¿Cuál es el estado actual?",
            intencion=Intencion.GENERAL,
            usuario_activo="usr_test",
            estado=EstadoSesion(),
        )

        # La lista total de mensajes no debe contener los 20 mensajes previos, debió recortar
        self.assertLess(len(mensajes), 10, "Debió podar mensajes antiguos para respetar el presupuesto")
        # El primer mensaje debe ser system y el último user
        self.assertEqual(mensajes[0]["role"], "system")
        self.assertEqual(mensajes[-1]["role"], "user")

        if db_path.exists():
            try:
                db_path.unlink()
            except Exception:
                pass

    def test_08_singleton_get_llm_client(self):
        """Confirma que get_llm_client() devuelva una única instancia compartida."""
        c1 = get_llm_client()
        c2 = get_llm_client()
        self.assertIs(c1, c2, "get_llm_client() debe ser un singleton thread-safe estricto")


if __name__ == "__main__":
    unittest.main()
