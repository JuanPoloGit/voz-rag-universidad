"""Configuración de la app integraciones.haku."""

from django.apps import AppConfig


class HakuConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "integraciones.haku"
    verbose_name = "Haku Inventario y Voz"

    def ready(self):
        """Inicializaciones al arrancar Django."""
        # Se asegura de registrar el adaptador de voz y servicios de Haku
        pass
