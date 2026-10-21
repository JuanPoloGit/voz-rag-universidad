"""Context processors para la aplicación inventario."""

from typing import Any, Dict
from inventario.servicios.alertas import obtener_alertas_stock


def alertas_stock(request) -> Dict[str, Any]:
    """Inyecta el resumen de alertas de stock y notificaciones pendientes para el usuario."""
    if not hasattr(request, "user") or not request.user.is_authenticated:
        return {
            "alertas_resumen": {
                "criticas": [],
                "bajas": [],
                "total_criticas": 0,
                "total_bajas": 0,
                "total_alertas": 0,
                "total_no_leidas": 0,
            }
        }

    alertas = obtener_alertas_stock(usuario=request.user)
    leidas = set(request.session.get("alertas_leidas", [])) if hasattr(request, "session") else set()
    no_leidas = 0
    for ex in alertas.get("criticas", []):
        clave = f"{ex.producto_id}:{ex.almacen_id}:{ex.stock_disponible}"
        ex.notificacion_key = clave
        ex.es_leida = clave in leidas
        if not ex.es_leida:
            no_leidas += 1
    for ex in alertas.get("bajas", []):
        clave = f"{ex.producto_id}:{ex.almacen_id}:{ex.stock_disponible}"
        ex.notificacion_key = clave
        ex.es_leida = clave in leidas
        if not ex.es_leida:
            no_leidas += 1

    alertas["total_no_leidas"] = no_leidas
    return {
        "alertas_resumen": alertas,
    }
