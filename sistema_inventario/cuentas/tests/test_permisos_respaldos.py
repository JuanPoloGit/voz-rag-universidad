"""
Pruebas de autorización y mixins para Copias de Seguridad y Centro de Exportaciones.
"""

from django.core.exceptions import PermissionDenied
from django.test import RequestFactory, TestCase
from django.views.generic import View

from cuentas.models import UsuarioAudacIA
from cuentas.permisos import AdministradorRequeridoMixin, SuperusuarioRequeridoMixin
from inventario.models import Almacen


class DummySuperView(SuperusuarioRequeridoMixin, View):
    def get(self, request):
        from django.http import HttpResponse
        return HttpResponse("OK_SUPER")


class DummyAdminView(AdministradorRequeridoMixin, View):
    def get(self, request):
        from django.http import HttpResponse
        return HttpResponse("OK_ADMIN")


class PermisosRespaldosYExportacionesTest(TestCase):
    def setUp(self):
        self.rf = RequestFactory()
        self.almacen = Almacen.objects.create(nombre="Almacén Central")

        self.superadmin = UsuarioAudacIA.objects.create_superuser(
            username="super_test",
            nombre_completo="Super Administrador",
            email="super@audacia.edu.co",
            password="Password123!",
            rol=UsuarioAudacIA.Rol.SUPERUSUARIO,
        )

        self.admin = UsuarioAudacIA.objects.create_user(
            username="admin_test",
            nombre_completo="Administrador Prueba",
            email="admin@audacia.edu.co",
            password="Password123!",
            rol=UsuarioAudacIA.Rol.ADMINISTRADOR,
        )

        self.operador = UsuarioAudacIA.objects.create_user(
            username="operador_test",
            nombre_completo="Operador Prueba",
            email="operador@audacia.edu.co",
            password="Password123!",
            rol=UsuarioAudacIA.Rol.USUARIO,
            almacen_asignado=self.almacen,
        )

    def test_superusuario_requerido_mixin(self):
        view = DummySuperView.as_view()

        # 1. Superadministrador tiene acceso
        req_super = self.rf.get("/seguridad/respaldos/")
        req_super.user = self.superadmin
        resp_super = view(req_super)
        self.assertEqual(resp_super.status_code, 200)

        # 2. Administrador NO tiene acceso (exclusivo Superadmin)
        req_admin = self.rf.get("/seguridad/respaldos/")
        req_admin.user = self.admin
        with self.assertRaises(PermissionDenied):
            view(req_admin)

        # 3. Operador normal NO tiene acceso
        req_op = self.rf.get("/seguridad/respaldos/")
        req_op.user = self.operador
        with self.assertRaises(PermissionDenied):
            view(req_op)

        # 4. Superadministrador inactivo es rechazado
        self.superadmin.is_active = False
        self.superadmin.save()
        with self.assertRaises(PermissionDenied):
            view(req_super)

    def test_administrador_requerido_mixin(self):
        view = DummyAdminView.as_view()

        # 1. Superadministrador tiene acceso
        req_super = self.rf.get("/reportes/exportaciones/")
        req_super.user = self.superadmin
        resp_super = view(req_super)
        self.assertEqual(resp_super.status_code, 200)

        # 2. Administrador tiene acceso
        req_admin = self.rf.get("/reportes/exportaciones/")
        req_admin.user = self.admin
        resp_admin = view(req_admin)
        self.assertEqual(resp_admin.status_code, 200)

        # 3. Operador normal NO tiene acceso
        req_op = self.rf.get("/reportes/exportaciones/")
        req_op.user = self.operador
        with self.assertRaises(PermissionDenied):
            view(req_op)
