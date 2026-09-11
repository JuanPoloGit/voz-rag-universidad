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
