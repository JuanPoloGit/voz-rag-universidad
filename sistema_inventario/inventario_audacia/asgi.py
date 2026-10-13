"""ASGI config for Inventario AudacIA."""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "inventario_audacia.settings")

application = get_asgi_application()
