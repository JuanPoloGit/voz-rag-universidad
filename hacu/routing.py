"""Clasificador de intencion de latencia despreciable.

Sustituye una llamada al LLM por dos barridos de expresion regular sobre el texto
normalizado (~10 microsegundos). Ante coincidencias en ambos dominios gana el que
mas terminos aporta, y el empate se resuelve a favor de AudacIA, que es el tema
de la exhibicion.
"""

from __future__ import annotations

import re
import unicodedata
from enum import Enum


class Intencion(str, Enum):
    """Dominio de conocimiento al que apunta la pregunta del visitante."""

    AUDACIA = "AUDACIA"
    UNIVERSIDAD = "UNIVERSIDAD"
    GENERAL = "GENERAL"


def normalizar(texto: str) -> str:
    """Minusculas sin tildes ni diacriticos, para que el patron no dependa del acento."""
    descompuesto = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in descompuesto if unicodedata.category(c) != "Mn")


class FastRouter:
    """Enruta la consulta al corpus correcto sin coste de inferencia."""

    _PATRON_AUDACIA = re.compile(
        r"\b(audacia|tanque|orion|holosand|robotica|robot|vision artificial|sensor(?:es)?|"
        r"cuda|machine learning|deep learning|ia|inteligencia artificial|prototipo|semillero)\b"
    )
    _PATRON_UNIVERSIDAD = re.compile(
        r"\b(universidad|unisimon|simon bolivar|rector|rectora|facultad(?:es)?|campus|historia|"
        r"institucional|carrera(?:s)?|pregrado|posgrado|programa academico|sede)\b"
    )

    def clasificar(self, texto: str) -> Intencion:
        """Devuelve el dominio dominante del mensaje."""
        plano = normalizar(texto)
        puntaje_audacia = len(self._PATRON_AUDACIA.findall(plano))
        puntaje_universidad = len(self._PATRON_UNIVERSIDAD.findall(plano))

        if puntaje_audacia == 0 and puntaje_universidad == 0:
            return Intencion.GENERAL
        if puntaje_universidad > puntaje_audacia:
            return Intencion.UNIVERSIDAD
        return Intencion.AUDACIA


# Giros que se refieren explicitamente a algo ya mencionado. Cuando el router no
# reconoce dominio pero el visitante dice "explicame mas a detalle cada uno", la
# pregunta pertenece al corpus del turno anterior. Sin esto, el rescate por
# distancia elegia a ciegas: en una prueba real mando una peticion de detalle
# sobre los proyectos de AudacIA al corpus institucional y respondio con
# facultades y directivas.
#
# La lista es deliberadamente estrecha. Un marcador de mas alcance haria heredar
# tambien preguntas personales ("y hay algo que te guste?"), que deben seguir
# resolviendose por distancia o quedarse sin contexto.
_GIROS_SEGUIMIENTO: tuple[str, ...] = (
    "cada uno", "cada una", "de esos", "de esas", "de eso", "sobre eso", "sobre ese",
    "ese proyecto", "esos proyectos", "el primero", "el segundo", "el ultimo",
    "mas detalle", "mas a detalle", "en detalle", "mas sobre eso", "cuentame mas",
    "explicame mas", "amplia", "profundiza", "y el otro", "los demas",
    # Preguntas que en una conversacion hablada son constantes y no nombran su
    # tema: sin el turno anterior no significan nada.
    "por que es asi", "por que es", "por que eso", "como es eso", "como asi",
    "a que te refieres", "y eso", "que quieres decir", "como funciona eso",
    "para que sirve eso", "y entonces",
)


def es_seguimiento(texto: str) -> bool:
    """True si el mensaje se apoya en lo dicho antes en vez de nombrar su tema."""
    plano = normalizar(texto)
    if len(plano.split()) > 16:
        return False
    return any(giro in plano for giro in _GIROS_SEGUIMIENTO)


# Peticiones que piden desarrollo de verdad y no una respuesta de tarima. Son
# deliberadamente pocas y explicitas: un marcador flojo ("explicame") aparece en
# cualquier pregunta corta y subiria el techo de generacion siempre, que es justo
# lo que la regla 3 intenta evitar.
_PETICIONES_DESARROLLO: tuple[str, ...] = (
    "detallad", "en detalle", "con detalle", "paso a paso", "en profundidad",
    "profundiza", "extiendete", "uno por uno", "ampliamente",
    "cada proyecto", "cada uno de los proyectos", "todos los proyectos",
    "todos y cada", "explicame cada", "explicame todos", "cuentame todo",
    "describeme todos", "habla de todos", "cuales son todos",
)


def pide_desarrollo(texto: str) -> bool:
    """True si la pregunta no se puede contestar honestamente en cuatro frases."""
    plano = normalizar(texto)
    return any(marca in plano for marca in _PETICIONES_DESARROLLO)
