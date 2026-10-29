"""Pruebas de Fase 0: infraestructura de base de datos dual (SQLite / PostgreSQL).

Verifica:
- Configuración por motor en ``settings.py`` (``transaction_mode=IMMEDIATE``,
  ``init_command`` WAL sin señal redundante en ``apps.py``, precedencia
  ``DB_ENGINE`` / ``USE_SQLITE`` y ``settings_test_pg``).
- Decorador ``@transaccion_con_reintento`` ante bloqueos de SQLite y códigos
  SQLSTATE ``40001`` / ``40P01`` de PostgreSQL, incluyendo rollback intermedio
  y comportamiento en transacciones anidadas.
- Excepciones de dominio (``StockInsuficiente``, ``InconsistenciaInventario``,
  ``OperacionDuplicada``) compatibles con ``ValidationError``.
"""

import importlib
import pathlib
import tempfile
from unittest.mock import patch

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, OperationalError, transaction
from django.db.backends.sqlite3.base import DatabaseWrapper as SQLiteDatabaseWrapper
from django.test import SimpleTestCase, TestCase

import inventario.apps as inventario_apps
from inventario.db import (
    CODIGOS_PG_REINTENTABLES,
    es_error_transitorio_bd,
    transaccion_con_reintento,
)
from inventario.models import Almacen
from inventario.servicios import (
    InconsistenciaInventario,
    InventarioServiceError,
    NecesitaTraslado,
    OperacionDuplicada,
    StockInsuficiente,
)
from inventario.views import _procesar_error_formulario
from inventario_audacia.settings import _resolver_motor_bd


class _PgErrorSimulado(Exception):
    """Simula un error nativo de psycopg2 / psycopg3 con código SQLSTATE."""

    def __init__(self, mensaje: str, *, pgcode: str = "", sqlstate: str = ""):
        super().__init__(mensaje)
        if pgcode:
            self.pgcode = pgcode
        if sqlstate:
            self.sqlstate = sqlstate


class _FormularioPrueba(forms.Form):
    cantidad = forms.IntegerField()


class ConfiguracionMotorDualTest(SimpleTestCase):
    """Valida la configuración de motores y eliminación de la señal redundante."""

    def test_precedencia_variables_entorno_motor(self):
        # Por defecto usa SQLite
        motor, es_sqlite = _resolver_motor_bd({})
        self.assertEqual(motor, "sqlite")
        self.assertTrue(es_sqlite)

        # DB_ENGINE explícito tiene prioridad incluso sin USE_SQLITE=False
        motor, es_sqlite = _resolver_motor_bd({"DB_ENGINE": "postgresql"})
        self.assertEqual(motor, "postgresql")
        self.assertFalse(es_sqlite)

        # USE_SQLITE=False sin DB_ENGINE selecciona PostgreSQL
        motor, es_sqlite = _resolver_motor_bd({"USE_SQLITE": "False"})
        self.assertEqual(motor, "postgresql")
        self.assertFalse(es_sqlite)

        # DB_ENGINE=sqlite fuerza SQLite
        motor, es_sqlite = _resolver_motor_bd({"USE_SQLITE": "False", "DB_ENGINE": "sqlite3"})
        self.assertEqual(motor, "sqlite3")
        self.assertTrue(es_sqlite)

    def test_opciones_sqlite_en_settings(self):
        db_cfg = settings.DATABASES["default"]
        if db_cfg["ENGINE"] != "django.db.backends.sqlite3":
            self.skipTest("Prueba específica para entorno activo SQLite.")

        opciones = db_cfg["OPTIONS"]
        self.assertEqual(opciones.get("transaction_mode"), "IMMEDIATE")
        self.assertEqual(opciones.get("timeout"), 20)
        init_cmd = opciones.get("init_command", "").lower()
        self.assertIn("journal_mode=wal", init_cmd.replace(" ", ""))
        self.assertIn("synchronous=normal", init_cmd.replace(" ", ""))
        self.assertIn("busy_timeout=20000", init_cmd.replace(" ", ""))

    def test_senal_wal_eliminada_de_apps(self):
        self.assertFalse(
            hasattr(inventario_apps, "activar_sqlite_wal"),
            "activar_sqlite_wal debe eliminarse de inventario/apps.py al migrarse a init_command.",
        )

    def test_wal_activo_por_init_command_en_archivo_sqlite(self):
        """Verifica que SQLite habilita WAL y IMMEDIATE vía init_command sin señales."""
        db_cfg = settings.DATABASES["default"]
        if db_cfg["ENGINE"] != "django.db.backends.sqlite3":
            self.skipTest("Prueba específica para entorno activo SQLite.")

        with tempfile.TemporaryDirectory() as tmpdir:
            ruta_db = pathlib.Path(tmpdir) / "verificacion_wal.sqlite3"
            cfg = {
                **db_cfg,
                "NAME": str(ruta_db),
                "TIME_ZONE": None,
                "CONN_MAX_AGE": 0,
                "CONN_HEALTH_CHECKS": False,
                "AUTOCOMMIT": True,
                "ATOMIC_REQUESTS": False,
            }
            wrapper = SQLiteDatabaseWrapper(cfg, alias="verificacion_wal")
            try:
                wrapper.ensure_connection()
                self.assertEqual(wrapper.transaction_mode, "IMMEDIATE")
                with wrapper.cursor() as cursor:
                    cursor.execute("PRAGMA journal_mode;")
                    modo_journal = cursor.fetchone()[0].lower()
                    cursor.execute("PRAGMA busy_timeout;")
                    busy_timeout = int(cursor.fetchone()[0])

                self.assertEqual(modo_journal, "wal")
                self.assertEqual(busy_timeout, 20000)
            finally:
                wrapper.close()

    def test_settings_test_pg_configurado_correctamente(self):
        pg_settings = importlib.import_module("inventario_audacia.settings_test_pg")
        pg_db = pg_settings.DATABASES["default"]
        self.assertEqual(pg_db["ENGINE"], "django.db.backends.postgresql")
        self.assertFalse(pg_settings.USE_SQLITE)
        self.assertEqual(pg_settings.DB_ENGINE, "postgresql")
        self.assertEqual(pg_db["CONN_MAX_AGE"], 0)
        self.assertTrue(pg_db["CONN_HEALTH_CHECKS"])


class DeteccionErroresTransitoriosTest(SimpleTestCase):
    """Pruebas unitarias de clasificación de errores reintentables."""

    def test_codigos_pg_esperados(self):
        self.assertEqual(CODIGOS_PG_REINTENTABLES, frozenset({"40001", "40P01"}))

    def test_sqlite_locked_y_busy_son_transitorios(self):
        self.assertTrue(es_error_transitorio_bd(OperationalError("database is locked")))
        self.assertTrue(es_error_transitorio_bd(OperationalError("database table is locked")))
        self.assertTrue(es_error_transitorio_bd(OperationalError("database is busy")))

    def test_postgres_serialization_y_deadlock_son_transitorios(self):
        err_40001 = DatabaseError("could not serialize access")
        err_40001.__cause__ = _PgErrorSimulado("serialization failure", pgcode="40001")
        self.assertTrue(es_error_transitorio_bd(err_40001))

        err_40p01 = OperationalError("deadlock detected")
        err_40p01.__cause__ = _PgErrorSimulado("deadlock", sqlstate="40P01")
        self.assertTrue(es_error_transitorio_bd(err_40p01))

    def test_errores_no_transitorios_no_se_reintentan(self):
        self.assertFalse(es_error_transitorio_bd(OperationalError("no such table: inventario_foo")))
        self.assertFalse(es_error_transitorio_bd(IntegrityError("UNIQUE constraint failed")))

        err_23505 = IntegrityError("duplicate key value violates unique constraint")
        err_23505.__cause__ = _PgErrorSimulado("unique_violation", pgcode="23505")
        self.assertFalse(es_error_transitorio_bd(err_23505))


class TransaccionConReintentoTest(TestCase):
    """Verifica atomicidad, rollback intermedio y backoff de @transaccion_con_reintento."""

    def test_valida_max_intentos_minimo(self):
        with self.assertRaises(ValueError):
            transaccion_con_reintento(max_intentos=0)

    @patch("inventario.db.transacciones.time.sleep")
    def test_reintenta_ante_sqlite_locked_y_revierte_intento_fallido(self, mock_sleep):
        intentos = {"n": 0}

        @transaccion_con_reintento
        def crear_almacen_con_fallo_transitorio():
            intentos["n"] += 1
            Almacen.objects.create(nombre=f"Almacén Intento {intentos['n']}", activo=True)
            if intentos["n"] < 3:
                raise OperationalError("database is locked")
            return "COMPLETADO"

        resultado = crear_almacen_con_fallo_transitorio()
        self.assertEqual(resultado, "COMPLETADO")
        self.assertEqual(intentos["n"], 3)
        # Los intentos 1 y 2 debieron revertirse limpiamente; solo persiste el 3
        nombres = list(
            Almacen.objects.filter(nombre__startswith="Almacén Intento ").values_list("nombre", flat=True)
        )
        self.assertEqual(nombres, ["Almacén Intento 3"])
        self.assertEqual([c.args[0] for c in mock_sleep.call_args_list], [0.05, 0.15])

    @patch("inventario.db.transacciones.time.sleep")
    def test_reintenta_ante_deadlock_postgresql_40p01(self, mock_sleep):
        intentos = {"n": 0}

        @transaccion_con_reintento(max_intentos=3, tiempos_espera=(0.05, 0.15, 0.40))
        def operacion_pg():
            intentos["n"] += 1
            Almacen.objects.create(nombre=f"PG Intento {intentos['n']}", activo=True)
            if intentos["n"] == 1:
                err = OperationalError("deadlock detected")
                err.__cause__ = _PgErrorSimulado("deadlock", pgcode="40P01")
                raise err
            return intentos["n"]

        self.assertEqual(operacion_pg(), 2)
        self.assertEqual(
            list(Almacen.objects.filter(nombre__startswith="PG Intento ").values_list("nombre", flat=True)),
            ["PG Intento 2"],
        )
        self.assertEqual([c.args[0] for c in mock_sleep.call_args_list], [0.05])

    @patch("inventario.db.transacciones.time.sleep")
    def test_agota_max_intentos_con_esperas_crecientes_50_150_400(self, mock_sleep):
        intentos = {"n": 0}

        @transaccion_con_reintento(max_intentos=4, tiempos_espera=(0.05, 0.15, 0.40))
        def siempre_bloqueado():
            intentos["n"] += 1
            raise OperationalError("database is locked")

        with self.assertRaises(OperationalError):
            siempre_bloqueado()

        self.assertEqual(intentos["n"], 4)
        self.assertEqual([c.args[0] for c in mock_sleep.call_args_list], [0.05, 0.15, 0.40])

    @patch("inventario.db.transacciones.time.sleep")
    def test_no_reintenta_integrity_error_ni_validation_error(self, mock_sleep):
        intentos = {"n": 0}

        @transaccion_con_reintento(max_intentos=3)
        def fallo_integridad():
            intentos["n"] += 1
            raise IntegrityError("UNIQUE constraint failed")

        with self.assertRaises(IntegrityError):
            fallo_integridad()

        self.assertEqual(intentos["n"], 1)
        mock_sleep.assert_not_called()

    @patch("inventario.db.transacciones.time.sleep")
    def test_transaccion_anidada_delega_reintento_al_bloque_externo(self, mock_sleep):
        ejecuciones_externas = {"n": 0}
        ejecuciones_internas = {"n": 0}

        @transaccion_con_reintento(max_intentos=3)
        def paso_interno():
            ejecuciones_internas["n"] += 1
            if ejecuciones_externas["n"] == 1:
                raise OperationalError("database is locked")

        @transaccion_con_reintento(max_intentos=3)
        def flujo_externo():
            ejecuciones_externas["n"] += 1
            Almacen.objects.create(nombre=f"Externo {ejecuciones_externas['n']}", activo=True)
            paso_interno()

        flujo_externo()
        # El bloque interno NO reintentó por su cuenta; propagó al externo para repetir toda la unidad
        self.assertEqual(ejecuciones_externas["n"], 2)
        self.assertEqual(ejecuciones_internas["n"], 2)
        self.assertEqual(mock_sleep.call_count, 1)
        self.assertEqual(
            list(Almacen.objects.filter(nombre__startswith="Externo ").values_list("nombre", flat=True)),
            ["Externo 2"],
        )


class ExcepcionesDominioTest(SimpleTestCase):
    """Verifica jerarquía y compatibilidad de las excepciones de dominio con las vistas."""

    def test_herencia_validation_error_y_service_error(self):
        ex_stock = StockInsuficiente(
            "Stock insuficiente de sensor.",
            producto_id=10,
            almacen_id=2,
            disponible=1,
            solicitado=5,
        )
        self.assertIsInstance(ex_stock, ValidationError)
        self.assertIsInstance(ex_stock, InventarioServiceError)
        self.assertEqual(ex_stock.producto_id, 10)
        self.assertEqual(ex_stock.disponible, 1)
        self.assertEqual(ex_stock.solicitado, 5)
        self.assertEqual(ex_stock.code, "stock_insuficiente")

        ex_inc = InconsistenciaInventario(
            "Descuadre en préstamo.",
            entidad="Existencia",
            entidad_id=7,
            detalles={"esperado": 4, "actual": 1},
        )
        self.assertIsInstance(ex_inc, ValidationError)
        self.assertIsInstance(ex_inc, InventarioServiceError)
        self.assertEqual(ex_inc.detalles["esperado"], 4)

        ex_dup = OperacionDuplicada(
            "Movimiento ya aplicado.",
            clave_idempotencia="SAL-1-1-abc",
            movimiento_id="uuid-123",
        )
        self.assertIsInstance(ex_dup, ValidationError)
        self.assertIsInstance(ex_dup, InventarioServiceError)
        self.assertEqual(ex_dup.clave_idempotencia, "SAL-1-1-abc")

        ex_tras = NecesitaTraslado("Requiere traslado", entidad="Almacen", destino="Norte")
        self.assertIsInstance(ex_tras, InventarioServiceError)

    def test_compatibilidad_con_procesar_error_formulario(self):
        form = _FormularioPrueba(data={"cantidad": 5})
        self.assertTrue(form.is_valid())
        _procesar_error_formulario(form, StockInsuficiente("Solo queda 1 unidad disponible."))
        self.assertIn("Solo queda 1 unidad disponible.", form.non_field_errors())
