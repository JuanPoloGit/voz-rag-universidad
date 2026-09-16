"""Punto de entrada de HACU.

Levanta el sistema mediante el composition root compartido y cede el control a la
consola o a la ventana de exhibicion.

Uso:
    python run_hacu.py                 # consola limpia
    python run_hacu.py --debug         # router, latencias y diagnostico
    python run_hacu.py --ui            # ventana de exhibicion
    python run_hacu.py --voz-salida    # consola: escribes tu, HACU responde en voz alta
    python run_hacu.py --ui --voz      # ventana con microfono y altavoz
    python run_hacu.py --ui --voz --pantalla-completa
"""

from __future__ import annotations

import argparse
import logging
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
    parser.add_argument("--ui", action="store_true", help="Ventana grafica en vez de consola")
    parser.add_argument("--voz", action="store_true", help="Activa microfono y altavoz")
    parser.add_argument("--voz-salida", action="store_true",
                        help="Solo altavoz: HACU habla, pero se escribe la pregunta")
    parser.add_argument("--sin-voz", action="store_true", help="Fuerza el modo escrito")
    parser.add_argument("--pantalla-completa", action="store_true")
    return parser.parse_args()


def aplicar_argumentos(config: AppConfig, args: argparse.Namespace) -> AppConfig:
    """Los argumentos de la linea de ordenes mandan sobre las variables de entorno."""
    if args.debug:
        config = replace(config, debug_console=True)
    if args.voz:
        config = replace(config, voz=replace(config.voz, activa=True))
    if args.voz_salida:
        config = replace(config, voz=replace(config.voz, solo_salida=True))
    if args.sin_voz:
        config = replace(config, voz=replace(config.voz, activa=False, solo_salida=False))
    if args.pantalla_completa:
        config = replace(config, interfaz=replace(config.interfaz, pantalla_completa=True))
    return config


def _arrancar_voz(config: AppConfig, logger: logging.Logger):
    """Construye la capa de voz sin tumbar el arranque si falta hardware."""
    from hacu.voz import ServicioDeVoz  # noqa: PLC0415  (import perezoso: Qt/audio son opcionales)

    servicio = ServicioDeVoz(config.voz, logger)
    if not (config.voz.activa or config.voz.solo_salida):
        return servicio
    for problema in servicio.problemas:
        print(f"⚠️  {problema}")
    print(f"🎤 Oido: {'listo' if servicio.disponible else 'NO disponible'}"
          f"   🔊 Voz: {servicio.motor if servicio.puede_hablar else 'NO disponible'}")
    if "externo" in servicio.motor:
        print("   ⚠️  Ese motor arranca un proceso por frase: habra pausas largas entre")
        print("      frases. Descarga la voz con `python -m hacu.voz --descargar`.")
    if not servicio.puede_hablar:
        print("   Comprueba el sintetizador con: python -m hacu.voz --hablar \"prueba\"")
    # El modelo de reconocimiento solo se carga si alguien va a hablarle. En modo
    # solo-salida seria descargar un modelo entero para no usarlo nunca.
    if servicio.disponible:
        print("   Cargando el modelo de reconocimiento...")
        try:
            servicio.precargar()
        except Exception as error:
            print(f"⚠️  No se pudo precargar el reconocimiento: {error}")
            logger.warning("Precarga de STT fallida", exc_info=True)
    return servicio


def main() -> int:
    args = parsear_argumentos()
    config = aplicar_argumentos(AppConfig.from_env(), args)
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

    voz = _arrancar_voz(config, logger)
    try:
        if args.ui:
            try:
                from hacu.interfaz import lanzar  # noqa: PLC0415
            except ImportError as error:
                print(f"❌ Falta la interfaz grafica: {error}")
                print("   Instala con: pip install -r requirements-ui.txt")
                return 1
            return lanzar(componentes, config, voz)

        consola = HacuConsole(
            config=config, logger=logger, sesion=componentes.sesion, db=componentes.db, voz=voz
        )
        consola.ejecutar()
        return 0
    finally:
        voz.cerrar()
        cerrar(componentes)


if __name__ == "__main__":
    sys.exit(main())
