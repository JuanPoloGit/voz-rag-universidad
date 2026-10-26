"""Capa de servicios del sistema de inventario.

Autoridad única para mutaciones de datos maestros y existencias.
"""

class InventarioServiceError(Exception):
    """Excepción base para errores en la capa de servicios de inventario."""
    pass


class NecesitaTraslado(InventarioServiceError):
    """Se lanza cuando una operación requiere trasladar existencias o reasignar ubicaciones."""

    def __init__(self, mensaje: str, entidad=None, destino=None):
        super().__init__(mensaje)
        self.entidad = entidad
        self.destino = destino


from .alertas import (
    desmarcar_alerta_stock_leida,
    limpiar_alertas_stock_leidas,
    marcar_alerta_stock_leida,
    marcar_todas_alertas_stock_leidas,
    obtener_alertas_stock,
    obtener_claves_alertas_leidas,
    verificar_estado_alerta,
)
from .operaciones import (
    confirmar_conteo_fisico,
    registrar_baja_prestamo,
    registrar_conteo_fisico,
    registrar_devolucion_prestamo,
    registrar_entrada,
    registrar_prestamo,
    registrar_salida,
    registrar_traslado_entre_almacenes,
    registrar_traslado_interno,
    revertir_movimiento,
)
