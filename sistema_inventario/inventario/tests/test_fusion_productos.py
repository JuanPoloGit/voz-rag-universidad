"""Pruebas unitarias y de integración para la Fusión de Productos Duplicados (Task 6)."""

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
    crear_almacen,
    crear_categoria,
    crear_producto,
    crear_ubicacion,
    fusionar_productos,
)

Usuario = get_user_model()


class FusionProductosBaseTestCase(TestCase):
    def setUp(self):
        super().setUp()
        self.admin = Usuario.objects.create_superuser(
            username="admin_fusion",
            email="admin_fusion@audacia.local",
            password="adminpassword123",
            nombre_completo="Admin Fusion",
            rol=Usuario.Rol.ADMINISTRADOR,
        )
        self.almacen = crear_almacen(
            nombre="Almacen Central Fusion",
            descripcion="Central",
            usuario=self.admin,
        )
        self.usuario_normal = Usuario.objects.create_user(
            username="user_fusion",
            email="user_fusion@audacia.local",
            password="userpassword123",
            nombre_completo="Usuario Fusion",
            rol=Usuario.Rol.USUARIO,
            almacen_asignado=self.almacen,
        )
        self.cat = crear_categoria(
            nombre="Cables Fusion",
            almacen_principal=self.almacen,
            usuario=self.admin,
        )
        self.ubic_1 = crear_ubicacion(
            nombre="Estante F1",
            almacen=self.almacen,
            usuario=self.admin,
        )
        self.ubic_2 = crear_ubicacion(
            nombre="Estante F2",
            almacen=self.almacen,
            usuario=self.admin,
        )

        # Producto principal: 50 unidades, en ubic_1
        self.prod_a = crear_producto(
            nombre="Cable HDMI 2m 4K Principal",
            categoria=self.cat,
            stock_inicial=50,
            ubicaciones=[self.ubic_1.pk],
            usuario=self.admin,
        )

        # Producto duplicado: 30 unidades, en ubic_2
        self.prod_b = crear_producto(
            nombre="Cable HDMI 2 metros Duplicado",
            categoria=self.cat,
            stock_inicial=30,
            ubicaciones=[self.ubic_2.pk],
            usuario=self.admin,
        )


class TestFusionarProductosServicio(FusionProductosBaseTestCase):
    def test_suma_existencias_al_principal(self):
        fusionar_productos(
            producto_principal=self.prod_a,
            producto_duplicado=self.prod_b,
            usuario=self.admin,
        )
        ex_a = Existencia.objects.get(producto=self.prod_a, almacen=self.almacen)
        ex_b = Existencia.objects.get(producto=self.prod_b, almacen=self.almacen)

        self.assertEqual(ex_a.stock_disponible, 50 + 30)
        self.assertEqual(ex_b.stock_disponible, 0)

    def test_reasigna_movimientos_al_principal(self):
        fusionar_productos(
            producto_principal=self.prod_a,
            producto_duplicado=self.prod_b,
            usuario=self.admin,
        )
        self.assertFalse(Movimiento.objects.filter(producto=self.prod_b).exists())
        self.assertTrue(Movimiento.objects.filter(producto=self.prod_a).count() >= 2)

    def test_duplicado_queda_archivado_e_inactivo(self):
        fusionar_productos(
            producto_principal=self.prod_a,
            producto_duplicado=self.prod_b,
            usuario=self.admin,
        )
        self.prod_b.refresh_from_db()
        self.assertTrue(self.prod_b.archivado)
        self.assertFalse(self.prod_b.activo)

    def test_unifica_ubicaciones_sin_duplicar(self):
        fusionar_productos(
            producto_principal=self.prod_a,
            producto_duplicado=self.prod_b,
            usuario=self.admin,
        )
        ubics = set(
            ProductoUbicacion.objects.filter(producto=self.prod_a).values_list("ubicacion_id", flat=True)
        )
        # El producto principal mantiene su ubicación física única (invariante 1 producto = 1 ubicación)
        self.assertEqual(ubics, {self.ubic_1.pk})
        self.assertFalse(ProductoUbicacion.objects.filter(producto=self.prod_b).exists())

    def test_usuario_normal_no_puede_fusionar(self):
        with self.assertRaises(PermissionDenied):
            fusionar_productos(
                producto_principal=self.prod_a,
                producto_duplicado=self.prod_b,
                usuario=self.usuario_normal,
            )

    def test_no_se_puede_fusionar_consigo_mismo(self):
        with self.assertRaises(ValidationError):
            fusionar_productos(
                producto_principal=self.prod_a,
                producto_duplicado=self.prod_a,
                usuario=self.admin,
            )

    def test_auditoria_detallada(self):
        fusionar_productos(
            producto_principal=self.prod_a,
            producto_duplicado=self.prod_b,
            usuario=self.admin,
        )
        aud = Auditoria.objects.filter(accion="FUSIONAR_PRODUCTOS", id_entidad=str(self.prod_a.id)).first()
        self.assertIsNotNone(aud)
        self.assertIn("duplicado_codigo", aud.valores_anteriores)
        self.assertEqual(aud.valores_anteriores["duplicado_id"], self.prod_b.id)


class TestVistaFusionar(FusionProductosBaseTestCase):
    def test_vista_fusionar_get_y_post(self):
        self.client.force_login(self.admin)
        r_get = self.client.get(reverse("inventario:producto-fusionar", args=[self.prod_a.pk]))
        self.assertEqual(r_get.status_code, 200)
        self.assertContains(r_get, "Fusionar con Producto Duplicado")

        r_post = self.client.post(
            reverse("inventario:producto-fusionar", args=[self.prod_a.pk]),
            {"producto_duplicado": self.prod_b.pk},
        )
        self.assertRedirects(r_post, reverse("inventario:producto-detalle", args=[self.prod_a.pk]))
        self.prod_b.refresh_from_db()
        self.assertTrue(self.prod_b.archivado)

    def test_vista_fusionar_denegada_a_usuario_normal(self):
        self.client.force_login(self.usuario_normal)
        r = self.client.get(reverse("inventario:producto-fusionar", args=[self.prod_a.pk]))
        self.assertEqual(r.status_code, 403)
