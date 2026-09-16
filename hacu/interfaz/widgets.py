"""Widgets a medida de la ventana de exhibicion.

Tres piezas que Qt no trae y que son las que hacen que se entienda de lejos:

- `NucleoHacu`: anillos concentricos que respiran. Es el unico indicador que se
  lee desde el fondo de la sala, donde el texto de estado ya no se distingue.
- `MedidorNivel`: barras del nivel de entrada, para que el operador vea que el
  microfono capta antes de que el visitante se de cuenta de que no.
- `BurbujaMensaje`: una intervencion de la conversacion, con soporte para crecer
  token a token mientras el modelo genera.
"""

from __future__ import annotations

import math
from datetime import datetime

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .estilos import (
    ACENTO,
    BORDE,
    COLOR_ESTADO,
    SUPERFICIE,
    TEXTO_SUAVE,
    TEXTO_TENUE,
    EstadoUI,
)

_FPS = 30


class NucleoHacu(QWidget):
    """Indicador circular animado: color por estado, amplitud por nivel de voz."""

    # Cuantos anillos y cuanto se separan. Tres es el maximo que sigue leyendose
    # como una sola figura; con cuatro parece una diana.
    _ANILLOS = 3

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(240, 240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._estado = EstadoUI.REPOSO
        self._nivel = 0.0
        self._nivel_suave = 0.0
        self._fase = 0.0
        self._reloj = QTimer(self)
        self._reloj.timeout.connect(self._latir)
        self._reloj.start(1000 // _FPS)

    def set_estado(self, estado: EstadoUI) -> None:
        self._estado = estado
        self.update()

    def set_nivel(self, nivel: float) -> None:
        # El nivel crudo salta demasiado para animar con el: se suaviza con un
        # filtro de primer orden, rapido al subir y lento al bajar, que es como
        # se comporta un vumetro de verdad.
        objetivo = max(0.0, min(1.0, nivel * 8.0))
        alfa = 0.55 if objetivo > self._nivel_suave else 0.15
        self._nivel_suave += (objetivo - self._nivel_suave) * alfa
        self._nivel = objetivo

    def _latir(self) -> None:
        velocidad = {
            EstadoUI.REPOSO: 0.9,
            EstadoUI.ESCUCHANDO: 2.4,
            EstadoUI.PENSANDO: 3.4,
            EstadoUI.HABLANDO: 2.0,
            EstadoUI.ERROR: 1.2,
        }[self._estado]
        self._fase = (self._fase + velocidad / _FPS) % (2 * math.pi)
        if self._estado is not EstadoUI.ESCUCHANDO:
            self._nivel_suave *= 0.90
        self.update()

    def paintEvent(self, evento) -> None:  # noqa: N802 (API de Qt)
        del evento
        pintor = QPainter(self)
        pintor.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(COLOR_ESTADO[self._estado])
        centro = self.rect().center()
        radio_base = min(self.width(), self.height()) / 2 - 14

        respiracion = (math.sin(self._fase) + 1) / 2
        energia = self._nivel_suave if self._estado is EstadoUI.ESCUCHANDO else respiracion

        halo = QRadialGradient(centro, radio_base)
        halo.setColorAt(0.0, QColor(color.red(), color.green(), color.blue(), int(46 + 70 * energia)))
        halo.setColorAt(1.0, QColor(color.red(), color.green(), color.blue(), 0))
        pintor.setPen(Qt.PenStyle.NoPen)
        pintor.setBrush(halo)
        pintor.drawEllipse(centro, radio_base, radio_base)

        for i in range(self._ANILLOS):
            desfase = i / self._ANILLOS
            pulso = (math.sin(self._fase - desfase * 1.8) + 1) / 2
            radio = radio_base * (0.42 + 0.19 * i) * (1 + 0.10 * energia * pulso)
            opacidad = int(200 - i * 52 + 40 * pulso)
            pintor.setBrush(Qt.BrushStyle.NoBrush)
            pintor.setPen(QPen(QColor(color.red(), color.green(), color.blue(),
                                      max(30, min(255, opacidad))), 2.2 - i * 0.4))
            pintor.drawEllipse(centro, radio, radio)

        nucleo = radio_base * (0.19 + 0.06 * energia)
        pintor.setPen(Qt.PenStyle.NoPen)
        pintor.setBrush(color)
        pintor.drawEllipse(centro, nucleo, nucleo)

        pintor.setPen(QColor("#080C16"))
        fuente = QFont(self.font())
        fuente.setPointSizeF(max(9.0, nucleo * 0.52))
        fuente.setBold(True)
        pintor.setFont(fuente)
        pintor.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "H")
        pintor.end()


class MedidorNivel(QWidget):
    """Barras discretas del nivel de entrada. Verde mientras entra voz."""

    _BARRAS = 22

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(22)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._nivel = 0.0
        self._pico = 0.0
        self._activo = False

    def set_nivel(self, nivel: float, activo: bool) -> None:
        self._nivel = max(0.0, min(1.0, nivel * 8.0))
        self._activo = activo
        # Retencion de pico: sin ella un pico de 30 ms no se llega a ver.
        self._pico = max(self._nivel, self._pico * 0.94)
        self.update()

    def paintEvent(self, evento) -> None:  # noqa: N802
        del evento
        pintor = QPainter(self)
        pintor.setRenderHint(QPainter.RenderHint.Antialiasing)
        ancho = (self.width() - (self._BARRAS - 1) * 3) / self._BARRAS
        encendidas = int(self._nivel * self._BARRAS)
        indice_pico = int(self._pico * self._BARRAS)
        for i in range(self._BARRAS):
            x = i * (ancho + 3)
            alto = 6 + (self.height() - 8) * (i / self._BARRAS) ** 1.5
            y = (self.height() - alto) / 2
            if not self._activo:
                color = QColor(BORDE)
            elif i < encendidas:
                color = QColor("#4ADE80") if i < self._BARRAS * 0.75 else QColor("#F2A33C")
            elif i == indice_pico:
                color = QColor(TEXTO_TENUE)
            else:
                color = QColor(SUPERFICIE)
            pintor.setPen(Qt.PenStyle.NoPen)
            pintor.setBrush(color)
            pintor.drawRoundedRect(x, y, ancho, alto, 2, 2)
        pintor.end()


class BotonHablar(QPushButton):
    """Pulsar-para-hablar. Emite al apretar y al soltar, no al hacer clic."""

    pulsado = Signal()
    soltado = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("MANTÉN PULSADO", parent)
        self.setObjectName("hablar")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(56)
        self._apretado = False

    def mousePressEvent(self, evento) -> None:  # noqa: N802
        super().mousePressEvent(evento)
        self.apretar()

    def mouseReleaseEvent(self, evento) -> None:  # noqa: N802
        super().mouseReleaseEvent(evento)
        self.soltar()

    def apretar(self) -> None:
        if self._apretado or not self.isEnabled():
            return
        self._apretado = True
        self.setDown(True)
        self.pulsado.emit()

    def soltar(self) -> None:
        if not self._apretado:
            return
        self._apretado = False
        self.setDown(False)
        self.soltado.emit()


class BurbujaMensaje(QFrame):
    """Una intervencion. Crece token a token mientras el modelo genera."""

    def __init__(self, autor: str, es_hacu: bool, texto: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("burbujaHacu" if es_hacu else "burbujaVisitante")
        self._texto = texto

        disposicion = QVBoxLayout(self)
        disposicion.setContentsMargins(18, 13, 18, 14)
        disposicion.setSpacing(6)

        cabecera = QHBoxLayout()
        cabecera.setSpacing(10)
        etiqueta = QLabel(autor.upper())
        etiqueta.setObjectName("autorHacu" if es_hacu else "autorVisitante")
        sello = QLabel(datetime.now().strftime("%H:%M"))
        sello.setObjectName("sello")
        cabecera.addWidget(etiqueta)
        cabecera.addStretch(1)
        cabecera.addWidget(sello)
        disposicion.addLayout(cabecera)

        self._cuerpo = QLabel(texto)
        self._cuerpo.setObjectName("cuerpo")
        self._cuerpo.setWordWrap(True)
        self._cuerpo.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        disposicion.addWidget(self._cuerpo)

    def anadir(self, fragmento: str) -> None:
        self._texto += fragmento
        self._cuerpo.setText(self._texto)

    def fijar(self, texto: str) -> None:
        self._texto = texto
        self._cuerpo.setText(texto)

    @property
    def texto(self) -> str:
        return self._texto


class Metrica(QWidget):
    """Par rotulo/valor del pie de pagina."""

    def __init__(self, rotulo: str, valor: str = "—", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        disposicion = QVBoxLayout(self)
        disposicion.setContentsMargins(0, 0, 0, 0)
        disposicion.setSpacing(1)
        titulo = QLabel(rotulo.upper())
        titulo.setObjectName("metrica")
        self._valor = QLabel(valor)
        self._valor.setObjectName("metricaValor")
        disposicion.addWidget(titulo)
        disposicion.addWidget(self._valor)

    def set(self, valor: str) -> None:
        self._valor.setText(valor)


def separador() -> QFrame:
    linea = QFrame()
    linea.setObjectName("separador")
    linea.setFrameShape(QFrame.Shape.HLine)
    return linea


def titulo_panel(texto: str) -> QLabel:
    etiqueta = QLabel(texto.upper())
    etiqueta.setObjectName("tituloPanel")
    return etiqueta


__all__ = ["BotonHablar", "BurbujaMensaje", "MedidorNivel", "Metrica", "NucleoHacu",
           "TEXTO_SUAVE", "ACENTO", "separador", "titulo_panel"]
