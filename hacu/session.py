"""Orquestacion de un turno de conversacion.

Separa la logica del turno de la capa de presentacion. La consola de exhibicion y
el harness de pruebas ejecutan exactamente este mismo camino, de modo que lo que
se mide en las pruebas es lo que ocurre en escena.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from .context import ContextBuilder, EstadoSesion
from .estilo import RetenedorDeCola
from .extractor import BackgroundMemoryExtractor
from .identity import IdentityResolver
from .llm import LlmService
from .memory import HacuMemoryDB
from .routing import FastRouter, Intencion


@dataclass(frozen=True)
class ResultadoTurno:
    """Traza completa de un turno, apta para consola o para informe de pruebas."""

    entrada: str
    respuesta: str
    intencion: Intencion
    usuario: str
    usuario_anterior: str
    migrado: bool
    tokens: int
    segundos: float
    coletilla_descartada: bool = False

    @property
    def tokens_por_segundo(self) -> float:
        return self.tokens / self.segundos if self.segundos > 0 else 0.0


class HacuSession:
    """Un visitante frente a HACU: identidad, enrutado, generacion y persistencia."""

    def __init__(
        self,
        llm: LlmService,
        db: HacuMemoryDB,
        router: FastRouter,
        identity: IdentityResolver,
        extractor: BackgroundMemoryExtractor,
        context_builder: ContextBuilder,
        logger: logging.Logger,
    ) -> None:
        self._llm = llm
        self._db = db
        self._router = router
        self._identity = identity
        self._extractor = extractor
        self._context = context_builder
        self._log = logger.getChild("sesion")
        self.estado = EstadoSesion()

    @property
    def usuario_activo(self) -> str:
        return self._identity.usuario_activo

    @property
    def identidad(self) -> IdentityResolver:
        return self._identity

    def clasificar(self, texto: str) -> tuple[Intencion, float]:
        """Enruta el texto y devuelve (intencion, segundos empleados)."""
        inicio = time.perf_counter()
        intencion = Intencion.UNIVERSIDAD if self.estado.trivia else self._router.clasificar(texto)
        return intencion, time.perf_counter() - inicio

    def turno(
        self,
        texto: str,
        on_token: Callable[[str], None] | None = None,
        intencion: Intencion | None = None,
    ) -> ResultadoTurno:
        """Procesa un mensaje completo del visitante y devuelve su traza."""
        evento = self._identity.procesar(texto)
        migrado = False
        if evento.requiere_migracion:
            self._db.migrate_profile(evento.usuario_anterior, evento.usuario_actual)
            migrado = True

        if intencion is None:
            intencion, _ = self.clasificar(texto)

        usuario = self._identity.usuario_activo
        mensajes = self._context.build_messages(texto, intencion, usuario, self.estado)

        inicio = time.perf_counter()
        emitido: list[str] = []
        retenedor = RetenedorDeCola()
        tokens = 0

        def emitir(texto: str) -> None:
            if not texto:
                return
            emitido.append(texto)
            if on_token is not None:
                on_token(texto)

        for fragmento in self._llm.stream_chat(mensajes):
            tokens += 1
            emitir(retenedor.alimentar(fragmento))
        emitir(retenedor.cerrar())
        segundos = time.perf_counter() - inicio

        respuesta = "".join(emitido).strip()
        if retenedor.descartada:
            self._log.debug("Coletilla de cierre descartada (regla 12)")
        self._db.add_message(usuario, "user", texto)
        self._db.add_message(usuario, "assistant", respuesta)
        self._extractor.encolar(usuario, texto)

        return ResultadoTurno(
            entrada=texto,
            respuesta=respuesta,
            intencion=intencion,
            usuario=usuario,
            usuario_anterior=evento.usuario_anterior,
            migrado=migrado,
            tokens=tokens,
            segundos=segundos,
            coletilla_descartada=retenedor.descartada,
        )
