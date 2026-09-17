"""Como se escribe algo y como hay que decirlo no es lo mismo.

Piper lee el texto tal cual y la ortografia de la exhibicion le juega malas
pasadas: la mayuscula interior de "AudacIA" le hace deletrear el final, y la hache
muda del castellano convierte "Hacu" en "Acu". Esta capa reescribe el texto SOLO
para la voz, justo antes de sintetizar. Lo que se lee en pantalla y lo que se
guarda en la memoria no cambian: el visitante ve "AudacIA" y oye "Audacia".

Cada entrada del lexico esta medida, no supuesta: se sintetizo la frase y se
transcribio lo sintetizado, que es la unica forma de saber que sale por el
altavoz sin tener a alguien escuchando. Lo que ya sonaba bien no esta aqui —las
siglas que el castellano deletrea (ROV, ROP, PCR, HLB) salen correctas solas.
"""

from __future__ import annotations

import re

# (como se escribe, como hay que decirlo). Medido con Piper + faster-whisper:
# la columna de la derecha es lo que produce una transcripcion legible.
LEXICO: tuple[tuple[str, str], ...] = (
    # La marca de la casa. "AudacIA" se oia "Auda... ya"; "Hacu", "Acu".
    ("AudacIA", "Audacia"),
    ("Hacu", "Jacu"),
    # Proyectos con mayuscula interior o nombre en ingles.
    ("SkinnIA", "Skinia"),
    ("VART", "Vart"),
    ("fAIr LAC", "Fair Lac"),
    ("MacondoLab", "Macondo Lab"),
    # Siglas que el castellano NO deletrea bien de corrido. Las dos
    # institucionales se dicen por su nombre completo porque Piper no las
    # pronuncia en ninguna grafia: "OEA" sonaba "olla", "O.E.A." sonaba "via" y
    # "O E A" sonaba "lo que hay". Ademas, para el publico de una exhibicion el
    # nombre entero informa mas que la sigla.
    ("IoT", "Internet de las Cosas"),
    ("SARS-CoV-2", "sars cov dos"),
    ("OEA", "Organización de los Estados Americanos"),
    ("MinCiencias", "el Ministerio de Ciencia, Tecnología e Innovación"),
    # Simbolos que se leen como simbolo y no como palabra.
    ("m²", "metros cuadrados"),
)

# Limite de palabra propio: `\b` no sirve para entradas con espacio ("fAIr LAC"),
# con guion ("SARS-CoV-2") o que terminan en un simbolo ("m²").
_ANTES = r"(?<![0-9A-Za-zÁÉÍÓÚÜÑáéíóúüñ])"
_DESPUES = r"(?![0-9A-Za-zÁÉÍÓÚÜÑáéíóúüñ])"


def compilar(lexico: tuple[tuple[str, str], ...]) -> tuple[tuple[re.Pattern[str], str], str]:
    """Prepara el lexico. Las entradas mas largas primero, para que ganen."""
    ordenado = sorted(lexico, key=lambda par: len(par[0]), reverse=True)
    return tuple(
        (re.compile(_ANTES + re.escape(escrito) + _DESPUES, re.IGNORECASE), dicho)
        for escrito, dicho in ordenado
    )


_COMPILADO = compilar(LEXICO)


def para_voz(texto: str, lexico: tuple[tuple[re.Pattern[str], str], ...] | None = None) -> str:
    """Devuelve el texto listo para el sintetizador. No toca lo que se muestra."""
    if not texto:
        return texto
    for patron, dicho in (lexico if lexico is not None else _COMPILADO):
        texto = patron.sub(dicho, texto)
    return texto
