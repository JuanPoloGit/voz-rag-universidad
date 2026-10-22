"""Pruebas unitarias y de integración para Categorías (Task 3).

Verifica creación, edición, validación de permisos por almacén asignado,
detección de necesidad de traslado cuando hay productos asociados, y auditoría.
"""

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse

from inventario.models import Almacen, Auditoria, Categoria, Producto
from inventario.servicios import NecesitaTraslado
from inventario.servicios.datos_maestros import (
    actualizar_categoria,
    crear_almacen,
    crear_categoria,
)

Usuario = get_user_model()


class CategoriaBaseTestCase(TestCase):
    def setUp(self):
        super().setUp()
        self.admin = Usuario.objects.create_superuser(
            username="admin_cat",
            email="admin_cat@audacia.local",
            password="adminpassword123",
            nombre_completo="Admin Categorias",
            rol=Usuario.Rol.ADMINISTRADOR,
        )
        self.almacen_a = crear_almacen(
            nombre="Almacen Central Cat",
            descripcion="Central",
            usuario=self.admin,
        )
        self.almacen_b = crear_almacen(
            nombre="Almacen Satelite Cat",
            descripcion="Satelite",
            usuario=self.admin,
        )
        self.usuario_normal = Usuario.objects.create_user(
            username="user_cat",
            email="user_cat@audacia.local",
            password="userpassword123",
            nombre_completo="Usuario Cat",
            rol=Usuario.Rol.USUARIO,
            almacen_asignado=self.almacen_a,
        )


class TestCrearCategoriaServicio(CategoriaBaseTestCase):
    def test_crea_categoria_con_almacen_principal_y_audita(self):
        cat = crear_categoria(
            nombre="Cables",
            almacen_principal=self.almacen_a,
            usuario=self.admin,
            ip_origen="192.168.1.10",
        )
        self.assertEqual(cat.nombre, "Cables")
        self.assertEqual(cat.almacen_principal, self.almacen_a)
        self.assertEqual(cat.prefijo_codigo, "CAB")
        self.assertTrue(cat.activo)

        aud = Auditoria.objects.filter(tipo_entidad="Categoria", accion="CREAR_CATEGORIA").first()
        self.assertIsNotNone(aud)
        self.assertEqual(aud.id_entidad, str(cat.id))
        self.assertEqual(aud.valores_nuevos["almacen_principal_id"], self.almacen_a.id)

    def test_nombre_duplicado_lanza_validation_error(self):
        crear_categoria(
            nombre="Herramientas",
            almacen_principal=self.almacen_a,
            usuario=self.admin,
        )
        with self.assertRaises(ValidationError):
            crear_categoria(
                nombre="Herramientas",
                almacen_principal=self.almacen_b,
                usuario=self.admin,
            )

    def test_usuario_normal_puede_crear_en_su_almacen(self):
        cat = crear_categoria(
            nombre="Sensores",
            almacen_principal=self.almacen_a,
            usuario=self.usuario_normal,
        )
        self.assertEqual(cat.almacen_principal, self.almacen_a)

    def test_usuario_normal_no_puede_crear_en_almacen_ajeno(self):
        with self.assertRaises(PermissionDenied):
            crear_categoria(
                nombre="Actuadores",
                almacen_principal=self.almacen_b,
                usuario=self.usuario_normal,
            )


class TestActualizarCategoriaServicio(CategoriaBaseTestCase):
    def setUp(self):
        super().setUp()
        self.categoria = crear_categoria(
            nombre="Ferretería",
            almacen_principal=self.almacen_a,
            usuario=self.admin,
        )

    def test_editar_nombre_sin_cambiar_almacen_ok(self):
        actualizar_categoria(
            categoria=self.categoria,
            nombre="Ferretería General",
            almacen_principal=self.almacen_a,
            usuario=self.admin,
        )
        self.categoria.refresh_from_db()
        self.assertEqual(self.categoria.nombre, "Ferretería General")

        aud = Auditoria.objects.filter(tipo_entidad="Categoria", accion="EDITAR_CATEGORIA").first()
        self.assertIsNotNone(aud)
        self.assertEqual(aud.valores_anteriores["nombre"], "Ferretería")
        self.assertEqual(aud.valores_nuevos["nombre"], "Ferretería General")

    def test_cambiar_almacen_sin_productos_actualiza_directamente(self):
        actualizar_categoria(
            categoria=self.categoria,
            nombre="Ferretería",
            almacen_principal=self.almacen_b,
            usuario=self.admin,
        )
        self.categoria.refresh_from_db()
        self.assertEqual(self.categoria.almacen_principal, self.almacen_b)

    def test_cambiar_almacen_con_productos_lanza_necesita_traslado(self):
        # Crear un producto asociado a esta categoría
        Producto.objects.create(
            codigo_interno="FER-0001",
            nombre="Tornillo 1/2",
            categoria=self.categoria,
        )
        with self.assertRaises(NecesitaTraslado):
            actualizar_categoria(
                categoria=self.categoria,
                nombre="Ferretería",
                almacen_principal=self.almacen_b,
                usuario=self.admin,
            )

    def test_usuario_normal_no_puede_editar_categoria_de_otro_almacen(self):
        cat_b = crear_categoria(
            nombre="Robótica",
            almacen_principal=self.almacen_b,
            usuario=self.admin,
        )
        with self.assertRaises(PermissionDenied):
            actualizar_categoria(
                categoria=cat_b,
                nombre="Robótica Avanzada",
                almacen_principal=self.almacen_b,
                usuario=self.usuario_normal,
            )


class TestCategoriaVistas(CategoriaBaseTestCase):
    def setUp(self):
        super().setUp()
        self.cat_a = crear_categoria(
            nombre="Electronica A",
            almacen_principal=self.almacen_a,
            usuario=self.admin,
        )
        self.cat_b = crear_categoria(
            nombre="Quimicos B",
            almacen_principal=self.almacen_b,
            usuario=self.admin,
        )

    def test_lista_requiere_login(self):
        r = self.client.get(reverse("inventario:categoria-lista"))
        self.assertEqual(r.status_code, 302)

    def test_usuario_normal_ve_solo_categorias_de_su_almacen(self):
        self.client.force_login(self.usuario_normal)
        r = self.client.get(reverse("inventario:categoria-lista"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Electronica A")
        self.assertNotContains(r, "Quimicos B")

    def test_admin_ve_todas_las_categorias(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse("inventario:categoria-lista"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Electronica A")
        self.assertContains(r, "Quimicos B")

    def test_usuario_normal_crea_categoria_forzada_a_su_almacen(self):
        self.client.force_login(self.usuario_normal)
        r = self.client.post(
            reverse("inventario:categoria-crear"),
            {"nombre": "Optica", "almacen_principal": self.almacen_b.pk},
        )
        self.assertEqual(r.status_code, 302)
        nueva = Categoria.objects.get(nombre="Optica")
        # El form debe forzar almacen_principal = almacen_asignado del usuario normal
        self.assertEqual(nueva.almacen_principal, self.almacen_a)

    def test_admin_crea_categoria_en_cualquier_almacen(self):
        self.client.force_login(self.admin)
        r = self.client.post(
            reverse("inventario:categoria-crear"),
            {"nombre": "Mecánica", "almacen_principal": self.almacen_b.pk},
        )
        self.assertEqual(r.status_code, 302)
        nueva = Categoria.objects.get(nombre="Mecánica")
        self.assertEqual(nueva.almacen_principal, self.almacen_b)

    def test_editar_categoria_con_productos_captura_necesita_traslado_y_muestra_error(self):
        Producto.objects.create(
            codigo_interno="ELE-0001",
            nombre="Resistencia 10k",
            categoria=self.cat_a,
        )
        self.client.force_login(self.admin)
        r = self.client.post(
            reverse("inventario:categoria-editar", args=[self.cat_a.pk]),
            {"nombre": "Electronica A", "almacen_principal": self.almacen_b.pk},
        )
        # Debe re-renderizar el form con error o redirigir con mensaje
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "requiere trasladar")

    def test_recrear_categoria_eliminada_exitoso(self):
        from inventario.servicios.datos_maestros import eliminar_categoria
        cat_temp = crear_categoria(
            nombre="Temporal Borrar",
            almacen_principal=self.almacen_a,
            usuario=self.admin,
        )
        eliminar_categoria(categoria=cat_temp, usuario=self.admin)
        self.assertTrue(cat_temp.eliminado)

        # Ahora intentar crearla nuevamente por POST en la vista
        self.client.force_login(self.admin)
        r = self.client.post(
            reverse("inventario:categoria-crear"),
            {"nombre": "Temporal Borrar", "almacen_principal": self.almacen_a.pk},
        )
        self.assertEqual(r.status_code, 302)
        cat_temp.refresh_from_db()
        self.assertFalse(cat_temp.eliminado)
        self.assertTrue(cat_temp.activo)

