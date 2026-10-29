"""Capa de servicios del sistema de inventario.

Autoridad única para mutaciones de datos maestros y existencias.
"""

from .excepciones import (
    ExcepcionDominioInventario,
    InconsistenciaInventario,
    InventarioServiceError,
    NecesitaTraslado,
    OperacionDuplicada,
    StockInsuficiente,
)


from .stock import (
    ajustar_stock_conteo,
    bloquear_existencias,
    dar_baja_prestamo,
    descontar_disponible,
    inicializar_stock,
    mover_a_prestamo,
    retornar_de_prestamo,
    sumar_disponible,
)
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
    rechazar_conteo_fisico,
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
