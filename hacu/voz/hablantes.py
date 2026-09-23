"""Deteccion de CAMBIO de hablante. No identifica a nadie y no guarda nada.

El problema real en una tarima: Camila pregunta tres cosas, se aparta, y el
siguiente visitante hereda su perfil. HACU le llama Camila y le atribuye lo que
dijo ella.

Esto lo resuelve notando que el timbre cambio. Lo que NO hace, a proposito:

- No pone nombre a la voz. Solo dice "esta es otra persona", nunca "esta es
  Camila".
- No escribe nada en disco. La referencia vive en memoria durante la visita y se
  borra al cambiar de visitante o al cerrar. No hay huella de voz persistida, y
  por tanto no hay base de datos biometrica que custodiar.

La distincion importa: un vector de voz guardado es un dato biometrico, y este
montaje es una exhibicion abierta al publico con menores entre el publico.
Guardarlo seria una decision de la universidad, no de un commit.

Calibracion medida con dos voces distintas sintetizadas (12 pares de la misma
voz, 16 pares de voces distintas): misma voz 0.844-0.947, voces distintas
0.314-0.448. El umbral 0.65 cae en medio de ese hueco. Con voces humanas reales
y ruido de sala la separacion sera menor, asi que el umbral es configurable y
cada comparacion se registra en el log para poder recalibrar con gente de verdad.
"""

from __future__ import annotations

import importlib.util
import logging
from enum import Enum

import numpy as np

_FALTA_RESEMBLYZER = (
    "Falta resemblyzer para distinguir voces. Instala "
    "'pip install resemblyzer setuptools' o desactivalo con HACU_HABLANTES=0."
)


def motivo_de_indisponibilidad() -> str | None:
    """Por que no se puede distinguir voces, o None si si se puede.

    Se comprueba al arrancar y no en mitad de un turno: si falta la libreria, el
    operador tiene que enterarse en el diagnostico de arranque, junto al resto de
    avisos de la capa de voz, y no por un traceback rojo cada vez que alguien
    habla.
    """
    return None if importlib.util.find_spec("resemblyzer") else _FALTA_RESEMBLYZER


class Cambio(str, Enum):
    """Que ha pasado con el hablante en esta intervencion."""

    PRIMERO = "PRIMERO"          # no habia referencia: esta es la primera voz
    MISMO = "MISMO"              # sigue hablando la misma persona
    OTRO = "OTRO"                # se acerco alguien distinto
    INSUFICIENTE = "INSUFICIENTE"  # audio demasiado corto para decidir


class DetectorDeHablante:
    """Compara el timbre de cada intervencion con el de la anterior."""

    # Peso de la intervencion nueva al actualizar la referencia. Bajo a
    # proposito: la referencia se adapta a como suena la persona a lo largo de la
    # visita sin que una frase con ruido la desplace entera.
    _ADAPTACION = 0.3
    # Cuantas intervenciones seguidas por debajo del umbral hacen falta para
    # declarar que hay otra persona. Una sola no basta, y esto no es prudencia
    # teorica: en la sesion en vivo del 21/09 la MISMA persona dio 0.554 en su
    # segunda frase y 0.678, 0.821, 0.863 y 0.903 en las siguientes. Ese 0.554
    # partio su perfil en dos a mitad de conversacion —HACU dejo de saber como
    # se llamaba— y era un valor atipico de un timbre construido con una sola
    # frase de tres segundos.
    #
    # El precio es un turno: si de verdad se acerca otra persona, su primera
    # frase se le atribuye todavia a la anterior. A cambio, una frase con ruido
    # o dicha de lado ya no borra la identidad del visitante que si esta.
    _SEGUIDAS_PARA_CAMBIAR = 2

    def __init__(self, umbral: float, minimo_segundos: float, frecuencia: int,
                 logger: logging.Logger) -> None:
        self._umbral = umbral
        self._minimo_muestras = int(minimo_segundos * frecuencia)
        self._log = logger.getChild("hablante")
        self._codificador = None
        self._referencia: np.ndarray | None = None
        self.ultima_similitud: float | None = None
        self._dudas = 0
        # Si el codificador falla, se apaga para el resto de la sesion. Antes se
        # reintentaba en cada intervencion y escupia el mismo traceback entero una
        # vez por turno: en una exhibicion eso es una pared de rojo en la consola
        # que no le dice nada nuevo a nadie.
        self.motivo_apagado: str | None = None

    @property
    def tiene_referencia(self) -> bool:
        return self._referencia is not None

    @property
    def activo(self) -> bool:
        return self.motivo_apagado is None

    def olvidar(self) -> None:
        """Borra la referencia. Se llama al cambiar de visitante y al cerrar."""
        self._referencia = None
        self.ultima_similitud = None
        self._dudas = 0

    def observar(self, audio: np.ndarray) -> Cambio:
        """Decide si quien acaba de hablar es la misma persona que antes."""
        if self.motivo_apagado is not None or audio.size < self._minimo_muestras:
            return Cambio.INSUFICIENTE
        try:
            vector = self._codificar(audio)
        except Exception as error:
            self.motivo_apagado = str(error)
            self._log.warning(
                "Distincion de voces desactivada para esta sesion: %s", error
            )
            return Cambio.INSUFICIENTE

        if self._referencia is None:
            self._referencia = vector
            self.ultima_similitud = None
            self._dudas = 0
            return Cambio.PRIMERO

        similitud = float(np.dot(self._referencia, vector))
        self.ultima_similitud = similitud
        if similitud >= self._umbral:
            # Mezcla suave: la referencia sigue a la persona durante la visita.
            self._referencia = _normalizar(
                (1 - self._ADAPTACION) * self._referencia + self._ADAPTACION * vector
            )
            self._dudas = 0
            self._log.debug("Mismo hablante (similitud %.3f)", similitud)
            return Cambio.MISMO

        self._dudas += 1
        if self._dudas < self._SEGUIDAS_PARA_CAMBIAR:
            # Todavia no. Se deja constancia para poder ajustar el umbral con
            # datos de sala en vez de a ojo.
            self._log.info(
                "Timbre distinto (similitud %.3f < %.2f), pero es la primera "
                "seguida: se mantiene el visitante", similitud, self._umbral)
            return Cambio.MISMO

        self._log.info("Cambio de hablante (similitud %.3f < %.2f, %d seguidas)",
                       similitud, self._umbral, self._dudas)
        self._referencia = vector
        self._dudas = 0
        return Cambio.OTRO

    # ---------------------------------------------------------------- internos

    def _codificar(self, audio: np.ndarray) -> np.ndarray:
        return _normalizar(self._asegurar_codificador().embed_utterance(
            audio.astype(np.float32)
        ))

    def _asegurar_codificador(self):
        if self._codificador is not None:
            return self._codificador
        try:
            from resemblyzer import VoiceEncoder  # noqa: PLC0415
        except ImportError as error:
            raise RuntimeError(_FALTA_RESEMBLYZER) from error
        self._log.info("Cargando el modelo de timbre de voz")
        self._codificador = VoiceEncoder("cpu")
        return self._codificador


def _normalizar(vector: np.ndarray) -> np.ndarray:
    """Vector unitario, para que la similitud sea un producto escalar y ya."""
    norma = float(np.linalg.norm(vector))
    return vector / norma if norma else vector
