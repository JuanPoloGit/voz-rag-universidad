"""Identifica si una frase esta en espanol o en ingles, sin dependencias externas.

Vive en la raiz del paquete porque lo usan dos consumidores que no comparten
capa:

- `context.py` lo aplica al mensaje de ENTRADA del visitante, para instruir
  explicitamente al modelo en que idioma responder (ver
  `ContextBuilder._recordatorio_de_idioma`). La regla 21 del system prompt ya
  pide responder en el idioma del visitante, pero medido en vivo el 25/09: dos
  preguntas seguidas en ingles y las dos respuestas salieron en espanol. Un 8B
  no obedece con fiabilidad una regla mas entre otras veinte; decirselo aparte
  en cada turno -igual que ya se hace con el nombre o con el hilo de la
  conversacion- es lo que funciona.
- `hacu/voz/__init__.py` lo aplica al texto de SALIDA (lo que HACU esta a
  punto de decir), para elegir con que voz de Piper leer cada frase: Piper es
  monolingue por modelo, asi que hablar los dos idiomas de verdad depende de
  saber cual es cual, frase a frase.

No hace falta un clasificador de proposito general: el catalogo de idiomas de
HACU tiene dos entradas. Un conteo de palabras funcionales tipicas de cada
idioma, mas los caracteres que solo aparecen en uno de los dos, basta para
acertar en frases de tarima (dos a seis oraciones) y es exactamente igual de
determinista que el detector de voz por energia de `voz/deteccion.py`: se
prueba alimentandolo con texto a mano, sin modelo de por medio.
"""

from __future__ import annotations

import re

# Palabras funcionales (articulos, pronombres, preposiciones, conectores) muy
# frecuentes y casi nunca ambiguas entre los dos idiomas. No es el lexico
# completo de ninguno: cuantas mas palabras, mas facil que una comparta grafia
# con la otra lengua y reste señal en vez de sumarla.
_FUNCIONALES_ES = frozenset({
    "el", "la", "los", "las", "un", "una", "unos", "unas", "de", "del", "que",
    "y", "en", "es", "son", "por", "para", "con", "como", "mas", "pero", "si",
    "no", "se", "su", "sus", "lo", "le", "les", "muy", "tambien", "esta",
    "estan", "eso", "esto", "aqui", "porque", "cuando", "donde", "quien",
    "cual", "cuales", "gracias", "favor", "hola", "adios", "bien", "vamos",
    "nosotros", "ustedes", "tu", "usted", "yo", "nuestro", "nuestra",
})
_FUNCIONALES_EN = frozenset({
    "the", "a", "an", "of", "that", "and", "in", "is", "are", "for", "with",
    "as", "more", "but", "yes", "no", "it", "its", "very", "also", "this",
    "these", "here", "because", "when", "where", "who", "which", "thanks",
    "please", "hello", "bye", "well", "let's", "we", "you", "your", "our",
    "actually", "so", "just", "like", "what", "how", "why",
})

# Caracteres que en la practica solo aparecen en espanol. Un solo acierto pesa
# fuerte: "informacion" sin tilde puede ser cualquiera de los dos, pero "¿" no.
_SOLO_ESPANOL = re.compile(r"[ñáéíóúü¿¡]", re.IGNORECASE)

_PALABRA = re.compile(r"[a-zA-ZñÑáéíóúÁÉÍÓÚüÜ']+")


def detectar_idioma(texto: str, por_defecto: str = "es") -> str:
    """"es" o "en" segun las palabras funcionales y tildes de la frase.

    `por_defecto` decide los empates y las frases sin señal (un nombre propio
    suelto, un numero, una frase corta ambigua como "ok" o "hola profesor"). Se
    deja en espanol porque es el idioma base de la exhibicion: fallar hacia el
    hablando ingles con acento se nota mas que al reves.
    """
    si_hay_tildes = bool(_SOLO_ESPANOL.search(texto))

    puntos_es = 2 if si_hay_tildes else 0
    puntos_en = 0
    for palabra in _PALABRA.findall(texto.lower()):
        if palabra in _FUNCIONALES_ES:
            puntos_es += 1
        elif palabra in _FUNCIONALES_EN:
            puntos_en += 1

    if puntos_es == puntos_en:
        return por_defecto
    return "es" if puntos_es > puntos_en else "en"
