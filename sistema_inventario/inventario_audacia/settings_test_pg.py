"""Configuración de Django para ejecutar la suite de pruebas sobre PostgreSQL.

Uso:
    python manage.py test --settings=inventario_audacia.settings_test_pg

Requiere un servidor PostgreSQL accesible (nativo o externo, sin necesidad de Docker)
y ``psycopg`` (>= 3.1.12) o ``psycopg2`` (>= 2.9.9) instalado en el entorno virtual.
"""

import os

from .settings import *  # noqa: F401,F403

USE_SQLITE = False
DB_ENGINE = "postgresql"

_pg_test_options: dict = {}
try:
    from django.db.backends.postgresql.psycopg_any import IsolationLevel

    _pg_test_options["isolation_level"] = IsolationLevel.READ_COMMITTED
except ImportError:
    _pg_test_options["options"] = "-c default_transaction_isolation=read\\ committed"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("DB_NAME", "inventario_audacia"),
        "USER": os.getenv("DB_USER", "inventario"),
        "PASSWORD": os.getenv("DB_PASSWORD", ""),
        "HOST": os.getenv("DB_HOST", "localhost"),
        "PORT": os.getenv("DB_PORT", "5432"),
        # En suites de pruebas CONN_MAX_AGE=0 evita bloqueos al destruir test_<db>.
        "CONN_MAX_AGE": 0,
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": _pg_test_options,
    }
}
