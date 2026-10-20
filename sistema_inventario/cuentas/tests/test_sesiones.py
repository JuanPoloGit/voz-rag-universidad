"""
Pruebas de sesiones, caducidad, logout y cambio de contraseña (§6.4 de la especificación).

Verifica:
1. "Recordar este dispositivo durante 7 días": sesión persistente en servidor.
2. Sin "Recordar": caducidad a las 8 horas de inactividad o browser close.
3. Logout seguro e invalidación de sesión.
4. Cambio de contraseña propio exigiendo contraseña actual y validador letra+número.
"""

from django.conf import settings
from django.test import Client, TestCase

from cuentas.models import UsuarioAudacIA
from inventario.models import Almacen, Auditoria


class SesionesYPasswordTest(TestCase):
    """Pruebas de gestión de sesiones y cambio de clave."""

    def setUp(self):
        self.almacen = Almacen.objects.create(nombre="Almacén Sesiones", activo=True)
        self.password_inicial = "ClaveValida1"
        self.usuario = UsuarioAudacIA.objects.create_user(
            username="usuario_sesion",
            nombre_completo="Usuario Sesión",
            password=self.password_inicial,
            rol=UsuarioAudacIA.Rol.USUARIO,
            almacen_asignado=self.almacen,
        )
        self.client = Client()

    def test_login_con_recordar_establece_7_dias(self):
        resp = self.client.post(
            "/cuentas/login/",
            {
                "username": "usuario_sesion",
                "password": self.password_inicial,
                "recordar": "on",
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "/")
        self.assertEqual(
            self.client.session.get_expiry_age(),
            7 * 86400,
        )

    def test_login_sin_recordar_establece_8_horas(self):
        resp = self.client.post(
            "/cuentas/login/",
            {
                "username": "usuario_sesion",
                "password": self.password_inicial,
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "/")
        self.assertEqual(
            self.client.session.get_expiry_age(),
            settings.SESSION_IDLE_TIMEOUT_SECONDS,
        )

    def test_logout_invalida_sesion_en_servidor(self):
        self.client.login(username="usuario_sesion", password=self.password_inicial)
        self.assertIn("_auth_user_id", self.client.session)

        resp = self.client.post("/cuentas/logout/")
        self.assertRedirects(resp, "/cuentas/login/")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_cambio_password_exitoso_y_auditado(self):
        self.client.login(username="usuario_sesion", password=self.password_inicial)
        nueva_clave = "NuevaClave99"

        resp = self.client.post(
            "/cuentas/cambiar-password/",
            {
                "password_actual": self.password_inicial,
                "password_nueva": nueva_clave,
                "password_confirmacion": nueva_clave,
            },
        )
        self.assertEqual(resp.status_code, 302)

        # Verificar que la nueva clave funciona
        self.usuario.refresh_from_db()
        self.assertTrue(self.usuario.check_password(nueva_clave))

        # Verificar registro en auditoría
        auditoria = Auditoria.objects.filter(
            accion="CAMBIAR_PASSWORD", id_entidad=str(self.usuario.id)
        ).first()
        self.assertIsNotNone(auditoria)
        self.assertEqual(auditoria.usuario, self.usuario)

    def test_cambio_password_rechaza_clave_sin_numero(self):
        self.client.login(username="usuario_sesion", password=self.password_inicial)
        clave_invalida = "Sololetras"

        resp = self.client.post(
            "/cuentas/cambiar-password/",
            {
                "password_actual": self.password_inicial,
                "password_nueva": clave_invalida,
                "password_confirmacion": clave_invalida,
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "letra y un número")
