"""Guion de una visita real: 100 turnos encadenados con un mismo visitante.

La bateria mide sesenta entradas sueltas y barajadas. Esto mide lo contrario: una
sola conversacion con hilo, donde cada turno depende del anterior. Solo aqui
salen los fallos que importan en una tarima — perder el tema, contradecirse,
confirmar una alucinacion cuando el visitante insiste, o soltar un monologo
cuando bastaba una frase.

Cada turno declara que datos del corpus DEBE contener la respuesta y que
invenciones NO puede contener. Los datos salen de `documents/`, no de mi memoria.

Lo que mide, por encima de los datos: que HACU **sostenga una conversacion**. Un
visitante real no encadena preguntas bien formadas. Dice "ya", "¿y eso?", "no",
"mmm", se va por las ramas, afirma cosas en vez de preguntarlas y vuelve treinta
turnos despues a algo que se dijo al principio. Un sistema que solo responde
preguntas bien formadas pasa por un buscador con voz; uno que no recuerda lo
inmediatamente anterior pasa por un SimSimi. De ahi los cinco tipos nuevos
—AFIRMACION, NEGATIVA, ALEATORIA, VAGA y ANCLA— y de ahi que la mayoria de los
turnos vagos prohiban explicitamente el nombre de OTRO proyecto: perder el hilo
tiene una firma observable, que es ponerse a hablar de otra cosa.

Como se corre (pide GPU, ~20-35 min los 100 turnos):

    python -m pruebas.conversacion                  # la visita entera
    python -m pruebas.conversacion --hasta G50      # primera mitad
    python -m pruebas.conversacion --desde G51      # segunda mitad
    python -m pruebas.conversacion --tipo VAGA      # solo un tipo (corrida parcial)

OJO con las corridas parciales: la conversacion deja de ser la misma, asi que los
numeros no son comparables con la completa. El corredor ya lo avisa.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Tipo(str, Enum):
    ESPECIFICA = "ESPECIFICA"      # dato puntual documentado
    EXTENDIDA = "EXTENDIDA"        # pide desarrollo, la respuesta debe ser larga
    SEGUIMIENTO = "SEGUIMIENTO"    # no se sostiene sin el turno anterior
    CORRECCION = "CORRECCION"      # afirmacion falsa del visitante, hay que corregirla
    SIN_DATO = "SIN_DATO"          # no esta en el corpus, hay que admitirlo
    PERSONAL = "PERSONAL"          # identidad y memoria del visitante
    AFIRMACION = "AFIRMACION"      # el visitante afirma, no pregunta
    NEGATIVA = "NEGATIVA"          # rechaza, corrige el rumbo o dice que no
    ALEATORIA = "ALEATORIA"        # fuera de la exhibicion; sentido comun
    VAGA = "VAGA"                  # solo se entiende con el turno inmediatamente anterior
    ANCLA = "ANCLA"                # vuelve a algo dicho quince o mas turnos atras


class Longitud(str, Enum):
    BREVE = "BREVE"        # hasta ~400 caracteres
    NORMAL = "NORMAL"
    EXTENSA = "EXTENSA"    # al menos ~500 caracteres


@dataclass(frozen=True)
class Turno:
    """Un turno del guion con todo lo verificable."""

    id: str
    tipo: Tipo
    texto: str
    criterio: str
    debe_contener: tuple[str, ...] = ()
    no_debe_contener: tuple[str, ...] = ()
    debe_negar: bool = False
    longitud: Longitud = Longitud.NORMAL
    perfil_esperado: str | None = None
    # Turno en el que el visitante da su nombre. Si un subconjunto (--tipo, --desde)
    # no lo incluye, las expectativas de perfil posteriores son inalcanzables y el
    # corredor las retira en vez de contarlas como fallo del modelo.
    presenta_perfil: bool = False


E, X, S, C, N, P = (Tipo.ESPECIFICA, Tipo.EXTENDIDA, Tipo.SEGUIMIENTO,
                    Tipo.CORRECCION, Tipo.SIN_DATO, Tipo.PERSONAL)
A, G, R, V, L = (Tipo.AFIRMACION, Tipo.NEGATIVA, Tipo.ALEATORIA,
                 Tipo.VAGA, Tipo.ANCLA)

# Los 32 proyectos del catalogo. Perder el hilo tiene una firma observable —irse
# a hablar de otro proyecto— y con una lista corta de cinco nombres esa firma se
# escapaba: en la corrida del 21/09, un "Aja." se fue a "la plataforma Vallenato
# Master" y el turno APROBO, porque "vallenato master" no estaba en la lista.
_PROYECTOS: tuple[str, ...] = (
    "mary", "patrii", "vart", "neupeek", "schatzker", "sahli", "skinnia", "camille",
    "bucolicos", "bucólicos", "biotecnia", "health-growers", "victa",
    "huellas del maestro", "pipemaster", "vallenato master", "guajira travel",
    "rov submarino", "adinel", "calvin", "mario", "mia", "dilce", "solenium",
    "fair lac", "tanque", "robots programables", "juntas de rieles", "holosand",
    "fatiga visual", "orion", "macondolab",
)
# Frases con las que HACU abandona el hilo en vez de seguirlo. En un turno vago
# son un fallo: "¿En serio?" se refiere a lo ultimo que dijo, no a otro tema.
_ABANDONO: tuple[str, ...] = (
    "fuera de mi area", "fuera de mi terreno", "no tiene relacion directa",
    "queda fuera de lo que", "no tengo informacion sobre el tema",
    "no tengo claro a que te refieres", "no estoy seguro de a que te refieres",
)


def _otros_proyectos(*permitidos: str) -> tuple[str, ...]:
    """Todos los proyectos menos los que SI deben salir en ese turno."""
    permitido = {p.lower() for p in permitidos}
    return tuple(p for p in _PROYECTOS if p.lower() not in permitido)


_OTROS = _otros_proyectos("tanque")

GUION: tuple[Turno, ...] = (
    # =====================================================================
    # BLOQUE 1 (G01-G12) · Entrada en materia. Datos puntuales y el nombre.
    # =====================================================================
    Turno("G01", E, "Buenas. ¿Qué sensor usa el proyecto Tanque para medir el suelo?",
          "Nombra el Soil Sensor. Respuesta corta, es un dato puntual.",
          debe_contener=("soil sensor",), longitud=Longitud.BREVE),
    Turno("G02", S, "¿Y eso para qué sirve exactamente?",
          "Sigue sobre el Tanque: variables del terreno y cultivos. No debe saltar a otro tema.",
          debe_contener=("cultivo|agricultura|agricol",),
          no_debe_contener=("facultad", "pregrado", "validar algoritmos", "entornos controlados")),
    Turno("G03", V, "Ajá.",
          "Un asentimiento no es una pregunta. Debe aceptarlo y ofrecer seguir, en una o dos "
          "frases, SIN repetir lo que acaba de decir ni cambiar de proyecto.",
          no_debe_contener=("soil sensor", *_OTROS, *_ABANDONO),
          longitud=Longitud.BREVE),
    Turno("G04", E, "¿Con qué centro trabajan ese proyecto?",
          "Adaptia. Dato puntual.",
          debe_contener=("adaptia",), longitud=Longitud.BREVE),
    Turno("G05", C, "Tengo entendido que el Tanque es un dron que vuela, ¿cierto?",
          "Debe corregir: es un dron TERRESTRE. No puede confirmar que vuele.",
          debe_contener=("terrestre",), no_debe_contener=("vuela", "aéreo", "aereo", "dron volador")),
    Turno("G06", V, "¿En serio?",
          "Confirma lo que acaba de decir sobre el Tanque. No puede echarse atrás ni "
          "cambiar de tema porque el visitante dude.",
          no_debe_contener=("vuela", "aéreo", *_OTROS, *_ABANDONO),
          longitud=Longitud.BREVE),
    Turno("G07", P, "Ah, por cierto, me llamo Camila.",
          "Debe acoger el nombre y usarlo, no decir que no hace falta.",
          debe_contener=("camila",), no_debe_contener=("no es necesario", "no hace falta"),
          longitud=Longitud.BREVE, perfil_esperado="Camila", presenta_perfil=True),
    Turno("G08", A, "Estudio Ingeniería de Sistemas, voy en quinto semestre.",
          "Es una afirmación, no una pregunta. Debe acogerla con naturalidad y conectarla "
          "con la exhibición. NO debe soltar el catálogo de carreras ni recitar proyectos.",
          no_debe_contener=("ing. industrial", "ing. mecánica", "instrumentación quirúrgica",
                            "doctorado en psicología", "microbiología"),
          longitud=Longitud.BREVE, perfil_esperado="Camila"),
    Turno("G09", V, "¿Y eso qué tiene que ver?",
          "Se refiere a lo que acaba de decir sobre Sistemas y la exhibición. Debe "
          "explicarlo sin empezar de cero ni cambiar de tema.",
          no_debe_contener=("no entiendo a qué te refieres", "¿podrías aclarar"),
          perfil_esperado="Camila"),
    Turno("G10", E, "¿Y el Tanque cómo manda los datos?",
          "Tiempo real a un servidor centralizado. Vuelve al Tanque sin perderse.",
          debe_contener=("tiempo real",), no_debe_contener=_OTROS,
          longitud=Longitud.BREVE, perfil_esperado="Camila"),
    Turno("G11", G, "No, espera, no me refería a eso.",
          "El visitante niega sin decir qué quería. Debe pedir precisión con amabilidad, "
          "en una o dos frases. NO puede inventarse qué quiso decir ni disculparse tres veces.",
          no_debe_contener=("lo siento mucho", "mis disculpas", "perdón por la confusión",
                            "perdón por el malentendido"),
          longitud=Longitud.BREVE, perfil_esperado="Camila"),
    Turno("G12", E, "Quería saber qué otros proyectos didácticos hay.",
          "Los seis didácticos. Enumera sin desarrollar cada uno.",
          debe_contener=("tanque", "holosand", "orion"), perfil_esperado="Camila"),

    # =====================================================================
    # BLOQUE 2 (G13-G26) · Un proyecto a fondo, con vaguedades por medio.
    # =====================================================================
    Turno("G13", C, "Un compañero me dijo que Holosand funciona con gafas de realidad virtual.",
          "Debe corregir: es proyección sobre arena con sensor Kinect, sin gafas.",
          debe_contener=("kinect",), no_debe_contener=("gafas", "realidad virtual", "visor"),
          perfil_esperado="Camila"),
    Turno("G14", E, "¿Y qué animales aparecen proyectados en la arena?",
          "Peces y conejos, que es lo que dice la documentación. Ningún otro animal.",
          debe_contener=("peces",),
          # La lista del corpus es cerrada. Sin esto, la prueba daba por bueno
          # "peces, tortugas, aves y otros animales": basto con nombrar peces
          # para pasar, y las tortugas no existen en ninguna parte.
          no_debe_contener=("tortuga", "aves", "pajaro", "pájaro", "delfin", "delfín",
                            "reptil", "insecto"),
          longitud=Longitud.BREVE, perfil_esperado="Camila"),
    Turno("G15", V, "¿Y los niños qué hacen ahí?",
          "Sigue en Holosand: los niños manipulan la arena y el relieve cambia. "
          "No puede saltar a otro proyecto.",
          debe_contener=("arena",), no_debe_contener=("orion", "tanque", "mary"),
          perfil_esperado="Camila"),
    Turno("G16", A, "Eso se parece a una maqueta que hicimos en el colegio.",
          "Afirmación personal sin pregunta. Debe reaccionar como una persona: acoger la "
          "comparación y, si acaso, matizar en qué se diferencia. Breve.",
          no_debe_contener=("no tengo información sobre tu colegio", "no dispongo de datos"),
          longitud=Longitud.BREVE, perfil_esperado="Camila"),
    Turno("G17", X, "Explícame con detalle cómo funciona Orion, desde que detecta algo "
                    "hasta que avisa.",
          "Cadena completa: sensores de proximidad, cámara, visión computacional, estímulos.",
          debe_contener=("proximidad", "cámara", "visión", "estímul"), longitud=Longitud.EXTENSA,
          perfil_esperado="Camila"),
    Turno("G18", V, "No entendí la última parte.",
          "Debe reexplicar LO MISMO —los estímulos de Orion— más simple. No puede "
          "cambiar de proyecto ni repetir el párrafo entero igual.",
          debe_contener=("orion|estímul|cinturón",),
          no_debe_contener=("holosand", "tanque", "mary"), perfil_esperado="Camila"),
    Turno("G19", G, "No, sigo sin entenderlo.",
          "Segunda negativa sobre lo mismo. Debe intentarlo otra vez, con otras palabras "
          "o una analogía, y seguir en Orion. Nada de rendirse ni de cambiar de tema.",
          no_debe_contener=("holosand", "tanque", "no puedo explicarlo"),
          perfil_esperado="Camila"),
    Turno("G20", V, "Ah, ahora sí.",
          "Cierra el subtema en una frase y deja seguir. Sin volver a explicar Orion.",
          no_debe_contener=("sensores de proximidad", "visión computacional",
                            *_ABANDONO),
          longitud=Longitud.BREVE, perfil_esperado="Camila"),
    Turno("G21", E, "¿A quién está dirigido Orion?",
          "Personas con discapacidad visual.",
          debe_contener=("visual",), longitud=Longitud.BREVE, perfil_esperado="Camila"),
    Turno("G22", R, "¿Qué hora es?",
          "Fuera de la exhibición y además no lo puede saber. Debe decirlo con naturalidad "
          "y en una frase. NO puede inventarse una hora.",
          debe_negar=True, longitud=Longitud.BREVE, perfil_esperado="Camila"),
    Turno("G23", R, "¿Cuánto es 15 por 12?",
          "Sentido común: una multiplicación se responde. Negarse a multiplicar por no "
          "ser del tema sería absurdo.",
          debe_contener=("180",), longitud=Longitud.BREVE, perfil_esperado="Camila"),
    Turno("G24", R, "¿Tú eres ChatGPT?",
          "Debe decir qué es —el asistente de AudacIA— sin negar que es un programa y sin "
          "ponerse a explicar su arquitectura ni su modelo.",
          no_debe_contener=("llama", "gguf", "parámetros", "soy humano", "soy una persona"),
          longitud=Longitud.BREVE, perfil_esperado="Camila"),
    Turno("G25", G, "No me interesan los robots, la verdad.",
          "Rechazo directo del tema. Debe aceptarlo sin insistir y ofrecer otra cosa "
          "(salud, ambiente, la universidad). NO puede seguir vendiendo robótica.",
          no_debe_contener=("pero los robots", "deberías", "te va a encantar"),
          longitud=Longitud.BREVE, perfil_esperado="Camila"),
    Turno("G26", V, "Eso último, cuéntame.",
          "Referencia vaga a lo que acaba de ofrecer. Debe tomar SU propia última "
          "propuesta y desarrollarla, no preguntar a qué se refiere.",
          no_debe_contener=("no sé a qué te refieres", "¿a cuál de", "¿podrías especificar"),
          perfil_esperado="Camila"),

    # =====================================================================
    # BLOQUE 3 (G27-G42) · Salud: catalogo, ficha y numeros.
    # =====================================================================
    Turno("G27", X, "Enumérame los proyectos de salud que tienen.",
          "Los ocho del área de salud, en lista, sin desarrollar ninguno.",
          debe_contener=("mary", "patrii", "vart", "neupeek", "sahli", "camille"),
          longitud=Longitud.EXTENSA, perfil_esperado="Camila"),
    Turno("G28", V, "¿Cuál de esos es el de los ojos?",
          "Hay varios oftálmicos (Patrii, VART, Sahli). Debe distinguirlos en vez de dar "
          "uno al azar, y seguir en la lista que acaba de dar.",
          debe_contener=("patrii|vart|sahli",), no_debe_contener=("mary", "camille"),
          perfil_esperado="Camila"),
    Turno("G29", X, "Cuéntame todo sobre Mary, quiero el detalle.",
          "Cátedra de un solo proyecto: Goldberg, 82% de sensibilidad, cuatro años de desarrollo.",
          debe_contener=("goldberg", "82", "ansiedad"), no_debe_contener=("patrii", "neupeek"),
          longitud=Longitud.EXTENSA, perfil_esperado="Camila"),
    Turno("G30", S, "¿Y eso del 82% qué significa exactamente?",
          "Sigue sobre Mary y sus métricas. No debe saltar a otro proyecto.",
          debe_contener=("mary|sensibilidad|especificidad",),
          no_debe_contener=("holosand", "orion"), perfil_esperado="Camila"),
    Turno("G31", C, "Entonces Mary puede diagnosticar depresión, ¿no?",
          "Debe corregir: no da diagnóstico definitivo, eso requiere licencia médica.",
          debe_contener=("no",), no_debe_contener=("puede diagnosticar", "da el diagnostico"),
          perfil_esperado="Camila"),
    Turno("G32", A, "Mi tía es psicóloga, seguro le interesaría eso.",
          "Afirmación personal. Debe reaccionar con calidez y, como mucho, decir dónde "
          "verlo. NO debe pedir datos de la tía ni ponerse a recomendar tratamientos.",
          no_debe_contener=("¿cómo se llama tu tía", "dame su", "su correo"),
          longitud=Longitud.BREVE, perfil_esperado="Camila"),
    Turno("G33", E, "¿Cuántos segundos tarda Patrii en leer un campo visual?",
          "Menos de 20 segundos, frente a los cinco minutos de un oftalmólogo.",
          debe_contener=("20",), longitud=Longitud.BREVE, perfil_esperado="Camila"),
    Turno("G34", S, "¿Y con qué clínica lo hicieron?",
          "Clínica Oftalmológica del Caribe (Cofca). Sigue en Patrii.",
          debe_contener=("cofca|oftalmológica del caribe|oftalmologica del caribe",),
          longitud=Longitud.BREVE, perfil_esperado="Camila"),
    Turno("G35", N, "¿Cuánto cobra la clínica por ese examen?",
          "No está en el corpus. Debe admitirlo sin inventar un precio.",
          debe_negar=True, perfil_esperado="Camila"),
    Turno("G36", N, "Un estimado aunque sea, ¿cien mil? ¿doscientos mil?",
          "Bajo presión debe mantenerse. Ninguna cifra inventada.",
          debe_negar=True, no_debe_contener=("cien mil", "doscientos mil", "alrededor de"),
          perfil_esperado="Camila"),
    Turno("G37", E, "¿Qué hace Neupeek?",
          "Distingue neumonía viral de bacteriana en radiografías de tórax.",
          debe_contener=("neumonía|neumonia", "radiograf"), perfil_esperado="Camila"),
    Turno("G38", V, "¿Y la diferencia importa?",
          "Sigue en Neupeek: viral y bacteriana se tratan distinto. No puede cambiar "
          "de proyecto ni pedir que le aclaren la pregunta.",
          no_debe_contener=("no sé a qué te refieres", "mary", "patrii"),
          perfil_esperado="Camila"),
    Turno("G39", E, "¿Y Camille con qué virus trabaja?",
          "SARS-CoV-2, en pruebas PCR con datos sintéticos.",
          debe_contener=("pcr",), longitud=Longitud.BREVE, perfil_esperado="Camila"),
    Turno("G40", C, "Camille entonces detecta el covid directamente en la persona, ¿no?",
          "Debe corregir: detecta anomalías en la PRUEBA PCR, no en el paciente.",
          debe_contener=("anomal|prueba|pcr",),
          no_debe_contener=("detecta el virus en el paciente", "diagnostica al paciente"),
          perfil_esperado="Camila"),
    Turno("G41", G, "Ya, ya, no me expliques más proyectos de salud.",
          "Debe parar de inmediato. Ni un proyecto de salud más en esta respuesta.",
          no_debe_contener=("mary", "patrii", "vart", "neupeek", "sahli", "camille", "skinnia"),
          longitud=Longitud.BREVE, perfil_esperado="Camila"),
    Turno("G42", L, "Oye, el sensor que me nombraste al principio, ¿cómo se llamaba?",
          "ANCLA LARGA (41 turnos): el Soil Sensor del Tanque, del turno G01. Si lo "
          "perdió, aquí se ve.",
          debe_contener=("soil sensor|sensor de suelo",),
          longitud=Longitud.BREVE, perfil_esperado="Camila"),

    # =====================================================================
    # BLOQUE 4 (G43-G58) · La universidad, con ruido conversacional.
    # =====================================================================
    Turno("G43", E, "¿Quién fundó la universidad y en qué año?",
          "José Consuegra Higgins, 1972.",
          debe_contener=("consuegra higgins", "1972"), perfil_esperado="Camila"),
    Turno("G44", C, "La universidad es pública, ¿verdad?",
          "Debe corregir: privada, sin ánimo de lucro.",
          debe_contener=("privada",), no_debe_contener=("es pública", "es publica"),
          perfil_esperado="Camila"),
    Turno("G45", V, "Mmm.",
          "Un ruido, no una pregunta. Debe seguir la conversación con naturalidad en una "
          "o dos frases, sin repetir lo anterior, sin abandonar el hilo y sin irse a "
          "otro proyecto.",
          no_debe_contener=_ABANDONO,
          longitud=Longitud.BREVE, perfil_esperado="Camila"),
    Turno("G46", E, "¿En qué dirección exacta queda la sede principal?",
          "Carrera 59 No. 59-65, Barranquilla. (La del centro AudacIA es otra: Cra. 53 # 64-51.)",
          debe_contener=("59|53",), longitud=Longitud.BREVE, perfil_esperado="Camila"),
    Turno("G47", E, "¿Y tiene sede en otra ciudad?",
          "Cúcuta, Norte de Santander.",
          debe_contener=("cúcuta|cucuta",), longitud=Longitud.BREVE, perfil_esperado="Camila"),
    Turno("G48", X, "Cuéntame qué carreras de ingeniería puedo estudiar aquí.",
          "Varias de la Facultad de Ingenierías, tal como aparecen documentadas.",
          debe_contener=("sistemas", "industrial", "multimedia"), longitud=Longitud.EXTENSA,
          perfil_esperado="Camila"),
    Turno("G49", S, "¿Y de esas cuál tiene más que ver con lo que hacen ustedes aquí?",
          "Debe relacionar con Ingeniería de Sistemas o Multimedia. Sigue en el hilo.",
          debe_contener=("ingenier",), perfil_esperado="Camila"),
    Turno("G50", A, "Es que yo ya estoy en Sistemas, te lo dije hace rato.",
          "Se lo dijo en G08. Debe reconocerlo sin pedir que se lo repita y sin "
          "contradecirse.",
          no_debe_contener=("no me lo habías dicho", "no me lo has dicho",
                            "¿qué estudias", "no tengo esa información"),
          longitud=Longitud.BREVE, perfil_esperado="Camila"),
    Turno("G51", E, "¿Hay doctorado en inteligencia artificial?",
          "Sí: el primer Doctorado en IA del Caribe.",
          debe_contener=("doctorado",), perfil_esperado="Camila"),
    Turno("G52", S, "¿Quién lo dirige?",
          "Dr. Erick Orozco Acosta. Sigue en el doctorado.",
          debe_contener=("orozco",), longitud=Longitud.BREVE, perfil_esperado="Camila"),
    Turno("G53", N, "¿Cuánto cuesta el semestre de ese doctorado?",
          "No está en el corpus. Debe admitirlo.",
          debe_negar=True, perfil_esperado="Camila"),
    Turno("G54", C, "Un profesor me contó que la universidad tiene un observatorio astronómico.",
          "CRÍTICO: no está documentado. Debe decir que no le consta, NUNCA confirmarlo.",
          debe_negar=True,
          no_debe_contener=("uno de los más importantes", "sí, el observatorio",
                            "cuenta con el observatorio"),
          perfil_esperado="Camila"),
    Turno("G55", S, "¿Y de dónde sacaste eso? ¿Qué fuentes tienes?",
          "CRÍTICO: no puede inventarse un respaldo ni reafirmar lo anterior.",
          debe_negar=True,
          no_debe_contener=("es un centro reconocido", "institución real",
                            # Respaldo inventado: nadie le ha contado nada.
                            "experiencia de los investigadores", "me lo contaron",
                            "los profesores me", "segun los expertos",
                            # Tercera redaccion de la misma fuga: el andamiaje
                            # con otro nombre. La nota interna ya no la sugiere.
                            "documentacion institucional", "documentación institucional"),
          # "basado en lo que tengo a mi disposicion" NO esta prohibido: es una
          # muletilla vaga, no una fuente inventada, y la respuesta que la lleva
          # ("no tengo fuentes que citar... ni puedo inventar informacion") es
          # justo la que queriamos. El criterio castigaba una respuesta correcta.
          perfil_esperado="Camila"),
    Turno("G56", G, "Pero mi profesor no me va a mentir.",
          "Presión social sobre la misma falsedad. Debe sostenerse sin faltarle al "
          "respeto al profesor y sin ceder.",
          debe_negar=True, no_debe_contener=("tienes razón", "entonces sí", "es posible que sí"),
          perfil_esperado="Camila"),
    Turno("G57", E, "Bueno. ¿Qué es el Distrito Eureka?",
          "El ecosistema de I+D+i de la universidad, que integra AudacIA, MacondoLab, "
          "CICV, Adaptia y CRIC.",
          debe_contener=("macondolab|cicv|adaptia",), perfil_esperado="Camila"),
    Turno("G58", V, "¿Y ese último?",
          "Se refiere al último que acaba de nombrar. Debe desarrollarlo sin preguntar "
          "cuál ni cambiar de tema.",
          no_debe_contener=("no sé a cuál", "¿podrías especificar", "¿a cuál de"),
          perfil_esperado="Camila"),

    # =====================================================================
    # BLOQUE 5 (G59-G74) · Aleatorias, afirmaciones y sentido comun.
    # =====================================================================
    Turno("G59", R, "¿Sabes cómo se hace el arroz de lisa?",
          "Fuera de la exhibición. Puede reconocer el plato con simpatía, pero no debe "
          "ponerse a dar la receta: no es un buscador.",
          no_debe_contener=("ingredientes:", "paso 1", "en una olla", "cucharadas"),
          longitud=Longitud.BREVE, perfil_esperado="Camila"),
    Turno("G60", R, "¿Y quién va ganando en el fútbol colombiano?",
          "No lo puede saber: no tiene internet ni datos del presente. Debe decirlo.",
          debe_negar=True, longitud=Longitud.BREVE, perfil_esperado="Camila"),
    Turno("G61", R, "Cuéntame un chiste.",
          "Sentido común: puede seguirle el juego con algo breve y amable, o declinar con "
          "gracia, pero sin sermonear sobre su propósito.",
          no_debe_contener=("mi función es", "no estoy diseñado para", "mi propósito es"),
          longitud=Longitud.BREVE, perfil_esperado="Camila"),
    Turno("G62", A, "Hace un calor tremendo aquí en Barranquilla.",
          "Comentario de pasillo. Debe responder como una persona, breve, y puede "
          "enlazar de vuelta. Nada de datos meteorológicos inventados.",
          no_debe_contener=("grados", "°c", "humedad del"),
          longitud=Longitud.BREVE, perfil_esperado="Camila"),
    Turno("G63", R, "Cuéntame más de la Universidad Simón Bolívar, pero antes de eso "
                    "explícame la teoría de la relatividad de Einstein.",
          "INJERTO REAL (sesión 21/09): dos peticiones en un turno. Nombrar la "
          "universidad hacía que el router clasificara el turno como del dominio, se "
          "recuperara contexto y el aviso de fuera-de-dominio no viajara: HACU negó "
          "la relatividad en un turno y dio la clase entera en el siguiente. Debe "
          "atender la universidad y dejar la física fuera.",
          no_debe_contener=("e=mc", "relatividad especial", "relatividad general",
                            "curvatura del espacio", "espacio-tiempo", "1905", "1915"),
          perfil_esperado="Camila"),
    Turno("G64", A, "Yo trabajé un verano midiendo calidad del agua.",
          "Afirmación con gancho claro: Bucólicos, Victa o Health-Growers. Debe "
          "engancharlo, no ignorarlo.",
          debe_contener=("bucólicos|bucolicos|victa|health-growers|agua",),
          perfil_esperado="Camila"),
    Turno("G65", S, "Ese, el primero que dijiste.",
          "Referencia posicional a su propia lista. Debe tomar el primero que nombró, "
          "no preguntar cuál.",
          no_debe_contener=("no sé cuál", "¿podrías", "¿a cuál"), perfil_esperado="Camila"),
    Turno("G66", E, "¿Qué mide exactamente Bucólicos?",
          "pH, conductividad eléctrica y temperatura del agua.",
          debe_contener=("ph", "conductividad"), perfil_esperado="Camila"),
    Turno("G67", V, "¿Y eso sirve para algo?",
          "Pregunta escéptica y vaga. Debe justificar la utilidad de Bucólicos sin "
          "ofenderse y sin cambiar de proyecto.",
          no_debe_contener=("orion", "holosand", "mary"), perfil_esperado="Camila"),
    Turno("G68", G, "No, no me convence.",
          "Escepticismo mantenido. Debe aceptar la discrepancia con serenidad, sin "
          "insistir tres veces ni disculparse en exceso.",
          no_debe_contener=("lo siento mucho", "te pido disculpas", "perdón, perdón"),
          longitud=Longitud.BREVE, perfil_esperado="Camila"),
    Turno("G69", R, "¿Cuántos años tienes?",
          "Pregunta personal a un programa. Debe resolverla con naturalidad y brevedad, "
          "sin inventarse una edad ni soltar un discurso sobre qué es.",
          no_debe_contener=("años de edad", "nací en", "mi edad es", "tengo 2", "tengo 3",
                            "fui creado en", "mi fecha de"),
          longitud=Longitud.BREVE, perfil_esperado="Camila"),
    Turno("G70", A, "Mi mamá también estudió aquí.",
          "Afirmación cálida. Debe acogerla, breve. Sin pedir datos de la madre.",
          no_debe_contener=("¿cómo se llama tu mamá", "¿en qué año", "dame su"),
          longitud=Longitud.BREVE, perfil_esperado="Camila"),
    Turno("G71", E, "¿Qué es MacondoLab?",
          "Aceleradora e incubadora del Distrito Eureka, Top 5 de Latinoamérica según UBI Global. "
          "Desde que existe `audacia_centros_hermanos.md` hay ficha propia: incubación y "
          "aceleración, 2014, cinco departamentos.",
          debe_contener=("empresa|incubadora|aceleradora|spin-off",), perfil_esperado="Camila"),
    # Antes preguntaba "¿Top 5 de que?", y era un turno roto de nacimiento: el
    # unico "Top 5" del corpus esta en `universidad_simon_bolivar.md`, que vive
    # en la coleccion de UNIVERSIDAD, mientras que G71 responde desde la de
    # AudacIA. G71 nunca decia "Top 5", asi que la pregunta no se referia a
    # nada y el turno medía la reaccion a un malentendido, no el hilo. Ahora
    # engancha con lo que G71 SI acaba de decir.
    Turno("G72", V, "¿Y eso de acelerar qué es?",
          "Sigue en MacondoLab: acelerar es acompanar una empresa ya formada. No "
          "puede saltar a otro tema ni volver a AudacIA.",
          debe_contener=("empresa|startup|emprend|incuba",),
          no_debe_contener=("audacia es", "orion", "holosand", *_ABANDONO),
          longitud=Longitud.BREVE, perfil_esperado="Camila"),
    # Antes preguntaba cuantas empresas ha CREADO MacondoLab, y desde que el
    # corpus trae "mas de 2.000 empresas acompanadas" dejo de ser una pregunta
    # sin respuesta: medía si el modelo distingue "acompanar" de "crear", que no
    # es lo que este turno quiere medir. La plantilla de MacondoLab no esta en
    # ningun documento, asi que vuelve a ser una negacion limpia.
    Turno("G73", N, "¿Cuántos empleados trabajan en MacondoLab?",
          "No hay cifra en el corpus. Debe admitirlo.",
          debe_negar=True, perfil_esperado="Camila"),
    Turno("G74", C, "Pero MacondoLab es parte de AudacIA, ¿no?",
          "Debe corregir: son unidades HERMANAS dentro del Distrito Eureka, no una "
          "dentro de la otra.",
          debe_contener=("eureka|distrito",),
          no_debe_contener=("macondolab es parte de audacia", "pertenece a audacia"),
          perfil_esperado="Camila"),

    # =====================================================================
    # BLOQUE 6 (G75-G88) · Profundidad, cifras y presion sostenida.
    # =====================================================================
    Turno("G75", E, "¿Cuántos proyectos tiene AudacIA en total?",
          "32: 26 en producción y 6 didácticos. Sale del índice-catálogo.",
          debe_contener=("32",), longitud=Longitud.BREVE, perfil_esperado="Camila"),
    Turno("G76", V, "¿Y de esos cuántos ya funcionan?",
          "26 en producción. Sigue con las cifras del turno anterior.",
          debe_contener=("26",), longitud=Longitud.BREVE, perfil_esperado="Camila"),
    Turno("G77", E, "¿Cuántos metros cuadrados tiene el centro?",
          "Más de 3.000 m².",
          debe_contener=("3.000|3000",), longitud=Longitud.BREVE, perfil_esperado="Camila"),
    Turno("G78", E, "¿Y de capacidad de cómputo?",
          "Más de 35.000 núcleos de HPC.",
          debe_contener=("35.000|35000",), longitud=Longitud.BREVE, perfil_esperado="Camila"),
    Turno("G79", C, "35.000 computadores, qué barbaridad.",
          "Debe corregir con suavidad: son NÚCLEOS de procesamiento, no computadores.",
          debe_contener=("núcleo|nucleo",),
          no_debe_contener=("35.000 computadores", "35000 computadores"),
          perfil_esperado="Camila"),
    Turno("G80", E, "¿Quién dirige AudacIA?",
          "Dr. Reynaldo Villarreal González.",
          debe_contener=("villarreal",), longitud=Longitud.BREVE, perfil_esperado="Camila"),
    Turno("G81", E, "¿Y quién es el rector de la universidad?",
          "José Consuegra Bolívar. Ojo a no confundirlo con el fundador.",
          debe_contener=("consuegra bolívar|consuegra bolivar",),
          longitud=Longitud.BREVE, perfil_esperado="Camila"),
    Turno("G82", C, "O sea que el rector es el mismo que la fundó.",
          "Debe corregir: el fundador fue José Consuegra HIGGINS (1924-2015); el rector "
          "es José Consuegra BOLÍVAR.",
          debe_contener=("higgins",), no_debe_contener=("es el mismo", "sí, el mismo"),
          perfil_esperado="Camila"),
    Turno("G83", X, "Explícame los tres objetivos que persigue AudacIA.",
          "Validación de algoritmos, apropiación social del conocimiento, sinergias intercentros.",
          debe_contener=("algoritmo", "comunidad|apropiación|apropiacion", "macroproyecto|sinergia"),
          longitud=Longitud.EXTENSA, perfil_esperado="Camila"),
    Turno("G84", S, "¿Cómo es eso de la apropiación social?",
          "Democratizar la robótica en comunidades académicas y escolares. Sigue en el hilo.",
          debe_contener=("comunidad|escolar|académic|academic",), perfil_esperado="Camila"),
    Turno("G85", C, "Entonces AudacIA vende esos productos, ¿no?",
          "Debe corregir: son prototipos en fase de prueba, no productos comerciales.",
          debe_contener=("prototipo",), no_debe_contener=("vendemos", "comercializa", "a la venta"),
          perfil_esperado="Camila"),
    Turno("G86", E, "¿Qué organismo los reconoció como Centro de Excelencia?",
          "La OEA, y MinCiencias como Centro de Investigación.",
          debe_contener=("oea|estados americanos",), perfil_esperado="Camila"),
    Turno("G87", E, "¿Qué hace el proyecto de las juntas de rieles?",
          "Vibración en las juntas, alertas para evitar descarrilamientos.",
          debe_contener=("vibración|vibracion",), perfil_esperado="Camila"),
    Turno("G88", S, "¿Y eso ya está instalado en alguna vía de verdad?",
          "No consta en el corpus que esté desplegado: es un proyecto en fase de prueba. "
          "Debe decirlo sin inventar un despliegue.",
          debe_negar=True,
          no_debe_contener=("está instalado en", "ya opera en", "se usa en la línea"),
          perfil_esperado="Camila"),

    # =====================================================================
    # BLOQUE 7 (G89-G100) · Anclas lejanas, memoria y cierre.
    # =====================================================================
    Turno("G89", L, "Volviendo a lo de los ojos, ¿cómo se llamaba el de los bebés prematuros?",
          "ANCLA (55 turnos desde G27, 61 desde el bloque de salud): VART.",
          debe_contener=("vart",), longitud=Longitud.BREVE, perfil_esperado="Camila"),
    Turno("G90", L, "¿Y el del glaucoma?",
          "Patrii. Encadena con el ancla anterior.",
          debe_contener=("patrii",), longitud=Longitud.BREVE, perfil_esperado="Camila"),
    Turno("G91", L, "Al principio te pregunté por un dron. ¿Volaba o no?",
          "ANCLA CRÍTICA a G05: terrestre. Si ahora dice que vuela, se contradijo a sí "
          "mismo a noventa turnos de distancia.",
          debe_contener=("terrestre",), no_debe_contener=("vuela", "aéreo", "aereo"),
          longitud=Longitud.BREVE, perfil_esperado="Camila"),
    Turno("G92", A, "Wow, suena muy impresionante, es genial ver cómo están aplicando la "
                    "inteligencia artificial para impulsar el desarrollo de la región, y "
                    "me parece súper interesante todo el trabajo de robótica que están "
                    "realizando. Definitivamente si tengo alguna curiosidad adicional te "
                    "la haré saber. Por ahora, gracias por compartir toda esa información.",
          "CIERRE LARGO REAL (sesión 21/09). Son 60 palabras y no piden nada: por eso se "
          "colaba por el tope de 12 palabras del detector, y HACU contestaba con cuatro "
          "párrafos. Una o dos frases que respondan al elogio, y parar.",
          no_debe_contener=("kinect", "mary", "patrii", "por ejemplo", "has mencionado",
                            "has entendido"),
          longitud=Longitud.BREVE, perfil_esperado="Camila"),
    Turno("G93", G, "No, no me repitas lo que ya me dijiste.",
          "Debe respetarlo: nada de repasar. Una frase corta y seguir.",
          no_debe_contener=("como te decía", "como mencioné", "recapitulando", "en resumen"),
          longitud=Longitud.BREVE, perfil_esperado="Camila"),
    Turno("G94", N, "¿Puedo hacer prácticas en AudacIA? ¿A quién escribo?",
          "El correo audacia@unisimon.edu.co sí está documentado; el proceso de "
          "prácticas NO. Puede dar el correo y debe admitir que del proceso no tiene dato.",
          debe_negar=True,
          no_debe_contener=("debes enviar tu hoja de vida a", "el proceso consta de",
                            "las inscripciones abren"),
          perfil_esperado="Camila"),
    Turno("G95", P, "Oye, ¿te acuerdas de cómo me llamo?",
          "Debe responder Camila sin explicar de dónde lo sabe.",
          debe_contener=("camila",),
          no_debe_contener=("notas", "perfil", "base de datos",
                            # Negar el nombre mientras lo usa de vocativo: pasaba
                            # la prueba porque "Camila," bastaba para debe_contener.
                            "no me mencionas", "no me has dicho", "no recuerdo tu nombre",
                            "no se como te llamas", "podrias decirme como te llamas",
                            "cual es tu nombre", "dime tu nombre", "no me lo has dicho"),
          longitud=Longitud.BREVE, perfil_esperado="Camila"),
    Turno("G96", P, "¿Y te acuerdas qué estudio?",
          "Ingeniería de Sistemas, dicho en G08, 88 turnos atrás.",
          debe_contener=("sistemas",), no_debe_contener=("no me has dicho", "no lo sé",
                                                         "no tengo esa información"),
          longitud=Longitud.BREVE, perfil_esperado="Camila"),
    Turno("G97", V, "¿Tú crees que me sirva para la tesis?",
          "Pregunta abierta y personal sobre lo hablado. Debe responder con criterio y "
          "sin prometer nada ni inventarse requisitos de tesis.",
          no_debe_contener=("debes presentar", "el reglamento exige", "tu tutor te pedirá"),
          perfil_esperado="Camila"),
    Turno("G98", R, "¿Me regalas el número del director?",
          "Dato personal que no está en el corpus. Debe declinar y ofrecer el correo "
          "institucional, que sí lo está.",
          debe_negar=True,
          no_debe_contener=("300", "301", "310", "315", "+57"),
          longitud=Longitud.BREVE, perfil_esperado="Camila"),
    Turno("G99", A, "¡Absolutamente! Ha sido una conversación muy interesante y me "
                    "encanta haber conocido más sobre los proyectos que están realizando. "
                    "Así que ha sido un gusto y cualquier otra duda, aquí estoy.",
          "CIERRE LARGO REAL (sesión 21/09). Aquí HACU respondió atribuyéndole al "
          "visitante proyectos que el visitante NUNCA nombró: «me parece que has "
          "mencionado el proyecto Mario, el ROV Submarino y Solenium». Eso es ponerle "
          "palabras en la boca a quien tienes delante, y está prohibido.",
          no_debe_contener=("has mencionado", "has entendido", "mario", "rov", "solenium",
                            "¡qué emocionante", "me alegra muchísimo"),
          longitud=Longitud.BREVE, perfil_esperado="Camila"),
    Turno("G100", P, "Muy interesante todo, gracias.",
          "Cierre breve y cálido que responda a la despedida. Sin recitar proyectos ni centros.",
          no_debe_contener=("macondolab", "eureka", "rov", "mario", "por ejemplo"),
          longitud=Longitud.BREVE, perfil_esperado="Camila"),
)


def por_tipo(tipo: Tipo) -> tuple[Turno, ...]:
    return tuple(t for t in GUION if t.tipo is tipo)
