"""Pruebas adversariales de QA para la Fase 5: Dashboard, Reportes, Aislamiento y Gráficos.

Misión de QA:
1. Aislamiento estricto del Dashboard: Usuario normal asignado a Almacén A NO debe ver
   en sus métricas ni un solo producto ni existencia perteneciente exclusivamente al Almacén B.
2. Endpoint de gráfico de categorías: Usuario normal solo recibe categorías de su almacén asignado
   con datos > 0. Categorías vacías y categorías de otros almacenes son excluidas.
3. Reporte de existencias con filtro CRITICO y BAJO: Verificar que el queryset excluye existencias
   de almacenes ajenos para usuarios normales, incluso ante intentos de manipulación de parámetros GET (?almacen_id=...).
4. Reporte de productos estancados: Verificar que un producto creado hace más de 30 días sin movimientos
   aparece en la lista, pero un producto con movimiento reciente no. Aislamiento por almacén verificado.
5. Casos límite adversariales: Usuario normal sin almacén asignado maneja consultas de forma segura sin 500.
"""

from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from cuentas.models import UsuarioAudacIA
from inventario.models import Almacen, Categoria, Existencia, Movimiento, Prestamo, Producto
from inventario.servicios.operaciones import (
    registrar_entrada,
    registrar_prestamo,
)
from web.servicios.reportes import (
    obtener_datos_grafico_categorias,
    obtener_metricas_dashboard,
    obtener_reporte_existencias,
    obtener_reporte_productos_estancados,
)
from web.views import DashboardView

User = get_user_model()


class Fase5QATestAdversarial(TestCase):
    """Suite de pruebas adversariales independientes para Fase 5."""

    def setUp(self):
        # 1. Almacenes independientes
        self.almacen_alpha = Almacen.objects.create(nombre="Almacén Alpha QA", activo=True)
        self.almacen_beta = Almacen.objects.create(nombre="Almacén Beta QA", activo=True)

        # 2. Usuarios con roles diferenciados
        self.user_alpha = User.objects.create_user(
            username="operador_alpha",
            password="SecurePassword123",
            nombre_completo="Operador Alpha QA",
            rol=UsuarioAudacIA.Rol.USUARIO,
            almacen_asignado=self.almacen_alpha,
        )
        self.user_beta = User.objects.create_user(
            username="operador_beta",
            password="SecurePassword123",
            nombre_completo="Operador Beta QA",
            rol=UsuarioAudacIA.Rol.USUARIO,
            almacen_asignado=self.almacen_beta,
        )
        self.admin = User.objects.create_user(
            username="admin_qa",
            password="SecurePassword123",
            nombre_completo="Administrador General QA",
            rol=UsuarioAudacIA.Rol.ADMINISTRADOR,
            almacen_asignado=None,
        )

        # 3. Categorías
        # Alpha: una con productos y una vacía (0 productos)
        self.cat_alpha_activa = Categoria.objects.create(
            nombre="Electrónica Alpha",
            almacen_principal=self.almacen_alpha,
        )
        self.cat_alpha_vacia = Categoria.objects.create(
            nombre="Suministros Vacíos Alpha",
            almacen_principal=self.almacen_alpha,
        )
        # Beta: una con productos y una vacía
        self.cat_beta_activa = Categoria.objects.create(
            nombre="Maquinaria Pesada Beta",
            almacen_principal=self.almacen_beta,
        )
        self.cat_beta_vacia = Categoria.objects.create(
            nombre="Herramientas Vacías Beta",
            almacen_principal=self.almacen_beta,
        )

        # 4. Productos y Existencias en Almacén Alpha
        # 4.1 Alpha Normal: Stock=10, Préstamo=3 -> Disp=7, Mín=5 -> NORMAL
        self.prod_a_normal = Producto.objects.create(
            nombre="Router Core Alpha",
            categoria=self.cat_alpha_activa,
            stock_minimo=5,
        )
        registrar_entrada(
            producto=self.prod_a_normal,
            almacen=self.almacen_alpha,
            cantidad=10,
            usuario=self.user_alpha,
            clave_idempotencia="IDEMP-ENT-ALPHA-NORM",
        )
        registrar_prestamo(
            producto=self.prod_a_normal,
            almacen=self.almacen_alpha,
            cantidad=3,
            destinatario="Laboratorio Alpha",
            usuario=self.user_alpha,
            clave_idempotencia="IDEMP-PRES-ALPHA-NORM",
        )

        # 4.2 Alpha Stock Bajo: Disp=2, Mín=5 -> BAJO
        self.prod_a_bajo = Producto.objects.create(
            nombre="Switch PoE Alpha",
            categoria=self.cat_alpha_activa,
            stock_minimo=5,
        )
        registrar_entrada(
            producto=self.prod_a_bajo,
            almacen=self.almacen_alpha,
            cantidad=2,
            usuario=self.user_alpha,
            clave_idempotencia="IDEMP-ENT-ALPHA-BAJO",
        )

        # 4.3 Alpha Crítico: Disp=0, Mín=2 -> CRÍTICO
        self.prod_a_critico = Producto.objects.create(
            nombre="Fibra Óptica Alpha",
            categoria=self.cat_alpha_activa,
            stock_minimo=2,
        )
        Existencia.objects.create(
            producto=self.prod_a_critico,
            almacen=self.almacen_alpha,
            stock_disponible=0,
            stock_en_prestamo=0,
        )

        # 5. Productos y Existencias en Almacén Beta
        # 5.1 Beta Normal: Stock=50, Préstamo=15 -> Disp=35, Mín=10 -> NORMAL
        self.prod_b_normal = Producto.objects.create(
            nombre="Generador Industrial Beta",
            categoria=self.cat_beta_activa,
            stock_minimo=10,
        )
        registrar_entrada(
            producto=self.prod_b_normal,
            almacen=self.almacen_beta,
            cantidad=50,
            usuario=self.user_beta,
            clave_idempotencia="IDEMP-ENT-BETA-NORM",
        )
        registrar_prestamo(
            producto=self.prod_b_normal,
            almacen=self.almacen_beta,
            cantidad=15,
            destinatario="Planta Externa Beta",
            usuario=self.user_beta,
            clave_idempotencia="IDEMP-PRES-BETA-NORM",
        )

        # 5.2 Beta Stock Bajo: Disp=1, Mín=8 -> BAJO
        self.prod_b_bajo = Producto.objects.create(
            nombre="Compresor Beta",
            categoria=self.cat_beta_activa,
            stock_minimo=8,
        )
        registrar_entrada(
            producto=self.prod_b_bajo,
            almacen=self.almacen_beta,
            cantidad=1,
            usuario=self.user_beta,
            clave_idempotencia="IDEMP-ENT-BETA-BAJO",
        )

        # 5.3 Beta Crítico: Disp=0, Mín=4 -> CRÍTICO
        self.prod_b_critico = Producto.objects.create(
            nombre="Torno CNC Beta",
            categoria=self.cat_beta_activa,
            stock_minimo=4,
        )
        Existencia.objects.create(
            producto=self.prod_b_critico,
            almacen=self.almacen_beta,
            stock_disponible=0,
            stock_en_prestamo=0,
        )

    # ═══════════════════════════════════════════════════════════════════════
    # 1. PRUEBAS DE AISLAMIENTO ESTRICTO DEL DASHBOARD
    # ═══════════════════════════════════════════════════════════════════════

    def test_aislamiento_estricto_dashboard_metricas_usuario_normal(self):
        """El usuario de Alpha NO debe recibir ninguna métrica, producto ni existencia de Beta."""
        metricas_alpha = obtener_metricas_dashboard(self.user_alpha)

        # Productos en Alpha: prod_a_normal, prod_a_bajo, prod_a_critico = 3
        # No debe incluir los 3 productos de Beta
        self.assertEqual(metricas_alpha["total_productos"], 3)

        # Stock disponible en Alpha: (10-3) + 2 + 0 = 9.
        # No debe incluir los (50-15) + 1 + 0 = 36 de Beta
        self.assertEqual(metricas_alpha["total_stock_disponible"], 9)

        # Préstamos en Alpha: 3 unidades prestadas
        # No debe incluir las 15 unidades prestadas de Beta
        self.assertEqual(metricas_alpha["total_stock_prestamo"], 3)

        # Críticos en Alpha: solo prod_a_critico (1)
        # No debe incluir el crítico de Beta (prod_b_critico)
        self.assertEqual(metricas_alpha["total_criticos"], 1)

        # Bajo stock en Alpha: solo prod_a_bajo (1)
        # No debe incluir el bajo stock de Beta (prod_b_bajo)
        self.assertEqual(metricas_alpha["total_bajo_stock"], 1)

        # Total préstamos activos: 1
        self.assertEqual(metricas_alpha["total_prestamos_activos"], 1)

        # Movimientos recientes: Todos deben pertenecer a Almacén Alpha
        for mov in metricas_alpha["ultimos_movimientos"]:
            self.assertTrue(
                mov.almacen_origen == self.almacen_alpha or mov.almacen_destino == self.almacen_alpha,
                f"Fuga de movimiento no perteneciente a Alpha: {mov}",
            )
            self.assertNotEqual(mov.producto, self.prod_b_normal)
            self.assertNotEqual(mov.producto, self.prod_b_bajo)
            self.assertNotEqual(mov.producto, self.prod_b_critico)

        # Préstamos recientes: Todos deben ser de Almacén Alpha
        for pres in metricas_alpha["prestamos_recientes"]:
            self.assertEqual(pres.almacen, self.almacen_alpha)
            self.assertNotEqual(pres.producto, self.prod_b_normal)

    def test_aislamiento_dashboard_vista_http_operador(self):
        """La vista HTTP GET del dashboard no debe filtrar nombres de productos de otros almacenes."""
        self.client.force_login(self.user_alpha)
        url = reverse("web:dashboard")
        resp = self.client.get(url)

        self.assertEqual(resp.status_code, 200)
        # Productos de Alpha deben figurar
        self.assertContains(resp, "Router Core Alpha")
        # Productos de Beta NUNCA deben figurar en la respuesta de Alpha
        self.assertNotContains(resp, "Generador Industrial Beta")
        self.assertNotContains(resp, "Compresor Beta")
        self.assertNotContains(resp, "Torno CNC Beta")
        self.assertNotContains(resp, "Almacén Beta QA")

    def test_metricas_dashboard_administrador_consolida_todos_los_almacenes(self):
        """En contraste, el Administrador debe ver la suma consolidada de Alpha y Beta."""
        metricas_admin = obtener_metricas_dashboard(self.admin)

        # 3 de Alpha + 3 de Beta = 6 productos
        self.assertEqual(metricas_admin["total_productos"], 6)
        # 9 (Alpha) + 36 (Beta) = 45 disponibles
        self.assertEqual(metricas_admin["total_stock_disponible"], 45)
        # 3 (Alpha) + 15 (Beta) = 18 en préstamo
        self.assertEqual(metricas_admin["total_stock_prestamo"], 18)
        # 1 (Alpha) + 1 (Beta) = 2 críticos
        self.assertEqual(metricas_admin["total_criticos"], 2)
        # 1 (Alpha) + 1 (Beta) = 2 bajo stock
        self.assertEqual(metricas_admin["total_bajo_stock"], 2)
        # 1 (Alpha) + 1 (Beta) = 2 préstamos activos
        self.assertEqual(metricas_admin["total_prestamos_activos"], 2)

    # ═══════════════════════════════════════════════════════════════════════
    # 2. PRUEBAS DEL ENDPOINT DE GRÁFICO DE CATEGORÍAS
    # ═══════════════════════════════════════════════════════════════════════

    def test_grafico_categorias_filtra_por_almacen_y_excluye_datos_cero(self):
        """Usuario de Alpha solo recibe categorías de Alpha que tengan productos > 0."""
        self.client.force_login(self.user_alpha)
        url = reverse("web:grafico-categorias")
        resp = self.client.get(url)

        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        # Debe incluir la categoría con productos de Alpha
        self.assertIn("Electrónica Alpha", data["labels"])
        # NO debe incluir categorías con 0 productos de Alpha
        self.assertNotIn("Suministros Vacíos Alpha", data["labels"])
        # NO debe incluir ninguna categoría de Beta (tenga o no productos)
        self.assertNotIn("Maquinaria Pesada Beta", data["labels"])
        self.assertNotIn("Herramientas Vacías Beta", data["labels"])

        # Solo debe haber 1 categoría calificada para Alpha
        self.assertEqual(len(data["labels"]), 1)
        self.assertEqual(len(data["data"]), 1)
        self.assertEqual(data["data"][0], 3)  # los 3 productos de Alpha

    def test_grafico_categorias_administrador_ve_ambos_almacenes_sin_categorias_vacias(self):
        """El administrador ve categorías con productos de todos los almacenes, omitiendo vacías."""
        self.client.force_login(self.admin)
        url = reverse("web:grafico-categorias")
        resp = self.client.get(url)

        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        self.assertIn("Electrónica Alpha", data["labels"])
        self.assertIn("Maquinaria Pesada Beta", data["labels"])
        self.assertNotIn("Suministros Vacíos Alpha", data["labels"])
        self.assertNotIn("Herramientas Vacías Beta", data["labels"])

    # ═══════════════════════════════════════════════════════════════════════
    # 3. PRUEBAS DEL REPORTE DE EXISTENCIAS (FILTRO CRITICO Y BAJO ADVERSARIAL)
    # ═══════════════════════════════════════════════════════════════════════

    def test_reporte_existencias_filtro_critico_aislamiento_estricto(self):
        """filtro_stock=CRITICO debe retornar exclusivamente existencias en stock 0 del almacén asignado."""
        qs = obtener_reporte_existencias(usuario=self.user_alpha, filtro_stock="CRITICO")

        self.assertEqual(qs.count(), 1)
        item = qs.first()
        self.assertEqual(item.producto, self.prod_a_critico)
        self.assertEqual(item.almacen, self.almacen_alpha)

        # Verificar que el crítico de Beta NO está en el queryset
        ids_productos = [ex.producto_id for ex in qs]
        self.assertNotIn(self.prod_b_critico.id, ids_productos)

    def test_reporte_existencias_filtro_bajo_aislamiento_estricto(self):
        """filtro_stock=BAJO debe retornar exclusivamente existencias por debajo del mínimo de su almacén."""
        qs = obtener_reporte_existencias(usuario=self.user_alpha, filtro_stock="BAJO")

        self.assertEqual(qs.count(), 1)
        item = qs.first()
        self.assertEqual(item.producto, self.prod_a_bajo)
        self.assertEqual(item.almacen, self.almacen_alpha)

        ids_productos = [ex.producto_id for ex in qs]
        self.assertNotIn(self.prod_b_bajo.id, ids_productos)

    def test_reporte_existencias_intento_tampering_parametro_almacen(self):
        """Adversarial: Usuario de Alpha intenta consultar Almacén Beta vía parámetro GET ?almacen_id=..."""
        self.client.force_login(self.user_alpha)
        url = reverse("web:reporte-existencias")

        # Intento de forzar almacen_id de Beta con filtro CRITICO
        resp = self.client.get(url, {"almacen_id": self.almacen_beta.id, "filtro_stock": "CRITICO"})
        self.assertEqual(resp.status_code, 200)

        existencias = resp.context["existencias"]
        # Debe ignorar el almacén forzado y retornar solo el de Alpha
        for ex in existencias:
            self.assertEqual(ex.almacen, self.almacen_alpha)
            self.assertNotEqual(ex.almacen, self.almacen_beta)

        self.assertContains(resp, "Fibra Óptica Alpha")
        self.assertNotContains(resp, "Torno CNC Beta")

    # ═══════════════════════════════════════════════════════════════════════
    # 4. PRUEBAS DEL REPORTE DE PRODUCTOS ESTANCADOS
    # ═══════════════════════════════════════════════════════════════════════

    def test_reporte_productos_estancados_inactividad_y_movimiento_reciente(self):
        """Verifica que producto > 30 días sin movimientos aparece, pero producto con movimiento reciente no."""
        hace_45_dias = timezone.now() - timedelta(days=45)
        hace_40_dias = timezone.now() - timedelta(days=40)

        # 1. Producto Alpha creado hace 45 días SIN ningún movimiento
        prod_estancado_sin_mov = Producto.objects.create(
            nombre="Cinta Aislante Antigua",
            categoria=self.cat_alpha_activa,
            stock_minimo=1,
        )
        Producto.objects.filter(id=prod_estancado_sin_mov.id).update(fecha_creacion=hace_45_dias)

        # 2. Producto Alpha creado hace 45 días con movimiento antiguo (hace 40 días)
        prod_estancado_con_mov_antiguo = Producto.objects.create(
            nombre="Conectores RJ45 Antiguos",
            categoria=self.cat_alpha_activa,
            stock_minimo=1,
        )
        Producto.objects.filter(id=prod_estancado_con_mov_antiguo.id).update(fecha_creacion=hace_45_dias)
        _, mov_antiguo = registrar_entrada(
            producto=prod_estancado_con_mov_antiguo,
            almacen=self.almacen_alpha,
            cantidad=10,
            usuario=self.user_alpha,
            clave_idempotencia="IDEMP-MOV-ANTIGUO-1",
        )
        Movimiento.objects.filter(id=mov_antiguo.id).update(fecha_hora=hace_40_dias)

        # 3. Producto Alpha creado hace 45 días pero con movimiento RECIENTE (hoy)
        prod_con_mov_reciente = Producto.objects.create(
            nombre="Patch Cord Activo Reciente",
            categoria=self.cat_alpha_activa,
            stock_minimo=1,
        )
        Producto.objects.filter(id=prod_con_mov_reciente.id).update(fecha_creacion=hace_45_dias)
        registrar_entrada(
            producto=prod_con_mov_reciente,
            almacen=self.almacen_alpha,
            cantidad=5,
            usuario=self.user_alpha,
            clave_idempotencia="IDEMP-MOV-RECIENTE-1",
        )

        # 4. Producto Beta creado hace 45 días sin movimientos (en almacén Beta)
        prod_estancado_beta = Producto.objects.create(
            nombre="Válvula Beta Estancada",
            categoria=self.cat_beta_activa,
            stock_minimo=1,
        )
        Producto.objects.filter(id=prod_estancado_beta.id).update(fecha_creacion=hace_45_dias)

        # Ejecutamos el servicio de estancados para Alpha con umbral de 30 días
        estancados = obtener_reporte_productos_estancados(usuario=self.user_alpha, dias_inactividad=30)
        estancados_ids = [p.id for p in estancados]

        # a) El producto sin movimientos debe aparecer como estancado
        self.assertIn(
            prod_estancado_sin_mov.id,
            estancados_ids,
            "Producto creado hace > 30 días sin movimientos debe figurar como estancado.",
        )

        # b) El producto con movimiento de hace 40 días (> 30) debe aparecer como estancado
        self.assertIn(
            prod_estancado_con_mov_antiguo.id,
            estancados_ids,
            "Producto con último movimiento hace 40 días debe figurar como estancado.",
        )

        # c) El producto con movimiento reciente NO debe figurar como estancado
        self.assertNotIn(
            prod_con_mov_reciente.id,
            estancados_ids,
            "Producto con movimiento reciente NO debe figurar como estancado.",
        )

        # d) Aislamiento: El producto estancado de Beta NO debe figurar en el reporte de Alpha
        self.assertNotIn(
            prod_estancado_beta.id,
            estancados_ids,
            "Producto de Almacén Beta no debe figurar en el reporte de productos estancados de Alpha.",
        )

        # Comprobación de vista HTTP
        self.client.force_login(self.user_alpha)
        url = reverse("web:reporte-estancados")
        resp = self.client.get(url, {"dias": 30})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Cinta Aislante Antigua")
        self.assertContains(resp, "Conectores RJ45 Antiguos")
        self.assertNotContains(resp, "Patch Cord Activo Reciente")
        self.assertNotContains(resp, "Válvula Beta Estancada")

    # ═══════════════════════════════════════════════════════════════════════
    # 5. CASO LÍMITE: USUARIO NORMAL SIN ALMACÉN ASIGNADO
    # ═══════════════════════════════════════════════════════════════════════

    def test_usuario_sin_almacen_asignado_comportamiento_seguro(self):
        """Un usuario normal con almacen_asignado=None (corrupción o estado huérfano) no debe disparar 500."""
        user_huerfano = User.objects.create_user(
            username="user_huerfano",
            password="SecurePassword123",
            nombre_completo="Usuario Huérfano QA",
            rol=UsuarioAudacIA.Rol.USUARIO,
            almacen_asignado=self.almacen_alpha,
        )
        # Forzamos almacen_asignado=None a nivel DB para simular estado corrupto/desvinculado
        User.objects.filter(id=user_huerfano.id).update(almacen_asignado=None)
        user_huerfano.refresh_from_db()

        metricas = obtener_metricas_dashboard(user_huerfano)
        self.assertEqual(metricas["total_productos"], 0)
        self.assertEqual(metricas["total_stock_disponible"], 0)
        self.assertEqual(metricas["total_stock_prestamo"], 0)
        self.assertEqual(metricas["total_criticos"], 0)
        self.assertEqual(metricas["total_bajo_stock"], 0)
        self.assertEqual(metricas["total_prestamos_activos"], 0)
        self.assertEqual(len(metricas["ultimos_movimientos"]), 0)
        self.assertEqual(len(metricas["prestamos_recientes"]), 0)

        grafico_cat = obtener_datos_grafico_categorias(user_huerfano)
        self.assertEqual(grafico_cat["labels"], [])
        self.assertEqual(grafico_cat["data"], [])

        reporte_ex = obtener_reporte_existencias(user_huerfano)
        self.assertEqual(reporte_ex.count(), 0)

        estancados = obtener_reporte_productos_estancados(user_huerfano)
        self.assertEqual(len(estancados), 0)

        # Vía vista directamente con RequestFactory (sin pasar por login signal)
        factory = RequestFactory()
        req = factory.get(reverse("web:dashboard"))
        req.user = user_huerfano
        resp = DashboardView.as_view()(req)
        self.assertEqual(resp.status_code, 200)
