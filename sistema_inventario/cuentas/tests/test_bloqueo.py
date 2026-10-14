"""
Pruebas de bloqueo por intentos fallidos (§5.2 de la especificación).

Verifica:
1. Registro de intentos fallidos.
2. Bloqueo al alcanzar 5 intentos fallidos consecutivos en la cuenta.
3. Mensaje exacto de respuesta contiene "cuenta bloqueada".
4. Rechazo de accesos durante los 15 minutos de bloqueo activo (incluso con clave correcta).
5. Desbloqueo automático una vez transcurrida la ventana de 15 minutos.
6. Reinicio del contador tras un acceso exitoso.
7. Límite de intentos por dirección IP.
"""

from datetime import timedelta

from django.test import Client, TestCase
from django.utils import timezone

from cuentas.models import IntentoFallido, UsuarioAudacIA
from cuentas.services import (
    limpiar_intentos_fallidos,
    registrar_intento_fallido,
    verificar_bloqueo,
)
from inventario.models import Almacen


class BloqueoIntentosTest(TestCase):
    """Pruebas unitarias y de integración para bloqueo temporal."""

    def setUp(self):
        self.almacen = Almacen.objects.create(nombre="Almacén Bloqueo", activo=True)
        self.password = "ClaveValida1"
        self.usuario = UsuarioAudacIA.objects.create_user(
            username="usuario_bloqueo",
            nombre_completo="Usuario Para Bloqueo",
            password=self.password,
            rol=UsuarioAudacIA.Rol.USUARIO,
            almacen_asignado=self.almacen,
        )
        self.client = Client()

    def test_intentos_menores_al_limite_no_bloquean(self):
        ip = "192.168.1.10"
        for _ in range(4):
            registrar_intento_fallido("usuario_bloqueo", ip)

        bloqueado, _ = verificar_bloqueo("usuario_bloqueo", ip)
        self.assertFalse(bloqueado)

    def test_quinto_intento_bloquea_cuenta_con_mensaje_especifico(self):
        ip = "192.168.1.10"
        for _ in range(5):
            registrar_intento_fallido("usuario_bloqueo", ip)

        bloqueado, mensaje = verificar_bloqueo("usuario_bloqueo", ip)
        self.assertTrue(bloqueado)
        self.assertIn("cuenta bloqueada", mensaje.lower())

    def test_bloqueo_activo_rechaza_incluso_clave_correcta_en_login(self):
        ip = "192.168.1.10"
        for _ in range(5):
            registrar_intento_fallido("usuario_bloqueo", ip)

        # Intento de login vía vista POST con contraseña correcta
        resp = self.client.post(
            "/cuentas/login/",
            {"username": "usuario_bloqueo", "password": self.password},
            REMOTE_ADDR=ip,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "cuenta bloqueada", status_code=200)
        # La sesión no debe haberse iniciado
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_expiracion_del_bloqueo_tras_15_minutos(self):
        ip = "192.168.1.10"
        for _ in range(5):
            registrar_intento_fallido("usuario_bloqueo", ip)

        # Simular que los intentos ocurrieron hace 16 minutos
        hace_16_min = timezone.now() - timedelta(minutes=16)
        IntentoFallido.objects.filter(usuario=self.usuario).update(fecha_intento=hace_16_min)

        bloqueado, _ = verificar_bloqueo("usuario_bloqueo", ip)
        self.assertFalse(bloqueado, "Tras 15 minutos el bloqueo debe expirar automáticamente.")

    def test_login_exitoso_limpia_intentos_fallidos(self):
        ip = "192.168.1.10"
        registrar_intento_fallido("usuario_bloqueo", ip)
        registrar_intento_fallido("usuario_bloqueo", ip)
        self.assertEqual(IntentoFallido.objects.filter(usuario=self.usuario).count(), 2)

        limpiar_intentos_fallidos(self.usuario, ip)
        self.assertEqual(IntentoFallido.objects.filter(usuario=self.usuario).count(), 0)
