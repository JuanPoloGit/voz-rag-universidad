"""Pruebas unitarias e integrales para Ubicaciones (Servicios y Vistas)."""

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, TestCase
from django.urls import reverse

from cuentas.models import UsuarioAudacIA
from inventario.models import Almacen, Auditoria, Ubicacion
from inventario.servicios.datos_maestros import (
    actualizar_ubicacion,
    crear_ubicacion,
)


class TestUbicacionServicio(TestCase):
    """Pruebas de la capa de servicios para Ubicación."""

    def setUp(self):
        self.superusuario = UsuarioAudacIA.objects.create_superuser(
            username="super_ubic",
            password="Password123!",
            nombre_completo="Super Administrador Ubic",
            rol=UsuarioAudacIA.Rol.SUPERUSUARIO,
        )
        self.administrador = UsuarioAudacIA.objects.create_user(
            username="admin_ubic",
            password="Password123!",
            nombre_completo="Admin Ubic",
            rol=UsuarioAudacIA.Rol.ADMINISTRADOR,
        )
        self.almacen_a = Almacen.objects.create(nombre="Bodega Norte", activo=True)
        self.almacen_b = Almacen.objects.create(nombre="Bodega Sur", activo=True)

        self.usuario_norte = UsuarioAudacIA.objects.create_user(
            username="user_norte",
            password="Password123!",
            nombre_completo="Operario Norte",
            rol=UsuarioAudacIA.Rol.USUARIO,
            almacen_asignado=self.almacen_a,
        )
        self.usuario_sur = UsuarioAudacIA.objects.create_user(
            username="user_sur",
            password="Password123!",
            nombre_completo="Operario Sur",
            rol=UsuarioAudacIA.Rol.USUARIO,
            almacen_asignado=self.almacen_b,
        )

    def test_crear_ubicacion_exitoso_y_auditoria(self):
        ubic = crear_ubicacion(
            nombre="Estante A-1",
            almacen=self.almacen_a,
            descripcion="Fila superior",
            usuario=self.usuario_norte,
            ip_origen="192.168.1.10",
        )
        self.assertIsNotNone(ubic.id)
        self.assertEqual(ubic.nombre, "Estante A-1")
        self.assertEqual(ubic.almacen, self.almacen_a)

        auditoria = Auditoria.objects.filter(
            tipo_entidad="Ubicacion",
            id_entidad=str(ubic.id),
            accion="CREAR_UBICACION",
        ).first()
        self.assertIsNotNone(auditoria)
        self.assertEqual(auditoria.usuario, self.usuario_norte)
        self.assertEqual(auditoria.valores_nuevos["nombre"], "Estante A-1")
        self.assertEqual(auditoria.valores_nuevos["almacen_id"], self.almacen_a.id)

    def test_nombre_duplicado_en_mismo_almacen_falla(self):
        crear_ubicacion(nombre="Zona C", almacen=self.almacen_a, usuario=self.administrador)
        with self.assertRaises(ValidationError):
            crear_ubicacion(nombre="Zona C", almacen=self.almacen_a, usuario=self.administrador)

    def test_nombre_duplicado_en_distinto_almacen_permitido(self):
        u1 = crear_ubicacion(nombre="Zona C", almacen=self.almacen_a, usuario=self.administrador)
        u2 = crear_ubicacion(nombre="Zona C", almacen=self.almacen_b, usuario=self.administrador)
        self.assertEqual(u1.nombre, u2.nombre)
        self.assertNotEqual(u1.almacen_id, u2.almacen_id)

    def test_usuario_normal_no_puede_crear_en_almacen_ajeno(self):
        with self.assertRaises(PermissionDenied):
            crear_ubicacion(
                nombre="Estante Invasor",
                almacen=self.almacen_b,
                usuario=self.usuario_norte,  # Pertenece a almacen_a
            )

    def test_renombrar_ubicacion_conserva_historial_anterior_en_auditoria(self):
        ubic = crear_ubicacion(nombre="Estante Viejo", almacen=self.almacen_a, usuario=self.usuario_norte)
        actualizada = actualizar_ubicacion(
            ubicacion=ubic,
            nombre="Estante Renombrado",
            descripcion="Actualizado",
            usuario=self.usuario_norte,
            ip_origen="127.0.0.1",
        )
        self.assertEqual(actualizada.nombre, "Estante Renombrado")
        ubic.refresh_from_db()
        self.assertEqual(ubic.nombre, "Estante Renombrado")

        # Regla §7.2: conservar historial del nombre anterior en auditoría
        aud = Auditoria.objects.filter(
            tipo_entidad="Ubicacion",
            id_entidad=str(ubic.id),
            accion="EDITAR_UBICACION",
        ).first()
        self.assertIsNotNone(aud)
        self.assertEqual(aud.valores_anteriores["nombre"], "Estante Viejo")
        self.assertEqual(aud.valores_nuevos["nombre"], "Estante Renombrado")

    def test_usuario_normal_no_puede_editar_ubicacion_almacen_ajeno(self):
        ubic_sur = crear_ubicacion(nombre="Rack Sur 1", almacen=self.almacen_b, usuario=self.administrador)
        with self.assertRaises(PermissionDenied):
            actualizar_ubicacion(
                ubicacion=ubic_sur,
                nombre="Hack Sur",
                descripcion="",
                usuario=self.usuario_norte,
            )


class TestUbicacionVistas(TestCase):
    """Pruebas de vistas tradicionales para Ubicación."""

    def setUp(self):
        self.client = Client()
        self.administrador = UsuarioAudacIA.objects.create_user(
            username="admin_view_u",
            password="Password123!",
            nombre_completo="Admin View Ubic",
            rol=UsuarioAudacIA.Rol.ADMINISTRADOR,
        )
        self.almacen_a = Almacen.objects.create(nombre="Almacén Alpha", activo=True)
        self.almacen_b = Almacen.objects.create(nombre="Almacén Beta", activo=True)

        self.ubic_a = Ubicacion.objects.create(almacen=self.almacen_a, nombre="Cajón Alpha 1")
        self.ubic_b = Ubicacion.objects.create(almacen=self.almacen_b, nombre="Cajón Beta 1")

        self.usuario_alpha = UsuarioAudacIA.objects.create_user(
            username="user_alpha",
            password="Password123!",
            nombre_completo="Usuario Alpha",
            rol=UsuarioAudacIA.Rol.USUARIO,
            almacen_asignado=self.almacen_a,
        )

    def test_usuario_normal_solo_ve_ubicaciones_de_su_almacen(self):
        self.client.force_login(self.usuario_alpha)
        resp = self.client.get(reverse("inventario:ubicacion-lista"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Cajón Alpha 1")
        self.assertNotContains(resp, "Cajón Beta 1")

    def test_admin_ve_todas_las_ubicaciones(self):
        self.client.force_login(self.administrador)
        resp = self.client.get(reverse("inventario:ubicacion-lista"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Cajón Alpha 1")
        self.assertContains(resp, "Cajón Beta 1")

    def test_usuario_crea_ubicacion_en_su_almacen(self):
        self.client.force_login(self.usuario_alpha)
        resp = self.client.post(
            reverse("inventario:ubicacion-crear"),
            data={"nombre": "Nuevo Cajón Alpha", "descripcion": ""},
        )
        self.assertRedirects(resp, reverse("inventario:ubicacion-lista"))
        nueva = Ubicacion.objects.filter(nombre="Nuevo Cajón Alpha").first()
        self.assertIsNotNone(nueva)
        self.assertEqual(nueva.almacen, self.almacen_a)

    def test_usuario_intento_crear_en_almacen_ajeno_se_ignora_o_fuerza_a_su_almacen(self):
        self.client.force_login(self.usuario_alpha)
        # Envía almacen_id de almacen_b en el POST
        resp = self.client.post(
            reverse("inventario:ubicacion-crear"),
            data={"nombre": "Infiltrado", "descripcion": "", "almacen": self.almacen_b.id},
        )
        self.assertRedirects(resp, reverse("inventario:ubicacion-lista"))
        ubic = Ubicacion.objects.get(nombre="Infiltrado")
        # El backend forzó su almacén asignado Alpha
        self.assertEqual(ubic.almacen, self.almacen_a)
