"""Pruebas de verificación y consolidación para la Fase 1 de HAKU Inventario.

Valida:
1. Secuencia conversacional obligatoria:
   - "Consulta la Ponchadora RJ45" -> Consulta producto.
   - "¿Qué almacenes tenemos?" -> Lista almacenes, sin quedar atrapado en ponchadora.
   - "Consulta la Ponchadora RJ45" -> Vuelve a consultar ponchadora.
   - "¿Qué productos hay?" -> Catálogo general, sin quedar atrapado en ponchadora.
   - "¿Cuántas entradas han habido hoy?" -> Métricas de movimientos; no pide producto para registrar entrada.
2. Normalización fonética y técnica:
   - "rejota 45" y "re jota 45" -> RJ45.
   - "estoco" y "estock" -> stock.
   - "barro stop" y "bajo stop" -> bajo stock.
   - Variantes de almacén/bodega ("almasén", "volega", "armazen").
3. Desambiguación de consultas estadísticas vs operaciones:
   - "¿Cuántas entradas hubo hoy?" -> consulta estadística.
   - "¿Cuántas salidas se hicieron hoy?" -> consulta estadística.
   - "Registra una entrada de cinco unidades" -> operación (aclaración si falta producto).
   - "Saca dos ponchadoras" -> propuesta transaccional que requiere confirmación.
4. Nuevas intenciones globales y permisos:
   - LISTAR_CATALOGO (con filtro de almacén asignado para usuarios normales).
   - PRODUCTOS_CON_STOCK (solo productos con existencias disponibles > 0).
   - ALERTAS_STOCK (productos agotados o con existencias <= stock_minimo).
   - LISTAR_ALMACENES.
5. Aislamiento estricto multiusuario y manejo de fallbacks sin renovación ciega de foco.
"""

from __future__ import annotations

import time
from decimal import Decimal
from django.test import TestCase

from cuentas.models import UsuarioAudacIA
from inventario.models import Almacen, Categoria, Existencia, Movimiento, Producto, Ubicacion

from .cache import HakuCacheManager
from .contexto import EstadoOperativoHaku, HakuOperationalContext
from .lenguaje.normalizador import IntencionResultado, IntencionTipo, NormalizadorLenguajeHaku
from .services import ejecutar_confirmacion_propuesta, procesar_comando_inventario


class HakuFase1ConsolidacionTestCase(TestCase):
    """Batería de pruebas formales para la Fase 1."""

    def setUp(self):
        HakuOperationalContext.limpiar_todo()
        HakuCacheManager.invalidar_todo()

        # Almacenes
        self.almacen_principal = Almacen.objects.create(nombre="Almacén Principal", activo=True)
        self.almacen_norte = Almacen.objects.create(nombre="Bodega Norte", activo=True)

        # Categorías
        self.cat_herramientas = Categoria.objects.create(
            nombre="Herramientas de Red",
            almacen_principal=self.almacen_principal,
        )
        self.cat_cables = Categoria.objects.create(
            nombre="Cables y Accesorios",
            almacen_principal=self.almacen_norte,
        )

        # Ubicaciones
        self.ubi_herramientas = Ubicacion.objects.create(
            nombre="Pasillo 1 - Estante H1",
            almacen=self.almacen_principal,
        )
        self.ubi_cables = Ubicacion.objects.create(
            nombre="Pasillo 2 - Estante C2",
            almacen=self.almacen_norte,
        )

        # Productos
        self.prod_ponchadora = Producto.objects.create(
            codigo_interno="HER-0001",
            nombre="Ponchadora RJ45",
            descripcion="Herramienta para ponchar conectores RJ45 y RJ11",
            categoria=self.cat_herramientas,
            stock_inicial=8,
            stock_minimo=3,
        )
        self.prod_ponchadora.ubicaciones.add(self.ubi_herramientas)
        self.exist_ponchadora = Existencia.objects.create(
            producto=self.prod_ponchadora,
            almacen=self.almacen_principal,
            stock_disponible=8,
            stock_en_prestamo=0,
        )

        self.prod_hdmi = Producto.objects.create(
            codigo_interno="CAB-0001",
            nombre="Cable HDMI 2 metros",
            descripcion="Cable de video alta velocidad",
            categoria=self.cat_cables,
            stock_inicial=15,
            stock_minimo=5,
        )
        self.prod_hdmi.ubicaciones.add(self.ubi_cables)
        self.exist_hdmi = Existencia.objects.create(
            producto=self.prod_hdmi,
            almacen=self.almacen_norte,
            stock_disponible=15,
            stock_en_prestamo=0,
        )

        # Producto con stock agotado para pruebas de disponibilidad y alerta
        self.prod_agotado = Producto.objects.create(
            codigo_interno="CAB-0002",
            nombre="Cable VGA 5 metros",
            categoria=self.cat_cables,
            stock_inicial=0,
            stock_minimo=2,
        )
        self.prod_agotado.ubicaciones.add(self.ubi_cables)
        self.exist_agotado = Existencia.objects.create(
            producto=self.prod_agotado,
            almacen=self.almacen_norte,
            stock_disponible=0,
            stock_en_prestamo=0,
        )

        # Usuarios
        self.admin_user = UsuarioAudacIA.objects.create_user(
            username="admin_haku",
            password="Password123",
            nombre_completo="Administrador de Inventario",
            rol=UsuarioAudacIA.Rol.ADMINISTRADOR,
        )
        self.operador_user = UsuarioAudacIA.objects.create_user(
            username="operador_norte",
            password="Password123",
            nombre_completo="Operador Bodega Norte",
            rol=UsuarioAudacIA.Rol.USUARIO,
            almacen_asignado=self.almacen_norte,
        )

    def tearDown(self):
        HakuOperationalContext.limpiar_todo()
        HakuCacheManager.invalidar_todo()

    # ─────────────────────────────────────────────────────────────────────────
    # 1. Secuencia obligatoria de auditoría
    # ─────────────────────────────────────────────────────────────────────────
    def test_secuencia_conversacional_obligatoria(self):
        """Valida que la conversación no quede secuestrada por el producto en contexto."""
        session_key = "session_fase1_secuencia"
        ctx = HakuOperationalContext.obtener_o_crear(
            usuario_id=self.admin_user.id,
            session_key=session_key,
        )

        # Turno 1: «Consulta la Ponchadora RJ45» -> Datos de la ponchadora
        r1 = procesar_comando_inventario(
            usuario=self.admin_user,
            mensaje="Consulta la Ponchadora RJ45",
            estado_operativo=ctx,
        )
        self.assertEqual(r1["tipo"], "consulta")
        self.assertIn("Ponchadora RJ45", r1["mensaje_pantalla"])
        self.assertEqual(ctx.producto_id, self.prod_ponchadora.id)

        # Turno 2: «¿Qué almacenes tenemos?» -> Lista almacenes, NO la ponchadora
        r2 = procesar_comando_inventario(
            usuario=self.admin_user,
            mensaje="¿Qué almacenes tenemos?",
            estado_operativo=ctx,
        )
        self.assertEqual(r2["tipo"], "consulta")
        self.assertIn("Almacén Principal", r2["mensaje_pantalla"])
        self.assertIn("Bodega Norte", r2["mensaje_pantalla"])
        self.assertNotIn("Ponchadora", r2["mensaje_pantalla"])

        # Turno 3: «Consulta la Ponchadora RJ45» -> Vuelve a consultar la ponchadora
        r3 = procesar_comando_inventario(
            usuario=self.admin_user,
            mensaje="Consulta la Ponchadora RJ45",
            estado_operativo=ctx,
        )
        self.assertEqual(r3["tipo"], "consulta")
        self.assertIn("Ponchadora RJ45", r3["mensaje_pantalla"])

        # Turno 4: «¿Qué productos hay?» -> Catálogo general, sin quedar fijado en ponchadora
        r4 = procesar_comando_inventario(
            usuario=self.admin_user,
            mensaje="¿Qué productos hay?",
            estado_operativo=ctx,
        )
        self.assertEqual(r4["tipo"], "consulta")
        self.assertIn("Catálogo de productos", r4["mensaje_pantalla"])
        self.assertIn("Cable HDMI 2 metros", r4["mensaje_pantalla"])
        self.assertIn("Ponchadora RJ45", r4["mensaje_pantalla"])

        # Turno 5: «¿Cuántas entradas han habido hoy?» -> Consulta de métricas, no registrar entrada
        r5 = procesar_comando_inventario(
            usuario=self.admin_user,
            mensaje="¿Cuántas entradas han habido hoy?",
            estado_operativo=ctx,
        )
        self.assertEqual(r5["tipo"], "consulta")
        self.assertIn("entradas", r5["mensaje_pantalla"].lower())
        self.assertNotEqual(r5["tipo"], "aclaracion")

    # ─────────────────────────────────────────────────────────────────────────
    # 2. Normalización fonética y técnica
    # ─────────────────────────────────────────────────────────────────────────
    def test_normalizacion_siglas_rejota45(self):
        """Verifica que 'rejota 45' y 're jota 45' resuelvan a la Ponchadora RJ45."""
        res1 = NormalizadorLenguajeHaku.interpretar("consulta rejota 45", self.admin_user)
        self.assertEqual(res1.producto, self.prod_ponchadora)

        res2 = NormalizadorLenguajeHaku.interpretar("consulta la ponchadora re jota 45", self.admin_user)
        self.assertEqual(res2.producto, self.prod_ponchadora)

    def test_normalizacion_costeno_estoco_y_estock(self):
        """Verifica que 'estoco' y 'estock' se normalicen a 'stock'."""
        res1 = NormalizadorLenguajeHaku.interpretar("cuánto estoco queda de la ponchadora", self.admin_user)
        self.assertEqual(res1.tipo, IntencionTipo.CONSULTAR_STOCK)
        self.assertEqual(res1.producto, self.prod_ponchadora)

        res2 = NormalizadorLenguajeHaku.interpretar("cuál es el estock de la ponchadora", self.admin_user)
        self.assertEqual(res2.tipo, IntencionTipo.CONSULTAR_STOCK)
        self.assertEqual(res2.producto, self.prod_ponchadora)

    def test_normalizacion_costeno_barro_stop_y_bajo_stop(self):
        """Verifica que 'barro stop' y 'bajo stop' resuelvan a ALERTAS_STOCK."""
        res1 = NormalizadorLenguajeHaku.interpretar("qué productos tienen bajo stop", self.admin_user)
        self.assertEqual(res1.tipo, IntencionTipo.ALERTAS_STOCK)

        res2 = NormalizadorLenguajeHaku.interpretar("avísame lo que esté en barro stop", self.admin_user)
        self.assertEqual(res2.tipo, IntencionTipo.ALERTAS_STOCK)

    def test_normalizacion_almacenes_variantes(self):
        """Verifica que 'almasén', 'volega' y 'armazen' resuelvan a LISTAR_ALMACENES."""
        res1 = NormalizadorLenguajeHaku.interpretar("qué almasenes tenemos", self.admin_user)
        self.assertEqual(res1.tipo, IntencionTipo.LISTAR_ALMACENES)

        res2 = NormalizadorLenguajeHaku.interpretar("dime las volegas disponibles", self.admin_user)
        self.assertEqual(res2.tipo, IntencionTipo.LISTAR_ALMACENES)

    # ─────────────────────────────────────────────────────────────────────────
    # 3. Desambiguación de consultas estadísticas vs operaciones
    # ─────────────────────────────────────────────────────────────────────────
    def test_desambiguacion_metricas_movimientos(self):
        """Preguntas sobre movimientos históricos deben ser estadísticas, no mutaciones."""
        # Consultas de entradas / salidas históricas
        res_ent = NormalizadorLenguajeHaku.interpretar("¿cuántas entradas hubo hoy?", self.admin_user)
        self.assertEqual(res_ent.tipo, IntencionTipo.CONSULTAR_METRICAS_MOVIMIENTOS)
        self.assertFalse(res_ent.requiere_confirmacion)

        res_sal = NormalizadorLenguajeHaku.interpretar("¿cuántas salidas se hicieron hoy?", self.admin_user)
        self.assertEqual(res_sal.tipo, IntencionTipo.CONSULTAR_METRICAS_MOVIMIENTOS)
        self.assertFalse(res_sal.requiere_confirmacion)

        # Operaciones transaccionales reales
        res_op_ent = NormalizadorLenguajeHaku.interpretar("registra una entrada de cinco unidades", self.admin_user)
        # Sin producto en foco debe pedir aclaración, NO ser consulta estadística
        self.assertEqual(res_op_ent.tipo, IntencionTipo.SOLICITAR_ACLARACION)

        res_op_sal = NormalizadorLenguajeHaku.interpretar("saca dos ponchadoras", self.admin_user)
        self.assertEqual(res_op_sal.tipo, IntencionTipo.REGISTRAR_SALIDA)
        self.assertEqual(res_op_sal.producto, self.prod_ponchadora)
        self.assertEqual(res_op_sal.cantidad, 2)
        self.assertTrue(res_op_sal.requiere_confirmacion)

    # ─────────────────────────────────────────────────────────────────────────
    # 4. Nuevas intenciones globales y permisos
    # ─────────────────────────────────────────────────────────────────────────
    def test_catalogo_permisos_usuario_normal(self):
        """Un usuario normal con almacén asignado solo debe ver productos de su almacén."""
        r_admin = procesar_comando_inventario(
            usuario=self.admin_user,
            mensaje="¿Cuál es el listado de productos?",
        )
        self.assertIn("Ponchadora RJ45", r_admin["mensaje_pantalla"])
        self.assertIn("Cable HDMI 2 metros", r_admin["mensaje_pantalla"])

        r_operador = procesar_comando_inventario(
            usuario=self.operador_user,
            mensaje="¿Cuál es el listado de productos?",
        )
        # Operador asignado a Bodega Norte solo ve cables
        self.assertIn("Cable HDMI 2 metros", r_operador["mensaje_pantalla"])
        self.assertNotIn("Ponchadora RJ45", r_operador["mensaje_pantalla"])

    def test_productos_con_stock_disponible(self):
        """'productos con stock' debe excluir productos agotados con existencia 0."""
        r = procesar_comando_inventario(
            usuario=self.admin_user,
            mensaje="¿Qué productos hay en stock?",
        )
        self.assertEqual(r["tipo"], "consulta")
        self.assertIn("Cable HDMI 2 metros", r["mensaje_pantalla"])
        self.assertIn("Ponchadora RJ45", r["mensaje_pantalla"])
        self.assertNotIn("Cable VGA 5 metros", r["mensaje_pantalla"])

    def test_alertas_bajo_stock(self):
        """'productos agotados o con bajo stock' debe listar productos bajo mínimo o en 0."""
        r = procesar_comando_inventario(
            usuario=self.admin_user,
            mensaje="¿Qué productos tienen bajo stock?",
        )
        self.assertEqual(r["tipo"], "consulta")
        self.assertIn("Cable VGA 5 metros", r["mensaje_pantalla"])

    def test_listar_almacenes(self):
        """'qué almacenes tenemos' devuelve la lista formal de almacenes activos."""
        r = procesar_comando_inventario(
            usuario=self.admin_user,
            mensaje="¿Qué almacenes tenemos?",
        )
        self.assertEqual(r["tipo"], "consulta")
        self.assertIn("Almacén Principal", r["mensaje_pantalla"])
        self.assertIn("Bodega Norte", r["mensaje_pantalla"])

    # ─────────────────────────────────────────────────────────────────────────
    # 5. Aislamiento multiusuario y fallbacks sin renovación de foco
    # ─────────────────────────────────────────────────────────────────────────
    def test_aislamiento_contexto_dos_usuarios(self):
        """Dos usuarios interactuando simultáneamente no deben interferir en sus focos."""
        ctx_a = HakuOperationalContext.obtener_o_crear(self.admin_user.id, "session_user_a")
        ctx_b = HakuOperationalContext.obtener_o_crear(self.operador_user.id, "session_user_b")

        # Admin consulta ponchadora
        procesar_comando_inventario(self.admin_user, "consulta la ponchadora", estado_operativo=ctx_a)
        # Operador consulta cable HDMI
        procesar_comando_inventario(self.operador_user, "consulta el cable hdmi", estado_operativo=ctx_b)

        # Admin dice "¿cuántos quedan de ese?"
        r_a = procesar_comando_inventario(self.admin_user, "¿cuántos quedan de ese?", estado_operativo=ctx_a)
        self.assertIn("Ponchadora", r_a["mensaje_pantalla"])

        # Operador dice "¿cuántos quedan de ese?"
        r_b = procesar_comando_inventario(self.operador_user, "¿cuántos quedan de ese?", estado_operativo=ctx_b)
        self.assertIn("Cable HDMI", r_b["mensaje_pantalla"])

    def test_fallback_no_renueva_foco_perpetuamente(self):
        """Una entrada no entendida no debe restablecer el TTL de foco como si fuera actividad legítima."""
        ctx = HakuOperationalContext.obtener_o_crear(self.admin_user.id, "session_user_fallback")
        ctx.asignar_producto(self.prod_ponchadora.id, self.prod_ponchadora.codigo_interno, self.prod_ponchadora.nombre)
        t_original = ctx.ultimo_acceso

        # Simular pequeño paso de tiempo
        time.sleep(0.01)

        # Comando no comprendido / balbuceo
        r = procesar_comando_inventario(self.admin_user, "eh mm um", estado_operativo=ctx)
        self.assertEqual(r["tipo"], "no_comprendido")
        # El último acceso legítimo del foco no debe ser renovado
        self.assertEqual(ctx.ultimo_acceso, t_original)

    # ─────────────────────────────────────────────────────────────────────────
    # 6. Pruebas de aceptación adicionales (Flujo conversacional e integridad)
    # ─────────────────────────────────────────────────────────────────────────
    def test_distincion_stock_global_vs_individual(self):
        """Distingue semánticamente '¿qué productos hay en stock?' de '¿cuánto stock tiene la Ponchadora RJ45?'."""
        r_global = procesar_comando_inventario(self.admin_user, "¿qué productos hay en stock?")
        self.assertEqual(r_global["tipo"], "consulta")
        self.assertIn("Productos con existencias disponibles", r_global["mensaje_pantalla"])
        self.assertIn("Cable HDMI", r_global["mensaje_pantalla"])

        r_individual = procesar_comando_inventario(self.admin_user, "¿cuánto stock tiene la Ponchadora RJ45?")
        self.assertEqual(r_individual["tipo"], "consulta")
        self.assertIn("Ponchadora RJ45", r_individual["mensaje_pantalla"])
        self.assertIn("8 unidades", r_individual["mensaje_pantalla"])

    def test_referencias_contextuales_validas_y_ubicacion(self):
        """Consulta de producto seguida de '¿cuántos quedan de ese?' y '¿y su ubicación?'."""
        ctx = HakuOperationalContext.obtener_o_crear(self.admin_user.id, "session_ref_validas")
        # Turno 1: consulta producto
        r1 = procesar_comando_inventario(self.admin_user, "Consulta la Ponchadora RJ45", estado_operativo=ctx)
        self.assertEqual(ctx.producto_id, self.prod_ponchadora.id)

        # Turno 2: referencia demostrativa de stock
        r2 = procesar_comando_inventario(self.admin_user, "¿cuántos quedan de ese producto?", estado_operativo=ctx)
        self.assertIn("Ponchadora RJ45", r2["mensaje_pantalla"])
        self.assertIn("8 unidades", r2["mensaje_pantalla"])

        # Turno 3: referencia elíptica de ubicación
        r3 = procesar_comando_inventario(self.admin_user, "¿y su ubicación?", estado_operativo=ctx)
        self.assertIn("Pasillo 1 - Estante H1", r3["mensaje_pantalla"])

    def test_cambio_de_producto_y_cambio_de_tema(self):
        """Consulta producto A -> cambia a producto B -> cambia de tema a resumen de inventario."""
        ctx = HakuOperationalContext.obtener_o_crear(self.admin_user.id, "session_cambio_tema")
        # Producto A
        procesar_comando_inventario(self.admin_user, "consulta la ponchadora", estado_operativo=ctx)
        self.assertEqual(ctx.producto_id, self.prod_ponchadora.id)

        # Producto B
        procesar_comando_inventario(self.admin_user, "consulta el cable hdmi", estado_operativo=ctx)
        self.assertEqual(ctx.producto_id, self.prod_hdmi.id)

        # Cambio de tema: resumen general
        r_tema = procesar_comando_inventario(self.admin_user, "¿cómo va el inventario?", estado_operativo=ctx)
        self.assertEqual(r_tema["tipo"], "consulta")
        self.assertIn("resumen", r_tema["mensaje_pantalla"].lower())

    def test_usuario_estandar_sin_almacen_asignado(self):
        """Un usuario con rol USUARIO sin almacén asignado no debe recibir datos de otros almacenes."""
        user_sin_almacen = UsuarioAudacIA.objects.create_user(
            username="usuario_sin_almacen",
            password="Password123",
            nombre_completo="Usuario Huérfano",
            rol=UsuarioAudacIA.Rol.USUARIO,
            almacen_asignado=self.almacen_principal,
        )
        UsuarioAudacIA.objects.filter(pk=user_sin_almacen.pk).update(almacen_asignado=None)
        user_sin_almacen.refresh_from_db()

        r_cat = procesar_comando_inventario(user_sin_almacen, "¿cuál es el listado de productos?")
        self.assertIn("no tiene un almacén asignado", r_cat["mensaje_pantalla"])
        self.assertEqual(len(r_cat["datos"].get("productos", [])), 0)

        r_stock = procesar_comando_inventario(user_sin_almacen, "¿qué productos hay en stock?")
        self.assertIn("no tiene un almacén asignado", r_stock["mensaje_pantalla"])

        r_metr = procesar_comando_inventario(user_sin_almacen, "¿cuántas entradas han habido hoy?")
        self.assertIn("no tiene un almacén asignado", r_metr["mensaje_pantalla"])

    def test_confirmacion_y_cancelacion_propuesta_operativa(self):
        """Flujo completo de propuesta en 2 pasos: confirmación y cancelación explícita."""
        ctx = HakuOperationalContext.obtener_o_crear(self.admin_user.id, "session_propuesta_ciclo")

        # 1. Crear propuesta de salida con destinatario requerido
        r_prop = procesar_comando_inventario(self.admin_user, "saca 2 ponchadoras pa Pedro", estado_operativo=ctx)
        self.assertEqual(r_prop["tipo"], "propuesta")
        self.assertTrue(r_prop.get("requiere_confirmacion"))
        self.assertIsNotNone(ctx.propuesta_activa)

        # 2. Cancelar propuesta
        r_canc = procesar_comando_inventario(self.admin_user, "cancelar", estado_operativo=ctx)
        self.assertEqual(r_canc["tipo"], "accion_cancelar")
        self.assertIsNone(ctx.propuesta_activa)
        # Existencias no deben haber cambiado
        self.exist_ponchadora.refresh_from_db()
        self.assertEqual(self.exist_ponchadora.stock_disponible, 8)

        # 3. Crear nueva propuesta y confirmar
        procesar_comando_inventario(self.admin_user, "saca 2 ponchadoras pa Pedro", estado_operativo=ctx)
        self.assertIsNotNone(ctx.propuesta_activa)
        r_conf = procesar_comando_inventario(self.admin_user, "confirmar", estado_operativo=ctx)
        self.assertEqual(r_conf["tipo"], "accion_confirmar")
        # Ejecutar la transacción de confirmación atómica
        r_ejec = ejecutar_confirmacion_propuesta(usuario=self.admin_user, propuesta=ctx.propuesta_activa)
        self.assertTrue(r_ejec.get("exito"))
        ctx.propuesta_activa = None
        HakuOperationalContext.guardar(ctx)
        self.assertIsNone(ctx.propuesta_activa)
        # Existencias deben haberse descontado atómicamente
        self.exist_ponchadora.refresh_from_db()
        self.assertEqual(self.exist_ponchadora.stock_disponible, 6)

    def test_inmutabilidad_existencias_antes_de_confirmacion(self):
        """Una orden operativa no debe mutar existencias ni movimientos antes de confirmar."""
        stock_antes = self.exist_ponchadora.stock_disponible
        movs_antes = Movimiento.objects.count()

        # Emitir comando operativo
        r = procesar_comando_inventario(self.admin_user, "saca 3 ponchadoras pa Pedro")
        self.assertEqual(r["tipo"], "propuesta")

        # Comprobar inmutabilidad estricta
        self.exist_ponchadora.refresh_from_db()
        self.assertEqual(self.exist_ponchadora.stock_disponible, stock_antes)
        self.assertEqual(Movimiento.objects.count(), movs_antes)

    def test_propuesta_cancelada_no_puede_confirmarse(self):
        """Garantiza que turnos consecutivos 'cancelar' seguido de 'confirmar' no ejecuten transacciones."""
        ctx = HakuOperationalContext.obtener_o_crear(self.admin_user.id, "session_cancel_then_confirm")
        stock_inicial = self.exist_ponchadora.stock_disponible
        movs_inicial = Movimiento.objects.count()

        # Turno 1: Generar propuesta
        r_prop = procesar_comando_inventario(self.admin_user, "saca 2 ponchadoras pa Pedro", estado_operativo=ctx)
        self.assertEqual(r_prop["tipo"], "propuesta")
        self.assertIsNotNone(ctx.propuesta_activa)

        # Turno 2: Cancelar propuesta
        r_canc = procesar_comando_inventario(self.admin_user, "cancelar", estado_operativo=ctx)
        self.assertEqual(r_canc["tipo"], "accion_cancelar")
        self.assertIsNone(ctx.propuesta_activa)

        # Turno 3: Intentar confirmar inmediatamente tras la cancelación en la misma sesión
        r_conf = procesar_comando_inventario(self.admin_user, "confirmar", estado_operativo=ctx)
        self.assertEqual(r_conf["tipo"], "accion_confirmar")
        self.assertIn("No hay ninguna transacción pendiente de confirmación", r_conf["mensaje_pantalla"])
        self.assertNotIn("propuesta_id", r_conf)

        # Verificar invariantes de datos: ni existencias ni movimientos mutaron
        self.exist_ponchadora.refresh_from_db()
        self.assertEqual(self.exist_ponchadora.stock_disponible, stock_inicial)
        self.assertEqual(Movimiento.objects.count(), movs_inicial)

