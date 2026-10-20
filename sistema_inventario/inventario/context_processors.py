"""Context processors para la aplicación inventario."""

from typing import Any, Dict
from inventario.servicios.alertas import obtener_alertas_stock


def alertas_stock(request) -> Dict[str, Any]:
    """Inyecta el resumen de alertas de stock para el usuario en cada petición."""
    if not hasattr(request, "user") or not request.user.is_authenticated:
        return {
            "alertas_resumen": {
                "criticas": [],
                "bajas": [],
                "total_criticas": 0,
                "total_bajas": 0,
                "total_alertas": 0,
            }
        }

    alertas = obtener_alertas_stock(usuario=request.user)
    return {
        "alertas_resumen": alertas,
    }
