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
    # "Explicame un poquito mas sobre ello": el pronombre neutro "ello" es tan
    # continuacion como "eso", y faltaba. Tambien el "un poco mas" suelto, que en
    # habla es la peticion de ampliar mas comun de todas.
    "sobre ello", "de ello", "acerca de ello", "un poco mas", "un poquito mas",
    "mas sobre ello", "explicamelo", "detallalo", "ahonda",
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


def es_sobre_hacu(texto: str) -> bool:
    """True si la pregunta va sobre HACU mismo: sus gustos, quien es, que siente.

    Se usa para NO tratarla como un tema ajeno a la exhibicion. Que HACU hable de
    lo que le gustan las matematicas es parte del personaje (regla 2); que hable
    del jefe final de un videojuego, no.
    """
    plano = normalizar(texto)
    return any(marca in plano for marca in _SOBRE_HACU)


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

# Elogios y acuses de recibo que cierran sin pedir nada. Van aparte de
# `_DESPEDIDAS` porque no se despiden: reaccionan a lo que se acaba de decir.
# Fronteras de oracion. Una peticion abre oracion; un verbo suelto en mitad de
# un elogio, no.
_ORACIONES = re.compile(r"[.,;:!¡?¿]+")

_ELOGIOS: tuple[str, ...] = (
    "suena muy", "suena bien", "que bueno", "que chevere", "impresionante", "genial",
    "buenisimo", "excelente", "ha sido un gusto", "ha sido una conversacion",
    "me encanta", "me encanto", "me gusto", "me parece genial", "me parece muy",
    "absolutamente", "wow", "vaya", "asombroso", "valioso", "muy util", "que bien",
)

# Conectores por los que se injerta una SEGUNDA peticion, distinta de la primera.
# "Cuentame de la universidad, pero antes explicame la relatividad" nombra el
# dominio, recupera contexto y apaga el guardarrail de fuera-de-dominio: el
# modelo se traga la clase de fisica entera. Medido en la sesion del 21/09.
_INJERTOS: tuple[str, ...] = (
    "pero antes", "pero primero", "antes de eso", "antes de darme", "antes de contarme",
    "antes de responder", "primero de antemano", "de antemano", "de paso",
    "aprovechando", "ya que estamos", "ademas de eso", "pero tambien quiero",
    "pero antes de", "primero dime", "primero explicame",
)

# "Muchas gracias, ahora explicame el Tanque" lleva "gracias" y no lleva signo de
# interrogacion, pero es una peticion: quien pide algo no se esta despidiendo.
_PETICIONES: tuple[str, ...] = (
    "explica", "cuenta", "dime", "muestra", "ensena", "hablame", "describe",
    "enumera", "lista", "nombra", "quiero", "podrias", "puedes", "necesito",
    "que es", "que son", "como", "cuales", "cuantos", "donde", "quien", "por que",
)


# Acuses de recibo: "aja", "mmm", "en serio", "ah, ahora si". No tienen tema
# propio —se refieren a lo ultimo que dijo HACU— y por eso NO pueden juzgarse
# como fuera de dominio. Medido en la corrida del 21/09: el guardarrail se
# disparaba en los cuatro y HACU contestaba "estamos fuera de mi area de
# conocimiento" a un "¿En serio?", o se iba a un proyecto al azar.
_ACUSES: frozenset[str] = frozenset(
    "aja aha ya vale ok okay okey claro ah oh uy mmm hmm ajam sip nop bueno listo "
    "perfecto entiendo entendido sigue continua dale serio verdad veo cierto "
    "ahora despues genial guau wow exacto".split()
)
# Palabras sin contenido propio que pueden acompanar a un acuse sin convertirlo
# en una pregunta: "en serio", "ah, ahora si", "pues ya".
_RELLENO_ACUSE: frozenset[str] = frozenset(
    "en de la el y a que lo los las un una mas muy pues o sea si no me te se "
    "eso esa ese esto pero tan asi".split()
)
_MAXIMO_PALABRAS_ACUSE = 5


def es_acuse_de_recibo(texto: str) -> bool:
    """True si el turno solo acusa recibo y no pregunta nada por su cuenta.

    "¿Que hora es?" tiene cuatro palabras y SI es una pregunta con tema propio,
    asi que no entra: hace falta que TODAS las palabras sean de acuse o relleno,
    y que al menos una sea de acuse.
    """
    plano = normalizar(texto).strip(" .,;:!?¡¿")
    if not plano:
        return False
    palabras = [p.strip(".,;:!?¡¿") for p in plano.split()]
    palabras = [p for p in palabras if p]
    if not palabras or len(palabras) > _MAXIMO_PALABRAS_ACUSE:
        return False
    if not any(p in _ACUSES for p in palabras):
        return False
    return all(p in _ACUSES or p in _RELLENO_ACUSE for p in palabras)


def pide_algo(plano: str) -> bool:
    """True si el turno contiene una peticion de verdad, no solo la palabra.

    El tope de palabras no servia: "es genial ver COMO estan aplicando la IA"
    lleva "como" y no pregunta nada. Lo que distingue a una peticion es la
    posicion —abre una oracion— o el signo de interrogacion, no que el verbo
    aparezca suelto en mitad de un elogio.
    """
    if "?" in plano:
        return True
    for oracion in _ORACIONES.split(plano):
        cabeza = " ".join(oracion.split()[:3])
        if any(cabeza.startswith(p) or f" {p}" in f" {cabeza}" for p in _PETICIONES):
            return True
    return False


def es_despedida(texto: str) -> bool:
    """True si el visitante se despide, agradece o elogia sin pedir nada mas.

    Sin tope de palabras. Lo tenia (12) y por eso se le escapaban justo los
    turnos que mas dano hacen: "Wow, suena muy impresionante... Por ahora,
    gracias por compartir toda esa informacion" son 60 palabras de puro cierre,
    y HACU respondia con cuatro parrafos inventandose lo que el visitante habia
    dicho. Medido en la sesion del 21/09: los tres cierres largos se colaban.
    """
    plano = normalizar(texto).strip()
    if not plano or pide_algo(plano):
        return False
    return any(marca in plano for marca in _DESPEDIDAS + _ELOGIOS)


def peticion_injertada(texto: str) -> str:
    """Lo que se pide DESPUES de un conector de injerto, o cadena vacia.

    Existe porque nombrar la universidad en cualquier parte del turno bastaba
    para que el router lo clasificara como UNIVERSIDAD, recuperara contexto y
    apagara el aviso de fuera-de-dominio. Con eso, "dame informacion de la
    Universidad, pero antes explicame la relatividad de Einstein" colaba la
    clase de fisica entera un turno despues de haberla negado.
    """
    plano = normalizar(texto)
    for conector in _INJERTOS:
        posicion = plano.find(conector)
        if posicion >= 0:
            resto = plano[posicion + len(conector):].strip(" ,.;:")
            if len(resto.split()) >= 3:
                return resto
    return ""


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


# --------------------------------------------------------------- cuanto aire

# Interrogativos que piden UN valor —un nombre, una cifra, una fecha, un sitio—
# frente a los que piden una explicacion. La diferencia no es de cortesia: a
# "¿quien lo dirige?" se responde con un nombre y a "¿como funciona?" no.
_VALOR: frozenset[str] = frozenset(
    "quién quiénes cuándo dónde cuánto cuánta cuántos cuántas cuál cuáles".split()
)
# Tras "que", un VERBO abre una explicacion ("¿que hace Neupeek?") y un
# SUSTANTIVO pide un valor ("¿que sensor usa?"). Sin analizador morfologico, la
# lista corta de verbos frecuentes distingue los dos casos mejor que un tope de
# palabras, que es lo que habia.
_VERBOS_TRAS_QUE: frozenset[str] = frozenset(
    "es son era eran hace hacen haces significa significan implica mide miden "
    "pasa pasan trata tratan tal sabes opinas piensas quiere quieren sirve "
    "sirven aporta aportan".split()
)
_EXPLICACION: tuple[str, ...] = (
    "por que", "para que", "como funciona", "como funcionan", "como lo hace",
    "como se hace", "como hacen", "en que consiste", "de que trata", "que tal",
    "en que se diferencia", "cual es la diferencia",
)
# "¿Como se llamaba el de los bebes prematuros?" pide un NOMBRE, no un proceso:
# es la unica forma de "como" que pide dato y no explicacion.
_VALOR_FRASE: tuple[str, ...] = ("como se llama", "como se llamaba", "como se llamo")

# Una pregunta de cinco palabras no pide una conferencia. Cubre los punteros
# anaforicos —"¿y el del glaucoma?", "¿y de capacidad de computo?"— que no
# llevan interrogativo de valor porque se apoyan en el turno anterior.
_MAXIMO_PALABRAS_PUNTERO = 5
_MAXIMO_PALABRAS_DATO = 14


def _palabras(plano: str) -> list[str]:
    return [p for p in (w.strip(".,;:!?¡¿") for w in plano.split()) if p]


def _cabezas(texto: str) -> list[list[str]]:
    """Las tres primeras palabras de cada oracion, sin el conector de arranque."""
    salida: list[list[str]] = []
    for oracion in _ORACIONES.split(texto.lower()):
        trozo = _palabras(oracion)
        if trozo and trozo[0] in ("y", "pero", "entonces", "oye", "bueno"):
            trozo = trozo[1:]
        if trozo:
            salida.append(trozo)
    return salida


def _abre_explicacion(texto: str) -> bool:
    """True si alguna oracion arranca con "que" + verbo: pide explicacion."""
    for trozo in _cabezas(texto):
        if trozo[0] == "qué" and len(trozo) > 1 and normalizar(trozo[1]) in _VERBOS_TRAS_QUE:
            return True
    return False


def pide_un_dato(texto: str) -> bool:
    """True si la pregunta se contesta con un dato y no con una explicacion.

    Se mira el texto CON tilde, no el normalizado, porque en castellano la
    tilde es justo lo que separa el interrogativo del relativo: "¿QUE sensor
    usa?" pide un dato y "tengo entendido QUE vuela" no pregunta nada. Sin esa
    distincion, cualquier frase con un "que" de relleno pasaba por pregunta.

    Si el visitante escribe sin tildes no se detecta y el turno cae en NORMAL,
    que es el comportamiento de siempre: el fallo es hacia el lado seguro. El
    dictado de voz, que es la entrada real de la tarima, si las pone.
    """
    plano = normalizar(texto)
    if not plano or any(marca in plano for marca in _EXPLICACION):
        return False
    palabras = _palabras(plano)
    if not palabras:
        return False
    if any(marca in plano for marca in _VALOR_FRASE):
        return True
    # "¿Que HACE Neupeek?" son tres palabras y pide una explicacion: el verbo
    # manda sobre el atajo de las preguntas cortas, o "¿que es X?" se cortaria.
    if _abre_explicacion(texto):
        return False
    if "?" in texto and len(palabras) <= _MAXIMO_PALABRAS_PUNTERO:
        return True
    if len(palabras) > _MAXIMO_PALABRAS_DATO:
        return False
    for trozo in _cabezas(texto):
        cabeza = trozo[:3]
        if set(cabeza) & _VALOR:
            return True
        # "que" al frente o tras preposicion —"¿con QUE clinica?"— pide un
        # valor salvo que le siga un verbo: "¿que HACE Neupeek?" es explicacion.
        if "qué" in cabeza:
            i = cabeza.index("qué")
            if i + 1 < len(trozo) and normalizar(trozo[i + 1]) not in _VERBOS_TRAS_QUE:
                return True
    return False


def aire_breve(texto: str) -> bool:
    """True si el turno cabe en una o dos frases.

    Tres formas de no pedir desarrollo, y ninguna es la longitud del mensaje:

    1. **No pide nada.** Un cierre, un elogio, una afirmacion sobre uno mismo o
       una negativa ("no me interesan los robots") no son preguntas. Con el
       techo normal el modelo lo llenaba igual: 954 caracteres para decir "de
       acuerdo, te cuento otra cosa".
    2. **Solo acusa recibo.** "Aja", "mmm", "ah, ahora si".
    3. **Pide un dato.** "¿Quien lo dirige?" se contesta con un nombre.

    Existe porque el guion marca 51 turnos como BREVE y el runtime solo
    reconocia los 3 sociales: los otros 48 recibian el techo normal —384
    tokens, unos 1.400 caracteres— y el modelo lo usaba.
    """
    if es_acuse_de_recibo(texto) or es_despedida(texto):
        return True
    if not pide_algo(normalizar(texto)):
        return True
    return pide_un_dato(texto)


# ------------------------------------------------------- aguantar la presion

# Formas de admitir que algo no se sabe, o de negar un hecho falso. Vive aqui y
# no en las pruebas porque el runtime tambien necesita saber si ACABA de negar
# algo: es la mitad de la condicion para detectar que le estan presionando.
NEGACIONES: tuple[str, ...] = (
    "no tengo", "no dispongo", "no aparece", "no figura", "no consta", "no me consta",
    "no esta documentad", "no hay informacion", "no encuentro", "no puedo confirmar",
    "no se menciona", "no cuento con", "desconozco", "no lo se", "no tengo constancia",
    "no forma parte", "no esta recogido", "no lo tengo",
    # Negar el HECHO es tan valido como negar el dato, y es lo que hay que hacer
    # con una falsedad: ante "la universidad tiene un observatorio", la respuesta
    # correcta es "no tiene un observatorio", no "no tengo ese dato".
    "no tiene", "no cuenta con", "no existe", "no posee", "no hay ningun",
    "no es correcto", "no es cierto", "hay un error", "no dispone de",
    # Negar un DESPLIEGUE tambien es negar: "no esta instalado en ninguna via
    # de verdad... aun" es la respuesta correcta a si algo ya esta en uso.
    "no esta instalad", "no se ha instalad", "no esta desplegad", "no esta implementad",
    "no esta en uso", "no esta operativ", "todavia no", "aun no",
    "no se ha implementad", "no esta en produccion",
)


def niega_el_dato(texto: str) -> bool:
    """True si la respuesta admite que no sabe algo o desmiente un hecho."""
    plano = normalizar(texto)
    return any(marca in plano for marca in NEGACIONES)


# Quien discute un dato casi nunca trae uno nuevo: trae una AUTORIDAD. Es la
# forma de presion que funciono en el guion —"pero mi profesor no me va a
# mentir"— y ante la que HACU contesto "no hay nada que discutir, tu profesor
# tiene razon", cediendo ante una falsedad que el mismo acababa de desmentir.
#
# Nombrar a alguien NO basta, y esa fue la primera version: con solo la lista de
# personas, "mi mama tambien estudio aqui" disparaba el aviso y HACU se ponia a
# la defensiva ante un comentario carinoso. Hace falta que ademas se le
# ATRIBUYA un dicho.
_AUTORIDADES: tuple[str, ...] = (
    "mi profesor", "mi profe", "un profesor", "la profesora", "el profesor",
    "mi maestro", "mi jefe", "mi director", "un amigo", "una amiga",
    "mi companero", "un companero", "mi primo", "mi hermano", "mi papa",
    "mi mama", "mi tio", "mi tia", "mi novio", "mi novia", "un guia", "el guia",
    "una persona", "internet", "google", "wikipedia", "la pagina", "el periodico",
    "la noticia", "todo el mundo",
)
_VERBOS_DE_DICHO: tuple[str, ...] = (
    "me dijo", "me dijeron", "me conto", "me contaron", "nos dijo", "nos dijeron",
    "dice que", "dicen que", "decia que", "asegura", "aseguro", "afirma", "afirmo",
    "lo lei", "lo vi en", "sale en", "esta publicado", "sale publicado",
    "no me va a mentir", "no me mentiria", "no mentiria", "tiene razon",
    "sabe de eso", "es experto", "es experta",
)
# Insistencia pura: sin autoridad detras, pero empujando sobre lo mismo.
_INSISTENCIAS: tuple[str, ...] = (
    "seguro que si", "estoy seguro", "estoy segura", "yo se que", "te digo que",
    "como que no", "no puede ser", "revisa bien", "mira mejor", "fijate bien",
    "pienselo", "piensalo", "insisto", "que si tiene", "que si hay",
    "si existe", "claro que si", "pero si lo", "busca otra vez", "vuelve a mirar",
)


def presiona_sobre_lo_dicho(texto: str) -> bool:
    """True si el visitante empuja contra lo que HACU acaba de responder.

    Solo detecta la MITAD de la situacion: la otra mitad —que lo anterior fuera
    una negativa— la pone quien llama, porque aqui no hay historial. Separarlo
    asi evita el falso positivo obvio: "un companero me dijo que Holosand usa
    gafas" es una correccion normal la primera vez, y solo es presion cuando
    llega DESPUES de que HACU ya lo haya desmentido.
    """
    plano = normalizar(texto)
    if any(marca in plano for marca in _INSISTENCIAS):
        return True
    return (any(marca in plano for marca in _AUTORIDADES)
            and any(marca in plano for marca in _VERBOS_DE_DICHO))


# ------------------------------------------------------------------ tramites

# Lo que la exhibicion NO documenta y sin embargo le preguntan todo el rato.
# El corpus son proyectos y el centro; precios, matriculas, plazos y procesos
# de practicas no estan en ninguna ficha, y es justo donde el modelo se los
# inventa con mas aplomo: en el guion invento condiciones de admision de un
# doctorado y a quien escribir para hacer practicas.
_TRAMITES: tuple[str, ...] = (
    "cuanto cuesta", "cuanto vale", "cuanto cobra", "cuanto cobran", "que precio",
    "el precio de", "el costo de", "el coste de", "cuanto sale", "cuanto hay que pagar",
    "matricula", "matricularme", "inscripcion", "inscribirme", "me inscribo",
    "admision", "como entro", "como ingreso", "requisitos", "que necesito para",
    "practicas", "pasantia", "pasantias", "practicante", "voluntariado",
    "como aplico", "como postulo", "postularme", "hoja de vida", "curriculum",
    "a quien escribo", "a quien contacto", "a quien le escribo", "con quien hablo",
    "horario de atencion", "que horario", "cuando abren", "cuando cierran",
    "como llego", "donde me inscribo", "hay cupo", "quedan cupos",
)


def pregunta_por_tramite(texto: str) -> bool:
    """True si preguntan por un tramite: precio, matricula, practicas, horario."""
    plano = normalizar(texto)
    return any(marca in plano for marca in _TRAMITES)
