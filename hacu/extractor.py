"""Extraccion de memoria episodica en segundo plano.

Un unico hilo trabajador consume una cola acotada. Sustituye al patron anterior de
"un hilo nuevo por mensaje", que no acotaba la concurrencia y competia con la
generacion interactiva por el mismo contexto del modelo.

El pipeline de un hecho es: filtro barato -> LLM con salida JSON -> saneado
determinista -> deduplicacion -> persistencia. Cada etapa puede descartar; solo lo
que sobrevive a todas llega a la memoria del visitante.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass

from .config import MemoryConfig, ModelConfig
from .llm import LlmService
from .memory import HacuMemoryDB
from .prompts import PROMPT_CONSOLIDACION, PROMPT_EXTRACCION
from .sanitizer import FactSanitizer, primera_contradiccion


@dataclass(frozen=True)
class TareaExtraccion:
    """Unidad de trabajo encolada tras cada turno del visitante."""

    user_id: str
    mensaje: str


class BackgroundMemoryExtractor:
    """Deriva hechos permanentes del visitante sin penalizar la latencia de escena."""

    _CENTINELA = None

    def __init__(
        self,
        db: HacuMemoryDB,
        llm: LlmService,
        sanitizer: FactSanitizer,
        memory_config: MemoryConfig,
        model_config: ModelConfig,
        logger: logging.Logger,
    ) -> None:
        self._db = db
        self._llm = llm
        self._sanitizer = sanitizer
        self._mem_cfg = memory_config
        self._mod_cfg = model_config
        self._log = logger.getChild("extractor")
        self._cola: queue.Queue[TareaExtraccion | None] = queue.Queue(maxsize=32)
        self._hilo: threading.Thread | None = None

    # ----------------------------------------------------------- ciclo de vida

    def iniciar(self) -> None:
        if self._hilo is not None:
            return
        self._hilo = threading.Thread(target=self._bucle, name="hacu-memoria", daemon=True)
        self._hilo.start()
        self._log.debug("Trabajador de memoria iniciado")

    def detener(self, timeout: float = 5.0) -> None:
        """Drena la cola y termina el trabajador de forma ordenada."""
        if self._hilo is None:
            return
        try:
            self._cola.put_nowait(self._CENTINELA)
        except queue.Full:
            pass
        self._hilo.join(timeout=timeout)
        self._hilo = None
        self._log.debug("Trabajador de memoria detenido")

    def encolar(self, user_id: str, mensaje: str) -> None:
        """Encola el turno si aporta material; nunca bloquea el hilo de escena."""
        if not self._merece_analisis(mensaje):
            return
        try:
            self._cola.put_nowait(TareaExtraccion(user_id=user_id, mensaje=mensaje.strip()))
        except queue.Full:
            self._log.warning("Cola de memoria saturada; se descarta el turno")

    def esperar_vacio(self, timeout: float = 120.0) -> bool:
        """Bloquea hasta que la cola se drene. Solo para pruebas y cierres controlados."""
        limite = time.monotonic() + timeout
        while not self._cola.empty() and time.monotonic() < limite:
            time.sleep(0.05)
        self._cola.join()
        return self._cola.empty()

    @staticmethod
    def _merece_analisis(mensaje: str) -> bool:
        limpio = mensaje.strip()
        if len(limpio.split()) < 3 or limpio.isdigit():
            return False
        return "?" not in limpio and "¿" not in limpio

    # ---------------------------------------------------------------- trabajo

    def _bucle(self) -> None:
        while True:
            tarea = self._cola.get()
            try:
                if tarea is self._CENTINELA:
                    return
                self._procesar(tarea)
            except Exception:
                self._log.error("Fallo procesando la memoria episodica", exc_info=True)
            finally:
                self._cola.task_done()

    def _procesar(self, tarea: TareaExtraccion) -> None:
        hecho = self._extraer(tarea.mensaje)
        if hecho is None:
            return

        previos = self._db.get_all_episodes(tarea.user_id)
        if not self._db.add_episode(tarea.user_id, hecho):
            return
        self._log.info("Hecho registrado (%s): %s", tarea.user_id, hecho)

        # Consolidar por volumen deja convivir contradicciones hasta llegar al
        # umbral. Un hecho que niega a otro se resuelve en el momento.
        conflicto = primera_contradiccion(hecho, previos)
        if conflicto is not None:
            self._log.info("Contradiccion (%s): %r vs %r", tarea.user_id, hecho, conflicto)
        if conflicto is not None or self._db.count_episodes(tarea.user_id) >= self._mem_cfg.consolidation_threshold:
            self.consolidar(tarea.user_id)

    def _extraer(self, mensaje: str) -> str | None:
        """Pide un hecho al modelo y lo somete al saneador antes de aceptarlo."""
        datos = self._llm.completar_json(
            PROMPT_EXTRACCION.format(mensaje=mensaje.replace('"', "'")),
            max_tokens=self._mod_cfg.utility_max_tokens,
        )
        if not datos:
            return None
        if datos.get("sobre_el_visitante") is not True:
            self._log.debug("Extraccion descartada: el modelo no la atribuye al visitante")
            return None
        bruto = datos.get("hecho")
        if not isinstance(bruto, str):
            return None

        limpio = self._sanitizer.limpiar(bruto)
        if limpio is None:
            self._log.debug("Extraccion descartada por el saneador: %r", bruto[:120])
        return limpio

    def consolidar(self, user_id: str) -> None:
        """Reescribe el perfil resolviendo contradicciones y recortando al maximo configurado."""
        hechos = self._db.get_all_episodes(user_id)
        if len(hechos) < 2:
            return

        datos = self._llm.completar_json(
            PROMPT_CONSOLIDACION.format(
                maximo=self._mem_cfg.max_facts_per_profile,
                hechos="\n".join(f"{i}. {h}" for i, h in enumerate(hechos, 1)),
            ),
            max_tokens=self._mod_cfg.consolidation_max_tokens,
        )
        if not datos:
            self._recortar(user_id, hechos)
            return

        propuestos = datos.get("perfil")
        if not isinstance(propuestos, list):
            self._recortar(user_id, hechos)
            return

        limpios: list[str] = []
        vistos: set[str] = set()
        for candidato in propuestos:
            if not isinstance(candidato, str):
                continue
            hecho = self._sanitizer.limpiar(candidato)
            if hecho is None:
                continue
            clave = FactSanitizer.clave_dedup(hecho)
            if clave in vistos:
                continue
            vistos.add(clave)
            limpios.append(hecho)

        if not limpios:
            # La consolidacion no produjo nada valido: se conserva el perfil previo.
            self._log.warning("Consolidacion vacia para %s; se mantiene el perfil anterior", user_id)
            self._recortar(user_id, hechos)
            return

        self._db.overwrite_episodes(user_id, limpios[: self._mem_cfg.max_facts_per_profile])
        self._log.info("Perfil consolidado (%s): %d -> %d hechos", user_id, len(hechos), len(limpios))

    def _recortar(self, user_id: str, hechos: list[str]) -> None:
        """Red de seguridad: si la consolidacion falla, al menos se acota el crecimiento."""
        maximo = self._mem_cfg.max_facts_per_profile
        if len(hechos) > maximo:
            self._db.overwrite_episodes(user_id, hechos[-maximo:])
