"""Control determinista del cierre de turno.

La regla 12 del system prompt prohibe las coletillas ("¿Te gustaria saber mas
sobre...?"), y en la bateria viva el modelo la ignoro en 34 de 60 turnos. Con un
8B el prompt no basta, asi que se anade una segunda capa, igual que con el
meta-texto de la memoria.

El recorte no se hace despues de imprimir: `RetenedorDeCola` retiene la ultima
frase del stream y decide si emitirla cuando la generacion termina. El visitante
solo percibe el retraso de una frase, y lo que se imprime es lo mismo que se
persiste y lo que leeria una capa de voz.
"""

from __future__ import annotations

import re

from .routing import normalizar

_TERMINADORES = ".!?"
# Las coletillas observadas en la bateria van de 39 a ~125 caracteres. Por encima
# de este limite se asume que la frase lleva contenido propio y se conserva.
_LONGITUD_MAXIMA_COLETILLA = 140

# Formulas de cierre vacio. Solo se descartan si la frase final ES una de estas,
# no si aparecen dentro de una respuesta con contenido.
_COLETILLAS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p) for p in (
        r"\bte\s+gustaria\s+(saber|conocer|escuchar)\s+mas\b",
        r"\bte\s+interesaria\s+(saber|conocer)\s+mas\b",
        r"\bquieres\s+(saber|conocer)\s+(algo\s+)?mas\b",
        r"\bte\s+gustaria\s+que\s+te\s+(cuente|hable)\b",
        r"\bhay\s+algo\s+mas\s+en\s+lo\s+que\s+pueda\s+ayudar",
        r"\balgo\s+mas\s+en\s+lo\s+que\s+te\s+pueda\s+ayudar",
        r"\ben\s+que\s+(mas\s+)?puedo\s+ayudarte\b",
        r"\bpuedo\s+ayudarte\s+con\s+algo\s+mas\b",
    )
)


def es_coletilla(frase: str) -> bool:
    """True si la frase es una invitacion generica sin contenido propio."""
    limpia = frase.strip()
    if not limpia or len(limpia) > _LONGITUD_MAXIMA_COLETILLA:
        return False
    if not limpia.endswith("?"):
        return False
    plano = normalizar(limpia)
    return any(patron.search(plano) for patron in _COLETILLAS)


class RetenedorDeCola:
    """Deja pasar el stream salvo la ultima frase, que se decide al cerrar."""

    def __init__(self) -> None:
        self._buffer = ""
        self._emitido_algo = False
        self.descartada = False

    def alimentar(self, fragmento: str) -> str:
        """Acumula el fragmento y devuelve el texto que ya es seguro emitir."""
        self._buffer += fragmento
        corte = self._corte(self._buffer)
        if corte <= 0:
            return ""
        listo, self._buffer = self._buffer[:corte], self._buffer[corte:]
        self._emitido_algo = self._emitido_algo or bool(listo.strip())
        return listo

    def cerrar(self) -> str:
        """Devuelve la ultima frase, o cadena vacia si resulto ser una coletilla."""
        cola, self._buffer = self._buffer, ""
        # Nunca se descarta si es lo unico que hay: el turno se quedaria mudo.
        if self._emitido_algo and cola.strip() and es_coletilla(cola):
            self.descartada = True
            return ""
        return cola

    @staticmethod
    def _corte(texto: str) -> int:
        """Posicion tras el ultimo terminador que aun tenga texto real detras.

        Se rescanea el buffer en cada fragmento a proposito. Una version
        incremental con estado resulto ser incorrecta con fragmentos grandes, y el
        coste real aqui es despreciable: unos cientos de miles de comparaciones de
        caracter frente a un segundo largo de inferencia en GPU.
        """
        fin = len(texto.rstrip())
        for i in range(fin - 1, -1, -1):
            if texto[i] in _TERMINADORES and texto[i + 1 : fin].strip():
                return i + 1
        return 0
