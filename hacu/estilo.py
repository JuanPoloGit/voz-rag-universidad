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
        r"(excelente|buena|muy buena|gran) pregunta",
        r"me encanta (que|tu pregunta)",
        r"gracias por (preguntar|tu pregunta)",
        # Entusiasmo inflado. No es adulacion AL VISITANTE —por eso los patrones
        # de arriba no lo cazaban— sino una frase entera de "me parece muy
        # interesante" delante de la respuesta de verdad. En la sesion con
        # publico aparecia en casi todos los turnos, y cuando ocupa la frase
        # completa no aporta un solo dato: se va, y lo que queda es el contenido.
        # Todos estos terminan en `$`: solo se descarta la frase cuando el
        # entusiasmo ES la frase entera. "Me parece muy interesante el proyecto
        # Mary." se va; "...es muy interesante porque analiza el lenguaje para
        # detectar ansiedad" se queda, porque despues del adjetivo viene la
        # respuesta de verdad y la cola se pasa del limite.
        r"(me parece|me resulta|encuentro) (muy |bastante |realmente |especialmente )?"
        r"(interesante|innovador|innovadora|emocionante|fascinante|impresionante|"
        r"valioso|valiosa|prometedor|prometedora|apasionante)[^.!?]{0,30}[.!]?$",
        r"es (un proyecto |una iniciativa )?(muy |realmente )?"
        r"(interesante|innovador|innovadora|emocionante|fascinante|impresionante)"
        r"[^.!?]{0,25}[.!]?$",
        r"(que|muy) (interesante|emocionante|innovador)[^.!?]{0,25}[.!]?$",
        # Risa de relleno. Visto en escena delante de alguien que decia no saber
        # quien era: "¡Ahah, no te preocupes!". No hay turno en una exhibicion en
        # el que una risa escrita aporte algo, y hay muchos en los que ofende.
        r"(a?ja)?(ja|je|ha|ah)(ja|je|ha|ah)+[^.!?]{0,30}[.!]?$",
        r"no te preocupes[^.!?]{0,20}[.!]?$",
        r"que (bueno|genial|bien|chevere)[^.!?]{0,18}[.!]?$",
        # "Me parece que el proyecto Mary es muy interesante." El sujeto va en
        # medio, asi que los patrones anteriores no lo alcanzan.
        r"(me parece|creo|considero|dirian?) que .{0,45}? es "
        r"(muy |bastante |realmente )?"
        r"(interesante|innovador|innovadora|emocionante|fascinante|impresionante)"
        r"[^.!?]{0,25}[.!]?$",
    )
)
# "Gracias por compartirlo" y "es un placer conocerte" SALIERON de la lista a
# proposito. Se filtraban siempre, y cuando un visitante cuenta algo suyo esa
# frase es la respuesta correcta, no un halago: borrarla dejaba a HACU
# contestando a una confidencia con una pregunta de tramite. El problema medido
# eran los elogios A LA PREGUNTA antes de un dato, y esos siguen fuera.
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


# Atribuciones: frases en las que HACU le cuenta al visitante lo que el visitante
# supuestamente dijo o entendio. En la sesion con publico del 21/09 aparecieron
# siete veces y las siete eran FALSAS: "me parece que has mencionado varios
# proyectos que te han llamado la atencion, incluyendo el proyecto Mario, el ROV
# Submarino y Solenium" —el visitante no habia nombrado ninguno de los tres—.
# Es el contexto recuperado leido como si lo hubiera dicho la persona de enfrente,
# y es peor que divagar: le pone palabras en la boca a quien tienes delante.
#
# Se cazan solo las metaobservaciones sobre lo que el visitante menciono o
# entendio. "Me dijiste que estudias Sistemas" NO esta aqui a proposito: eso es
# seguir el hilo, que es justo lo que queremos.
_ATRIBUCIONES: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p) for p in (
        r"(me parece|veo|noto|entiendo|celebro) que (has|hayas|habias) "
        r"(mencionado|entendido|comprendido|captado|apreciado|encontrado|"
        r"senalado|valorado|dicho|planteado)",
        r"(has|habias) mencionado (varios|varias|algunos|algunas|que|lo|los|las)",
        r"(me alegra|me gusta) que (hayas|has) (entendido|comprendido|captado|mencionado)",
        r"gracias por (compartir|contarme|darme|explicarme|brindarme)"
        r"( conmigo)? (la|esa|toda esa|esta|tanta) informacion",
        r"(has|habias) (entendido|comprendido) (muy bien|correctamente|perfectamente)",
    )
)
_LONGITUD_MAXIMA_ATRIBUCION = 400


def es_atribucion(frase: str) -> bool:
    """True si la frase le atribuye al visitante algo que dijo o entendio."""
    limpia = frase.strip()
    if not limpia or len(limpia) > _LONGITUD_MAXIMA_ATRIBUCION:
        return False
    plano = _VOCATIVO.sub("", normalizar(limpia).lstrip("¡!¿? "))
    return any(patron.match(plano) for patron in _ATRIBUCIONES)


def filtrar_atribuciones(texto: str) -> tuple[str, int]:
    """Quita las frases que le ponen palabras en la boca al visitante."""
    frases = [f for f in _SEPARADOR_FRASES.split(texto) if f.strip()]
    conservadas = [f.strip() for f in frases if not es_atribucion(f)]
    quitadas = len(frases) - len(conservadas)
    if not quitadas:
        return texto, 0
    if not conservadas:
        return "", quitadas
    prefijo = " " if texto[:1].isspace() else ""
    return prefijo + " ".join(conservadas), quitadas


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
    # Primero el caso en el que el giro ES la respuesta: "no aparece en las
    # notas" quiere decir "no lo tengo", y borrar solo el giro dejaba un "No"
    # suelto. Va antes que los patrones genericos, que se lo comerian a medias.
    (re.compile(r"\bno\s+(?:aparece|figura|consta|esta|viene|se\s+menciona)\s+en\s+"
                r"(?:las|mis|estas)\s+notas\b", re.IGNORECASE), "no lo tengo"),
    (re.compile(r"\bno\s+(?:aparece|figura|consta|esta|viene|se\s+menciona)\s+en\s+"
                r"(?:la\s+)?documentaci[oó]n\b", re.IGNORECASE), "no lo tengo"),
    (re.compile(r",?\s*(?:que\s+)?(?:menciono|menciona|mencionan|aparecen?|figuran?|"
                r"se\s+mencionan?|se\s+menciona|tengo|hay)\s+en\s+(?:las|mis|estas)\s+notas\b",
                re.IGNORECASE), ""),
    (re.compile(r"\b(?:seg[uú]n|de\s+acuerdo\s+con|conforme\s+a)\s+(?:las|mis|estas)\s+notas\b,?\s*",
                re.IGNORECASE), ""),
    (re.compile(r"\ben\s+(?:las|mis|estas)\s+notas(?:\s+de\s+la\s+documentaci[oó]n[^,.]*)?,?\s*",
                re.IGNORECASE), ""),
    # Visto en escena: "En el contexto de lo que sé privadas que tengo...". El
    # modelo escribio "las notas privadas que tengo" y el patron generico de
    # abajo se comio solo "las notas", dejando el adjetivo huerfano. La frase
    # entera, con sus adjetivos y su coletilla, va primero.
    (re.compile(r"\b(?:las|mis|estas)\s+notas(?:\s+(?:privadas|internas|que\s+tengo|"
                r"de\s+que\s+dispongo))+", re.IGNORECASE), "lo que sé"),
    (re.compile(r"\b(?:las|mis|estas)\s+notas\b", re.IGNORECASE), "lo que sé"),
    # Fuga nueva, vista en 11 de 30 turnos del guion: prohibida "segun mis
    # notas", el modelo encontro "segun la documentacion de AudacIA". Es el mismo
    # andamiaje con otro nombre, y en escena suena a que lee de una ficha.
    # No se come la coma de delante: comersela pegaba el vocativo a la frase
    # ("Camila, segun la documentacion, los proyectos" -> "Camilalos proyectos").
    (re.compile(r"\s*seg[uú]n\s+(?:la\s+|el\s+|mi\s+|mis\s+)?"
                r"(?:documentaci[oó]n|informaci[oó]n|corpus|material)"
                r"(?:\s+interna)?(?:\s+(?:que|de\s+la\s+que)\s+(?:tengo|dispongo|manejo))?"
                r"(?:\s+(?:de|del|de\s+la)\s+[^,.:]{1,40})?\s*,?\s*", re.IGNORECASE), " "),
    (re.compile(r",?\s*(?:que\s+)?(?:se\s+)?(?:mencionan?|aparecen?|figuran?)\s+en\s+"
                r"(?:la\s+)?documentaci[oó]n(?:\s+(?:de|del|de\s+la)\s+[^,.:]{1,40})?",
                re.IGNORECASE), ""),
    (re.compile(r"\bla\s+documentaci[oó]n\s+interna\b", re.IGNORECASE), "lo que sé"),
    # Se come el giro entero hasta la siguiente puntuacion. Cualquier frase que
    # diga "la documentacion institucional" ya es una fuga, asi que no hay nada
    # que conservar dentro del giro; afinar los limites palabra a palabra solo
    # dejaba colgando el final ("...es lo que sé disposición").
    (re.compile(r"(?:basad[oa]s?\s+en\s+)?\bla\s+documentaci[oó]n\s+"
                r"(?:institucional|interna|oficial)[^.;:!?]{0,70}", re.IGNORECASE), "lo que sé"),
)
_ESPACIO_SOBRANTE = re.compile(r"\s{2,}")

# El stream de llama.cpp pierde de vez en cuando el espacio inicial de un token y
# salen pegones: "productos comerciales.AudacIA se enfoca", "Esto incluye26". En
# pantalla es feo; en voz es peor, porque el sintetizador lo lee como una sola
# palabra. Se separa solo donde no hay ambiguedad: nunca dentro de un decimal
# (3.000) ni de una sigla con puntos (R.O.V.).
_PEGONES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"([.!?,;:])([A-ZÁÉÍÓÚÑ][a-záéíóúñ])"), r"\1 \2"),
    (re.compile(r"([a-záéíóúñ])(\d{2,})"), r"\1 \2"),
    # Sigla pegada a la palabra siguiente: "ROVSubmarino". Exige dos mayusculas
    # seguidas y luego una palabra capitalizada, para no partir "MacondoLab"
    # (una sola mayuscula interior) ni "AudacIA" (no lleva palabra detras).
    # Limitacion conocida: si el pegon es sigla + palabra en minuscula
    # ("VARTevalua"), la expresion no sabe donde acaba la sigla y corta mal
    # ("VAR Tevalua"). Solo dispara sobre texto que YA venia roto, asi que el
    # resultado no es peor que la entrada, pero tampoco lo arregla.
    (re.compile(r"\b([A-ZÁÉÍÓÚÑ]{2,})([A-ZÁÉÍÓÚÑ][a-záéíóúñ]{2,})"), r"\1 \2"),
)


def separar_pegones(texto: str) -> tuple[str, int]:
    """Repone los espacios que se pierden en el stream. Devuelve (texto, arreglos)."""
    total = 0
    for patron, reemplazo in _PEGONES:
        texto, n = patron.subn(reemplazo, texto)
        total += n
    return texto, total


# Marcas de Markdown. El modelo las escribe por costumbre —"**Salud y
# Diagnostico Medico**"— y aqui no las renderiza nadie: en pantalla se ven los
# asteriscos crudos y Piper los LEE, asi que el visitante oye "asterisco
# asterisco salud". Se quitan en la misma capa que las fugas para que pantalla,
# voz y memoria sigan recibiendo exactamente el mismo texto.
_MARCAS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Encabezados al principio de linea: "### Titulo" -> "Titulo".
    (re.compile(r"^#{1,6}\s+", re.MULTILINE), ""),
    # Negrita y cursiva, con y sin subrayado. El contenido se conserva entero.
    (re.compile(r"\*\*\*(?=\S)(.+?)(?<=\S)\*\*\*", re.DOTALL), r"\1"),
    (re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", re.DOTALL), r"\1"),
    (re.compile(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])"), r"\1"),
    # El subrayado solo cuenta como marca si esta suelto: `snake_case` y
    # `n_ctx` son nombres de cosas de este proyecto, no cursivas.
    (re.compile(r"(?<![\w_])__(?=\S)(.+?)(?<=\S)__(?![\w_])", re.DOTALL), r"\1"),
    # Codigo entre comillas invertidas: se lee el contenido, no la comilla.
    (re.compile(r"`{1,3}([^`\n]+?)`{1,3}"), r"\1"),
    # Vinetas al principio de linea. La numerada ("1. Mary:") se queda: se lee
    # bien en voz alta y ordena la enumeracion.
    (re.compile(r"^[ \t]*[-*+•]\s+", re.MULTILINE), ""),
)


def limpiar_marcas(texto: str) -> tuple[str, int]:
    """Quita el Markdown que nadie renderiza. Devuelve (texto, marcas quitadas)."""
    limpio, total = texto, 0
    for patron, reemplazo in _MARCAS:
        limpio, n = patron.subn(reemplazo, limpio)
        total += n
    return (limpio, total) if total else (texto, 0)


def limpiar_fugas(texto: str) -> tuple[str, int]:
    """Quita las referencias al andamiaje interno. Devuelve (texto, fugas eliminadas)."""
    limpio, total = texto, 0
    for patron, reemplazo in _FUGAS:
        limpio, n = patron.subn(reemplazo, limpio)
        total += n
    if not total:
        return texto, 0
    if not texto[:1].isspace():
        limpio = limpio.lstrip()
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

    def __init__(self, permitir_cierre_breve: bool = False,
                 permitir_calidez: bool = False) -> None:
        # En una despedida, "De nada, Camila." es la respuesta COMPLETA, asi que
        # el minimo de contenido previo no aplica: sin esto, la coletilla
        # "¿te gustaria saber mas sobre otros proyectos?" se conservaba para no
        # dejar el turno mudo, y convertia un cierre correcto en un folleto.
        self._permitir_cierre_breve = permitir_cierre_breve
        # Cuando el visitante cuenta algo personal, el filtro de adulacion se
        # apaga: reconocer lo que acaban de contarte es el trabajo de un
        # expositor, no un halago vacio. El filtro se hizo contra "Excelente
        # pregunta" delante de un dato, no contra "que bueno, enhorabuena".
        self._permitir_calidez = permitir_calidez
        self._buffer = ""
        self._emitido = ""
        self.descartada = False
        self.truncada = False
        self.adulaciones_quitadas = 0
        self.atribuciones_quitadas = 0
        self.fugas_limpiadas = 0
        self.pegones_separados = 0
        self.marcas_quitadas = 0

    def alimentar(self, fragmento: str) -> str:
        """Acumula el fragmento y devuelve el texto que ya es seguro emitir."""
        self._buffer += fragmento
        corte = self._corte(self._buffer)
        if corte <= 0:
            return ""
        listo, self._buffer = self._buffer[:corte], self._buffer[corte:]
        if not self._permitir_calidez:
            listo, quitadas = filtrar_adulacion(listo)
            self.adulaciones_quitadas += quitadas
        # Las atribuciones se filtran SIEMPRE, tambien en las confidencias: que
        # el visitante cuente algo suyo no autoriza a inventarse lo que dijo.
        listo, atribuidas = filtrar_atribuciones(listo)
        self.atribuciones_quitadas += atribuidas
        listo, fugas = limpiar_fugas(listo)
        self.fugas_limpiadas += fugas
        listo, marcas = limpiar_marcas(listo)
        self.marcas_quitadas += marcas
        listo, pegones = separar_pegones(listo)
        self.pegones_separados += pegones
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
        suficiente = (self._permitir_cierre_breve
                      or len(self._emitido.strip()) >= _MINIMO_CONTENIDO_PREVIO)
        if suficiente and cola.strip() and es_coletilla(cola):
            self.descartada = True
            return ""
        # La cola tambien puede ser un cumplido suelto, si no deja el turno mudo.
        if self._emitido.strip() and not self._permitir_calidez:
            cola, quitadas = filtrar_adulacion(cola)
            self.adulaciones_quitadas += quitadas
        if self._emitido.strip():
            cola, atribuidas = filtrar_atribuciones(cola)
            self.atribuciones_quitadas += atribuidas
        cola, fugas = limpiar_fugas(cola)
        self.fugas_limpiadas += fugas
        cola, marcas = limpiar_marcas(cola)
        self.marcas_quitadas += marcas
        cola, pegones = separar_pegones(cola)
        self.pegones_separados += pegones
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
