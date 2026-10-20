"""Pruebas unitarias de resiliencia avanzada para el Cerebro Central y Supervisor.

Cobertura:
1. Supervisor de Proceso (SupervisorLlamaServer):
   - Exclusividad de bloqueo (lockfile) para evitar supervisores concurrentes.
   - Detección de caída de proceso (DEAD) y registro de historial de fallos.
   - Límite estricto de reinicios consecutivos y transición a estado AGOTADO.
   - Rechazo de auto-reinicio una vez en AGOTADO hasta restablecimiento explícito.
   - Validación de propiedad de PID y limpieza de archivos de estado obsoletos.
   - Parada limpia y liberación de recursos.

2. Cliente del Cerebro Central (ClienteCerebroCentral):
   - Rechazo temprano por exceso de caracteres de prompt (LlmLimiteExcedidoError).
   - Rechazo por desbordamiento de presupuesto de contexto por ranura (LlmLimiteExcedidoError).
   - Detección y tipificación de errores de memoria GPU (CUDA OOM -> LlmOomError).
   - Detección de respuestas corruptas o incompletas (JSONDecodeError -> LlmRespuestaInvalidaError).
   - Rechazo por saturación de concurrencia (HTTP 429 / Cola llena -> LlmSaturadoError).
   - Registro y recolección thread-safe de telemetría de fallos en memoria (obtener_metricas).
"""

from __future__ import annotations

import io
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

from cerebro_audacia.conexion.cliente import (
    ClienteCerebroCentral,
    ConfiguracionCerebroCentral,
)
from cerebro_audacia.conexion.excepciones import (
    LlmLimiteExcedidoError,
    LlmNoDisponibleError,
    LlmOomError,
    LlmRespuestaInvalidaError,
    LlmSaturadoError,
    ViolacionArquitecturaError,
)
from cerebro_audacia.proveedor_modelo.supervisor import (
    EstadoSupervisor,
    SupervisorLlamaServer,
)


class TestSupervisorLlamaServer(unittest.TestCase):
    """Pruebas unitarias del supervisor desacoplado de llama-server."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="audacia_sup_test_")
        self.directorio = Path(self.temp_dir)
        self.supervisor = SupervisorLlamaServer(
            directorio_base=self.directorio,
            max_reinicios_consecutivos=3,
            espera_base_s=0.01,
            factor_backoff=1.5,
            ventana_estabilidad_s=1.0,
        )

    def tearDown(self) -> None:
        self.supervisor.detener()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_01_bloqueo_exclusivo_evita_supervisores_duplicados(self) -> None:
        """Dos instancias no pueden supervisar simultáneamente el mismo directorio."""
        adquirido_1 = self.supervisor.adquirir_bloqueo_supervisor()
        self.assertTrue(adquirido_1, "El primer supervisor debió adquirir el lock.")

        supervisor_2 = SupervisorLlamaServer(directorio_base=self.directorio)
        adquirido_2 = supervisor_2.adquirir_bloqueo_supervisor()
        self.assertFalse(adquirido_2, "El segundo supervisor no debe poder adquirir el mismo lock.")

        self.supervisor.liberar_bloqueo_supervisor()

        adquirido_3 = supervisor_2.adquirir_bloqueo_supervisor()
        self.assertTrue(adquirido_3, "Tras liberar el primero, el segundo supervisor debió adquirirlo.")
        supervisor_2.liberar_bloqueo_supervisor()

    def test_02_limpieza_de_estado_con_pid_obsoleto(self) -> None:
        """Si el archivo de estado apunta a un PID inexistente, se descarta y limpia."""
        pid_falso = 999999
        self.supervisor.registrar_estado_disco(
            pid=pid_falso,
            puerto=8080,
            perfil="servidor_rtx5070ti",
            ejecutable="llama-server.exe",
            modelo="test.gguf",
        )
        self.assertTrue(self.supervisor.archivo_estado.exists())

        with patch.object(self.supervisor, "verificar_propiedad_proceso", return_value=False):
            estado_leido = self.supervisor.leer_estado_disco()
            self.assertIsNone(estado_leido, "No debió aceptar un PID no validado.")
            self.assertFalse(self.supervisor.archivo_estado.exists(), "Debió limpiar el archivo de estado obsoleto.")

    def test_03_deteccion_de_caida_y_transicion_a_agotado(self) -> None:
        """Superar el límite de reinicios consecutivos lleva al estado AGOTADO."""
        self.assertEqual(self.supervisor.estado, EstadoSupervisor.DETENIDO)

        # Simular caídas consecutivas sin comando de arranque
        for i in range(1, 4):
            estado = self.supervisor.supervisar_paso()
            self.assertEqual(estado, EstadoSupervisor.DETENIDO)
            self.assertEqual(self.supervisor.reinicios_consecutivos, i)

        # El 4to intento debe superar max_reinicios (3) y entrar en AGOTADO
        estado_final = self.supervisor.supervisar_paso()
        self.assertEqual(estado_final, EstadoSupervisor.AGOTADO)
        self.assertEqual(self.supervisor.estado, EstadoSupervisor.AGOTADO)

        # En estado AGOTADO, llamadas subsiguientes se rechazan inmediatamente
        estado_bloqueado = self.supervisor.supervisar_paso()
        self.assertEqual(estado_bloqueado, EstadoSupervisor.AGOTADO)

        # Se puede restablecer manualmente
        self.supervisor.restablecer_supervisor()
        self.assertEqual(self.supervisor.estado, EstadoSupervisor.DETENIDO)
        self.assertEqual(self.supervisor.reinicios_consecutivos, 0)

    def test_04_reseteo_de_contador_tras_periodo_estable(self) -> None:
        """Si el proceso permanece saludable por la ventana de estabilidad, reinicios vuelve a 0."""
        # Forzar fallos previos
        self.supervisor.supervisar_paso()
        self.assertEqual(self.supervisor.reinicios_consecutivos, 1)

        # Mockear proceso vivo y saludable
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.poll.return_value = None
        self.supervisor._proceso = mock_proc

        with patch.object(self.supervisor, "comprobar_salud_endpoint", return_value=True):
            # Primer paso saludable: registra último éxito
            estado_1 = self.supervisor.supervisar_paso()
            self.assertEqual(estado_1, EstadoSupervisor.SALUDABLE)
            self.assertEqual(self.supervisor.reinicios_consecutivos, 1)

            # Simular paso del tiempo mayor a ventana_estabilidad_s (1.0s)
            self.supervisor._ultimo_exito_salud -= 2.0
            estado_2 = self.supervisor.supervisar_paso()
            self.assertEqual(estado_2, EstadoSupervisor.SALUDABLE)
            self.assertEqual(self.supervisor.reinicios_consecutivos, 0, "Debió resetear contador de reinicios.")


class TestClienteCerebroCentralResiliencia(unittest.TestCase):
    """Pruebas unitarias de límites, validaciones, OOM y telemetría en el cliente."""

    def setUp(self) -> None:
        self.cfg = ConfiguracionCerebroCentral()
        self.cfg.max_caracteres_prompt = 1000
        self.cfg.n_ctx = 4096
        self.cfg.parallel = 2  # ventana por ranura = 2048
        self.cfg.margen_seguridad_tokens = 128  # presupuesto utilizable = 1920
        self.cliente = ClienteCerebroCentral(config=self.cfg)

    def test_01_rechazo_por_caracteres_excesivos_en_prompt(self) -> None:
        """Prompt con más caracteres que max_caracteres_prompt se rechaza con LlmLimiteExcedidoError."""
        prompt_gigante = "A" * 1200
        mensajes = [{"role": "user", "content": prompt_gigante}]

        with self.assertRaises(LlmLimiteExcedidoError) as ctx:
            self.cliente.generar(mensajes=mensajes, max_tokens=100)

        self.assertIn("excede el límite máximo permitido", str(ctx.exception))
        metricas = self.cliente.obtener_metricas()
        self.assertEqual(metricas["limites_excedidos"], 1)
        self.assertEqual(metricas["total_solicitudes"], 1)

    def test_02_rechazo_por_max_tokens_invalido(self) -> None:
        """max_tokens <= 0 o no entero lanza ValueError y suma a límites excedidos."""
        mensajes = [{"role": "user", "content": "Hola"}]

        with self.assertRaises(ValueError):
            self.cliente.generar(mensajes=mensajes, max_tokens=-5)

        with self.assertRaises(ValueError):
            self.cliente.generar(mensajes=mensajes, max_tokens=0)

        metricas = self.cliente.obtener_metricas()
        self.assertEqual(metricas["limites_excedidos"], 2)

    def test_03_rechazo_por_presupuesto_de_contexto_excedido(self) -> None:
        """Solicitar max_tokens mayor que la ventana utilizable lanza LlmLimiteExcedidoError."""
        mensajes = [{"role": "user", "content": "Hola"}]
        # Presupuesto utilizable = 2048 - 128 = 1920 tokens
        with self.assertRaises(LlmLimiteExcedidoError) as ctx:
            self.cliente.generar(mensajes=mensajes, max_tokens=2000)

        self.assertIn("excede la ventana máxima utilizable por ranura", str(ctx.exception))
        metricas = self.cliente.obtener_metricas()
        self.assertEqual(metricas["limites_excedidos"], 1)

    def test_04_rechazo_por_presupuesto_combinado_entrada_salida(self) -> None:
        """La suma de tokens estimados de entrada + max_tokens no puede exceder el presupuesto."""
        # 800 caracteres ~= 250 tokens de entrada.
        # Presupuesto: 1920. Si pedimos max_tokens = 1800, 250 + 1800 = 2050 > 1920.
        mensajes = [{"role": "user", "content": "x" * 800}]
        with self.assertRaises(LlmLimiteExcedidoError) as ctx:
            self.cliente.generar(mensajes=mensajes, max_tokens=1800)

        self.assertIn("presupuesto de contexto estimado", str(ctx.exception))
        metricas = self.cliente.obtener_metricas()
        self.assertEqual(metricas["limites_excedidos"], 1)

    def test_05_deteccion_cuda_oom_y_alerta_a_resource_tracker(self) -> None:
        """Una respuesta HTTP de error con mensaje de CUDA OOM lanza LlmOomError y marca tracker."""
        tracker_mock = MagicMock()
        self.cliente.resource_tracker = tracker_mock

        mensajes = [{"role": "user", "content": "Calcula esto"}]

        cuerpo_oom = b'{"error": {"message": "CUDA out of memory: tried to allocate 512MiB", "type": "server_error"}}'
        error_http = urllib.error.HTTPError(
            url="http://127.0.0.1:8080/v1/chat/completions",
            code=500,
            msg="Internal Server Error",
            hdrs={},
            fp=io.BytesIO(cuerpo_oom),
        )

        with patch("urllib.request.urlopen", side_effect=error_http):
            with self.assertRaises(LlmOomError) as ctx:
                self.cliente.generar(mensajes=mensajes, max_tokens=100)

            self.assertIn("CUDA OOM", str(ctx.exception))

        metricas = self.cliente.obtener_metricas()
        self.assertEqual(metricas["errores_oom"], 1)
        self.assertTrue(tracker_mock.marcar_saturado.called)

    def test_06_deteccion_respuesta_truncada_o_json_malformado(self) -> None:
        """Si llama-server devuelve un payload incompleto o JSON inválido, lanza LlmRespuestaInvalidaError."""
        mensajes = [{"role": "user", "content": "Hola"}]

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b'{"choices": [{"message": {"content": "Hola'  # JSON truncado
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            with self.assertRaises(LlmRespuestaInvalidaError) as ctx:
                self.cliente.generar(mensajes=mensajes, max_tokens=50)

            self.assertIn("truncada o JSON malformado", str(ctx.exception))

        metricas = self.cliente.obtener_metricas()
        self.assertEqual(metricas["respuestas_invalidas"], 1)

    def test_07_saturacion_http_429(self) -> None:
        """Un código HTTP 429 lanza LlmSaturadoError y computa en métricas de saturación."""
        mensajes = [{"role": "user", "content": "Hola"}]

        error_429 = urllib.error.HTTPError(
            url="http://127.0.0.1:8080/v1/chat/completions",
            code=429,
            msg="Too Many Requests",
            hdrs={},
            fp=io.BytesIO(b"Servidor saturado"),
        )

        with patch("urllib.request.urlopen", side_effect=error_429):
            with self.assertRaises(LlmSaturadoError):
                self.cliente.generar(mensajes=mensajes, max_tokens=50)

        metricas = self.cliente.obtener_metricas()
        self.assertEqual(metricas["rechazos_saturacion"], 1)

    def test_08_metricas_thread_safe_bajo_concurrencia(self) -> None:
        """Múltiples hilos concurrentes actualizan las métricas de forma atómica y consistente."""
        mensajes = [{"role": "user", "content": "Mensaje corto"}]

        def tarea_simulada(i: int) -> None:
            try:
                # La mitad violará el límite de max_tokens
                if i % 2 == 0:
                    self.cliente.generar(mensajes=mensajes, max_tokens=0)
                else:
                    self.cliente.generar(mensajes=[], max_tokens=50)
            except Exception:
                pass

        hilos = [threading.Thread(target=tarea_simulada, args=(i,)) for i in range(20)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()

        metricas = self.cliente.obtener_metricas()
        self.assertEqual(metricas["total_solicitudes"], 20)
        self.assertEqual(metricas["limites_excedidos"], 20)


if __name__ == "__main__":
    unittest.main()
