"""Composition root compartido.

Construye el arbol de dependencias una sola vez para que la consola de exhibicion
y el harness de pruebas arranquen el sistema de forma identica.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from .config import AppConfig
from .context import ContextBuilder
from .extractor import BackgroundMemoryExtractor
from .identity import IdentityResolver
from .llm import LlmService
from .memory import HacuMemoryDB
from .rag import LocalRAGEngine
from .routing import FastRouter
from .sanitizer import FactSanitizer
from .session import HacuSession

Progreso = Callable[[str], None]


class ConfiguracionInviable(RuntimeError):
    """El presupuesto de contexto no cabe en la ventana del modelo."""


@dataclass
class Componentes:
    """Referencias vivas del sistema, necesarias para el cierre ordenado."""

    config: AppConfig
    logger: logging.Logger
    llm: LlmService
    db: HacuMemoryDB
    rag: LocalRAGEngine
    identity: IdentityResolver
    extractor: BackgroundMemoryExtractor
    sesion: HacuSession
    perfiles_saneados: int = 0
    hechos_saneados: int = 0
    mensajes_podados: int = 0
    segundos_precalentamiento: float = 0.0


def verificar_presupuesto(config: AppConfig) -> None:
    """Falla al arrancar si el prompt no cabe, en vez de truncarse en escena.

    llama.cpp descarta por la izquierda cuando el contexto se desborda, y lo
    primero que se pierde es el system prompt: HACU cambiaria de personalidad sin
    que nada lo indique.
    """
    prompt, disponible = config.presupuesto_contexto()
    if prompt >= disponible:
        raise ConfiguracionInviable(
            f"El prompt estimado ({prompt} tokens) no cabe en la ventana disponible "
            f"({disponible} tokens tras reservar la generacion). Reduce "
            f"broad_results, history_messages o chat_max_tokens, o sube n_ctx."
        )


def construir(
    config: AppConfig, logger: logging.Logger, progreso: Progreso | None = None
) -> Componentes:
    """Levanta modelo, memoria, corpus y trabajador de fondo en ese orden."""
    avisar = progreso or (lambda _: None)
    verificar_presupuesto(config)
    prompt, disponible = config.presupuesto_contexto()
    logger.info("Presupuesto de contexto: %d de %d tokens", prompt, disponible)

    llm = LlmService(config.model, logger)
    avisar("Cargando el modelo en la VRAM...")
    llm.cargar()

    db = HacuMemoryDB(config.memory.db_path, logger)
    sanitizer = FactSanitizer()
    identity = IdentityResolver(config.default_user)

    perfiles = hechos = 0
    if config.memory.sanitize_on_startup:
        perfiles, hechos = db.sanear(sanitizer, identity.es_nombre_valido, config.default_user)
        if perfiles or hechos:
            avisar(f"Saneado inicial: {perfiles} perfiles y {hechos} hechos eliminados.")

    podados = db.podar_historial(config.memory.retencion_horas)
    if podados:
        avisar(f"Retencion: {podados} mensajes con mas de {config.memory.retencion_horas}h eliminados.")

    rag = LocalRAGEngine(config.rag, logger)
    if config.rag.multilingual_embeddings and not rag.embeddings_multilingues:
        mensaje = (
            "El embedding multilingue no esta disponible (falta sentence-transformers "
            "o fallo la descarga del modelo). Con el embedding por defecto la "
            "recuperacion sobre el corpus en espanol baja de 18/20 a 9/20 consultas: "
            "HACU diria no conocer proyectos que si estan documentados."
        )
        if config.rag.exigir_multilingue:
            raise ConfiguracionInviable(
                mensaje + " Instala 'pip install sentence-transformers' y verifica la "
                "conexion, o arranca con HACU_MULTILINGUE=0 para aceptar el modo degradado."
            )
        avisar("AVISO: " + mensaje)
    avisar("Sincronizando corpus institucional...")
    rag.sincronizar_documentos()
    rag.purgar_colecciones_obsoletas()

    extractor = BackgroundMemoryExtractor(
        db=db, llm=llm, sanitizer=sanitizer,
        memory_config=config.memory, model_config=config.model, logger=logger,
    )
    extractor.iniciar()

    sesion = HacuSession(
        llm=llm, db=db, router=FastRouter(), identity=identity, extractor=extractor,
        context_builder=ContextBuilder(db, rag, config.rag, config.memory), logger=logger,
    )

    avisar("Precalentando el modelo...")
    calentamiento = llm.precalentar()

    return Componentes(
        config=config, logger=logger, llm=llm, db=db, rag=rag, identity=identity,
        extractor=extractor, sesion=sesion,
        perfiles_saneados=perfiles, hechos_saneados=hechos,
        mensajes_podados=podados, segundos_precalentamiento=calentamiento,
    )


def cerrar(componentes: Componentes) -> None:
    """Cierre ordenado: drena la cola de memoria antes de soltar la base y la VRAM."""
    componentes.extractor.detener()
    componentes.db.cerrar()
    componentes.llm.cerrar()
    componentes.logger.info("Sesion finalizada")
