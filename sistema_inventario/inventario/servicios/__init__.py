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
