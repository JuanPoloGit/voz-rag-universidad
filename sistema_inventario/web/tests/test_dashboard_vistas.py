"""Pruebas funcionales e integración para el Dashboard y Reportes de la app web."""

from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model

from cuentas.models import UsuarioAudacIA
from inventario.models import Almacen, Categoria, Existencia, Producto
from inventario.servicios.operaciones import registrar_entrada

User = get_user_model()


class DashboardVistasTest(TestCase):
    def setUp(self):
        self.almacen = Almacen.objects.create(nombre="Almacén Web", activo=True)
        self.user = User.objects.create_user(
            username="user_web",
            password="Password123",
            nombre_completo="Usuario Web",
            rol=UsuarioAudacIA.Rol.USUARIO,
            almacen_asignado=self.almacen,
        )
        self.cat = Categoria.objects.create(
            nombre="Cómputo",
            almacen_principal=self.almacen,
        )
        self.prod = Producto.objects.create(
            nombre="Teclado USB",
            categoria=self.cat,
            stock_minimo=2,
        )
        registrar_entrada(
            producto=self.prod,
            almacen=self.almacen,
            cantidad=5,
            usuario=self.user,
            clave_idempotencia="STOCK-WEB-INI",
        )

    def test_dashboard_view_requiere_login(self):
        url = reverse("web:dashboard")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 302)

    def test_dashboard_view_autenticado(self):
        self.client.force_login(self.user)
        url = reverse("web:dashboard")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "web/dashboard.html")
        self.assertIn("metricas", resp.context)
        self.assertEqual(resp.context["metricas"]["total_productos"], 1)

    def test_grafico_categorias_json(self):
        self.client.force_login(self.user)
        url = reverse("web:grafico-categorias")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("labels", data)
        self.assertIn("data", data)
        self.assertIn("Cómputo", data["labels"])

    def test_grafico_balance_json(self):
        self.client.force_login(self.user)
        url = reverse("web:grafico-balance")
        resp = self.client.get(url, {"dias": 7})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("labels", data)
        self.assertIn("entradas", data)
        self.assertIn("salidas", data)

    def test_reporte_existencias_view(self):
        self.client.force_login(self.user)
        url = reverse("web:reporte-existencias")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "web/reporte_existencias.html")
        self.assertIn("existencias", resp.context)

    def test_reporte_movimientos_view(self):
        self.client.force_login(self.user)
        url = reverse("web:reporte-movimientos")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "web/reporte_movimientos.html")

    def test_reporte_prestamos_view(self):
        self.client.force_login(self.user)
        url = reverse("web:reporte-prestamos")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "web/reporte_prestamos.html")

    def test_reporte_estancados_view(self):
        self.client.force_login(self.user)
        url = reverse("web:reporte-estancados")
        resp = self.client.get(url, {"dias": 30})
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "web/reporte_estancados.html")
