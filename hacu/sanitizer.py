"""Saneado determinista de hechos episodicos.

Segunda linea de defensa contra la fuga de meta-texto. La primera es la salida
JSON forzada del modelo; esta capa no confia en ella y valida forma, longitud,
persona gramatical y ausencia de marcadores de razonamiento antes de que un hecho
llegue a SQLite. Es deterministica a proposito: no cuesta inferencia y no falla
de forma distinta segun la temperatura del modelo.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

# Frases con las que el LLM narra su propio trabajo. Si aparecen, la salida es
# razonamiento y no un hecho del visitante.
_MARCADORES_META: tuple[str, ...] = (
    "perfil final", "aqui te presento", "aqui esta", "he eliminado", "he borrado", "he quitado",
    "he mantenido", "he resuelto", "he analizado", "basado en", "segun el mensaje", "segun la lista",
    "lista de datos", "los datos que", "que me proporcionaste", "no hay suficiente",
    "no se proporciona", "no se menciona", "no aporta", "no es un hecho", "hecho extraido",
    "el mensaje", "el usuario menciona", "el usuario dice", "como asistente", "como modelo",
    "inteligencia artificial", "en resumen", "nota:", "explicacion", "respetando el dato",
    "contradiccion", "duplicad", "json", "null", "ninguno",
    "estas hablando con", "proporcionarme", "no proporcionad", "no especificad",
    "listado extenso", "nombre del usuario", "perfil actualizado",
    "es importante destacar", "puede no ser completo", "cabe aclarar", "ten en cuenta que",
    "hacu", "asistente expositor", "asistente de cocina", "te llamas", "sobre_el_visitante",
    # Definiciones genericas: son conocimiento del mundo, no datos del visitante.
    "se refiere a", "se define como", "significa que", "consiste en", "es un concepto",
    "es la capacidad",
)

# Un hecho se registra en tercera persona; la primera persona indica que la
# extraccion fallo y copio el mensaje del visitante tal cual.
_MARCADORES_PRIMERA_PERSONA: tuple[str, ...] = (
    "soy ", "estoy ", "tengo ", "quiero ", "me gusta", "me gustan", "me encanta", "me llamo",
    "mi nombre", "yo ", "nosotros ", "mi ", "conmigo",
)

# Contenido que no debe quedar persistido como "hecho" de un visitante en una
# exhibicion abierta al publico, por muy bien formada que este la oracion.
_TERMINOS_INADMISIBLES: tuple[str, ...] = (
    "racista", "homofob", "machista", "xenofob", "misogin", "nazi", "fascista",
    "terrorista", "violador", "asesino", "ladron", "criminal", "delincuente",
    "puto", "puta", "marica", "maricon", "perra", "zorra", "cabron", "pendejo",
    "gonorrea", "malparido", "hijueputa", "idiota", "imbecil", "estupido",
)

_PREFIJOS_BASURA = re.compile(r"^\s*(?:[-*•·]+|\d+[.)])\s*")
# Forma "Etiqueta: valor": el modelo devolviendo una ficha de campos, no un hecho.
_PATRON_ETIQUETA = re.compile(r"^[^:]{2,40}:\s")

# Salidas literales de los ejemplos del prompt de extraccion. El modelo las copia
# cuando no encuentra nada que extraer, y acaban archivadas como hechos reales.
# Se mantienen en un dominio ajeno a la exhibicion para que copiarlas sea detectable.
_EJEMPLOS_DEL_PROMPT: frozenset[str] = frozenset({
    "es panadero y vive en cartagena",
    "ya no le interesa el ajedrez",
})

# Formas que nunca son un dato permanente del visitante, ancladas al sujeto de la
# oracion: hechos sobre terceros, sobre la conversacion, o instrucciones coladas.
_PATRONES_RECHAZO: tuple[re.Pattern[str], ...] = (
    re.compile(r"^(se llama|su nombre es|puede llamarse|se hace llamar)\b"),
    re.compile(r"^(audacia|la universidad|el proyecto|la facultad|el robot|el tanque|orion|holosand)\b"),
    re.compile(r"^(nunca|no)\s+(dijo|menciono|afirmo|conto|quiere que)\b"),
    re.compile(r"^a partir de ahora\b"),
    re.compile(r"^fue fundad"),
    re.compile(r"^(no\s+)?tiene\s+proyectos?\b"),
)
_ESPACIOS = re.compile(r"\s+")


def _plano(texto: str) -> str:
    descompuesto = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in descompuesto if unicodedata.category(c) != "Mn")


@dataclass(frozen=True)
class LimitesHecho:
    """Umbrales de forma de un hecho episodico valido."""

    min_caracteres: int = 10
    max_caracteres: int = 160
    min_palabras: int = 3
    max_palabras: int = 22


class FactSanitizer:
    """Normaliza y valida hechos; devuelve None cuando el texto no es un hecho."""

    def __init__(self, limites: LimitesHecho | None = None) -> None:
        self._lim = limites or LimitesHecho()

    def limpiar(self, texto: str | None) -> str | None:
        """Devuelve el hecho normalizado o None si debe descartarse."""
        if not texto:
            return None

        hecho = _PREFIJOS_BASURA.sub("", texto.strip())
        hecho = hecho.strip(" \t\n\"'`*_")
        hecho = _ESPACIOS.sub(" ", hecho).strip()
        if not hecho:
            return None

        # Un texto que termina en dos puntos es un encabezado, no un hecho;
        # uno con forma "Etiqueta: valor" es una ficha de campos.
        if hecho.endswith(":") or _PATRON_ETIQUETA.match(hecho):
            return None

        plano = _plano(hecho)
        if any(marcador in plano for marcador in _MARCADORES_META):
            return None
        if any(plano.startswith(marcador) for marcador in _MARCADORES_PRIMERA_PERSONA):
            return None
        if any(termino in plano for termino in _TERMINOS_INADMISIBLES):
            return None
        if any(patron.match(plano) for patron in _PATRONES_RECHAZO):
            return None
        if self.clave_dedup(hecho) in _EJEMPLOS_DEL_PROMPT:
            return None
        if not any(c.isalpha() for c in hecho):
            return None

        palabras = hecho.split()
        if not (self._lim.min_palabras <= len(palabras) <= self._lim.max_palabras):
            return None
        if not (self._lim.min_caracteres <= len(hecho) <= self._lim.max_caracteres):
            return None

        if not hecho.endswith((".", "!", "?")):
            hecho += "."
        return hecho[0].upper() + hecho[1:]

    @staticmethod
    def clave_dedup(hecho: str) -> str:
        """Clave normalizada para detectar hechos equivalentes sin distinguir tildes ni puntuacion."""
        plano = _plano(hecho)
        return _ESPACIOS.sub(" ", re.sub(r"[^a-z0-9 ]", "", plano)).strip()


# --------------------------------------------------------------------- contradicciones

# Palabras sin carga semantica: no cuentan para medir el solapamiento entre hechos.
_VACIAS: frozenset[str] = frozenset(
    """el la los las un una unos unas de del al a en con por para y o u ni que se su sus
    lo le les es son era eran esta estan ha han hace desde como mas muy tambien pero
    todo toda todos todas este esta esto ese esa eso aqui alli alla""".split()
)
_MARCAS_NEGACION: tuple[str, ...] = ("no ", "ya no ", "nunca ", "tampoco ", "dejo de ")


def _contenido(hecho: str) -> set[str]:
    """Palabras con carga semantica del hecho, normalizadas."""
    limpio = re.sub(r"[^a-z0-9 ]", " ", _plano(hecho))
    return {p for p in limpio.split() if len(p) > 2 and p not in _VACIAS}


def _niega(hecho: str) -> bool:
    plano = " " + _plano(hecho)
    return any(f" {marca}" in plano for marca in _MARCAS_NEGACION)


def contradice(nuevo: str, existente: str) -> bool:
    """True si ambos hechos hablan de lo mismo y uno niega lo que el otro afirma.

    Heuristica deliberadamente estricta: exige solapamiento alto de contenido
    (coeficiente de solapamiento >= 0.4) Y polaridad distinta. Su proposito no es
    resolver la contradiccion, sino disparar la consolidacion, que si la resuelve.
    """
    a, b = _contenido(nuevo), _contenido(existente)
    if not a or not b:
        return False
    if _niega(nuevo) == _niega(existente):
        return False
    return len(a & b) / min(len(a), len(b)) >= 0.4


def primera_contradiccion(nuevo: str, existentes: Iterable[str]) -> str | None:
    """Devuelve el primer hecho existente que el nuevo contradice, si lo hay."""
    return next((h for h in existentes if contradice(nuevo, h)), None)
