"""
Pruebas unitarias para el módulo de permisos granulares (RBAC/PBAC) en cuentas.
"""

from django.test import TestCase, RequestFactory
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied

from cuentas.models import PermisoAudacIA, PRESETS_PERMISOS
from cuentas.forms import UsuarioForm
from cuentas.services import purgar_sesiones_activas
from inventario.models import Almacen

User = get_user_model()


class PermisosGranularesModelTest(TestCase):
    """Pruebas del modelo PermisoAudacIA y métodos de UsuarioAudacIA."""

    def setUp(self):
        self.almacen = Almacen.objects.create(nombre="Almacén Central", activo=True)
        self.admin = User.objects.create_user(
            username="admin_test",
            nombre_completo="Admin Test",
            password="Password123",
            rol=User.Rol.ADMINISTRADOR,
        )
        self.superadmin = User.objects.create_superuser(
            username="super_test",
            nombre_completo="Super Test",
            password="Password123",
        )
        self.operario = User.objects.create_user(
            username="operario_test",
            nombre_completo="Operario Test",
            password="Password123",
            rol=User.Rol.USUARIO,
            almacen_asignado=self.almacen,
        )

    def test_catalogo_permisos_completo(self):
        """Verifica que el catálogo inicial tenga los permisos cargados."""
        total = PermisoAudacIA.objects.count()
        self.assertEqual(total, 19)
        # Verificar permisos clave
        codigos = PermisoAudacIA.get_todos_codigos()
        self.assertIn("catalogo.ver", codigos)
        self.assertIn("catalogo.crear", codigos)
        self.assertIn("movimientos.entrada", codigos)
        self.assertIn("movimientos.prestamo", codigos)
        self.assertIn("movimientos.traslado_interno", codigos)
        self.assertIn("movimientos.traslado_almacen", codigos)
        self.assertIn("movimientos.revertir", codigos)

    def test_bypass_administrador_y_superusuario(self):
        """El administrador y superusuario tienen todos los permisos sin necesidad de M2M."""
        self.assertTrue(self.admin.tiene_permiso("movimientos.traslado_interno"))
        self.assertTrue(self.admin.tiene_permiso("movimientos.traslado_almacen"))
        self.assertTrue(self.admin.tiene_permiso("catalogo.ver"))

        self.assertTrue(self.superadmin.tiene_permiso("movimientos.traslado_interno"))
        self.assertTrue(self.superadmin.tiene_permiso("movimientos.traslado_almacen"))
        self.assertTrue(self.superadmin.tiene_permiso("catalogo.ver"))

    def test_usuario_operativo_permisos_personalizados(self):
        """Un usuario operativo solo tiene los permisos explícitamente concedidos."""
        # Limpiar permisos
        self.operario.permisos_personalizados.clear()
        self.assertFalse(self.operario.tiene_permiso("movimientos.entrada"))
        self.assertFalse(self.operario.tiene_permiso("movimientos.traslado_interno"))

        # Asignar únicamente movimientos.entrada
        p_entrada = PermisoAudacIA.objects.get(codigo="movimientos.entrada")
        self.operario.permisos_personalizados.add(p_entrada)

        # Limpiar cache de instancia si existiese
        if hasattr(self.operario, "_permisos_cache"):
            delattr(self.operario, "_permisos_cache")

        self.assertTrue(self.operario.tiene_permiso("movimientos.entrada"))
        self.assertFalse(self.operario.tiene_permiso("movimientos.dotacion"))
        self.assertFalse(self.operario.tiene_permiso("movimientos.traslado_interno"))

    def test_versionamiento_permisos_incrementa_al_modificar(self):
        """Al modificar los permisos de un usuario, su version de permisos se incrementa."""
        version_inicial = self.operario.permisos_version
        p_salida = PermisoAudacIA.objects.get(codigo="movimientos.dotacion")

        form = UsuarioForm(
            data={
                "username": self.operario.username,
                "nombre_completo": self.operario.nombre_completo,
                "rol": self.operario.rol,
                "almacenes_asignados": [self.almacen.id],
                "permisos_personalizados": [p_salida.id],
                "is_active": True,
            },
            instance=self.operario,
            request_user=self.admin,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()

        self.operario.refresh_from_db()
        self.assertGreater(self.operario.permisos_version, version_inicial)
        self.assertTrue(self.operario.tiene_permiso("movimientos.dotacion"))
        self.assertFalse(self.operario.tiene_permiso("movimientos.traslado_interno"))
        self.assertFalse(self.operario.tiene_permiso("movimientos.traslado_almacen"))

    def test_preset_colombiano_almacenista_herramientas(self):
        """Verifica que el preset antes llamado Pañolero tenga la denominación colombiana."""
        self.assertIn("PANOLERO", PRESETS_PERMISOS)
        preset = PRESETS_PERMISOS["PANOLERO"]
        self.assertEqual(preset["nombre"], "Almacenista de Herramientas / Despacho")
        self.assertIn("herramientas", preset["descripcion"])

    def test_permiso_seguridad_gestionar_usuarios_eliminado(self):
        """Verifica que seguridad.gestionar_usuarios no exista en el catálogo de permisos."""
        self.assertNotIn("seguridad.gestionar_usuarios", PermisoAudacIA.get_todos_codigos())
        self.assertFalse(PermisoAudacIA.objects.filter(codigo="seguridad.gestionar_usuarios").exists())

    def test_purga_sesiones_al_desactivar_usuario(self):
        """Al desactivar un usuario se ejecuta la purga de sesiones activas."""
        # Crear sesión en la BD
        from django.contrib.sessions.backends.db import SessionStore
        s = SessionStore()
        s["_auth_user_id"] = str(self.operario.id)
        s["_auth_user_hash"] = self.operario.get_session_auth_hash()
        s.save()

        from django.contrib.sessions.models import Session
        self.assertTrue(Session.objects.filter(session_key=s.session_key).exists())

        # Desactivar usuario con el formulario
        form = UsuarioForm(
            data={
                "username": self.operario.username,
                "nombre_completo": self.operario.nombre_completo,
                "rol": self.operario.rol,
                "almacenes_asignados": [self.almacen.id],
                "permisos_personalizados": [p.id for p in self.operario.permisos_personalizados.all()],
                "is_active": False,
            },
            instance=self.operario,
            request_user=self.admin,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()

        # Comprobar que la sesión fue purgada
        self.assertFalse(Session.objects.filter(session_key=s.session_key).exists())


class PermisosDesacopladosViewsTest(TestCase):
    """Pruebas de acceso a vistas cuando el usuario carece de permiso .ver pero tiene permisos operativos."""

    def setUp(self):
        from inventario.models import Almacen, Categoria, Ubicacion, Producto, ProductoUbicacion, Existencia
        self.almacen = Almacen.objects.create(nombre="Bodega Principal", activo=True)
        self.categoria = Categoria.objects.create(nombre="Herramientas", activo=True, almacen_principal=self.almacen)
        self.ubicacion = Ubicacion.objects.create(nombre="Estante A", almacen=self.almacen, activo=True)
        self.producto = Producto.objects.create(
            nombre="Taladro Percutor",
            categoria=self.categoria,
            activo=True,
            ubicacion=self.ubicacion,
        )
        Existencia.objects.create(producto=self.producto, almacen=self.almacen, stock_disponible=10)

        self.user = User.objects.create_user(
            username="operador_modular",
            nombre_completo="Operador Modular",
            password="Password123",
            rol=User.Rol.USUARIO,
            almacen_asignado=self.almacen,
        )
        self.user.almacenes_asignados.add(self.almacen)
        self.client.login(username="operador_modular", password="Password123")

    def test_movimientos_sin_ver_pero_con_entrada(self):
        """Usuario con movimientos.entrada pero sin movimientos.ver puede acceder a la vista pero con historial oculto."""
        p_entrada = PermisoAudacIA.objects.get(codigo="movimientos.entrada")
        self.user.permisos_personalizados.set([p_entrada])

        response = self.client.get("/inventario/movimientos/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["puede_ver_historial"])
        self.assertContains(response, "Historial de Movimientos Restringido")
        self.assertContains(response, "+ Nuevo Movimiento")

    def test_movimientos_con_traslado_interno_muestra_pestana_traslado(self):
        """Usuario con movimientos.traslado_interno puede ver la pestaña de Traslados."""
        p_traslado = PermisoAudacIA.objects.get(codigo="movimientos.traslado_interno")
        p_ver = PermisoAudacIA.objects.get(codigo="movimientos.ver")
        self.user.permisos_personalizados.set([p_traslado, p_ver])

        response = self.client.get("/inventario/movimientos/?vista=TRASLADO")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["puede_ver_historial"])
        self.assertContains(response, "Traslados")

    def test_catalogo_sin_ver_pero_con_crear(self):
        """Usuario con catalogo.crear pero sin catalogo.ver puede acceder pero con tabla oculta."""
        p_crear = PermisoAudacIA.objects.get(codigo="catalogo.crear")
        self.user.permisos_personalizados.set([p_crear])

        response = self.client.get("/inventario/productos/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["puede_ver_catalogo"])
        self.assertContains(response, "Catálogo de Productos Restringido")
        self.assertContains(response, "+ Nuevo Producto")

    def test_categorias_sin_ver_pero_con_gestionar_categorias(self):
        """Usuario con infraestructura.gestionar_categorias pero sin infraestructura.ver puede acceder pero con tabla oculta."""
        p_gestionar = PermisoAudacIA.objects.get(codigo="infraestructura.gestionar_categorias")
        self.user.permisos_personalizados.set([p_gestionar])

        response = self.client.get("/inventario/categorias/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["puede_ver_categorias"])
        self.assertContains(response, "Listado de Categorías Restringido")
        self.assertContains(response, "+ Nueva Categoría")

    def test_ubicaciones_sin_ver_pero_con_gestionar_ubicaciones(self):
        """Usuario con infraestructura.gestionar_ubicaciones pero sin infraestructura.ver puede acceder pero con tabla oculta."""
        p_gestionar = PermisoAudacIA.objects.get(codigo="infraestructura.gestionar_ubicaciones")
        self.user.permisos_personalizados.set([p_gestionar])

        response = self.client.get("/inventario/ubicaciones/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["puede_ver_ubicaciones"])
        self.assertContains(response, "Listado de Ubicaciones Restringido")
        self.assertContains(response, "+ Nueva Ubicación")

    def test_usuario_sin_ningun_permiso_recibe_403(self):
        """Usuario sin ningún permiso no puede entrar a movimientos, catalogo o infraestructura."""
        self.user.permisos_personalizados.clear()

        r_mov = self.client.get("/inventario/movimientos/")
        self.assertEqual(r_mov.status_code, 403)

        r_cat = self.client.get("/inventario/productos/")
        self.assertEqual(r_cat.status_code, 403)

        r_infra = self.client.get("/inventario/categorias/")
        self.assertEqual(r_infra.status_code, 403)

