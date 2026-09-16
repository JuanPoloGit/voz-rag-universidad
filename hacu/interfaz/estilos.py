"""Paleta y hoja de estilos de la ventana de exhibicion.

Un solo sitio para los colores, porque el widget del nucleo los pinta a mano con
QPainter y el resto los recibe por QSS: si vivieran en dos sitios, acabarian
siendo dos disenos distintos.

La paleta es oscura a proposito. La ventana se proyecta en una sala con luz
irregular y a menudo sobre un monitor grande; un fondo claro deslumbra de cerca y
se lava de lejos. Los contrastes de texto sobre fondo estan por encima de 7:1
(AAA de WCAG) porque el publico lee de pie y a varios metros.
"""

from __future__ import annotations

from enum import Enum

# --- Paleta ----------------------------------------------------------------
FONDO = "#080C16"
SUPERFICIE = "#111828"
SUPERFICIE_ALTA = "#182034"
BORDE = "#243049"
TEXTO = "#EDF1FA"
TEXTO_SUAVE = "#93A0BD"
TEXTO_TENUE = "#5E6B88"

ACENTO = "#31D9C0"          # HACU
ACENTO_OSCURO = "#1B8C7D"
VISITANTE = "#7C8CF8"       # burbuja del visitante
ALERTA = "#F2A33C"
PELIGRO = "#F2545B"

# --- Estados ---------------------------------------------------------------


class EstadoUI(str, Enum):
    """En que esta HACU ahora mismo. Manda el color y la animacion del nucleo."""

    REPOSO = "REPOSO"
    ESCUCHANDO = "ESCUCHANDO"
    PENSANDO = "PENSANDO"
    HABLANDO = "HABLANDO"
    ERROR = "ERROR"


COLOR_ESTADO: dict[EstadoUI, str] = {
    EstadoUI.REPOSO: TEXTO_TENUE,
    EstadoUI.ESCUCHANDO: "#4ADE80",
    EstadoUI.PENSANDO: ALERTA,
    EstadoUI.HABLANDO: ACENTO,
    EstadoUI.ERROR: PELIGRO,
}

ROTULO_ESTADO: dict[EstadoUI, str] = {
    EstadoUI.REPOSO: "En espera",
    EstadoUI.ESCUCHANDO: "Escuchando",
    EstadoUI.PENSANDO: "Pensando",
    EstadoUI.HABLANDO: "Hablando",
    EstadoUI.ERROR: "Error",
}

FAMILIA_TIPOGRAFICA = '"Segoe UI Variable", "Segoe UI", "Inter", "Noto Sans", sans-serif'


def hoja(tamano_texto: int) -> str:
    """QSS de toda la ventana, con el cuerpo de texto que pida la configuracion."""
    return f"""
    QWidget {{
        background: {FONDO};
        color: {TEXTO};
        font-family: {FAMILIA_TIPOGRAFICA};
        font-size: {tamano_texto}px;
    }}
    /* Sin esto, cada QLabel pinta su propio rectangulo de fondo y los rotulos
       aparecen dentro de una caja oscura que no dibujo nadie. */
    QLabel, QWidget#transparente {{
        background: transparent;
    }}
    QFrame#cabecera {{
        background: {SUPERFICIE};
        border-bottom: 1px solid {BORDE};
    }}
    QLabel#marca {{
        font-size: {tamano_texto + 9}px;
        font-weight: 700;
        letter-spacing: 1px;
    }}
    QLabel#submarca {{
        color: {TEXTO_SUAVE};
        font-size: {tamano_texto - 3}px;
        letter-spacing: 2px;
    }}
    QLabel#estadoTexto {{
        font-size: {tamano_texto - 1}px;
        font-weight: 600;
    }}
    QLabel#pista {{
        color: {TEXTO_TENUE};
        font-size: {tamano_texto - 3}px;
    }}
    QScrollArea, QScrollArea > QWidget > QWidget {{
        background: {FONDO};
        border: none;
    }}
    QScrollBar:vertical {{
        background: transparent; width: 10px; margin: 4px 2px;
    }}
    QScrollBar::handle:vertical {{
        background: {BORDE}; border-radius: 5px; min-height: 40px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {TEXTO_TENUE}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}

    QFrame#burbujaHacu {{
        margin-right: 72px;
        background: {SUPERFICIE_ALTA};
        border: 1px solid {BORDE};
        border-left: 3px solid {ACENTO};
        border-radius: 14px;
    }}
    QFrame#burbujaVisitante {{
        margin-left: 72px;
        background: {SUPERFICIE};
        border: 1px solid {BORDE};
        border-left: 3px solid {VISITANTE};
        border-radius: 14px;
    }}
    QLabel#autorHacu {{ color: {ACENTO}; font-weight: 700; font-size: {tamano_texto - 4}px;
                        letter-spacing: 1px; }}
    QLabel#autorVisitante {{ color: {VISITANTE}; font-weight: 700; font-size: {tamano_texto - 4}px;
                             letter-spacing: 1px; }}
    QLabel#cuerpo {{ background: transparent; line-height: 150%; }}
    QLabel#sello {{ color: {TEXTO_TENUE}; font-size: {tamano_texto - 5}px; }}

    QFrame#panel {{
        background: {SUPERFICIE};
        border-left: 1px solid {BORDE};
    }}
    QLabel#tituloPanel {{
        color: {TEXTO_SUAVE}; font-size: {tamano_texto - 4}px;
        font-weight: 700; letter-spacing: 2px;
    }}
    QPushButton {{
        background: {SUPERFICIE_ALTA};
        border: 1px solid {BORDE};
        border-radius: 9px;
        padding: 9px 14px;
        color: {TEXTO};
    }}
    QPushButton:hover {{ background: {BORDE}; }}
    QPushButton:pressed {{ background: {ACENTO_OSCURO}; }}
    QPushButton#peligro {{ border-color: {PELIGRO}; color: {PELIGRO}; }}
    QPushButton#peligro:hover {{ background: {PELIGRO}; color: {FONDO}; }}
    QPushButton#hablar {{
        background: {ACENTO_OSCURO};
        border: 2px solid {ACENTO};
        border-radius: 26px;
        padding: 14px 16px;
        font-size: {tamano_texto}px;
        font-weight: 700;
        letter-spacing: 1px;
    }}
    QPushButton#hablar:pressed {{ background: {ACENTO}; color: {FONDO}; }}
    QPushButton#hablar:disabled {{
        background: {SUPERFICIE}; border-color: {BORDE}; color: {TEXTO_TENUE};
    }}
    QComboBox {{
        background: {SUPERFICIE_ALTA}; border: 1px solid {BORDE};
        border-radius: 8px; padding: 7px 10px;
    }}
    QComboBox QAbstractItemView {{
        background: {SUPERFICIE_ALTA}; border: 1px solid {BORDE};
        selection-background-color: {ACENTO_OSCURO};
    }}
    QLineEdit {{
        background: {SUPERFICIE_ALTA}; border: 1px solid {BORDE};
        border-radius: 10px; padding: 11px 14px;
        selection-background-color: {ACENTO_OSCURO};
    }}
    QLineEdit:focus {{ border-color: {ACENTO}; }}
    QCheckBox {{ color: {TEXTO_SUAVE}; spacing: 10px; background: transparent; }}
    QCheckBox::indicator {{
        width: 16px; height: 16px;
        border: 1px solid {BORDE}; border-radius: 4px;
        background: {SUPERFICIE_ALTA};
    }}
    QCheckBox::indicator:hover {{ border-color: {ACENTO}; }}
    QCheckBox::indicator:checked {{
        background: {ACENTO}; border-color: {ACENTO};
    }}
    QCheckBox:disabled {{ color: {TEXTO_TENUE}; }}
    QFrame#separador {{ background: {BORDE}; max-height: 1px; border: none; }}
    QFrame#piePagina {{ background: {SUPERFICIE}; border-top: 1px solid {BORDE}; }}
    QLabel#metrica {{ color: {TEXTO_SUAVE}; font-size: {tamano_texto - 4}px; }}
    QLabel#metricaValor {{ color: {TEXTO}; font-size: {tamano_texto - 2}px; font-weight: 600; }}
    """
