"""Pruebas unitarias para el motor de alertas y notificaciones de stock crítico y bajo."""

from django.test import Client, RequestFactory, TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse

from cuentas.models import UsuarioAudacIA
from inventario.models import Almacen, AlertaStockLeida, Categoria, ConteoFisico, Existencia, Producto
from inventario.servicios.alertas import obtener_alertas_stock, verificar_estado_alerta
from inventario.servicios.operaciones import registrar_conteo_fisico, confirmar_conteo_fisico
from inventario.context_processors import alertas_stock

User = get_user_model()


class AlertasNotificacionesTest(TestCase):
    def setUp(self):
        # Almacenes
        self.almacen_a = Almacen.objects.create(nombre="Almacén Principal", activo=True)
        self.almacen_b = Almacen.objects.create(nombre="Almacén Secundario", activo=True)

        # Usuarios
        self.superadmin = User.objects.create_superuser(
            username="superadmin",
            password="Password123",
            nombre_completo="Super Administrador",
        )
        self.admin = User.objects.create_user(
            username="admin_user",
            password="Password123",
            nombre_completo="Administrador General",
            rol=UsuarioAudacIA.Rol.ADMINISTRADOR,
        )
        self.admin_2 = User.objects.create_user(
            username="admin_user_2",
            password="Password123",
            nombre_completo="Segundo Administrador",
            rol=UsuarioAudacIA.Rol.ADMINISTRADOR,
        )
        self.usuario_a = User.objects.create_user(
            username="user_a",
            password="Password123",
            nombre_completo="Operador Almacén A",
            rol=UsuarioAudacIA.Rol.USUARIO,
            almacen_asignado=self.almacen_a,
        )
        self.usuario_b = User.objects.create_user(
            username="user_b",
            password="Password123",
            nombre_completo="Operador Almacén B",
            rol=UsuarioAudacIA.Rol.USUARIO,
            almacen_asignado=self.almacen_b,
        )

        # Categorías
        self.cat_a = Categoria.objects.create(
            nombre="Electrónica",
            almacen_principal=self.almacen_a,
        )
        self.cat_b = Categoria.objects.create(
            nombre="Herramientas",
            almacen_principal=self.almacen_b,
        )

        # Productos
        # prod_critico: stock = 0, stock_minimo = 5
        self.prod_critico = Producto.objects.create(
            nombre="Cable HDMI 2m",
            categoria=self.cat_a,
            stock_minimo=5,
        )
        self.ex_critica = Existencia.objects.create(
            producto=self.prod_critico,
            almacen=self.almacen_a,
            stock_disponible=0,
            stock_en_prestamo=0,
        )

        # prod_bajo: stock = 3, stock_minimo = 10
        self.prod_bajo = Producto.objects.create(
            nombre="Cinta Aislante",
            categoria=self.cat_a,
            stock_minimo=10,
        )
        self.ex_baja = Existencia.objects.create(
            producto=self.prod_bajo,
            almacen=self.almacen_a,
            stock_disponible=3,
            stock_en_prestamo=0,
        )

        # prod_normal: stock = 20, stock_minimo = 5
        self.prod_normal = Producto.objects.create(
            nombre="Conector RJ45",
            categoria=self.cat_a,
            stock_minimo=5,
        )
        self.ex_normal = Existencia.objects.create(
            producto=self.prod_normal,
            almacen=self.almacen_a,
            stock_disponible=20,
            stock_en_prestamo=0,
        )

        # prod_en_almacen_b: stock = 0, stock_minimo = 2
        self.prod_b = Producto.objects.create(
            nombre="Destornillador Phillips",
            categoria=self.cat_b,
            stock_minimo=2,
        )
        self.ex_b = Existencia.objects.create(
            producto=self.prod_b,
            almacen=self.almacen_b,
            stock_disponible=0,
            stock_en_prestamo=0,
        )

    def test_verificar_estado_alerta(self):
        """Verifica la clasificación de alerta según stock disponible y mínimo."""
        self.assertEqual(verificar_estado_alerta(self.ex_critica)["nivel"], "CRITICO")
        self.assertEqual(verificar_estado_alerta(self.ex_baja)["nivel"], "BAJO")
        self.assertIsNone(verificar_estado_alerta(self.ex_normal)["nivel"])

    def test_obtener_alertas_stock_usuario_normal(self):
        """El usuario normal solo recibe alertas de existencias de su almacén asignado."""
        alertas = obtener_alertas_stock(usuario=self.usuario_a)

        # Debe ver ex_critica y ex_baja de almacen_a
        self.assertEqual(alertas["total_criticas"], 1)
        self.assertEqual(alertas["total_bajas"], 1)
        self.assertEqual(alertas["total_alertas"], 2)

        criticas_ids = [e.id for e in alertas["criticas"]]
        self.assertIn(self.ex_critica.id, criticas_ids)
        self.assertNotIn(self.ex_b.id, criticas_ids)

        bajas_ids = [e.id for e in alertas["bajas"]]
        self.assertIn(self.ex_baja.id, bajas_ids)

    def test_obtener_alertas_stock_administrador(self):
        """El administrador ve todas las alertas de todos los almacenes."""
        alertas = obtener_alertas_stock(usuario=self.admin)

        # Debe ver ex_critica (almacen_a) y ex_b (almacen_b) como críticas
        self.assertEqual(alertas["total_criticas"], 2)
        self.assertEqual(alertas["total_bajas"], 1)
        self.assertEqual(alertas["total_alertas"], 3)

    def test_context_processor_alertas_stock(self):
        """El context processor inyecta alertas_resumen para la barra superior."""
        factory = RequestFactory()
        request = factory.get("/")
        request.user = self.usuario_a

        context = alertas_stock(request)
        self.assertIn("alertas_resumen", context)
        self.assertEqual(context["alertas_resumen"]["total_alertas"], 2)
        self.assertEqual(context["alertas_resumen"]["total_criticas"], 1)
        self.assertEqual(context["alertas_resumen"]["total_bajas"], 1)

    def test_notificacion_conteo_pendiente_para_administradores(self):
        """Los conteos pendientes de aprobación se notifican a administradores pero no a usuarios normales."""
        conteo = registrar_conteo_fisico(
            producto=self.prod_normal,
            almacen=self.almacen_a,
            cantidad_contada=15,
            usuario=self.usuario_a,
            motivo="Auditoría periódica",
        )
        self.assertFalse(conteo.confirmado)

        factory = RequestFactory()

        # Usuario normal no recibe notificación de conteos pendientes
        req_user = factory.get("/")
        req_user.user = self.usuario_a
        ctx_user = alertas_stock(req_user)["alertas_resumen"]
        self.assertEqual(len(ctx_user["conteos_pendientes"]), 0)
        self.assertEqual(len(ctx_user["conteos_no_leidos"]), 0)

        # Administrador recibe la notificación con su clave 'conteo:<id>'
        req_admin = factory.get("/")
        req_admin.user = self.admin
        ctx_admin = alertas_stock(req_admin)["alertas_resumen"]
        self.assertEqual(len(ctx_admin["conteos_pendientes"]), 1)
        self.assertEqual(len(ctx_admin["conteos_no_leidos"]), 1)
        self.assertEqual(ctx_admin["conteos_pendientes"][0].id, conteo.id)
        self.assertEqual(ctx_admin["conteos_pendientes"][0].notificacion_key, f"conteo:{conteo.id}")
        self.assertFalse(ctx_admin["conteos_pendientes"][0].es_leida)

    def test_unicidad_y_persistencia_lectura_conteo_entre_administradores(self):
        """Si el Admin 1 marca la notificación de conteo como leída, para el Admin 2 sigue no leída y persiste para Admin 1."""
        conteo = registrar_conteo_fisico(
            producto=self.prod_normal,
            almacen=self.almacen_a,
            cantidad_contada=18,
            usuario=self.usuario_a,
            motivo="Verificación de estantería",
        )
        key_conteo = f"conteo:{conteo.id}"

        client = Client()

        # Admin 1 inicia sesión y marca como leída la notificación del conteo
        client.force_login(self.admin)
        resp = client.post(
            reverse("inventario:alerta-marcar-leida"),
            {"accion": "una", "key": key_conteo},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(resp.status_code, 200)

        # Verificar en contexto de Admin 1: está leída
        factory = RequestFactory()
        req_admin1 = factory.get("/")
        req_admin1.user = self.admin
        ctx_admin1 = alertas_stock(req_admin1)["alertas_resumen"]
        self.assertEqual(len(ctx_admin1["conteos_pendientes"]), 1)
        self.assertTrue(ctx_admin1["conteos_pendientes"][0].es_leida)
        self.assertEqual(len(ctx_admin1["conteos_no_leidos"]), 0)

        # Para Admin 2: NO está leída porque la lectura es única e individual
        req_admin2 = factory.get("/")
        req_admin2.user = self.admin_2
        ctx_admin2 = alertas_stock(req_admin2)["alertas_resumen"]
        self.assertEqual(len(ctx_admin2["conteos_pendientes"]), 1)
        self.assertFalse(ctx_admin2["conteos_pendientes"][0].es_leida)
        self.assertEqual(len(ctx_admin2["conteos_no_leidos"]), 1)

        # Persistencia: Admin 1 cierra sesión y vuelve a entrar en otra solicitud/sesión
        client.logout()
        client.force_login(self.admin)
        req_admin1_nueva_sesion = factory.get("/")
        req_admin1_nueva_sesion.user = self.admin
        ctx_admin1_nueva = alertas_stock(req_admin1_nueva_sesion)["alertas_resumen"]
        self.assertEqual(len(ctx_admin1_nueva["conteos_no_leidos"]), 0)
        self.assertTrue(ctx_admin1_nueva["conteos_pendientes"][0].es_leida)

    def test_resolucion_global_al_aprobar_conteo(self):
        """Al confirmarse y aplicarse el ajuste del conteo, la notificación desaparece para TODOS los administradores."""
        conteo = registrar_conteo_fisico(
            producto=self.prod_normal,
            almacen=self.almacen_a,
            cantidad_contada=10,
            usuario=self.usuario_a,
            motivo="Ajuste requerido",
        )

        factory = RequestFactory()

        # Ambos administradores ven 1 conteo pendiente
        req1 = factory.get("/")
        req1.user = self.admin
        self.assertEqual(len(alertas_stock(req1)["alertas_resumen"]["conteos_pendientes"]), 1)

        req2 = factory.get("/")
        req2.user = self.admin_2
        self.assertEqual(len(alertas_stock(req2)["alertas_resumen"]["conteos_pendientes"]), 1)

        # Admin 2 aprueba el conteo
        confirmar_conteo_fisico(
            conteo=conteo,
            usuario=self.admin_2,
        )

        # Ya ningún administrador tiene la notificación porque el conteo fue resuelto
        req1_post = factory.get("/")
        req1_post.user = self.admin
        self.assertEqual(len(alertas_stock(req1_post)["alertas_resumen"]["conteos_pendientes"]), 0)
        self.assertEqual(len(alertas_stock(req1_post)["alertas_resumen"]["conteos_no_leidos"]), 0)

        req2_post = factory.get("/")
        req2_post.user = self.admin_2
        self.assertEqual(len(alertas_stock(req2_post)["alertas_resumen"]["conteos_pendientes"]), 0)
        self.assertEqual(len(alertas_stock(req2_post)["alertas_resumen"]["conteos_no_leidos"]), 0)

        # Las marcas de lectura residuales fueron eliminadas
        self.assertFalse(AlertaStockLeida.objects.filter(clave=f"conteo:{conteo.id}").exists())

