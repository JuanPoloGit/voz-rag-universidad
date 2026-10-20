"""Configuración de la app integraciones.haku."""

from django.apps import AppConfig


class HakuConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "integraciones.haku"
    verbose_name = "HACU Inventario y Voz"

    def ready(self):
        """Inicializaciones al arrancar Django."""
        try:
            from .cache import conectar_senales_cache
            from .contexto import conectar_senales_contexto
            conectar_senales_cache()
            conectar_senales_contexto()
        except Exception:
            pass
