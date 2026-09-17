"""Ensamblaje del contexto enviado al modelo.

Todo lo que HACU debe saber se redacta en prosa natural. Las etiquetas entre
corchetes se eliminaron a proposito: el modelo las imitaba y acababa emitiendolas
en escena. Ademas, la consulta que llega al RAG es el texto literal del visitante,
sin prefijos de audiencia, para no contaminar el embedding de busqueda.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from .config import MemoryConfig, RagConfig
from .memory import HacuMemoryDB
from .prompts import MODO_TRIVIA, PERFILES_AUDIENCIA, SYSTEM_PROMPT_BASE
from .rag import LocalRAGEngine
from .routing import (
    FastRouter,
    Intencion,
    es_catalogo,
    es_despedida,
    es_seguimiento,
    es_sobre_el_centro,
    normalizar,
    pregunta_por_fuentes,
    pide_desarrollo,
)

Mensaje = dict[str, str]


class Profundidad(str, Enum):
    """Cuanto material hace falta traer para poder responder bien.

    Nace de una peticion concreta: con 32 proyectos, "¿cuales tienen?",
    "explicame cada uno" y "cuentame todo sobre Mary" necesitan cosas distintas.
    Traer siempre lo mismo hacia que la primera se quedara corta (solo salian
    cuatro proyectos) y la tercera superficial (una linea de la ficha).
    """

    CATALOGO = "CATALOGO"   # enumerar: basta el indice
    RESUMEN = "RESUMEN"     # un repaso de cada uno: indice + fichas
    DETALLE = "DETALLE"     # a fondo sobre uno: fichas
    CENTRO = "CENTRO"       # el centro en si: lo institucional


def profundidad_de(mensaje: str) -> Profundidad:
    """Clasifica la pregunta por cuanta anchura y cuanto fondo necesita."""
    catalogo = es_catalogo(mensaje)
    if catalogo and pide_desarrollo(mensaje):
        return Profundidad.RESUMEN
    if catalogo:
        return Profundidad.CATALOGO
    if es_sobre_el_centro(mensaje):
        return Profundidad.CENTRO
    return Profundidad.DETALLE

_TERMINOS_AMPLIOS: frozenset[str] = frozenset(
    "proyecto proyectos todos todas cuales cuantos listar lista enumera facultad facultades historia".split()
)

# Una pregunta hablada suele apoyarse en lo anterior sin nombrarlo. El historial
# viaja como mensajes previos, pero compite con varios miles de caracteres de
# documentacion recuperada que van justo antes de la pregunta. Repetir la ultima
# respuesta, recortada, pegada al comentario actual, le da el ancla que necesita.
_LONGITUD_ANCLA = 240
_PALABRAS_AUTOSUFICIENTE = 7


def _necesita_ancla(mensaje: str) -> bool:
    """True si el mensaje no se sostiene solo y hay que recordarle de que se hablaba."""
    return es_seguimiento(mensaje) or len(mensaje.split()) <= _PALABRAS_AUTOSUFICIENTE


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


# Subconjunto del anterior: el visitante pregunta por SU PROPIO nombre. Medido en
# el guion (G24): con el nombre delante en las notas, HACU respondia "Camila, no
# me mencionas un nombre que yo recuerde" — saludaba con el nombre y lo negaba en
# la misma frase. La regla 6 ya lo prohibe y el modelo la incumple: la pregunta
# directa activa el reflejo de no revelar las notas. Se resuelve con una
# instruccion explicita en el turno, no con mas texto en el system prompt.
_PATRONES_SU_NOMBRE: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p) for p in (
        r"\bcomo\s+me\s+llamo\b",
        r"\b(cual\s+es|sabes|recuerdas|dime)\s+mi\s+nombre\b",
        r"\bmi\s+nombre\s+(cual|es\s+cual)\b",
        r"\bquien\s+soy\b",
        r"\bte\s+acuerdas\s+de\s+(mi|como\s+me\s+llamo|mi\s+nombre)\b",
        r"\brecuerdas\s+(como\s+me\s+llamo|mi\s+nombre|quien\s+soy)\b",
        r"\bsabes\s+(como\s+me\s+llamo|quien\s+soy)\b",
    )
)


def _pregunta_su_nombre(mensaje: str) -> bool:
    """True si el visitante pregunta por su propio nombre."""
    plano = normalizar(mensaje)
    return any(patron.search(plano) for patron in _PATRONES_SU_NOMBRE)


# Encabezado del material recuperado. Deliberadamente SIN la palabra
# "documentacion": la nota decia "Recuperado de la documentacion institucional de
# la Universidad Simon Bolivar" y HACU la repetia palabra por palabra al hablar
# ("basada en la documentacion institucional de la Universidad Simon Bolivar").
# Es el mismo fallo que los ejemplos del prompt: lo que se le pone delante, lo
# copia. Si ahora copia "lo que sabes de AudacIA", suena a expositor y no a
# lector de fichas.
_FUENTES: dict[Intencion, str] = {
    Intencion.AUDACIA: "Lo que sabes de AudacIA",
    Intencion.UNIVERSIDAD: "Lo que sabes de la universidad",
}


def recuperar_de_audacia(rag, rag_config: RagConfig, mensaje: str,
                         consulta: str, n: int) -> str | None:
    """Elige que traer del corpus de AudacIA segun lo que pida la pregunta.

    - CATALOGO: solo el indice. Enumera los 32 proyectos de una vez.
    - RESUMEN: el indice como esqueleto mas unas fichas para dar cuerpo.
    - DETALLE: fichas, que es donde vive la explicacion de cada proyecto.
    - CENTRO: lo institucional (personas, patentes, sedes, reconocimientos), que
      de otro modo pierde siempre contra los treinta y siete fragmentos de fichas.

    Vive fuera de `ContextBuilder` para que la prueba de recuperacion mida este
    mismo camino y no una busqueda plana que el sistema ya no hace.
    """
    nivel = profundidad_de(mensaje)
    if nivel is Profundidad.DETALLE:
        return rag.buscar(Intencion.AUDACIA, consulta, n_results=n)
    if nivel is Profundidad.CENTRO:
        return rag.buscar(Intencion.AUDACIA, consulta,
                          n_results=rag_config.fragmentos_centro, tipo="institucional")

    indice = rag.cargar_indice(Intencion.AUDACIA)
    if nivel is Profundidad.CATALOGO:
        return indice
    fichas = rag.buscar(Intencion.AUDACIA, consulta,
                        n_results=rag_config.fragmentos_resumen, tipo="ficha")
    return "\n---\n".join(x for x in (indice, fichas) if x) or None


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
        usuario_anonimo: str = "visitante",
    ) -> None:
        self._db = db
        self._rag = rag
        self._rag_cfg = rag_config
        self._mem_cfg = memory_config
        self._anonimo = usuario_anonimo
        # Solo para saber si el mensaje nombra su propio tema. Es sin estado y
        # cuesta microsegundos; la sesion usa su propia instancia para enrutar.
        self._router = FastRouter()

    def build_messages(
        self, mensaje_usuario: str, intencion: Intencion, usuario_activo: str, estado: EstadoSesion
    ) -> list[Mensaje]:
        """Ensambla sistema + historial + turno enriquecido."""
        consulta = self._consulta_recuperacion(usuario_activo, mensaje_usuario)
        contexto, intencion_fuente = self._recuperar(mensaje_usuario, consulta, intencion)
        episodios = self._db.get_all_episodes(usuario_activo)

        notas: list[str] = [f"Hablas con {usuario_activo}."]
        ancla = self._ancla_conversacional(usuario_activo, mensaje_usuario)
        if ancla:
            notas.append(ancla)
        recordatorio = self._recordatorio_de_nombre(usuario_activo, mensaje_usuario)
        if recordatorio:
            notas.append(recordatorio)
        if pregunta_por_fuentes(mensaje_usuario):
            notas.append(
                "Te esta preguntando de donde sacas lo que dices. No tienes fuentes que "
                "citar y no puedes inventarte ninguna: nadie te ha contado nada, ni has "
                "hablado con investigadores ni con profesores. Di con naturalidad que es "
                "lo que sabes de la exhibicion, y si lo anterior no te consta, rectificalo."
            )
        if episodios:
            notas.append(
                "Lo que ya sabes de esta persona, de lo mas antiguo a lo mas reciente:\n"
                + "\n".join(f"- {e}" for e in episodios)
            )
        if contexto and intencion_fuente is not None:
            notas.append(f"{_FUENTES[intencion_fuente]}:\n{contexto}")

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

    def _ancla_conversacional(self, usuario: str, mensaje: str) -> str | None:
        """Recuerda lo ultimo que dijo HACU cuando la pregunta no se sostiene sola."""
        if not _necesita_ancla(mensaje):
            return None
        historial = self._db.get_recent_history(usuario, self._mem_cfg.history_messages)
        ultima = next(
            (m["content"] for m in reversed(historial) if m["role"] == "assistant"), None
        )
        if not ultima:
            return None
        recorte = ultima.strip()
        if len(recorte) > _LONGITUD_ANCLA:
            recorte = recorte[:_LONGITUD_ANCLA].rsplit(" ", 1)[0] + "..."
        return (
            "Su comentario se apoya en lo ultimo que le dijiste, que fue: "
            f'"{recorte}". Respondele sobre ESO, no cambies de tema.'
        )

    def _recordatorio_de_nombre(self, usuario: str, mensaje: str) -> str | None:
        """Instruccion explicita cuando preguntan por su propio nombre."""
        if not _pregunta_su_nombre(mensaje):
            return None
        if usuario == self._anonimo:
            return (
                "Te esta preguntando por su propio nombre y todavia no te lo ha dicho. "
                "Admitelo con naturalidad y pideselo."
            )
        return (
            f"Te esta preguntando por su propio nombre: se llama {usuario}. Diselo "
            "directamente, sin explicar de donde lo sabes y sin negar que lo recuerdas."
        )

    def _ultima_pregunta(self, usuario: str) -> str | None:
        """El ultimo mensaje del visitante, que es donde vive el tema de la conversacion."""
        historial = self._db.get_recent_history(usuario, self._mem_cfg.history_messages)
        return next((m["content"] for m in reversed(historial) if m["role"] == "user"), None)

    def _consulta_recuperacion(self, usuario: str, mensaje: str) -> str:
        """Consulta que va al RAG. Un seguimiento no nombra su tema: se lo presta el turno anterior.

        Medido en el guion (G02): tras "¿que sensor usa el Tanque?", la pregunta
        "¿y eso para que sirve exactamente?" no contiene una sola palabra de
        contenido. Embebida tal cual, recupero el fragmento generico de objetivos
        de AudacIA y HACU cambio de tema. Heredar el dominio no bastaba: acertaba
        el corpus y erraba el fragmento. Concatenar la pregunta anterior devuelve
        al embedding las palabras que el visitante ya no repite en voz alta.

        El criterio es mas estrecho que el del ancla: `_necesita_ancla` acepta
        tambien cualquier mensaje de siete palabras o menos, y eso vale para
        recordarle el tema en prosa pero no para la consulta. "¿Que sensores usa
        Holosand?" son seis palabras y se sostiene sola; concatenarle la pregunta
        anterior mezclaria dos proyectos en un mismo embedding. Aqui solo se
        expande cuando el mensaje contiene un giro que remite explicitamente a lo
        ya dicho Y no nombra ningun tema propio: "¿y eso de Holosand como va?"
        lleva giro pero se basta, y prestarle el turno anterior solo mezclaria dos
        proyectos en la misma consulta.
        """
        if not es_seguimiento(mensaje):
            return mensaje
        if self._router.clasificar(mensaje) is not Intencion.GENERAL:
            return mensaje
        anterior = self._ultima_pregunta(usuario)
        return f"{anterior} {mensaje}" if anterior else mensaje

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
        self, mensaje: str, consulta: str, intencion: Intencion
    ) -> tuple[str | None, Intencion | None]:
        """Devuelve (contexto, corpus del que salio). El corpus puede no ser la intencion.

        `mensaje` es lo que dijo el visitante y decide los guardarrailes; `consulta`
        es lo que se embebe, que en un seguimiento arrastra la pregunta anterior.
        """
        # Una despedida no necesita documentacion: darsela es invitar a rellenar.
        if es_despedida(mensaje):
            return None, None

        n = self._fragmentos(consulta, intencion)

        if intencion is Intencion.AUDACIA:
            contexto = self._recuperar_por_profundidad(mensaje, consulta, n)
            if contexto:
                return contexto, intencion

        if intencion is Intencion.GENERAL:
            # El router es lexico: una pregunta de seguimiento sin palabra clave
            # cae aqui. Se consulta igual y se acepta solo si esta cerca.
            # El filtro corre sobre las dos: la consulta expandida puede arrastrar
            # una pregunta personal del turno anterior que el mensaje ya no dice.
            if _pregunta_por_personas(mensaje) or _pregunta_por_personas(consulta):
                return None, None
            rescate = self._rag.buscar_relevante(
                consulta, n_results=n, umbral=self._rag_cfg.umbral_rescate_general
            )
            return rescate if rescate is not None else (None, None)

        return self._rag.buscar(intencion, consulta, n_results=n), intencion

    def _recuperar_por_profundidad(self, mensaje: str, consulta: str, n: int) -> str | None:
        return recuperar_de_audacia(self._rag, self._rag_cfg, mensaje, consulta, n)

    def _fragmentos(self, mensaje: str, intencion: Intencion) -> int:
        """Cuantos fragmentos recuperar segun la amplitud detectada en la pregunta."""
        if not _TERMINOS_AMPLIOS & set(normalizar(mensaje).split()):
            return self._rag_cfg.default_results
        if intencion is Intencion.UNIVERSIDAD:
            return self._rag_cfg.broad_results_universidad
        return self._rag_cfg.broad_results_audacia
