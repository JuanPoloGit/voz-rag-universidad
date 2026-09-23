"""Retoques deterministas sobre el audio que ya produjo Piper.

Dos defectos medidos en `es_MX-claude-high`, los dos audibles en una sala:

1. **Huecos de aire.** El modelo mete silencios internos de 250 a 450 ms en
   frases cortas ("Claro, con gusto." trae 450 ms en la coma). Caen en los
   limites prosodicos, que en castellano van justo detras de la silaba tonica,
   y por eso parece que la pausa la provoca la tilde. No la provoca: la tilde
   se fonemiza bien (`esta` sin tilde da `ˈesta`; `está` da `estˈa`). Lo que
   sobra es el aire, no el acento. `comprimir_silencios` recorta el hueco y
   deja intacto todo lo demas: la acentuacion no se toca.

2. **Bombeo de volumen.** Piper normaliza por pico y por frase. Igualar picos
   no es igualar sonoridad: medido sobre nueve frases, el audio crudo tiene un
   9.8 % de dispersion de RMS y el normalizado un 14.6 %, porque a un "Si." le
   sube 35 % la sonoridad respecto de una frase larga. `nivelar` normaliza por
   RMS con techo de pico, que es lo que el oido juzga.

Es aritmetica sobre un array: se prueba sin altavoz, sin microfono y sin GPU.
"""

from __future__ import annotations

import numpy as np

# Ventana de analisis. 10 ms es el grano habitual de deteccion de voz: mas fino
# persigue los cierres de las oclusivas, mas grueso se come silabas cortas.
_VENTANA_S = 0.010
# Por debajo de esto no hay voz. Medido sobre el pico de la ventana, no sobre el
# RMS: una /s/ final tiene poca energia media pero pico claro.
_UMBRAL_SILENCIO = 0.02
# RMS de referencia de la voz. `volumen_tts` multiplica esto, de modo que el
# valor por defecto (0.9) reproduce la sonoridad que tenia la normalizacion de
# Piper (RMS medio 0.19) y nadie nota un cambio de volumen al actualizar.
RMS_REFERENCIA = 0.21
# Ningun pico pasa de aqui. Deja margen para el conversor del sistema.
_TECHO = 0.95


def comprimir_silencios(
    muestras: np.ndarray,
    frecuencia: int,
    maximo_ms: int = 120,
    umbral: float = _UMBRAL_SILENCIO,
) -> np.ndarray:
    """Recorta los silencios *internos* que pasen de `maximo_ms`.

    No toca el silencio del principio ni el del final: el primero es el ataque
    de la frase y el segundo lo necesita la cola de reproduccion. `maximo_ms`
    en 0 o menos devuelve el audio tal cual.
    """
    if maximo_ms <= 0 or muestras.size == 0:
        return muestras
    ancho = max(1, int(frecuencia * _VENTANA_S))
    ventanas = muestras.size // ancho
    if ventanas == 0:
        return muestras

    bloques = muestras[: ventanas * ancho].reshape(ventanas, ancho)
    envolvente = np.abs(bloques).max(axis=1)
    quieto = envolvente < umbral
    tope = max(1, int(maximo_ms * frecuencia / 1000) // ancho)

    conservar = np.ones(ventanas, dtype=bool)
    i = 0
    while i < ventanas:
        if not quieto[i]:
            i += 1
            continue
        fin = i
        while fin < ventanas and quieto[fin]:
            fin += 1
        # `i > 0` deja el silencio inicial; `fin < ventanas`, el final.
        if i > 0 and fin < ventanas and (fin - i) > tope:
            conservar[i + tope : fin] = False
        i = fin

    if conservar.all():
        return muestras
    indices = np.repeat(conservar, ancho)
    cuerpo = muestras[: ventanas * ancho][indices]
    cola = muestras[ventanas * ancho :]
    return np.concatenate((cuerpo, cola)) if cola.size else cuerpo


def nivelar(
    muestras: np.ndarray, volumen: float = 1.0, techo: float = _TECHO,
) -> np.ndarray:
    """Lleva la frase al RMS de referencia sin que ningun pico pase del techo.

    Normalizar por RMS y no por pico es lo que iguala la sonoridad percibida;
    el techo evita que una frase con un pico alto sature al aplicar la ganancia.
    """
    if muestras.size == 0:
        return muestras
    rms = float(np.sqrt(np.mean(np.square(muestras, dtype=np.float64))))
    pico = float(np.abs(muestras).max())
    if rms <= 0.0 or pico <= 0.0:
        return muestras
    ganancia = min((RMS_REFERENCIA * max(volumen, 0.0)) / rms, techo / pico)
    if abs(ganancia - 1.0) < 1e-3:
        return muestras
    return (muestras * ganancia).astype(np.float32, copy=False)
