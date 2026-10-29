"""Pruebas unitarias e integrales para la Fase 5: Modularidad y Limpieza del Código.

Cubre:
1. Reemplazo de `ProductoUbicacion` por FK directa `Producto.ubicacion` con validación de almacén.
2. Adaptador de compatibilidad retrospectiva `_UbicacionesCompatManager` (all, filter, first, count, exists, etc.).
3. Advertencias de deprecación `DeprecationWarning` en servicios al recibir parámetro `ubicacion`.
4. Actualización directa de `producto.ubicacion` en traslados internos y entre almacenes.
5. Vistas canónicas de redirección `RedireccionPestañaMovimientosView` (`PrestamoListaView`, `ConteoFisicoListaView`).
6. Modularización de `inventario.views` como paquete Python y re-exportación transparente con `__all__`.
"""

import warnings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import RequestFactory, TestCase
from django.urls import resolve, reverse

import inventario.views as views_pkg
from cuentas.models import PermisoAudacIA, UsuarioAudacIA
from inventario.models import (
    Almacen,
    Categoria,
    Existencia,
    Movimiento,
    Producto,
    ProductoUbicacion,
    TipoCategoria,
    Ubicacion,
)
from inventario.servicios.datos_maestros import fusionar_productos
from inventario.servicios.operaciones import (
    registrar_entrada,
    registrar_prestamo,
    registrar_salida,
    registrar_traslado_entre_almacenes,
    registrar_traslado_interno,
)
from inventario.views._comun import RedireccionPestañaMovimientosView

User = get_user_model()


class TestFase5ModularidadLimpieza(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Almacenes
        cls.almacen_a = Almacen.objects.create(nombre="Almacén Robótica Principal")
        cls.almacen_b = Almacen.objects.create(nombre="Almacén IA Secundaria")

        # Ubicaciones
        cls.ubicacion_a1 = Ubicacion.objects.create(nombre="Estante A-1", almacen=cls.almacen_a)
        cls.ubicacion_a2 = Ubicacion.objects.create(nombre="Estante A-2", almacen=cls.almacen_a)
        cls.ubicacion_b1 = Ubicacion.objects.create(nombre="Gaveta B-1", almacen=cls.almacen_b)

        # Categorías y Tipos
        cls.categoria_a = Categoria.objects.create(
            nombre="Microcontroladores",
            almacen_principal=cls.almacen_a,
        )
        cls.categoria_b = Categoria.objects.create(
            nombre="Servomotores",
            almacen_principal=cls.almacen_b,
        )
        cls.tipo_cat = TipoCategoria.objects.create(
            nombre="Hardware",
            categoria=cls.categoria_a,
        )

        # Usuarios
        cls.admin_user = User.objects.create_user(
            username="admin_fase5",
            nombre_completo="Admin Modularidad",
            email="admin_fase5@audacia.edu.co",
            password="AdminPassword123!",
            rol=UsuarioAudacIA.Rol.ADMINISTRADOR,
        )

        cls.user_sin_permisos = User.objects.create_user(
            username="lector_fase5",
            nombre_completo="Lector Sin Permisos",
            email="lector_fase5@audacia.edu.co",
            password="UserPassword123!",
            rol=UsuarioAudacIA.Rol.USUARIO,
            almacen_asignado=cls.almacen_a,
        )
        cls.user_sin_permisos.permisos_personalizados.clear()

        cls.user_movimientos = User.objects.create_user(
            username="operador_fase5",
            nombre_completo="Operador de Movimientos",
            email="operador_fase5@audacia.edu.co",
            password="UserPassword123!",
            rol=UsuarioAudacIA.Rol.USUARIO,
            almacen_asignado=cls.almacen_a,
        )
        cls.user_movimientos.almacenes_asignados.add(cls.almacen_a, cls.almacen_b)
        permisos_mov = PermisoAudacIA.objects.filter(codigo__startswith="movimientos.")
        cls.user_movimientos.permisos_personalizados.set(permisos_mov)

    def test_01_asignacion_directa_fk_ubicacion_y_validacion_almacen(self):
        """Verifica que producto.ubicacion es FK directa y valida consistencia de almacén."""
        producto = Producto.objects.create(
            nombre="ESP32-WROOM-32D",
            categoria=self.categoria_a,
            ubicacion=self.ubicacion_a1,
        )
        producto.refresh_from_db()
        self.assertEqual(producto.ubicacion, self.ubicacion_a1)
        self.assertEqual(producto.ubicacion.almacen, self.almacen_a)

        # Validación clean() rechaza ubicación de almacén ajeno a la categoría
        producto.ubicacion = self.ubicacion_b1
        with self.assertRaises(ValidationError) as ctx:
            producto.clean()
        self.assertIn("pero la categoría del producto pertenece a", str(ctx.exception))

    def test_02_adaptador_compatibilidad_ubicaciones_proxy(self):
        """Verifica que producto.ubicaciones mantiene 100% de compatibilidad con código legado."""
        producto = Producto.objects.create(
            nombre="Arduino Uno R3",
            categoria=self.categoria_a,
            ubicacion=self.ubicacion_a1,
        )

        # .all() y list iteración
        ubicaciones_qs = producto.ubicaciones.all()
        self.assertEqual(list(ubicaciones_qs), [self.ubicacion_a1])
        self.assertEqual(list(producto.ubicaciones), [self.ubicacion_a1])

        # .first(), .count(), .exists()
        self.assertEqual(producto.ubicaciones.first(), self.ubicacion_a1)
        self.assertEqual(producto.ubicaciones.count(), 1)
        self.assertTrue(producto.ubicaciones.exists())

        # .filter() y .exclude()
        self.assertEqual(producto.ubicaciones.filter(nombre="Estante A-1").count(), 1)
        self.assertEqual(producto.ubicaciones.filter(nombre="Inexistente").count(), 0)
        self.assertEqual(producto.ubicaciones.exclude(nombre="Estante A-1").count(), 0)

        # .add() muta directamente producto.ubicacion
        producto.ubicaciones.add(self.ubicacion_a2)
        producto.refresh_from_db()
        self.assertEqual(producto.ubicacion, self.ubicacion_a2)

        # .remove() y .clear() limpian la ubicación
        producto.ubicaciones.remove(self.ubicacion_a2)
        producto.refresh_from_db()
        self.assertIsNone(producto.ubicacion)
        self.assertEqual(producto.ubicaciones.count(), 0)
        self.assertFalse(producto.ubicaciones.exists())
        self.assertIsNone(producto.ubicaciones.first())

        producto.ubicacion = self.ubicacion_a1
        producto.save()
        producto.ubicaciones.clear()
        producto.refresh_from_db()
        self.assertIsNone(producto.ubicacion)

        # Compatibilidad de símbolo ProductoUbicacion legado
        self.assertIsNotNone(ProductoUbicacion)
        self.assertTrue(hasattr(ProductoUbicacion, "objects"))
        self.assertTrue(hasattr(ProductoUbicacion.objects, "filter"))

    def test_03_advertencias_de_deprecacion_en_operaciones(self):
        """Verifica que pasar el parámetro 'ubicacion' en operaciones genera DeprecationWarning."""
        producto = Producto.objects.create(
            nombre="Sensor LiDAR Fase5",
            categoria=self.categoria_a,
            ubicacion=self.ubicacion_a1,
        )

        # 1. registrar_entrada con ubicacion
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            registrar_entrada(
                producto=producto,
                almacen=self.almacen_a,
                cantidad=10,
                usuario=self.user_movimientos,
                motivo="Entrada de prueba Fase 5",
                ubicacion=self.ubicacion_a1,
            )
            self.assertTrue(any(issubclass(item.category, DeprecationWarning) for item in w))
            msg = [str(item.message) for item in w if issubclass(item.category, DeprecationWarning)][0]
            self.assertIn("El parámetro 'ubicacion' en registrar_entrada está deprecado", msg)

        # 2. registrar_salida con ubicacion
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            registrar_salida(
                producto=producto,
                almacen=self.almacen_a,
                cantidad=2,
                motivo="Dotación equipo pruebas",
                destinatario="Equipo Robótica",
                usuario=self.user_movimientos,
                ubicacion=self.ubicacion_a1,
            )
            self.assertTrue(any(issubclass(item.category, DeprecationWarning) for item in w))
            msg = [str(item.message) for item in w if issubclass(item.category, DeprecationWarning)][0]
            self.assertIn("El parámetro 'ubicacion' en registrar_salida está deprecado", msg)

        # 3. registrar_prestamo con ubicacion
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            registrar_prestamo(
                producto=producto,
                almacen=self.almacen_a,
                cantidad=1,
                destinatario="Estudiante Investigador",
                usuario=self.user_movimientos,
                ubicacion=self.ubicacion_a1,
            )
            self.assertTrue(any(issubclass(item.category, DeprecationWarning) for item in w))
            msg = [str(item.message) for item in w if issubclass(item.category, DeprecationWarning)][0]
            self.assertIn("El parámetro 'ubicacion' en registrar_prestamo está deprecado", msg)

    def test_04_traslados_actualizan_fk_ubicacion_directamente(self):
        """Verifica que traslados internos y entre almacenes actualizan directamente producto.ubicacion."""
        producto = Producto.objects.create(
            nombre="Driver de Motores TB6612",
            categoria=self.categoria_a,
            ubicacion=self.ubicacion_a1,
        )
        Existencia.objects.create(
            producto=producto,
            almacen=self.almacen_a,
            stock_disponible=20,
            stock_en_prestamo=0,
        )

        # Traslado interno (mueve la ubicación dentro del almacén sin modificar stock)
        registrar_traslado_interno(
            producto=producto,
            almacen=self.almacen_a,
            ubicacion_origen=self.ubicacion_a1,
            ubicacion_destino=self.ubicacion_a2,
            usuario=self.user_movimientos,
            motivo="Reorganización interna estantes",
        )
        producto.refresh_from_db()
        self.assertEqual(producto.ubicacion, self.ubicacion_a2)

        # Traslado entre almacenes (requiere trasladar todo el stock disponible y actualizar categoría)
        registrar_traslado_entre_almacenes(
            producto=producto,
            almacen_origen=self.almacen_a,
            almacen_destino=self.almacen_b,
            cantidad=20,
            ubicacion_origen=self.ubicacion_a2,
            ubicacion_destino=self.ubicacion_b1,
            categoria_destino=self.categoria_b,
            usuario=self.user_movimientos,
            motivo="Transferencia de sede IA",
        )
        producto.refresh_from_db()
        self.assertEqual(producto.ubicacion, self.ubicacion_b1)

    def test_05_fusion_de_productos_propaga_ubicacion(self):
        """Verifica que fusionar productos asigna la ubicación del origen si el destino no tenía."""
        prod_origen = Producto.objects.create(
            nombre="Resistencia 220 Ohm Origen",
            categoria=self.categoria_a,
            ubicacion=self.ubicacion_a1,
        )
        prod_destino = Producto.objects.create(
            nombre="Resistencia 220 Ohm Destino",
            categoria=self.categoria_a,
            ubicacion=None,
        )

        fusionar_productos(
            producto_principal=prod_destino,
            producto_duplicado=prod_origen,
            usuario=self.admin_user,
        )

        prod_destino.refresh_from_db()
        self.assertEqual(prod_destino.ubicacion, self.ubicacion_a1)

    def test_06_vistas_redireccion_autenticacion_y_permisos(self):
        """Verifica que RedireccionPestañaMovimientosView exige login y permisos granulares."""
        url_prestamos = reverse("inventario:prestamo-lista")
        url_conteos = reverse("inventario:conteo-lista")

        # 1. No autenticado redirige a login
        resp_prestamos_anon = self.client.get(url_prestamos)
        self.assertEqual(resp_prestamos_anon.status_code, 302)
        self.assertIn("/cuentas/login/", resp_prestamos_anon.url)

        resp_conteos_anon = self.client.get(url_conteos)
        self.assertEqual(resp_conteos_anon.status_code, 302)
        self.assertIn("/cuentas/login/", resp_conteos_anon.url)

        # 2. Autenticado sin permisos recibe 403 Forbidden
        self.client.force_login(self.user_sin_permisos)
        resp_sin_permiso_p = self.client.get(url_prestamos)
        self.assertEqual(resp_sin_permiso_p.status_code, 403)

        resp_sin_permiso_c = self.client.get(url_conteos)
        self.assertEqual(resp_sin_permiso_c.status_code, 403)

        # 3. Autenticado con permisos redirige a pestaña correcta y preserva querystrings
        self.client.force_login(self.user_movimientos)

        resp_ok_p = self.client.get(f"{url_prestamos}?q=resistencia&page=2")
        self.assertEqual(resp_ok_p.status_code, 302)
        self.assertIn("/inventario/movimientos/", resp_ok_p.url)
        self.assertIn("vista=PRESTAMOS", resp_ok_p.url)
        self.assertIn("q=resistencia", resp_ok_p.url)
        self.assertIn("page=2", resp_ok_p.url)

        resp_ok_c = self.client.get(f"{url_conteos}?almacen={self.almacen_a.id}")
        self.assertEqual(resp_ok_c.status_code, 302)
        self.assertIn("/inventario/movimientos/", resp_ok_c.url)
        self.assertIn("vista=CONTEOS", resp_ok_c.url)
        self.assertIn(f"almacen={self.almacen_a.id}", resp_ok_c.url)

    def test_07_modularizacion_views_package_reexportacion_total(self):
        """Verifica que el paquete inventario.views re-exporta todas las vistas con paridad 100%."""
        # Verificar que __all__ existe y contiene las clases principales
        self.assertTrue(hasattr(views_pkg, "__all__"))
        self.assertGreater(len(views_pkg.__all__), 30)

        # Vistas de catálogo
        self.assertTrue(hasattr(views_pkg, "ProductoListaView"))
        self.assertTrue(hasattr(views_pkg, "ProductoCrearView"))
        self.assertTrue(hasattr(views_pkg, "ProductoEditarView"))
        self.assertTrue(hasattr(views_pkg, "ProductoDetalleView"))
        self.assertTrue(hasattr(views_pkg, "ProductoFusionarView"))

        # Vistas de movimientos
        self.assertTrue(hasattr(views_pkg, "MovimientoListaView"))
        self.assertTrue(hasattr(views_pkg, "EntradaCrearView"))
        self.assertTrue(hasattr(views_pkg, "SalidaCrearView"))
        self.assertTrue(hasattr(views_pkg, "TrasladoCrearView"))
        self.assertTrue(hasattr(views_pkg, "MovimientoRevertirView"))

        # Vistas de infraestructura
        self.assertTrue(hasattr(views_pkg, "AlmacenListaView"))
        self.assertTrue(hasattr(views_pkg, "UbicacionListaView"))
        self.assertTrue(hasattr(views_pkg, "CategoriaListaView"))

        # Vistas de préstamos y conteos
        self.assertTrue(hasattr(views_pkg, "PrestamoListaView"))
        self.assertTrue(hasattr(views_pkg, "PrestamoCrearView"))
        self.assertTrue(hasattr(views_pkg, "ConteoFisicoListaView"))
        self.assertTrue(hasattr(views_pkg, "ConteoFisicoCrearView"))

        # Vistas de alertas y respaldos
        self.assertTrue(hasattr(views_pkg, "AlertasStockListaView"))
        self.assertTrue(hasattr(views_pkg, "RespaldoListaView"))

        # Utilidades y mixins
        self.assertTrue(hasattr(views_pkg, "AjaxModalFormMixin"))
        self.assertTrue(hasattr(views_pkg, "RedireccionPestañaMovimientosView"))
        self.assertTrue(hasattr(views_pkg, "_obtener_ip"))
        self.assertTrue(hasattr(views_pkg, "_procesar_error_formulario"))

        # Todos los nombres listados en __all__ deben existir en el paquete
        for nombre in views_pkg.__all__:
            self.assertTrue(
                hasattr(views_pkg, nombre),
                f"El símbolo '{nombre}' en __all__ no está expuesto en inventario.views",
            )
