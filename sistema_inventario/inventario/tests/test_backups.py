"""
Pruebas exhaustivas para el sistema de respaldos y calendario laboral (Política A - Colombia).
Totalmente determinista, no bloqueante y libre de contención transaccional de SQLite.
"""

import datetime
import hashlib
import os
import shutil
import sqlite3
import tempfile
from io import StringIO
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from inventario.models import Auditoria
from scripts.colombia_calendario import (
    calcular_domingo_pascua,
    es_dia_laboral,
    es_festivo_colombia,
    obtener_festivos_colombia,
    obtener_ultimos_dias_laborales,
)


class CalendarioLaboralColombiaTests(TestCase):
    """Pruebas del cálculo de festivos y días hábiles en Colombia."""

    def test_festivos_2026_total_y_fechas_clave(self):
        """Verifica que el año 2026 contenga exactamente 18 festivos oficiales."""
        festivos_2026 = obtener_festivos_colombia(2026)
        self.assertEqual(len(festivos_2026), 18)

        # Fijos
        self.assertIn(datetime.date(2026, 1, 1), festivos_2026)    # Año nuevo
        self.assertIn(datetime.date(2026, 5, 1), festivos_2026)    # Día trabajo
        self.assertIn(datetime.date(2026, 7, 20), festivos_2026)   # Independencia
        self.assertIn(datetime.date(2026, 8, 7), festivos_2026)    # Boyacá
        self.assertIn(datetime.date(2026, 12, 8), festivos_2026)   # Inmaculada
        self.assertIn(datetime.date(2026, 12, 25), festivos_2026)  # Navidad

        # Basados en Pascua 2026 (Pascua = 5 de abril de 2026)
        pascua_2026 = calcular_domingo_pascua(2026)
        self.assertEqual(pascua_2026, datetime.date(2026, 4, 5))
        self.assertIn(datetime.date(2026, 4, 2), festivos_2026)   # Jueves Santo
        self.assertIn(datetime.date(2026, 4, 3), festivos_2026)   # Viernes Santo

    def test_es_dia_laboral_fines_de_semana_y_festivos(self):
        """Sábados, domingos y festivos no deben ser días laborales."""
        self.assertFalse(es_dia_laboral(datetime.date(2026, 4, 5)))  # Domingo
        self.assertFalse(es_dia_laboral(datetime.date(2026, 4, 4)))  # Sábado
        self.assertFalse(es_dia_laboral(datetime.date(2026, 4, 3)))  # Viernes Santo
        self.assertTrue(es_dia_laboral(datetime.date(2026, 4, 1)))   # Miércoles laboral

    def test_ultimos_dias_laborales_acotado_sin_bucle(self):
        """Verifica que el generador de últimos días laborales sea finito y exacto."""
        referencia = datetime.date(2026, 4, 6)
        ultimos_3 = obtener_ultimos_dias_laborales(referencia, 3)
        self.assertEqual(len(ultimos_3), 3)
        for d in ultimos_3:
            self.assertTrue(es_dia_laboral(d))


class BackupYRestauracionTests(TestCase):
    """Pruebas de los comandos de backup y restauración sobre base de datos SQLite aislada."""

    def setUp(self):
        super().setUp()
        self.temp_dir = tempfile.mkdtemp(prefix="test_audacia_backups_")
        self.test_db_path = os.path.join(self.temp_dir, "origen.sqlite3")

        # Crear una base de datos SQLite aislada e independiente
        conn = sqlite3.connect(self.test_db_path)
        conn.execute("CREATE TABLE inventario_test (id INTEGER PRIMARY KEY, item TEXT)")
        conn.execute("INSERT INTO inventario_test (item) VALUES ('Equipo AudacIA Pro')")
        conn.commit()
        conn.close()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        super().tearDown()

    def test_backup_genera_archivo_comprimido_y_sha256(self):
        """El comando backup_inventario debe generar .sqlite3.gz y su archivo .sha256."""
        out = StringIO()
        call_command(
            "backup_inventario",
            "--db-path", self.test_db_path,
            "--output-dir", self.temp_dir,
            "--force",
            "--no-input",
            stdout=out
        )

        archivos = os.listdir(self.temp_dir)
        gz_files = [f for f in archivos if f.endswith(".sqlite3.gz")]
        sha_files = [f for f in archivos if f.endswith(".sha256")]

        self.assertEqual(len(gz_files), 1)
        self.assertEqual(len(sha_files), 1)

        gz_path = os.path.join(self.temp_dir, gz_files[0])
        sha_path = os.path.join(self.temp_dir, sha_files[0])

        # Verificar integridad del SHA-256
        with open(gz_path, "rb") as f:
            sha_calculado = hashlib.sha256(f.read()).hexdigest()

        with open(sha_path, "r", encoding="utf-8") as f:
            sha_guardado = f.read().split()[0]

        self.assertEqual(sha_calculado, sha_guardado)

    def test_restaurar_inventario_con_checksum_valido(self):
        """Debe restaurar la base de datos sin errores ni bloqueos si el checksum es íntegro."""
        # 1. Crear backup de la BD aislada
        call_command(
            "backup_inventario",
            "--db-path", self.test_db_path,
            "--output-dir", self.temp_dir,
            "--force",
            "--no-input",
        )
        gz_files = [f for f in os.listdir(self.temp_dir) if f.endswith(".sqlite3.gz")]
        gz_path = os.path.join(self.temp_dir, gz_files[0])

        destino_restaurado = os.path.join(self.temp_dir, "restaurado.sqlite3")

        # 2. Restaurar hacia la base de datos destino independiente
        out_restore = StringIO()
        call_command(
            "restaurar_inventario",
            gz_path,
            "--destino", destino_restaurado,
            "--no-input",
            stdout=out_restore
        )

        self.assertTrue(os.path.exists(destino_restaurado))
        self.assertIn("restaurada exitosamente", out_restore.getvalue())

        # 3. Comprobar que los datos existen en la BD restaurada
        conn = sqlite3.connect(destino_restaurado)
        row = conn.execute("SELECT item FROM inventario_test WHERE id = 1").fetchone()
        conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "Equipo AudacIA Pro")

    def test_restaurar_inventario_falla_si_checksum_corrupto(self):
        """Si el archivo de backup fue alterado o no coincide el SHA-256, debe abortar con CommandError."""
        # 1. Crear backup
        call_command(
            "backup_inventario",
            "--db-path", self.test_db_path,
            "--output-dir", self.temp_dir,
            "--force",
            "--no-input",
        )
        gz_files = [f for f in os.listdir(self.temp_dir) if f.endswith(".sqlite3.gz")]
        gz_path = os.path.join(self.temp_dir, gz_files[0])
        sha_path = gz_path + ".sha256"

        # Corromper el archivo .sha256 con un hash inválido
        with open(sha_path, "w", encoding="utf-8") as f:
            f.write("0000000000000000000000000000000000000000000000000000000000000000  backup.gz\n")

        destino_restaurado = os.path.join(self.temp_dir, "restaurado_fail.sqlite3")
        with self.assertRaises(CommandError) as ctx:
            call_command("restaurar_inventario", gz_path, "--destino", destino_restaurado, "--no-input")

        self.assertIn("Checksum SHA-256 no coincide", str(ctx.exception))

    def test_politica_retencion_purga_backups_antiguos(self):
        """Verifica que la rotación de backups elimine archivos que exceden la política."""
        # Crear archivos antiguos simulados
        antiguo_gz = os.path.join(self.temp_dir, "backup_20200101_100000.sqlite3.gz")
        antiguo_sha = antiguo_gz + ".sha256"
        with open(antiguo_gz, "w") as f:
            f.write("dummy")
        with open(antiguo_sha, "w") as f:
            f.write("dummy")

        # Ejecutar backup actual que disparará la purga
        call_command(
            "backup_inventario",
            "--db-path", self.test_db_path,
            "--output-dir", self.temp_dir,
            "--force",
            "--no-input",
        )

        # El backup del año 2020 debe haber sido purgado
        self.assertFalse(os.path.exists(antiguo_gz))
        self.assertFalse(os.path.exists(antiguo_sha))
