"""Deteccion de voz por energia, sin dependencias externas.

Decide cuando el visitante empieza y termina de hablar a partir del nivel de cada
trama. No pretende competir con un VAD neuronal: es una maquina de estados con
histeresis, calibrada contra el ruido real de la sala al arrancar, y su unico
trabajo es cerrar la grabacion cuando llega el silencio.

En una tarima con altavoz abierto esto NO basta —HACU se oye a si mismo y se
responde solo—, por eso el modo por defecto es pulsar-para-hablar y esto queda
como opcion para sala controlada o auriculares. La logica es pura: se prueba
alimentandola con niveles a mano, sin microfono.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

# Suelo de ruido por debajo del cual no se baja el umbral. Un microfono silenciado
# mide practicamente cero, y un umbral proporcional a cero dispararia con todo.
_SUELO_RUIDO = 0.002


def nivel_rms(muestras: Sequence[float]) -> float:
    """Energia eficaz de la trama, en la escala -1.0 a 1.0 de las muestras."""
    if not len(muestras):
        return 0.0
    total = math.fsum(float(m) * float(m) for m in muestras)
    return math.sqrt(total / len(muestras))


class Estado(str, Enum):
    """En que punto de la frase esta el visitante."""

    ESPERANDO = "ESPERANDO"    # silencio, aun no ha empezado
    HABLANDO = "HABLANDO"      # voz en curso
    CERRADA = "CERRADA"        # frase terminada, hay que transcribir


@dataclass(frozen=True)
class ParametrosVoz:
    """Tiempos de la maquina de estados, en milisegundos."""

    bloque_ms: int = 30
    silencio_final_ms: int = 700
    minimo_voz_ms: int = 300
    maximo_ms: int = 20000

    def tramas(self, milisegundos: int) -> int:
        return max(1, round(milisegundos / self.bloque_ms))


class DetectorDeVoz:
    """Maquina de estados con histeresis sobre el nivel de cada trama."""

    def __init__(self, umbral: float, parametros: ParametrosVoz | None = None) -> None:
        self._parametros = parametros or ParametrosVoz()
        self.umbral = max(umbral, _SUELO_RUIDO)
        self._estado = Estado.ESPERANDO
        self._tramas_voz = 0
        self._tramas_silencio = 0
        self._tramas_totales = 0

    @property
    def estado(self) -> Estado:
        return self._estado

    @property
    def milisegundos_capturados(self) -> int:
        return self._tramas_totales * self._parametros.bloque_ms

    def reiniciar(self) -> None:
        self._estado = Estado.ESPERANDO
        self._tramas_voz = self._tramas_silencio = self._tramas_totales = 0

    def alimentar(self, nivel: float) -> Estado:
        """Procesa una trama y devuelve el estado resultante."""
        if self._estado is Estado.CERRADA:
            return self._estado

        hay_voz = nivel >= self.umbral
        if self._estado is Estado.ESPERANDO:
            if not hay_voz:
                self._tramas_voz = 0
                return self._estado
            self._tramas_voz += 1
            # Un golpe suelto no abre la grabacion: hace falta voz sostenida.
            if self._tramas_voz >= self._parametros.tramas(self._parametros.minimo_voz_ms):
                self._estado = Estado.HABLANDO
                self._tramas_totales = self._tramas_voz
                self._tramas_silencio = 0
            return self._estado

        self._tramas_totales += 1
        if hay_voz:
            self._tramas_silencio = 0
        else:
            self._tramas_silencio += 1
            if self._tramas_silencio >= self._parametros.tramas(self._parametros.silencio_final_ms):
                self._estado = Estado.CERRADA
        if self.milisegundos_capturados >= self._parametros.maximo_ms:
            self._estado = Estado.CERRADA
        return self._estado


def umbral_desde_ruido(niveles: Sequence[float], factor: float) -> float:
    """Umbral de disparo a partir del ruido medido en la sala.

    Se toma la mediana y no la media: un portazo o una tos durante la calibracion
    desplazan la media lo suficiente como para que HACU se quede sordo el resto de
    la jornada.
    """
    if not niveles:
        return _SUELO_RUIDO * factor
    ordenados = sorted(niveles)
    mitad = len(ordenados) // 2
    mediana = (ordenados[mitad] if len(ordenados) % 2
               else (ordenados[mitad - 1] + ordenados[mitad]) / 2)
    return max(mediana * factor, _SUELO_RUIDO * factor)
