"""Corrige, en el texto ya transcrito, los nombres propios de la exhibicion que
Whisper transcribe casi bien.

`transcriptor.py` ya siembra `config.vocabulario` como `initial_prompt`, pero
eso es una sugerencia blanda para el decodificador, no una restriccion. Medido
en vivo el 25/09: "Holosand" salio transcrito "Colosand" -una sola letra
distinta- en una frase en ingles. El prompt no lo evito porque para entonces la
decision ya estaba tomada en el audio: el decodificador escogio una palabra
que suena casi igual y que tambien es pronunciable: el prompt sugiere, no
fuerza.

Esta capa corrige por fuerza, DESPUES de transcribir: cada palabra del texto
que dista una sola letra (insertar, borrar o sustituir) de una entrada del
vocabulario se sustituye por la forma correcta. Se probaron dos disenos mas
laxos antes de este y los dos corregian de mas:

- Partir las entradas de varias palabras ("Soil Sensor") metia palabras
  genericas del idioma como objetivo -"sensores" rozaba 86% de parecido con
  "Sensor" y se corregia solo, arruinando una frase normal-. Por eso solo se
  usan las entradas de UNA palabra: son justo los nombres inventados
  (Holosand, MacondoLab, Adaptia, Kinect...), nunca vocabulario comun.
- Un umbral de parecido por cociente (`difflib.SequenceMatcher`) no separaba
  bien los dos casos: el error real, "colosand" contra "holosand", da 87.5%
  de parecido, pero "adaptar" contra "Adaptia" -una palabra corriente del
  idioma, no un error de transcripcion- da 85.7%, demasiado cerca para
  cortar entre los dos con un solo umbral. La distancia de edicion cruda si
  los separa: "colosand"/"holosand" y "kinet"/"kinect" estan a UNA letra,
  "adaptar"/"adaptia" esta a dos. Corregir solo a distancia 1 arregla el caso
  medido sin tocar palabras del idioma que simplemente se parecen.
"""

from __future__ import annotations

import re

from ..routing import normalizar

# Solo se corrige a una letra de distancia (insertar, borrar o sustituir).
# Es deliberadamente estricto: dos palabras cualesquiera de mas de cuatro
# letras rara vez coinciden por azar a esta distancia, y ya se veian falsos
# positivos con umbrales mas laxos (ver docstring del modulo).
_DISTANCIA_MAXIMA = 1
# Por debajo de esto, estar a una letra de distancia no dice casi nada: "hace"
# (verbo comunisimo) esta a una sola letra de "Hacu" y se corregia solo,
# convirtiendo "hace diez anos" en "Hacu diez anos". Subir el minimo a cinco
# deja fuera justo esa entrada -"Hacu" es demasiado corta y demasiado parecida
# a palabras corrientes- sin perder ninguna otra: el resto de nombres
# inventados de la exhibicion tiene cinco letras o mas.
_LONGITUD_MINIMA = 5

_PALABRA = re.compile(r"[^\W\d_]+", re.UNICODE)


def _distancia_edicion(a: str, b: str, tope: int) -> int:
    """Distancia de Levenshtein entre `a` y `b`, cortando en cuanto supera `tope`.

    Palabras sueltas de pocas letras: no hace falta una libreria para esto.
    `tope` evita rellenar la fila entera de la matriz cuando ya es imposible
    quedar por debajo del umbral -no importa por cuanto se pasen, solo que se
    pasen-.
    """
    if a == b:
        return 0
    if abs(len(a) - len(b)) > tope:
        return tope + 1
    anterior = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        actual = [i] + [0] * len(b)
        minima_fila = actual[0]
        for j, cb in enumerate(b, start=1):
            costo = 0 if ca == cb else 1
            valor = min(
                anterior[j] + 1,          # borrar de a
                actual[j - 1] + 1,        # insertar en a
                anterior[j - 1] + costo,  # sustituir
            )
            actual[j] = valor
            minima_fila = min(minima_fila, valor)
        if minima_fila > tope:
            return tope + 1
        anterior = actual
    return anterior[-1]


def _vocabulario_por_palabra(vocabulario: tuple[str, ...]) -> dict[str, str]:
    """{palabra normalizada -> forma correcta}, solo de las entradas de UNA palabra.

    Ver el docstring del modulo: las entradas de varias palabras se excluyen
    a proposito para no meter vocabulario generico como objetivo de correccion.
    """
    mapa: dict[str, str] = {}
    for entrada in vocabulario:
        palabras = _PALABRA.findall(entrada)
        if len(palabras) == 1 and len(palabras[0]) >= _LONGITUD_MINIMA:
            mapa[normalizar(palabras[0])] = palabras[0]
    return mapa


def corregir_vocabulario(texto: str, vocabulario: tuple[str, ...]) -> str:
    """Sustituye en `texto` las palabras que estan a una letra de una entrada.

    No toca una palabra que YA es del vocabulario (evita mayusculas raras a
    mitad de frase) ni ninguna de menos de `_LONGITUD_MINIMA` letras. El resto
    del texto -espacios, puntuacion, mayusculas de otras palabras- se conserva
    intacto. Si dos entradas del vocabulario quedan igual de cerca, no corrige
    nada: adivinar entre dos nombres propios distintos es peor que dejar el
    error, porque cambia de que se esta hablando.
    """
    if not texto or not vocabulario:
        return texto
    candidatas = _vocabulario_por_palabra(vocabulario)
    if not candidatas:
        return texto
    formas_correctas = set(candidatas.values())

    def _reemplazar(coincidencia: re.Match[str]) -> str:
        palabra = coincidencia.group(0)
        if len(palabra) < _LONGITUD_MINIMA or palabra in formas_correctas:
            return palabra
        clave = normalizar(palabra)
        if clave in candidatas:
            return palabra  # ya es exactamente una entrada del vocabulario
        mejor_distancia = _DISTANCIA_MAXIMA + 1
        mejor_forma: str | None = None
        empate = False
        for clave_candidata, forma in candidatas.items():
            distancia = _distancia_edicion(clave, clave_candidata, _DISTANCIA_MAXIMA)
            if distancia < mejor_distancia:
                mejor_distancia, mejor_forma, empate = distancia, forma, False
            elif distancia == mejor_distancia and distancia <= _DISTANCIA_MAXIMA:
                empate = True
        if mejor_forma is not None and mejor_distancia <= _DISTANCIA_MAXIMA and not empate:
            return mejor_forma
        return palabra

    return _PALABRA.sub(_reemplazar, texto)
