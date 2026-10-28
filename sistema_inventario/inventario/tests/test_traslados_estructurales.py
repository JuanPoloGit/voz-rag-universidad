"""Pruebas para Traslados Estructurales al desactivar almacén, ubicación o categoría (Task 5)."""

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
)
from inventario.servicios.stock import (
    desactivar_almacen,
    desactivar_categoria,
    desactivar_ubicacion,
    inicializar_stock,
    trasladar_productos_almacen,
    trasladar_productos_categoria,
    trasladar_productos_ubicacion,
)

Usuario = get_user_model()


class TrasladosBaseTestCase(TestCase):
    def setUp(self):
        super().setUp()
        self.admin = Usuario.objects.create_superuser(
            username="admin_traslados",
            email="admin_traslados@audacia.local",
            password="adminpassword123",
            nombre_completo="Admin Traslados",
            rol=Usuario.Rol.ADMINISTRADOR,
        )
        self.almacen_a = crear_almacen(
            nombre="Almacen Origen Traslado",
            descripcion="Origen",
            usuario=self.admin,
        )
        self.almacen_b = crear_almacen(
            nombre="Almacen Destino Traslado",
            descripcion="Destino",
            usuario=self.admin,
        )
        self.almacen_vacio = crear_almacen(
            nombre="Almacen Vacio",
            descripcion="Vacio",
            usuario=self.admin,
        )
        self.usuario_normal = Usuario.objects.create_user(
            username="user_traslados",
            email="user_traslados@audacia.local",
            password="userpassword123",
            nombre_completo="Usuario Traslados",
            rol=Usuario.Rol.USUARIO,
            almacen_asignado=self.almacen_a,
        )
        self.cat_a = crear_categoria(
            nombre="Cables Traslado",
            almacen_principal=self.almacen_a,
            usuario=self.admin,
        )
        self.prod_a = crear_producto(
            nombre="Cable UTP Cat6 100m",
            categoria=self.cat_a,
            stock_inicial=50,
            usuario=self.admin,
        )
        self.ubic_a1 = crear_ubicacion(
            nombre="Rack A1",
            almacen=self.almacen_a,
            usuario=self.admin,
        )
        self.ubic_a2 = crear_ubicacion(
            nombre="Rack A2",
            almacen=self.almacen_a,
            usuario=self.admin,
        )
        self.ubic_b1 = crear_ubicacion(
            nombre="Rack B1",
            almacen=self.almacen_b,
            usuario=self.admin,
        )
        ProductoUbicacion.objects.get_or_create(producto=self.prod_a, ubicacion=self.ubic_a1)


class TestTrasladoAlmacen(TrasladosBaseTestCase):
    def test_traslada_existencias_y_registra_movimientos(self):
        n = trasladar_productos_almacen(
            almacen_origen=self.almacen_a,
            almacen_destino=self.almacen_b,
            usuario=self.admin,
        )
        self.assertEqual(n, 1)

        self.assertFalse(Existencia.objects.filter(producto=self.prod_a, almacen=self.almacen_a).exists())
        ex_b = Existencia.objects.get(producto=self.prod_a, almacen=self.almacen_b)
        self.assertEqual(ex_b.stock_disponible, 50)

        mov = Movimiento.objects.filter(
            tipo=Movimiento.Tipo.TRASLADO_ENTRE,
            producto=self.prod_a,
            almacen_origen=self.almacen_a,
            almacen_destino=self.almacen_b,
        ).first()
        self.assertIsNotNone(mov)
        self.assertEqual(mov.cantidad, 50)

    def test_no_puede_trasladar_a_mismo_almacen(self):
        with self.assertRaises(ValidationError):
            trasladar_productos_almacen(
                almacen_origen=self.almacen_a,
                almacen_destino=self.almacen_a,
                usuario=self.admin,
            )

    def test_desactivar_almacen_con_stock_exige_destino(self):
        with self.assertRaises(ValidationError):
            desactivar_almacen(
                almacen=self.almacen_a,
                almacen_destino=None,
                usuario=self.admin,
            )

    def test_desactivar_almacen_traslada_y_desactiva(self):
        desactivar_almacen(
            almacen=self.almacen_a,
            almacen_destino=self.almacen_b,
            usuario=self.admin,
        )
        self.almacen_a.refresh_from_db()
        self.assertFalse(self.almacen_a.activo)

        self.assertFalse(Existencia.objects.filter(producto=self.prod_a, almacen=self.almacen_a).exists())
        ex_b = Existencia.objects.get(producto=self.prod_a, almacen=self.almacen_b)
        self.assertEqual(ex_b.stock_disponible, 50)

        aud = Auditoria.objects.filter(accion="DESACTIVAR_ALMACEN", id_entidad=str(self.almacen_a.id)).first()
        self.assertIsNotNone(aud)

    def test_desactivar_almacen_vacio_sin_destino_ok(self):
        desactivar_almacen(
            almacen=self.almacen_vacio,
            almacen_destino=None,
            usuario=self.admin,
        )
        self.almacen_vacio.refresh_from_db()
        self.assertFalse(self.almacen_vacio.activo)

    def test_desactivar_actualiza_categorias_apuntando_al_almacen(self):
        desactivar_almacen(
            almacen=self.almacen_a,
            almacen_destino=self.almacen_b,
            usuario=self.admin,
        )
        self.cat_a.refresh_from_db()
        self.assertEqual(self.cat_a.almacen_principal, self.almacen_b)


class TestTrasladoUbicacion(TrasladosBaseTestCase):
    def test_reasigna_producto_ubicaciones(self):
        n = trasladar_productos_ubicacion(
            ubicacion_origen=self.ubic_a1,
            ubicacion_destino=self.ubic_a2,
            usuario=self.admin,
        )
        self.assertGreaterEqual(n, 1)
        self.assertFalse(ProductoUbicacion.objects.filter(ubicacion=self.ubic_a1).exists())
        self.assertTrue(ProductoUbicacion.objects.filter(ubicacion=self.ubic_a2).exists())

    def test_destino_debe_ser_mismo_almacen(self):
        with self.assertRaises(ValidationError):
            trasladar_productos_ubicacion(
                ubicacion_origen=self.ubic_a1,
                ubicacion_destino=self.ubic_b1,
                usuario=self.admin,
            )

    def test_desactivar_ubicacion_con_productos_exige_destino(self):
        with self.assertRaises(ValidationError):
            desactivar_ubicacion(
                ubicacion=self.ubic_a1,
                ubicacion_destino=None,
                usuario=self.admin,
            )

    def test_desactivar_ubicacion_traslada_y_desactiva(self):
        desactivar_ubicacion(
            ubicacion=self.ubic_a1,
            ubicacion_destino=self.ubic_a2,
            usuario=self.admin,
        )
        self.ubic_a1.refresh_from_db()
        self.assertFalse(self.ubic_a1.activo)
        self.assertTrue(ProductoUbicacion.objects.filter(ubicacion=self.ubic_a2).exists())


class TestTrasladoCategoria(TrasladosBaseTestCase):
    def test_traslada_existencias_al_nuevo_almacen_y_actualiza_categoria(self):
        n = trasladar_productos_categoria(
            categoria=self.cat_a,
            almacen_destino=self.almacen_b,
            usuario=self.admin,
        )
        self.assertGreaterEqual(n, 1)
        self.cat_a.refresh_from_db()
        self.assertEqual(self.cat_a.almacen_principal, self.almacen_b)

        ex_b = Existencia.objects.get(producto=self.prod_a, almacen=self.almacen_b)
        self.assertEqual(ex_b.stock_disponible, 50)

    def test_desactivar_categoria_con_productos_falla(self):
        with self.assertRaises(ValidationError):
            desactivar_categoria(
                categoria=self.cat_a,
                usuario=self.admin,
            )

    def test_desactivar_categoria_vacia_ok(self):
        cat_vacia = crear_categoria(
            nombre="Categoria Vacia",
            almacen_principal=self.almacen_b,
            usuario=self.admin,
        )
        desactivar_categoria(
            categoria=cat_vacia,
            usuario=self.admin,
        )
        cat_vacia.refresh_from_db()
        self.assertFalse(cat_vacia.activo)


class TestVistasTraslado(TrasladosBaseTestCase):
    def test_vista_desactivar_almacen_get_y_post(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse("inventario:almacen-desactivar", args=[self.almacen_a.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Traslado Estructural Requerido")

        r_post = self.client.post(
            reverse("inventario:almacen-desactivar", args=[self.almacen_a.pk]),
            {"almacen_destino": self.almacen_b.pk},
        )
        self.assertRedirects(r_post, reverse("inventario:almacen-lista"))
        self.almacen_a.refresh_from_db()
        self.assertFalse(self.almacen_a.activo)

    def test_vista_desactivar_almacen_denegada_a_usuario_normal(self):
        self.client.force_login(self.usuario_normal)
        r = self.client.get(reverse("inventario:almacen-desactivar", args=[self.almacen_a.pk]))
        self.assertEqual(r.status_code, 403)
