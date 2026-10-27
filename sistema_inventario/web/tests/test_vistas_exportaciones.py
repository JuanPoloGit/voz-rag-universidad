"""
Pruebas de autorización y vistas del Centro de Exportaciones (Excel / CSV).
"""

from io import BytesIO
import openpyxl
from django.test import TestCase
from django.urls import reverse

from cuentas.models import UsuarioAudacIA
from inventario.models import Almacen, Categoria, Existencia, Movimiento, Prestamo, Producto


class VistasExportacionesTest(TestCase):
    def setUp(self):
        self.almacen = Almacen.objects.create(nombre="Almacén Export Vistas")
        self.categoria = Categoria.objects.create(nombre="Herramientas", almacen_principal=self.almacen)

        self.superadmin = UsuarioAudacIA.objects.create_superuser(
            username="super_exp_vistas",
            nombre_completo="Super Administrador Vistas",
            email="super_exp_vistas@audacia.edu.co",
            password="Password123!",
            rol=UsuarioAudacIA.Rol.SUPERUSUARIO,
        )

        self.admin = UsuarioAudacIA.objects.create_user(
            username="admin_exp_vistas",
            nombre_completo="Administrador Vistas",
            email="admin_exp_vistas@audacia.edu.co",
            password="Password123!",
            rol=UsuarioAudacIA.Rol.ADMINISTRADOR,
        )

        self.operador = UsuarioAudacIA.objects.create_user(
            username="operador_exp_vistas",
            nombre_completo="Operador Vistas",
            email="operador_exp_vistas@audacia.edu.co",
            password="Password123!",
            rol=UsuarioAudacIA.Rol.USUARIO,
            almacen_asignado=self.almacen,
        )

        self.producto = Producto.objects.create(
            codigo_interno="HERR-001",
            nombre="Taladro Percutor",
            categoria=self.categoria,
            stock_minimo=3,
        )

        self.existencia = Existencia.objects.create(
            producto=self.producto,
            almacen=self.almacen,
            stock_disponible=8,
            stock_en_prestamo=1,
        )

        self.movimiento = Movimiento.objects.create(
            clave_idempotencia="IDEM-TEST-EXP-VISTAS-01",
            tipo=Movimiento.Tipo.ENTRADA,
            producto=self.producto,
            almacen_destino=self.almacen,
            cantidad=8,
            stock_disponible_anterior=0,
            stock_disponible_posterior=8,
            usuario=self.admin,
            motivo="Entrada de prueba",
        )

        self.prestamo = Prestamo.objects.create(
            producto=self.producto,
            almacen=self.almacen,
            movimiento=self.movimiento,
            usuario=self.admin,
            cantidad_original=1,
            cantidad_pendiente_devolucion=1,
            destinatario="Taller Central",
            estado=Prestamo.Estado.ABIERTO,
        )

    def test_centro_exportaciones_control_acceso(self):
        url = reverse("web:centro-exportaciones")

        # 1. Superadmin tiene acceso: 200 OK
        self.client.force_login(self.superadmin)
        resp_super = self.client.get(url)
        self.assertEqual(resp_super.status_code, 200)
        self.assertContains(resp_super, "Centro de Exportaciones")

        # 2. Administrador tiene acceso: 200 OK
        self.client.force_login(self.admin)
        resp_admin = self.client.get(url)
        self.assertEqual(resp_admin.status_code, 200)

        # 3. Operador normal NO tiene acceso: 403 Forbidden
        self.client.force_login(self.operador)
        resp_op = self.client.get(url)
        self.assertEqual(resp_op.status_code, 403)

    def test_descarga_dashboard_excel_view(self):
        url = reverse("web:exportar-dashboard-excel")

        # 1. Operador: 403 Forbidden
        self.client.force_login(self.operador)
        resp_op = self.client.get(url)
        self.assertEqual(resp_op.status_code, 403)

        # 2. Admin: 200 OK con libro Excel
        self.client.force_login(self.admin)
        resp_admin = self.client.get(url)
        self.assertEqual(resp_admin.status_code, 200)
        self.assertIn("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", resp_admin["Content-Type"])
        self.assertIn("attachment; filename=", resp_admin["Content-Disposition"])

        wb = openpyxl.load_workbook(BytesIO(resp_admin.content))
        self.assertEqual(len(wb.sheetnames), 4)

    def test_descarga_datos_excel_view(self):
        self.client.force_login(self.admin)

        # 1. Existencias
        url_ex = reverse("web:exportar-datos-excel", kwargs={"tipo": "existencias"})
        resp_ex = self.client.get(url_ex)
        self.assertEqual(resp_ex.status_code, 200)

        # 2. Movimientos
        url_mov = reverse("web:exportar-datos-excel", kwargs={"tipo": "movimientos"})
        resp_mov = self.client.get(url_mov)
        self.assertEqual(resp_mov.status_code, 200)

        # 3. Préstamos
        url_pres = reverse("web:exportar-datos-excel", kwargs={"tipo": "prestamos"})
        resp_pres = self.client.get(url_pres)
        self.assertEqual(resp_pres.status_code, 200)

        # 4. Tipo inválido -> 404
        url_invalido = reverse("web:exportar-datos-excel", kwargs={"tipo": "invalido"})
        resp_inv = self.client.get(url_invalido)
        self.assertEqual(resp_inv.status_code, 404)

    def test_descarga_datos_csv_view(self):
        self.client.force_login(self.admin)

        # 1. Dashboard CSV
        url_dash = reverse("web:exportar-datos-csv", kwargs={"tipo": "dashboard"})
        resp_dash = self.client.get(url_dash)
        self.assertEqual(resp_dash.status_code, 200)
        self.assertIn("text/csv", resp_dash["Content-Type"])
        self.assertTrue(resp_dash.content.startswith(b"\xef\xbb\xbf"))

        # 2. Existencias CSV
        url_ex = reverse("web:exportar-datos-csv", kwargs={"tipo": "existencias"})
        resp_ex = self.client.get(url_ex)
        self.assertEqual(resp_ex.status_code, 200)

        # 3. Movimientos CSV
        url_mov = reverse("web:exportar-datos-csv", kwargs={"tipo": "movimientos"})
        resp_mov = self.client.get(url_mov)
        self.assertEqual(resp_mov.status_code, 200)

        # 4. Préstamos CSV
        url_pres = reverse("web:exportar-datos-csv", kwargs={"tipo": "prestamos"})
        resp_pres = self.client.get(url_pres)
        self.assertEqual(resp_pres.status_code, 200)
