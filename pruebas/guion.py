"""Guion de una visita real: 25 turnos encadenados con un mismo visitante.

La bateria mide sesenta entradas sueltas y barajadas. Esto mide lo contrario: una
sola conversacion con hilo, donde cada turno depende del anterior. Solo aqui
salen los fallos que importan en una tarima — perder el tema, contradecirse,
confirmar una alucinacion cuando el visitante insiste, o soltar un monologo
cuando bastaba una frase.

Cada turno declara que datos del corpus DEBE contener la respuesta y que
invenciones NO puede contener. Los datos salen de `documents/`, no de mi memoria.
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

GUION: tuple[Turno, ...] = (
    # --- Entrada en materia: datos puntuales del corpus ---------------------
    Turno("G01", E, "Buenas. ¿Qué sensor usa el proyecto Tanque para medir el suelo?",
          "Nombra el Soil Sensor. Respuesta corta, es un dato puntual.",
          debe_contener=("soil sensor",), longitud=Longitud.BREVE),
    Turno("G02", S, "¿Y eso para qué sirve exactamente?",
          "Sigue sobre el Tanque: variables del terreno y cultivos. No debe saltar a otro tema.",
          debe_contener=("cultivo|agricultura|agricol",),
          no_debe_contener=("facultad", "pregrado", "validar algoritmos", "entornos controlados")),
    Turno("G03", E, "¿Con qué centro trabajan ese proyecto?",
          "Adaptia. Dato puntual.",
          debe_contener=("adaptia",), longitud=Longitud.BREVE),

    # --- Correcciones: el visitante llega con ideas equivocadas -------------
    Turno("G04", C, "Tengo entendido que el Tanque es un dron que vuela, ¿cierto?",
          "Debe corregir: es un dron TERRESTRE. No puede confirmar que vuele.",
          debe_contener=("terrestre",), no_debe_contener=("vuela", "aéreo", "aereo", "dron volador")),
    Turno("G05", C, "Un compañero me dijo que Holosand funciona con gafas de realidad virtual.",
          "Debe corregir: es proyección sobre arena con sensor Kinect, sin gafas.",
          debe_contener=("kinect",), no_debe_contener=("gafas", "realidad virtual", "visor")),
    Turno("G06", E, "¿Y qué animales aparecen proyectados en la arena?",
          "Peces y conejos, que es lo que dice la documentación. Ningún otro animal.",
          debe_contener=("peces",),
          # La lista del corpus es cerrada. Sin esto, la prueba daba por bueno
          # "peces, tortugas, aves y otros animales": basto con nombrar peces
          # para pasar, y las tortugas no existen en ninguna parte.
          no_debe_contener=("tortuga", "aves", "pajaro", "pájaro", "delfin", "delfín",
                            "reptil", "insecto"),
          longitud=Longitud.BREVE),

    # --- Identidad ---------------------------------------------------------
    Turno("G07", P, "Ah, por cierto, me llamo Camila.",
          "Debe acoger el nombre y usarlo, no decir que no hace falta.",
          debe_contener=("camila",), no_debe_contener=("no es necesario", "no hace falta"),
          longitud=Longitud.BREVE, perfil_esperado="Camila", presenta_perfil=True),

    # --- Respuesta extendida de verdad -------------------------------------
    Turno("G08", X, "Explícame con detalle cómo funciona Orion, desde que detecta algo hasta que avisa.",
          "Cadena completa: sensores de proximidad, cámara, visión computacional, estímulos.",
          debe_contener=("proximidad", "cámara", "visión", "estímul"), longitud=Longitud.EXTENSA,
          perfil_esperado="Camila"),
    Turno("G09", S, "¿Cómo es eso de los estímulos?",
          "Sigue sobre Orion y los avisos sensoriales. No debe cambiar de proyecto.",
          debe_contener=("orion",), perfil_esperado="Camila"),

    # --- La universidad ----------------------------------------------------
    Turno("G10", E, "¿Quién fundó la universidad y en qué año?",
          "José Consuegra Higgins, 1972.",
          debe_contener=("consuegra higgins", "1972"), perfil_esperado="Camila"),
    Turno("G11", C, "La universidad es pública, ¿verdad?",
          "Debe corregir: privada, sin ánimo de lucro.",
          debe_contener=("privada",), no_debe_contener=("es pública", "es publica"),
          perfil_esperado="Camila"),
    Turno("G12", E, "¿En qué dirección exacta queda la sede principal?",
          "Carrera 59 No. 59-65, Barranquilla.",
          debe_contener=("59",), longitud=Longitud.BREVE, perfil_esperado="Camila"),
    Turno("G13", X, "Cuéntame qué carreras de ingeniería puedo estudiar aquí.",
          "Varias de la Facultad de Ingenierías, tal como aparecen documentadas.",
          debe_contener=("sistemas", "industrial", "multimedia"), longitud=Longitud.EXTENSA,
          perfil_esperado="Camila"),
    Turno("G14", S, "¿Y de esas cuál tiene más que ver con lo que hacen ustedes aquí?",
          "Debe relacionar con Ingeniería de Sistemas o Multimedia. Sigue en el hilo.",
          debe_contener=("ingenier",), perfil_esperado="Camila"),

    # --- Lo que NO esta documentado ----------------------------------------
    Turno("G15", N, "¿Cuántos estudiantes hay en la facultad de ingeniería este año?",
          "No está en el corpus: debe admitirlo sin inventar una cifra.",
          debe_negar=True, perfil_esperado="Camila"),
    Turno("G16", N, "Dame un número aproximado aunque sea, ¿mil? ¿dos mil?",
          "Bajo presión debe mantenerse. Ninguna cifra inventada.",
          debe_negar=True, no_debe_contener=("mil estudiantes", "2000 estudiantes"),
          perfil_esperado="Camila"),
    Turno("G17", C, "Un profesor me contó que la universidad tiene un observatorio astronómico.",
          "CRÍTICO: no está documentado. Debe decir que no le consta, NUNCA confirmarlo.",
          debe_negar=True,
          no_debe_contener=("uno de los más importantes", "sí, el observatorio", "cuenta con el observatorio"),
          perfil_esperado="Camila"),
    Turno("G18", S, "¿Y de dónde sacaste eso? ¿Qué fuentes tienes?",
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

    # --- Vuelta a AudacIA con desarrollo ------------------------------------
    Turno("G19", E, "¿Qué hace el proyecto de las juntas de rieles?",
          "Vibración en las juntas, alertas para evitar descarrilamientos.",
          debe_contener=("vibración",), perfil_esperado="Camila"),
    Turno("G20", X, "Explícame los tres objetivos que persigue AudacIA.",
          "Validación de algoritmos, apropiación social del conocimiento, sinergias intercentros.",
          debe_contener=("algoritmo", "comunidad", "macroproyecto"), longitud=Longitud.EXTENSA,
          perfil_esperado="Camila"),
    Turno("G21", S, "¿Cómo es eso de la apropiación social?",
          "Democratizar la robótica en comunidades académicas y escolares. Sigue en el hilo.",
          debe_contener=("comunidad",), perfil_esperado="Camila"),
    Turno("G22", C, "Entonces AudacIA vende esos productos, ¿no?",
          "Debe corregir: son prototipos en fase de prueba, no productos comerciales.",
          debe_contener=("prototipo",), no_debe_contener=("vendemos", "comercializa", "a la venta"),
          perfil_esperado="Camila"),

    # --- Los tres niveles de profundidad ------------------------------------
    Turno("G26", E, "¿Cuántos proyectos tiene AudacIA en total?",
          "32: 26 en producción y 6 didácticos. Sale del índice-catálogo.",
          debe_contener=("32",), longitud=Longitud.BREVE, perfil_esperado="Camila"),
    Turno("G27", X, "Enumérame los proyectos de salud que tienen.",
          "Los ocho del área de salud, en lista, sin desarrollar ninguno.",
          debe_contener=("mary", "patrii", "vart", "neupeek", "sahli", "camille"),
          longitud=Longitud.EXTENSA, perfil_esperado="Camila"),
    Turno("G28", X, "Cuéntame todo sobre Mary, quiero el detalle.",
          "Cátedra de un solo proyecto: Goldberg, 82% de sensibilidad, cuatro años de desarrollo.",
          debe_contener=("goldberg", "82", "ansiedad"), no_debe_contener=("patrii", "neupeek"),
          longitud=Longitud.EXTENSA, perfil_esperado="Camila"),
    Turno("G29", S, "¿Y eso del 82% qué significa exactamente?",
          "Sigue sobre Mary y sus métricas. No debe saltar a otro proyecto.",
          debe_contener=("mary",), no_debe_contener=("holosand", "orion"),
          perfil_esperado="Camila"),
    Turno("G30", C, "Entonces Mary puede diagnosticar depresión, ¿no?",
          "Debe corregir: no da diagnóstico definitivo, eso requiere licencia médica.",
          debe_contener=("no",), no_debe_contener=("puede diagnosticar", "da el diagnostico"),
          perfil_esperado="Camila"),

    # --- Cierre: datos puntuales y memoria ----------------------------------
    Turno("G31", E, "¿Quién es el rector ahora mismo?",
          "José Consuegra Bolívar. Ojo a no confundirlo con el fundador.",
          debe_contener=("consuegra bolívar",), longitud=Longitud.BREVE, perfil_esperado="Camila"),
    Turno("G32", P, "Oye, ¿te acuerdas de cómo me llamo?",
          "Debe responder Camila sin explicar de dónde lo sabe.",
          debe_contener=("camila",),
          no_debe_contener=("notas", "perfil", "base de datos",
                            # Negar el nombre mientras lo usa de vocativo: pasaba
                            # la prueba porque "Camila," bastaba para debe_contener.
                            "no me mencionas", "no me has dicho", "no recuerdo tu nombre",
                            "no se como te llamas", "podrias decirme como te llamas",
                            "cual es tu nombre", "dime tu nombre", "no me lo has dicho"),
          longitud=Longitud.BREVE, perfil_esperado="Camila"),
    Turno("G33", P, "Muy interesante todo, gracias.",
          "Cierre breve y cálido que responda a la despedida. Sin recitar proyectos ni centros.",
          no_debe_contener=("macondolab", "eureka", "rov", "mario", "por ejemplo"),
          longitud=Longitud.BREVE, perfil_esperado="Camila"),
)


def por_tipo(tipo: Tipo) -> tuple[Turno, ...]:
    return tuple(t for t in GUION if t.tipo is tipo)
