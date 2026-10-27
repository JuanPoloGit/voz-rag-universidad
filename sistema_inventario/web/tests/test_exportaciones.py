"""
Pruebas de los servicios de exportación a Excel y CSV (web/servicios/excel_export.py y csv_export.py).
"""

from io import BytesIO
import openpyxl
from django.test import TestCase

from cuentas.models import UsuarioAudacIA
from inventario.models import Almacen, Categoria, Existencia, Movimiento, Prestamo, Producto
from web.servicios.csv_export import (
    exportar_existencias_csv,
    exportar_inventario_completo_csv,
    exportar_movimientos_csv,
    exportar_prestamos_csv,
    exportar_resumen_dashboard_csv,
)
from web.servicios.excel_export import (
    exportar_dashboard_excel,
    exportar_existencias_excel,
    exportar_movimientos_excel,
    exportar_prestamos_excel,
)
from web.servicios.reportes import (
    obtener_metricas_dashboard,
    obtener_reporte_existencias,
    obtener_reporte_movimientos_periodo,
    obtener_reporte_prestamos_activos,
)


class ServiciosExportacionTest(TestCase):
    def setUp(self):
        self.almacen = Almacen.objects.create(nombre="Almacén Pruebas")
        self.categoria = Categoria.objects.create(nombre="Electrónica", almacen_principal=self.almacen)

        self.admin = UsuarioAudacIA.objects.create_user(
            username="admin_exp_test",
            nombre_completo="Admin Export Test",
            email="admin_exp@audacia.edu.co",
            password="Password123!",
            rol=UsuarioAudacIA.Rol.ADMINISTRADOR,
        )

        self.producto = Producto.objects.create(
            codigo_interno="PROD-EXP-001",
            nombre="Osciloscopio Digital",
            categoria=self.categoria,
            stock_minimo=5,
        )

        self.existencia = Existencia.objects.create(
            producto=self.producto,
            almacen=self.almacen,
            stock_disponible=10,
            stock_en_prestamo=2,
        )

        self.movimiento = Movimiento.objects.create(
            clave_idempotencia="IDEM-TEST-EXP-001",
            tipo=Movimiento.Tipo.ENTRADA,
            producto=self.producto,
            almacen_destino=self.almacen,
            cantidad=10,
            stock_disponible_anterior=0,
            stock_disponible_posterior=10,
            usuario=self.admin,
            motivo="Dotación inicial",
        )

        self.prestamo = Prestamo.objects.create(
            producto=self.producto,
            almacen=self.almacen,
            movimiento=self.movimiento,
            usuario=self.admin,
            cantidad_original=2,
            cantidad_pendiente_devolucion=2,
            destinatario="Profesor Investigador",
            estado=Prestamo.Estado.ABIERTO,
        )

    def test_exportar_dashboard_excel_profesional(self):
        buffer = exportar_dashboard_excel(self.admin, almacen=self.almacen)
        self.assertIsInstance(buffer, BytesIO)
        self.assertGreater(len(buffer.getvalue()), 0)

        wb = openpyxl.load_workbook(buffer)
        nombres_hojas = wb.sheetnames
        self.assertEqual(len(nombres_hojas), 4)
        self.assertIn("Resumen Ejecutivo", nombres_hojas)
        self.assertIn("Existencias y Stock", nombres_hojas)
        self.assertIn("Últimos Movimientos", nombres_hojas)
        self.assertIn("Préstamos Pendientes", nombres_hojas)

        # Verificar gráficos en Resumen Ejecutivo
        ws_resumen = wb["Resumen Ejecutivo"]
        self.assertGreaterEqual(len(ws_resumen._charts), 1)

        # Verificar bordes en celdas KPI combinadas (col 1 y col 2 en fila 9)
        celda_kpi_1 = ws_resumen.cell(row=9, column=1)
        celda_kpi_2 = ws_resumen.cell(row=9, column=2)
        self.assertIsNotNone(celda_kpi_1.border.top.style)
        self.assertIsNotNone(celda_kpi_2.border.top.style)

        # Verificar hoja de existencias
        ws_ex = wb["Existencias y Stock"]
        self.assertEqual(ws_ex.cell(row=4, column=1).value, "Código Interno")
        self.assertEqual(ws_ex.cell(row=5, column=1).value, "PROD-EXP-001")
        self.assertEqual(ws_ex.cell(row=5, column=5).value, 10)

    def test_exportar_hojas_individuales_excel(self):
        # Existencias
        qs_ex = obtener_reporte_existencias(self.admin)
        buf_ex = exportar_existencias_excel(qs_ex)
        wb_ex = openpyxl.load_workbook(buf_ex)
        self.assertEqual(wb_ex.active.title, "Existencias")

        # Movimientos
        qs_mov = obtener_reporte_movimientos_periodo(self.admin)
        buf_mov = exportar_movimientos_excel(qs_mov)
        wb_mov = openpyxl.load_workbook(buf_mov)
        self.assertEqual(wb_mov.active.title, "Kardex de Movimientos")

        # Préstamos
        qs_pres = obtener_reporte_prestamos_activos(self.admin)
        buf_pres = exportar_prestamos_excel(qs_pres)
        wb_pres = openpyxl.load_workbook(buf_pres)
        self.assertEqual(wb_pres.active.title, "Préstamos")

    def test_exportar_csv_con_bom(self):
        # 1. Existencias CSV
        qs_ex = obtener_reporte_existencias(self.admin)
        resp_ex = exportar_existencias_csv(qs_ex)
        self.assertEqual(resp_ex.status_code, 200)
        self.assertIn("text/csv", resp_ex["Content-Type"])
        self.assertTrue(resp_ex.content.startswith(b"\xef\xbb\xbf"))  # UTF-8 BOM
        self.assertIn(b"PROD-EXP-001", resp_ex.content)

        # 2. Movimientos CSV
        qs_mov = obtener_reporte_movimientos_periodo(self.admin)
        resp_mov = exportar_movimientos_csv(qs_mov)
        self.assertTrue(resp_mov.content.startswith(b"\xef\xbb\xbf"))
        self.assertIn(b"Dotaci\xc3\xb3n inicial", resp_mov.content)

        # 3. Préstamos CSV
        qs_pres = obtener_reporte_prestamos_activos(self.admin)
        resp_pres = exportar_prestamos_csv(qs_pres)
        self.assertTrue(resp_pres.content.startswith(b"\xef\xbb\xbf"))
        self.assertIn(b"Profesor Investigador", resp_pres.content)

        # 4. Inventario Completo CSV (Dashboard)
        resp_full = exportar_inventario_completo_csv(self.admin, almacen=self.almacen)
        self.assertTrue(resp_full.content.startswith(b"\xef\xbb\xbf"))
        self.assertIn(b"PROD-EXP-001", resp_full.content)
        self.assertIn(b"Osciloscopio Digital", resp_full.content)
        self.assertIn(b"Almac\xc3\xa9n Pruebas", resp_full.content)

        # 5. Resumen Dashboard CSV
        metricas = obtener_metricas_dashboard(self.admin)
        resp_dash = exportar_resumen_dashboard_csv(metricas, usuario=self.admin)
        self.assertTrue(resp_dash.content.startswith(b"\xef\xbb\xbf"))
        self.assertIn(b"Total Productos en Cat\xc3\xa1logo", resp_dash.content)

    def test_consolidado_general_csv_sin_duplicados(self):
        """Valida que un producto con existencias en múltiples almacenes no se duplique en el consolidado general."""
        alm2 = Almacen.objects.create(nombre="Almacén Secundario")
        Existencia.objects.create(
            producto=self.producto,
            almacen=alm2,
            stock_disponible=5,
            stock_en_prestamo=1,
        )
        self.assertEqual(Existencia.objects.filter(producto=self.producto).count(), 2)
        self.assertEqual(Producto.objects.filter(activo=True, archivado=False).count(), 1)

        # Consolidado General (almacen=None)
        resp = exportar_inventario_completo_csv(self.admin, almacen=None)
        self.assertEqual(resp.status_code, 200)
        lineas = resp.content.decode("utf-8-sig").strip().split("\r\n")
        # 1 fila cabecera + exactamente 1 fila del producto consolidado
        self.assertEqual(len(lineas), 2)
        fila_prod = lineas[1].split(";")
        self.assertEqual(fila_prod[0], "PROD-EXP-001")
        # Stock disponible consolidado: 10 + 5 = 15
        self.assertEqual(fila_prod[6], "15")
        # Stock en prestamo consolidado: 2 + 1 = 3
        self.assertEqual(fila_prod[7], "3")
        # Stock total consolidado: 15 + 3 = 18
        self.assertEqual(fila_prod[8], "18")

    def test_prestamos_filtro_historico_incluye_cerrados(self):
        """Valida que la exportación de préstamos con histórico completo incluya préstamos en estado CERRADO."""
        mov2 = Movimiento.objects.create(
            clave_idempotencia="IDEM-TEST-EXP-002",
            tipo=Movimiento.Tipo.PRESTAMO,
            producto=self.producto,
            almacen_origen=self.almacen,
            cantidad=3,
            stock_disponible_anterior=10,
            stock_disponible_posterior=7,
            usuario=self.admin,
            motivo="Préstamo cerrado previo",
        )
        prestamo_cerrado = Prestamo.objects.create(
            producto=self.producto,
            almacen=self.almacen,
            movimiento=mov2,
            usuario=self.admin,
            cantidad_original=3,
            cantidad_pendiente_devolucion=0,
            destinatario="Estudiante Graduado",
            estado=Prestamo.Estado.CERRADO,
        )

        # Por defecto (activos únicamente)
        activos = obtener_reporte_prestamos_activos(self.admin, incluir_cerrados=False)
        self.assertNotIn(prestamo_cerrado, activos)

        # Con histórico completo (incluir_cerrados=True o estado="TODOS")
        todos = obtener_reporte_prestamos_activos(self.admin, incluir_cerrados=True)
        self.assertIn(prestamo_cerrado, todos)

        todos_por_estado = obtener_reporte_prestamos_activos(self.admin, estado="TODOS")
        self.assertIn(prestamo_cerrado, todos_por_estado)

        # En exportación CSV
        resp_csv = exportar_prestamos_csv(todos)
        self.assertIn(b"Estudiante Graduado", resp_csv.content)
        self.assertIn(b"Cerrado", resp_csv.content)
