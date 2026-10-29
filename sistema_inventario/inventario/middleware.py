"""
Middleware para la visualización de páginas de error personalizadas.
Permite renderizar 404.html de diseño propio tanto en producción como
en desarrollo local (DEBUG = True), salvo que se use ?debug=1.
"""

from django.conf import settings
from django.shortcuts import render


class CustomErrorPagesMiddleware:
    """Intercepa respuestas 404 en navegación web para mostrar la plantilla Dark Navy."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        # Interceptar 404 para peticiones web normales
        if response.status_code == 404:
            static_url = getattr(settings, "STATIC_URL", "/static/")
            media_url = getattr(settings, "MEDIA_URL", "/media/")
            if (
                not request.path.startswith(static_url)
                and not request.path.startswith(media_url)
                and not request.path.startswith("/api/")
                and request.headers.get("x-requested-with") != "XMLHttpRequest"
                and request.GET.get("debug") != "1"
            ):
                return render(request, "404.html", status=404)

        return response
