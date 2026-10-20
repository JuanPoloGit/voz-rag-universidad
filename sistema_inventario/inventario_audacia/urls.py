"""
URL raíz de Inventario AudacIA.

Cada app registra sus propias rutas bajo su prefijo.
"""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("cuentas/", include("cuentas.urls")),
    path("inventario/", include("inventario.urls")),
    path("api/haku/", include("integraciones.haku.urls")),
    path("", include("web.urls")),
]
