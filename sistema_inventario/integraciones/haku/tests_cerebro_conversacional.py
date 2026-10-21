"""Pruebas rigurosas de la Fase de Mejora del Cerebro Conversacional de HAKU Inventario.

Valida:
1. Separación de intención, entidad y contexto:
   - Aislamiento por cambio de flujo (intenciones CRUD limpian contexto de producto).
   - Sustitución de entidad: producto explícito nuevo reemplaza al anterior y limpia candidatos.
   - Consultas globales (agotados, bajo stock) no heredan producto en foco.
2. Eliminación del fallback ciego:
   - Instrucciones incompletas o ambiguas retornan SOLICITUD_NO_COMPRENDIDA o SOLICITAR_ACLARACION,
     nunca caen a BUSCAR_PRODUCTO ciegamente.
3. Traslado entre almacenes (TRASLADAR_PRODUCTO / TRASLADO_ENTRE):
   - Generación de propuesta en dos pasos.
   - Validación estricta de stock disponible en origen y almacenes distintos.
   - Ejecución atómica: decremento en origen, incremento en destino, Movimiento y Auditoria inmutable.
4. Ciclo de vida y TTL de 30 minutos (1800s).
5. Métricas de diagnóstico en memoria (HakuDiagnostico).
"""

from __future__ import annotations

import time
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from cuentas.models import UsuarioAudacIA
from inventario.models import Almacen, Auditoria, Categoria, Existencia, Movimiento, Producto, Ubicacion
from integraciones.haku.contexto import HakuOperationalContext, TIMEOUT_INACTIVIDAD_SEGUNDOS
from integraciones.haku.diagnostico import HakuDiagnostico
from integraciones.haku.lenguaje.normalizador import IntencionResultado, IntencionTipo, NormalizadorLenguajeHaku
from integraciones.haku.services import (
    ejecutar_confirmacion_propuesta,
    procesar_comando_inventario,
)


class CerebroConversacionalHakuTestCase(TestCase):
    """Suite de pruebas para las mejoras del cerebro conversacional determinista de HAKU."""

    def setUp(self):
        User = get_user_model()
        self.almacen_a = Almacen.objects.create(nombre="Almacén Principal")
        self.almacen_b = Almacen.objects.create(nombre="Bodega Secundaria")
        self.categoria = Categoria.objects.create(nombre="Conectividad", almacen_principal=self.almacen_a)

        self.admin = User.objects.create_user(
            username="admin_haku",
            email="admin_haku@audacia.local",
            password="password123",
            nombre_completo="Administrador General",
            rol=UsuarioAudacIA.Rol.ADMINISTRADOR,
        )

        self.operario = User.objects.create_user(
            username="operario_haku",
            email="operario_haku@audacia.local",
            password="password123",
            nombre_completo="Operario Almacén A",
            rol=UsuarioAudacIA.Rol.USUARIO,
            almacen_asignado=self.almacen_a,
        )

        # Productos de prueba
        self.prod_hdmi = Producto.objects.create(
            codigo_interno="CAB-1001",
            nombre="Cable HDMI 2.0 3m",
            categoria=self.categoria,
            stock_minimo=5,
            activo=True,
        )
        self.prod_ponchadora = Producto.objects.create(
            codigo_interno="HER-2001",
            nombre="Ponchadora RJ45 Impacto",
            categoria=self.categoria,
            stock_minimo=2,
            activo=True,
        )

        # Existencias iniciales
        Existencia.objects.create(
            producto=self.prod_hdmi,
            almacen=self.almacen_a,
            stock_disponible=25,
            stock_en_prestamo=0,
        )
        Existencia.objects.create(
            producto=self.prod_hdmi,
            almacen=self.almacen_b,
            stock_disponible=0,
            stock_en_prestamo=0,
        )

        HakuOperationalContext.limpiar_todo()
        HakuDiagnostico.reset()

    def tearDown(self):
        HakuOperationalContext.limpiar_todo()
        HakuDiagnostico.reset()

    # ─────────────────────────────────────────────────────────────────────────
    # 1. AISLAMIENTO POR CAMBIO DE FLUJO (CRUD vs PRODUCTO EN FOCO)
    # ─────────────────────────────────────────────────────────────────────────

    def test_cambio_de_flujo_a_crud_usuario_limpia_foco_producto(self):
        """Si hay un producto en contexto y el usuario inicia un flujo CRUD de usuario,

        el foco del producto debe purgarse para no contaminar el formulario conversacional.
        """
        session_key = "sess_flujo_crud_1"
        contexto = HakuOperationalContext.obtener_o_crear(self.admin.id, session_key, self.almacen_a.id)
        contexto.asignar_producto(self.prod_hdmi.id, self.prod_hdmi.codigo_interno, self.prod_hdmi.nombre)
        contexto.asignar_candidatos([{"id": self.prod_hdmi.id, "nombre": self.prod_hdmi.nombre}])
        HakuOperationalContext.guardar(contexto)

        intencion = NormalizadorLenguajeHaku.interpretar(
            mensaje="Ahora quiero crear un nuevo usuario",
            usuario=self.admin,
            estado_operativo=contexto,
        )

        self.assertEqual(intencion.tipo, IntencionTipo.CREAR_USUARIO)
        # El contexto en memoria debió limpiarse del producto anterior
        self.assertIsNone(contexto.producto_id)
        self.assertIsNone(contexto.producto_nombre)
        self.assertEqual(contexto.candidatos, [])

    def test_cambio_de_flujo_a_crud_producto_limpia_foco_anterior(self):
        """Si el usuario solicita dar de alta un producto, se purga el foco previo."""
        session_key = "sess_flujo_crud_2"
        contexto = HakuOperationalContext.obtener_o_crear(self.admin.id, session_key, self.almacen_a.id)
        contexto.asignar_producto(self.prod_hdmi.id, self.prod_hdmi.codigo_interno, self.prod_hdmi.nombre)
        HakuOperationalContext.guardar(contexto)

        intencion = NormalizadorLenguajeHaku.interpretar(
            mensaje="Necesito registrar un nuevo producto en el catálogo",
            usuario=self.admin,
            estado_operativo=contexto,
        )

        self.assertEqual(intencion.tipo, IntencionTipo.CREAR_PRODUCTO)
        self.assertIsNone(contexto.producto_id)

    # ─────────────────────────────────────────────────────────────────────────
    # 2. SUSTITUCIÓN DE ENTIDAD EXPLÍCITA Y LIMPIEZA DE AMBIGÜEDAD PREVIA
    # ─────────────────────────────────────────────────────────────────────────

    def test_producto_explicito_nuevo_sustituye_foco_anterior_y_candidatos(self):
        """Mencionar un producto nuevo explícitamente debe reemplazar el foco anterior

        y limpiar cualquier lista de candidatos de desambiguación previa.
        """
        session_key = "sess_sustitucion_1"
        contexto = HakuOperationalContext.obtener_o_crear(self.operario.id, session_key, self.almacen_a.id)
        contexto.asignar_producto(self.prod_hdmi.id, self.prod_hdmi.codigo_interno, self.prod_hdmi.nombre)
        contexto.asignar_candidatos([
            {"id": self.prod_hdmi.id, "nombre": self.prod_hdmi.nombre},
            {"id": 999, "nombre": "Cable HDMI 5m"},
        ])
        HakuOperationalContext.guardar(contexto)

        intencion = NormalizadorLenguajeHaku.interpretar(
            mensaje="¿Cuánto stock queda de la ponchadora?",
            usuario=self.operario,
            estado_operativo=contexto,
        )

        self.assertEqual(intencion.tipo, IntencionTipo.CONSULTAR_STOCK)
        self.assertEqual(intencion.producto.id, self.prod_ponchadora.id)
        self.assertEqual(contexto.candidatos, [])

    # ─────────────────────────────────────────────────────────────────────────
    # 3. CONSULTAS GLOBALES NO HEREDAN PRODUCTO EN FOCO
    # ─────────────────────────────────────────────────────────────────────────

    def test_consulta_productos_sin_stock_no_hereda_producto_anterior(self):
        """«¿Cuáles están agotados?» con un producto previo en contexto debe ser

        una consulta global de stock cero (PRODUCTOS_SIN_STOCK), no consultar el producto anterior.
        """
        session_key = "sess_global_1"
        contexto = HakuOperationalContext.obtener_o_crear(self.operario.id, session_key, self.almacen_a.id)
        contexto.asignar_producto(self.prod_hdmi.id, self.prod_hdmi.codigo_interno, self.prod_hdmi.nombre)
        HakuOperationalContext.guardar(contexto)

        intencion = NormalizadorLenguajeHaku.interpretar(
            mensaje="¿Cuáles productos están agotados?",
            usuario=self.operario,
            estado_operativo=contexto,
        )

        self.assertEqual(intencion.tipo, IntencionTipo.PRODUCTOS_SIN_STOCK)
        self.assertIsNone(intencion.producto)

    def test_consulta_productos_bajo_stock_no_hereda_producto_anterior(self):
        """«¿Qué productos están por debajo del mínimo?» debe ser consulta global."""
        session_key = "sess_global_2"
        contexto = HakuOperationalContext.obtener_o_crear(self.operario.id, session_key, self.almacen_a.id)
        contexto.asignar_producto(self.prod_hdmi.id, self.prod_hdmi.codigo_interno, self.prod_hdmi.nombre)
        HakuOperationalContext.guardar(contexto)

        intencion = NormalizadorLenguajeHaku.interpretar(
            mensaje="¿Qué productos están por debajo del mínimo?",
            usuario=self.operario,
            estado_operativo=contexto,
        )

        self.assertEqual(intencion.tipo, IntencionTipo.PRODUCTOS_BAJO_STOCK)
        self.assertIsNone(intencion.producto)

    # ─────────────────────────────────────────────────────────────────────────
    # 4. ELIMINACIÓN DEL FALLBACK CIEGO
    # ─────────────────────────────────────────────────────────────────────────

    def test_instruccion_incompleta_retorna_solicitud_no_comprendida_sin_buscar_producto(self):
        """Un mensaje vago u operativo genérico como 'trasladar' o 'cambiar'

        no debe forzar ciegamente un BUSCAR_PRODUCTO con fallback arbitrario.
        """
        intencion = NormalizadorLenguajeHaku.interpretar(
            mensaje="trasladar",
            usuario=self.operario,
        )
        self.assertIn(
            intencion.tipo,
            [IntencionTipo.SOLICITUD_NO_COMPRENDIDA, IntencionTipo.SOLICITAR_ACLARACION],
        )
        self.assertNotEqual(intencion.tipo, IntencionTipo.BUSCAR_PRODUCTO)

    # ─────────────────────────────────────────────────────────────────────────
    # 5. TRASLADO ENTRE ALMACENES (PROPUESTA Y EJECUCIÓN ATÓMICA)
    # ─────────────────────────────────────────────────────────────────────────

    def test_traslado_completo_genera_propuesta(self):
        """'trasladar 5 unidades de cable HDMI hacia almacén Bodega Secundaria'

        debe reconocer TRASLADAR_PRODUCTO, producto, cantidad, almacén origen y destino.
        """
        intencion = NormalizadorLenguajeHaku.interpretar(
            mensaje="trasladar 5 unidades de cable HDMI hacia almacén Bodega Secundaria",
            usuario=self.admin,
        )
        self.assertEqual(intencion.tipo, IntencionTipo.TRASLADAR_PRODUCTO)
        self.assertEqual(intencion.producto.id, self.prod_hdmi.id)
        self.assertEqual(intencion.cantidad, 5)
        self.assertTrue(intencion.requiere_confirmacion)
        self.assertIsNotNone(intencion.datos_extra)
        self.assertEqual(intencion.datos_extra.get("almacen_destino_id"), self.almacen_b.id)

    def test_procesar_comando_genera_propuesta_traslado_con_stock_suficiente(self):
        """procesar_comando_inventario debe generar la propuesta transaccional para traslado."""
        session_key = "sess_traslado_propuesta"
        contexto = HakuOperationalContext.obtener_o_crear(self.admin.id, session_key, self.almacen_a.id)

        res = procesar_comando_inventario(
            usuario=self.admin,
            mensaje="trasladar 5 unidades de cable HDMI hacia almacén Bodega Secundaria",
            estado_operativo=contexto,
        )

        self.assertEqual(res["tipo"], "propuesta")
        self.assertTrue(res["requiere_confirmacion"])
        propuesta = res["propuesta"]
        self.assertEqual(propuesta["tipo_operacion"], Movimiento.Tipo.TRASLADO_ENTRE)
        self.assertEqual(propuesta["producto_id"], self.prod_hdmi.id)
        self.assertEqual(propuesta["cantidad"], 5)
        self.assertEqual(propuesta["almacen_origen_id"], self.almacen_a.id)
        self.assertEqual(propuesta["almacen_destino_id"], self.almacen_b.id)

    def test_traslado_rechazado_si_stock_insuficiente(self):
        """Si el stock en origen es menor a la cantidad solicitada para traslado,

        debe devolver un error sin generar propuesta.
        """
        session_key = "sess_traslado_sin_stock"
        contexto = HakuOperationalContext.obtener_o_crear(self.admin.id, session_key, self.almacen_a.id)

        res = procesar_comando_inventario(
            usuario=self.admin,
            mensaje="trasladar 100 unidades de cable HDMI hacia almacén Bodega Secundaria",
            estado_operativo=contexto,
        )

        self.assertEqual(res["tipo"], "error")
        self.assertIn("Stock insuficiente", res["mensaje_pantalla"])

    def test_ejecutar_confirmacion_propuesta_traslado_atomico_y_auditoria(self):
        """ejecutar_confirmacion_propuesta para TRASLADO_ENTRE debe:

        1. Decrementar origen (25 -> 20).
        2. Incrementar destino (0 -> 5).
        3. Crear Movimiento con almacenes origen y destino.
        4. Crear Auditoria con accion=TRASLADO_ENTRE y via_haku=True.
        """
        session_key = "sess_traslado_exec"
        contexto = HakuOperationalContext.obtener_o_crear(self.admin.id, session_key, self.almacen_a.id)

        res_prop = procesar_comando_inventario(
            usuario=self.admin,
            mensaje="trasladar 5 unidades de cable HDMI hacia almacén Bodega Secundaria",
            estado_operativo=contexto,
        )
        propuesta = res_prop["propuesta"]

        # Confirmar la propuesta
        confirmacion = ejecutar_confirmacion_propuesta(
            usuario=self.admin,
            propuesta=propuesta,
            ip_origen="127.0.0.1",
        )

        self.assertTrue(confirmacion["exito"])
        self.assertEqual(confirmacion["stock_nuevo"], 20)

        # Verificar existencias en base de datos
        ex_origen = Existencia.objects.get(producto=self.prod_hdmi, almacen=self.almacen_a)
        ex_destino = Existencia.objects.get(producto=self.prod_hdmi, almacen=self.almacen_b)
        self.assertEqual(ex_origen.stock_disponible, 20)
        self.assertEqual(ex_destino.stock_disponible, 5)

        # Verificar Movimiento
        mov = Movimiento.objects.get(id=confirmacion["movimiento_id"])
        self.assertEqual(mov.tipo, Movimiento.Tipo.TRASLADO_ENTRE)
        self.assertEqual(mov.cantidad, 5)
        self.assertEqual(mov.almacen_origen, self.almacen_a)
        self.assertEqual(mov.almacen_destino, self.almacen_b)

        # Verificar Auditoría
        audit = Auditoria.objects.filter(accion="TRASLADO_ENTRE", id_entidad=str(self.prod_hdmi.id)).first()
        self.assertIsNotNone(audit)
        self.assertTrue(audit.via_haku)
        self.assertEqual(audit.valores_anteriores["stock_origen"], 25)
        self.assertEqual(audit.valores_nuevos["stock_origen"], 20)
        self.assertEqual(audit.valores_anteriores["stock_destino"], 0)
        self.assertEqual(audit.valores_nuevos["stock_destino"], 5)

    # ─────────────────────────────────────────────────────────────────────────
    # 6. MÉTRICAS DE DIAGNÓSTICO EN MEMORIA (HakuDiagnostico)
    # ─────────────────────────────────────────────────────────────────────────

    def test_diagnostico_registra_intencion_latencia_y_propuestas(self):
        """HakuDiagnostico debe registrar métricas en memoria sin persistencia en BD."""
        HakuDiagnostico.reset()

        # Procesar un comando real
        procesar_comando_inventario(
            usuario=self.operario,
            mensaje="¿Cuánto stock queda de cable HDMI?",
        )

        metricas = HakuDiagnostico.obtener_metricas()
        self.assertGreaterEqual(metricas["total_intenciones"], 1)
        self.assertIn("CONSULTAR_STOCK", metricas["distribucion_intenciones"])
        self.assertGreater(metricas["latencia_promedio_ms"], 0)
        self.assertEqual(metricas["tasa_fallback_pct"], 0.0)

    # ─────────────────────────────────────────────────────────────────────────
    # 7. TTL CONVERSACIONAL DE 30 MINUTOS (1800s)
    # ─────────────────────────────────────────────────────────────────────────

    def test_constante_ttl_es_1800_segundos(self):
        """Verifica que el TTL conversacional está configurado estrictamente en 1800s (30 min)."""
        self.assertEqual(TIMEOUT_INACTIVIDAD_SEGUNDOS, 1800)
        self.assertEqual(NormalizadorLenguajeHaku.TIMEOUT_CONTEXTO_SEGUNDOS, 1800)
