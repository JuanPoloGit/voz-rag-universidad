from django.apps import AppConfig


class InventarioConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "inventario"
    verbose_name = "Inventario"

    def ready(self):
        import inventario.signals_auditoria  # noqa: F401

