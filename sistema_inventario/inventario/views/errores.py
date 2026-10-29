"""Vistas personalizadas para manejo de errores HTTP (403, 404, 500)."""

from django.shortcuts import render


def error_403_view(request, exception=None):
    """Vista personalizada para error 403 (Permiso Denegado)."""
    return render(request, "403.html", status=403)


def error_404_view(request, exception=None):
    """Vista personalizada para error 404 (No Encontrado)."""
    return render(request, "404.html", status=404)


def error_500_view(request):
    """Vista personalizada para error 500 (Error del Servidor)."""
    return render(request, "500.html", status=500)
