"""Excepciones de dominio para la capa de servicios de inventario.

Las excepciones operativas de stock e integridad heredan de
``django.core.exceptions.ValidationError`` (además de ``InventarioServiceError``)
para mantener compatibilidad transparente con formularios, modales AJAX y vistas
actuales sin requerir bloques ``except`` adicionales en la capa de presentación.
"""

from typing import Any, Optional

from django.core.exceptions import ValidationError


class InventarioServiceError(Exception):
    """Excepción base para errores en la capa de servicios de inventario."""


class NecesitaTraslado(InventarioServiceError):
    """Se lanza cuando una operación requiere trasladar existencias o reasignar ubicaciones."""

    def __init__(self, mensaje: str, entidad: Any = None, destino: Any = None):
        super().__init__(mensaje)
        self.entidad = entidad
        self.destino = destino


class ExcepcionDominioInventario(ValidationError, InventarioServiceError):
    """Base para errores de negocio que deben comportarse como ``ValidationError``."""

    codigo_defecto = "error_inventario"

    def __init__(
        self,
        mensaje: str,
        *,
        code: Optional[str] = None,
        params: Optional[dict[str, Any]] = None,
    ):
        super().__init__(mensaje, code=code or self.codigo_defecto, params=params)


class StockInsuficiente(ExcepcionDominioInventario):
    """Se lanza cuando el stock disponible no cubre la cantidad requerida."""

    codigo_defecto = "stock_insuficiente"

    def __init__(
        self,
        mensaje: str = "No hay stock disponible suficiente para completar la operación.",
        *,
        producto_id: Optional[int] = None,
        almacen_id: Optional[int] = None,
        disponible: Optional[int] = None,
        solicitado: Optional[int] = None,
    ):
        self.producto_id = producto_id
        self.almacen_id = almacen_id
        self.disponible = disponible
        self.solicitado = solicitado
        super().__init__(mensaje, code=self.codigo_defecto)


class InconsistenciaInventario(ExcepcionDominioInventario):
    """Se lanza cuando una invariante contable del inventario resulta violada."""

    codigo_defecto = "inconsistencia_inventario"

    def __init__(
        self,
        mensaje: str = "Se detectó una inconsistencia contable en las existencias del inventario.",
        *,
        entidad: Optional[str] = None,
        entidad_id: Optional[Any] = None,
        detalles: Optional[dict[str, Any]] = None,
    ):
        self.entidad = entidad
        self.entidad_id = entidad_id
        self.detalles = detalles or {}
        super().__init__(mensaje, code=self.codigo_defecto)


class OperacionDuplicada(ExcepcionDominioInventario):
    """Se lanza cuando una operación idempotente ya fue procesada previamente."""

    codigo_defecto = "operacion_duplicada"

    def __init__(
        self,
        mensaje: str = "Esta operación ya fue registrada previamente.",
        *,
        clave_idempotencia: Optional[str] = None,
        movimiento_id: Optional[Any] = None,
    ):
        self.clave_idempotencia = clave_idempotencia
        self.movimiento_id = movimiento_id
        super().__init__(mensaje, code=self.codigo_defecto)
