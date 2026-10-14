"""
Pruebas de configuración de seguridad y plataforma — Inventario AudacIA.

Verifica que las directivas de seguridad requeridas por la especificación
estén activas en settings.py.
"""

from django.conf import settings
from django.test import SimpleTestCase


class ConfigSeguridadTest(SimpleTestCase):
    """Verifica parámetros de seguridad en settings."""

    def test_modelo_usuario_personalizado(self):
        self.assertEqual(settings.AUTH_USER_MODEL, "cuentas.UsuarioAudacIA")

    def test_backend_autenticacion_personalizado(self):
        self.assertIn(
            "cuentas.backends.UsuarioAudacIABackend",
            settings.AUTHENTICATION_BACKENDS,
            "Debe incluirse el backend personalizado para filtrar cuentas inactivas y eliminadas lógicamente.",
        )

    def test_validador_letra_y_numero(self):
        nombres = [v.get("NAME") for v in settings.AUTH_PASSWORD_VALIDATORS]
        self.assertIn("cuentas.validators.LetraYNumeroValidator", nombres)

    def test_politica_bloqueo_intentos_fallidos(self):
        self.assertEqual(settings.LOGIN_MAX_ATTEMPTS, 5)
        self.assertEqual(settings.LOGIN_LOCKOUT_MINUTES, 15)
        self.assertTrue(hasattr(settings, "LOGIN_IP_MAX_ATTEMPTS"))

    def test_politica_inactividad_sesion(self):
        self.assertTrue(hasattr(settings, "SESSION_IDLE_TIMEOUT_SECONDS"))
        self.assertEqual(settings.SESSION_IDLE_TIMEOUT_SECONDS, 28800)

    def test_motor_base_datos_valido(self):
        motor = settings.DATABASES["default"]["ENGINE"]
        self.assertIn(
            motor,
            [
                "django.db.backends.sqlite3",
                "django.db.backends.postgresql",
            ],
            "El motor de base de datos debe ser SQLite o PostgreSQL.",
        )
