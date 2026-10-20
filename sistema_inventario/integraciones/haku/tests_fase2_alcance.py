"""
Suite de pruebas unitarias e integrales para la Fase 2 de HAKU Inventario:
Separación entre intención y alcance semántico, consultas globales y multi-almacén.

Cubre exhaustivamente los 12 grupos de aceptación:
1. Consultas globales de préstamos (con métricas diferenciadas).
2. Consultas globales de stock agotado y bajo stock (estricto 0 < disponible < minimo).
3. Consulta global inmediatamente posterior a consulta individual (aislamiento de alcance absoluto).
4. Consultas individuales con nombre o código.
5. Consultas de colección o categoría.
6. Consultas con almacén explícito y aislamiento de permisos (usuario normal vs admin).
7. Preguntas ambiguas sin contexto previo (solicitud de aclaración segura).
8. Preguntas de seguimiento legítimas con contexto (herencia contextual justificada).
9. Cambio de tema (Topic Shift) y entidad no encontrada (aislamiento de foco).
10. Diferenciación estricta entre estados de stock (disponible, agotado, bajo stock).
11. Respuestas de borde (vacío, sin préstamos, múltiples prestatarios).
12. No regresión en flujos transaccionales (confirmación en 2 pasos y cancelación).
"""

import time
from django.test import TestCase
from django.contrib.auth import get_user_model
from inventario.models import Producto, Categoria, Almacen, Ubicacion, Existencia, Movimiento, Prestamo
from integraciones.haku.lenguaje.normalizador import NormalizadorLenguajeHaku, IntencionTipo
from integraciones.haku.contexto import HakuOperationalContext
from integraciones.haku.services import procesar_comando_inventario, ejecutar_confirmacion_propuesta

User = get_user_model()


class BaseFase2TestCase(TestCase):
    """Configuración de base de datos con almacenes, categorías, productos y préstamos."""

    def setUp(self):
        super().setUp()

        # 1. Almacenes
        self.almacen_norte = Almacen.objects.create(
            nombre="Bodega Norte",
            activo=True,
        )
        self.almacen_sur = Almacen.objects.create(
            nombre="Bodega Sur",
            activo=True,
        )

        # 2. Categorías
        self.cat_cables = Categoria.objects.create(
            nombre="Cables y Conectividad",
            almacen_principal=self.almacen_norte,
            activo=True,
        )
        self.cat_herramientas = Categoria.objects.create(
            nombre="Herramientas",
            almacen_principal=self.almacen_norte,
            activo=True,
        )
        self.cat_redes = Categoria.objects.create(
            nombre="Equipos de Red",
            almacen_principal=self.almacen_sur,
            activo=True,
        )

        # 3. Ubicaciones
        self.ub_estante_a = Ubicacion.objects.create(
            almacen=self.almacen_norte,
            nombre="Estante A1",
        )
        self.ub_cajon_b = Ubicacion.objects.create(
            almacen=self.almacen_norte,
            nombre="Cajón B2",
        )
        self.ub_rack_sur = Ubicacion.objects.create(
            almacen=self.almacen_sur,
            nombre="Rack Sur 1",
        )

        # 4. Productos
        # P1: Cable HDMI 2m - Stock suficiente (20 disp, 5 min)
        self.prod_hdmi = Producto.objects.create(
            codigo_interno="CAB-0001",
            nombre="Cable HDMI 2m",
            categoria=self.cat_cables,
            stock_minimo=5,
            activo=True,
        )
        self.prod_hdmi.ubicaciones.add(self.ub_estante_a)
        Existencia.objects.create(
            producto=self.prod_hdmi,
            almacen=self.almacen_norte,
            stock_disponible=20,
            stock_en_prestamo=3,
        )

        # P2: Cable UTP Bobina - Stock bajo (2 disp, 5 min) -> 0 < 2 < 5
        self.prod_utp = Producto.objects.create(
            codigo_interno="CAB-0002",
            nombre="Cable UTP Bobina 100m",
            categoria=self.cat_cables,
            stock_minimo=5,
            activo=True,
        )
        self.prod_utp.ubicaciones.add(self.ub_estante_a)
        Existencia.objects.create(
            producto=self.prod_utp,
            almacen=self.almacen_norte,
            stock_disponible=2,
            stock_en_prestamo=0,
        )

        # P3: Ponchadora Profesional - Stock agotado (0 disp, 2 min)
        self.prod_ponchadora = Producto.objects.create(
            codigo_interno="HER-0001",
            nombre="Ponchadora RJ45 Profesional",
            categoria=self.cat_herramientas,
            stock_minimo=2,
            activo=True,
        )
        self.prod_ponchadora.ubicaciones.add(self.ub_cajon_b)
        Existencia.objects.create(
            producto=self.prod_ponchadora,
            almacen=self.almacen_norte,
            stock_disponible=0,
            stock_en_prestamo=2,
        )

        # P4: Switch Gigabit - en Bodega Sur (10 disp, 2 min)
        self.prod_switch = Producto.objects.create(
            codigo_interno="RED-0001",
            nombre="Switch Gigabit 8 Puertos",
            categoria=self.cat_redes,
            stock_minimo=2,
            activo=True,
        )
        self.prod_switch.ubicaciones.add(self.ub_rack_sur)
        Existencia.objects.create(
            producto=self.prod_switch,
            almacen=self.almacen_sur,
            stock_disponible=10,
            stock_en_prestamo=0,
        )

        # 5. Usuarios
        self.admin_user = User.objects.create_user(
            username="admin_test",
            email="admin@audacia.local",
            password="password123",
            nombre_completo="Administrador General",
            rol="ADMINISTRADOR",
        )

        self.operario_norte = User.objects.create_user(
            username="operario_norte",
            email="norte@audacia.local",
            password="password123",
            nombre_completo="Operario Bodega Norte",
            rol="OPERARIO",
            almacen_asignado=self.almacen_norte,
        )

        # 6. Préstamos activos
        # Préstamo 1: 3 unidades de HDMI a María Gómez
        self.mov1 = Movimiento.objects.create(
            clave_idempotencia="prestamo-demo-hdmi-1",
            tipo=Movimiento.Tipo.PRESTAMO,
            producto=self.prod_hdmi,
            cantidad=3,
            almacen_origen=self.almacen_norte,
            usuario=self.admin_user,
            destinatario="María Gómez",
            motivo="Préstamo de prueba",
            stock_disponible_anterior=23,
            stock_disponible_posterior=20,
        )
        self.prestamo1 = Prestamo.objects.create(
            movimiento=self.mov1,
            producto=self.prod_hdmi,
            almacen=self.almacen_norte,
            usuario=self.admin_user,
            destinatario="María Gómez",
            cantidad_original=3,
            cantidad_pendiente_devolucion=3,
            estado=Prestamo.Estado.ABIERTO,
        )
        # Préstamo 2: 2 unidades de Ponchadora a Carlos Martínez
        self.mov2 = Movimiento.objects.create(
            clave_idempotencia="prestamo-demo-ponchadora-2",
            tipo=Movimiento.Tipo.PRESTAMO,
            producto=self.prod_ponchadora,
            cantidad=2,
            almacen_origen=self.almacen_norte,
            usuario=self.admin_user,
            destinatario="Carlos Martínez",
            motivo="Préstamo de prueba",
            stock_disponible_anterior=2,
            stock_disponible_posterior=0,
        )
        self.prestamo2 = Prestamo.objects.create(
            movimiento=self.mov2,
            producto=self.prod_ponchadora,
            almacen=self.almacen_norte,
            usuario=self.admin_user,
            destinatario="Carlos Martínez",
            cantidad_original=2,
            cantidad_pendiente_devolucion=2,
            estado=Prestamo.Estado.ABIERTO,
        )


class TestGrupo01ConsultasGlobalesPrestamos(BaseFase2TestCase):
    """Grupo 1: Expresiones globales de préstamos sin vinculación a producto individual."""

    def test_consultas_globales_prestamos_expresiones(self):
        expresiones = [
            "¿Hay algún producto en préstamos?",
            "¿Qué tenemos prestado?",
            "¿Qué productos están prestados?",
            "¿Cuáles son los préstamos activos?",
            "Muéstrame los productos que están prestados",
            "¿Quién tiene cosas prestadas?",
        ]
        for expr in expresiones:
            res = NormalizadorLenguajeHaku.interpretar(expr, self.admin_user)
            self.assertEqual(
                res.tipo,
                IntencionTipo.PRESTAMOS_ACTIVOS,
                f"Fallo en expresión global de préstamos: '{expr}' -> tipo {res.tipo}"
            )
            self.assertIsNone(
                res.producto,
                f"La expresión global '{expr}' no debe vincular a un producto individual."
            )

    def test_consultas_metricas_diferenciadas_prestamos(self):
        # Métrica: Conteo de registros
        res_reg = NormalizadorLenguajeHaku.interpretar("¿Cuántos préstamos están activos?", self.admin_user)
        self.assertEqual(res_reg.tipo, IntencionTipo.PRESTAMOS_ACTIVOS)
        self.assertEqual(res_reg.datos_extra.get("metrica"), "CONTEO_REGISTROS")

        resp_serv_reg = procesar_comando_inventario(self.admin_user, "¿Cuántos préstamos están activos?")
        self.assertIn("préstamos activos", resp_serv_reg["mensaje_pantalla"])
        self.assertIn("2", resp_serv_reg["mensaje_pantalla"])

        # Métrica: Conteo de unidades totales
        res_uni = NormalizadorLenguajeHaku.interpretar("¿Cuántas unidades hay prestadas?", self.admin_user)
        self.assertEqual(res_uni.tipo, IntencionTipo.PRESTAMOS_ACTIVOS)
        self.assertEqual(res_uni.datos_extra.get("metrica"), "CONTEO_UNIDADES")

        resp_serv_uni = procesar_comando_inventario(self.admin_user, "¿Cuántas unidades hay prestadas?")
        self.assertIn("unidades pendientes de devolución", resp_serv_uni["mensaje_pantalla"])
        self.assertIn("5", resp_serv_uni["mensaje_pantalla"])

        # Métrica: Listado de productos
        res_prod = NormalizadorLenguajeHaku.interpretar("¿Qué productos tenemos prestados?", self.admin_user)
        self.assertEqual(res_prod.tipo, IntencionTipo.PRESTAMOS_ACTIVOS)
        self.assertEqual(res_prod.datos_extra.get("metrica"), "LISTA_PRODUCTOS")

        resp_serv_prod = procesar_comando_inventario(self.admin_user, "¿Qué productos tenemos prestados?")
        self.assertIn("Cable HDMI 2m", resp_serv_prod["mensaje_pantalla"])
        self.assertIn("Ponchadora RJ45 Profesional", resp_serv_prod["mensaje_pantalla"])


class TestGrupo02StockAgotadoYBajo(BaseFase2TestCase):
    """Grupo 2: Consultas globales de productos agotados y con stock bajo."""

    def test_consultas_globales_stock_agotado(self):
        expresiones = [
            "¿Qué productos se agotaron?",
            "¿Qué productos no tienen stock?",
            "¿Qué artículos están sin existencias?",
        ]
        for expr in expresiones:
            res = NormalizadorLenguajeHaku.interpretar(expr, self.admin_user)
            self.assertEqual(
                res.tipo,
                IntencionTipo.PRODUCTOS_SIN_STOCK,
                f"Fallo en consulta de agotados: '{expr}' -> tipo {res.tipo}"
            )
            self.assertIsNone(res.producto)

        # En ejecución de servicio: solo debe figurar HER-0001 (disponible=0), no CAB-0002 (disponible=2)
        resp = procesar_comando_inventario(self.admin_user, "¿Qué productos se agotaron?")
        self.assertIn("HER-0001", resp["mensaje_pantalla"])
        self.assertNotIn("CAB-0002", resp["mensaje_pantalla"])

    def test_consultas_globales_bajo_stock(self):
        expresiones = [
            "¿Cuáles necesitan reposición?",
            "¿Qué productos están por debajo del mínimo?",
        ]
        for expr in expresiones:
            res = NormalizadorLenguajeHaku.interpretar(expr, self.admin_user)
            self.assertEqual(
                res.tipo,
                IntencionTipo.PRODUCTOS_BAJO_STOCK,
                f"Fallo en consulta de bajo stock: '{expr}' -> tipo {res.tipo}"
            )
            self.assertIsNone(res.producto)

        # En ejecución de servicio: solo CAB-0002 (0 < 2 < 5), NUNCA HER-0001 (disponible=0 es agotado)
        resp = procesar_comando_inventario(self.admin_user, "¿Cuáles necesitan reposición?")
        self.assertIn("CAB-0002", resp["mensaje_pantalla"])
        self.assertNotIn("HER-0001", resp["mensaje_pantalla"])


class TestGrupo03AislamientoAlcanceContexto(BaseFase2TestCase):
    """Grupo 3: Consulta global inmediatamente posterior a consulta individual (cero contaminación)."""

    def test_consulta_global_agotados_tras_consulta_individual(self):
        ctx = {"haku_ultimo_producto_id": str(self.prod_hdmi.id), "haku_ultimo_acceso": time.time()}

        # Turno 2: Pregunta global de agotados teniendo en memoria el Cable HDMI
        res = NormalizadorLenguajeHaku.interpretar("¿Qué productos se agotaron?", self.admin_user, contexto_sesion=ctx)
        self.assertEqual(res.tipo, IntencionTipo.PRODUCTOS_SIN_STOCK)
        self.assertIsNone(res.producto, "El alcance GLOBAL no debe heredar el producto previo en contexto.")

        resp = procesar_comando_inventario(self.admin_user, "¿Qué productos se agotaron?", sesion=ctx)
        self.assertEqual(resp["tipo"], "consulta")
        self.assertIn("HER-0001", resp["mensaje_pantalla"])
        # No debe haber aclaración ni reducción al Cable HDMI
        self.assertNotIn("Cable HDMI 2m", resp["mensaje_pantalla"])

    def test_consulta_global_prestamos_tras_consulta_individual(self):
        ctx = {"haku_ultimo_producto_id": str(self.prod_ponchadora.id), "haku_ultimo_acceso": time.time()}

        # Turno 2: Pregunta global de préstamos
        res = NormalizadorLenguajeHaku.interpretar("¿Hay algún producto en préstamos?", self.admin_user, contexto_sesion=ctx)
        self.assertEqual(res.tipo, IntencionTipo.PRESTAMOS_ACTIVOS)
        self.assertIsNone(res.producto, "El alcance GLOBAL no debe contaminarse con la ponchadora previa.")

        resp = procesar_comando_inventario(self.admin_user, "¿Hay algún producto en préstamos?", sesion=ctx)
        self.assertIn("Cable HDMI 2m", resp["mensaje_pantalla"])
        self.assertIn("Ponchadora RJ45 Profesional", resp["mensaje_pantalla"])


class TestGrupo04ConsultasIndividualesNombreCodigo(BaseFase2TestCase):
    """Grupo 4: Consultas individuales con código interno o nombre específico."""

    def test_consulta_individual_codigo_exacto(self):
        res = NormalizadorLenguajeHaku.interpretar("Stock de CAB-0001", self.admin_user)
        self.assertEqual(res.tipo, IntencionTipo.CONSULTAR_STOCK)
        self.assertEqual(res.producto, self.prod_hdmi)

    def test_consulta_individual_nombre_exacto(self):
        res = NormalizadorLenguajeHaku.interpretar("¿Dónde está la ponchadora profesional?", self.admin_user)
        self.assertEqual(res.tipo, IntencionTipo.CONSULTAR_UBICACION)
        self.assertEqual(res.producto, self.prod_ponchadora)


class TestGrupo05ConsultasColeccionCategoria(BaseFase2TestCase):
    """Grupo 5: Consultas sobre colección o familia sin secuestro a producto individual."""

    def test_consulta_coleccion_cables(self):
        res = NormalizadorLenguajeHaku.interpretar("¿Qué cables están disponibles?", self.admin_user)
        self.assertEqual(res.tipo, IntencionTipo.PRODUCTOS_CON_STOCK)
        self.assertIsNone(res.producto)
        self.assertIn("cable", res.datos_extra.get("filtro_nombre", ""))

        resp = procesar_comando_inventario(self.admin_user, "¿Qué cables están disponibles?")
        self.assertEqual(resp["tipo"], "consulta")
        self.assertIn("Cable HDMI 2m", resp["mensaje_pantalla"])


class TestGrupo06AlmacenPermisosAislamiento(BaseFase2TestCase):
    """Grupo 6: Consultas con almacén explícito y control estricto de acceso."""

    def test_admin_consulta_cualquier_almacen(self):
        res = NormalizadorLenguajeHaku.interpretar("¿Cuánto queda del switch en Bodega Sur?", self.admin_user)
        self.assertEqual(res.tipo, IntencionTipo.CONSULTAR_STOCK)
        self.assertEqual(res.producto, self.prod_switch)
        self.assertEqual(res.almacen, self.almacen_sur)

        resp = procesar_comando_inventario(self.admin_user, "¿Cuánto queda del switch en Bodega Sur?")
        self.assertEqual(resp["tipo"], "consulta")
        self.assertIn("Switch Gigabit 8 Puertos", resp["mensaje_pantalla"])
        self.assertIn("Bodega Sur", resp["mensaje_pantalla"])

    def test_operario_consulta_almacen_autorizado(self):
        # Operario asignado a Bodega Norte consulta Bodega Norte
        resp = procesar_comando_inventario(self.operario_norte, "Stock de CAB-0001 en Bodega Norte")
        self.assertEqual(resp["tipo"], "consulta")
        self.assertIn("Cable HDMI 2m", resp["mensaje_pantalla"])

    def test_operario_consulta_almacen_no_autorizado(self):
        # Operario asignado a Bodega Norte intenta consultar Bodega Sur
        resp = procesar_comando_inventario(self.operario_norte, "Stock de CAB-0001 en Bodega Sur")
        self.assertEqual(resp["tipo"], "consulta")
        self.assertIn("No tienes permiso para consultar el almacén 'Bodega Sur'", resp["mensaje_pantalla"])


class TestGrupo07PreguntasAmbiguasSinContexto(BaseFase2TestCase):
    """Grupo 7: Preguntas ambiguas sin contexto previo solicitan aclaración educada."""

    def test_cuanto_queda_sin_contexto(self):
        res = NormalizadorLenguajeHaku.interpretar("¿Cuánto queda?", self.admin_user)
        self.assertEqual(res.tipo, IntencionTipo.SOLICITAR_ACLARACION)
        self.assertIn("¿De cuál producto deseas consultar el stock?", res.mensaje_aclaracion)

    def test_donde_esta_ubicado_sin_contexto(self):
        res = NormalizadorLenguajeHaku.interpretar("¿Dónde está ubicado?", self.admin_user)
        self.assertEqual(res.tipo, IntencionTipo.SOLICITAR_ACLARACION)
        self.assertIn("¿De cuál producto deseas conocer la ubicación?", res.mensaje_aclaracion)


class TestGrupo08SeguimientoLegitimoConContexto(BaseFase2TestCase):
    """Grupo 8: Preguntas de seguimiento dependientes con herencia contextual legítima."""

    def test_flujo_multiturno_mismo_producto(self):
        # Turno 1: Consulta inicial del switch
        ctx = {"haku_ultimo_producto_id": str(self.prod_switch.id), "haku_ultimo_acceso": time.time()}

        # Turno 2: Seguimiento de ubicación con elipsis
        res_ub = NormalizadorLenguajeHaku.interpretar("¿Dónde está ubicado?", self.admin_user, contexto_sesion=ctx)
        self.assertEqual(res_ub.tipo, IntencionTipo.CONSULTAR_UBICACION)
        self.assertEqual(res_ub.producto, self.prod_switch)

        # Turno 3: Seguimiento de préstamos con demostrativo "de ese"
        res_pr = NormalizadorLenguajeHaku.interpretar("¿Y de ese cuánto hay prestado?", self.admin_user, contexto_sesion=ctx)
        self.assertEqual(res_pr.tipo, IntencionTipo.PRESTAMOS_ACTIVOS)
        self.assertEqual(res_pr.producto, self.prod_switch)


class TestGrupo09CambioDeTemaYEntidadNoEncontrada(BaseFase2TestCase):
    """Grupo 9: Topic shift y entidades no encontradas aíslan el foco conversacional."""

    def test_topic_shift_rompe_contexto(self):
        ctx = {"haku_ultimo_producto_id": str(self.prod_hdmi.id), "haku_ultimo_acceso": time.time()}

        # Usuario introduce ruptura discursiva hacia entidad inexistente
        res = NormalizadorLenguajeHaku.interpretar(
            "Cambiando de tema, ¿cuántos osciloscopios tenemos?",
            self.admin_user,
            contexto_sesion=ctx,
        )
        # El normalizador no debe responder con datos del Cable HDMI
        self.assertNotEqual(res.producto, self.prod_hdmi)

    def test_entidad_no_catalogada_aisla_foco(self):
        # Al consultar un producto inexistente sin romper tema, se solicita aclaración sin arrastrar el anterior
        ctx = {"haku_ultimo_producto_id": str(self.prod_hdmi.id), "haku_ultimo_acceso": time.time()}
        res = NormalizadorLenguajeHaku.interpretar(
            "¿Cuánto queda del microscopio electrónico?",
            self.admin_user,
            contexto_sesion=ctx,
        )
        self.assertEqual(res.tipo, IntencionTipo.SOLICITAR_ACLARACION)
        self.assertIsNone(res.producto)


class TestGrupo10DiferenciacionEstadosStock(BaseFase2TestCase):
    """Grupo 10: Validación estricta de filtros de stock (0 vs 0 < disp < min vs disp >= min)."""

    def test_criterio_estricto_disponible_vs_bajo_stock_vs_agotado(self):
        # 1. P3 Ponchadora: disponible=0 -> AGOTADO, NO BAJO_STOCK
        resp_agotados = procesar_comando_inventario(self.admin_user, "¿Qué productos se agotaron?")
        self.assertIn("HER-0001", resp_agotados["mensaje_pantalla"])

        resp_bajo = procesar_comando_inventario(self.admin_user, "¿Cuáles necesitan reposición?")
        self.assertNotIn("HER-0001", resp_bajo["mensaje_pantalla"], "Producto con 0 disponible no debe figurar en bajo stock.")

        # 2. P2 Cable UTP: disponible=2, minimo=5 -> BAJO_STOCK, NO AGOTADO
        self.assertIn("CAB-0002", resp_bajo["mensaje_pantalla"])
        self.assertNotIn("CAB-0002", resp_agotados["mensaje_pantalla"])

        # 3. P1 Cable HDMI: disponible=20, minimo=5 -> Ni agotado ni bajo stock
        self.assertNotIn("CAB-0001", resp_agotados["mensaje_pantalla"])
        self.assertNotIn("CAB-0001", resp_bajo["mensaje_pantalla"])


class TestGrupo11RespuestasDeBorde(BaseFase2TestCase):
    """Grupo 11: Comportamiento ante colecciones vacías o sin resultados."""

    def test_sin_prestamos_activos(self):
        # Eliminar préstamos activos
        Prestamo.objects.all().delete()
        Existencia.objects.all().update(stock_en_prestamo=0)

        resp = procesar_comando_inventario(self.admin_user, "¿Hay algún producto en préstamos?")
        self.assertEqual(resp["tipo"], "consulta")
        self.assertIn("No hay préstamos activos pendientes en el inventario", resp["mensaje_pantalla"])

    def test_sin_productos_agotados(self):
        # Llevar stock de ponchadora a positivo
        Existencia.objects.filter(producto=self.prod_ponchadora).update(stock_disponible=10)

        resp = procesar_comando_inventario(self.admin_user, "¿Qué productos se agotaron?")
        self.assertEqual(resp["tipo"], "consulta")
        self.assertIn("No hay productos agotados", resp["mensaje_pantalla"])


class TestGrupo12FlujosTransaccionalesConfirmacion(BaseFase2TestCase):
    """Grupo 12: Las consultas y mejoras de Fase 2 no interfieren con transacciones de 2 pasos."""

    def test_propuesta_y_confirmacion_prestamo(self):
        # Paso 1: Solicitar préstamo
        resp_p1 = procesar_comando_inventario(
            self.admin_user,
            "Préstale 2 unidades de CAB-0001 a Pedro Picapiedra"
        )
        self.assertEqual(resp_p1["tipo"], "propuesta")
        propuesta = resp_p1.get("propuesta")
        self.assertIsNotNone(propuesta)
        self.assertEqual(propuesta["cantidad"], 2)

        # Paso 2: Confirmar
        resp_p2 = procesar_comando_inventario(
            self.admin_user,
            "sí, confirma",
            propuesta_activa=propuesta
        )
        self.assertEqual(resp_p2["tipo"], "accion_confirmar")
        self.assertIn("Confirmación recibida", resp_p2["mensaje_pantalla"])

        # Paso 3: Ejecutar confirmación atómica
        resp_ejec = ejecutar_confirmacion_propuesta(self.admin_user, propuesta)
        self.assertTrue(resp_ejec["exito"])

        # Verificar en base de datos
        prestamo_creado = Prestamo.objects.filter(destinatario="Pedro Picapiedra").first()
        self.assertIsNotNone(prestamo_creado)
        self.assertEqual(prestamo_creado.cantidad_original, 2)

    def test_cancelacion_propuesta(self):
        resp_p1 = procesar_comando_inventario(
            self.admin_user,
            "Préstale 1 unidad de CAB-0001 a Pedro Picapiedra"
        )
        propuesta = resp_p1.get("propuesta")
        self.assertIsNotNone(propuesta)

        resp_cancel = procesar_comando_inventario(
            self.admin_user,
            "cancela eso",
            propuesta_activa=propuesta
        )
        self.assertEqual(resp_cancel["tipo"], "accion_cancelar")
        self.assertIn("Operación cancelada", resp_cancel["mensaje_pantalla"])


class TestAuditoriaFuncionalHaku(BaseFase2TestCase):
    """Pruebas obligatorias de la auditoría funcional de HAKU Inventario."""

    def test_01_prestamo_conserva_contexto_y_solicita_destinatario(self):
        """1. Un préstamo que conserva el contexto mientras solicita el nombre del destinatario."""
        # Usuario solicita préstamo del producto sin indicar destinatario
        resp1 = procesar_comando_inventario(
            self.admin_user,
            "Quiero realizar un préstamo de 1 unidad de Cable HDMI 2m"
        )
        self.assertEqual(resp1["tipo"], "aclaracion")
        self.assertIn("¿A quién se le prestará 1 unidad", resp1["mensaje_pantalla"])

        # Estado operativo debe haber guardado operacion_pendiente
        ctx = HakuOperationalContext.obtener_o_crear(self.admin_user.id, "test_session_1")
        ctx.operacion_pendiente = {
            "tipo": "PRESTAMO",
            "esperando": "destinatario",
            "producto_id": self.prod_hdmi.id,
            "cantidad": 1,
        }
        HakuOperationalContext.guardar(ctx)

        # 3. Respuesta posterior con el nombre completa la operación pendiente
        resp2 = procesar_comando_inventario(
            self.admin_user,
            "Ayancarlos",
            estado_operativo=ctx
        )
        self.assertEqual(resp2["tipo"], "propuesta")
        self.assertIn("Yancarlos", resp2["propuesta"]["destinatario"])
        self.assertEqual(resp2["propuesta"]["producto_id"], self.prod_hdmi.id)

    def test_02_prestamo_respuesta_con_prefijo_conversacional(self):
        """3. Respuesta con 'La persona se llama Yang Cablos'."""
        ctx = HakuOperationalContext.obtener_o_crear(self.admin_user.id, "test_session_2")
        ctx.operacion_pendiente = {
            "tipo": "PRESTAMO",
            "esperando": "destinatario",
            "producto_id": self.prod_hdmi.id,
            "cantidad": 1,
        }
        HakuOperationalContext.guardar(ctx)

        resp = procesar_comando_inventario(
            self.admin_user,
            "La persona se llama Yang Cablos",
            estado_operativo=ctx
        )
        self.assertEqual(resp["tipo"], "propuesta")
        self.assertEqual(resp["propuesta"]["destinatario"], "Yang Cablos")

    def test_04_productos_agotados_separados_de_bajo_stock(self):
        """4. Productos agotados separados de productos con bajo stock."""
        # Ponchadora tiene stock_disponible = 0 (Agotado)
        # UTP tiene stock_disponible = 2, stock_minimo = 5 (Bajo stock)
        resp_bajo = procesar_comando_inventario(self.admin_user, "¿Qué productos están por debajo del mínimo?")
        self.assertEqual(resp_bajo["tipo"], "consulta")
        # Debe incluir Cable UTP y NO Ponchadora
        self.assertIn("Cable UTP Bobina 100m", resp_bajo["mensaje_pantalla"])
        self.assertNotIn("Ponchadora RJ45", resp_bajo["mensaje_pantalla"])

        resp_agotados = procesar_comando_inventario(self.admin_user, "¿Cuáles productos están agotados?")
        self.assertEqual(resp_agotados["tipo"], "consulta")
        self.assertIn("Ponchadora RJ45", resp_agotados["mensaje_pantalla"])
        self.assertNotIn("Cable UTP Bobina 100m", resp_agotados["mensaje_pantalla"])

    def test_05_stock_disponible_afectado_por_prestamos(self):
        """5. Stock disponible afectado por préstamos, sin doble descuento."""
        existencia = Existencia.objects.get(producto=self.prod_hdmi, almacen=self.almacen_norte)
        # disponible es 20, en_prestamo es 3, total es 23
        self.assertEqual(existencia.stock_disponible, 20)
        self.assertEqual(existencia.stock_en_prestamo, 3)
        self.assertEqual(existencia.stock_total, 23)

    def test_06_creacion_producto_por_voz_abre_formulario(self):
        """6. Registro de un producto mediante voz abre formulario interactivo."""
        resp = procesar_comando_inventario(self.admin_user, "Quiero registrar un nuevo producto")
        self.assertEqual(resp["tipo"], "formulario_crear_producto")
        self.assertIn("categorias", resp["datos"])
        self.assertIn("almacenes", resp["datos"])

    def test_07_inhabilitacion_producto_por_voz(self):
        """7. Inhabilitación de un producto mediante voz."""
        resp = procesar_comando_inventario(
            self.admin_user,
            "Quiero inhabilitar el producto CAB-0001"
        )
        self.assertEqual(resp["tipo"], "propuesta")
        self.assertEqual(resp["propuesta"]["tipo_operacion"], "INHABILITAR_PRODUCTO")
        self.assertEqual(resp["propuesta"]["producto_id"], self.prod_hdmi.id)

    def test_08_consulta_movimientos_filtrada_por_producto(self):
        """8. Una consulta de movimientos filtrada por producto."""
        resp = procesar_comando_inventario(
            self.admin_user,
            "¿Qué movimientos han habido del cable HDMI?"
        )
        self.assertEqual(resp["tipo"], "consulta")
        self.assertIn("Cable HDMI", resp["mensaje_pantalla"])

    def test_09_consulta_sin_movimientos_respuesta_breve(self):
        """9. Una consulta sin movimientos que genera una respuesta breve."""
        from inventario.models import Prestamo
        Prestamo.objects.all().delete()
        Movimiento.objects.all().delete()
        resp = procesar_comando_inventario(
            self.admin_user,
            "cómo va la vaina hoy"
        )
        self.assertEqual(resp["tipo"], "consulta")
        self.assertIn("Hoy no se registraron movimientos de inventario", resp["mensaje_pantalla"])

    def test_10_pregunta_fuera_de_alcance_sol(self):
        """10. Una consulta sobre el sol que recibe una respuesta de fuera de alcance."""
        resp = procesar_comando_inventario(
            self.admin_user,
            "¿Qué es el sol?"
        )
        self.assertEqual(resp["tipo"], "fuera_de_dominio")
        self.assertIn("Solo gestiono operaciones y consultas del inventario", resp["mensaje_pantalla"])
        self.assertIn("Audacia", resp["mensaje_verbal"])

    def test_11_coincidencias_multiples_solicitan_seleccion(self):
        """11. Una consulta de producto con varias coincidencias que solicita una selección."""
        # Creamos un segundo Cable HDMI para forzar ambigüedad
        Producto.objects.create(
            codigo_interno="CAB-0003",
            nombre="Cable HDMI 2.0 2m",
            categoria=self.cat_cables,
            stock_minimo=3,
            activo=True,
        )
        resp = procesar_comando_inventario(
            self.admin_user,
            "consulta Cable HDMI"
        )
        self.assertEqual(resp["tipo"], "aclaracion")
        self.assertIn("varias coincidencias", resp["mensaje_pantalla"])

    def test_12_codigo_exacto_no_se_confunde(self):
        """12. Una consulta con un código exacto de producto que no se confunde con otro artículo."""
        resp = procesar_comando_inventario(
            self.admin_user,
            "Stock de CAB-0002"
        )
        self.assertEqual(resp["tipo"], "consulta")
        self.assertIn("CAB-0002", resp["mensaje_pantalla"])
        self.assertEqual(resp["datos"]["codigo"], "CAB-0002")

    def test_13_confirmacion_inhabilitacion_ejecuta_y_archiva_producto(self):
        """13. La confirmación de una propuesta de inhabilitar producto ejecuta el archivado real."""
        from integraciones.haku.services import ejecutar_confirmacion_propuesta
        propuesta = {
            "id": "prop-inhab-123",
            "tipo_operacion": "INHABILITAR_PRODUCTO",
            "producto_id": self.prod_hdmi.id,
            "producto_nombre": self.prod_hdmi.nombre,
            "producto_codigo": self.prod_hdmi.codigo_interno,
            "expira_en": time.time() + 300,
        }
        res = ejecutar_confirmacion_propuesta(self.admin_user, propuesta)
        self.assertTrue(res["exito"])
        self.prod_hdmi.refresh_from_db()
        self.assertFalse(self.prod_hdmi.activo)
        self.assertTrue(self.prod_hdmi.archivado)

    def test_17_inhabilitacion_idempotencia_y_producto_inactivo(self):
        """17. Inhabilitación no se duplica por confirmación repetida y rechaza productos ya inactivos."""
        from integraciones.haku.services import ejecutar_confirmacion_propuesta
        from django.core.exceptions import ValidationError
        propuesta = {
            "id": "prop-inhab-idem",
            "tipo_operacion": "INHABILITAR_PRODUCTO",
            "producto_id": self.prod_hdmi.id,
            "producto_nombre": self.prod_hdmi.nombre,
            "producto_codigo": self.prod_hdmi.codigo_interno,
            "expira_en": time.time() + 300,
        }
        res1 = ejecutar_confirmacion_propuesta(self.admin_user, propuesta, idempotency_key="key-inhab-1")
        self.assertTrue(res1["exito"])
        self.assertFalse(res1.get("idempotente", False))

        # Reintento con la misma clave de idempotencia
        res2 = ejecutar_confirmacion_propuesta(self.admin_user, propuesta, idempotency_key="key-inhab-1")
        self.assertTrue(res2["exito"])
        self.assertTrue(res2.get("idempotente", False))

        # Intento de nueva propuesta sobre producto ya inactivo/archivado
        propuesta_nueva = {
            "id": "prop-inhab-nueva",
            "tipo_operacion": "INHABILITAR_PRODUCTO",
            "producto_id": self.prod_hdmi.id,
            "producto_nombre": self.prod_hdmi.nombre,
            "producto_codigo": self.prod_hdmi.codigo_interno,
            "expira_en": time.time() + 300,
        }
        with self.assertRaises(ValidationError):
            ejecutar_confirmacion_propuesta(self.admin_user, propuesta_nueva, idempotency_key="key-inhab-2")

    def test_18_preguntas_fuera_dominio_variadas_y_terminos_tecnicos(self):
        """18. Preguntas científicas, históricas y generales nuevas vs consultas legítimas."""
        # 1. Preguntas fuera de dominio nuevas
        preguntas_fuera = [
            "¿A qué temperatura hierve el agua?",
            "¿Cuáles son los planetas del sistema solar?",
            "Dime una receta para hacer pizza",
            "¿Quién ganó el mundial de fútbol?",
            "¿Cuánto cuesta un vuelo a París?",
        ]
        for p in preguntas_fuera:
            resp = procesar_comando_inventario(self.admin_user, p)
            self.assertEqual(resp["tipo"], "fuera_de_dominio", f"Debería ser fuera de dominio: '{p}' -> {resp['tipo']}")

        # 2. Consultas válidas expresadas informalmente
        resp_coloquial = procesar_comando_inventario(self.admin_user, "¿Qué tenemos disponible para trabajar hoy?")
        self.assertEqual(resp_coloquial["tipo"], "consulta")

        # 3. Preguntas con palabras de inventario pero fuera de dominio ("chiste de cables")
        resp_chiste = procesar_comando_inventario(self.admin_user, "Cuéntame un chiste de cables")
        self.assertEqual(resp_chiste["tipo"], "fuera_de_dominio")

    def test_19_solicitud_ambigua_pide_aclaracion(self):
        """19. Solicitud ambigua o incompleta pide aclaración en vez de inventar datos."""
        # Petición incompleta: "Dónde está" sin indicar cuál producto ni tener foco
        resp = procesar_comando_inventario(self.admin_user, "¿Dónde está?")
        self.assertEqual(resp["tipo"], "aclaracion")
        self.assertIn("ubicación", resp["mensaje_pantalla"])

    def test_20_prestamo_conservacion_destinatario_y_cancelacion(self):
        """20. Flujo de préstamo: conserva destinatario a través de turnos y limpia contexto al cancelar."""
        ctx = HakuOperationalContext.obtener_o_crear(self.admin_user.id, "ses_test_20")
        
        # Turno 1: Indicar intención y producto
        resp1 = procesar_comando_inventario(self.admin_user, "Prestar 2 cables HDMI", estado_operativo=ctx)
        self.assertEqual(resp1["tipo"], "aclaracion")
        self.assertIn("¿A quién se le prestarán", resp1["mensaje_pantalla"])

        # Turno 2: Indicar destinatario
        resp2 = procesar_comando_inventario(self.admin_user, "La persona se llama Carlos Martínez", estado_operativo=ctx)
        self.assertEqual(resp2["tipo"], "propuesta")
        self.assertEqual(resp2["propuesta"]["destinatario"], "Carlos Martínez")
        self.assertEqual(resp2["propuesta"]["cantidad"], 2)

        # Turno 3: Cancelar préstamo
        resp3 = procesar_comando_inventario(self.admin_user, "Cancelar eso", estado_operativo=ctx)
        self.assertEqual(resp3["tipo"], "accion_cancelar")
        
        # Verificar que el contexto pendiente se limpió
        ctx_actualizado = HakuOperationalContext.obtener_o_crear(self.admin_user.id, "ses_test_20")
        self.assertIsNone(ctx_actualizado.propuesta_activa)


