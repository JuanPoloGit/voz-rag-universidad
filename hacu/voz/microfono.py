"""Captura de audio del microfono.

Envuelve sounddevice para que el resto del sistema no sepa nada de PortAudio. La
captura corre en el hilo de audio de la libreria y deja las tramas en una cola; el
consumidor (la interfaz o el bucle de consola) las recoge cuando puede, de modo que
un tiron del modelo no provoca cortes en la grabacion.

Dos modos, uno por cada situacion real:

- Pulsar para hablar: `iniciar()` / `detener()`. Es el modo de exhibicion, porque
  el microfono solo esta abierto mientras alguien mantiene pulsado, y el altavoz
  nunca se cuela en la grabacion.
- Escucha continua: `escuchar_hasta_silencio()`, que delega en `DetectorDeVoz`.
  Bloquea, asi que va en un hilo aparte.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable

import numpy as np

from ..config import VozConfig
from .deteccion import DetectorDeVoz, Estado, ParametrosVoz, nivel_rms, umbral_desde_ruido


class AudioNoDisponible(RuntimeError):
    """No hay backend de audio o no hay dispositivo de entrada utilizable."""


def _sounddevice():
    try:
        import sounddevice  # noqa: PLC0415
    except (ImportError, OSError) as error:  # OSError: falta PortAudio
        raise AudioNoDisponible(
            "No se pudo cargar sounddevice. Instala 'pip install sounddevice' "
            "(en Linux hace falta ademas el paquete del sistema portaudio19-dev)."
        ) from error
    return sounddevice


class Microfono:
    """Una fuente de audio a 16 kHz mono, lista para Whisper."""

    def __init__(self, config: VozConfig, logger: logging.Logger) -> None:
        self._cfg = config
        self._log = logger.getChild("microfono")
        self._sd = _sounddevice()
        self._tramas: queue.Queue[np.ndarray] = queue.Queue()
        self._stream = None
        self._nivel = 0.0
        self._candado = threading.Lock()
        self._muestras_bloque = max(1, int(config.frecuencia * config.bloque_ms / 1000))
        # El operador puede cambiar de tarjeta a mitad de exhibicion, asi que el
        # indice no se lee de la config congelada: se guarda aqui y el proximo
        # `iniciar()` abre el stream donde toque.
        self.dispositivo: int | None = config.dispositivo_entrada

    # ------------------------------------------------------------------ estado

    @property
    def nivel(self) -> float:
        """Nivel de la ultima trama, para el medidor de la interfaz."""
        with self._candado:
            return self._nivel

    @property
    def capturando(self) -> bool:
        return self._stream is not None

    # ------------------------------------------------------------- pulsar/soltar

    def iniciar(self) -> None:
        """Abre el microfono y empieza a acumular tramas."""
        if self._stream is not None:
            return
        self._vaciar()
        self._stream = self._sd.InputStream(
            samplerate=self._cfg.frecuencia,
            channels=self._cfg.canales,
            dtype="float32",
            blocksize=self._muestras_bloque,
            device=self.dispositivo,
            callback=self._recibir,
        )
        self._stream.start()
        self._log.debug("Captura iniciada")

    def detener(self) -> np.ndarray:
        """Cierra el microfono y devuelve todo lo capturado como un solo array."""
        if self._stream is None:
            return np.zeros(0, dtype=np.float32)
        self._stream.stop()
        self._stream.close()
        self._stream = None
        with self._candado:
            self._nivel = 0.0
        tramas = self._recoger()
        self._log.debug("Captura detenida: %d muestras", sum(len(t) for t in tramas))
        return np.concatenate(tramas) if tramas else np.zeros(0, dtype=np.float32)

    # ------------------------------------------------------------ escucha sola

    def calibrar(self) -> float:
        """Mide el ruido de la sala y devuelve el umbral de disparo."""
        milisegundos = self._cfg.calibracion_ms
        self.iniciar()
        try:
            niveles: list[float] = []
            objetivo = max(1, milisegundos // self._cfg.bloque_ms)
            while len(niveles) < objetivo:
                try:
                    trama = self._tramas.get(timeout=1.0)
                except queue.Empty:
                    break
                niveles.append(nivel_rms(trama))
        finally:
            self.detener()
        umbral = umbral_desde_ruido(niveles, self._cfg.umbral_voz)
        self._log.info("Ruido ambiente calibrado: umbral %.4f sobre %d tramas",
                       umbral, len(niveles))
        return umbral

    def escuchar_hasta_silencio(
        self, umbral: float, cancelado: Callable[[], bool] | None = None
    ) -> np.ndarray:
        """Espera a que alguien hable y devuelve la frase completa. Bloquea."""
        parametros = ParametrosVoz(
            bloque_ms=self._cfg.bloque_ms,
            silencio_final_ms=self._cfg.silencio_final_ms,
            minimo_voz_ms=self._cfg.minimo_voz_ms,
            maximo_ms=self._cfg.maximo_grabacion_ms,
        )
        detector = DetectorDeVoz(umbral, parametros)
        acumulado: list[np.ndarray] = []
        self.iniciar()
        try:
            while True:
                if cancelado is not None and cancelado():
                    return np.zeros(0, dtype=np.float32)
                try:
                    trama = self._tramas.get(timeout=0.5)
                except queue.Empty:
                    continue
                estado = detector.alimentar(nivel_rms(trama))
                if estado is not Estado.ESPERANDO:
                    acumulado.append(trama)
                if estado is Estado.CERRADA:
                    break
        finally:
            self.detener()
        return np.concatenate(acumulado) if acumulado else np.zeros(0, dtype=np.float32)

    def cerrar(self) -> None:
        if self._stream is not None:
            self.detener()

    # ---------------------------------------------------------------- internos

    def _recibir(self, datos, _tramas, _tiempo, estado) -> None:
        """Callback del hilo de audio. Debe ser barato: copiar y salir."""
        if estado:
            self._log.debug("Aviso de PortAudio en captura: %s", estado)
        trama = np.asarray(datos, dtype=np.float32).reshape(-1).copy()
        with self._candado:
            self._nivel = float(np.sqrt(np.mean(trama * trama))) if trama.size else 0.0
        self._tramas.put(trama)

    def _recoger(self) -> list[np.ndarray]:
        tramas: list[np.ndarray] = []
        while True:
            try:
                tramas.append(self._tramas.get_nowait())
            except queue.Empty:
                return tramas

    def _vaciar(self) -> None:
        self._recoger()
