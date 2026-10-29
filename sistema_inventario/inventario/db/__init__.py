"""Utilidades de infraestructura de base de datos agnósticas al motor (SQLite / PostgreSQL)."""

from .transacciones import (
    CODIGOS_PG_REINTENTABLES,
    es_error_transitorio_bd,
    transaccion_con_reintento,
)

__all__ = [
    "CODIGOS_PG_REINTENTABLES",
    "es_error_transitorio_bd",
    "transaccion_con_reintento",
]
