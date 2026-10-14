"""
Pruebas para las invariantes de negocio del modelo UsuarioAudacIA.

Reglas clave de la especificación (§6.2):
- Superusuario: único e ineliminable.
- Administrador: acceso global, almacen_asignado es null.
- Usuario: tiene asignado obligatoriamente un único almacén.
- Coherencia de flags is_superuser e is_staff según el rol.
"""

from django.core.exceptions import ValidationError
from django.test import TestCase

from cuentas.models import UsuarioAudacIA
from inventario.models import Almacen


class UsuarioInvariantesTest(TestCase):
    """Verifica reglas de negocio e integridad en el modelo de usuario."""

    def setUp(self):
        self.almacen = Almacen.objects.create(nombre="Almacén Central", activo=True)

    def test_creacion_superusuario_valido(self):
        super_user = UsuarioAudacIA.objects.create_superuser(
            username="superadmin",
            nombre_completo="Super Administrador",
            password="Password123",
        )
        self.assertEqual(super_user.rol, UsuarioAudacIA.Rol.SUPERUSUARIO)
        self.assertTrue(super_user.is_superuser)
        self.assertTrue(super_user.is_staff)
        self.assertIsNone(super_user.almacen_asignado)

    def test_impedir_segundo_superusuario(self):
        UsuarioAudacIA.objects.create_superuser(
            username="super1",
            nombre_completo="Primer Super",
            password="Password123",
        )
        with self.assertRaises(ValidationError):
            UsuarioAudacIA.objects.create(
                username="super2",
                nombre_completo="Segundo Super",
                rol=UsuarioAudacIA.Rol.SUPERUSUARIO,
            )

    def test_superusuario_es_ineliminable(self):
        super_user = UsuarioAudacIA.objects.create_superuser(
            username="super_fijo",
            nombre_completo="Super Fijo",
            password="Password123",
        )
        with self.assertRaises(ValidationError):
            super_user.delete()

    def test_usuario_normal_exige_almacen(self):
        # Intentar crear un usuario normal sin almacén debe fallar
        usuario_sin_almacen = UsuarioAudacIA(
            username="operador_huerfano",
            nombre_completo="Operador Sin Almacén",
            rol=UsuarioAudacIA.Rol.USUARIO,
            almacen_asignado=None,
        )
        with self.assertRaises(ValidationError):
            usuario_sin_almacen.save()

    def test_usuario_normal_con_almacen_exitoso(self):
        usuario = UsuarioAudacIA.objects.create_user(
            username="operador_valido",
            nombre_completo="Operador Válido",
            password="Password123",
            rol=UsuarioAudacIA.Rol.USUARIO,
            almacen_asignado=self.almacen,
        )
        self.assertEqual(usuario.almacen_asignado, self.almacen)
        self.assertFalse(usuario.is_superuser)
        self.assertFalse(usuario.is_staff)

    def test_administrador_limpia_almacen_y_ajusta_flags(self):
        admin_user = UsuarioAudacIA.objects.create_user(
            username="admin_general",
            nombre_completo="Admin General",
            password="Password123",
            rol=UsuarioAudacIA.Rol.ADMINISTRADOR,
            almacen_asignado=self.almacen,  # Se envía con almacén para verificar limpieza
        )
        self.assertIsNone(admin_user.almacen_asignado)
        self.assertFalse(admin_user.is_superuser)
        self.assertTrue(admin_user.is_staff)
