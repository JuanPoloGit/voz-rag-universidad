"""
URL raíz de Inventario AudacIA.

Cada app registra sus propias rutas bajo su prefijo.
"""

from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.static import serve

from inventario.views import error_403_view, error_404_view, error_500_view

urlpatterns = [
    path("admin/", admin.site.urls),
    path("cuentas/", include("cuentas.urls")),
    path("inventario/", include("inventario.urls")),
    path("api/haku/", include("integraciones.haku.urls")),
    path("403/", error_403_view, name="error-403"),
    path("404/", error_404_view, name="error-404"),
    path("500/", error_500_view, name="error-500"),
    path("", include("web.urls")),
    re_path(
        r"^static/(?P<path>.*)$",
        serve,
        {"document_root": settings.BASE_DIR / "static"},
    ),
]

handler403 = "inventario.views.error_403_view"
handler404 = "inventario.views.error_404_view"
handler500 = "inventario.views.error_500_view"
