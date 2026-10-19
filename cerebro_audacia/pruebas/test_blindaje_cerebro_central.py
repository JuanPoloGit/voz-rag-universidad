"""Pruebas Automatizadas de Blindaje Arquitectónico del Cerebro Central (FASE 0).

Verifica de forma estricta las 12 garantías del Cerebro Central:
1. Un agente puede utilizar el Cerebro Central.
2. Dos agentes pueden utilizar el mismo Cerebro Central.
3. Cambiar el modelo central no exige cambiar el agente ni su código.
4. Un agente no puede especificar arbitrariamente otro modelo (lanza ViolacionArquitecturaError).
5. Un agente no puede indicar una ruta .gguf (lanza ViolacionArquitecturaError).
6. Las solicitudes concurrentes respetan el límite configurado por el governor.
7. Una cola saturada produce error controlado (LlmSaturadoError).
8. Un timeout no bloquea indefinidamente al agente (LlmColaExpiradaError).
9. La caída del Cerebro Central produce degradación controlada (CircuitBreaker OPEN).
10. No se crean procesos LLM secundarios desde agentes (barreras en AgentContract y AgentMetadata).
11. La configuración del modelo procede exclusivamente de la infraestructura central.
12. Los agentes no tienen acceso directo al backend físico de inferencia.
"""

from __future__ import annotations

import json
import threading
import time
import unittest
import urllib
import urllib.error
import urllib.request
from unittest.mock import MagicMock, patch
from typing import Any, Mapping

from cerebro_audacia.agentes.contrato import AgentContract
from cerebro_audacia.agentes.esquemas import (
    AgentContext,
    AgentErrorCode,
    AgentExecutionError,
    AgentInput,
    AgentMetadata,
    AgentOutput,
    ContractValidationError,
    DataAutonomyLevel,
    IdempotencyMode,
    ResourceKind,
    ResourceProfile,
)
from cerebro_audacia.conexion import (
    ClienteCerebroCentral,
    ConfiguracionCerebroCentral,
    LlmColaExpiradaError,
    LlmNoDisponibleError,
    LlmSaturadoError,
    ViolacionArquitecturaError,
)


class AgentePruebaA(AgentContract):
    """Agente de prueba para validar consumo del Cerebro Central."""

    def __init__(self, cliente_llm: ClienteCerebroCentral | None = None):
        self._llm = cliente_llm

    @property
    def metadata(self) -> AgentMetadata:
        return AgentMetadata(
            id="agente-prueba-a",
            nombre="Agente de Prueba A",
            version="1.0.0",
            descripcion="Agente consumidor del Cerebro Central para pruebas.",
            nivel_autonomia=DataAutonomyLevel.READ_ONLY,
            canales_permitidos=("consola", "web_lan"),
            requiere_autenticacion=False,
            capacidades=("consultar",),
            perfil_recursos=ResourceProfile(
                recursos_requeridos=(ResourceKind.CPU,),
                max_concurrencia=5,
                timeout_segundos=5.0,
            ),
        )

    def _ejecutar_interno(self, entrada: AgentInput) -> AgentOutput:
        mensajes = [{"role": "user", "content": entrada.contenido}]
        if self._llm:
            resp = self._llm.generar(mensajes=mensajes, max_tokens=100)
            return AgentOutput(respuesta=resp.texto, datos={"modelo": resp.modelo_reportado})
        return AgentOutput(respuesta="Sin LLM configurado")


class AgentePruebaB(AgentContract):
    """Segundo agente independiente que comparte el mismo Cerebro Central."""

    def __init__(self, cliente_llm: ClienteCerebroCentral | None = None):
        self._llm = cliente_llm

    @property
    def metadata(self) -> AgentMetadata:
        return AgentMetadata(
            id="agente-prueba-b",
            nombre="Agente de Prueba B",
            version="1.0.0",
            descripcion="Segundo agente consumidor del Cerebro Central.",
            nivel_autonomia=DataAutonomyLevel.READ_ONLY,
            canales_permitidos=("consola", "web_lan"),
            requiere_autenticacion=False,
            capacidades=("analizar",),
            perfil_recursos=ResourceProfile(
                recursos_requeridos=(ResourceKind.CPU,),
                max_concurrencia=5,
                timeout_segundos=5.0,
            ),
        )

    def _ejecutar_interno(self, entrada: AgentInput) -> AgentOutput:
        mensajes = [{"role": "user", "content": entrada.contenido}]
        if self._llm:
            resp = self._llm.generar(mensajes=mensajes, max_tokens=100)
            return AgentOutput(respuesta=f"Analizado por B: {resp.texto}")
        return AgentOutput(respuesta="Sin LLM configurado")


class TestBlindajeCerebroCentral(unittest.TestCase):
    """Suite de pruebas para certificar el blindaje del Cerebro Central Único."""

    def setUp(self):
        self.config = ConfiguracionCerebroCentral()
        self.config.url_chat = "http://127.0.0.1:8080/v1/chat/completions"
        self.config.url_modelos = "http://127.0.0.1:8080/v1/models"
        self.config.url_salud = "http://127.0.0.1:8080/health"
        self.config.modelo_por_defecto = "Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf"

    # 1. Un agente puede utilizar el Cerebro Central
    @patch("urllib.request.urlopen")
    def test_1_agente_puede_utilizar_cerebro_central(self, mock_urlopen):
        resp_mock = MagicMock()
        resp_mock.status = 200
        resp_mock.read.return_value = json.dumps({
            "choices": [{"message": {"role": "assistant", "content": "Respuesta desde el Cerebro Central"}}],
            "usage": {"completion_tokens": 10},
        }).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = resp_mock

        cliente = ClienteCerebroCentral(config=self.config)
        agente = AgentePruebaA(cliente_llm=cliente)

        ctx = AgentContext.crear(canal="consola")
        entrada = AgentInput(contenido="¿Cuál es el estado?", contexto=ctx)
        res = agente.ejecutar(entrada)

        self.assertTrue(res.exitoso)
        self.assertEqual(res.salida.respuesta, "Respuesta desde el Cerebro Central")
        self.assertEqual(res.salida.datos["modelo"], "Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf")

    # 2. Dos agentes pueden utilizar el mismo Cerebro Central
    @patch("urllib.request.urlopen")
    def test_2_dos_agentes_utilizan_mismo_cerebro_central(self, mock_urlopen):
        resp_mock = MagicMock()
        resp_mock.status = 200
        resp_mock.read.return_value = json.dumps({
            "choices": [{"message": {"role": "assistant", "content": "Dato compartido"}}],
            "usage": {"completion_tokens": 5},
        }).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = resp_mock

        cliente_compartido = ClienteCerebroCentral(config=self.config)
        agente1 = AgentePruebaA(cliente_llm=cliente_compartido)
        agente2 = AgentePruebaB(cliente_llm=cliente_compartido)

        ctx = AgentContext.crear(canal="consola")
        res1 = agente1.ejecutar(AgentInput(contenido="Consulta 1", contexto=ctx))
        res2 = agente2.ejecutar(AgentInput(contenido="Consulta 2", contexto=ctx))

        self.assertTrue(res1.exitoso)
        self.assertTrue(res2.exitoso)
        self.assertEqual(res1.salida.respuesta, "Dato compartido")
        self.assertIn("Analizado por B: Dato compartido", res2.salida.respuesta)

    # 3. Cambiar el modelo central no exige cambiar el agente
    @patch("urllib.request.urlopen")
    def test_3_cambio_modelo_central_transparente_para_agente(self, mock_urlopen):
        # Simular que el Cerebro Central ahora tiene Llama 12B cargado
        resp_mock = MagicMock()
        resp_mock.status = 200
        resp_mock.read.return_value = json.dumps({
            "choices": [{"message": {"role": "assistant", "content": "Generado con 12B"}}],
            "usage": {"completion_tokens": 8},
        }).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = resp_mock

        # El Cerebro Central cambia su modelo en infraestructura
        config_12b = ConfiguracionCerebroCentral()
        config_12b.modelo_por_defecto = "Meta-Llama-3.1-12B-Instruct-Q4_K_M.gguf"
        cliente_12b = ClienteCerebroCentral(config=config_12b)

        # El agente NO modifica su código ni su contrato
        agente = AgentePruebaA(cliente_llm=cliente_12b)
        ctx = AgentContext.crear(canal="consola")
        res = agente.ejecutar(AgentInput(contenido="Hola", contexto=ctx))

        self.assertTrue(res.exitoso)
        self.assertEqual(res.salida.respuesta, "Generado con 12B")
        self.assertEqual(res.salida.datos["modelo"], "Meta-Llama-3.1-12B-Instruct-Q4_K_M.gguf")

    # 4. Un agente no puede especificar arbitrariamente otro modelo
    def test_4_agente_no_puede_especificar_otro_modelo(self):
        cliente = ClienteCerebroCentral(config=self.config)

        # Intentar pasar 'model' al cliente oficial
        with self.assertRaises(ViolacionArquitecturaError) as cm:
            cliente.generar(
                mensajes=[{"role": "user", "content": "hola"}],
                model="otro-modelo-arbitrario",  # PROHIBIDO
            )
        self.assertIn("está prohibido", str(cm.exception))

        # Intentar pasar 'modelo'
        with self.assertRaises(ViolacionArquitecturaError):
            cliente.generar(
                mensajes=[{"role": "user", "content": "hola"}],
                modelo="gpt-4o",  # PROHIBIDO
            )

    # 5. Un agente no puede indicar una ruta .gguf
    def test_5_agente_no_puede_indicar_ruta_gguf(self):
        cliente = ClienteCerebroCentral(config=self.config)

        with self.assertRaises(ViolacionArquitecturaError) as cm:
            cliente.generar(
                mensajes=[{"role": "user", "content": "hola"}],
                model_path="/ruta/local/modelo.gguf",  # PROHIBIDO
            )
        self.assertIn("model_path", str(cm.exception))

        with self.assertRaises(ViolacionArquitecturaError):
            cliente.generar(
                mensajes=[{"role": "user", "content": "hola"}],
                gguf_path="custom.gguf",  # PROHIBIDO
            )

    # 6. Las solicitudes concurrentes respetan el límite configurado por el governor
    def test_6_concurrencia_respeta_limite_governor(self):
        # Concurrencia máxima = 2
        cliente = ClienteCerebroCentral(config=self.config, max_concurrencia=2, max_cola=5)

        bloqueo_hilo = threading.Event()
        max_activas_observadas = 0
        lock_metricas = threading.Lock()

        def peticion_simulada():
            nonlocal max_activas_observadas
            with cliente.governor.adquirir():
                with lock_metricas:
                    if cliente.governor.activas > max_activas_observadas:
                        max_activas_observadas = cliente.governor.activas
                bloqueo_hilo.wait(timeout=0.5)

        hilos = [threading.Thread(target=peticion_simulada) for _ in range(4)]
        for h in hilos:
            h.start()

        time.sleep(0.05)
        # En ningún momento debe superar max_concurrencia=2
        self.assertLessEqual(max_activas_observadas, 2)
        bloqueo_hilo.set()
        for h in hilos:
            h.join()

    # 7. Una cola saturada produce error controlado (LlmSaturadoError)
    def test_7_cola_saturada_produce_error_controlado(self):
        # Concurrencia = 1, Cola = 1
        cliente = ClienteCerebroCentral(config=self.config, max_concurrencia=1, max_cola=1)

        bloqueo = threading.Event()

        def peticion_bloqueante():
            with cliente.governor.adquirir():
                bloqueo.wait(timeout=1.0)

        # Ocupar la única ranura activa
        t1 = threading.Thread(target=peticion_bloqueante)
        t1.start()
        time.sleep(0.02)

        # Ocupar el único puesto en cola
        t2 = threading.Thread(target=peticion_bloqueante)
        t2.start()
        time.sleep(0.02)

        # La tercera petición debe ser rechazada inmediatamente por saturación
        with self.assertRaises(LlmSaturadoError) as cm:
            with cliente.governor.adquirir():
                pass

        self.assertIn("saturado", str(cm.exception).lower())
        bloqueo.set()
        t1.join()
        t2.join()

    # 8. Un timeout no bloquea indefinidamente al agente
    def test_8_timeout_en_cola_desbloquea_con_error(self):
        # Timeout de cola ultracorto: 0.1s
        cliente = ClienteCerebroCentral(config=self.config, max_concurrencia=1, max_cola=2, timeout_cola=0.1)

        bloqueo = threading.Event()

        def peticion_larga():
            with cliente.governor.adquirir():
                bloqueo.wait(timeout=1.0)

        t1 = threading.Thread(target=peticion_larga)
        t1.start()
        time.sleep(0.02)

        # La segunda petición espera más de 0.1s y debe lanzar LlmColaExpiradaError
        inicio = time.monotonic()
        with self.assertRaises(LlmColaExpiradaError):
            with cliente.governor.adquirir():
                pass
        duracion = time.monotonic() - inicio
        self.assertLess(duracion, 0.4)  # No bloqueó indefinidamente

        bloqueo.set()
        t1.join()

    # 9. La caída del Cerebro Central produce degradación controlada (Circuit Breaker)
    @patch("urllib.request.urlopen")
    def test_9_caida_del_cerebro_central_activa_circuit_breaker(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.URLError("Conexión rechazada por daemon caído")

        cliente = ClienteCerebroCentral(config=self.config)

        # Provocar 3 fallos consecutivos
        for _ in range(3):
            try:
                cliente.generar(mensajes=[{"role": "user", "content": "hola"}])
            except ConnectionError:
                pass

        # El cuarto intento debe fallar de inmediato en 0 ms con LlmNoDisponibleError
        inicio = time.perf_counter()
        with self.assertRaises(LlmNoDisponibleError) as cm:
            cliente.generar(mensajes=[{"role": "user", "content": "hola"}])
        latencia_ms = (time.perf_counter() - inicio) * 1000.0

        self.assertLess(latencia_ms, 5.0)  # Fallo inmediato sin esperar sockets
        self.assertIn("OPEN", str(cm.exception))

    # 10. No se crean procesos LLM secundarios desde agentes (barreras en AgentContract y AgentMetadata)
    def test_10_barrera_contra_agentes_con_llm_propio(self):
        # Intento A: Declarar dependencias prohibidas de modelos locales
        with self.assertRaises(ContractValidationError) as cm:
            AgentMetadata(
                id="agente-rebelde",
                nombre="Agente Rebelde",
                version="1.0.0",
                descripcion="Intenta cargar llama_cpp",
                nivel_autonomia=DataAutonomyLevel.READ_ONLY,
                canales_permitidos=("consola",),
                requiere_autenticacion=False,
                dependencias=("llama_cpp", "modelo.gguf"),  # PROHIBIDO
            )
        self.assertIn("regla de Cerebro Central único", str(cm.exception))

        # Intento B: Agente que posee atributos prohibidos de modelo propio
        class AgenteConModeloLocal(AgentContract):
            def __init__(self):
                self._llama = "instancia_local_falsa"  # ATRIBUTO PROHIBIDO

            @property
            def metadata(self) -> AgentMetadata:
                return AgentMetadata(
                    id="agente-con-modelo",
                    nombre="Agente Con Modelo",
                    version="1.0.0",
                    descripcion="Posee modelo propio",
                    nivel_autonomia=DataAutonomyLevel.READ_ONLY,
                    canales_permitidos=("consola",),
                    requiere_autenticacion=False,
                )

            def _ejecutar_interno(self, entrada: AgentInput) -> AgentOutput:
                return AgentOutput(respuesta="hack")

        agente_ilegal = AgenteConModeloLocal()
        ctx = AgentContext.crear(canal="consola")
        res = agente_ilegal.ejecutar(AgentInput(contenido="hola", contexto=ctx))
        self.assertFalse(res.exitoso)
        self.assertIn("posee el atributo prohibido '_llama'", res.error.mensaje)

        # Intento C: Entrada con parámetro 'model'
        agente_valido = AgentePruebaA()
        entrada_con_param_modelo = AgentInput(
            contenido="hola",
            contexto=ctx,
            parametros={"model": "gpt-4"},  # PROHIBIDO
        )
        res_param = agente_valido.ejecutar(entrada_con_param_modelo)
        self.assertFalse(res_param.exitoso)
        self.assertIn("Parámetro prohibido 'model'", res_param.error.mensaje)

    # 11. La configuración del modelo procede exclusivamente del Cerebro Central
    def test_11_configuracion_modelo_procede_exclusivamente_de_infraestructura(self):
        cfg = ConfiguracionCerebroCentral()
        # La configuración contiene URLs del servicio e identidad oficial
        self.assertTrue(cfg.url_chat.startswith("http"))
        self.assertTrue(cfg.url_salud.endswith("/health"))
        self.assertTrue(cfg.url_modelos.endswith("/v1/models"))
        self.assertIn("Llama", cfg.modelo_por_defecto)

    # 12. Los agentes no tienen acceso directo al backend físico de inferencia
    def test_12_agentes_sin_acceso_directo_a_backend_fisico(self):
        cliente = ClienteCerebroCentral(config=self.config)
        # El cliente no expone socket crudo, punteros CUDA ni descriptores de archivo del servidor
        self.assertFalse(hasattr(cliente, "socket"))
        self.assertFalse(hasattr(cliente, "proceso_servidor"))
        self.assertFalse(hasattr(cliente, "vram_handle"))


if __name__ == "__main__":
    unittest.main()
