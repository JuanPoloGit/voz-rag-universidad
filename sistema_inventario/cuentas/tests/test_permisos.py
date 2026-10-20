"""
Pruebas para el módulo de permisos y control de acceso por almacén y rol.

Verifica la capa de autorización del servidor (§5.3 y §6.2 de la especificación):
- Superusuario y Administrador: acceso total a almacenes.
- Usuario normal: acceso estrictamente limitado a su almacén asignado.
- Cuentas inactivas o eliminadas: rechazo absoluto.
- Filtrado de querysets por almacén.
- Decoradores y mixins de vistas.
"""

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.test import RequestFactory, TestCase
from django.views import View

from cuentas.models import UsuarioAudacIA
from cuentas.permisos import (
    AccesoAlmacenMixin,
    RolRequeridoMixin,
    filtrar_por_almacen,
    obtener_almacen_o_denegar,
    puede_acceder_almacen,
    requiere_rol,
)
from inventario.models import Almacen, Ubicacion


class PermisosAlmacenTest(TestCase):
    """Pruebas de funciones núcleo de permisos."""

    def setUp(self):
        self.almacen_a = Almacen.objects.create(nombre="Almacén A", activo=True)
        self.almacen_b = Almacen.objects.create(nombre="Almacén B", activo=True)
        self.almacen_inactivo = Almacen.objects.create(nombre="Almacén Inactivo", activo=False)
        self.almacen_eliminado = Almacen.objects.create(nombre="Almacén Eliminado", eliminado=True)

        self.superusuario = UsuarioAudacIA.objects.create_superuser(
            username="super_perm",
            nombre_completo="Super Permisos",
            password="Password123",
        )
        self.admin = UsuarioAudacIA.objects.create_user(
            username="admin_perm",
            nombre_completo="Admin Permisos",
            password="Password123",
            rol=UsuarioAudacIA.Rol.ADMINISTRADOR,
        )
        self.usuario_a = UsuarioAudacIA.objects.create_user(
            username="user_a",
            nombre_completo="Usuario Almacén A",
            password="Password123",
            rol=UsuarioAudacIA.Rol.USUARIO,
            almacen_asignado=self.almacen_a,
        )

    def test_superusuario_y_admin_tienen_acceso_a_almacenes_activos(self):
        self.assertTrue(puede_acceder_almacen(self.superusuario, self.almacen_a))
        self.assertTrue(puede_acceder_almacen(self.superusuario, self.almacen_b))
        self.assertTrue(puede_acceder_almacen(self.admin, self.almacen_a))
        self.assertTrue(puede_acceder_almacen(self.admin, self.almacen_b))

    def test_usuario_normal_solo_accede_a_su_almacen(self):
        self.assertTrue(puede_acceder_almacen(self.usuario_a, self.almacen_a))
        self.assertFalse(puede_acceder_almacen(self.usuario_a, self.almacen_b))

    def test_usuario_inactivo_o_eliminado_es_rechazado(self):
        self.usuario_a.is_active = False
        self.usuario_a.save()
        self.assertFalse(puede_acceder_almacen(self.usuario_a, self.almacen_a))

        self.usuario_a.is_active = True
        self.usuario_a.eliminado_logicamente = True
        self.usuario_a.save()
        self.assertFalse(puede_acceder_almacen(self.usuario_a, self.almacen_a))

    def test_anonimo_es_rechazado(self):
        anon = AnonymousUser()
        self.assertFalse(puede_acceder_almacen(anon, self.almacen_a))

    def test_almacen_inactivo_o_eliminado_es_rechazado_para_usuario_normal(self):
        self.assertFalse(puede_acceder_almacen(self.usuario_a, self.almacen_inactivo))
        self.assertFalse(puede_acceder_almacen(self.usuario_a, self.almacen_eliminado))

    def test_filtrar_por_almacen(self):
        Ubicacion.objects.create(nombre="Ubic A1", almacen=self.almacen_a)
        Ubicacion.objects.create(nombre="Ubic A2", almacen=self.almacen_a)
        Ubicacion.objects.create(nombre="Ubic B1", almacen=self.almacen_b)

        qs = Ubicacion.objects.all()

        # Administrador y Superusuario ven todo
        self.assertEqual(filtrar_por_almacen(qs, self.superusuario).count(), 3)
        self.assertEqual(filtrar_por_almacen(qs, self.admin).count(), 3)

        # Usuario A solo ve las de su almacén
        qs_filtrado = filtrar_por_almacen(qs, self.usuario_a)
        self.assertEqual(qs_filtrado.count(), 2)
        for u in qs_filtrado:
            self.assertEqual(u.almacen_id, self.almacen_a.id)

    def test_obtener_almacen_o_denegar_exitoso(self):
        almacen = obtener_almacen_o_denegar(self.usuario_a, self.almacen_a.id)
        self.assertEqual(almacen.id, self.almacen_a.id)

    def test_obtener_almacen_o_denegar_ajeno_lanza_permission_denied(self):
        with self.assertRaises(PermissionDenied):
            obtener_almacen_o_denegar(self.usuario_a, self.almacen_b.id)


class DecoradoresYMixinsTest(TestCase):
    """Pruebas de decoradores y mixins de control de acceso."""

    def setUp(self):
        self.factory = RequestFactory()
        self.almacen = Almacen.objects.create(nombre="Almacén Test", activo=True)
        self.almacen_ajeno = Almacen.objects.create(nombre="Almacén Ajeno", activo=True)

        self.admin = UsuarioAudacIA.objects.create_user(
            username="admin_view",
            nombre_completo="Admin Vista",
            password="Password123",
            rol=UsuarioAudacIA.Rol.ADMINISTRADOR,
        )
        self.usuario = UsuarioAudacIA.objects.create_user(
            username="user_view",
            nombre_completo="User Vista",
            password="Password123",
            rol=UsuarioAudacIA.Rol.USUARIO,
            almacen_asignado=self.almacen,
        )

    def test_decorador_requiere_rol_permite_acceso_autorizado(self):
        @requiere_rol(UsuarioAudacIA.Rol.ADMINISTRADOR, UsuarioAudacIA.Rol.SUPERUSUARIO)
        def mi_vista(request):
            return HttpResponse("OK")

        req = self.factory.get("/")
        req.user = self.admin
        resp = mi_vista(req)
        self.assertEqual(resp.content.decode(), "OK")

    def test_decorador_requiere_rol_deniega_acceso_no_autorizado(self):
        @requiere_rol(UsuarioAudacIA.Rol.ADMINISTRADOR)
        def mi_vista(request):
            return HttpResponse("OK")

        req = self.factory.get("/")
        req.user = self.usuario
        with self.assertRaises(PermissionDenied):
            mi_vista(req)

    def test_mixin_acceso_almacen_permite_acceso_autorizado(self):
        class VistaProtegida(AccesoAlmacenMixin, View):
            def get(self, request, *args, **kwargs):
                return HttpResponse(f"Almacén: {self.almacen.nombre}")

        req = self.factory.get(f"/?almacen_id={self.almacen.id}")
        req.user = self.usuario
        vista = VistaProtegida.as_view()
        resp = vista(req)
        self.assertIn("Almacén Test", resp.content.decode())

    def test_mixin_acceso_almacen_deniega_almacen_ajeno(self):
        class VistaProtegida(AccesoAlmacenMixin, View):
            def get(self, request, *args, **kwargs):
                return HttpResponse("OK")

        req = self.factory.get(f"/?almacen_id={self.almacen_ajeno.id}")
        req.user = self.usuario
        vista = VistaProtegida.as_view()
        with self.assertRaises(PermissionDenied):
            vista(req)
