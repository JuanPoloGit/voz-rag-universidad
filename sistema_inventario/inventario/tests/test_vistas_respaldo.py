"""
Pruebas de las vistas de Copias de Seguridad (inventario/views.py).
Verificación de control de acceso estricto para Superadministradores y descarga segura.
"""

import os
import shutil
import tempfile
from unittest.mock import patch
from django.test import TestCase
from django.urls import reverse

from cuentas.models import UsuarioAudacIA
from inventario.models import Almacen


class VistasRespaldoTest(TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_vistas_respaldo_")
        self.almacen = Almacen.objects.create(nombre="Almacén Respaldos")

        self.superadmin = UsuarioAudacIA.objects.create_superuser(
            username="super_vista_test",
            nombre_completo="Super Administrador Vistas",
            email="super_vista@audacia.edu.co",
            password="Password123!",
            rol=UsuarioAudacIA.Rol.SUPERUSUARIO,
        )

        self.admin = UsuarioAudacIA.objects.create_user(
            username="admin_vista_test",
            nombre_completo="Administrador Vistas",
            email="admin_vista@audacia.edu.co",
            password="Password123!",
            rol=UsuarioAudacIA.Rol.ADMINISTRADOR,
        )

        self.operador = UsuarioAudacIA.objects.create_user(
            username="operador_vista_test",
            nombre_completo="Operador Vistas",
            email="operador_vista@audacia.edu.co",
            password="Password123!",
            rol=UsuarioAudacIA.Rol.USUARIO,
            almacen_asignado=self.almacen,
        )

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch("inventario.views.listar_respaldos")
    @patch("inventario.views.obtener_estadisticas_respaldos")
    def test_acceso_respaldo_lista(self, mock_stats, mock_listar):
        mock_listar.return_value = []
        mock_stats.return_value = {
            "total_copias": 0,
            "tamano_total_bytes": 0,
            "tamano_total_formateado": "0 B",
            "ultimo_respaldo": None,
        }

        url = reverse("inventario:respaldo-lista")

        # 1. Superadministrador: 200 OK
        self.client.force_login(self.superadmin)
        resp_super = self.client.get(url)
        self.assertEqual(resp_super.status_code, 200)
        self.assertContains(resp_super, "Copias de Seguridad")

        # 2. Administrador: 403 Forbidden
        self.client.force_login(self.admin)
        resp_admin = self.client.get(url)
        self.assertEqual(resp_admin.status_code, 403)

        # 3. Operador: 403 Forbidden
        self.client.force_login(self.operador)
        resp_op = self.client.get(url)
        self.assertEqual(resp_op.status_code, 403)

    @patch("inventario.views.generar_respaldo_bd")
    def test_crear_respaldo_view(self, mock_generar):
        mock_generar.return_value = {
            "archivo": "backup_20261027_120000.sqlite3.gz",
            "ruta": "/tmp/backup_20261027_120000.sqlite3.gz",
            "sha256": "abc123456",
            "tamano_bytes": 1024,
            "tamano_formateado": "1.0 KB",
            "omitido": False,
        }

        url = reverse("inventario:respaldo-crear")

        # 1. Admin normal no puede generar: 403
        self.client.force_login(self.admin)
        resp_admin = self.client.post(url)
        self.assertEqual(resp_admin.status_code, 403)

        # 2. Superadmin genera y redirige a la lista
        self.client.force_login(self.superadmin)
        resp_super = self.client.post(url)
        self.assertRedirects(resp_super, reverse("inventario:respaldo-lista"))
        mock_generar.assert_called_once()

    def test_descargar_respaldo_view(self):
        # Crear un archivo de prueba en el directorio temporal
        archivo_nombre = "backup_20261027_120000.sqlite3.gz"
        ruta_archivo = os.path.join(self.temp_dir, archivo_nombre)
        with open(ruta_archivo, "wb") as f:
            f.write(b"CONTENIDO_GZIP_MOCK")

        url = reverse("inventario:respaldo-descargar", kwargs={"nombre_archivo": archivo_nombre})

        with patch("inventario.views.obtener_ruta_respaldo_segura", return_value=ruta_archivo):
            # 1. Admin normal no puede descargar: 403
            self.client.force_login(self.admin)
            resp_admin = self.client.get(url)
            self.assertEqual(resp_admin.status_code, 403)

            # 2. Superadmin descarga: 200 OK con FileResponse
            self.client.force_login(self.superadmin)
            resp_super = self.client.get(url)
            self.assertEqual(resp_super.status_code, 200)
            self.assertEqual(resp_super["Content-Disposition"], f'attachment; filename="{archivo_nombre}"')

        # 3. Archivo inexistente retorna 404
        url_inexistente = reverse("inventario:respaldo-descargar", kwargs={"nombre_archivo": "backup_19990101_000000.sqlite3.gz"})
        resp_404 = self.client.get(url_inexistente)
        self.assertEqual(resp_404.status_code, 404)
