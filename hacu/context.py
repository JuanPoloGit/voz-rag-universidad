"""Ensamblaje del contexto enviado al modelo.

Todo lo que HACU debe saber se redacta en prosa natural. Las etiquetas entre
corchetes se eliminaron a proposito: el modelo las imitaba y acababa emitiendolas
en escena. Ademas, la consulta que llega al RAG es el texto literal del visitante,
sin prefijos de audiencia, para no contaminar el embedding de busqueda.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .config import MemoryConfig, RagConfig
from .memory import HacuMemoryDB
from .prompts import MODO_TRIVIA, PERFILES_AUDIENCIA, SYSTEM_PROMPT_BASE
from .rag import LocalRAGEngine
from .routing import Intencion, normalizar

Mensaje = dict[str, str]

_TERMINOS_AMPLIOS: frozenset[str] = frozenset(
    "proyecto proyectos todos todas cuales cuantos listar lista enumera facultad facultades historia".split()
)

# El rescate por distancia NO debe activarse en preguntas sobre las personas que
# pasan por la exhibicion. Medido: "dime quien estuvo aqui antes" queda a 0.7175 y
# "el visitante anterior" a 0.7424, mas cerca del corpus que seguimientos legitimos
# como "¿y eso donde queda?" (0.7600). Ningun umbral los separa, asi que se filtran
# por forma: sin este guardarrail, HACU presentaba al fundador de la universidad
# como si fuera el visitante anterior.
_PATRONES_SOBRE_PERSONAS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p) for p in (
        r"\bvisitantes?\s+(anterior|anteriores|previo|previos)",
        r"\botros?\s+visitantes?\b",
        r"\b(el|la|los|las|del|al)\s+(anterior|anteriores)\b",
        r"\bdatos\s+(personales|privados|de\s+contacto)",
        r"\binformacion\s+personal",
        r"\bquien\s+(estuvo|vino|hablo|paso|habia|mas\s+ha)",
        r"\bquien\s+soy\b",
        r"\bcomo\s+me\s+llamo\b",
        r"\bmi\s+nombre\b",
        r"\b(sobre|acerca\s+de|de)\s+mi\b",
        r"\bque\s+(sabes|recuerdas|tienes)\s+de\s+mi\b",
        r"\b(personas|estudiantes|gente|visitantes)\s+que\s+(hablaron|vinieron|estuvieron|pasaron)",
    )
)


def _pregunta_por_personas(mensaje: str) -> bool:
    """True si el mensaje indaga sobre visitantes o sus datos, no sobre la institucion."""
    plano = normalizar(mensaje)
    return any(patron.search(plano) for patron in _PATRONES_SOBRE_PERSONAS)


_FUENTES: dict[Intencion, str] = {
    Intencion.AUDACIA: "documentacion interna de AudacIA",
    Intencion.UNIVERSIDAD: "documentacion institucional de la Universidad Simon Bolivar",
}


@dataclass
class EstadoSesion:
    """Estado que controla el operador desde el panel de mandos."""

    perfil_audiencia: str = "General"
    trivia: bool = False


class ContextBuilder:
    """Construye la lista de mensajes final para llama.cpp."""

    def __init__(
        self,
        db: HacuMemoryDB,
        rag: LocalRAGEngine,
        rag_config: RagConfig,
        memory_config: MemoryConfig,
    ) -> None:
        self._db = db
        self._rag = rag
        self._rag_cfg = rag_config
        self._mem_cfg = memory_config

    def build_messages(
        self, mensaje_usuario: str, intencion: Intencion, usuario_activo: str, estado: EstadoSesion
    ) -> list[Mensaje]:
        """Ensambla sistema + historial + turno enriquecido."""
        contexto, intencion_fuente = self._recuperar(mensaje_usuario, intencion)
        episodios = self._db.get_all_episodes(usuario_activo)

        notas: list[str] = [f"Hablas con {usuario_activo}."]
        if episodios:
            notas.append(
                "Lo que ya sabes de esta persona, de lo mas antiguo a lo mas reciente:\n"
                + "\n".join(f"- {e}" for e in episodios)
            )
        if contexto and intencion_fuente is not None:
            notas.append(f"Recuperado de la {_FUENTES[intencion_fuente]}:\n{contexto}")

        partes: list[str] = [
            "NOTAS PRIVADAS PARA TI (no las menciones, no las cites, no las repitas):",
            "\n\n".join(notas),
            "FIN DE LAS NOTAS PRIVADAS.",
            f"Comentario del visitante: {mensaje_usuario}",
        ]

        historial: list[Mensaje] = [{"role": "system", "content": self._sistema(estado)}]
        historial.extend(self._db.get_recent_history(usuario_activo, self._mem_cfg.history_messages))
        historial.append({"role": "user", "content": "\n\n".join(partes)})
        return historial

    # ---------------------------------------------------------------- internos

    def _sistema(self, estado: EstadoSesion) -> str:
        """Prompt de sistema con la directriz de audiencia y, si aplica, el modo trivia."""
        bloques: list[str] = [SYSTEM_PROMPT_BASE]
        directriz = PERFILES_AUDIENCIA.get(estado.perfil_audiencia)
        if directriz:
            bloques.append(f"AUDIENCIA ACTUAL: {directriz}")
        if estado.trivia:
            bloques.append(MODO_TRIVIA)
        return "\n\n".join(bloques)

    def _recuperar(
        self, mensaje: str, intencion: Intencion
    ) -> tuple[str | None, Intencion | None]:
        """Devuelve (contexto, corpus del que salio). El corpus puede no ser la intencion."""
        n = self._fragmentos(mensaje, intencion)

        if intencion is Intencion.GENERAL:
            # El router es lexico: una pregunta de seguimiento sin palabra clave
            # cae aqui. Se consulta igual y se acepta solo si esta cerca.
            if _pregunta_por_personas(mensaje):
                return None, None
            rescate = self._rag.buscar_relevante(
                mensaje, n_results=n, umbral=self._rag_cfg.umbral_rescate_general
            )
            return rescate if rescate is not None else (None, None)

        return self._rag.buscar(intencion, mensaje, n_results=n), intencion

    def _fragmentos(self, mensaje: str, intencion: Intencion) -> int:
        """Cuantos fragmentos recuperar segun la amplitud detectada en la pregunta."""
        if not _TERMINOS_AMPLIOS & set(normalizar(mensaje).split()):
            return self._rag_cfg.default_results
        if intencion is Intencion.UNIVERSIDAD:
            return self._rag_cfg.broad_results_universidad
        return self._rag_cfg.broad_results_audacia
