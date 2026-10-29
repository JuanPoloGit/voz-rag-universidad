"""Pruebas unitarias para los servicios analíticos y de reportes de la app web."""

from django.test import TestCase
from django.contrib.auth import get_user_model

from cuentas.models import UsuarioAudacIA
from inventario.models import Almacen, Categoria, Existencia, Movimiento, Prestamo, Producto
from inventario.servicios.operaciones import registrar_entrada, registrar_prestamo, registrar_salida
from web.servicios.reportes import (
    obtener_datos_grafico_balance_movimientos,
    obtener_datos_grafico_categorias,
    obtener_metricas_dashboard,
    obtener_reporte_existencias,
    obtener_reporte_movimientos_periodo,
    obtener_reporte_prestamos_activos,
    obtener_reporte_productos_estancados,
)

User = get_user_model()


class ReportesServiciosTest(TestCase):
    def setUp(self):
        self.almacen_a = Almacen.objects.create(nombre="Almacén Alpha", activo=True)
        self.almacen_b = Almacen.objects.create(nombre="Almacén Beta", activo=True)

        self.admin = User.objects.create_user(
            username="admin_rep",
            password="Password123",
            nombre_completo="Administrador Reportes",
            rol=UsuarioAudacIA.Rol.ADMINISTRADOR,
        )
        self.user_a = User.objects.create_user(
            username="user_rep_a",
            password="Password123",
            nombre_completo="Operador Almacén Alpha",
            rol=UsuarioAudacIA.Rol.USUARIO,
            almacen_asignado=self.almacen_a,
        )

        self.cat_redes = Categoria.objects.create(nombre="Redes", almacen_principal=self.almacen_a)
        self.cat_herramientas = Categoria.objects.create(nombre="Herramientas", almacen_principal=self.almacen_a)
        self.cat_beta = Categoria.objects.create(nombre="BetaCat", almacen_principal=self.almacen_b)

        # 1. Producto normal en Alpha
        self.prod_router = Producto.objects.create(
            nombre="Router Gigabit",
            categoria=self.cat_redes,
            stock_minimo=3,
        )
        registrar_entrada(
            producto=self.prod_router,
            almacen=self.almacen_a,
            cantidad=10,
            usuario=self.user_a,
            clave_idempotencia="ENT-ROUTER-1",
        )

        # 2. Producto con stock bajo en Alpha
        self.prod_switch = Producto.objects.create(
            nombre="Switch 8 Puertos",
            categoria=self.cat_redes,
            stock_minimo=5,
        )
        registrar_entrada(
            producto=self.prod_switch,
            almacen=self.almacen_a,
            cantidad=2,
            usuario=self.user_a,
            clave_idempotencia="ENT-SWITCH-1",
        )

        # 3. Producto agotado (crítico) en Alpha
        self.prod_pinza = Producto.objects.create(
            nombre="Pinza Ponchadora",
            categoria=self.cat_herramientas,
            stock_minimo=2,
        )
        Existencia.objects.create(
            producto=self.prod_pinza,
            almacen=self.almacen_a,
            stock_disponible=0,
            stock_en_prestamo=0,
        )

        # 4. Préstamo activo
        registrar_prestamo(
            producto=self.prod_router,
            almacen=self.almacen_a,
            cantidad=3,
            destinatario="Profesor Redes",
            usuario=self.user_a,
            clave_idempotencia="PRES-ROUTER-1",
        )

        # 5. Producto en Almacén Beta
        self.prod_beta = Producto.objects.create(
            nombre="Taladro Percutor",
            categoria=self.cat_beta,
            stock_minimo=1,
        )
        registrar_entrada(
            producto=self.prod_beta,
            almacen=self.almacen_b,
            cantidad=4,
            usuario=self.admin,
            clave_idempotencia="ENT-BETA-1",
        )

    def test_obtener_metricas_dashboard_usuario_normal(self):
        """El usuario normal solo ve métricas correspondientes a su almacén asignado."""
        metricas = obtener_metricas_dashboard(usuario=self.user_a)

        # Productos en Alpha: router, switch, pinza (3)
        self.assertEqual(metricas["total_productos"], 3)
        # Stock disponible: router (10 - 3 en prestamo = 7) + switch (2) + pinza (0) = 9
        self.assertEqual(metricas["total_stock_disponible"], 9)
        # Stock en préstamo: router (3)
        self.assertEqual(metricas["total_stock_prestamo"], 3)
        # Crítico: pinza (1)
        self.assertEqual(metricas["total_criticos"], 1)
        # Bajo stock: switch (1)
        self.assertEqual(metricas["total_bajo_stock"], 1)
        # Préstamos activos: 1
        self.assertEqual(metricas["total_prestamos_activos"], 1)

    def test_obtener_metricas_dashboard_administrador(self):
        """El administrador ve las métricas consolidadas de todos los almacenes."""
        metricas = obtener_metricas_dashboard(usuario=self.admin)

        # Alpha (3) + Beta (1) = 4 productos
        self.assertEqual(metricas["total_productos"], 4)
        # 9 (Alpha) + 4 (Beta) = 13 disponibles
        self.assertEqual(metricas["total_stock_disponible"], 13)

    def test_obtener_datos_grafico_categorias(self):
        """Agrupación por categoría respetando el ámbito de almacén."""
        datos = obtener_datos_grafico_categorias(usuario=self.user_a)
        self.assertIn("labels", datos)
        self.assertIn("data", datos)
        self.assertIn("Redes", datos["labels"])
        self.assertIn("Herramientas", datos["labels"])
        self.assertNotIn("BetaCat", datos["labels"])

    def test_obtener_datos_grafico_balance_movimientos(self):
        """Balance de entradas y salidas en el período."""
        balance = obtener_datos_grafico_balance_movimientos(usuario=self.user_a, dias=7)
        self.assertIn("labels", balance)
        self.assertIn("entradas", balance)
        self.assertIn("salidas", balance)

    def test_obtener_reporte_existencias_filtro(self):
        """Reporte de existencias filtrable por estado crítico o bajo."""
        criticos = obtener_reporte_existencias(usuario=self.user_a, filtro_stock="CRITICO")
        self.assertEqual(criticos.count(), 1)
        self.assertEqual(criticos.first().producto, self.prod_pinza)

    def test_obtener_reporte_prestamos_activos(self):
        """Reporte de préstamos abiertos con cálculo de días transcurridos."""
        prestamos = obtener_reporte_prestamos_activos(usuario=self.user_a)
        self.assertEqual(len(prestamos), 1)
        self.assertEqual(prestamos[0].destinatario, "Profesor Redes")
        self.assertIsNotNone(prestamos[0].dias_transcurridos)

    def test_obtener_reporte_productos_estancados(self):
        """Reporte de productos sin movimientos en N días."""
        estancados = obtener_reporte_productos_estancados(usuario=self.user_a, dias_inactividad=30)
        # prod_pinza fue creado manualmente sin movimientos, debe figurar como estancado
        prod_ids = [p.id for p in estancados]
        self.assertIn(self.prod_pinza.id, prod_ids)
