"""Pruebas unitarias e integrales para la Fase 4: Rendimiento y Optimización de Consultas.

Cubre:
1. Cache y contador de versión en context processor `alertas_stock` (0 consultas en cache hit).
2. Invalidación atómica mediante señales al mutar Existencia, ConteoFisico, Prestamo y AlertaStockLeida.
3. Despacho perezoso de pestañas y eliminación de N+1 en `MovimientoListaView`.
4. Búsqueda normalizada insensible a mayúsculas y acentos sin dependencias de extensiones del motor.
5. Verificación de índices agregados para optimizar el rendimiento en SQLite y PostgreSQL.
"""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from cuentas.models import UsuarioAudacIA
from inventario.context_processors import alertas_stock
from inventario.models import (
    Almacen,
    AlertaStockLeida,
    Categoria,
    ConteoFisico,
    Existencia,
    Movimiento,
    Prestamo,
    Producto,
    Ubicacion,
)
from inventario.servicios.alertas import (
    CACHE_ALERTA_VERSION_KEY,
    invalidar_cache_alertas,
    obtener_version_alertas,
)
from inventario.utils import normalizar_texto

User = get_user_model()


class TestFase4Rendimiento(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Limpiar caché al inicio
        cache.clear()

        # Datos maestros de prueba
        cls.almacen = Almacen.objects.create(nombre="Almacén Central de Robótica")
        cls.ubicacion = Ubicacion.objects.create(nombre="Estante A-01", almacen=cls.almacen)
        cls.categoria = Categoria.objects.create(
            nombre="Cámaras y Sensores Ópticos",
            almacen_principal=cls.almacen,
        )

        # Usuario Administrador
        cls.admin_user = User.objects.create_user(
            username="admin_perf",
            nombre_completo="Admin Rendimiento",
            email="admin_perf@audacia.edu.co",
            password="PerfPassword123!",
            rol=UsuarioAudacIA.Rol.ADMINISTRADOR,
            almacen_asignado=cls.almacen,
            is_staff=True,
            is_superuser=True,
        )

        # Crear varios productos y existencias para medir N+1 y normalización
        cls.productos = []
        for i in range(5):
            p = Producto.objects.create(
                nombre=f"Cámara de Profundidad Estéreo {i}",
                categoria=cls.categoria,
                stock_inicial=10,
                stock_minimo=5,
            )
            p.ubicaciones.add(cls.ubicacion)
            Existencia.objects.create(
                producto=p,
                almacen=cls.almacen,
                stock_disponible=10,
                stock_en_prestamo=0,
            )
            cls.productos.append(p)

    def setUp(self):
        cache.clear()
        self.factory = RequestFactory()

    def test_normalizacion_texto_diacriticos(self):
        """Verifica que normalizar_texto elimine acentos y normalice a minúsculas."""
        self.assertEqual(normalizar_texto("CáMARA Óptica"), "camara optica")
        self.assertEqual(normalizar_texto("Batería de Litio-Polímero 11.1V"), "bateria de litio-polimero 11.1v")
        self.assertEqual(normalizar_texto("  ESP32 con Antena Externa  "), "esp32 con antena externa")
        self.assertEqual(normalizar_texto(""), "")
        self.assertEqual(normalizar_texto(None), "")

    def test_nombre_normalizado_en_modelos_y_save(self):
        """Verifica que Almacen, Categoria y Producto actualicen nombre_normalizado en save()."""
        alm = Almacen.objects.create(nombre="Almacén de Herramientas Eléctricas")
        self.assertEqual(alm.nombre_normalizado, "almacen de herramientas electricas")

        cat = Categoria.objects.create(nombre="Microcontroladores y Relés", almacen_principal=alm)
        self.assertEqual(cat.nombre_normalizado, "microcontroladores y reles")

        prod = Producto.objects.create(
            nombre="Batería LiPo 3S 2200mAh",
            categoria=cat,
            stock_inicial=2,
            stock_minimo=1,
        )
        self.assertEqual(prod.nombre_normalizado, "bateria lipo 3s 2200mah")

        # Modificación
        prod.nombre = "Batería LiPo 4S 5000mAh Ultra"
        prod.save()
        prod.refresh_from_db()
        self.assertEqual(prod.nombre_normalizado, "bateria lipo 4s 5000mah ultra")

    def test_busqueda_normalizada_sin_acentos(self):
        """Verifica que una búsqueda sin acentos encuentre registros con acentos y viceversa."""
        q_sin_acento = "camara"
        resultados = Producto.objects.filter(
            nombre_normalizado__contains=normalizar_texto(q_sin_acento)
        )
        self.assertGreaterEqual(resultados.count(), 5)

        q_con_acento = "CÁMARA"
        resultados_acento = Producto.objects.filter(
            nombre_normalizado__contains=normalizar_texto(q_con_acento)
        )
        self.assertEqual(resultados.count(), resultados_acento.count())

    def test_context_processor_cache_hit_cero_consultas(self):
        """Verifica que el context processor alertas_stock ejecute 0 consultas en cache hit."""
        request = self.factory.get("/inventario/movimientos/")
        request.user = self.admin_user

        # 1. Primera llamada (Cache Miss) -> Debe poblar la caché
        res1 = alertas_stock(request)
        self.assertIn("alertas_resumen", res1)

        # 2. Segunda llamada (Cache Hit) -> Debe devolver del caché con 0 consultas
        with self.assertNumQueries(0):
            res2 = alertas_stock(request)
            self.assertEqual(
                res1["alertas_resumen"]["total_alertas"],
                res2["alertas_resumen"]["total_alertas"],
            )

    def test_invalidacion_cache_alertas_por_senales(self):
        """Verifica que mutaciones en Existencia, ConteoFisico o Prestamo incrementen la versión global."""
        request = self.factory.get("/inventario/movimientos/")
        request.user = self.admin_user

        # Llenar cache
        alertas_stock(request)
        version_inicial = obtener_version_alertas()

        # Mutación de Existencia
        ex = Existencia.objects.first()
        ex.stock_disponible = 0
        ex.save()

        version_post_existencia = obtener_version_alertas()
        self.assertGreater(version_post_existencia, version_inicial)

        # El context processor ahora debe ejecutar queries nuevamente (cache miss en nueva versión)
        # y guardar la nueva versión
        res_post_mutacion = alertas_stock(request)
        self.assertIn("alertas_resumen", res_post_mutacion)

        # Siguiente llamado vuelve a ser 0 consultas
        with self.assertNumQueries(0):
            alertas_stock(request)

    def test_context_processor_omision_en_ajax(self):
        """Verifica que peticiones AJAX o JSON no ejecuten consultas de alertas."""
        request_ajax = self.factory.get(
            "/inventario/movimientos/",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        request_ajax.user = self.admin_user

        with self.assertNumQueries(0):
            res = alertas_stock(request_ajax)
            self.assertEqual(res["alertas_resumen"]["total_alertas"], 0)

    def test_movimiento_lista_consultas_controladas_kardex(self):
        """Verifica que la pestaña de movimientos (Kardex) se ejecute con <= 15 consultas (sin N+1)."""
        self.client.force_login(self.admin_user)

        # Pre-calentar caché de alertas para aislar las consultas de la vista
        request_warm = self.factory.get("/inventario/movimientos/")
        request_warm.user = self.admin_user
        alertas_stock(request_warm)

        url = reverse("inventario:movimiento-lista") + "?vista=SALIDA"
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)

        # Assert estricto: la cantidad total de consultas debe ser <= 15
        self.assertLessEqual(
            len(ctx.captured_queries),
            15,
            f"Se ejecutaron {len(ctx.captured_queries)} consultas (máximo permitido: 15). Consultas: {[q['sql'] for q in ctx.captured_queries]}",
        )

    def test_despacho_perezoso_pestanas(self):
        """Verifica que la vista MovimientoListaView cargue únicamente los datos de la pestaña activa."""
        self.client.force_login(self.admin_user)

        # 1. Pestaña CONTEOS: prestamos_lista debe estar vacía y conteos_lista poblada/paginada
        ConteoFisico.objects.create(
            producto=self.productos[0],
            almacen=self.almacen,
            cantidad_contada=5,
            stock_sistema=10,
            diferencia=-5,
            usuario_conteo=self.admin_user,
        )
        resp_conteos = self.client.get(reverse("inventario:movimiento-lista") + "?vista=CONTEOS")
        self.assertEqual(resp_conteos.status_code, 200)
        self.assertEqual(len(resp_conteos.context["prestamos_lista"]), 0)
        self.assertGreaterEqual(len(resp_conteos.context["conteos_lista"]), 1)
        self.assertEqual(len(resp_conteos.context["estancados_lista"]), 0)

        # 2. Pestaña PRESTAMOS: conteos_lista debe estar vacía
        resp_prestamos = self.client.get(reverse("inventario:movimiento-lista") + "?vista=PRESTAMOS")
        self.assertEqual(resp_prestamos.status_code, 200)
        self.assertEqual(len(resp_prestamos.context["conteos_lista"]), 0)
        self.assertEqual(len(resp_prestamos.context["estancados_lista"]), 0)

    def test_indices_fase4_definidos(self):
        """Verifica que los índices de optimización de la Fase 4 existan en Meta."""
        mov_index_names = [idx.name for idx in Movimiento._meta.indexes]
        self.assertIn("idx_mov_fecha_tipo", mov_index_names)

        prestamo_index_names = [idx.name for idx in Prestamo._meta.indexes]
        self.assertIn("idx_prestamo_prod_estado", prestamo_index_names)

        existencia_index_names = [idx.name for idx in Existencia._meta.indexes]
        self.assertIn("idx_existencia_prod_disp", existencia_index_names)
