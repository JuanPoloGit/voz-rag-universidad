"""
Pruebas para el backend de autenticación UsuarioAudacIABackend.

Verifica que usuarios inactivos o con eliminado_logicamente=True
sean rechazados en cualquier intento de autenticación (sección 6.3).
"""

from django.contrib.auth import authenticate
from django.test import TestCase

from cuentas.backends import UsuarioAudacIABackend
from cuentas.models import UsuarioAudacIA
from inventario.models import Almacen


class UsuarioAudacIABackendTest(TestCase):
    """Pruebas del backend de autenticación personalizado."""

    def setUp(self):
        self.backend = UsuarioAudacIABackend()
        self.password = "Secr3to123"
        self.almacen = Almacen.objects.create(nombre="Almacén Backend Test", activo=True)
        self.usuario = UsuarioAudacIA.objects.create_user(
            username="operador_test",
            nombre_completo="Operador de Prueba",
            password=self.password,
            rol=UsuarioAudacIA.Rol.USUARIO,
            almacen_asignado=self.almacen,
        )

    def test_usuario_activo_y_no_eliminado_puede_autenticarse(self):
        self.assertTrue(self.backend.user_can_authenticate(self.usuario))
        user_auth = authenticate(username="operador_test", password=self.password)
        self.assertIsNotNone(user_auth)
        self.assertEqual(user_auth.pk, self.usuario.pk)

    def test_usuario_inactivo_no_puede_autenticarse(self):
        self.usuario.is_active = False
        self.usuario.save()

        self.assertFalse(self.backend.user_can_authenticate(self.usuario))
        user_auth = authenticate(username="operador_test", password=self.password)
        self.assertIsNone(user_auth)

    def test_usuario_eliminado_logicamente_no_puede_autenticarse(self):
        self.usuario.eliminado_logicamente = True
        self.usuario.save()

        self.assertFalse(self.backend.user_can_authenticate(self.usuario))
        user_auth = authenticate(username="operador_test", password=self.password)
        self.assertIsNone(user_auth)
