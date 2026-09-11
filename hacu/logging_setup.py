"""Configuracion de logging.

La consola es parte del montaje escenico: solo debe mostrar la conversacion y el
panel del operador. Todo el diagnostico (router, extracciones, descartes del
saneador, errores de RAG) va al archivo de log, salvo que se active el modo debug.
"""

from __future__ import annotations

import logging
from pathlib import Path

_FORMATO = "%(asctime)s | %(levelname)-8s | %(name)-18s | %(message)s"


def configurar_logging(log_file: Path, debug_console: bool = False) -> logging.Logger:
    """Inicializa el logger raiz de HACU y devuelve el logger de la aplicacion."""
    log_file.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("hacu")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    logger.propagate = False

    archivo = logging.FileHandler(log_file, encoding="utf-8")
    archivo.setLevel(logging.DEBUG)
    archivo.setFormatter(logging.Formatter(_FORMATO))
    logger.addHandler(archivo)

    consola = logging.StreamHandler()
    consola.setLevel(logging.DEBUG if debug_console else logging.WARNING)
    consola.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(consola)

    return logger
