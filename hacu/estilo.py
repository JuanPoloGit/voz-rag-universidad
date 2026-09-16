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

# Si tras recortar la coletilla lo emitido no llega a esto, la coletilla se queda:
# un turno reducido a "Hola, Salvador." corta la conversacion en seco.
_MINIMO_CONTENIDO_PREVIO = 60

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
        # Variantes vistas en el guion conversacional: la misma coletilla sin la
        # palabra "mas", que era lo unico que buscaban los patrones de arriba.
        r"\bte\s+gustaria\s+saber\s+algo\s+(mas|en\s+particular|en\s+especifico|concreto)",
        r"\bhay\s+algo\s+(en\s+particular|especifico|concreto)\s+que\s+(te|quieras|quieres)",
        r"\bquieres\s+que\s+te\s+(cuente|hable|explique)\s+(mas\s+)?(sobre|de|acerca)",
        r"\bsobre\s+cual(es)?\s+(te\s+gustaria|quieres|prefieres)",
        r"\bque\s+te\s+gustaria\s+saber\b",
    )
)


# Frases enteras de adulacion. La regla 10 las prohibe y el modelo las sigue
# produciendo en los saludos: 7 de 60 turnos en la ultima bateria abrian con
# "Me alegra que...". Se descartan solo cuando la frase COMPLETA es la formula,
# nunca cuando lleva contenido propio detras.
_ADULACION: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p) for p in (
        r"me alegra (que|saber|conocer|poder|mucho|verte|tenerte)",
        r"que (bueno|genial|interesante|alegria) (que|verte|tenerte)",
        r"(excelente|buena) pregunta",
        r"me encanta (que|tu pregunta)",
        r"es un placer (conocerte|tenerte|saludarte)",
        r"gracias por (preguntar|compartir|tu pregunta)",
    )
)
_LONGITUD_MAXIMA_ADULACION = 120

# Afirmaciones que no dicen nada y con las que el modelo abre cuando le piden
# algo largo: "¡Claro que sí, Juan!". No entran en la lista de arriba porque
# aquella exige que el patron empiece la frase y estas tienen que ocupar la frase
# ENTERA: "Claro que si: el Tanque es terrestre" lleva contenido y no se toca.
# El vocativo final es opcional porque casi siempre esta.
_AFIRMACION_VACIA = re.compile(
    r"^(claro(\s+que\s+si)?|por\s+supuesto(\s+que\s+si)?|desde\s+luego|como\s+no|"
    r"con\s+(mucho\s+)?gusto|sin\s+problema)"
    r"(\s*,\s*[a-z0-9áéíóúüñ-]+)?\s*[!.…]*$"
)
_SEPARADOR_FRASES = re.compile(r"(?<=[.!?])\s+")
# Vocativo opcional al inicio: "Sofia, me alegra que..." tambien es adulacion.
_VOCATIVO = re.compile(r"^[¡!¿?\s]*(?:[a-záéíóúüñ]+[ -]?){0,3}[,:]\s*")


def es_adulacion(frase: str) -> bool:
    """True si la frase entera es un cumplido o una afirmacion vacia."""
    limpia = frase.strip()
    if not limpia or len(limpia) > _LONGITUD_MAXIMA_ADULACION:
        return False
    crudo = normalizar(limpia).lstrip("¡!¿? ")
    if _AFIRMACION_VACIA.match(crudo):
        return True
    plano = _VOCATIVO.sub("", crudo)
    return any(patron.match(plano) for patron in _ADULACION)


def filtrar_adulacion(texto: str) -> tuple[str, int]:
    """Quita las frases que son puro cumplido. Devuelve (texto, frases quitadas)."""
    frases = [f for f in _SEPARADOR_FRASES.split(texto) if f.strip()]
    conservadas = [f.strip() for f in frases if not es_adulacion(f)]
    quitadas = len(frases) - len(conservadas)
    if not quitadas:
        return texto, 0
    if not conservadas:
        return "", quitadas
    prefijo = " " if texto[:1].isspace() else ""
    return prefijo + " ".join(conservadas), quitadas


# Fugas del andamiaje interno. La regla 5 las prohibe y el modelo las sigue
# produciendo ("no se menciona en las notas", "las facultades que menciono en las
# notas"). Se eliminan como giro, conservando el contenido de la frase: lo que
# sobra es la referencia al documento, no el dato.
_FUGAS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r",?\s*(?:que\s+)?(?:menciono|menciona|mencionan|aparecen?|figuran?|"
                r"se\s+mencionan?|se\s+menciona|tengo|hay)\s+en\s+(?:las|mis|estas)\s+notas\b",
                re.IGNORECASE), ""),
    (re.compile(r"\b(?:seg[uú]n|de\s+acuerdo\s+con|conforme\s+a)\s+(?:las|mis|estas)\s+notas\b,?\s*",
                re.IGNORECASE), ""),
    (re.compile(r"\ben\s+(?:las|mis|estas)\s+notas(?:\s+de\s+la\s+documentaci[oó]n[^,.]*)?,?\s*",
                re.IGNORECASE), ""),
    (re.compile(r"\b(?:las|mis|estas)\s+notas\b", re.IGNORECASE), "lo que sé"),
)
_ESPACIO_SOBRANTE = re.compile(r"\s{2,}")


def limpiar_fugas(texto: str) -> tuple[str, int]:
    """Quita las referencias al andamiaje interno. Devuelve (texto, fugas eliminadas)."""
    limpio, total = texto, 0
    for patron, reemplazo in _FUGAS:
        limpio, n = patron.subn(reemplazo, limpio)
        total += n
    if not total:
        return texto, 0
    limpio = _ESPACIO_SOBRANTE.sub(" ", limpio)
    limpio = re.sub(r"\s+([,.;:])", r"\1", limpio)
    # Una frase que empezaba por el giro eliminado arranca ahora en minuscula.
    limpio = re.sub(r"(^|[.!?]\s+)([a-záéíóúñ])",
                    lambda m: m.group(1) + m.group(2).upper(), limpio)
    return limpio, total


def ultima_frase(texto: str) -> str:
    """La ultima frase del texto. Es la unidad que deciden `es_coletilla` y el retenedor."""
    frases = [f for f in _SEPARADOR_FRASES.split(texto.strip()) if f.strip()]
    return frases[-1].strip() if frases else ""


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
        self._emitido = ""
        self.descartada = False
        self.truncada = False
        self.adulaciones_quitadas = 0
        self.fugas_limpiadas = 0

    def alimentar(self, fragmento: str) -> str:
        """Acumula el fragmento y devuelve el texto que ya es seguro emitir."""
        self._buffer += fragmento
        corte = self._corte(self._buffer)
        if corte <= 0:
            return ""
        listo, self._buffer = self._buffer[:corte], self._buffer[corte:]
        listo, quitadas = filtrar_adulacion(listo)
        self.adulaciones_quitadas += quitadas
        listo, fugas = limpiar_fugas(listo)
        self.fugas_limpiadas += fugas
        self._emitido = self._emitido + listo
        return listo

    def cerrar(self, incompleta: bool = False) -> str:
        """Devuelve la ultima frase, o cadena vacia si hay que descartarla.

        `incompleta` lo pone quien sabe que la generacion se corto por tope de
        tokens: lo retenido es entonces media frase ("...utiliza un sensor Kinect
        para escan") y soltarla es peor que no decir nada. Como el retenedor ya
        guardaba esa frase sin emitirla, basta con tirarla: el visitante ve la
        respuesta terminar en el ultimo punto de verdad.
        """
        cola, self._buffer = self._buffer, ""
        if incompleta and cola.strip() and self._emitido.strip():
            self.truncada = True
            return ""
        # Nunca se descarta si dejaria el turno mudo o reducido a un saludo.
        if len(self._emitido.strip()) >= _MINIMO_CONTENIDO_PREVIO and cola.strip() and es_coletilla(cola):
            self.descartada = True
            return ""
        # La cola tambien puede ser un cumplido suelto, si no deja el turno mudo.
        if self._emitido.strip():
            cola, quitadas = filtrar_adulacion(cola)
            self.adulaciones_quitadas += quitadas
        cola, fugas = limpiar_fugas(cola)
        self.fugas_limpiadas += fugas
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
