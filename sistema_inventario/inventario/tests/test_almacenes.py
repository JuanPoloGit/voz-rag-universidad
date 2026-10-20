"""Pruebas unitarias e integrales para Almacenes (Servicios y Vistas)."""

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, TestCase
from django.urls import reverse

from cuentas.models import UsuarioAudacIA
from inventario.models import Almacen, Auditoria
from inventario.servicios.datos_maestros import (
    actualizar_almacen,
    crear_almacen,
    reactivar_almacen,
)


class TestAlmacenServicio(TestCase):
    """Pruebas de la capa de servicios para Almacén."""

    def setUp(self):
        self.superusuario = UsuarioAudacIA.objects.create_superuser(
            username="super_test",
            password="Password123!",
            nombre_completo="Super Administrador",
            rol=UsuarioAudacIA.Rol.SUPERUSUARIO,
        )
        self.administrador = UsuarioAudacIA.objects.create_user(
            username="admin_test",
            password="Password123!",
            nombre_completo="Admin Test",
            rol=UsuarioAudacIA.Rol.ADMINISTRADOR,
        )
        self.almacen_base = Almacen.objects.create(nombre="Almacén Base", activo=True)
        self.usuario_normal = UsuarioAudacIA.objects.create_user(
            username="user_test",
            password="Password123!",
            nombre_completo="Usuario Operativo",
            rol=UsuarioAudacIA.Rol.USUARIO,
            almacen_asignado=self.almacen_base,
        )

    def test_crear_almacen_exitoso_y_auditoria(self):
        almacen = crear_almacen(
            nombre="Bodega Principal",
            descripcion="Almacén central de equipos",
            usuario=self.administrador,
            ip_origen="192.168.1.50",
        )
        self.assertIsNotNone(almacen.id)
        self.assertEqual(almacen.nombre, "Bodega Principal")
        self.assertTrue(almacen.activo)
        self.assertFalse(almacen.eliminado)

        # Verificar registro inmutable de auditoría
        auditoria = Auditoria.objects.filter(
            tipo_entidad="Almacen",
            id_entidad=str(almacen.id),
            accion="CREAR_ALMACEN",
        ).first()
        self.assertIsNotNone(auditoria)
        self.assertEqual(auditoria.usuario, self.administrador)
        self.assertEqual(auditoria.valores_nuevos["nombre"], "Bodega Principal")
        self.assertEqual(auditoria.resultado, "OK")

    def test_crear_almacen_nombre_duplicado_falla(self):
        crear_almacen(nombre="Bodega Única", usuario=self.administrador)
        with self.assertRaises(ValidationError):
            crear_almacen(nombre="Bodega Única", usuario=self.administrador)

    def test_usuario_normal_no_puede_crear_almacen(self):
        with self.assertRaises((PermissionDenied, PermissionError)):
            crear_almacen(nombre="Bodega Prohibida", usuario=self.usuario_normal)

    def test_actualizar_almacen_audita_valores_anteriores_y_nuevos(self):
        almacen = crear_almacen(nombre="Bodega Antigua", descripcion="Desc A", usuario=self.administrador)
        actualizado = actualizar_almacen(
            almacen=almacen,
            nombre="Bodega Renombrada",
            descripcion="Desc B",
            usuario=self.administrador,
            ip_origen="127.0.0.1",
        )
        self.assertEqual(actualizado.nombre, "Bodega Renombrada")
        almacen.refresh_from_db()
        self.assertEqual(almacen.nombre, "Bodega Renombrada")
        self.assertEqual(almacen.descripcion, "Desc B")

        auditoria = Auditoria.objects.filter(
            tipo_entidad="Almacen",
            id_entidad=str(almacen.id),
            accion="EDITAR_ALMACEN",
        ).first()
        self.assertIsNotNone(auditoria)
        self.assertEqual(auditoria.valores_anteriores["nombre"], "Bodega Antigua")
        self.assertEqual(auditoria.valores_nuevos["nombre"], "Bodega Renombrada")

    def test_usuario_normal_no_puede_actualizar_almacen(self):
        almacen = crear_almacen(nombre="Bodega Intacta", usuario=self.administrador)
        with self.assertRaises((PermissionDenied, PermissionError)):
            actualizar_almacen(
                almacen=almacen,
                nombre="Hack",
                descripcion="",
                usuario=self.usuario_normal,
            )

    def test_reactivar_almacen(self):
        almacen = crear_almacen(nombre="Bodega Inactiva", usuario=self.administrador)
        almacen.activo = False
        almacen.save(update_fields=["activo"])

        reactivado = reactivar_almacen(almacen=almacen, usuario=self.superusuario)
        self.assertTrue(reactivado.activo)
        almacen.refresh_from_db()
        self.assertTrue(almacen.activo)

        aud = Auditoria.objects.filter(accion="REACTIVAR_ALMACEN", id_entidad=str(almacen.id)).first()
        self.assertIsNotNone(aud)


class TestAlmacenVistas(TestCase):
    """Pruebas de vistas tradicionales para Almacén."""

    def setUp(self):
        self.client = Client()
        self.superusuario = UsuarioAudacIA.objects.create_superuser(
            username="super_view",
            password="Password123!",
            nombre_completo="Super Administrador View",
            rol=UsuarioAudacIA.Rol.SUPERUSUARIO,
        )
        self.administrador = UsuarioAudacIA.objects.create_user(
            username="admin_view",
            password="Password123!",
            nombre_completo="Admin View",
            rol=UsuarioAudacIA.Rol.ADMINISTRADOR,
        )
        self.almacen_activo = Almacen.objects.create(nombre="Almacén Activo 1", activo=True)
        self.almacen_inactivo = Almacen.objects.create(nombre="Almacén Inactivo 2", activo=False)
        self.usuario_normal = UsuarioAudacIA.objects.create_user(
            username="user_view",
            password="Password123!",
            nombre_completo="Usuario Operativo View",
            rol=UsuarioAudacIA.Rol.USUARIO,
            almacen_asignado=self.almacen_activo,
        )

    def test_lista_almacenes_requiere_autenticacion(self):
        resp = self.client.get(reverse("inventario:almacen-lista"))
        self.assertEqual(resp.status_code, 302)

    def test_lista_almacenes_filtra_inactivos_por_defecto(self):
        self.client.force_login(self.usuario_normal)
        resp = self.client.get(reverse("inventario:almacen-lista"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Almacén Activo 1")
        self.assertNotContains(resp, "Almacén Inactivo 2")

    def test_admin_puede_ver_inactivos_con_parametro(self):
        self.client.force_login(self.administrador)
        resp = self.client.get(reverse("inventario:almacen-lista") + "?mostrar_inactivos=1")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Almacén Activo 1")
        self.assertContains(resp, "Almacén Inactivo 2")

    def test_usuario_normal_denegado_en_crear_almacen(self):
        self.client.force_login(self.usuario_normal)
        resp = self.client.get(reverse("inventario:almacen-crear"))
        self.assertEqual(resp.status_code, 403)

    def test_admin_crea_almacen_por_post(self):
        self.client.force_login(self.administrador)
        resp = self.client.post(
            reverse("inventario:almacen-crear"),
            data={"nombre": "Nuevo Almacén Web", "descripcion": "Creado por formulario"},
        )
        self.assertRedirects(resp, reverse("inventario:almacen-lista"))
        nuevo = Almacen.objects.filter(nombre="Nuevo Almacén Web").first()
        self.assertIsNotNone(nuevo)
        self.assertEqual(nuevo.descripcion, "Creado por formulario")

    def test_admin_edita_almacen_por_post(self):
        self.client.force_login(self.administrador)
        resp = self.client.post(
            reverse("inventario:almacen-editar", args=[self.almacen_activo.id]),
            data={"nombre": "Almacén Activo Renombrado", "descripcion": "Nueva desc"},
        )
        self.assertRedirects(resp, reverse("inventario:almacen-lista"))
        self.almacen_activo.refresh_from_db()
        self.assertEqual(self.almacen_activo.nombre, "Almacén Activo Renombrado")
