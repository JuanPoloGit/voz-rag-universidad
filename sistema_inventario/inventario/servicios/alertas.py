"""Servicios de detección y consulta de alertas de inventario.

Monitorea existencias en estado crítico (sin stock, stock == 0) y estado
bajo (stock < stock_minimo) en cumplimiento estricto de la sección 8.4
de la especificación de requisitos.
"""

from typing import Any, Dict, Optional
from django.core.cache import cache
from django.db.models import F

from inventario.models import Almacen, Existencia, AlertaStockLeida

CACHE_ALERTA_VERSION_KEY = "alertas:version"
CACHE_ALERTA_TTL = 60


def obtener_version_alertas() -> int:
    """Retorna la versión actual del caché de alertas de stock.

    Si no existe la clave en la caché, se inicializa en 1.
    """
    version = cache.get(CACHE_ALERTA_VERSION_KEY)
    if version is None:
        version = 1
        cache.set(CACHE_ALERTA_VERSION_KEY, version, None)
    return int(version)


def invalidar_cache_alertas() -> None:
    """Incrementa la versión global de alertas para invalidar claves de usuario atómicamente."""
    try:
        cache.incr(CACHE_ALERTA_VERSION_KEY)
    except Exception:
        cache.set(CACHE_ALERTA_VERSION_KEY, 1, None)


def verificar_estado_alerta(existencia: Existencia) -> Dict[str, Any]:
    """Determina si una existencia se encuentra en nivel crítico, bajo o normal.
    
    Reglas sección 8.4:
    - Sin stock (CRÍTICO): stock_disponible == 0.
    - Stock bajo (BAJO): 0 < stock_disponible < producto.stock_minimo.
    - Normal: stock_disponible >= producto.stock_minimo.
    """
    prod = existencia.producto
    stock = existencia.stock_disponible
    minimo = prod.stock_minimo

    if stock == 0:
        return {
            "nivel": "CRITICO",
            "mensaje": (
                f"¡ALERTA CRÍTICA! El producto '{prod.codigo_interno} — {prod.nombre}' "
                f"se encuentra AGOTADO (0 unidades) en el almacén '{existencia.almacen.nombre}'."
            ),
        }
    elif 0 < stock < minimo:
        return {
            "nivel": "BAJO",
            "mensaje": (
                f"¡ALERTA DE STOCK BAJO! El producto '{prod.codigo_interno} — {prod.nombre}' "
                f"tiene solo {stock} unidades disponibles (umbral mínimo: {minimo}) "
                f"en el almacén '{existencia.almacen.nombre}'."
            ),
        }
    return {
        "nivel": None,
        "mensaje": "",
    }


def obtener_alertas_stock(
    *,
    usuario: Optional[Any] = None,
    almacen: Optional[Almacen] = None,
) -> Dict[str, Any]:
    """Obtiene las existencias con alerta crítica y baja respetando el ámbito del usuario.
    
    Si el usuario tiene rol Usuario normal (es_usuario_normal=True), solo retorna
    las existencias de su almacén asignado. Si es administrador o superusuario,
    retorna las de todos los almacenes (a menos que se especifique un almacén).
    """
    qs = Existencia.objects.filter(
        producto__archivado=False,
        producto__activo=True,
        almacen__activo=True,
        almacen__eliminado=False,
    ).select_related("producto", "almacen")

    if usuario and getattr(usuario, "es_usuario_normal", False):
        user_alms = usuario.get_almacenes_asignados()
        if user_alms.exists():
            qs = qs.filter(almacen__in=user_alms)
            if almacen:
                if user_alms.filter(pk=almacen.pk).exists():
                    qs = qs.filter(almacen=almacen)
                else:
                    qs = qs.none()
        else:
            qs = qs.none()
    elif almacen:
        qs = qs.filter(almacen=almacen)

    criticas = qs.filter(stock_disponible=0).order_by("producto__codigo_interno")
    bajas = qs.filter(
        stock_disponible__gt=0,
        stock_disponible__lt=F("producto__stock_minimo"),
    ).order_by("producto__codigo_interno")

    total_criticas = criticas.count()
    total_bajas = bajas.count()

    return {
        "criticas": criticas,
        "bajas": bajas,
        "total_criticas": total_criticas,
        "total_bajas": total_bajas,
        "total_alertas": total_criticas + total_bajas,
    }


def obtener_claves_alertas_leidas(usuario) -> set:
    """Retorna el conjunto de claves de alertas marcadas como leídas de forma persistente."""
    if not usuario or not usuario.is_authenticated:
        return set()
    return set(
        AlertaStockLeida.objects.filter(usuario=usuario).values_list("clave", flat=True)
    )


def marcar_alerta_stock_leida(usuario, clave: str) -> None:
    """Marca una alerta específica como leída para el usuario."""
    if usuario and usuario.is_authenticated and clave:
        AlertaStockLeida.objects.get_or_create(usuario=usuario, clave=clave)
        invalidar_cache_alertas()


def desmarcar_alerta_stock_leida(usuario, clave: str) -> None:
    """Desmarca una alerta como leída (vuelve a estado pendiente)."""
    if usuario and usuario.is_authenticated and clave:
        AlertaStockLeida.objects.filter(usuario=usuario, clave=clave).delete()
        invalidar_cache_alertas()


def marcar_todas_alertas_stock_leidas(usuario, claves: list) -> None:
    """Marca un conjunto de alertas como leídas en bloque para el usuario."""
    if usuario and usuario.is_authenticated and claves:
        existentes = set(
            AlertaStockLeida.objects.filter(usuario=usuario, clave__in=claves).values_list("clave", flat=True)
        )
        nuevas = [AlertaStockLeida(usuario=usuario, clave=k) for k in claves if k not in existentes]
        if nuevas:
            AlertaStockLeida.objects.bulk_create(nuevas, ignore_conflicts=True)
        invalidar_cache_alertas()


def limpiar_alertas_stock_leidas(usuario) -> None:
    """Elimina todas las alertas leídas registradas para el usuario."""
    if usuario and usuario.is_authenticated:
        AlertaStockLeida.objects.filter(usuario=usuario).delete()
        invalidar_cache_alertas()
