"""Punto de entrada de HACU.

Levanta el sistema mediante el composition root compartido y cede el control a la
consola de exhibicion.

Uso:
    python run_hacu.py            # modo exhibicion (consola limpia)
    python run_hacu.py --debug    # muestra router, latencias y diagnostico
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace

from hacu.bootstrap import cerrar, construir
from hacu.cli import HacuConsole
from hacu.config import AppConfig
from hacu.llm import detectar_gpu
from hacu.logging_setup import configurar_logging


def parsear_argumentos() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Asistente expositor HACU")
    parser.add_argument("--debug", action="store_true", help="Diagnostico en consola")
    return parser.parse_args()


def main() -> int:
    args = parsear_argumentos()
    config = AppConfig.from_env()
    if args.debug:
        config = replace(config, debug_console=True)

    logger = configurar_logging(config.log_file, config.debug_console)

    print("--- DIAGNOSTICO DE HARDWARE ---")
    gpu = detectar_gpu()
    if gpu:
        print(f"✅ GPU detectada: {gpu}")
    else:
        print("⚠️  No se detecto GPU NVIDIA. El modelo correra en CPU (mucho mas lento).")
        logger.warning("nvidia-smi no disponible; posible ejecucion en CPU")

    try:
        componentes = construir(config, logger, progreso=lambda m: print(f"🔧 {m}"))
    except Exception as error:
        print(f"❌ No se pudo iniciar HACU: {error}")
        logger.critical("Fallo el arranque", exc_info=True)
        return 1
    if componentes.segundos_precalentamiento:
        print(f"✅ Sistema listo (precalentado en {componentes.segundos_precalentamiento:.1f}s).")
    else:
        print("✅ Sistema listo.")

    consola = HacuConsole(
        config=config, logger=logger, sesion=componentes.sesion, db=componentes.db
    )
    try:
        consola.ejecutar()
    finally:
        cerrar(componentes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
