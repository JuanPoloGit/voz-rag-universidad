"""Pruebas unitarias e integrales para la Fase 6: Preparación para Producción PostgreSQL.

Cubre:
1. Fábrica y polimorfismo de `BackendRespaldo` (`RespaldoSQLite` y `RespaldoPostgres`).
2. Generación y restauración en `RespaldoPostgres` con validación estricta de seguridad:
   - Inyección de contraseña exclusivamente por variable de entorno PGPASSWORD (nunca en CLI).
   - Formato binario comprimido nativo (-Fc).
   - Restauración limpia con flags --clean --if-exists --no-owner.
3. Compatibilidad de archivos duales (.sqlite3.gz y .pgdump) en `listar_respaldos` y prevención de Path Traversal.
4. Serialización portátil libre de errores en dumpdata (`--natural-foreign --natural-primary`).
5. Limpieza portátil de datos de prueba con el ORM (`poblar_datos_prueba`).
"""

import os
import subprocess
import tempfile
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.db import connection
from django.test import SimpleTestCase, TestCase

from inventario.models import Almacen, Auditoria
from inventario.servicios.respaldo import (
    BackendRespaldo,
    RespaldoPostgres,
    RespaldoSQLite,
    generar_respaldo_bd,
    listar_respaldos,
    obtener_backend_respaldo,
    obtener_ruta_respaldo_segura,
    restaurar_respaldo_bd,
)


class TestFase6BackendRespaldo(SimpleTestCase):
    """Pruebas unitarias de la fábrica y arquitectura polimórfica de respaldos."""

    def test_01_fabrica_backend_por_vendor(self):
        """Verifica que la fábrica retorne la clase correspondiente a cada motor."""
        backend_pg = obtener_backend_respaldo("postgresql")
        self.assertIsInstance(backend_pg, RespaldoPostgres)
        self.assertEqual(backend_pg.extension, ".pgdump")
        self.assertEqual(backend_pg.tipo_motor, "postgresql")

        backend_sqlite = obtener_backend_respaldo("sqlite")
        self.assertIsInstance(backend_sqlite, RespaldoSQLite)
        self.assertEqual(backend_sqlite.extension, ".sqlite3.gz")
        self.assertEqual(backend_sqlite.tipo_motor, "sqlite")

        backend_default = obtener_backend_respaldo()
        self.assertIsInstance(backend_default, BackendRespaldo)

    @patch("subprocess.run")
    def test_02_generar_respaldo_postgres_seguridad_pgpassword(self, mock_run):
        """Verifica que pg_dump reciba la contraseña por entorno PGPASSWORD y NUNCA en la línea de comandos."""
        mock_run.return_value = MagicMock(returncode=0)
        backend = RespaldoPostgres()

        fake_config = {
            "NAME": "audacia_prod_db",
            "USER": "audacia_admin",
            "PASSWORD": "SuperSecretPassword123!",
            "HOST": "db.audacia.internal",
            "PORT": "5432",
        }

        destino = os.path.join(tempfile.gettempdir(), "test_backup.pgdump")
        backend.generar(destino, db_config=fake_config)

        self.assertTrue(mock_run.called)
        cmd, kwargs = mock_run.call_args

        # 1. El comando debe invocar pg_dump con formato Custom (-Fc)
        args_lista = cmd[0]
        self.assertEqual(args_lista[0], "pg_dump")
        self.assertIn("-Fc", args_lista)
        self.assertIn("-h", args_lista)
        self.assertIn("db.audacia.internal", args_lista)
        self.assertIn("-U", args_lista)
        self.assertIn("audacia_admin", args_lista)
        self.assertIn("audacia_prod_db", args_lista)

        # 2. AUDITORÍA CRÍTICA DE SEGURIDAD: La contraseña NO debe figurar en ningún argumento
        for arg in args_lista:
            self.assertNotIn("SuperSecretPassword123!", str(arg))

        # 3. La contraseña debe transmitirse en el entorno del subproceso
        env_pasado = kwargs.get("env", {})
        self.assertEqual(env_pasado.get("PGPASSWORD"), "SuperSecretPassword123!")

    @patch("subprocess.run")
    def test_03_restaurar_respaldo_postgres_seguridad_flags(self, mock_run):
        """Verifica que pg_restore use --clean --if-exists --no-owner y pase PGPASSWORD de forma segura."""
        mock_run.return_value = MagicMock(returncode=0)
        backend = RespaldoPostgres()

        fake_config = {
            "NAME": "audacia_prod_db",
            "USER": "audacia_admin",
            "PASSWORD": "RestorePassword789!",
            "HOST": "localhost",
            "PORT": "5432",
        }

        archivo_backup = os.path.join(tempfile.gettempdir(), "test_backup.pgdump")
        backend.restaurar(archivo_backup, db_config=fake_config)

        self.assertTrue(mock_run.called)
        cmd, kwargs = mock_run.call_args

        args_lista = cmd[0]
        self.assertEqual(args_lista[0], "pg_restore")
        self.assertIn("--clean", args_lista)
        self.assertIn("--if-exists", args_lista)
        self.assertIn("--no-owner", args_lista)
        self.assertIn("-d", args_lista)
        self.assertIn("audacia_prod_db", args_lista)

        # La contraseña NO debe estar en la línea de comandos
        for arg in args_lista:
            self.assertNotIn("RestorePassword789!", str(arg))

        # La contraseña debe estar en PGPASSWORD
        self.assertEqual(kwargs.get("env", {}).get("PGPASSWORD"), "RestorePassword789!")

    @patch("subprocess.run", side_effect=FileNotFoundError("pg_dump no encontrado"))
    def test_04_error_amigable_si_binario_no_instalado(self, mock_run):
        """Verifica que se lance un mensaje explicativo si las herramientas cliente no están en PATH."""
        backend = RespaldoPostgres()
        with self.assertRaises(RuntimeError) as ctx:
            backend.generar(
                "ruta.pgdump",
                db_config={"NAME": "db", "USER": "u", "PASSWORD": "p", "HOST": "h", "PORT": "5432"},
            )
        self.assertIn("pg_dump' no fue encontrado en el sistema", str(ctx.exception))


class TestFase6IntegracionDual(TestCase):
    """Pruebas de integración con archivos, detección de motor y comandos de gestión."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_fase6_respaldos_")

    def tearDown(self):
        import shutil
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_05_listar_respaldos_reconoce_ambos_motores(self):
        """Verifica que listar_respaldos detecte y etiquete respaldos tanto de SQLite como de PostgreSQL."""
        # 1. Crear archivo simulado SQLite
        sqlite_file = os.path.join(self.temp_dir, "backup_20261028_120000.sqlite3.gz")
        with open(sqlite_file, "wb") as f:
            f.write(b"fake sqlite gz content")
        with open(sqlite_file + ".sha256", "w") as f:
            f.write("abcd1234abcd1234  backup_20261028_120000.sqlite3.gz\n")

        # 2. Crear archivo simulado PostgreSQL
        pg_file = os.path.join(self.temp_dir, "backup_20261028_130000.pgdump")
        with open(pg_file, "wb") as f:
            f.write(b"fake pgdump custom content")
        with open(pg_file + ".sha256", "w") as f:
            f.write("ef015678ef015678  backup_20261028_130000.pgdump\n")

        lista = listar_respaldos(output_dir=self.temp_dir)
        self.assertEqual(len(lista), 2)

        archivos = {r["archivo"]: r for r in lista}
        self.assertIn("backup_20261028_120000.sqlite3.gz", archivos)
        self.assertEqual(archivos["backup_20261028_120000.sqlite3.gz"]["motor"], "sqlite")

        self.assertIn("backup_20261028_130000.pgdump", archivos)
        self.assertEqual(archivos["backup_20261028_130000.pgdump"]["motor"], "postgresql")

    def test_06_obtener_ruta_segura_permite_pgdump_y_bloquea_traversal(self):
        """Verifica que obtener_ruta_respaldo_segura acepte .pgdump y bloquee path traversal."""
        pg_file = os.path.join(self.temp_dir, "backup_20261028_140000.pgdump")
        with open(pg_file, "wb") as f:
            f.write(b"pgdump content")

        ruta = obtener_ruta_respaldo_segura("backup_20261028_140000.pgdump", output_dir=self.temp_dir)
        self.assertEqual(ruta, os.path.realpath(pg_file))

        # Ataques de Directory / Path Traversal deben fallar
        with self.assertRaises(ValueError):
            obtener_ruta_respaldo_segura("../backup_20261028_140000.pgdump", output_dir=self.temp_dir)

        with self.assertRaises(ValueError):
            obtener_ruta_respaldo_segura("../../etc/passwd", output_dir=self.temp_dir)

        with self.assertRaises(ValueError):
            obtener_ruta_respaldo_segura("archivo_arbitrario.exe", output_dir=self.temp_dir)

    def test_07_poblar_datos_prueba_limpieza_portatil_orm(self):
        """Verifica que Auditoria.objects.all()._raw_delete(...) se ejecute limpiamente con el ORM."""
        Auditoria.objects.create(
            accion="TEST_ACCION",
            tipo_entidad="Prueba",
            id_entidad="1",
        )
        self.assertGreaterEqual(Auditoria.objects.count(), 1)

        # Ejecutar borrado masivo mediante el ORM (_raw_delete bypass de pre_delete signal inmutable)
        Auditoria.objects.all()._raw_delete(Auditoria.objects.db)
        self.assertEqual(Auditoria.objects.count(), 0)

    def test_08_runbook_dumpdata_serializacion_completa(self):
        """Verifica que los modelos de la base de datos se serialicen limpiamente con las opciones del Runbook."""
        out_dump = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        out_dump.close()
        try:
            call_command(
                "dumpdata",
                "--natural-foreign",
                "--natural-primary",
                "-e", "contenttypes",
                "-e", "auth.permission",
                "-e", "sessions",
                "--output", out_dump.name,
            )
            self.assertTrue(os.path.exists(out_dump.name))
            self.assertGreater(os.path.getsize(out_dump.name), 100)
        finally:
            if os.path.exists(out_dump.name):
                os.remove(out_dump.name)
