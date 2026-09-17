"""Hilos de trabajo de la interfaz.

Regla unica y no negociable: en el hilo de la interfaz no se genera, no se
transcribe y no se espera. Un turno del modelo son entre uno y tres segundos, y
una transcripcion casi uno; hacerlos en el hilo de Qt congela la ventana entera y
en una exhibicion eso se lee como "se colgo".

Los `Signal` cruzan de hilo por si solos (conexion en cola), asi que el trabajador
emite y la ventana pinta sin cerrojos por medio. La sintesis NO pasa por senales:
el locutor se alimenta aqui mismo, porque el sintetizador ya tiene su propia cola
y su propio hilo, y rebotar por la interfaz solo anadiria retardo a la voz.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import QThread, Signal

from ..session import HacuSession, ResultadoTurno
from ..voz import Locutor, ServicioDeVoz


class TrabajadorTurno(QThread):
    """Ejecuta un turno completo y va emitiendo los tokens segun salen."""

    token = Signal(str)
    listo = Signal(object)          # ResultadoTurno
    fallo = Signal(str)

    def __init__(
        self,
        sesion: HacuSession,
        texto: str,
        locutor: Locutor | None,
        logger: logging.Logger,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._sesion = sesion
        self._texto = texto
        self._locutor = locutor
        self._log = logger.getChild("turno")

    def run(self) -> None:  # noqa: D102 (API de QThread)
        try:
            resultado: ResultadoTurno = self._sesion.turno(self._texto, on_token=self._emitir)
            if self._locutor is not None:
                self._locutor.cerrar()
            self.listo.emit(resultado)
        except Exception as error:
            self._log.error("Fallo el turno", exc_info=True)
            self.fallo.emit(str(error))

    def _emitir(self, fragmento: str) -> None:
        self.token.emit(fragmento)
        if self._locutor is not None:
            self._locutor.alimentar(fragmento)


class TrabajadorTranscripcion(QThread):
    """Cierra el microfono y transcribe lo grabado."""

    # (texto, cambio de hablante, similitud). La similitud viaja para poder
    # calibrar el umbral en la sala mirando numeros reales.
    transcrito = Signal(str, bool, float)
    fallo = Signal(str)

    def __init__(self, voz: ServicioDeVoz, logger: logging.Logger, parent=None) -> None:
        super().__init__(parent)
        self._voz = voz
        self._log = logger.getChild("stt")

    def run(self) -> None:
        try:
            escucha = self._voz.detener_escucha()
            self.transcrito.emit(escucha.texto, escucha.cambio_de_hablante,
                                 escucha.similitud if escucha.similitud is not None else -1.0)
        except Exception as error:
            self._log.error("Fallo la transcripcion", exc_info=True)
            self.fallo.emit(str(error))


class TrabajadorEscuchaContinua(QThread):
    """Escucha automatica: espera frases hasta que se le pide parar."""

    transcrito = Signal(str, bool, float)
    fallo = Signal(str)

    def __init__(self, voz: ServicioDeVoz, logger: logging.Logger, parent=None) -> None:
        super().__init__(parent)
        self._voz = voz
        self._log = logger.getChild("escucha")
        self._parar = False
        self._pausado = False

    def detener(self) -> None:
        self._parar = True

    def pausar(self, pausado: bool) -> None:
        """Se pausa mientras HACU habla: si no, se oye a si mismo y se responde solo."""
        self._pausado = pausado

    def run(self) -> None:
        try:
            while not self._parar:
                if self._pausado:
                    self.msleep(120)
                    continue
                escucha = self._voz.escuchar_una_frase(cancelado=self._debe_salir)
                if escucha:
                    self.transcrito.emit(
                        escucha.texto.strip(), escucha.cambio_de_hablante,
                        escucha.similitud if escucha.similitud is not None else -1.0,
                    )
        except Exception as error:
            self._log.error("Fallo la escucha continua", exc_info=True)
            self.fallo.emit(str(error))

    def _debe_salir(self) -> bool:
        return self._parar or self._pausado


class TrabajadorTarea(QThread):
    """Cualquier tarea lenta del panel del operador (purgas, calibraciones)."""

    terminado = Signal(object)
    fallo = Signal(str)

    def __init__(self, tarea: Callable[[], object], parent=None) -> None:
        super().__init__(parent)
        self._tarea = tarea

    def run(self) -> None:
        try:
            self.terminado.emit(self._tarea())
        except Exception as error:
            self.fallo.emit(str(error))
