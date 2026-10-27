"""
Pruebas del servicio de respaldo de base de datos (inventario/servicios/respaldo.py).
"""

import os
import shutil
import sqlite3
import tempfile
from django.test import TestCase

from cuentas.models import UsuarioAudacIA
from inventario.servicios.respaldo import (
    calcular_sha256_archivo,
    generar_respaldo_bd,
    listar_respaldos,
    obtener_estadisticas_respaldos,
    obtener_ruta_respaldo_segura,
)


class ServicioRespaldoTest(TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_audacia_respaldos_")
        self.test_db_path = os.path.join(self.temp_dir, "origen_test.sqlite3")

        # Crear base de datos aislada
        conn = sqlite3.connect(self.test_db_path)
        conn.execute("CREATE TABLE t (id INT, v TEXT)")
        conn.execute("INSERT INTO t VALUES (1, 'AudacIA')")
        conn.commit()
        conn.close()

        self.superadmin = UsuarioAudacIA.objects.create_superuser(
            username="super_backup_test",
            nombre_completo="Super Administrador Backup",
            email="super_backup@audacia.edu.co",
            password="Password123!",
            rol=UsuarioAudacIA.Rol.SUPERUSUARIO,
        )

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_generar_respaldo_y_listar(self):
        # 1. Generar respaldo
        resultado = generar_respaldo_bd(
            usuario=self.superadmin,
            force=True,
            output_dir=self.temp_dir,
            db_path=self.test_db_path,
            motivo="Prueba de generación de respaldo",
        )

        self.assertFalse(resultado["omitido"])
        self.assertTrue(os.path.exists(resultado["ruta"]))
        self.assertTrue(resultado["ruta"].endswith(".sqlite3.gz"))
        self.assertTrue(os.path.exists(resultado["ruta"] + ".sha256"))

        # 2. Verificar hash
        sha_calculado = calcular_sha256_archivo(resultado["ruta"])
        self.assertEqual(sha_calculado, resultado["sha256"])

        # 3. Listar respaldos
        lista = listar_respaldos(output_dir=self.temp_dir)
        self.assertEqual(len(lista), 1)
        respaldo = lista[0]
        self.assertEqual(respaldo["archivo"], resultado["archivo"])
        self.assertTrue(respaldo["integridad_ok"])
        self.assertEqual(respaldo["sha256"], resultado["sha256"])

        # 4. Estadísticas
        stats = obtener_estadisticas_respaldos(output_dir=self.temp_dir)
        self.assertEqual(stats["total_copias"], 1)
        self.assertGreater(stats["tamano_total_bytes"], 0)
        self.assertIsNotNone(stats["ultimo_respaldo"])

    def test_obtener_ruta_segura_path_traversal(self):
        # 1. Generar archivo válido
        res = generar_respaldo_bd(force=True, output_dir=self.temp_dir, db_path=self.test_db_path)
        nombre = res["archivo"]

        # 2. Obtener ruta legítima
        ruta = obtener_ruta_respaldo_segura(nombre, output_dir=self.temp_dir)
        self.assertEqual(ruta, res["ruta"])

        # 3. Intentos de path traversal deben lanzar ValueError
        with self.assertRaises(ValueError):
            obtener_ruta_respaldo_segura(f"../{nombre}", output_dir=self.temp_dir)

        with self.assertRaises(ValueError):
            obtener_ruta_respaldo_segura("subcarpeta/archivo.gz", output_dir=self.temp_dir)

        with self.assertRaises(ValueError):
            obtener_ruta_respaldo_segura("hack.txt", output_dir=self.temp_dir)

        # 4. Archivo que cumple patrón pero no existe debe lanzar FileNotFoundError
        with self.assertRaises(FileNotFoundError):
            obtener_ruta_respaldo_segura("backup_19990101_120000.sqlite3.gz", output_dir=self.temp_dir)
