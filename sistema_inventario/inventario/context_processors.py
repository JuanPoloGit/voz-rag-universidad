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
    criticas = alertas.get("criticas", [])
    bajas = alertas.get("bajas", [])
    for ex in criticas:
        clave = f"{ex.producto_id}:{ex.almacen_id}:{ex.stock_disponible}"
        ex.notificacion_key = clave
        ex.es_leida = clave in leidas
    for ex in bajas:
        clave = f"{ex.producto_id}:{ex.almacen_id}:{ex.stock_disponible}"
        ex.notificacion_key = clave
        ex.es_leida = clave in leidas

    criticas_no_leidas = [ex for ex in criticas if not ex.es_leida]
    bajas_no_leidas = [ex for ex in bajas if not ex.es_leida]
    alertas["criticas_no_leidas"] = criticas_no_leidas
    alertas["bajas_no_leidas"] = bajas_no_leidas
    alertas["total_no_leidas"] = len(criticas_no_leidas) + len(bajas_no_leidas)
    return {
        "alertas_resumen": alertas,
    }
