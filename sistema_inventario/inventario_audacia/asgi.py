"""ASGI config for Inventario AudacIA."""

import os

from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "inventario_audacia.settings")

django_application = get_asgi_application()
application = ASGIStaticFilesHandler(django_application)
