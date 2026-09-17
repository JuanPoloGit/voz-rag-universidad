"""Clasificador de intencion de latencia despreciable.

Sustituye una llamada al LLM por dos barridos de expresion regular sobre el texto
normalizado (~10 microsegundos). Ante coincidencias en ambos dominios gana el que
mas terminos aporta, y el empate se resuelve a favor de AudacIA, que es el tema
de la exhibicion.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
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
        r"cuda|machine learning|deep learning|ia|inteligencia artificial|prototipo(?:s)?|semillero|"
        # "proyecto" faltaba, y es la palabra mas dicha en la exhibicion:
        # "enumerame los proyectos de salud" caia en GENERAL, se saltaba la
        # politica de catalogo y respondia con tres de ocho proyectos.
        r"proyecto(?:s)?|centro de investigacion|exhibicion)\b"
    )
    _PATRON_UNIVERSIDAD = re.compile(
        r"\b(universidad|unisimon|simon bolivar|rector|rectora|facultad(?:es)?|campus|historia|"
        r"institucional|carrera(?:s)?|pregrado|posgrado|programa academico|sede)\b"
    )

    def __init__(self) -> None:
        # Nombres de proyecto aprendidos del indice del corpus. No se escriben
        # aqui a proposito: serian treinta y dos nombres duplicados que habria
        # que mantener a mano cada vez que cambia la documentacion.
        self._patron_nombres: re.Pattern[str] | None = None

    def aprender_nombres(self, nombres: Iterable[str]) -> int:
        """Ensena al router los nombres de proyecto que hay en el corpus.

        Sin esto, "¿que es Mary?" o "cuentame de Patrii" caian en GENERAL y se
        saltaban la politica de profundidad: el router solo conocia palabras
        genericas y ninguno de los nombres propios de la exhibicion.
        """
        limpios = sorted({normalizar(n).strip() for n in nombres if len(n.strip()) > 2},
                         key=len, reverse=True)
        if not limpios:
            self._patron_nombres = None
            return 0
        self._patron_nombres = re.compile(
            r"(?<![a-z0-9])(?:" + "|".join(re.escape(n) for n in limpios) + r")(?![a-z0-9])"
        )
        return len(limpios)

    def clasificar(self, texto: str) -> Intencion:
        """Devuelve el dominio dominante del mensaje."""
        plano = normalizar(texto)
        puntaje_audacia = len(self._PATRON_AUDACIA.findall(plano))
        if self._patron_nombres is not None:
            puntaje_audacia += len(self._patron_nombres.findall(plano))
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


# Preguntas sobre HACU mismo. Aunque empiecen por "y", no continuan el tema: "¿y
# hay algo que te guste?" no pide mas sobre el ultimo proyecto, cambia de asunto.
# Heredar el dominio aqui le enchufaria fragmentos de proyectos a una pregunta
# personal, que es justo lo que la lista estrecha de giros evitaba.
_SOBRE_HACU: tuple[str, ...] = (
    "te gusta", "te guste", "te gustan", "prefieres", "te interesa", "te apasiona",
    "tu que", "y tu", "eres", "sientes", "piensas", "opinas", "crees", "sueñas",
    "te llamas", "quien eres", "que eres",
)


def es_seguimiento(texto: str) -> bool:
    """True si el mensaje se apoya en lo dicho antes en vez de nombrar su tema.

    Una pregunta que ARRANCA con "y" es continuacion por definicion: en habla,
    "¿y que animales salen en la arena?" no abre tema, sigue el anterior. Faltaba,
    y costo caro: esa pregunta caia en GENERAL, el rescate no encontraba la ficha
    de Holosand y HACU, al que le preguntaban por animales teniendo solo "fauna
    interactiva", se invento tortugas y aves.
    """
    plano = normalizar(texto)
    if len(plano.split()) > 16:
        return False
    if any(marca in plano for marca in _SOBRE_HACU):
        return False
    if plano.lstrip("¿¡ ").startswith("y "):
        return True
    return any(giro in plano for giro in _GIROS_SEGUIMIENTO)


# Peticiones que piden desarrollo de verdad y no una respuesta de tarima. Son
# deliberadamente pocas y explicitas: un marcador flojo ("explicame") aparece en
# cualquier pregunta corta y subiria el techo de generacion siempre, que es justo
# lo que la regla 3 intenta evitar.
_PETICIONES_DESARROLLO: tuple[str, ...] = (
    "detallad", "en detalle", "con detalle", "paso a paso", "en profundidad",
    "profundiza", "extiendete", "uno por uno", "ampliamente",
    "cuentame todo", "explicame todo", "todo sobre", "todo lo que sepas",
    # Estos piden ademas anchura y tambien salen en `es_catalogo`: "explicame
    # cada proyecto" quiere los 32 Y quiere que los explique, no que los liste.
    "explicame cada", "explicame todos", "describeme todos", "habla de todos",
    "cuentame de cada", "resumeme cada",
)


def pide_desarrollo(texto: str) -> bool:
    """True si piden FONDO. La anchura la mide `es_catalogo`, que es otro eje:
    "¿cuales son todos?" es anchura sin fondo y "cuentame todo sobre Mary" es
    fondo sin anchura. Las dos suben el techo de generacion, pero no traen lo
    mismo del corpus."""
    plano = normalizar(texto)
    return any(marca in plano for marca in _PETICIONES_DESARROLLO)


# Preguntas que piden la LISTA, no la explicacion. La diferencia importa: con 32
# proyectos, responder "cuales tienen" trayendo fichas completas agota el
# contexto y deja fuera la mitad del catalogo.
_PREGUNTAS_DE_CATALOGO: tuple[str, ...] = (
    "que proyectos", "cuales proyectos", "cuales son los proyectos", "cuales son todos",
    "todos los proyectos", "lista de proyectos", "listado de proyectos", "que proyecto tiene",
    "cuantos proyectos", "que tienen en audacia", "que hay en audacia", "que hacen en audacia",
    "que mas proyectos", "otros proyectos", "que mas tienen", "que mas hay",
    # Imperativos: no preguntan, ordenan. Piden lista igual.
    "enumera", "enumerame", "listame", "nombrame", "dime los", "dime cuales",
    "menciona los", "menciname",
    "en que trabajan", "en que estan trabajando", "portafolio",
    # Anchura pedida en plural sin la palabra "lista": "explicame cada proyecto"
    # abarca los 32 igual que "¿cuales tienen?".
    "cada proyecto", "cada uno de los proyectos", "todos y cada",
    "explicame cada", "explicame todos", "describeme todos", "habla de todos",
)


def es_catalogo(texto: str) -> bool:
    """True si la pregunta pide enumerar, no profundizar."""
    plano = normalizar(texto)
    return any(marca in plano for marca in _PREGUNTAS_DE_CATALOGO)


# Preguntas sobre el CENTRO y no sobre sus proyectos. Hacen falta porque el
# corpus de AudacIA son 43 fragmentos de fichas contra 10 institucionales: sin
# filtrar, "¿que patentes tiene el centro?" recuperaba cuatro proyectos de salud
# y ni una patente.
_PREGUNTAS_DE_CENTRO: tuple[str, ...] = (
    "patente", "publicacion", "publicaciones", "articulo cientifico", "articulos cientificos",
    "reconocimiento", "premio", "distincion", "acreditac", "oea", "minciencias",
    "quien dirige", "quien lidera", "quien esta al frente", "director", "directora",
    "investigador", "investigadores", "equipo del centro", "quien trabaja",
    "donde queda", "donde esta", "donde funciona", "ubicacion", "que direccion",
    "infraestructura", "supercomputador", "nucleos", "laboratorios", "metros cuadrados",
    "mision", "vision", "filosofia", "que significa audacia", "que es audacia",
    "que servicios", "como trabajan", "metodologia", "historia de audacia",
    "aliados", "convenios", "inversion",
)


def es_sobre_el_centro(texto: str) -> bool:
    """True si preguntan por el centro en si, no por un proyecto concreto."""
    plano = normalizar(texto)
    return any(marca in plano for marca in _PREGUNTAS_DE_CENTRO)


# Despedidas y agradecimientos. No son una pregunta y no hay nada que recuperar:
# medido en el guion, un "muy interesante, gracias" disparaba el rescate por
# distancia, traia fragmentos sueltos de proyectos y HACU cerraba la visita
# recitando un catalogo —y llegando a inventarse una empresa aliada que no
# existe en el corpus—. Sin material que recitar, cierra y ya.
_DESPEDIDAS: tuple[str, ...] = (
    "gracias", "muchas gracias", "te lo agradezco", "muy interesante", "que interesante",
    "adios", "hasta luego", "hasta pronto", "nos vemos", "chao", "chau", "me voy",
    "eso es todo", "ya esta", "perfecto gracias", "buen trabajo", "felicidades",
)
_MAXIMO_PALABRAS_DESPEDIDA = 12

# "Muchas gracias, ahora explicame el Tanque" lleva "gracias" y no lleva signo de
# interrogacion, pero es una peticion: quien pide algo no se esta despidiendo.
_PETICIONES: tuple[str, ...] = (
    "explica", "cuenta", "dime", "muestra", "ensena", "hablame", "describe",
    "enumera", "lista", "nombra", "quiero", "podrias", "puedes", "necesito",
    "que es", "que son", "como", "cuales", "cuantos", "donde", "quien", "por que",
)


def es_despedida(texto: str) -> bool:
    """True si el visitante se despide o agradece, sin pedir nada mas."""
    plano = normalizar(texto).strip()
    if "?" in plano or len(plano.split()) > _MAXIMO_PALABRAS_DESPEDIDA:
        return False
    if any(peticion in plano for peticion in _PETICIONES):
        return False
    return any(marca in plano for marca in _DESPEDIDAS)


# "¿De donde sacaste eso?" es una trampa conocida: el modelo, presionado, se
# inventa un respaldo —"la experiencia de los investigadores y profesores"— que
# nadie le ha dado. La regla 14 lo prohibe y el modelo la incumple, asi que la
# instruccion va en el turno, que es donde si obedece.
_PREGUNTAS_POR_FUENTES: tuple[str, ...] = (
    "de donde sacaste", "de donde sacas", "de donde lo sacaste", "que fuentes",
    "cual es tu fuente", "quien te lo dijo", "quien te dijo", "como lo sabes",
    "como sabes eso", "en que te basas", "eso quien lo dice", "de donde viene eso",
    "puedes citar", "tienes pruebas", "como estas seguro",
)


def pregunta_por_fuentes(texto: str) -> bool:
    """True si el visitante pide el respaldo de lo que HACU acaba de decir."""
    plano = normalizar(texto)
    return any(marca in plano for marca in _PREGUNTAS_POR_FUENTES)


# Lineas del indice-catalogo: "- **Mary**: chatbot que analiza el lenguaje...".
_NOMBRE_EN_INDICE = re.compile(r"^\s*[-*]\s*\*\*(?P<nombre>[^*]+)\*\*\s*:", re.MULTILINE)


def nombres_en_indice(indice: str) -> tuple[str, ...]:
    """Extrae los nombres de proyecto del indice, para ensenarselos al router."""
    if not indice:
        return ()
    return tuple(m.group("nombre").strip() for m in _NOMBRE_EN_INDICE.finditer(indice))


# El visitante cuenta algo suyo en vez de preguntar. Importa distinguirlo: ante
# una confidencia, las capas de estilo que existen para que HACU no adule
# sobran, porque reconocer lo que alguien acaba de contarte es exactamente lo
# que hace un expositor. Medido en escena: "tengo una novia que se llama
# Daniela..." recibia de vuelta "¿que te trae a la exhibicion hoy?".
_PRIMERA_PERSONA: tuple[str, ...] = (
    "tengo", "soy", "estoy", "me llamo", "mi nombre", "me gusta", "me encanta",
    "quiero contarte", "vengo a", "vengo de", "estudio", "trabajo", "vivo",
    "mi novia", "mi novio", "mi esposa", "mi esposo", "mi hijo", "mi hija",
    "mi madre", "mi padre", "mi hermano", "mi hermana", "mi perro", "mi gato",
    "acabo de", "hoy cumpl", "estoy orgullos", "me hace ilusion", "me emociona",
)


# "Tengo entendido que el Tanque vuela" empieza en primera persona pero no habla
# de quien lo dice: es una afirmacion sobre la exhibicion, y ahi toca corregir,
# no acompanar.
_NO_ES_CONFIDENCIA: tuple[str, ...] = (
    "tengo entendido", "me dijeron", "me contaron", "escuche que", "he leido",
    "un companero", "un profesor", "un amigo me", "dicen que", "es verdad que",
)


def es_confidencia(texto: str) -> bool:
    """True si el visitante esta contando algo suyo y no preguntando nada."""
    plano = normalizar(texto)
    if any(marca in plano for marca in _NO_ES_CONFIDENCIA):
        return False
    if "?" in texto or plano.lstrip("¿ ").startswith(("que ", "como ", "cuando ", "donde ",
                                                      "quien ", "cual ", "cuanto ", "por que")):
        return False
    return any(marca in plano for marca in _PRIMERA_PERSONA)
