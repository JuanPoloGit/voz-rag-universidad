"""WSGI config for Inventario AudacIA."""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "inventario_audacia.settings")

application = get_wsgi_application()
