"""Context processors para la aplicación inventario."""

from typing import Any, Dict
from django.core.cache import cache

from inventario.models import ConteoFisico
from inventario.servicios.alertas import (
    CACHE_ALERTA_TTL,
    obtener_alertas_stock,
    obtener_claves_alertas_leidas,
    obtener_version_alertas,
)


def alertas_stock(request) -> Dict[str, Any]:
    """Inyecta el resumen de alertas de stock y notificaciones pendientes para el usuario.

    Optimizado bajo la Fase 4 con caché atómica por usuario/versión y omisión en AJAX/JSON.
    """
    if (
        not hasattr(request, "user")
        or not request.user.is_authenticated
        or request.headers.get("x-requested-with") == "XMLHttpRequest"
        or "application/json" in request.headers.get("Accept", "")
        or request.path_info.startswith("/static/")
        or request.path_info.startswith("/media/")
    ):
        return {
            "alertas_resumen": {
                "criticas": [],
                "bajas": [],
                "conteos_pendientes": [],
                "conteos_no_leidos": [],
                "total_criticas": 0,
                "total_bajas": 0,
                "total_conteos": 0,
                "total_alertas_stock": 0,
                "total_alertas": 0,
                "total_no_leidas": 0,
            }
        }

    version = obtener_version_alertas()
    cache_key = f"alertas:{request.user.id}:{version}"
    resumen_en_cache = cache.get(cache_key)
    if resumen_en_cache is not None:
        return {"alertas_resumen": resumen_en_cache}

    alertas = obtener_alertas_stock(usuario=request.user)
    leidas = obtener_claves_alertas_leidas(request.user)
    if hasattr(request, "session"):
        request.session["alertas_leidas"] = list(leidas)

    criticas = list(alertas.get("criticas", []))
    bajas = list(alertas.get("bajas", []))
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
    alertas["total_alertas_stock"] = alertas.get("total_alertas", 0)

    # Conteos físicos pendientes de confirmación administrativa (notificación exclusiva de administradores)
    conteos_pendientes = []
    conteos_no_leidos = []
    if getattr(request.user, "es_administrador", False):
        conteos_qs = ConteoFisico.objects.filter(estado=ConteoFisico.Estado.PENDIENTE).select_related(
            "producto", "almacen", "usuario_conteo"
        ).order_by("-fecha_conteo")
        for c in conteos_qs:
            clave = f"conteo:{c.id}"
            c.notificacion_key = clave
            c.es_leida = clave in leidas
            conteos_pendientes.append(c)
            if not c.es_leida:
                conteos_no_leidos.append(c)

    alertas["conteos_pendientes"] = conteos_pendientes
    alertas["conteos_no_leidos"] = conteos_no_leidos
    alertas["total_conteos"] = len(conteos_pendientes)
    alertas["total_conteos_no_leidos"] = len(conteos_no_leidos)
    alertas["total_alertas"] = alertas["total_alertas_stock"] + len(conteos_pendientes)
    alertas["total_no_leidas"] = len(criticas_no_leidas) + len(bajas_no_leidas) + len(conteos_no_leidos)

    cache.set(cache_key, alertas, CACHE_ALERTA_TTL)
    return {
        "alertas_resumen": alertas,
    }
