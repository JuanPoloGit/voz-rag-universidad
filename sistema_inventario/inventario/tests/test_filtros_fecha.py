"""Pruebas exhaustivas para la validación del filtro por fecha (fecha_desde / fecha_hasta)
a través de todas las tablas y vistas del sistema donde está implementado.
"""
from datetime import datetime, date, timedelta
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from django.contrib.auth import get_user_model
from inventario.models import Almacen, Categoria, Ubicacion, Producto, Movimiento, Prestamo, ConteoFisico

User = get_user_model()


class FiltrosFechaTestCase(TestCase):
    """Verificación integral del filtrado por fechas en todas las tablas."""

    @classmethod
    def setUpTestData(cls):
        # 1. Crear usuarios
        cls.admin = User.objects.create_superuser(
            username="admin_test",
            email="admin@test.com",
            password="adminpassword123",
            nombre_completo="Admin Test",
        )

        # 2. Crear almacenes y catálogo base
        cls.almacen = Almacen.objects.create(nombre="Almacén Principal")
        cls.categoria = Categoria.objects.create(
            nombre="Electrónica",
            almacen_principal=cls.almacen,
        )
        cls.ubicacion = Ubicacion.objects.create(
            nombre="Estante A",
            almacen=cls.almacen,
        )
        cls.producto = Producto.objects.create(
            nombre="Multímetro Digital",
            categoria=cls.categoria,
            ubicacion=cls.ubicacion,
            stock_minimo=5,
        )
        cls.producto2 = Producto.objects.create(
            nombre="Osciloscopio Digital",
            categoria=cls.categoria,
            ubicacion=cls.ubicacion,
            stock_minimo=2,
        )

        # Fechas de referencia fijas en la zona horaria del sistema (America/Bogota)
        cls.fecha_hoy = timezone.localdate()
        cls.fecha_ayer = timezone.localdate() - timedelta(days=1)
        cls.fecha_antigua = timezone.localdate() - timedelta(days=10)

        # 3. Crear movimientos en distintas fechas
        # Movimiento 1: Ayer (ENTRADA)
        cls.mov_ayer = Movimiento.objects.create(
            clave_idempotencia="idemp-ayer-1",
            tipo=Movimiento.Tipo.ENTRADA,
            producto=cls.producto,
            cantidad=10,
            almacen_destino=cls.almacen,
            usuario=cls.admin,
            motivo="Entrada de ayer",
            stock_disponible_anterior=0,
            stock_disponible_posterior=10,
        )
        # Forzar fecha_hora de ayer
        Movimiento.objects.filter(pk=cls.mov_ayer.pk).update(
            fecha_hora=timezone.now() - timedelta(days=1)
        )
        cls.mov_ayer.refresh_from_db()

        # Movimiento 2: Hoy (SALIDA / DOTACION)
        cls.mov_hoy_salida = Movimiento.objects.create(
            clave_idempotencia="idemp-hoy-salida",
            tipo=Movimiento.Tipo.SALIDA,
            producto=cls.producto,
            cantidad=2,
            almacen_origen=cls.almacen,
            usuario=cls.admin,
            motivo="Dotación a técnico",
            destinatario="Carlos Técnico",
            stock_disponible_anterior=10,
            stock_disponible_posterior=8,
        )

        # Movimiento 3: Hoy (PRESTAMO)
        cls.mov_hoy_prestamo = Movimiento.objects.create(
            clave_idempotencia="idemp-hoy-prestamo",
            tipo=Movimiento.Tipo.PRESTAMO,
            producto=cls.producto,
            cantidad=1,
            almacen_origen=cls.almacen,
            usuario=cls.admin,
            motivo="Préstamo temporal",
            destinatario="Laboratorio 1",
            stock_disponible_anterior=8,
            stock_disponible_posterior=7,
        )
        cls.prestamo_hoy = Prestamo.objects.create(
            movimiento=cls.mov_hoy_prestamo,
            producto=cls.producto,
            almacen=cls.almacen,
            cantidad_original=1,
            cantidad_pendiente_devolucion=1,
            destinatario="Laboratorio 1",
            usuario=cls.admin,
        )

        # Movimiento 4: Hoy (TRASLADO)
        cls.mov_hoy_traslado = Movimiento.objects.create(
            clave_idempotencia="idemp-hoy-traslado",
            tipo=Movimiento.Tipo.TRASLADO_INTERNO,
            producto=cls.producto,
            cantidad=1,
            almacen_origen=cls.almacen,
            almacen_destino=cls.almacen,
            usuario=cls.admin,
            motivo="Reubicación interna",
            stock_disponible_anterior=7,
            stock_disponible_posterior=7,
        )

        # 4. Crear Conteos Físicos
        # Conteo de ayer
        cls.conteo_ayer = ConteoFisico.objects.create(
            producto=cls.producto,
            almacen=cls.almacen,
            cantidad_contada=10,
            stock_sistema=10,
            diferencia=0,
            usuario_conteo=cls.admin,
        )
        ConteoFisico.objects.filter(pk=cls.conteo_ayer.pk).update(
            fecha_conteo=timezone.now() - timedelta(days=1)
        )
        cls.conteo_ayer.refresh_from_db()

        # Conteo de hoy
        cls.conteo_hoy = ConteoFisico.objects.create(
            producto=cls.producto2,
            almacen=cls.almacen,
            cantidad_contada=8,
            stock_sistema=7,
            diferencia=1,
            usuario_conteo=cls.admin,
        )

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.admin)

    def test_filtro_fecha_kardex_general(self):
        """Verifica que el Kardex general filtra correctamente por rango de fechas."""
        url = reverse("inventario:movimiento-lista")
        
        # Filtrar solo hoy: debe incluir los 3 de hoy, pero no el de ayer
        resp_hoy = self.client.get(url, {
            "vista": "MOVIMIENTOS",
            "fecha_desde": self.fecha_hoy.strftime("%Y-%m-%d"),
            "fecha_hasta": self.fecha_hoy.strftime("%Y-%m-%d"),
        })
        self.assertEqual(resp_hoy.status_code, 200)
        pks_hoy = [m.pk for m in resp_hoy.context["movimientos"]]
        self.assertIn(self.mov_hoy_salida.pk, pks_hoy)
        self.assertIn(self.mov_hoy_prestamo.pk, pks_hoy)
        self.assertIn(self.mov_hoy_traslado.pk, pks_hoy)
        self.assertNotIn(self.mov_ayer.pk, pks_hoy)

        # Filtrar solo ayer: debe incluir únicamente el de ayer
        resp_ayer = self.client.get(url, {
            "vista": "MOVIMIENTOS",
            "fecha_desde": self.fecha_ayer.strftime("%Y-%m-%d"),
            "fecha_hasta": self.fecha_ayer.strftime("%Y-%m-%d"),
        })
        self.assertEqual(resp_ayer.status_code, 200)
        pks_ayer = [m.pk for m in resp_ayer.context["movimientos"]]
        self.assertIn(self.mov_ayer.pk, pks_ayer)
        self.assertNotIn(self.mov_hoy_salida.pk, pks_ayer)

    def test_filtro_fecha_pestaña_entradas(self):
        """Verifica el filtrado por fecha en la pestaña de Entradas."""
        url = reverse("inventario:movimiento-lista")
        
        # En la pestaña ENTRADA, ayer hubo 1 y hoy hubo 0
        resp_ayer = self.client.get(url, {
            "vista": "ENTRADA",
            "fecha_desde": self.fecha_ayer.strftime("%Y-%m-%d"),
            "fecha_hasta": self.fecha_ayer.strftime("%Y-%m-%d"),
        })
        self.assertEqual(resp_ayer.status_code, 200)
        self.assertEqual(len(resp_ayer.context["movimientos"]), 1)
        self.assertEqual(resp_ayer.context["movimientos"][0].pk, self.mov_ayer.pk)

        resp_hoy = self.client.get(url, {
            "vista": "ENTRADA",
            "fecha_desde": self.fecha_hoy.strftime("%Y-%m-%d"),
            "fecha_hasta": self.fecha_hoy.strftime("%Y-%m-%d"),
        })
        self.assertEqual(resp_hoy.status_code, 200)
        self.assertEqual(len(resp_hoy.context["movimientos"]), 0)

    def test_filtro_fecha_pestaña_salidas(self):
        """Verifica el filtrado por fecha en la pestaña de Salidas."""
        url = reverse("inventario:movimiento-lista")
        
        # En SALIDA hoy están mov_hoy_salida y mov_hoy_prestamo
        resp_hoy = self.client.get(url, {
            "vista": "SALIDA",
            "fecha_desde": self.fecha_hoy.strftime("%Y-%m-%d"),
            "fecha_hasta": self.fecha_hoy.strftime("%Y-%m-%d"),
        })
        self.assertEqual(resp_hoy.status_code, 200)
        pks = [m.pk for m in resp_hoy.context["movimientos"]]
        self.assertIn(self.mov_hoy_salida.pk, pks)
        self.assertNotIn(self.mov_ayer.pk, pks)

    def test_filtro_fecha_pestaña_dotacion(self):
        """Verifica el filtrado por fecha en la pestaña de Dotación."""
        url = reverse("inventario:movimiento-lista")
        
        resp_hoy = self.client.get(url, {
            "vista": "DOTACION",
            "fecha_desde": self.fecha_hoy.strftime("%Y-%m-%d"),
            "fecha_hasta": self.fecha_hoy.strftime("%Y-%m-%d"),
        })
        self.assertEqual(resp_hoy.status_code, 200)
        pks = [m.pk for m in resp_hoy.context["movimientos"]]
        self.assertIn(self.mov_hoy_salida.pk, pks)

    def test_filtro_fecha_pestaña_traslados(self):
        """Verifica el filtrado por fecha en la pestaña de Traslados."""
        url = reverse("inventario:movimiento-lista")
        
        resp_hoy = self.client.get(url, {
            "vista": "TRASLADO",
            "fecha_desde": self.fecha_hoy.strftime("%Y-%m-%d"),
            "fecha_hasta": self.fecha_hoy.strftime("%Y-%m-%d"),
        })
        self.assertEqual(resp_hoy.status_code, 200)
        pks = [m.pk for m in resp_hoy.context["movimientos"]]
        self.assertIn(self.mov_hoy_traslado.pk, pks)

        resp_ayer = self.client.get(url, {
            "vista": "TRASLADO",
            "fecha_desde": self.fecha_ayer.strftime("%Y-%m-%d"),
            "fecha_hasta": self.fecha_ayer.strftime("%Y-%m-%d"),
        })
        self.assertEqual(len(resp_ayer.context["movimientos"]), 0)

    def test_filtro_fecha_pestaña_prestamos(self):
        """Verifica el filtrado por fecha en la pestaña de Préstamos."""
        url = reverse("inventario:movimiento-lista")
        
        resp_hoy = self.client.get(url, {
            "vista": "PRESTAMOS",
            "fecha_desde": self.fecha_hoy.strftime("%Y-%m-%d"),
            "fecha_hasta": self.fecha_hoy.strftime("%Y-%m-%d"),
        })
        self.assertEqual(resp_hoy.status_code, 200)
        prestamos = resp_hoy.context["prestamos_lista"]
        self.assertEqual(len(prestamos), 1)
        self.assertEqual(prestamos[0].pk, self.prestamo_hoy.pk)

        # Filtrar fecha antigua donde no hay préstamos
        resp_antigua = self.client.get(url, {
            "vista": "PRESTAMOS",
            "fecha_desde": self.fecha_antigua.strftime("%Y-%m-%d"),
            "fecha_hasta": self.fecha_antigua.strftime("%Y-%m-%d"),
        })
        self.assertEqual(resp_antigua.status_code, 200)
        self.assertEqual(len(resp_antigua.context["prestamos_lista"]), 0)

    def test_filtro_fecha_pestaña_conteos(self):
        """Verifica el filtrado por fecha en la pestaña de Conteos Físicos."""
        url = reverse("inventario:movimiento-lista")
        
        resp_hoy = self.client.get(url, {
            "vista": "CONTEOS",
            "fecha_desde": self.fecha_hoy.strftime("%Y-%m-%d"),
            "fecha_hasta": self.fecha_hoy.strftime("%Y-%m-%d"),
        })
        self.assertEqual(resp_hoy.status_code, 200)
        conteos = resp_hoy.context["conteos_lista"]
        self.assertEqual(len(conteos), 1)
        self.assertEqual(conteos[0].pk, self.conteo_hoy.pk)

        resp_ayer = self.client.get(url, {
            "vista": "CONTEOS",
            "fecha_desde": self.fecha_ayer.strftime("%Y-%m-%d"),
            "fecha_hasta": self.fecha_ayer.strftime("%Y-%m-%d"),
        })
        self.assertEqual(resp_ayer.status_code, 200)
        conteos_ayer = resp_ayer.context["conteos_lista"]
        self.assertEqual(len(conteos_ayer), 1)
        self.assertEqual(conteos_ayer[0].pk, self.conteo_ayer.pk)

    def test_filtro_fecha_reporte_historico_movimientos(self):
        """Verifica el filtrado en la vista de Reporte de Movimientos."""
        url = reverse("web:reporte-movimientos")
        
        resp_hoy = self.client.get(url, {
            "fecha_desde": self.fecha_hoy.strftime("%Y-%m-%d"),
            "fecha_hasta": self.fecha_hoy.strftime("%Y-%m-%d"),
        })
        self.assertEqual(resp_hoy.status_code, 200)
        movs = resp_hoy.context["movimientos"]
        self.assertEqual(movs.count(), 3)

        resp_ayer = self.client.get(url, {
            "fecha_desde": self.fecha_ayer.strftime("%Y-%m-%d"),
            "fecha_hasta": self.fecha_ayer.strftime("%Y-%m-%d"),
        })
        self.assertEqual(resp_ayer.status_code, 200)
        movs_ayer = resp_ayer.context["movimientos"]
        self.assertEqual(movs_ayer.count(), 1)
        self.assertEqual(movs_ayer.first().pk, self.mov_ayer.pk)

    def test_filtro_fecha_exportaciones_csv_y_excel(self):
        """Verifica el filtrado por fechas en descargas CSV y Excel de movimientos."""
        url_csv = reverse("web:exportar-datos-csv", kwargs={"tipo": "movimientos"})
        url_excel = reverse("web:exportar-datos-excel", kwargs={"tipo": "movimientos"})
        
        params = {
            "fecha_desde": self.fecha_hoy.strftime("%Y-%m-%d"),
            "fecha_hasta": self.fecha_hoy.strftime("%Y-%m-%d"),
        }
        
        resp_csv = self.client.get(url_csv, params)
        self.assertEqual(resp_csv.status_code, 200)
        self.assertIn("text/csv", resp_csv["Content-Type"])

        resp_excel = self.client.get(url_excel, params)
        self.assertEqual(resp_excel.status_code, 200)
        self.assertIn("spreadsheetml", resp_excel["Content-Type"])

    def test_fechas_invalidas_no_rompen_el_sistema(self):
        """Verifica que fechas con formato inválido se manejen con gracia sin error 500."""
        url = reverse("inventario:movimiento-lista")
        
        resp = self.client.get(url, {
            "vista": "MOVIMIENTOS",
            "fecha_desde": "fecha-invalida",
            "fecha_hasta": "2026-99-99",
        })
        self.assertEqual(resp.status_code, 200)
        # Debe ignorar los valores inválidos y no lanzar excepción

    def test_rango_fechas_invertido_devuelve_cero_sin_error(self):
        """Verifica que si fecha_desde > fecha_hasta el sistema responde HTTP 200 con 0 resultados."""
        url = reverse("inventario:movimiento-lista")
        
        resp = self.client.get(url, {
            "vista": "MOVIMIENTOS",
            "fecha_desde": self.fecha_hoy.strftime("%Y-%m-%d"),
            "fecha_hasta": self.fecha_ayer.strftime("%Y-%m-%d"),
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.context["movimientos"]), 0)
