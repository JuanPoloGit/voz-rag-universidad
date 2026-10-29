"""Pruebas para Producto CRUD, inicialización de stock y búsqueda (Task 4)."""

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse

from inventario.models import (
    Almacen,
    Auditoria,
    Categoria,
    Existencia,
    Movimiento,
    Producto,
    ProductoUbicacion,
    Ubicacion,
)
from inventario.servicios.datos_maestros import (
    actualizar_producto,
    archivar_producto,
    crear_almacen,
    crear_categoria,
    crear_producto,
    crear_ubicacion,
)
from inventario.servicios.stock import (
    buscar_por_codigo_barras,
    inicializar_stock,
    verificar_nombre_similar,
)

Usuario = get_user_model()


class ProductoBaseTestCase(TestCase):
    def setUp(self):
        super().setUp()
        self.admin = Usuario.objects.create_superuser(
            username="admin_prod",
            email="admin_prod@audacia.local",
            password="adminpassword123",
            nombre_completo="Admin Productos",
            rol=Usuario.Rol.ADMINISTRADOR,
        )
        self.almacen = crear_almacen(
            nombre="Almacen Central Prod",
            descripcion="Central",
            usuario=self.admin,
        )
        self.almacen_otro = crear_almacen(
            nombre="Almacen Satelite Prod",
            descripcion="Satelite",
            usuario=self.admin,
        )
        self.usuario_normal = Usuario.objects.create_user(
            username="user_prod",
            email="user_prod@audacia.local",
            password="userpassword123",
            nombre_completo="Usuario Prod",
            rol=Usuario.Rol.USUARIO,
            almacen_asignado=self.almacen,
        )
        self.cat_cables = crear_categoria(
            nombre="Cables y Conexiones",
            almacen_principal=self.almacen,
            usuario=self.admin,
        )
        self.cat_otro = crear_categoria(
            nombre="Herramientas Remotas",
            almacen_principal=self.almacen_otro,
            usuario=self.admin,
        )
        self.ubicacion_1 = crear_ubicacion(
            nombre="Gaveta A-1",
            almacen=self.almacen,
            usuario=self.admin,
        )


class TestCrearProducto(ProductoBaseTestCase):
    def test_crea_producto_con_codigo_automatico(self):
        prod = crear_producto(
            nombre="Cable HDMI 2m",
            categoria=self.cat_cables,
            usuario=self.admin,
        )
        self.assertRegex(prod.codigo_interno, r"^CAB-\d{4}$")
        self.assertTrue(prod.activo)
        self.assertFalse(prod.archivado)

        aud = Auditoria.objects.filter(tipo_entidad="Producto", accion="CREAR_PRODUCTO").first()
        self.assertIsNotNone(aud)
        self.assertEqual(aud.id_entidad, str(prod.id))

    def test_stock_inicial_crea_existencia_y_movimiento_inicial(self):
        prod = crear_producto(
            nombre="Cable VGA 1.5m",
            categoria=self.cat_cables,
            stock_inicial=50,
            usuario=self.admin,
        )
        ex = Existencia.objects.get(producto=prod, almacen=self.almacen)
        self.assertEqual(ex.stock_disponible, 50)

        mov = Movimiento.objects.get(producto=prod, tipo=Movimiento.Tipo.MOVIMIENTO_INICIAL)
        self.assertEqual(mov.cantidad, 50)
        self.assertEqual(mov.stock_disponible_anterior, 0)
        self.assertEqual(mov.stock_disponible_posterior, 50)
        self.assertEqual(mov.almacen_destino, self.almacen)

    def test_stock_inicial_cero_no_crea_movimiento_inicial(self):
        prod = crear_producto(
            nombre="Cable Tipo C",
            categoria=self.cat_cables,
            stock_inicial=0,
            usuario=self.admin,
        )
        self.assertFalse(Movimiento.objects.filter(producto=prod).exists())
        self.assertTrue(Existencia.objects.filter(producto=prod, almacen=self.almacen).exists())

    def test_codigo_barras_duplicado_lanza_validation_error(self):
        crear_producto(
            nombre="Cable A",
            categoria=self.cat_cables,
            codigo_barras="7701234567890",
            usuario=self.admin,
        )
        with self.assertRaises(ValidationError):
            crear_producto(
                nombre="Cable B",
                categoria=self.cat_cables,
                codigo_barras="7701234567890",
                usuario=self.admin,
            )

    def test_codigo_barras_vacio_permite_multiples_productos(self):
        p1 = crear_producto(
            nombre="Resistencia 1K",
            categoria=self.cat_cables,
            codigo_barras="",
            usuario=self.admin,
        )
        p2 = crear_producto(
            nombre="Resistencia 10K",
            categoria=self.cat_cables,
            codigo_barras="",
            usuario=self.admin,
        )
        self.assertIsNotNone(p1.pk)
        self.assertIsNotNone(p2.pk)
        self.assertIsNone(p1.codigo_barras)
        self.assertIsNone(p2.codigo_barras)

    def test_asigna_ubicaciones_al_crear(self):
        prod = crear_producto(
            nombre="Cable de Red 5m",
            categoria=self.cat_cables,
            ubicaciones=[self.ubicacion_1.pk],
            usuario=self.admin,
        )
        self.assertTrue(ProductoUbicacion.objects.filter(producto=prod, ubicacion=self.ubicacion_1).exists())


class TestInicializarStock(ProductoBaseTestCase):
    def test_inicializa_stock_atomicamente(self):
        prod = crear_producto(
            nombre="Tester Digital",
            categoria=self.cat_cables,
            stock_inicial=0,
            usuario=self.admin,
        )
        ex, mov = inicializar_stock(
            producto=prod,
            almacen=self.almacen,
            cantidad=25,
            usuario=self.admin,
            ip_origen="192.168.1.15",
        )
        self.assertEqual(ex.stock_disponible, 25)
        self.assertEqual(mov.tipo, Movimiento.Tipo.MOVIMIENTO_INICIAL)
        self.assertEqual(mov.cantidad, 25)
        self.assertEqual(mov.stock_disponible_posterior, 25)


class TestActualizarYArchivarProducto(ProductoBaseTestCase):
    def setUp(self):
        super().setUp()
        self.prod = crear_producto(
            nombre="Osciloscopio USB",
            categoria=self.cat_cables,
            stock_inicial=5,
            stock_minimo=2,
            codigo_barras="12345678",
            usuario=self.admin,
        )

    def test_no_puede_cambiar_codigo_interno_al_actualizar(self):
        codigo_orig = self.prod.codigo_interno
        actualizar_producto(
            producto=self.prod,
            nombre="Osciloscopio Pro USB",
            stock_minimo=3,
            codigo_barras="12345678",
            nombre_empaque="",
            unidades_por_empaque=None,
            descripcion="Notas",
            ubicaciones=[],
            usuario=self.admin,
        )
        self.prod.refresh_from_db()
        self.assertEqual(self.prod.codigo_interno, codigo_orig)
        self.assertEqual(self.prod.nombre, "Osciloscopio Pro USB")

    def test_usuario_normal_no_puede_archivar(self):
        with self.assertRaises(PermissionDenied):
            archivar_producto(producto=self.prod, usuario=self.usuario_normal)

    def test_admin_archiva_producto(self):
        archivar_producto(producto=self.prod, usuario=self.admin)
        self.prod.refresh_from_db()
        self.assertTrue(self.prod.archivado)
        self.assertFalse(self.prod.activo)
        aud = Auditoria.objects.filter(tipo_entidad="Producto", accion="ARCHIVAR_PRODUCTO").first()
        self.assertIsNotNone(aud)


class TestBusquedaYSimilares(ProductoBaseTestCase):
    def test_buscar_por_codigo_barras(self):
        prod = crear_producto(
            nombre="Multímetro Fluke",
            categoria=self.cat_cables,
            codigo_barras="9876543210",
            usuario=self.admin,
        )
        encontrado = buscar_por_codigo_barras("9876543210")
        self.assertEqual(encontrado, prod)

        no_existe = buscar_por_codigo_barras("0000000000")
        self.assertIsNone(no_existe)

    def test_verificar_nombre_similar(self):
        crear_producto(
            nombre="Sensor de Temperatura LM35",
            categoria=self.cat_cables,
            usuario=self.admin,
        )
        similares = verificar_nombre_similar("Sensor de Temperatura")
        self.assertTrue(any("LM35" in s["nombre"] for s in similares))


class TestProductoVistas(ProductoBaseTestCase):
    def setUp(self):
        super().setUp()
        self.prod = crear_producto(
            nombre="Alicate de Corte",
            categoria=self.cat_cables,
            stock_inicial=10,
            codigo_barras="11223344",
            usuario=self.admin,
        )

    def test_lista_muestra_productos(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse("inventario:producto-lista"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Alicate de Corte")

    def test_busqueda_codigo_barras_redirige_a_detalle(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse("inventario:producto-lista"), {"q": "11223344"})
        self.assertRedirects(r, reverse("inventario:producto-detalle", args=[self.prod.pk]))

    def test_detalle_muestra_stock(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse("inventario:producto-detalle", args=[self.prod.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, self.prod.codigo_interno)
        self.assertContains(r, "10")  # stock disponible
        self.assertContains(r, "modalEntradaRapidaProducto")
        self.assertContains(r, "Registrar Entrada")
        self.assertContains(r, self.prod.categoria.almacen_principal.nombre)

    def test_detalle_renderiza_modal_entrada_manual(self):
        self.client.force_login(self.admin)
        url = reverse("inventario:producto-detalle", args=[self.prod.pk])
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "modalEntradaRapidaProducto")
        self.assertContains(r, "abrirModalEntradaProducto")
        self.assertNotContains(r, "urlParams.get('accion')")

    def test_endpoint_verificar_nombre_similar(self):
        self.client.force_login(self.admin)
        r = self.client.get(
            reverse("inventario:producto-verificar-nombre"),
            {"nombre": "Alicate"},
        )
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(any(s["nombre"] == "Alicate de Corte" for s in data["similares"]))
