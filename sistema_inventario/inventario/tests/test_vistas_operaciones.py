"""Pruebas unitarias para las vistas web y controladores de movimientos, préstamos, traslados y conteos."""

from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model

from cuentas.models import UsuarioAudacIA
from inventario.models import Almacen, Categoria, ConteoFisico, Existencia, Movimiento, Prestamo, Producto, Ubicacion
from inventario.servicios.operaciones import registrar_entrada, registrar_prestamo

User = get_user_model()


class VistasOperacionesTest(TestCase):
    def setUp(self):
        self.almacen_a = Almacen.objects.create(nombre="Almacén A", activo=True)
        self.almacen_b = Almacen.objects.create(nombre="Almacén B", activo=True)

        self.ubicacion_a = Ubicacion.objects.create(nombre="Estante Central", almacen=self.almacen_a)

        self.admin = User.objects.create_user(
            username="admin_ops",
            password="Password123",
            nombre_completo="Admin Operaciones",
            rol=UsuarioAudacIA.Rol.ADMINISTRADOR,
        )
        self.usuario_a = User.objects.create_user(
            username="user_ops_a",
            password="Password123",
            nombre_completo="Operador Almacén A",
            rol=UsuarioAudacIA.Rol.USUARIO,
            almacen_asignado=self.almacen_a,
        )

        self.cat_a = Categoria.objects.create(nombre="Materiales", almacen_principal=self.almacen_a)
        self.producto = Producto.objects.create(
            nombre="Tester LAN",
            categoria=self.cat_a,
            stock_minimo=2,
            nombre_empaque="Pack",
            unidades_por_empaque=5,
        )

        registrar_entrada(
            producto=self.producto,
            almacen=self.almacen_a,
            cantidad=10,
            usuario=self.usuario_a,
            clave_idempotencia="ENTRADA-VIEW-TEST",
        )

    def test_alertas_lista_view_get(self):
        self.client.force_login(self.usuario_a)
        url = reverse("inventario:alertas-lista")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "inventario/alertas_lista.html")

    def test_alertas_json_view_get(self):
        self.client.force_login(self.usuario_a)
        url = reverse("inventario:alertas-json")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("total_alertas", data)

    def test_movimiento_lista_view_get(self):
        self.client.force_login(self.usuario_a)
        url = reverse("inventario:movimiento-lista")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "inventario/movimiento_lista.html")

    def test_entrada_crear_view_post_unidades(self):
        self.client.force_login(self.usuario_a)
        url = reverse("inventario:movimiento-entrada")
        response = self.client.post(url, {
            "producto": self.producto.id,
            "almacen": self.almacen_a.id,
            "cantidad": 5,
            "motivo": "Compra adicional",
        })
        self.assertRedirects(response, reverse("inventario:movimiento-lista"))
        ex = Existencia.objects.get(producto=self.producto, almacen=self.almacen_a)
        self.assertEqual(ex.stock_disponible, 15)

    def test_salida_crear_view_post(self):
        self.client.force_login(self.usuario_a)
        url = reverse("inventario:movimiento-salida")
        response = self.client.post(url, {
            "producto": self.producto.id,
            "almacen": self.almacen_a.id,
            "cantidad": 3,
            "destinatario": "Ing. Morales",
            "motivo": "Uso en laboratorio",
        })
        self.assertRedirects(response, reverse("inventario:movimiento-lista"))
        ex = Existencia.objects.get(producto=self.producto, almacen=self.almacen_a)
        self.assertEqual(ex.stock_disponible, 7)

    def test_prestamo_crear_y_devolver_view(self):
        self.client.force_login(self.usuario_a)
        # 1. Crear préstamo
        url_crear = reverse("inventario:prestamo-crear")
        resp_crear = self.client.post(url_crear, {
            "producto": self.producto.id,
            "almacen": self.almacen_a.id,
            "cantidad": 2,
            "destinatario": "Estudiante Prueba",
            "motivo": "Proyecto final",
        })
        self.assertRedirects(resp_crear, reverse("inventario:prestamo-lista"))
        prestamo = Prestamo.objects.get(destinatario="Estudiante Prueba")
        self.assertEqual(prestamo.cantidad_pendiente_devolucion, 2)

        # 2. Devolución parcial de 1 unidad
        url_dev = reverse("inventario:prestamo-devolver", kwargs={"pk": prestamo.pk})
        resp_dev = self.client.post(url_dev, {
            "cantidad": 1,
            "motivo": "Devolución de 1 probador",
        })
        self.assertRedirects(resp_dev, reverse("inventario:prestamo-lista"))
        prestamo.refresh_from_db()
        self.assertEqual(prestamo.cantidad_pendiente_devolucion, 1)
        self.assertEqual(prestamo.estado, Prestamo.Estado.DEVUELTO_PARCIAL)

    def test_conteo_fisico_crear_y_confirmar_view(self):
        # 1. Operador crea conteo físico
        self.client.force_login(self.usuario_a)
        url_crear = reverse("inventario:conteo-crear")
        resp_crear = self.client.post(url_crear, {
            "producto": self.producto.id,
            "almacen": self.almacen_a.id,
            "cantidad_contada": 12,
            "motivo": "Auditoría física",
        })
        self.assertRedirects(resp_crear, reverse("inventario:conteo-lista"))
        conteo = ConteoFisico.objects.first()
        self.assertFalse(conteo.confirmado)

        # 2. Operador normal no puede confirmar
        url_conf = reverse("inventario:conteo-confirmar", kwargs={"pk": conteo.pk})
        resp_forbid = self.client.post(url_conf)
        self.assertEqual(resp_forbid.status_code, 403)

        # 3. Admin confirma
        self.client.force_login(self.admin)
        resp_admin = self.client.post(url_conf)
        self.assertRedirects(resp_admin, reverse("inventario:conteo-lista"))
        conteo.refresh_from_db()
        self.assertTrue(conteo.confirmado)
