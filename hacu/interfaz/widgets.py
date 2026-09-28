"""Widgets a medida de la ventana de exhibicion.

Tres piezas que Qt no trae y que son las que hacen que se entienda de lejos:

- `NucleoHacu`: un cerebro de perfil hecho de una malla low-poly (nodos y
  aristas, como la referencia de AudacIA), coloreado por estado, con energia
  que viaja por sus conexiones cuando HACU escucha, piensa o habla. Es el
  unico indicador que se lee desde el fondo de la sala, donde el texto de
  estado ya no se distingue.
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
    QColor,
    QFont,
    QPainter,
    QPen,
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
    BORDE,
    COLOR_ESTADO,
    SUPERFICIE,
    TEXTO_SUAVE,
    TEXTO_TENUE,
    EstadoUI,
)

_FPS = 30
# Cuanto tarda una particula de energia en cruzar una conexion activa, en
# segundos, por estado. HABLANDO/PENSANDO corren rapido; REPOSO no aparece
# aqui a proposito: en reposo la red se ve, pero no dispara ninguna neurona.
_SEGUNDOS_POR_VUELTA: dict[EstadoUI, float] = {
    EstadoUI.ESCUCHANDO: 1.9,
    EstadoUI.PENSANDO: 1.1,
    EstadoUI.HABLANDO: 1.4,
    EstadoUI.ERROR: 2.6,
}
# Cada cuantos segundos nace una onda nueva (un anillo que se expande desde
# el cerebro hasta el borde del widget), por estado: mas seguido cuanto mas
# "activo" esta HACU. Es lo que mas aprovecha una pantalla ultrawide, porque
# a diferencia del cerebro (limitado por la altura) una onda crece en las dos
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
# punto interior del cerebro), por estado.
_TASA_DESTELLO: dict[EstadoUI, float] = {
    EstadoUI.REPOSO: 0.15,
    EstadoUI.ESCUCHANDO: 0.45,
    EstadoUI.PENSANDO: 0.95,
    EstadoUI.HABLANDO: 0.65,
    EstadoUI.ERROR: 0.35,
}
_DURACION_DESTELLO = 0.5
# Puntos ambiente repartidos por TODO el widget (no solo cerca del cerebro),
# para que una pantalla ultrawide no deje franjas vacias a los lados.
_PUNTOS_FONDO = 70


def _construir_triangulos(aristas: tuple[tuple[int, int], ...]) -> tuple[tuple[int, int, int], ...]:
    """Deriva las caras (tripletas de nodos) de la malla a partir de sus aristas.

    En una triangulacion como `_ARISTAS` (de Delaunay, restringida al contorno
    del cerebro) tres nodos mutuamente conectados son, salvo casos raros, una
    cara real de la malla: no hace falta guardar una lista de caras aparte,
    alcanza con buscar triangulos en el grafo. El resultado son regiones
    interiores repartidas por todo el cerebro, y es lo que deja poner
    "neuronas" y destellos DENTRO del volumen en vez de solo sobre su
    contorno o sus conexiones. Se calcula una sola vez, al importar el modulo.
    """
    vecinos: dict[int, set[int]] = {}
    for i, j in aristas:
        vecinos.setdefault(i, set()).add(j)
        vecinos.setdefault(j, set()).add(i)
    caras: list[tuple[int, int, int]] = []
    for i, j in aristas:
        for k in vecinos.get(i, set()) & vecinos.get(j, set()):
            if k > j:  # cada triangulo (i<j<k) se cuenta una sola vez
                caras.append((i, j, k))
    return tuple(caras)


class NucleoHacu(QWidget):
    """Un cerebro de perfil hecho de una malla low-poly: nodos y aristas
    rectas, como la segunda referencia que dio AudacIA (una red de
    triangulos que brilla, no una placa de circuito impresa).

    La malla no esta inventada a mano ni es un grafo aleatorio: `_NODOS_REL`
    y `_ARISTAS` salen de una triangulacion de Delaunay restringida (libreria
    `triangle`, la misma que usan los generadores de arte low-poly) sobre el
    contorno real del cerebro — por eso los triangulos quedan parejos y el
    borde de la malla es, el mismo, el contorno del cerebro: no hace falta
    dibujar una linea de silueta aparte (eso fue justamente lo que se veia
    "hecho a mano" en el intento anterior).

    `_NODOS_DESTACADOS` son los nodos con el brillo ambiente mas grande
    (dispersos por todo el perfil, como los puntos mas luminosos de la
    referencia); `_ARISTAS_ACTIVAS` son las conexiones por las que viaja una
    particula de energia cuando HACU escucha, piensa o habla — tambien
    elegidas por dispersion, para que se vea movimiento por todo el cerebro y
    no solo en una esquina. En reposo la malla se ve entera pero quieta.
    """

    # Nodos de la malla (vertices de la triangulacion), en coordenadas
    # relativas al radio del cerebro (-1..1).
    _NODOS_REL: tuple[tuple[float, float], ...] = (
        (-0.6361, -0.7187), (-0.9052, -0.4495), (-0.9083, -0.3639), (-0.9817, -0.2844),
        (-0.9817, -0.0703), (-0.9174, 0.0), (-0.9205, 0.055), (-0.7829, 0.1774),
        (-0.7768, 0.2294), (-0.6636, 0.3364), (-0.3486, 0.3364), (-0.208, 0.4832),
        (-0.104, 0.4648), (-0.0673, 0.4924), (0.1621, 0.4924), (0.1927, 0.5168),
        (0.2844, 0.5168), (0.3578, 0.6086), (0.3639, 0.8226), (0.5627, 0.841),
        (0.5749, 0.7064), (0.7339, 0.5474), (0.737, 0.4434), (0.7706, 0.4251),
        (0.7798, 0.4526), (0.841, 0.4557), (0.8532, 0.3884), (0.9327, 0.3028),
        (0.9327, 0.159), (0.9786, 0.1101), (0.9786, -0.1193), (0.8777, -0.2599),
        (0.8716, -0.3884), (0.5291, -0.737), (0.4037, -0.7492), (0.315, -0.8318),
        (-0.3089, -0.8502), (-0.3945, -0.7737), (-0.4709, -0.7737), (-0.526, -0.7217),
        (0.8118, 0.4266), (0.2297, 0.439), (0.7146, 0.3623), (0.7934, 0.3652),
        (-0.6684, 0.1903), (-0.0265, 0.4), (-0.8011, 0.0341), (0.8509, 0.0362),
        (0.8507, 0.3064), (-0.7233, -0.4001), (-0.4327, -0.678), (0.5519, 0.4508),
        (0.1109, 0.3581), (0.7573, 0.2727), (-0.1745, 0.3019), (-0.8051, -0.1672),
        (-0.5623, -0.5226), (-0.2086, -0.644), (0.3931, 0.3822), (0.5448, 0.2262),
        (0.003, -0.841), (-0.5061, 0.2581), (-0.0085, 0.2123), (0.2609, 0.2656),
        (0.4403, -0.4744), (0.6322, -0.3126), (-0.4533, -0.0968), (-0.3306, 0.1028),
        (0.1824, -0.6005), (-0.0445, -0.2105), (-0.2326, -0.095), (-0.3408, -0.3525),
        (0.7004, -0.5627), (0.3576, -0.0807), (0.2074, -0.3249), (-0.5429, -0.2889),
        (0.3845, 0.1134), (0.1586, 0.0458), (0.5944, -0.0146), (-0.1219, -0.429),
    )
    # Conexiones entre nodos: indices sobre `_NODOS_REL`.
    _ARISTAS: tuple[tuple[int, int], ...] = (
        (0, 1), (0, 39), (0, 49), (0, 56), (1, 2), (1, 49),
        (2, 3), (2, 49), (2, 55), (3, 4), (3, 55), (4, 5),
        (4, 55), (5, 6), (5, 46), (5, 55), (6, 7), (6, 46),
        (7, 8), (7, 44), (7, 46), (8, 9), (8, 44), (9, 10),
        (9, 44), (9, 61), (10, 11), (10, 54), (10, 61), (10, 67),
        (11, 12), (11, 54), (12, 13), (12, 45), (12, 54), (13, 14),
        (13, 45), (14, 15), (14, 41), (14, 45), (14, 52), (15, 16),
        (15, 41), (16, 17), (16, 41), (16, 58), (17, 18), (17, 20),
        (17, 51), (17, 58), (18, 19), (18, 20), (19, 20), (20, 21),
        (20, 51), (21, 22), (21, 51), (22, 23), (22, 42), (22, 51),
        (23, 24), (23, 40), (23, 42), (23, 43), (24, 25), (24, 40),
        (25, 26), (25, 40), (26, 27), (26, 40), (26, 43), (26, 48),
        (27, 28), (27, 48), (28, 29), (28, 47), (28, 48), (28, 53),
        (29, 30), (29, 47), (30, 31), (30, 47), (31, 32), (31, 47),
        (31, 65), (31, 78), (32, 65), (32, 72), (33, 34), (33, 64),
        (33, 72), (34, 35), (34, 64), (34, 68), (35, 60), (35, 68),
        (36, 37), (36, 57), (36, 60), (37, 38), (37, 50), (37, 57),
        (38, 39), (38, 50), (39, 50), (39, 56), (40, 43), (41, 52),
        (41, 58), (41, 63), (42, 43), (42, 51), (42, 53), (42, 59),
        (43, 48), (43, 53), (44, 46), (44, 61), (44, 66), (45, 52),
        (45, 54), (45, 62), (46, 55), (46, 66), (47, 53), (47, 78),
        (48, 53), (49, 55), (49, 56), (49, 75), (50, 56), (50, 57),
        (50, 71), (51, 58), (51, 59), (52, 62), (52, 63), (53, 59),
        (53, 78), (54, 62), (54, 67), (55, 66), (55, 75), (56, 71),
        (56, 75), (57, 60), (57, 68), (57, 71), (57, 79), (58, 59),
        (58, 63), (58, 76), (59, 76), (59, 78), (60, 68), (61, 66),
        (61, 67), (62, 63), (62, 67), (62, 70), (62, 77), (63, 76),
        (63, 77), (64, 65), (64, 68), (64, 72), (64, 73), (64, 74),
        (65, 72), (65, 73), (65, 78), (66, 67), (66, 70), (66, 71),
        (66, 75), (67, 70), (68, 74), (68, 79), (69, 70), (69, 71),
        (69, 74), (69, 77), (69, 79), (70, 71), (70, 77), (71, 75),
        (71, 79), (73, 74), (73, 76), (73, 77), (73, 78), (74, 77),
        (74, 79), (76, 77), (76, 78),
    )
    # Nodos con brillo ambiente mas grande (siempre encendidos, elegidos por
    # dispersion para cubrir todo el perfil).
    _NODOS_DESTACADOS: tuple[int, ...] = (
        0, 3, 8, 10, 19, 22, 29, 32, 33, 36, 41, 59, 60, 62,
        68, 69, 73, 75,
    )
    # Que aristas llevan la particula de energia (indices sobre `_ARISTAS`).
    _ARISTAS_ACTIVAS: tuple[int, ...] = (
        0, 4, 9, 17, 21, 23, 27, 36, 38, 48, 50, 55, 77, 78,
        85, 87, 88, 94, 98, 99, 132, 141, 146, 152, 160, 169, 171, 173,
        176, 178, 191,
    )
    _MARGEN = 18  # deja sitio al halo, que se dibuja mas grande que el cerebro

    # Caras interiores de la malla, para las "neuronas" y los destellos. De
    # todas ellas se anima una de cada tres: con todas se veia amontonado, y
    # una de cada tres ya cubre el cerebro entero sin saturar el dibujo.
    _TRIANGULOS: tuple[tuple[int, int, int], ...] = _construir_triangulos(_ARISTAS)
    _INDICES_NEURONAS: tuple[int, ...] = tuple(range(0, len(_TRIANGULOS), 3))

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
        self._nodos: list[QPointF] = []
        self._centros: list[QPointF] = []
        self._radios_centro: list[float] = []
        self._ondas: list[float] = []
        self._prox_onda = 0.0
        self._destellos: list[tuple[QPointF, float]] = []
        self._reconstruir_geometria()
        self._reloj = QTimer(self)
        self._reloj.timeout.connect(self._latir)
        self._reloj.start(1000 // _FPS)

    def resizeEvent(self, evento) -> None:  # noqa: N802 (API de Qt)
        super().resizeEvent(evento)
        self._reconstruir_geometria()

    def _reconstruir_geometria(self) -> None:
        centro = QPointF(self.width() / 2, self.height() / 2)
        r = min(self.width(), self.height()) / 2 - self._MARGEN
        self._radio = max(0.0, r)
        if r <= 0:
            self._nodos = []
            self._centros = []
            self._radios_centro = []
            return
        self._nodos = [centro + QPointF(x * r, y * r) for x, y in self._NODOS_REL]
        self._centros = []
        self._radios_centro = []
        for i, j, k in self._TRIANGULOS:
            p, q, s = self._nodos[i], self._nodos[j], self._nodos[k]
            c = QPointF((p.x() + q.x() + s.x()) / 3, (p.y() + q.y() + s.y()) / 3)
            # Radio de deriva: la menor distancia del centroide a sus tres
            # vertices, para que la "neurona" nunca cruce el borde de su
            # propio triangulo por mucho que se mueva.
            radio_cara = min(
                math.hypot(p.x() - c.x(), p.y() - c.y()),
                math.hypot(q.x() - c.x(), q.y() - c.y()),
                math.hypot(s.x() - c.x(), s.y() - c.y()),
            )
            self._centros.append(c)
            self._radios_centro.append(radio_cara)

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
        self.update()

    def _avanzar_ondas(self) -> None:
        if self._tiempo >= self._prox_onda:
            self._ondas.append(self._tiempo)
            self._prox_onda = self._tiempo + _INTERVALO_ONDA[self._estado]
        limite = self._tiempo - _DURACION_ONDA
        self._ondas = [t0 for t0 in self._ondas if t0 > limite]

    def _avanzar_destellos(self) -> None:
        if self._centros and random.random() < _TASA_DESTELLO[self._estado] / _FPS:
            indice = random.choice(self._INDICES_NEURONAS)
            self._destellos.append((self._centros[indice], self._tiempo))
        limite = self._tiempo - _DURACION_DESTELLO
        self._destellos = [(p, t0) for p, t0 in self._destellos if t0 > limite]

    # -------------------------------------------------------------- pintura

    def paintEvent(self, evento) -> None:  # noqa: N802 (API de Qt)
        del evento
        pintor = QPainter(self)
        pintor.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._radio <= 0 or not self._nodos:
            pintor.end()
            return

        color = QColor(COLOR_ESTADO[self._estado])
        centro = QPointF(self.width() / 2, self.height() / 2)
        respiracion = (math.sin(self._fase) + 1) / 2
        energia = self._nivel_suave if self._estado is EstadoUI.ESCUCHANDO else respiracion

        # El orden importa: de atras hacia adelante, del fondo mas tenue al
        # destello mas brillante.
        self._pintar_fondo_ambiente(pintor, color, centro)
        self._pintar_ondas(pintor, color, centro)
        self._pintar_halo(pintor, color, centro, energia)
        self._pintar_malla(pintor, color, energia)
        self._pintar_nodos(pintor, color, respiracion)
        self._pintar_energia_bordes(pintor, color)
        self._pintar_neuronas_internas(pintor, color)
        self._pintar_destellos(pintor, color)
        pintor.end()

    def _pintar_fondo_ambiente(self, pintor: QPainter, color: QColor, centro: QPointF) -> None:
        """Un tinte tenue de todo el widget, mas un enjambre de puntos lejanos.

        En una pantalla ultrawide el cerebro (dibujado a un tamano limitado
        por la ALTURA del widget) deja franjas vacias a los lados; este tinte
        y estos puntos evitan que esas franjas se vean como espacio muerto en
        vez de parte del mismo fondo.
        """
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
        aureo = 0.6180339887
        for indice in range(_PUNTOS_FONDO):
            punto = QPointF((indice * aureo % 1.0) * ancho, (indice * aureo * aureo % 1.0) * alto)
            parpadeo = (math.sin(self._tiempo * (0.4 + (indice % 5) * 0.07) + indice) + 1) / 2
            pintor.setBrush(QColor(color.red(), color.green(), color.blue(), int(18 + 40 * parpadeo)))
            pintor.drawEllipse(punto, 1.4, 1.4)

    def _pintar_ondas(self, pintor: QPainter, color: QColor, centro: QPointF) -> None:
        """Anillos que nacen en el cerebro y se expanden hasta el borde del
        widget. Son ELIPSES, no circulos: a diferencia del cerebro (limitado
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

    def _pintar_malla(self, pintor: QPainter, color: QColor, energia: float) -> None:
        """Dos pasadas por linea (una ancha y tenue debajo, una fina y
        brillante encima) para que se vea con un resplandor propio, no como
        un trazo plano. El borde de la malla ya es el contorno del cerebro —
        no hace falta dibujar una silueta aparte.
        """
        tenue = QColor(color.red(), color.green(), color.blue(), int(35 + 25 * energia))
        nitida = QColor(color.red(), color.green(), color.blue(), int(150 + 60 * energia))
        pluma_ancha = QPen(tenue, 3.4)
        pluma_fina = QPen(nitida, 1.2)
        for i, j in self._ARISTAS:
            pintor.setPen(pluma_ancha)
            pintor.drawLine(self._nodos[i], self._nodos[j])
        for i, j in self._ARISTAS:
            pintor.setPen(pluma_fina)
            pintor.drawLine(self._nodos[i], self._nodos[j])

    def _pintar_nodos(self, pintor: QPainter, color: QColor, respiracion: float) -> None:
        """Brillo ambiente en cada nodo (mas grande en los destacados): la
        malla entera se ve "con luz propia", como en la referencia, no solo
        cuando hay energia viajando.
        """
        destacados = set(self._NODOS_DESTACADOS)
        for indice, punto in enumerate(self._nodos):
            radio_nodo = (5.5 if indice in destacados else 2.4) * (0.85 + 0.15 * respiracion)
            brillo = QRadialGradient(punto, radio_nodo * 2.4)
            pico = 235 if indice in destacados else 170
            brillo.setColorAt(0.0, QColor(255, 255, 255, int(pico * (0.55 + 0.45 * respiracion))))
            brillo.setColorAt(0.4, QColor(color.red(), color.green(), color.blue(),
                                           int(pico * 0.75 * (0.55 + 0.45 * respiracion))))
            brillo.setColorAt(1.0, QColor(color.red(), color.green(), color.blue(), 0))
            pintor.setPen(Qt.PenStyle.NoPen)
            pintor.setBrush(brillo)
            pintor.drawEllipse(punto, radio_nodo * 2.4, radio_nodo * 2.4)

    def _pintar_energia_bordes(self, pintor: QPainter, color: QColor) -> None:
        """Viaja por las conexiones activas solo cuando HACU esta activo,
        alternando de sentido por conexion (una red no dispara siempre del
        mismo nodo al mismo nodo). En reposo la malla se ve entera, pero
        quieta.
        """
        segundos_vuelta = _SEGUNDOS_POR_VUELTA.get(self._estado)
        if segundos_vuelta is None or not self._ARISTAS_ACTIVAS:
            return
        avance = self._fase / (2 * math.pi) * _SEGUNDOS_POR_VUELTA[EstadoUI.PENSANDO]
        n = len(self._ARISTAS_ACTIVAS)
        for orden, indice_arista in enumerate(self._ARISTAS_ACTIVAS):
            i, j = self._ARISTAS[indice_arista]
            origen, destino = (self._nodos[i], self._nodos[j]) if orden % 2 == 0 \
                else (self._nodos[j], self._nodos[i])
            t = (avance / segundos_vuelta + orden / n) % 1.0
            punto = QPointF(origen.x() + (destino.x() - origen.x()) * t,
                            origen.y() + (destino.y() - origen.y()) * t)
            brillo = QRadialGradient(punto, 8.5)
            brillo.setColorAt(0.0, QColor(255, 255, 255, 245))
            brillo.setColorAt(0.45, QColor(color.red(), color.green(), color.blue(), 205))
            brillo.setColorAt(1.0, QColor(color.red(), color.green(), color.blue(), 0))
            pintor.setPen(Qt.PenStyle.NoPen)
            pintor.setBrush(brillo)
            pintor.drawEllipse(punto, 5.0, 5.0)

    def _pintar_neuronas_internas(self, pintor: QPainter, color: QColor) -> None:
        """Puntos de luz que derivan DENTRO del volumen del cerebro (en el
        interior de una cara de la malla, no sobre sus aristas): a
        diferencia de `_pintar_energia_bordes`, que viaja por encima del
        contorno, esto es lo que da la sensacion de neuronas moviendose
        dentro del cerebro y no solo sobre su superficie. Cada una gira
        alrededor del centro de su propio triangulo -sin salirse de el, por
        construccion, ver `_reconstruir_geometria`- con una velocidad y una
        fase propias (formula del angulo dorado) para que no se vean
        sincronizadas entre si.
        """
        factor = {
            EstadoUI.REPOSO: 0.55, EstadoUI.ESCUCHANDO: 1.0, EstadoUI.PENSANDO: 1.7,
            EstadoUI.HABLANDO: 1.3, EstadoUI.ERROR: 0.7,
        }[self._estado]
        for orden, indice_cara in enumerate(self._INDICES_NEURONAS):
            centro_cara = self._centros[indice_cara]
            radio_deriva = self._radios_centro[indice_cara] * 0.55
            angulo = self._tiempo * factor * (0.5 + (orden % 5) * 0.11) + orden * 2.399
            punto = QPointF(
                centro_cara.x() + math.cos(angulo) * radio_deriva,
                centro_cara.y() + math.sin(angulo * 0.7 + orden) * radio_deriva,
            )
            parpadeo = (math.sin(self._tiempo * (1.3 + (orden % 4) * 0.2) + orden) + 1) / 2
            radio_punto = 1.6 + 0.9 * parpadeo
            alfa_pico = int(90 + 110 * parpadeo)
            brillo = QRadialGradient(punto, radio_punto * 3.0)
            brillo.setColorAt(0.0, QColor(255, 255, 255, alfa_pico))
            brillo.setColorAt(0.5, QColor(color.red(), color.green(), color.blue(), int(alfa_pico * 0.6)))
            brillo.setColorAt(1.0, QColor(color.red(), color.green(), color.blue(), 0))
            pintor.setPen(Qt.PenStyle.NoPen)
            pintor.setBrush(brillo)
            pintor.drawEllipse(punto, radio_punto * 3.0, radio_punto * 3.0)

    def _pintar_destellos(self, pintor: QPainter, color: QColor) -> None:
        """Chispazos breves: nacen en un punto interior al azar (ver
        `_avanzar_destellos`) y se apagan en `_DURACION_DESTELLO` segundos. A
        diferencia de las neuronas (que laten todo el rato) un destello es un
        evento puntual, para que la malla no se vea uniforme.
        """
        for punto, t0 in self._destellos:
            progreso = (self._tiempo - t0) / _DURACION_DESTELLO
            if not 0.0 <= progreso <= 1.0:
                continue
            radio = 3.0 + 20.0 * progreso
            alfa = int(255 * (1 - progreso) ** 2)
            brillo = QRadialGradient(punto, radio)
            brillo.setColorAt(0.0, QColor(255, 255, 255, alfa))
            brillo.setColorAt(0.5, QColor(color.red(), color.green(), color.blue(), int(alfa * 0.6)))
            brillo.setColorAt(1.0, QColor(color.red(), color.green(), color.blue(), 0))
            pintor.setPen(Qt.PenStyle.NoPen)
            pintor.setBrush(brillo)
            pintor.drawEllipse(punto, radio, radio)


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


__all__ = ["BotonHablar", "BurbujaMensaje", "DialogoTranscripcion", "MedidorNivel", "Metrica", "NucleoHacu",
           "TEXTO_SUAVE", "ACENTO", "separador", "titulo_panel"]
