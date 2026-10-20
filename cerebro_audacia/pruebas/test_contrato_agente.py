"""Pruebas unitarias exhaustivas y aisladas para AgentContract y esquemas tipados.

Cumple estrictamente los requisitos de la Fase Multiagente 1:
- Cero dependencias externas de GPU, LLM, STT, Django, base de datos o red.
- Cobertura completa de casos positivos, negativos y límites.
- Verificación de invariantes, trazabilidad, sanitización y serialización.
"""

from __future__ import annotations

import unittest
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
    ContractValidationError,
    DataAutonomyLevel,
)


# ─────────────────────────────────────────────────────────────────────────────
# AGENTES FALSOS (TEST DOUBLES) PARA VERIFICAR EL CONTRATO
# ─────────────────────────────────────────────────────────────────────────────


class AgenteConsultaMock(AgentContract):
    """Doble de prueba que simula un agente de solo lectura (ej. consulta de stock)."""

    def __init__(self, stock_simulado: int = 42):
        self._stock = stock_simulado

    @property
    def metadata(self) -> AgentMetadata:
        return AgentMetadata(
            id="mock_consulta",
            nombre="Agente de Consulta Mock",
            version="1.0.0",
            descripcion="Agente de prueba para consultas deterministas de solo lectura.",
            nivel_autonomia=DataAutonomyLevel.READ_ONLY,
            canales_permitidos=("web_lan", "rest_api"),
            requiere_autenticacion=True,
            capacidades=("consultar_stock", "listar_almacenes"),
            modalidades_salida=("texto", "datos_json"),
        )

    def _ejecutar_interno(self, entrada: AgentInput) -> AgentOutput:
        producto = entrada.parametros.get("producto", "Tornillo M6")
        return AgentOutput(
            respuesta=f"El stock actual de {producto} es {self._stock} unidades.",
            datos={"producto": producto, "stock": self._stock},
            modalidad="texto",
        )


class AgentePropuestaMock(AgentContract):
    """Doble de prueba que simula un agente con nivel de autonomía PROPOSAL_BASED."""

    @property
    def metadata(self) -> AgentMetadata:
        return AgentMetadata(
            id="mock_propuesta",
            nombre="Agente de Propuesta Mock",
            version="1.0.0",
            descripcion="Agente de prueba capaz de emitir propuestas de mutación.",
            nivel_autonomia=DataAutonomyLevel.PROPOSAL_BASED,
            canales_permitidos=("web_lan", "consola"),
            requiere_autenticacion=True,
            capacidades=("mover_stock",),
            modalidades_salida=("texto",),
        )

    def _ejecutar_interno(self, entrada: AgentInput) -> AgentOutput:
        cantidad = entrada.parametros.get("cantidad", 5)
        return AgentOutput(
            respuesta=f"Se propone transferir {cantidad} unidades. ¿Confirmar?",
            datos={"cantidad": cantidad},
            modalidad="texto",
            propuesta={
                "accion": "transferencia",
                "cantidad": cantidad,
                "origen": "Bodega A",
                "destino": "Bodega B",
            },
        )


class AgenteReadOnlyConPropuestaIlegalMock(AgentContract):
    """Doble de prueba que viola el contrato intentando emitir una propuesta siendo READ_ONLY."""

    @property
    def metadata(self) -> AgentMetadata:
        return AgentMetadata(
            id="mock_read_only_ilegal",
            nombre="Agente Ilegal Mock",
            version="1.0.0",
            descripcion="Intenta emitir propuesta siendo solo de lectura.",
            nivel_autonomia=DataAutonomyLevel.READ_ONLY,
            canales_permitidos=("web_lan",),
            requiere_autenticacion=False,
            capacidades=("consultar",),
        )

    def _ejecutar_interno(self, entrada: AgentInput) -> AgentOutput:
        return AgentOutput(
            respuesta="Respuesta no autorizada",
            propuesta={"mutacion": "no_permitida"},
        )


class AgenteRetornoInvalidoMock(AgentContract):
    """Doble de prueba que viola el contrato retornando un tipo que no es AgentOutput."""

    @property
    def metadata(self) -> AgentMetadata:
        return AgentMetadata(
            id="mock_retorno_invalido",
            nombre="Agente Retorno Inválido Mock",
            version="1.0.0",
            descripcion="Retorna una cadena en vez de AgentOutput.",
            nivel_autonomia=DataAutonomyLevel.READ_ONLY,
            canales_permitidos=("web_lan",),
            requiere_autenticacion=False,
        )

    def _ejecutar_interno(self, entrada: AgentInput) -> Any:
        return "cadena_plana_en_lugar_de_AgentOutput"  # Violación


class AgenteConFalloControladoMock(AgentContract):
    """Doble de prueba que simula un fallo de negocio controlado (AgentExecutionError)."""

    @property
    def metadata(self) -> AgentMetadata:
        return AgentMetadata(
            id="mock_fallo_controlado",
            nombre="Agente Fallo Controlado Mock",
            version="1.0.0",
            descripcion="Lanza AgentExecutionError.",
            nivel_autonomia=DataAutonomyLevel.READ_ONLY,
            canales_permitidos=("web_lan",),
            requiere_autenticacion=False,
        )

    def _ejecutar_interno(self, entrada: AgentInput) -> AgentOutput:
        raise AgentExecutionError(
            mensaje_seguro="El producto solicitado no existe en el catálogo.",
            codigo=AgentErrorCode.RESOURCE_UNAVAILABLE,
            detalles={"codigo_buscado": "PROD-999"},
        )


class AgenteConExcepcionInesperadaMock(AgentContract):
    """Doble de prueba que sufre un bug no controlado (ej. ZeroDivisionError)."""

    @property
    def metadata(self) -> AgentMetadata:
        return AgentMetadata(
            id="mock_excepcion_inesperada",
            nombre="Agente Bug Mock",
            version="1.0.0",
            descripcion="Sufre un error no controlado.",
            nivel_autonomia=DataAutonomyLevel.READ_ONLY,
            canales_permitidos=("web_lan",),
            requiere_autenticacion=False,
        )

    def _ejecutar_interno(self, entrada: AgentInput) -> AgentOutput:
        # Simular error imprevisto (ej. división por cero o fallo en driver)
        resultado = 10 / 0
        return AgentOutput(respuesta=str(resultado))


class AgenteConKeyboardInterruptMock(AgentContract):
    """Doble de prueba que lanza KeyboardInterrupt durante la ejecución."""

    @property
    def metadata(self) -> AgentMetadata:
        return AgentMetadata(
            id="mock_keyboard_interrupt",
            nombre="Agente Interrupción Teclado Mock",
            version="1.0.0",
            descripcion="Simula una señal SIGINT / KeyboardInterrupt durante la inferencia.",
            nivel_autonomia=DataAutonomyLevel.READ_ONLY,
            canales_permitidos=("web_lan",),
            requiere_autenticacion=False,
        )

    def _ejecutar_interno(self, entrada: AgentInput) -> AgentOutput:
        raise KeyboardInterrupt("Simulación de interrupción manual SIGINT")


class AgenteConSystemExitMock(AgentContract):
    """Doble de prueba que lanza SystemExit durante la ejecución."""

    @property
    def metadata(self) -> AgentMetadata:
        return AgentMetadata(
            id="mock_system_exit",
            nombre="Agente Salida Sistema Mock",
            version="1.0.0",
            descripcion="Simula una señal de terminación del proceso sys.exit().",
            nivel_autonomia=DataAutonomyLevel.READ_ONLY,
            canales_permitidos=("web_lan",),
            requiere_autenticacion=False,
        )

    def _ejecutar_interno(self, entrada: AgentInput) -> AgentOutput:
        raise SystemExit(1)



# ─────────────────────────────────────────────────────────────────────────────
# SUITE DE PRUEBAS DEL CONTRATO Y ESQUEMAS
# ─────────────────────────────────────────────────────────────────────────────


class TestAgentMetadata(unittest.TestCase):
    """Pruebas de validación estricta de AgentMetadata."""

    def test_metadata_valida(self):
        meta = AgentMetadata(
            id="haku_inventario",
            nombre="Haku Inventario",
            version="1.0.0",
            descripcion="Agente de bodega",
            nivel_autonomia=DataAutonomyLevel.PROPOSAL_BASED,
            canales_permitidos=("web_lan", "rest_api"),
            requiere_autenticacion=True,
            capacidades=("consultar_stock", "proponer_ajuste"),
        )
        self.assertEqual(meta.id, "haku_inventario")
        self.assertEqual(meta.nivel_autonomia, DataAutonomyLevel.PROPOSAL_BASED)
        self.assertTrue(meta.requiere_autenticacion)
        self.assertEqual(len(meta.canales_permitidos), 2)

    def test_metadata_rechaza_id_invalido(self):
        casos_invalidos = ["Haku Inventario", "haku.inventario", "haku/inv", "HAKU", "", "   "]
        for cid in casos_invalidos:
            with self.subTest(cid=cid):
                with self.assertRaises(ContractValidationError) as ctx:
                    AgentMetadata(
                        id=cid,
                        nombre="Nombre",
                        version="1.0",
                        descripcion="Desc",
                        nivel_autonomia=DataAutonomyLevel.READ_ONLY,
                        canales_permitidos=("web_lan",),
                        requiere_autenticacion=False,
                    )
                self.assertIn("inválido", str(ctx.exception).lower())

    def test_metadata_rechaza_canales_vacios(self):
        with self.assertRaises(ContractValidationError):
            AgentMetadata(
                id="agente_test",
                nombre="Nombre",
                version="1.0",
                descripcion="Desc",
                nivel_autonomia=DataAutonomyLevel.READ_ONLY,
                canales_permitidos=(),
                requiere_autenticacion=False,
            )

    def test_metadata_es_inmutable_frozen(self):
        meta = AgentMetadata(
            id="agente_test",
            nombre="Nombre",
            version="1.0",
            descripcion="Desc",
            nivel_autonomia=DataAutonomyLevel.READ_ONLY,
            canales_permitidos=("web_lan",),
            requiere_autenticacion=False,
        )
        with self.assertRaises(Exception):
            meta.id = "nuevo_id"  # type: ignore


class TestAgentContextAndInput(unittest.TestCase):
    """Pruebas para AgentContext y AgentInput."""

    def test_creacion_contexto_autogenera_correlation_id_y_timestamp(self):
        ctx = AgentContext.crear(canal="web_lan", usuario_id="usr_01")
        self.assertTrue(len(ctx.correlation_id) > 10)
        self.assertEqual(ctx.canal, "web_lan")
        self.assertEqual(ctx.usuario_id, "usr_01")
        self.assertTrue(ctx.timestamp_utc)

    def test_contexto_rechaza_canal_vacio(self):
        with self.assertRaises(ContractValidationError):
            AgentContext.crear(canal="")

    def test_input_valido_y_serializacion(self):
        ctx = AgentContext.crear(canal="web_lan", usuario_id="usr_01", correlation_id="trace-12345")
        inp = AgentInput(
            contenido="¿Cuánto stock hay de tornillos?",
            contexto=ctx,
            intencion="consultar_stock",
            parametros={"categoria": "ferreteria"},
        )
        d = inp.to_dict()
        self.assertEqual(d["contenido"], "¿Cuánto stock hay de tornillos?")
        self.assertEqual(d["contexto"]["correlation_id"], "trace-12345")
        self.assertEqual(d["parametros"]["categoria"], "ferreteria")

        # Deserialización simétrica
        inp2 = AgentInput.from_dict(d)
        self.assertEqual(inp2.contenido, inp.contenido)
        self.assertEqual(inp2.contexto.correlation_id, "trace-12345")
        self.assertEqual(inp2.parametros, inp.parametros)

    def test_input_rechaza_contenido_no_string(self):
        ctx = AgentContext.crear(canal="web_lan")
        with self.assertRaises(ContractValidationError):
            AgentInput(contenido=12345, contexto=ctx)  # type: ignore


class TestAgentResultInvariants(unittest.TestCase):
    """Pruebas de las invariantes estrictas de AgentResult."""

    def test_result_exito_correcto(self):
        salida = AgentOutput(respuesta="OK", datos={"val": 1})
        res = AgentResult.exito(salida=salida, correlation_id="c-1", duracion_ms=12.5)
        self.assertTrue(res.exitoso)
        self.assertIsNotNone(res.salida)
        self.assertIsNone(res.error)
        self.assertEqual(res.correlation_id, "c-1")
        self.assertEqual(res.duracion_ms, 12.5)

    def test_result_fallo_correcto(self):
        error = AgentError(codigo=AgentErrorCode.INVALID_INPUT, mensaje="Parámetro faltante")
        res = AgentResult.fallo(error=error, correlation_id="c-2", duracion_ms=5.0)
        self.assertFalse(res.exitoso)
        self.assertIsNone(res.salida)
        self.assertIsNotNone(res.error)
        self.assertEqual(res.error.codigo, AgentErrorCode.INVALID_INPUT)

    def test_invariante_exito_sin_salida_es_rechazado(self):
        with self.assertRaises(ContractValidationError):
            AgentResult(exitoso=True, correlation_id="c-1", salida=None, error=None)

    def test_invariante_exito_con_error_es_rechazado(self):
        salida = AgentOutput(respuesta="OK")
        error = AgentError(codigo=AgentErrorCode.INTERNAL_ERROR, mensaje="Error")
        with self.assertRaises(ContractValidationError):
            AgentResult(exitoso=True, correlation_id="c-1", salida=salida, error=error)

    def test_invariante_fallo_sin_error_es_rechazado(self):
        with self.assertRaises(ContractValidationError):
            AgentResult(exitoso=False, correlation_id="c-1", salida=None, error=None)

    def test_invariante_fallo_con_salida_es_rechazado(self):
        salida = AgentOutput(respuesta="OK")
        error = AgentError(codigo=AgentErrorCode.INTERNAL_ERROR, mensaje="Error")
        with self.assertRaises(ContractValidationError):
            AgentResult(exitoso=False, correlation_id="c-1", salida=salida, error=error)

    def test_serializacion_simetrica_resultado_exito_y_fallo(self):
        # Exito
        res_ok = AgentResult.exito(
            salida=AgentOutput(respuesta="Stock: 10", datos={"stock": 10}),
            correlation_id="trace-ok",
            duracion_ms=8.2,
        )
        dict_ok = res_ok.to_dict()
        res_ok_deserializado = AgentResult.from_dict(dict_ok)
        self.assertEqual(res_ok_deserializado.exitoso, True)
        self.assertEqual(res_ok_deserializado.salida.respuesta, "Stock: 10")
        self.assertEqual(res_ok_deserializado.correlation_id, "trace-ok")

        # Fallo
        res_err = AgentResult.fallo(
            error=AgentError(codigo=AgentErrorCode.TIMEOUT, mensaje="Tiempo agotado"),
            correlation_id="trace-err",
            duracion_ms=100.0,
        )
        dict_err = res_err.to_dict()
        res_err_deserializado = AgentResult.from_dict(dict_err)
        self.assertEqual(res_err_deserializado.exitoso, False)
        self.assertEqual(res_err_deserializado.error.codigo, AgentErrorCode.TIMEOUT)


class TestAgentContractExecution(unittest.TestCase):
    """Pruebas del ciclo de vida y garantías de ejecución de AgentContract."""

    def test_ejecucion_exitosa_agente_mock(self):
        agente = AgenteConsultaMock(stock_simulado=100)
        ctx = AgentContext.crear(canal="web_lan", usuario_id="operador_01", correlation_id="trace-abc-123")
        entrada = AgentInput(
            contenido="Consulta de existencia",
            contexto=ctx,
            intencion="consultar_stock",
            parametros={"producto": "Martillo"},
        )

        # Invocación directa
        resultado = agente.ejecutar(entrada)

        self.assertTrue(resultado.exitoso)
        self.assertEqual(resultado.correlation_id, "trace-abc-123")
        self.assertIn("100 unidades", resultado.salida.respuesta)
        self.assertEqual(resultado.salida.datos["stock"], 100)
        self.assertGreater(resultado.duracion_ms, 0.0)

    def test_invocacion_mediante_callable(self):
        agente = AgenteConsultaMock()
        ctx = AgentContext.crear(canal="web_lan", usuario_id="usr_01")
        entrada = AgentInput(contenido="Consulta", contexto=ctx)
        # agente(entrada) equivalente a agente.ejecutar(entrada)
        resultado = agente(entrada)
        self.assertTrue(resultado.exitoso)

    def test_agente_propuesta_emite_propuesta_valida(self):
        agente = AgentePropuestaMock()
        ctx = AgentContext.crear(canal="web_lan", usuario_id="admin_01", correlation_id="trace-prop-1")
        entrada = AgentInput(
            contenido="Transferir 10 unidades",
            contexto=ctx,
            intencion="mover_stock",
            parametros={"cantidad": 10},
        )
        resultado = agente.ejecutar(entrada)

        self.assertTrue(resultado.exitoso)
        self.assertIsNotNone(resultado.salida.propuesta)
        self.assertEqual(resultado.salida.propuesta["cantidad"], 10)
        self.assertEqual(resultado.salida.propuesta["accion"], "transferencia")

    def test_violacion_autonomia_read_only_con_propuesta_retorna_fallo(self):
        agente = AgenteReadOnlyConPropuestaIlegalMock()
        ctx = AgentContext.crear(canal="web_lan", correlation_id="trace-ilegal")
        entrada = AgentInput(contenido="Probar ilegalidad", contexto=ctx)

        resultado = agente.ejecutar(entrada)

        self.assertFalse(resultado.exitoso)
        self.assertEqual(resultado.correlation_id, "trace-ilegal")
        self.assertEqual(resultado.error.codigo, AgentErrorCode.EXECUTION_FAILED)
        self.assertIn("es READ_ONLY y no puede generar propuestas", resultado.error.mensaje)

    def test_violacion_tipo_retorno_agente_retorna_fallo_interno(self):
        agente = AgenteRetornoInvalidoMock()
        ctx = AgentContext.crear(canal="web_lan", correlation_id="trace-tipo")
        entrada = AgentInput(contenido="Probar tipo", contexto=ctx)

        resultado = agente.ejecutar(entrada)

        self.assertFalse(resultado.exitoso)
        self.assertEqual(resultado.error.codigo, AgentErrorCode.INTERNAL_ERROR)
        self.assertIn("en lugar de AgentOutput", resultado.error.mensaje)

    def test_fallo_controlado_preserva_codigo_y_mensaje_seguro(self):
        agente = AgenteConFalloControladoMock()
        ctx = AgentContext.crear(canal="web_lan", correlation_id="trace-controlado")
        entrada = AgentInput(contenido="Buscar producto", contexto=ctx)

        resultado = agente.ejecutar(entrada)

        self.assertFalse(resultado.exitoso)
        self.assertEqual(resultado.correlation_id, "trace-controlado")
        self.assertEqual(resultado.error.codigo, AgentErrorCode.RESOURCE_UNAVAILABLE)
        self.assertEqual(resultado.error.mensaje, "El producto solicitado no existe en el catálogo.")
        self.assertEqual(resultado.error.detalles["codigo_buscado"], "PROD-999")

    def test_excepcion_inesperada_es_capturada_y_sanitizada_sin_fugas(self):
        agente = AgenteConExcepcionInesperadaMock()
        ctx = AgentContext.crear(canal="web_lan", correlation_id="trace-crash")
        entrada = AgentInput(contenido="Operación rota", contexto=ctx)

        resultado = agente.ejecutar(entrada)

        self.assertFalse(resultado.exitoso)
        self.assertEqual(resultado.correlation_id, "trace-crash")
        self.assertEqual(resultado.error.codigo, AgentErrorCode.INTERNAL_ERROR)
        # El mensaje público NO debe filtrar el ZeroDivisionError ni rutas del sistema
        self.assertNotIn("ZeroDivisionError", resultado.error.mensaje)
        self.assertNotIn("division by zero", resultado.error.mensaje)
        self.assertIn("Error interno durante la ejecución del agente", resultado.error.mensaje)
        self.assertIsNone(resultado.error.detalles)

    def test_keyboard_interrupt_no_se_captura_y_se_propaga(self):
        """Demuestra que KeyboardInterrupt (BaseException) no es capturada como AgentResult.fallo."""
        agente = AgenteConKeyboardInterruptMock()
        ctx = AgentContext.crear(canal="web_lan", correlation_id="trace-sigint")
        entrada = AgentInput(contenido="Probar interrupción", contexto=ctx)

        with self.assertRaises(KeyboardInterrupt):
            agente.ejecutar(entrada)

    def test_system_exit_no_se_captura_y_se_propaga(self):
        """Demuestra que SystemExit (BaseException) no es capturada como AgentResult.fallo."""
        agente = AgenteConSystemExitMock()
        ctx = AgentContext.crear(canal="web_lan", correlation_id="trace-sysexit")
        entrada = AgentInput(contenido="Probar salida", contexto=ctx)

        with self.assertRaises(SystemExit):
            agente.ejecutar(entrada)



class TestAgentContractValidations(unittest.TestCase):
    """Pruebas de las reglas de validación previas del contrato."""

    def setUp(self):
        self.agente = AgenteConsultaMock()

    def test_rechazo_canal_no_permitido(self):
        # El agente solo permite 'web_lan' y 'rest_api'
        ctx = AgentContext.crear(canal="canal_telegram_no_autorizado", usuario_id="usr_01", correlation_id="tr-canal")
        entrada = AgentInput(contenido="Hola", contexto=ctx)

        resultado = self.agente.ejecutar(entrada)

        self.assertFalse(resultado.exitoso)
        self.assertEqual(resultado.correlation_id, "tr-canal")
        self.assertEqual(resultado.error.codigo, AgentErrorCode.CHANNEL_NOT_ALLOWED)
        self.assertIn("no está autorizado", resultado.error.mensaje)

    def test_rechazo_falta_de_autenticacion(self):
        # AgenteConsultaMock requiere autenticación (usuario_id no nulo ni vacío)
        ctx = AgentContext.crear(canal="web_lan", usuario_id=None, correlation_id="tr-auth")
        entrada = AgentInput(contenido="Consulta sin login", contexto=ctx)

        resultado = self.agente.ejecutar(entrada)

        self.assertFalse(resultado.exitoso)
        self.assertEqual(resultado.correlation_id, "tr-auth")
        self.assertEqual(resultado.error.codigo, AgentErrorCode.UNAUTHORIZED)
        self.assertIn("requiere una sesión con usuario autenticado", resultado.error.mensaje)

    def test_rechazo_intencion_no_declarada(self):
        # Capacidades del agente: ('consultar_stock', 'listar_almacenes')
        ctx = AgentContext.crear(canal="web_lan", usuario_id="usr_01", correlation_id="tr-intent")
        entrada = AgentInput(
            contenido="Eliminar bodega",
            contexto=ctx,
            intencion="eliminar_bodega_prohibida",
        )

        resultado = self.agente.ejecutar(entrada)

        self.assertFalse(resultado.exitoso)
        self.assertEqual(resultado.correlation_id, "tr-intent")
        self.assertEqual(resultado.error.codigo, AgentErrorCode.INTENT_NOT_SUPPORTED)
        self.assertIn("no está entre las capacidades declaradas", resultado.error.mensaje)

    def test_rechazo_contenido_vacio(self):
        ctx = AgentContext.crear(canal="web_lan", usuario_id="usr_01", correlation_id="tr-empty")
        entrada = AgentInput(contenido="   ", contexto=ctx)

        resultado = self.agente.ejecutar(entrada)

        self.assertFalse(resultado.exitoso)
        self.assertEqual(resultado.correlation_id, "tr-empty")
        self.assertEqual(resultado.error.codigo, AgentErrorCode.INVALID_INPUT)
        self.assertIn("no puede estar vacío", resultado.error.mensaje)


class TestPolimorfismoYSustitucionDeAgentes(unittest.TestCase):
    """Comprueba que consumidores genéricos puedan operar con cualquier agente compatible."""

    def test_consumidor_generico_interopera_con_multiples_agentes(self):
        def invocar_agente_generico(agente: AgentContract, texto: str, usuario: str) -> AgentResult:
            ctx = AgentContext.crear(canal="web_lan", usuario_id=usuario)
            inp = AgentInput(contenido=texto, contexto=ctx)
            return agente.ejecutar(inp)

        agente_a = AgenteConsultaMock(stock_simulado=15)
        agente_b = AgentePropuestaMock()

        res_a = invocar_agente_generico(agente_a, "Stock", "usuario_1")
        res_b = invocar_agente_generico(agente_b, "Mover", "usuario_2")

        self.assertTrue(res_a.exitoso)
        self.assertTrue(res_b.exitoso)
        self.assertIn("15 unidades", res_a.salida.respuesta)
        self.assertIsNotNone(res_b.salida.propuesta)


if __name__ == "__main__":
    unittest.main()
