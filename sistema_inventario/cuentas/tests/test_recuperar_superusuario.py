"""
Pruebas para el comando administrativo recuperar_superusuario (§8.1, Decisión 6).
"""

from io import StringIO
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from cuentas.models import UsuarioAudacIA
from cuentas.services import registrar_intento_fallido, verificar_bloqueo
from inventario.models import Auditoria


class RecuperarSuperusuarioCommandTests(TestCase):
    """Pruebas del comando de recuperación de superusuario."""

    def tearDown(self):
        cache.clear()
        super().tearDown()

    def test_falla_si_no_existe_superusuario(self):
        """Si no hay ningún superusuario en la base de datos, debe fallar con CommandError."""
        self.assertEqual(UsuarioAudacIA.objects.filter(rol=UsuarioAudacIA.Rol.SUPERUSUARIO).count(), 0)
        
        out = StringIO()
        err = StringIO()
        with self.assertRaises(CommandError) as ctx:
            call_command("recuperar_superusuario", password="Password123", stdout=out, stderr=err)
        
        self.assertIn("No se encontró ningún superusuario", str(ctx.exception))

    def test_recupera_superusuario_existente_con_exito(self):
        """Debe reactivar al superusuario, limpiar bloqueos, actualizar password y registrar auditoría."""
        super_user = UsuarioAudacIA.objects.create_superuser(
            username="superadmin",
            password="OldPassword123",
            nombre_completo="Super Admin Inicial"
        )
        # Simular que el superusuario quedó bloqueado, inactivo y eliminado lógicamente
        super_user.is_active = False
        super_user.eliminado_logicamente = True
        super_user.save()

        # Simular intentos fallidos y bloqueo en caché
        for _ in range(5):
            registrar_intento_fallido("superadmin", "127.0.0.1")
        
        bloqueado, _ = verificar_bloqueo("superadmin", "127.0.0.1")
        self.assertTrue(bloqueado)

        out = StringIO()
        call_command(
            "recuperar_superusuario",
            username="superadmin",
            password="NuevaPassword456",
            stdout=out
        )

        super_user.refresh_from_db()
        self.assertTrue(super_user.is_active)
        self.assertFalse(super_user.eliminado_logicamente)
        self.assertTrue(super_user.check_password("NuevaPassword456"))

        # Verificar que el bloqueo en caché se limpió
        bloqueado_post, _ = verificar_bloqueo("superadmin", "127.0.0.1")
        self.assertFalse(bloqueado_post)

        # Verificar auditoría
        auditoria = Auditoria.objects.filter(
            accion="RECUPERAR_SUPERUSUARIO",
            id_entidad=str(super_user.id)
        ).first()
        self.assertIsNotNone(auditoria)
        self.assertEqual(auditoria.usuario, super_user)
        self.assertIn("recuperación administrativa", auditoria.motivo)

    def test_recupera_buscando_automaticamente_al_unico_superusuario(self):
        """Si no se especifica --username, debe ubicar al superusuario único automáticamente."""
        super_user = UsuarioAudacIA.objects.create_superuser(
            username="root_admin",
            password="PasswordOriginal123",
            nombre_completo="Root Admin"
        )
        super_user.is_active = False
        super_user.save()

        out = StringIO()
        call_command("recuperar_superusuario", password="PasswordNueva789", stdout=out)

        super_user.refresh_from_db()
        self.assertTrue(super_user.is_active)
        self.assertTrue(super_user.check_password("PasswordNueva789"))
        self.assertIn("root_admin", out.getvalue())

    def test_valida_politica_contrasenas(self):
        """Si la nueva contraseña no cumple LetraYNumeroValidator, debe rechazarla con CommandError."""
        UsuarioAudacIA.objects.create_superuser(
            username="superadmin",
            password="OldPassword123",
            nombre_completo="Super Admin"
        )

        # Clave menor a 7 caracteres y sin números
        with self.assertRaises(CommandError) as ctx:
            call_command("recuperar_superusuario", username="superadmin", password="abc")
        
        self.assertTrue(len(str(ctx.exception)) > 0)

    def test_falla_si_usuario_especificado_no_es_superusuario(self):
        """Si se especifica un username de un usuario común o administrador, debe fallar."""
        from inventario.models import Almacen
        almacen = Almacen.objects.create(nombre="Almacén 1")
        UsuarioAudacIA.objects.create_user(
            username="usuario_normal",
            password="Password123",
            nombre_completo="Usuario Normal",
            rol=UsuarioAudacIA.Rol.USUARIO,
            almacen_asignado=almacen
        )

        with self.assertRaises(CommandError) as ctx:
            call_command("recuperar_superusuario", username="usuario_normal", password="PasswordNueva123")

        self.assertIn("no tiene privilegios de superusuario", str(ctx.exception))
