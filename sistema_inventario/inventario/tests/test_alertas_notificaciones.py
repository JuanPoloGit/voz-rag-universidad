"""Pruebas unitarias para el motor de alertas y notificaciones de stock crítico y bajo."""

from django.test import RequestFactory, TestCase
from django.contrib.auth import get_user_model

from cuentas.models import UsuarioAudacIA
from inventario.models import Almacen, Categoria, Existencia, Producto
from inventario.servicios.alertas import obtener_alertas_stock, verificar_estado_alerta
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
