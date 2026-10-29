"""Gestión de transacciones atómicas con reintento para SQLite y PostgreSQL.

Proporciona el decorador ``@transaccion_con_reintento`` para encapsular unidades
de trabajo transaccionales de modo agnóstico al motor de base de datos:
- En SQLite (modo WAL + ``IMMEDIATE``): reintenta ante ``OperationalError``
  transitorios por contención de escritura (``locked`` / ``busy``).
- En PostgreSQL (``READ COMMITTED``): reintenta ante fallos de serialización
  (SQLSTATE ``40001``) o interbloqueos detectados (SQLSTATE ``40P01``).
"""

import functools
import logging
import sqlite3
import time
from typing import Any, Callable, Optional, Sequence, TypeVar

from django.db import DatabaseError, OperationalError, transaction
from django.db.transaction import get_connection

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

# SQLSTATE estándar de PostgreSQL para errores transaccionales reintentables:
# - 40001: serialization_failure (SerializationFailure)
# - 40P01: deadlock_detected (DeadlockDetected)
CODIGOS_PG_REINTENTABLES = frozenset({"40001", "40P01"})

TIEMPOS_ESPERA_DEFECTO: Sequence[float] = (0.05, 0.15, 0.40)


def es_error_transitorio_bd(exc: BaseException) -> bool:
    """Determina si una excepción corresponde a un fallo transitorio reintentable.

    Inspecciona la cadena causal completa (``__cause__`` y ``__context__``) para
    soportar tanto ``sqlite3`` como ``psycopg2`` y ``psycopg`` v3 sin importar
    directamente drivers específicos del motor.
    """
    vistos: set[int] = set()
    actual: Optional[BaseException] = exc

    while actual is not None and id(actual) not in vistos:
        vistos.add(id(actual))

        pgcode = getattr(actual, "pgcode", None) or getattr(actual, "sqlstate", None)
        if isinstance(pgcode, str) and pgcode.upper() in CODIGOS_PG_REINTENTABLES:
            return True

        if isinstance(actual, (OperationalError, sqlite3.OperationalError)):
            mensaje = str(actual).lower()
            if "locked" in mensaje or "busy" in mensaje:
                return True

        actual = actual.__cause__ or actual.__context__

    return False


def _hay_transaccion_externa_activa(connection: Any) -> bool:
    """Indica si ya existe una transacción de negocio activa fuera del decorador.

    Ignora los bloques ``atomic()`` abiertos por ``django.test.TestCase``
    (marcados con ``_from_testcase = True``) para permitir probar los reintentos
    mediante savepoints en pruebas unitarias convencionales.
    """
    if not getattr(connection, "in_atomic_block", False):
        return False

    bloques = getattr(connection, "atomic_blocks", None)
    if not bloques:
        # Ej. ATOMIC_REQUESTS o autocommit desactivado manualmente
        return True

    return any(not getattr(bloque, "_from_testcase", False) for bloque in bloques)


def transaccion_con_reintento(
    _func: Optional[F] = None,
    *,
    max_intentos: int = 3,
    tiempos_espera: Sequence[float] = TIEMPOS_ESPERA_DEFECTO,
    using: Optional[str] = None,
) -> Any:
    """Ejecuta la función decorada dentro de ``transaction.atomic()`` con reintentos.

    Args:
        max_intentos: Número máximo de ejecuciones totales (mínimo 1).
        tiempos_espera: Secuencia de pausas en segundos entre intentos fallidos
            (por defecto 50 ms, 150 ms y 400 ms).
        using: Alias de conexión de base de datos en ``settings.DATABASES``.
    """
    if max_intentos < 1:
        raise ValueError("max_intentos debe ser al menos 1.")

    esperas = tuple(float(t) for t in tiempos_espera) if tiempos_espera else (0.0,)

    def decorador(func: F) -> F:
        @functools.wraps(func)
        def envoltura(*args: Any, **kwargs: Any) -> Any:
            conn = get_connection(using)

            # Si ya estamos anidados dentro de otra transacción de negocio,
            # delegamos el reintento completo al bloque transaccional externo.
            if _hay_transaccion_externa_activa(conn):
                with transaction.atomic(using=using):
                    return func(*args, **kwargs)

            for indice_intento in range(max_intentos):
                try:
                    with transaction.atomic(using=using):
                        return func(*args, **kwargs)
                except DatabaseError as exc:
                    es_ultimo_intento = (indice_intento + 1) >= max_intentos
                    if (
                        es_ultimo_intento
                        or not es_error_transitorio_bd(exc)
                        or getattr(conn, "needs_rollback", False)
                    ):
                        raise

                    pausa = esperas[min(indice_intento, len(esperas) - 1)]
                    logger.warning(
                        "Error transitorio de BD en %s (intento %d/%d: %s). "
                        "Reintentando en %.3fs...",
                        func.__qualname__,
                        indice_intento + 1,
                        max_intentos,
                        exc,
                        pausa,
                    )
                    if pausa > 0:
                        time.sleep(pausa)

            raise RuntimeError("Flujo inalcanzable en transaccion_con_reintento.")  # pragma: no cover

        return envoltura  # type: ignore[return-value]

    if _func is not None:
        return decorador(_func)
    return decorador
