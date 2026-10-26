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
        self.assertGreaterEqual(total, 19)
        # Verificar permisos clave
        codigos = PermisoAudacIA.get_todos_codigos()
        self.assertIn("catalogo.ver", codigos)
        self.assertIn("catalogo.crear", codigos)
        self.assertIn("movimientos.entrada", codigos)
        self.assertIn("movimientos.prestamo", codigos)
        self.assertIn("movimientos.traslado_interno", codigos)
        self.assertIn("movimientos.traslado_almacen", codigos)

    def test_bypass_administrador_y_superusuario(self):
        """El administrador y superusuario tienen todos los permisos sin necesidad de M2M."""
        self.assertTrue(self.admin.tiene_permiso("movimientos.traslado_interno"))
        self.assertTrue(self.admin.tiene_permiso("movimientos.traslado_almacen"))
        self.assertTrue(self.admin.tiene_permiso("seguridad.gestionar_usuarios"))

        self.assertTrue(self.superadmin.tiene_permiso("movimientos.traslado_interno"))
        self.assertTrue(self.superadmin.tiene_permiso("movimientos.traslado_almacen"))
        self.assertTrue(self.superadmin.tiene_permiso("seguridad.gestionar_usuarios"))

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
        self.assertFalse(self.operario.tiene_permiso("movimientos.traslado"))

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
