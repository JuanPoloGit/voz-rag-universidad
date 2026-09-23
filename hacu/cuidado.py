"""Lo que HACU hace cuando el visitante no viene a preguntar por proyectos.

En la primera prueba con una persona en crisis, HACU hizo casi todo mal: se rio
("¡Ahah, no te preocupes!") de quien decia no saber quien era, respondio a
"perdi a mis papas, me siento solo" ofreciendo la historia de la universidad e
inventandose un proyecto llamado "La Casa de la Cultura", y ante un "me voy a
suicidar" solto un "no puedo continuar con la conversacion de esta manera" —que
suena a puerta en la cara— seguido de un ofrecimiento de recursos de ayuda **en
Venezuela**, estando el montaje en Barranquilla.

Cada uno de esos fallos es el mismo de siempre —adulacion, pivote al catalogo,
alucinacion— pero aqui el precio es otro. Por eso esta capa NO es una regla mas
del prompt: un modelo de 8B lleva ignorandolas todo el proyecto, y este es el
unico sitio donde eso no se puede permitir.

Dos niveles:

- CRISIS: mencion de suicidio o de hacerse dano. La respuesta es un texto fijo,
  escrito una vez y revisable, que NO pasa por el modelo. Ni una palabra
  improvisada, ni un numero de telefono inventado.
- MALESTAR: tristeza, soledad, ansiedad declaradas en primera persona. Sigue
  respondiendo el modelo, pero con la instruccion explicita de reconocer lo que
  le han contado y de NO reconducir a la exhibicion.

Los recursos de ayuda concretos (telefonos, Bienestar Universitario) NO viven
aqui: se configuran. Inventar un numero de crisis es peor que no dar ninguno.
"""

from __future__ import annotations

import re
from enum import Enum

from .routing import normalizar


class Cuidado(str, Enum):
    """Que clase de atencion necesita este mensaje."""

    NINGUNO = "NINGUNO"
    MALESTAR = "MALESTAR"   # lo pasa mal y lo cuenta
    CRISIS = "CRISIS"       # habla de suicidio o de hacerse dano


# Giros en los que "morir" o "matar" no significan lo que parece. Van primero:
# "me muero de hambre" y "matar el tiempo" son de uso diario en Barranquilla y no
# pueden disparar nada.
_FIGURADO: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p) for p in (
        r"\bme\s+(muero|moria|morire)\s+de\s+(hambre|risa|frio|calor|sue[nñ]o|"
        r"ganas|amor|verguenza|aburrimiento|curiosidad|pena)\b",
        r"\bmatar\s+el\s+tiempo\b",
        r"\bme\s+mata\s+(la\s+)?(risa|curiosidad)\b",
        r"\bmuriendo(me)?\s+de\s+(risa|hambre|ganas)\b",
        r"\bde\s+muerte\b",
    )
)

_CRISIS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p) for p in (
        r"\bsuicid",
        r"\bquitarme\s+la\s+vida\b",
        r"\b(acabar|terminar)\s+con\s+(mi\s+vida|todo)\b",
        r"\bno\s+quiero\s+(vivir|seguir\s+vivi|seguir\s+aqui|existir)",
        r"\b(me\s+)?quiero\s+morir",
        r"\bquiero\s+(morirme|desaparecer)\b",
        r"\bmatarme\b",
        r"\b(hacerme|hacer\s+me)\s+da[nñ]o\b",
        r"\b(cortarme|lastimarme)\b",
        r"\bmejor\s+(estar\s+)?muerto\b",
        r"\bno\s+le\s+veo\s+sentido\s+a\s+(la\s+)?vida\b",
        r"\bya\s+no\s+aguanto\s+(mas|nada)\b",
    )
)

# Malestar declarado en primera persona. La primera persona es lo que separa
# "estoy muy triste" de "¿Mary detecta la depresion?", que es una pregunta sobre
# un proyecto y tiene que seguir contestandose como tal.
_PRIMERA_PERSONA = re.compile(
    r"\b(me\s+siento|estoy|tengo|sufro|padezco|no\s+tengo|perdi|me\s+quede|"
    r"me\s+sacaron|me\s+dejo|necesito|no\s+puedo\s+mas|llevo)\b"
)
_MALESTAR: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p) for p in (
        r"\bdepresi[oó]n\b", r"\bdeprimid",
        r"\bansiedad\b", r"\bangustia\b", r"\bataque\s+de\s+panico\b",
        r"\b(muy\s+)?(triste|solo|sola|vacio|vacia|perdido|perdida|mal)\b",
        r"\bno\s+tengo\s+a\s+nadie\b",
        r"\bnadie\s+me\s+(quiere|escucha|entiende)\b",
        r"\bnecesito\s+(hablar|alguien|ayuda)\b",
        r"\bllorar|llorando\b",
        r"\bno\s+puedo\s+mas\b",
    )
)


def evaluar(mensaje: str) -> Cuidado:
    """Clasifica el mensaje del visitante. Ante la duda, tira hacia arriba."""
    plano = normalizar(mensaje)
    if not plano.strip():
        return Cuidado.NINGUNO
    sin_figurado = plano
    for patron in _FIGURADO:
        sin_figurado = patron.sub(" ", sin_figurado)
    if any(p.search(sin_figurado) for p in _CRISIS):
        return Cuidado.CRISIS
    if _PRIMERA_PERSONA.search(plano) and any(p.search(plano) for p in _MALESTAR):
        return Cuidado.MALESTAR
    return Cuidado.NINGUNO


# Texto fijo de la respuesta de crisis. Se escribe aqui, entero y revisable, y no
# lo genera el modelo. Lo que NO hace, y en la prueba real hizo:
#   - no dice "no puedo continuar con la conversacion" (suena a puerta en la cara)
#   - no ofrece proyectos ni la historia de la universidad
#   - no da ningun telefono que no le hayan configurado
#   - no diagnostica, no promete y no moraliza
RESPUESTA_CRISIS: str = (
    "Espera un momento. Lo que acabas de decirme importa mucho más que cualquier "
    "cosa de esta exhibición.\n\n"
    "Yo soy una máquina de un montaje universitario, y esto no puedo acompañarlo "
    "como mereces. Pero aquí al lado hay personas que sí: habla con quien está "
    "atendiendo este stand. Está para eso y te va a escuchar.\n\n"
    "Si prefieres hablar con alguien de fuera del montaje, díselo y te ayudan a "
    "ponerte en contacto. Pedir ayuda hoy es lo más sensato que puedes hacer."
)

# Aviso para el operador. Va a la pantalla del panel y al log: la persona que
# atiende el stand tiene que enterarse en ese momento, no al leer el log por la
# noche.
AVISO_OPERADOR: str = (
    "Atención: el visitante ha dicho algo que necesita una persona, no un robot. "
    "HACU ha respondido con el texto fijo de cuidado y no ha seguido con la "
    "exhibición. Acércate."
)

NOTA_MALESTAR: str = (
    "Acaba de contarte algo que le duele, en primera persona. Esto NO es una "
    "pregunta sobre la exhibicion y no la trates como tal: no le ofrezcas "
    "proyectos, ni la historia de la universidad, ni le cambies de tema para "
    "volver a lo tuyo. Reconoce en una o dos frases lo que te ha dicho, con "
    "calidez y sin dramatizar, y dile que si quiere hablarlo con alguien, ahi al "
    "lado esta la persona que atiende el montaje. No des consejos de salud, no "
    "diagnostiques, no prometas nada y no te rias."
)


def respuesta_de_crisis(recursos: str = "") -> str:
    """El texto fijo, con los recursos locales si el centro los ha configurado."""
    limpio = (recursos or "").strip()
    return f"{RESPUESTA_CRISIS}\n\n{limpio}" if limpio else RESPUESTA_CRISIS
