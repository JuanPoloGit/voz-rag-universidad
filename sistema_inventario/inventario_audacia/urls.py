"""
URL raíz de Inventario AudacIA.

Cada app registra sus propias rutas bajo su prefijo.
"""

from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.static import serve

urlpatterns = [
    path("admin/", admin.site.urls),
    path("cuentas/", include("cuentas.urls")),
    path("inventario/", include("inventario.urls")),
    path("api/haku/", include("integraciones.haku.urls")),
    path("", include("web.urls")),
    re_path(
        r"^static/(?P<path>.*)$",
        serve,
        {"document_root": settings.BASE_DIR / "static"},
    ),
]
