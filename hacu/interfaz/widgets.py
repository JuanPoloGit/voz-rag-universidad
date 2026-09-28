"""Widgets a medida de la ventana de exhibicion.

Piezas que Qt no trae y que son las que hacen que se entienda de lejos:

- `NucleoHacu`: un nucleo de particulas de colores en rotacion (nube libre,
  sin silueta fija -pedido explicito del stand, ver el docstring de la
  clase-), coloreado por estado, con corrientes (curvas de energia) y rayos
  (zigzags breves) ademas de la nube. Es el unico indicador que se lee desde
  el fondo de la sala, donde el texto de estado ya no se distingue.
- `FondoCuadricula`: la cuadricula+vineta que va detras de toda la ventana,
  igual que el fondo de `.app` en la referencia del tutor.
- `MarcaRombo`: la marca romboide de la cabecera.
- `PildoraEstado`: el indicador de estado en capsula.
- `SecuenciaFlujo`: los 4 pasos del turno (reposo/escucha/procesa/responde).
- `MedidorNivel`: barras del nivel de entrada, para que el operador vea que el
  microfono capta antes de que el visitante se de cuenta de que no.
- `BurbujaMensaje`: una intervencion de la conversacion, con soporte para crecer
  token a token mientras el modelo genera.
"""

from __future__ import annotations

import math
import random
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QPointF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QApplication,
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
    ALERTA,
    AZUL,
    BORDE,
    COLOR_ESTADO,
    FONDO,
    PELIGRO,
    ROTULO_ESTADO,
    SUPERFICIE,
    TEXTO_SUAVE,
    TEXTO_TENUE,
    VIOLETA,
    VISITANTE,
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
        vertical.setSpacing(9)
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

_FPS = 30
# Cada cuantos segundos nace una onda nueva (un anillo que se expande desde
# el nucleo hasta el borde del widget), por estado: mas seguido cuanto mas
# "activo" esta HACU. Es lo que mas aprovecha una pantalla ultrawide, porque
# a diferencia del nucleo (limitado por la altura) una onda crece en las dos
# direcciones hasta tocar los bordes izquierdo y derecho.
_INTERVALO_ONDA: dict[EstadoUI, float] = {
    EstadoUI.REPOSO: 2.6,
    EstadoUI.ESCUCHANDO: 1.7,
    EstadoUI.PENSANDO: 0.9,
    EstadoUI.HABLANDO: 1.1,
    EstadoUI.ERROR: 1.3,
}
_DURACION_ONDA = 2.2  # segundos que tarda una onda en apagarse del todo
# Probabilidad por segundo de que nazca un destello (chispazo breve en un
# punto de la superficie del nucleo), por estado.
_TASA_DESTELLO: dict[EstadoUI, float] = {
    EstadoUI.REPOSO: 0.15,
    EstadoUI.ESCUCHANDO: 0.45,
    EstadoUI.PENSANDO: 0.95,
    EstadoUI.HABLANDO: 0.65,
    EstadoUI.ERROR: 0.35,
}
_DURACION_DESTELLO = 0.5
# Puntos ambiente repartidos por TODO el widget (no solo cerca del nucleo),
# para que una pantalla ultrawide no deje franjas vacias a los lados.
_PUNTOS_FONDO = 70

# --- Nucleo de particulas ----------------------------------------------
_N_PARTICULAS = 220
# Angulo dorado: la constante estandar para repartir puntos parejo sobre una
# esfera (distribucion de Fibonacci) sin que se amontonen en los polos, la
# misma tecnica que usa cualquier generador de esferas de puntos.
_ANGULO_DORADO = math.pi * (3 - math.sqrt(5))
_RAZON_AUREA = 0.6180339887

# Que tan rapido gira el nucleo sobre su eje, por estado.
_ROTACION_POR_ESTADO: dict[EstadoUI, float] = {
    EstadoUI.REPOSO: 0.12,
    EstadoUI.ESCUCHANDO: 0.32,
    EstadoUI.PENSANDO: 0.85,
    EstadoUI.HABLANDO: 0.5,
    EstadoUI.ERROR: 0.22,
}
# Cuanto se agita cada particula alrededor de su capa (radio "respirando"),
# por estado: quieto en reposo, tormenta en pensando.
_TURBULENCIA_POR_ESTADO: dict[EstadoUI, float] = {
    EstadoUI.REPOSO: 0.05,
    EstadoUI.ESCUCHANDO: 0.09,
    EstadoUI.PENSANDO: 0.17,
    EstadoUI.HABLANDO: 0.11,
    EstadoUI.ERROR: 0.20,
}
# Paleta por estado: tres tonos que se reparten entre las particulas (no un
# solo color) para lograr la mezcla de tonos de la referencia -un nucleo de
# energia con varios colores a la vez, no una silueta de un solo tono-,
# manteniendo los mismos colores que ya usa el resto de la interfaz.
_PALETA_ESTADO: dict[EstadoUI, tuple[str, str, str]] = {
    EstadoUI.REPOSO: (TEXTO_TENUE, "#3A4A6B", "#26314D"),
    EstadoUI.ESCUCHANDO: ("#4ADE80", ACENTO, "#8CF5CE"),
    EstadoUI.PENSANDO: (ALERTA, "#FFD48A", PELIGRO),
    EstadoUI.HABLANDO: (ACENTO, VISITANTE, "#8CE0FF"),
    EstadoUI.ERROR: (PELIGRO, ALERTA, "#FF8A80"),
}

# --- Corrientes y rayos (evolucion del nucleo, referencia del tutor) -------
# Cuantas corrientes salen del nucleo hacia el borde: curvas bezier con un
# punto de luz viajando encima, igual idea que los `streams` de la
# referencia (sin copiar su codigo: aqui es QPainterPath.cubicTo).
_N_CORRIENTES = 6
# Probabilidad por segundo de que nazca un rayo (zigzag breve entre dos
# puntos de la superficie), por estado -mas frecuente cuanto mas "activo".
_TASA_RAYO: dict[EstadoUI, float] = {
    EstadoUI.REPOSO: 0.03,
    EstadoUI.ESCUCHANDO: 0.10,
    EstadoUI.PENSANDO: 0.35,
    EstadoUI.HABLANDO: 0.18,
    EstadoUI.ERROR: 0.12,
}
_DURACION_RAYO = 0.35


class NucleoHacu(QWidget):
    """Un nucleo de particulas de colores en rotacion, SIN silueta fija.

    Pedido explicito del stand: que se vea lo mas parecido posible a una
    referencia en video (un nucleo de energia libre, tipo panel de control de
    IA) en vez de una forma reconocible -a diferencia del diseno anterior,
    que era una malla con silueta de cerebro-. Asi que aqui no hay contorno
    que respetar: es una nube de particulas repartida sobre una esfera que
    gira, se abre y se cierra segun el estado.

    El reparto de particulas usa la distribucion de Fibonacci sobre una
    esfera (`_ANGULO_DORADO`): la tecnica estandar para esparcir N puntos
    parejo sobre una superficie esferica sin que se amontonen en los polos,
    la misma que usa cualquier generador de nubes de puntos. Cada particula
    NO guarda posicion propia: en cada frame se recalcula a partir de su
    indice y de `self._tiempo` (la misma idea que ya usaban las ondas y los
    destellos), con una capa fija por particula (unas mas cerca del centro,
    otras mas hacia la superficie) y una turbulencia individual -radio que
    "respira" con su propia fase- para que la esfera no se vea rigida. La
    proyeccion a 2D es ortografica con una rotacion sobre el eje vertical
    (formulas de rotacion estandar) y las particulas se pintan de atras hacia
    adelante por su profundidad, para que la que esta "de frente" tape a la
    que esta detras -el truco habitual para que una nube de puntos plana se
    lea como una esfera en vez de un circulo relleno-.

    Evolucion (pedido del tutor, `interfaz-agente-audacia.html`): ademas de
    la nube, el nucleo tiene corrientes (curvas de energia con un punto
    viajando, `_pintar_corrientes`) y rayos (zigzags breves,
    `_pintar_rayos`) -la misma idea de "nube + rayos + corrientes" de la
    referencia, reconstruida con QPainterPath y mezcla aditiva en vez de
    copiar su canvas-.
    """

    _MARGEN = 18  # deja sitio al halo, que se dibuja mas grande que el nucleo

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(240, 240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._estado = EstadoUI.REPOSO
        self._nivel = 0.0
        self._nivel_suave = 0.0
        self._fase = 0.0
        self._tiempo = 0.0
        self._radio = 0.0
        self._ondas: list[float] = []
        self._prox_onda = 0.0
        # Cada destello guarda su posicion FIJA sobre la esfera unitaria
        # (x, y, z, instante de nacimiento): al pintarlo se rota igual que
        # cualquier particula, asi que gira con el nucleo en vez de quedarse
        # clavado en un punto de la pantalla.
        self._destellos: list[tuple[float, float, float, float]] = []
        # Cada rayo guarda sus dos extremos FIJOS sobre la esfera unitaria
        # mas una semilla propia (para el zigzag, ver `_pintar_rayos`): igual
        # patron que los destellos, gira con el nucleo en vez de temblar.
        self._rayos: list[tuple[tuple[float, float, float], tuple[float, float, float], float, int]] = []
        self._cuadricula = QPixmap()
        self._reconstruir_geometria()
        self._reloj = QTimer(self)
        self._reloj.timeout.connect(self._latir)
        self._reloj.start(1000 // _FPS)

    def resizeEvent(self, evento) -> None:  # noqa: N802 (API de Qt)
        super().resizeEvent(evento)
        self._reconstruir_geometria()

    def _reconstruir_geometria(self) -> None:
        self._radio = max(0.0, min(self.width(), self.height()) / 2 - self._MARGEN)
        self._cuadricula = _crear_cuadricula(self.width(), self.height())

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
        self._tiempo += 1.0 / _FPS
        if self._estado is not EstadoUI.ESCUCHANDO:
            self._nivel_suave *= 0.90
        self._avanzar_ondas()
        self._avanzar_destellos()
        self._avanzar_rayos()
        self.update()

    def _avanzar_ondas(self) -> None:
        if self._tiempo >= self._prox_onda:
            self._ondas.append(self._tiempo)
            self._prox_onda = self._tiempo + _INTERVALO_ONDA[self._estado]
        limite = self._tiempo - _DURACION_ONDA
        self._ondas = [t0 for t0 in self._ondas if t0 > limite]

    def _avanzar_destellos(self) -> None:
        if random.random() < _TASA_DESTELLO[self._estado] / _FPS:
            # Punto al azar sobre la esfera unitaria (metodo estandar: y
            # uniforme en [-1, 1], angulo uniforme alrededor del eje).
            y = random.uniform(-1.0, 1.0)
            theta = random.uniform(0.0, 2 * math.pi)
            radio_anillo = math.sqrt(max(0.0, 1 - y * y))
            self._destellos.append(
                (math.cos(theta) * radio_anillo, y, math.sin(theta) * radio_anillo, self._tiempo)
            )
        limite = self._tiempo - _DURACION_DESTELLO
        self._destellos = [d for d in self._destellos if d[3] > limite]

    def _avanzar_rayos(self) -> None:
        if random.random() < _TASA_RAYO[self._estado] / _FPS:
            y1 = random.uniform(-0.6, 0.9)
            theta1 = random.uniform(0.0, 2 * math.pi)
            radio1 = math.sqrt(max(0.0, 1 - y1 * y1))
            y2 = max(-1.0, min(1.0, y1 + random.uniform(-0.5, 0.5)))
            theta2 = theta1 + random.uniform(-0.6, 0.6)
            radio2 = math.sqrt(max(0.0, 1 - y2 * y2))
            p1 = (math.cos(theta1) * radio1, y1, math.sin(theta1) * radio1)
            p2 = (math.cos(theta2) * radio2, y2, math.sin(theta2) * radio2)
            self._rayos.append((p1, p2, self._tiempo, random.randint(0, 999_999)))
        limite = self._tiempo - _DURACION_RAYO
        self._rayos = [r for r in self._rayos if r[2] > limite]

    # -------------------------------------------------------------- pintura

    def paintEvent(self, evento) -> None:  # noqa: N802 (API de Qt)
        del evento
        pintor = QPainter(self)
        pintor.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._radio <= 0:
            pintor.end()
            return

        color = QColor(COLOR_ESTADO[self._estado])
        centro = QPointF(self.width() / 2, self.height() / 2)
        respiracion = (math.sin(self._fase) + 1) / 2
        energia = self._nivel_suave if self._estado is EstadoUI.ESCUCHANDO else respiracion

        # El orden importa: de atras hacia adelante, del fondo mas tenue al
        # nucleo de particulas encima de todo, con los rayos -el flash mas
        # brillante- al final.
        self._pintar_fondo_ambiente(pintor, color, centro)
        self._pintar_ondas(pintor, color, centro)
        self._pintar_halo(pintor, color, centro, energia)
        radio_nucleo = self._radio * (0.82 + 0.22 * energia)
        achatado = 0.92 + 0.04 * math.sin(self._tiempo * 0.3)
        rotacion = self._tiempo * _ROTACION_POR_ESTADO[self._estado]
        coseno_rot, seno_rot = math.cos(rotacion), math.sin(rotacion)
        self._pintar_corrientes(pintor, centro, energia)
        self._pintar_nucleo_particulas(pintor, centro, energia, radio_nucleo, achatado,
                                       coseno_rot, seno_rot)
        self._pintar_rayos(pintor, centro, radio_nucleo, achatado, coseno_rot, seno_rot)
        pintor.end()

    def _pintar_fondo_ambiente(self, pintor: QPainter, color: QColor, centro: QPointF) -> None:
        """La cuadricula compartida, mas un tinte tenue y un enjambre de
        puntos lejanos propios de este widget.

        En una pantalla ultrawide el nucleo (dibujado a un tamano limitado
        por la ALTURA del widget) deja franjas vacias a los lados; la
        cuadricula, el tinte y estos puntos evitan que esas franjas se vean
        como espacio muerto en vez de parte del mismo fondo.
        """
        pintor.drawPixmap(0, 0, self._cuadricula)
        alcance = max(self.width(), self.height()) * 0.8
        halo_fondo = QRadialGradient(centro, alcance)
        halo_fondo.setColorAt(0.0, QColor(color.red(), color.green(), color.blue(), 16))
        halo_fondo.setColorAt(1.0, QColor(color.red(), color.green(), color.blue(), 0))
        pintor.setPen(Qt.PenStyle.NoPen)
        pintor.setBrush(halo_fondo)
        pintor.drawRect(self.rect())

        # Reparto por razon aurea: cubre el rectangulo entero sin patron
        # visible y sin necesidad de guardar posiciones aleatorias fijas.
        ancho, alto = self.width(), self.height()
        for indice in range(_PUNTOS_FONDO):
            punto = QPointF((indice * _RAZON_AUREA % 1.0) * ancho,
                             (indice * _RAZON_AUREA * _RAZON_AUREA % 1.0) * alto)
            parpadeo = (math.sin(self._tiempo * (0.4 + (indice % 5) * 0.07) + indice) + 1) / 2
            pintor.setBrush(QColor(color.red(), color.green(), color.blue(), int(18 + 40 * parpadeo)))
            pintor.drawEllipse(punto, 1.4, 1.4)

    def _pintar_ondas(self, pintor: QPainter, color: QColor, centro: QPointF) -> None:
        """Anillos que nacen en el nucleo y se expanden hasta el borde del
        widget. Son ELIPSES, no circulos: a diferencia del nucleo (limitado
        por la altura) una onda crece en las dos direcciones hasta tocar los
        bordes izquierdo y derecho, que es lo que mas aprovecha el ancho de
        una pantalla ultrawide.
        """
        alcance_x = max(self.width() / 2 - 4, self._radio)
        alcance_y = max(self.height() / 2 - 4, self._radio)
        pintor.setBrush(Qt.BrushStyle.NoBrush)
        for t0 in self._ondas:
            progreso = (self._tiempo - t0) / _DURACION_ONDA
            if not 0.0 <= progreso <= 1.0:
                continue
            avance = 1 - (1 - progreso) ** 2  # easeOutQuad: arranca rapido, frena al final
            rx = self._radio + (alcance_x - self._radio) * avance
            ry = self._radio + (alcance_y - self._radio) * avance
            alfa = int(120 * (1 - progreso) ** 1.6)
            pintor.setPen(QPen(QColor(color.red(), color.green(), color.blue(), alfa), 1.6))
            pintor.drawEllipse(centro, rx, ry)

    def _pintar_halo(self, pintor: QPainter, color: QColor, centro: QPointF, energia: float) -> None:
        """Es lo que se lee desde el fondo de la sala."""
        halo = QRadialGradient(centro, self._radio + self._MARGEN)
        halo.setColorAt(0.0, QColor(color.red(), color.green(), color.blue(), int(40 + 60 * energia)))
        halo.setColorAt(1.0, QColor(color.red(), color.green(), color.blue(), 0))
        pintor.setPen(Qt.PenStyle.NoPen)
        pintor.setBrush(halo)
        pintor.drawEllipse(centro, self._radio + self._MARGEN, self._radio + self._MARGEN)

    def _pintar_corrientes(self, pintor: QPainter, centro: QPointF, energia: float) -> None:
        """Corrientes: curvas que salen del nucleo hacia el borde, con un
        punto de luz viajando por cada una.

        Misma idea que los `streams` de la referencia (curva + particula
        viajera) reconstruida con `QPainterPath.cubicTo` y mezcla aditiva
        (`CompositionMode_Plus`, el mismo truco de brillo que ya usaban los
        destellos) -no hay canvas ni JS que copiar, solo la tecnica-.
        """
        pintor.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        paleta = [QColor(c) for c in (AZUL, VIOLETA, ACENTO)]
        alcance = max(self.width(), self.height()) * 0.62
        for i in range(_N_CORRIENTES):
            angulo = (i / _N_CORRIENTES) * 2 * math.pi + self._tiempo * 0.06
            destino = QPointF(centro.x() + math.cos(angulo) * alcance,
                              centro.y() + math.sin(angulo) * alcance * 0.55)
            control1 = QPointF(centro.x() + math.cos(angulo + 0.4) * alcance * 0.35,
                               centro.y() + math.sin(angulo + 0.4) * alcance * 0.35)
            control2 = QPointF(centro.x() + math.cos(angulo - 0.2) * alcance * 0.75,
                               centro.y() + math.sin(angulo - 0.2) * alcance * 0.75)
            camino = QPainterPath(centro)
            camino.cubicTo(control1, control2, destino)
            color = paleta[i % len(paleta)]
            pintor.setPen(QPen(QColor(color.red(), color.green(), color.blue(),
                                      int(28 + 42 * energia)), 1.3))
            pintor.setBrush(Qt.BrushStyle.NoBrush)
            pintor.drawPath(camino)

            avance = (self._tiempo * (0.25 + 0.1 * (i % 3)) + i * 0.37) % 1.0
            punto = camino.pointAtPercent(avance)
            brillo = QRadialGradient(punto, 6.0)
            brillo.setColorAt(0.0, QColor(255, 255, 255, 200))
            brillo.setColorAt(1.0, QColor(255, 255, 255, 0))
            pintor.setPen(Qt.PenStyle.NoPen)
            pintor.setBrush(brillo)
            pintor.drawEllipse(punto, 5.0, 5.0)
        pintor.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

    def _pintar_nucleo_particulas(self, pintor: QPainter, centro: QPointF, energia: float,
                                  radio_nucleo: float, achatado: float,
                                  coseno_rot: float, seno_rot: float) -> None:
        """La nube de particulas: el nucleo en si.

        `factor` combina dos cosas por particula: una capa fija (que tan
        lejos del centro vive, fijada por su indice) y una turbulencia que
        respira con el tiempo -mas fuerte cuanto mas "activo" esta HACU-.
        `profundidad` (0..1, de atras hacia adelante tras la rotacion) decide
        tamano, opacidad y mezcla hacia blanco: las particulas "de frente" se
        ven grandes y casi blancas -como el nucleo caliente del centro-, las
        de atras quedan chicas y tenues, que es lo que hace leer la nube como
        una esfera en vez de un disco plano.

        `radio_nucleo`, `achatado` y la rotacion los calcula `paintEvent` una
        sola vez por frame -las corrientes y los rayos necesitan los mismos
        valores para proyectar sobre la misma esfera-.
        """
        paleta = [QColor(c) for c in _PALETA_ESTADO[self._estado]]
        turbulencia = _TURBULENCIA_POR_ESTADO[self._estado]

        proyectadas: list[tuple[float, float, float, int]] = []
        for i in range(_N_PARTICULAS):
            y = 1 - (i / (_N_PARTICULAS - 1)) * 2
            radio_anillo = math.sqrt(max(0.0, 1 - y * y))
            theta = i * _ANGULO_DORADO
            x, z = math.cos(theta) * radio_anillo, math.sin(theta) * radio_anillo
            # Capa y fase por particula: salen del propio indice (razon
            # aurea), sin guardar estado -mismo truco que el resto del
            # nucleo-. `capa` reparte particulas entre 0.62 y 1.02 del radio,
            # para que la nube tenga volumen y no sea una cascara hueca.
            capa = 0.62 + 0.4 * ((i * _RAZON_AUREA) % 1.0)
            fase_i = (i * _RAZON_AUREA * 2 * math.pi) % (2 * math.pi)
            frecuencia_i = 0.8 + (i % 7) * 0.15
            turbulencia_i = 1 + turbulencia * math.sin(self._tiempo * frecuencia_i + fase_i)
            factor = capa * turbulencia_i
            xr = x * coseno_rot - z * seno_rot
            zr = x * seno_rot + z * coseno_rot
            proyectadas.append((xr * factor, y * factor, zr * factor, i))

        proyectadas.sort(key=lambda p: p[2])  # atras primero, para que se tapen bien
        for xr, yr, zr, indice in proyectadas:
            profundidad = (zr + 1) / 2  # 0 = al fondo, 1 = de frente
            punto = QPointF(centro.x() + xr * radio_nucleo, centro.y() + yr * radio_nucleo * achatado)
            base = paleta[indice % len(paleta)]
            mezcla = 0.15 + 0.55 * profundidad
            r = int(base.red() + (255 - base.red()) * mezcla)
            g = int(base.green() + (255 - base.green()) * mezcla)
            b = int(base.blue() + (255 - base.blue()) * mezcla)
            radio_punto = (1.4 + 2.6 * profundidad) * (0.85 + 0.3 * energia)
            alfa = int(90 + 150 * profundidad)
            brillo = QRadialGradient(punto, radio_punto * 2.6)
            brillo.setColorAt(0.0, QColor(r, g, b, alfa))
            brillo.setColorAt(1.0, QColor(r, g, b, 0))
            pintor.setPen(Qt.PenStyle.NoPen)
            pintor.setBrush(brillo)
            pintor.drawEllipse(punto, radio_punto * 2.6, radio_punto * 2.6)

        # Nucleo caliente: un punto de luz intenso en el centro exacto, como
        # si ahi naciera la energia de toda la nube.
        caliente = QRadialGradient(centro, radio_nucleo * 0.22)
        caliente.setColorAt(0.0, QColor(255, 255, 255, int(200 + 40 * energia)))
        caliente.setColorAt(1.0, QColor(255, 255, 255, 0))
        pintor.setPen(Qt.PenStyle.NoPen)
        pintor.setBrush(caliente)
        pintor.drawEllipse(centro, radio_nucleo * 0.22, radio_nucleo * 0.22)

        self._pintar_destellos(pintor, centro, radio_nucleo, achatado, coseno_rot, seno_rot)

    def _pintar_destellos(self, pintor: QPainter, centro: QPointF, radio_nucleo: float,
                          achatado: float, coseno_rot: float, seno_rot: float) -> None:
        """Chispazos breves sobre la superficie de la esfera: nacen en un
        punto al azar (ver `_avanzar_destellos`) y se apagan en
        `_DURACION_DESTELLO` segundos. Giran con el nucleo -se proyectan con
        la misma rotacion que las particulas- en vez de quedarse fijos en la
        pantalla.
        """
        for x, y, z, t0 in self._destellos:
            progreso = (self._tiempo - t0) / _DURACION_DESTELLO
            if not 0.0 <= progreso <= 1.0:
                continue
            xr = x * coseno_rot - z * seno_rot
            punto = QPointF(centro.x() + xr * radio_nucleo, centro.y() + y * radio_nucleo * achatado)
            radio = 3.0 + 20.0 * progreso
            alfa = int(255 * (1 - progreso) ** 2)
            brillo = QRadialGradient(punto, radio)
            brillo.setColorAt(0.0, QColor(255, 255, 255, alfa))
            brillo.setColorAt(0.5, QColor(255, 255, 255, int(alfa * 0.4)))
            brillo.setColorAt(1.0, QColor(255, 255, 255, 0))
            pintor.setPen(Qt.PenStyle.NoPen)
            pintor.setBrush(brillo)
            pintor.drawEllipse(punto, radio, radio)

    def _pintar_rayos(self, pintor: QPainter, centro: QPointF, radio_nucleo: float,
                      achatado: float, coseno_rot: float, seno_rot: float) -> None:
        """Rayos: zigzags breves entre dos puntos de la esfera.

        Misma idea que los `lightning()` de la referencia (bolt quebrado por
        ruido), con un desplazamiento pseudoaleatorio FIJO por rayo (semilla
        propia guardada al nacer) para que el trazo no tiemble entre frames.
        """
        if not self._rayos:
            return
        pintor.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        for p1, p2, t0, semilla in self._rayos:
            progreso = (self._tiempo - t0) / _DURACION_RAYO
            if not 0.0 <= progreso <= 1.0:
                continue

            def proyectar(punto: tuple[float, float, float]) -> QPointF:
                x, y, z = punto
                xr = x * coseno_rot - z * seno_rot
                return QPointF(centro.x() + xr * radio_nucleo, centro.y() + y * radio_nucleo * achatado)

            a, b = proyectar(p1), proyectar(p2)
            dx, dy = b.x() - a.x(), b.y() - a.y()
            largo = math.hypot(dx, dy) or 1.0
            azar = random.Random(semilla)
            camino = QPainterPath(a)
            segmentos = 5
            for paso in range(1, segmentos):
                t = paso / segmentos
                jitter = azar.uniform(-0.14, 0.14) * largo
                camino.lineTo(a.x() + dx * t - dy / largo * jitter,
                              a.y() + dy * t + dx / largo * jitter)
            camino.lineTo(b)

            alfa = int(230 * (1 - progreso) ** 1.4)
            pintor.setPen(QPen(QColor(220, 233, 255, alfa), 2.0))
            pintor.drawPath(camino)
        pintor.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)


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


__all__ = ["BotonHablar", "BurbujaMensaje", "DialogoTranscripcion", "FondoCuadricula", "MarcaRombo",
           "MedidorNivel", "Metrica", "NucleoHacu", "PildoraEstado", "SecuenciaFlujo",
           "TEXTO_SUAVE", "ACENTO", "separador", "titulo_panel"]
