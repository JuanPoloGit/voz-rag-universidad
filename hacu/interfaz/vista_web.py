"""Nucleo animado de HACU como pagina web embebida (QWebEngineView), en vez
de QPainter.

Por que: la referencia visual del tutor (interfaz-agente-audacia.html) esta
hecha en HTML/CSS/canvas, y reproducirla con QPainter a mano solo llega
hasta cierto punto -sin blur real (`backdrop-filter`), con mezclas de color
mas toscas-. QWebEngineView renderiza HTML/CSS/canvas de verdad: es el
mismo motor (Chromium) que uso el tutor para su pagina.

Que NO cambia: el resto de la ventana (cabecera, panel operador, paneles de
vidrio) sigue siendo PySide6 nativo -son controles funcionales (combos,
checkboxes, botones), no necesitan blur de verdad, y ya quedaron bien con
QSS-. Solo el nucleo -la pieza mas dificil de igualar con QPainter- pasa a
ser web.

Una sola instancia, no dos: Pro y Simple mostraban cada una su propio
`NucleoHacu`, pero un QWebEngineView carga su propio proceso de Chromium
detras (memoria y CPU nada despreciables en el hardware de la muestra, que
ya carga un LLM local). En vez de duplicarlo, `VentanaHacu` crea UN
`NucleoWebHacu` y lo reengancha (reparent) al "slot" de la vista que este
activa -ver `_reubicar_nucleo` en ventana.py-, asi que solo corre un
Chromium sin importar cuantas vistas tenga la ventana.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QSizePolicy, QVBoxLayout, QWidget

from .estilos import FONDO, EstadoUI

_RUTA_HTML = Path(__file__).parent / "web" / "nucleo.html"


class _PuenteNucleo(QObject):
    """El lado Python del canal con la pagina.

    Son SENALES, no propiedades: la pagina se suscribe una vez al cargar
    (ver `_conectarPuente` en nucleo.js) y desde ahi Python solo empuja
    cambios -no hay polling desde JS ni desde Python-.
    """

    estadoCambiado = Signal(str)
    nivelCambiado = Signal(float)


class NucleoWebHacu(QWidget):
    """Envoltorio de QWebEngineView con la MISMA API publica que
    `NucleoHacu` (`set_estado`, `set_nivel`): a ventana.py no le importa
    cual de las dos hay detras.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(240, 240)

        self._puente = _PuenteNucleo(self)
        self._canal = QWebChannel(self)
        self._canal.registerObject("puenteHacu", self._puente)

        self._vista = QWebEngineView(self)
        pagina = self._vista.page()
        # Fondo oscuro ANTES de que cargue nada: sin esto Chromium pinta
        # blanco por defecto durante el primer instante de carga, un
        # destello fuera de lugar en una sala oscura.
        pagina.setBackgroundColor(QColor(FONDO))
        pagina.setWebChannel(self._canal)
        # Nada de menu contextual ("Ver codigo fuente", "Recargar"...): esto
        # es un widget de exhibicion, no una pestana de navegador.
        self._vista.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self._vista.load(QUrl.fromLocalFile(str(_RUTA_HTML)))

        disposicion = QVBoxLayout(self)
        disposicion.setContentsMargins(0, 0, 0, 0)
        disposicion.addWidget(self._vista)

    def set_estado(self, estado: EstadoUI) -> None:
        self._puente.estadoCambiado.emit(estado.value)

    def set_nivel(self, nivel: float) -> None:
        self._puente.nivelCambiado.emit(nivel)


__all__ = ["NucleoWebHacu"]
