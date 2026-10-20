"""
Pruebas para la gestión de cuentas de usuario por rol (§6.2 y §6.3).

Verifica:
1. Permisos de acceso: solo Administradores y Superusuario pueden gestionar cuentas.
2. Rol Administrador: solo puede crear y editar usuarios con rol USUARIO.
3. Rol Superusuario: único autorizado para crear o editar Administradores.
4. Protección del Superusuario: un Administrador no puede editarlo ni desactivarlo.
5. Eliminación lógica: marca eliminado_logicamente=True e is_active=False sin borrar físicamente.
6. Registro obligatorio de auditoría en cada mutación de cuentas.
"""

from django.core.exceptions import PermissionDenied
from django.test import Client, TestCase

from cuentas.models import UsuarioAudacIA
from inventario.models import Almacen, Auditoria


class GestionCuentasTest(TestCase):
    """Pruebas de vistas y formularios de gestión de usuarios."""

    def setUp(self):
        self.almacen = Almacen.objects.create(nombre="Almacén Gestión", activo=True)
        self.client = Client()

        self.superusuario = UsuarioAudacIA.objects.create_superuser(
            username="super_gestor",
            nombre_completo="Super Gestor",
            password="Password123",
        )
        self.admin = UsuarioAudacIA.objects.create_user(
            username="admin_gestor",
            nombre_completo="Admin Gestor",
            password="Password123",
            rol=UsuarioAudacIA.Rol.ADMINISTRADOR,
        )
        self.usuario = UsuarioAudacIA.objects.create_user(
            username="operador_gestor",
            nombre_completo="Operador Gestor",
            password="Password123",
            rol=UsuarioAudacIA.Rol.USUARIO,
            almacen_asignado=self.almacen,
        )

    def test_usuario_normal_no_puede_acceder_a_lista_usuarios(self):
        self.client.login(username="operador_gestor", password="Password123")
        resp = self.client.get("/cuentas/usuarios/")
        self.assertEqual(resp.status_code, 403)

    def test_admin_puede_listar_usuarios(self):
        self.client.login(username="admin_gestor", password="Password123")
        resp = self.client.get("/cuentas/usuarios/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "operador_gestor")

    def test_admin_puede_crear_usuario_normal_con_auditoria(self):
        self.client.login(username="admin_gestor", password="Password123")
        resp = self.client.post(
            "/cuentas/usuarios/crear/",
            {
                "username": "nuevo_operador",
                "nombre_completo": "Nuevo Operador",
                "password": "Password123",
                "rol": UsuarioAudacIA.Rol.USUARIO,
                "almacen_asignado": self.almacen.id,
            },
        )
        self.assertEqual(resp.status_code, 302)

        nuevo = UsuarioAudacIA.objects.filter(username="nuevo_operador").first()
        self.assertIsNotNone(nuevo)
        self.assertEqual(nuevo.rol, UsuarioAudacIA.Rol.USUARIO)

        # Verificar auditoría
        audit = Auditoria.objects.filter(accion="CREAR_USUARIO", id_entidad=str(nuevo.id)).first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.usuario, self.admin)

    def test_admin_no_puede_crear_otro_administrador(self):
        self.client.login(username="admin_gestor", password="Password123")
        resp = self.client.post(
            "/cuentas/usuarios/crear/",
            {
                "username": "intento_admin",
                "nombre_completo": "Intento Admin",
                "password": "Password123",
                "rol": UsuarioAudacIA.Rol.ADMINISTRADOR,
            },
        )
        # Debe rechazar en el formulario o denegar
        self.assertFalse(UsuarioAudacIA.objects.filter(username="intento_admin").exists())

    def test_superusuario_puede_crear_administrador(self):
        self.client.login(username="super_gestor", password="Password123")
        resp = self.client.post(
            "/cuentas/usuarios/crear/",
            {
                "username": "segundo_admin",
                "nombre_completo": "Segundo Admin",
                "password": "Password123",
                "rol": UsuarioAudacIA.Rol.ADMINISTRADOR,
            },
        )
        self.assertEqual(resp.status_code, 302)
        admin_creado = UsuarioAudacIA.objects.filter(username="segundo_admin").first()
        self.assertIsNotNone(admin_creado)
        self.assertEqual(admin_creado.rol, UsuarioAudacIA.Rol.ADMINISTRADOR)

    def test_admin_no_puede_modificar_al_superusuario(self):
        self.client.login(username="admin_gestor", password="Password123")
        resp = self.client.get(f"/cuentas/usuarios/{self.superusuario.id}/editar/")
        self.assertEqual(resp.status_code, 403)

    def test_eliminacion_logica_usuario(self):
        self.client.login(username="admin_gestor", password="Password123")
        resp = self.client.post(f"/cuentas/usuarios/{self.usuario.id}/eliminar/")
        self.assertEqual(resp.status_code, 302)

        self.usuario.refresh_from_db()
        self.assertTrue(self.usuario.eliminado_logicamente)
        self.assertFalse(self.usuario.is_active)

        # Registro físico debe conservarse en base de datos
        self.assertTrue(UsuarioAudacIA.objects.filter(id=self.usuario.id).exists())

        # Auditoría registrada
        audit = Auditoria.objects.filter(
            accion="ELIMINAR_LOGICAMENTE", id_entidad=str(self.usuario.id)
        ).first()
        self.assertIsNotNone(audit)

    def test_superusuario_no_puede_ser_eliminado_logicamente(self):
        self.client.login(username="admin_gestor", password="Password123")
        resp = self.client.post(f"/cuentas/usuarios/{self.superusuario.id}/eliminar/")
        self.assertEqual(resp.status_code, 403)
        self.superusuario.refresh_from_db()
        self.assertFalse(self.superusuario.eliminado_logicamente)
