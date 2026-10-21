"""Gestor de caché en RAM para optimización de lecturas frecuentes en Haku Inventario.

Cachés independientes (LocMemCache):
1. haku_cache_catalogo: Nombres, códigos y categorías (TTL: 30 minutos).
   Invalidado ante creación, edición o archivado de Producto.
2. haku_cache_ubicaciones: Ubicación física formateada por producto (TTL: 15 minutos).
   Invalidado cuando cambia la ubicación del producto.
3. haku_cache_stock: Stock orientativo disponible/en préstamo (TTL: 15 segundos máximo).
   Invalidado INMEDIATAMENTE tras cualquier movimiento, entrada, salida, préstamo o devolución.

REGLAS DE SEGURIDAD INNEGOCIABLES:
- NUNCA se cachean operaciones críticas ni validaciones de saldo para ejecutar transacciones.
- Toda mutación consulta la base de datos con select_for_update() en transacción atómica.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional

from django.core.cache import caches
from django.db.models.signals import post_delete, post_save

logger = logging.getLogger("integraciones.haku.cache")

ALIAS_CATALOGO = "haku_catalogo"
ALIAS_UBICACIONES = "haku_ubicaciones"
ALIAS_STOCK = "haku_stock"

TTL_CATALOGO = 1800     # 30 minutos
TTL_UBICACIONES = 900   # 15 minutos
TTL_STOCK = 15          # 15 segundos máximo
TTL_FALTANTES = 60      # 60 segundos para consultas agregadas de faltantes/reposición


class HakuCacheManager:
    """Gestor centralizado de cachés en RAM y métricas de rendimiento interno."""

    _lock = threading.Lock()
    _catalog_hits: int = 0
    _catalog_misses: int = 0
    _location_hits: int = 0
    _location_misses: int = 0
    _stock_hits: int = 0
    _stock_misses: int = 0
    _faltantes_hits: int = 0
    _faltantes_misses: int = 0
    _intenciones_detectadas: Dict[str, int] = {}

    @classmethod
    def _cache(cls, alias: str):
        try:
            return caches[alias]
        except Exception:
            return caches["default"]

    # ─────────────────────────────────────────────────────────────────────────
    # 1. CACHÉ DE CATÁLOGO (haku_cache_catalogo)
    # ─────────────────────────────────────────────────────────────────────────

    @classmethod
    def obtener_catalogo(cls, almacen_id: Optional[int] = None) -> Optional[List[Dict[str, Any]]]:
        """Obtiene la lista mínima de productos para reconocimiento del lenguaje."""
        cache = cls._cache(ALIAS_CATALOGO)
        key = f"catalogo:{almacen_id or 'all'}"
        data = cache.get(key)
        with cls._lock:
            if data is not None:
                cls._catalog_hits += 1
            else:
                cls._catalog_misses += 1
        return data

    @classmethod
    def guardar_catalogo(cls, data: List[Dict[str, Any]], almacen_id: Optional[int] = None) -> None:
        cache = cls._cache(ALIAS_CATALOGO)
        key = f"catalogo:{almacen_id or 'all'}"
        cache.set(key, data, timeout=TTL_CATALOGO)

    @classmethod
    def obtener_o_cargar_catalogo(cls, almacen_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """Obtiene el catálogo desde caché o lo precarga de DB si no existe."""
        cached = cls.obtener_catalogo(almacen_id)
        if cached is not None:
            return cached
        from inventario.models import Producto
        qs = Producto.objects.filter(activo=True, archivado=False)
        if almacen_id:
            qs = qs.filter(categoria__almacen_principal_id=almacen_id)
        data = list(qs.values("id", "nombre", "codigo_interno", "codigo_barras", "categoria__nombre"))
        cls.guardar_catalogo(data, almacen_id)
        return data

    @classmethod
    def invalidar_catalogo(cls) -> None:
        """Invalida el catálogo completo en RAM."""
        cache = cls._cache(ALIAS_CATALOGO)
        cache.clear()
        cls.invalidar_faltantes()
        logger.debug("Caché de catálogo invalidado")

    # ─────────────────────────────────────────────────────────────────────────
    # 2. CACHÉ DE UBICACIONES (haku_cache_ubicaciones)
    # ─────────────────────────────────────────────────────────────────────────

    @classmethod
    def obtener_ubicacion(cls, producto_id: int) -> Optional[str]:
        cache = cls._cache(ALIAS_UBICACIONES)
        key = f"ubicacion:{producto_id}"
        val = cache.get(key)
        with cls._lock:
            if val is not None:
                cls._location_hits += 1
            else:
                cls._location_misses += 1
        return val

    @classmethod
    def guardar_ubicacion(cls, producto_id: int, ubicacion_str: str) -> None:
        cache = cls._cache(ALIAS_UBICACIONES)
        key = f"ubicacion:{producto_id}"
        cache.set(key, ubicacion_str, timeout=TTL_UBICACIONES)

    @classmethod
    def obtener_o_cargar_ubicacion(cls, producto: Any) -> str:
        """Obtiene la ubicación formateada de caché o de DB si es un cache miss."""
        cached = cls.obtener_ubicacion(producto.id)
        if cached is not None:
            return cached
        ubicaciones = ", ".join(u.nombre for u in producto.ubicaciones.all()) or "Sin ubicación asignada"
        cls.guardar_ubicacion(producto.id, ubicaciones)
        return ubicaciones

    @classmethod
    def invalidar_ubicaciones(cls, producto_id: Optional[int] = None) -> None:
        cache = cls._cache(ALIAS_UBICACIONES)
        if producto_id:
            cache.delete(f"ubicacion:{producto_id}")
        else:
            cache.clear()

    # ─────────────────────────────────────────────────────────────────────────
    # 3. CACHÉ DE STOCK (haku_cache_stock - TTL 15s)
    # ─────────────────────────────────────────────────────────────────────────

    @classmethod
    def obtener_stock(cls, producto_id: int, almacen_id: Optional[int] = None) -> Optional[Dict[str, int]]:
        """Consulta stock orientativo de respuesta rápida."""
        cache = cls._cache(ALIAS_STOCK)
        key = f"stock:{producto_id}:{almacen_id or 'all'}"
        val = cache.get(key)
        with cls._lock:
            if val is not None:
                cls._stock_hits += 1
            else:
                cls._stock_misses += 1
        return val

    @classmethod
    def guardar_stock(cls, producto_id: int, almacen_id: Optional[int], data: Dict[str, int]) -> None:
        cache = cls._cache(ALIAS_STOCK)
        key = f"stock:{producto_id}:{almacen_id or 'all'}"
        cache.set(key, data, timeout=TTL_STOCK)

    @classmethod
    def obtener_o_cargar_stock(cls, producto_id: int, almacen_id: Optional[int] = None) -> Dict[str, int]:
        """Obtiene stock orientativo desde caché o lo consulta de DB si no está en RAM."""
        cached = cls.obtener_stock(producto_id, almacen_id)
        if cached is not None:
            return cached
        from inventario.models import Existencia
        existencia = Existencia.objects.filter(producto_id=producto_id, almacen_id=almacen_id).first()
        disponible = existencia.stock_disponible if existencia else 0
        prestado = existencia.stock_en_prestamo if existencia else 0
        data = {"disponible": disponible, "prestado": prestado}
        cls.guardar_stock(producto_id, almacen_id, data)
        return data

    @classmethod
    def invalidar_stock(cls, producto_id: Optional[int] = None, almacen_id: Optional[int] = None) -> None:
        """Invalida inmediatamente el stock tras cualquier mutación confirmada."""
        cache = cls._cache(ALIAS_STOCK)
        if producto_id:
            cache.delete(f"stock:{producto_id}:{almacen_id or 'all'}")
            cache.delete(f"stock:{producto_id}:all")
        else:
            cache.clear()
        cls.invalidar_faltantes(almacen_id)
        logger.debug("Caché de stock invalidado (producto=%s, almacen=%s)", producto_id, almacen_id)

    # ─────────────────────────────────────────────────────────────────────────
    # 4. CACHÉ DE FALTANTES Y REPOSICIÓN (TTL 60s)
    # ─────────────────────────────────────────────────────────────────────────

    @classmethod
    def obtener_faltantes(cls, almacen_id: Optional[int] = None, categoria_id: Optional[int] = None, scope_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        cache = cls._cache(ALIAS_STOCK)
        key = f"faltantes:{almacen_id or 'all'}:{categoria_id or 'all'}:{scope_id or 'global'}"
        data = cache.get(key)
        with cls._lock:
            if data is not None:
                cls._faltantes_hits += 1
            else:
                cls._faltantes_misses += 1
        return data

    @classmethod
    def guardar_faltantes(cls, data: Dict[str, Any], almacen_id: Optional[int] = None, categoria_id: Optional[int] = None, scope_id: Optional[str] = None) -> None:
        cache = cls._cache(ALIAS_STOCK)
        key = f"faltantes:{almacen_id or 'all'}:{categoria_id or 'all'}:{scope_id or 'global'}"
        cache.set(key, data, timeout=TTL_FALTANTES)

    @classmethod
    def invalidar_faltantes(cls, almacen_id: Optional[int] = None) -> None:
        cache = cls._cache(ALIAS_STOCK)
        if almacen_id:
            # Elimina claves conocidas para ese almacén y globales
            cache.delete(f"faltantes:{almacen_id}:all:global")
            cache.delete(f"faltantes:all:all:global")
        else:
            cache.delete(f"faltantes:all:all:global")
        # Además, como LocMemCache o Dummy no soportan patrones wildcard, se borran las variantes globales directas
        cache.clear()

    # ─────────────────────────────────────────────────────────────────────────
    # GESTIÓN Y MÉTRICAS
    # ─────────────────────────────────────────────────────────────────────────

    @classmethod
    def invalidar_todo(cls) -> None:
        """Limpia todos los cachés de lectura."""
        cls.invalidar_catalogo()
        cls.invalidar_ubicaciones()
        cls.invalidar_stock()
        cls.invalidar_faltantes()
        try:
            from .servicios_personas import HakuPersonasService
            HakuPersonasService.invalidar_cache_personas()
        except Exception:
            pass

    @classmethod
    def obtener_metricas(cls) -> Dict[str, int]:
        """Retorna las métricas acumuladas de aciertos y fallos de caché."""
        with cls._lock:
            return {
                "catalog_hits": cls._catalog_hits,
                "catalog_misses": cls._catalog_misses,
                "location_hits": cls._location_hits,
                "location_misses": cls._location_misses,
                "stock_hits": cls._stock_hits,
                "stock_misses": cls._stock_misses,
                "faltantes_hits": cls._faltantes_hits,
                "faltantes_misses": cls._faltantes_misses,
            }

    @classmethod
    def registrar_intencion_detectada(cls, tipo_intencion: str) -> None:
        """Incrementa el contador interno agregado de intenciones detectadas (sin guardar frases ni audios)."""
        if not tipo_intencion:
            return
        with cls._lock:
            cls._intenciones_detectadas[tipo_intencion] = cls._intenciones_detectadas.get(tipo_intencion, 0) + 1

    @classmethod
    def obtener_metricas_intenciones(cls) -> Dict[str, int]:
        """Retorna copia de los contadores agregados de intenciones detectadas."""
        with cls._lock:
            return dict(cls._intenciones_detectadas)

    @classmethod
    def reset_metricas(cls) -> None:
        with cls._lock:
            cls._catalog_hits = 0
            cls._catalog_misses = 0
            cls._location_hits = 0
            cls._location_misses = 0
            cls._stock_hits = 0
            cls._stock_misses = 0
            cls._faltantes_hits = 0
            cls._faltantes_misses = 0
            cls._intenciones_detectadas = {}


# ─────────────────────────────────────────────────────────────────────────────
# SEÑALES DE INVALIDACIÓN AUTOMÁTICA
# ─────────────────────────────────────────────────────────────────────────────

def _on_producto_modificado(sender, instance, **kwargs):
    """Invalida catálogo y ubicaciones cuando un producto se crea, edita o archiva."""
    from django.db import transaction

    def _invalidar():
        HakuCacheManager.invalidar_catalogo()
        HakuCacheManager.invalidar_ubicaciones(instance.id)
        HakuCacheManager.invalidar_stock(instance.id)

    # Si estamos en un TestCase sin commit de transacción o sin bloque atómico,
    # ejecutar de inmediato; de lo contrario en on_commit
    try:
        transaction.on_commit(_invalidar)
    except Exception:
        _invalidar()
    # Además, para asegurar que TestCase (que no hace commit real) invalide en memoria:
    _invalidar()


def _on_existencia_o_movimiento_modificado(sender, instance, **kwargs):
    """Invalida stock inmediatamente tras confirmarse cambios en base de datos."""
    from django.db import transaction
    prod_id = getattr(instance, "producto_id", None)
    almacen_id = getattr(instance, "almacen_id", None) or getattr(instance, "almacen_origen_id", None)

    def _invalidar():
        HakuCacheManager.invalidar_stock(prod_id, almacen_id)

    transaction.on_commit(_invalidar)


def _on_usuario_modificado(sender, instance, **kwargs):
    """Invalida caché de faltantes y personas cuando cambian permisos, roles o estado de un usuario."""
    from django.db import transaction

    def _invalidar():
        HakuCacheManager.invalidar_faltantes()
        HakuCacheManager.invalidar_todo()

    transaction.on_commit(_invalidar)


_senales_conectadas = False

def conectar_senales_cache():
    """Conecta los listeners de invalidación a los modelos de inventario y usuarios."""
    global _senales_conectadas
    if _senales_conectadas:
        return

    try:
        from inventario.models import Existencia, Movimiento, Prestamo, Producto
        from cuentas.models import UsuarioAudacIA
        post_save.connect(_on_producto_modificado, sender=Producto, dispatch_uid="haku_prod_save")
        post_delete.connect(_on_producto_modificado, sender=Producto, dispatch_uid="haku_prod_del")

        post_save.connect(_on_existencia_o_movimiento_modificado, sender=Existencia, dispatch_uid="haku_exist_save")
        post_save.connect(_on_existencia_o_movimiento_modificado, sender=Movimiento, dispatch_uid="haku_mov_save")
        post_save.connect(_on_existencia_o_movimiento_modificado, sender=Prestamo, dispatch_uid="haku_prest_save")

        post_save.connect(_on_usuario_modificado, sender=UsuarioAudacIA, dispatch_uid="haku_user_save")
        _senales_conectadas = True
        logger.info("Señales de invalidación de caché Haku conectadas con éxito")
    except Exception as exc:
        logger.warning("No se pudieron conectar las señales de caché Haku (%s)", exc)

