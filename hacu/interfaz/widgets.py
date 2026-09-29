"""Widgets a medida de la ventana de exhibicion.

Piezas que Qt no trae y que son las que hacen que se entienda de lejos:

- `FondoCuadricula`: la cuadricula+vineta que va detras de toda la ventana,
  igual que el fondo de `.app` en la referencia del tutor.
- `MarcaRombo`: la marca romboide de la cabecera.
- `PildoraEstado`: el indicador de estado en capsula.
- `SecuenciaFlujo`: los 4 pasos del turno (reposo/escucha/procesa/responde).
- `MedidorNivel`: barras del nivel de entrada, para que el operador vea que el
  microfono capta antes de que el visitante se de cuenta de que no.
- `BurbujaMensaje`: una intervencion de la conversacion, con soporte para crecer
  token a token mientras el modelo genera.

El nucleo animado (particulas, corrientes, rayos) YA NO vive aqui: es
`NucleoWebHacu` en `vista_web.py`, una pagina HTML/CSS/canvas embebida en un
`QWebEngineView` en vez de un widget pintado a mano con QPainter -mismo
lenguaje visual, pero con blur y mezcla de color reales, que QPainter solo
podia aproximar-.
"""

from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .estilos import (
    ACENTO,
    AZUL,
    BORDE,
    COLOR_ESTADO,
    FONDO,
    ROTULO_ESTADO,
    SUPERFICIE,
    TEXTO_SUAVE,
    TEXTO_TENUE,
    VIOLETA,
    EstadoUI,
)

# --- Fondo compartido: cuadricula + vineta ---------------------------------
# La misma imagen -pixmap cacheado, no se repinta a mano cada frame- la usan
# `FondoCuadricula` (toda la ventana) y `NucleoHacu` (su propio fondo
# ambiente), para que ambas vistas compartan un solo lenguaje visual en vez
# de tener cada una su propio fondo por separado.
_ESPACIADO_CUADRICULA = 54


def _crear_cuadricula(ancho: int, alto: int) -> QPixmap:
    """Renderiza cuadricula+vineta UNA vez a un pixmap con canal alfa.

    Pintar esto a mano en cada `paintEvent` (30 fps, hasta ~90 lineas en una
    ultrawide) es gasto de CPU que no hace falta si el patron no cambia salvo
    al redimensionar: se cachea aqui y solo se reconstruye en el resize.
    """
    ancho, alto = max(1, ancho), max(1, alto)
    pixmap = QPixmap(ancho, alto)
    pixmap.fill(Qt.GlobalColor.transparent)
    pintor = QPainter(pixmap)
    pintor.setRenderHint(QPainter.RenderHint.Antialiasing)
    color = QColor(AZUL)
    for x in range(0, ancho, _ESPACIADO_CUADRICULA):
        degradado = QLinearGradient(x, 0, x, alto)
        degradado.setColorAt(0.0, QColor(color.red(), color.green(), color.blue(), 0))
        degradado.setColorAt(0.5, QColor(color.red(), color.green(), color.blue(), 26))
        degradado.setColorAt(1.0, QColor(color.red(), color.green(), color.blue(), 0))
        pintor.setPen(QPen(QBrush(degradado), 1.0))
        pintor.drawLine(x, 0, x, alto)
    for y in range(0, alto, _ESPACIADO_CUADRICULA):
        degradado = QLinearGradient(0, y, ancho, y)
        degradado.setColorAt(0.0, QColor(color.red(), color.green(), color.blue(), 0))
        degradado.setColorAt(0.5, QColor(color.red(), color.green(), color.blue(), 18))
        degradado.setColorAt(1.0, QColor(color.red(), color.green(), color.blue(), 0))
        pintor.setPen(QPen(QBrush(degradado), 1.0))
        pintor.drawLine(0, y, ancho, y)
    vineta = QRadialGradient(QPointF(ancho / 2, alto / 2), math.hypot(ancho, alto) / 2)
    vineta.setColorAt(0.0, QColor(0, 0, 0, 0))
    vineta.setColorAt(0.72, QColor(0, 0, 0, 0))
    vineta.setColorAt(1.0, QColor(0, 0, 0, 130))
    pintor.setPen(Qt.PenStyle.NoPen)
    pintor.setBrush(vineta)
    pintor.drawRect(0, 0, ancho, alto)
    pintor.end()
    return pixmap


class FondoCuadricula(QWidget):
    """La base de toda la ventana: relleno solido + cuadricula + vineta.

    Va siempre en la capa de mas atras (indice 0 de un `QStackedLayout` en
    modo `StackAll`, igual tecnica que ya usaba `_vista_simple`); todo lo que
    se pinta encima usa fondos semitransparentes ("vidrio", ver
    `estilos.hoja`) para que esta cuadricula se note por debajo -la
    aproximacion practica al `backdrop-filter` de la referencia, que Qt no
    tiene barato para widgets-.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cache = QPixmap()

    def resizeEvent(self, evento) -> None:  # noqa: N802 (API de Qt)
        super().resizeEvent(evento)
        self._cache = _crear_cuadricula(self.width(), self.height())

    def paintEvent(self, evento) -> None:  # noqa: N802
        del evento
        pintor = QPainter(self)
        pintor.fillRect(self.rect(), QColor(FONDO))
        pintor.drawPixmap(0, 0, self._cache)
        pintor.end()


class MarcaRombo(QWidget):
    """Marca romboide: un cuadrado a 45° en degradado con una letra sin girar
    en el centro -misma idea que el `.mark` de la referencia (rombo + letra
    contrarrotada); reconstruida a mano porque QLabel no gira su contenido-.
    """

    def __init__(self, letra: str = "H", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._letra = letra
        self.setFixedSize(40, 40)

    def paintEvent(self, evento) -> None:  # noqa: N802
        del evento
        pintor = QPainter(self)
        pintor.setRenderHint(QPainter.RenderHint.Antialiasing)
        lado = min(self.width(), self.height()) * 0.62

        pintor.save()
        pintor.translate(self.width() / 2, self.height() / 2)
        pintor.rotate(45)
        degradado = QLinearGradient(-lado / 2, -lado / 2, lado / 2, lado / 2)
        degradado.setColorAt(0.0, QColor(AZUL))
        degradado.setColorAt(1.0, QColor(VIOLETA))
        pintor.setPen(QPen(QColor(255, 255, 255, 90), 1.2))
        pintor.setBrush(degradado)
        pintor.drawRoundedRect(int(-lado / 2), int(-lado / 2), int(lado), int(lado), 6, 6)
        pintor.restore()

        pintor.setPen(QColor(FONDO))
        fuente = pintor.font()
        fuente.setBold(True)
        fuente.setPixelSize(int(lado * 0.42))
        pintor.setFont(fuente)
        pintor.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._letra)
        pintor.end()


class PildoraEstado(QFrame):
    """Pastilla de estado: punto de color + rotulo en una capsula.

    Reemplaza el punto y el texto sueltos que tenia la cabecera -mismo patron
    que el `.status-pill` de la referencia-.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("pildoraEstado")
        fila = QHBoxLayout(self)
        fila.setContentsMargins(14, 6, 16, 6)
        fila.setSpacing(8)
        self._punto = QLabel("●")
        self._punto.setObjectName("pildoraPunto")
        self._texto = QLabel(ROTULO_ESTADO[EstadoUI.REPOSO].upper())
        self._texto.setObjectName("pildoraTexto")
        fila.addWidget(self._punto)
        fila.addWidget(self._texto)

    def set_estado(self, estado: EstadoUI) -> None:
        color = COLOR_ESTADO[estado]
        self._punto.setStyleSheet(f"color: {color};")
        self._texto.setStyleSheet(f"color: {color};")
        self._texto.setText(ROTULO_ESTADO[estado].upper())


# Los 4 pasos del turno. ERROR queda fuera a proposito: es una interrupcion,
# no una fase por la que pasa todo turno, asi que no tiene puesto en la
# secuencia -en ese estado no se resalta ninguno-.
_PASOS_FLUJO: tuple[tuple[str, str, EstadoUI], ...] = (
    ("01", "REPOSO", EstadoUI.REPOSO),
    ("02", "ESCUCHA", EstadoUI.ESCUCHANDO),
    ("03", "PROCESA", EstadoUI.PENSANDO),
    ("04", "RESPONDE", EstadoUI.HABLANDO),
)


class SecuenciaFlujo(QWidget):
    """Los cuatro pasos del turno, con el paso actual resaltado.

    Referencia: el `.flow` de interfaz-agente-audacia.html (pasos numerados,
    uno activo a la vez).
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        vertical = QVBoxLayout(self)
        vertical.setContentsMargins(0, 0, 0, 0)
        vertical.setSpacing(6)
        self._numeros: dict[EstadoUI, QLabel] = {}
        self._textos: dict[EstadoUI, QLabel] = {}
        for numero, texto, estado in _PASOS_FLUJO:
            fila = QHBoxLayout()
            fila.setSpacing(10)
            n = QLabel(numero)
            n.setObjectName("pasoNumero")
            n.setFixedWidth(24)
            t = QLabel(texto)
            t.setObjectName("pasoTexto")
            fila.addWidget(n)
            fila.addWidget(t)
            fila.addStretch(1)
            vertical.addLayout(fila)
            self._numeros[estado] = n
            self._textos[estado] = t

    def set_estado(self, estado: EstadoUI) -> None:
        for paso_estado, numero in self._numeros.items():
            activo = paso_estado is estado
            texto = self._textos[paso_estado]
            numero.setObjectName("pasoNumeroActivo" if activo else "pasoNumero")
            texto.setObjectName("pasoTextoActivo" if activo else "pasoTexto")
            # Cambiar objectName no repinta solo: hay que forzar a Qt a
            # releer la hoja de estilos para ese widget.
            for etiqueta in (numero, texto):
                etiqueta.style().unpolish(etiqueta)
                etiqueta.style().polish(etiqueta)


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


class ComboBoxSinRueda(QComboBox):
    """QComboBox que no le roba la rueda del ratón al `QScrollArea` que lo
    contiene.

    Por defecto, en cuanto el cursor pasa por encima de un combo dentro de un
    panel con scroll, la rueda cambia el valor seleccionado del combo en vez
    de desplazar el panel -es un comportamiento de base de Qt, no un bug de
    esta ventana-. En un panel angosto con varios combos (Audiencia,
    Micrófono, Altavoz) eso hace casi imposible llegar al fondo del panel
    solo con la rueda: cada combo por el que se pasa "atrapa" el gesto.
    Aquí se ignora la rueda salvo que el combo ya tenga el foco (clic previo),
    que es la señal de que el operador quiere cambiar su valor a propósito; un
    evento de rueda ignorado sube solo al padre, que es el `QScrollArea`.
    """

    def wheelEvent(self, evento) -> None:  # noqa: N802 (API de Qt)
        if self.hasFocus():
            super().wheelEvent(evento)
        else:
            evento.ignore()


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
        # Autor y hora se guardan, no solo se pintan: son lo que necesita la
        # transcripcion para reconstruir la conversacion tal como se vio.
        self.autor = autor
        self.es_hacu = es_hacu
        self.hora = datetime.now().strftime("%H:%M")

        disposicion = QVBoxLayout(self)
        disposicion.setContentsMargins(18, 13, 18, 14)
        disposicion.setSpacing(6)

        cabecera = QHBoxLayout()
        cabecera.setSpacing(10)
        etiqueta = QLabel(autor.upper())
        etiqueta.setObjectName("autorHacu" if es_hacu else "autorVisitante")
        sello = QLabel(self.hora)
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


def etiqueta_campo(texto: str) -> QLabel:
    """Rotulo pequeno encima de un control, para no adivinar que hace."""
    etiqueta = QLabel(texto)
    etiqueta.setObjectName("pista")
    return etiqueta


class DialogoTranscripcion(QDialog):
    """La conversacion entera en texto plano, lista para copiar de una vez.

    Existe porque la alternativa era una captura de pantalla por mensaje. Al
    abrirse deja el texto ya seleccionado: Ctrl+C basta, y el boton de copiar
    esta para quien no lo sepa. La ventana no es modal a proposito —se puede
    dejar abierta mientras sigue la visita— y no toca la conversacion.
    """

    def __init__(self, transcripcion: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Transcripción de la conversación")
        self.setMinimumSize(760, 560)
        self._texto = transcripcion

        disposicion = QVBoxLayout(self)
        disposicion.setContentsMargins(18, 18, 18, 18)
        disposicion.setSpacing(12)

        self._area = QPlainTextEdit(transcripcion)
        self._area.setReadOnly(True)
        self._area.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        fuente = QFont("Consolas")
        fuente.setStyleHint(QFont.StyleHint.Monospace)
        self._area.setFont(fuente)
        disposicion.addWidget(self._area, 1)

        self._aviso = QLabel("")
        self._aviso.setObjectName("pista")
        botones = QHBoxLayout()
        botones.addWidget(self._aviso, 1)
        copiar = QPushButton("Copiar todo")
        copiar.clicked.connect(self._copiar)
        guardar = QPushButton("Guardar como .txt")
        guardar.clicked.connect(self._guardar)
        cerrar = QPushButton("Cerrar")
        cerrar.clicked.connect(self.close)
        for boton in (copiar, guardar, cerrar):
            botones.addWidget(boton)
        disposicion.addLayout(botones)

        self._area.selectAll()
        self._area.setFocus()

    def _copiar(self) -> None:
        portapapeles = QApplication.clipboard()
        if portapapeles is None:          # sin gestor de portapapeles (CI, offscreen)
            self._aviso.setText("No hay portapapeles disponible.")
            return
        portapapeles.setText(self._texto)
        self._aviso.setText(f"Copiado: {len(self._texto.splitlines())} líneas.")

    def _guardar(self) -> None:
        sugerido = f"conversacion-{datetime.now().strftime('%Y%m%d-%H%M')}.txt"
        ruta, _ = QFileDialog.getSaveFileName(self, "Guardar transcripción", sugerido,
                                              "Texto (*.txt)")
        if not ruta:
            return
        try:
            Path(ruta).write_text(self._texto, encoding="utf-8")
        except OSError as error:
            self._aviso.setText(f"No se pudo guardar: {error}")
            return
        self._aviso.setText(f"Guardado en {Path(ruta).name}.")


__all__ = ["BotonHablar", "BurbujaMensaje", "ComboBoxSinRueda", "DialogoTranscripcion",
           "FondoCuadricula", "MarcaRombo", "MedidorNivel", "Metrica", "PildoraEstado",
           "SecuenciaFlujo", "TEXTO_SUAVE", "ACENTO", "separador", "titulo_panel"]
