from django.apps import AppConfig
from django.db.backends.signals import connection_created


def activar_sqlite_wal(sender, connection, **kwargs):
    """Configura SQLite con WAL mode y timeout para alta concurrencia."""
    if connection.vendor == "sqlite":
        with connection.cursor() as cursor:
            cursor.execute("PRAGMA journal_mode = WAL;")
            cursor.execute("PRAGMA synchronous = NORMAL;")
            cursor.execute("PRAGMA busy_timeout = 20000;")


class InventarioConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "inventario"
    verbose_name = "Inventario"

    def ready(self):
        import inventario.signals_auditoria  # noqa: F401
        connection_created.connect(activar_sqlite_wal)

