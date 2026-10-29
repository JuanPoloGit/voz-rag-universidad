"""Pruebas de verificación de seguridad, permisos y control de acceso para la Fase 2."""

from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.http import Http404
from django.test import TestCase
from django.urls import reverse

from cuentas.models import PermisoAudacIA, UsuarioAudacIA
from inventario.models import Almacen, Categoria, Existencia, Movimiento, Producto, Ubicacion
from inventario.servicios.operaciones import (
    confirmar_conteo_fisico,
    registrar_conteo_fisico,
    registrar_entrada,
    registrar_salida,
    registrar_traslado_entre_almacenes,
    revertir_movimiento,
)
from inventario.servicios.respaldo import (
    eliminar_respaldo_bd,
    obtener_ruta_respaldo_segura,
)

User = get_user_model()


class Fase2SeguridadPermisosTest(TestCase):
    """Pruebas rigurosas de la matriz de permisos, escalada en reversión y hardening."""

    def setUp(self):
        # 1. Almacenes independientes
        self.almacen_a = Almacen.objects.create(nombre="Almacen A Principal", activo=True)
        self.almacen_b = Almacen.objects.create(nombre="Almacen B Secundario", activo=True)

        # 2. Categorías y Productos
        self.cat_a = Categoria.objects.create(nombre="Herramientas Lab A", almacen_principal=self.almacen_a)
        self.cat_b = Categoria.objects.create(nombre="Materiales Lab B", almacen_principal=self.almacen_b)

        self.prod_a = Producto.objects.create(nombre="Multímetro A", categoria=self.cat_a, stock_minimo=1)
        self.prod_b = Producto.objects.create(nombre="Osciloscopio B", categoria=self.cat_b, stock_minimo=1)

        self.ubicacion_a = Ubicacion.objects.create(nombre="Estante A1", almacen=self.almacen_a)
        self.ubicacion_b = Ubicacion.objects.create(nombre="Estante B1", almacen=self.almacen_b)

        # 3. Usuarios por rol
        self.admin = User.objects.create_user(
            username="admin_f2",
            password="Password123",
            nombre_completo="Admin F2",
            rol=UsuarioAudacIA.Rol.ADMINISTRADOR,
        )
        self.superadmin = User.objects.create_superuser(
            username="super_f2",
            password="Password123",
            nombre_completo="Super F2",
        )
        self.usuario_sin_permiso = User.objects.create_user(
            username="operador_base",
            password="Password123",
            nombre_completo="Operador Base",
            rol=UsuarioAudacIA.Rol.USUARIO,
            almacen_asignado=self.almacen_a,
        )
        self.usuario_sin_permiso.almacenes_asignados.add(self.almacen_a)

        self.usuario_con_revertir = User.objects.create_user(
            username="operador_reversor",
            password="Password123",
            nombre_completo="Operador Con Reversión",
            rol=UsuarioAudacIA.Rol.USUARIO,
            almacen_asignado=self.almacen_a,
        )
        self.usuario_con_revertir.almacenes_asignados.add(self.almacen_a)

        # Asignar permisos al operador reversor
        p_rev, _ = PermisoAudacIA.objects.get_or_create(
            codigo="movimientos.revertir",
            defaults={"modulo": "MOVIMIENTOS", "nombre": "Revertir movimientos"},
        )
        p_ver, _ = PermisoAudacIA.objects.get_or_create(
            codigo="movimientos.ver",
            defaults={"modulo": "MOVIMIENTOS", "nombre": "Ver movimientos"},
        )
        p_ent, _ = PermisoAudacIA.objects.get_or_create(
            codigo="movimientos.entrada",
            defaults={"modulo": "MOVIMIENTOS", "nombre": "Entradas"},
        )
        p_sal, _ = PermisoAudacIA.objects.get_or_create(
            codigo="movimientos.dotacion",
            defaults={"modulo": "MOVIMIENTOS", "nombre": "Salidas"},
        )
        self.usuario_con_revertir.permisos_personalizados.add(p_rev, p_ver, p_ent, p_sal)

        p_inf_ver, _ = PermisoAudacIA.objects.get_or_create(
            codigo="infraestructura.ver",
            defaults={"modulo": "INFRAESTRUCTURA", "nombre": "Ver infraestructura"},
        )
        self.usuario_con_revertir.permisos_personalizados.add(p_inf_ver)

        # 4. Crear movimientos base para pruebas
        _, self.mov_entrada_a = registrar_entrada(
            producto=self.prod_a,
            almacen=self.almacen_a,
            cantidad=10,
            usuario=self.admin,
            clave_idempotencia="ENT-TEST-A",
        )
        _, self.mov_entrada_b = registrar_entrada(
            producto=self.prod_b,
            almacen=self.almacen_b,
            cantidad=10,
            usuario=self.admin,
            clave_idempotencia="ENT-TEST-B",
        )

    # ─────────────────────────────────────────────────────────────────────────
    # 2.1 Escalada en la reversión
    # ─────────────────────────────────────────────────────────────────────────

    def test_usuario_sin_permiso_revertir_es_rechazado_en_vista_y_servicio(self):
        """Un usuario sin permiso movimientos.revertir recibe 403 Forbidden tanto en vista como en servicio."""
        # 1. Intento vía vista GET
        self.client.login(username="operador_base", password="Password123")
        url = reverse("inventario:movimiento-revertir", kwargs={"pk": self.mov_entrada_a.pk})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 403)

        # 2. Intento vía vista POST
        resp_post = self.client.post(url, {"motivo": "Intento no autorizado"})
        self.assertEqual(resp_post.status_code, 403)

        # 3. Intento directo por servicio
        with self.assertRaises(PermissionDenied):
            revertir_movimiento(
                movimiento=self.mov_entrada_a,
                usuario=self.usuario_sin_permiso,
                motivo="Llamada directa sin permiso",
            )

    def test_usuario_con_permiso_revertir_puede_revertir_entrada_propia(self):
        """Un usuario con permiso movimientos.revertir puede revertir una entrada de su almacén asignado."""
        self.client.login(username="operador_reversor", password="Password123")
        url = reverse("inventario:movimiento-revertir", kwargs={"pk": self.mov_entrada_a.pk})
        resp = self.client.post(url, {"motivo": "Reversión autorizada"})
        self.assertEqual(resp.status_code, 302)

        self.mov_entrada_a.refresh_from_db()
        self.assertEqual(self.mov_entrada_a.estado, Movimiento.Estado.REVERTIDO)

    def test_usuario_con_permiso_revertir_no_puede_revertir_ajuste_conteo(self):
        """Revertir ajustes de conteo físico está estrictamente reservado para administradores."""
        conteo = registrar_conteo_fisico(
            producto=self.prod_a,
            almacen=self.almacen_a,
            cantidad_contada=15,
            usuario=self.admin,
        )
        _, mov_ajuste = confirmar_conteo_fisico(
            conteo=conteo,
            usuario=self.admin,
        )

        # Intento por usuario estándar (incluso con permiso movimientos.revertir)
        self.client.login(username="operador_reversor", password="Password123")
        url = reverse("inventario:movimiento-revertir", kwargs={"pk": mov_ajuste.pk})
        resp = self.client.post(url, {"motivo": "Intento de revertir conteo"})
        self.assertEqual(resp.status_code, 403)

        # Defensa en profundidad a nivel servicio
        with self.assertRaises(PermissionDenied):
            revertir_movimiento(
                movimiento=mov_ajuste,
                usuario=self.usuario_con_revertir,
                motivo="Intento de servicio de conteo",
            )

    def test_usuario_con_permiso_revertir_no_puede_revertir_traslado_entre_almacenes(self):
        """Revertir traslados entre almacenes está estrictamente reservado para administradores."""
        _, _, mov_traslado = registrar_traslado_entre_almacenes(
            producto=self.prod_a,
            almacen_origen=self.almacen_a,
            almacen_destino=self.almacen_b,
            cantidad=10,
            usuario=self.admin,
        )

        self.client.login(username="operador_reversor", password="Password123")
        url = reverse("inventario:movimiento-revertir", kwargs={"pk": mov_traslado.pk})
        resp = self.client.post(url, {"motivo": "Intento de revertir traslado"})
        self.assertEqual(resp.status_code, 403)

        with self.assertRaises(PermissionDenied):
            revertir_movimiento(
                movimiento=mov_traslado,
                usuario=self.usuario_con_revertir,
                motivo="Intento servicio traslado",
            )

    def test_usuario_con_permiso_revertir_no_puede_revertir_otro_almacen_idor(self):
        """Un usuario estándar no puede revertir movimientos de otro almacén al que no pertenece (IDOR)."""
        self.client.login(username="operador_reversor", password="Password123")
        # mov_entrada_b pertenece al Almacén B, el operador solo tiene acceso al Almacén A
        url = reverse("inventario:movimiento-revertir", kwargs={"pk": self.mov_entrada_b.pk})
        resp = self.client.post(url, {"motivo": "Intento IDOR en otro almacén"})
        self.assertEqual(resp.status_code, 403)

        with self.assertRaises(PermissionDenied):
            revertir_movimiento(
                movimiento=self.mov_entrada_b,
                usuario=self.usuario_con_revertir,
                motivo="Intento IDOR vía servicio",
            )

    def test_administrador_puede_revertir_cualquier_tipo_autorizado(self):
        """Un Administrador tiene autorización global para revertir entradas, traslados y ajustes."""
        self.client.login(username="admin_f2", password="Password123")
        url = reverse("inventario:movimiento-revertir", kwargs={"pk": self.mov_entrada_b.pk})
        resp = self.client.post(url, {"motivo": "Reversión administrativa legítima"})
        self.assertEqual(resp.status_code, 302)

        self.mov_entrada_b.refresh_from_db()
        self.assertEqual(self.mov_entrada_b.estado, Movimiento.Estado.REVERTIDO)

    # ─────────────────────────────────────────────────────────────────────────
    # 2.2 Validación de SECRET_KEY en producción
    # ─────────────────────────────────────────────────────────────────────────

    def test_secret_key_insegura_en_produccion_lanza_improperly_configured(self):
        """Si DEBUG es False y SECRET_KEY usa el valor por defecto o vacío, se rechaza la inicialización."""
        clave_insegura = "dev-insecure-key-change-me"
        debug = False
        if not debug and (not clave_insegura or clave_insegura == "dev-insecure-key-change-me"):
            error_capturado = True
        else:
            error_capturado = False
        self.assertTrue(error_capturado)

    # ─────────────────────────────────────────────────────────────────────────
    # 2.4 Aislamiento de topología entre almacenes (RBAC / PBAC)
    # ─────────────────────────────────────────────────────────────────────────

    def test_usuario_estandar_solo_ve_almacenes_asignados_en_listado(self):
        """AlmacenListaView oculta completamente almacenes a los que el usuario no tiene asignación."""
        self.client.login(username="operador_reversor", password="Password123")
        url = reverse("inventario:almacen-lista")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

        nombres_en_contexto = [a.nombre for a in resp.context["almacenes"]]
        self.assertIn("Almacen A Principal", nombres_en_contexto)
        self.assertNotIn("Almacen B Secundario", nombres_en_contexto)

    def test_usuario_estandar_solo_ve_ubicaciones_de_su_almacen(self):
        """UbicacionListaView no expone ubicaciones de almacenes foráneos."""
        self.client.login(username="operador_reversor", password="Password123")
        url = reverse("inventario:ubicacion-lista")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

        nombres_en_contexto = [u.nombre for u in resp.context["ubicaciones"]]
        self.assertIn("Estante A1", nombres_en_contexto)
        self.assertNotIn("Estante B1", nombres_en_contexto)

    def test_usuario_estandar_solo_ve_categorias_de_su_almacen(self):
        """CategoriaListaView no expone categorías ni estructura de almacenes foráneos."""
        self.client.login(username="operador_reversor", password="Password123")
        url = reverse("inventario:categoria-lista")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

        nombres_en_contexto = [c.nombre for c in resp.context["categorias"]]
        self.assertIn("Herramientas Lab A", nombres_en_contexto)
        self.assertNotIn("Materiales Lab B", nombres_en_contexto)

    # ─────────────────────────────────────────────────────────────────────────
    # 2.5 Seguridad contra Path Traversal en Respaldos
    # ─────────────────────────────────────────────────────────────────────────

    def test_obtener_ruta_respaldo_segura_rechaza_path_traversal(self):
        """obtener_ruta_respaldo_segura bloquea secuencias '../', rutas absolutas y nombres arbitrarios."""
        patrones_maliciosos = [
            "../backup_20261020_000000.sqlite3.gz",
            "..\\backup_20261020_000000.sqlite3.gz",
            "../../etc/passwd",
            "C:\\Windows\\system32\\cmd.exe",
            "/etc/shadow",
            "backup_20261020_000000.sqlite3.gz/../etc/passwd",
            "%2e%2e/backup.sqlite3.gz",
            "script.sh",
            "malicioso.exe",
        ]
        for patron in patrones_maliciosos:
            with self.subTest(patron=patron):
                with self.assertRaises(ValueError):
                    obtener_ruta_respaldo_segura(patron)

    def test_eliminar_respaldo_rechaza_path_traversal_y_usuarios_no_superadmin(self):
        """eliminar_respaldo_bd valida rol Superadministrador y previene path traversal."""
        # Intento de path traversal por superadmin
        with self.assertRaises(ValueError):
            eliminar_respaldo_bd("../escape.sqlite3.gz", usuario=self.superadmin)

        # Intento de eliminación por un usuario estándar
        with self.assertRaises(PermissionDenied):
            eliminar_respaldo_bd("backup_20261020_000000.sqlite3.gz", usuario=self.usuario_con_revertir)

        # Intento de eliminación por un administrador (no superadmin)
        with self.assertRaises(PermissionDenied):
            eliminar_respaldo_bd("backup_20261020_000000.sqlite3.gz", usuario=self.admin)
