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

# Pareja azul/violeta para la cuadricula de fondo, la marca y el nucleo
# evolucionado (corrientes y rayos): el mismo lenguaje frio-energetico de la
# referencia del tutor, sin tocar los colores de estado ya en uso.
AZUL = "#33BCFF"
VIOLETA = "#B68BFF"

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
    /* "Vidrio": semitransparente para que la cuadricula de FondoCuadricula se
       note por debajo, sin blur real (Qt no lo tiene barato para widgets;
       esta es la aproximacion practica). */
    QFrame#cabecera {{
        background: rgba(17, 24, 40, 0.72);
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
    /* El panel del operador (ver `scrollPanel` en ventana.py) vive dentro de
       QFrame#panel, que ya pinta su propio vidrio translucido: si el scroll
       heredara el fondo solido de la regla de arriba, taparia ese vidrio con
       un rectangulo opaco. Mas especifico por nombre, gana sobre la regla
       generica. */
    QScrollArea#scrollPanel, QScrollArea#scrollPanel > QWidget > QWidget {{
        background: transparent;
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
        background: rgba(24, 32, 52, 0.72);
        border: 1px solid {BORDE};
        border-left: 3px solid {ACENTO};
        border-radius: 14px;
    }}
    QFrame#burbujaVisitante {{
        margin-left: 72px;
        background: rgba(17, 24, 40, 0.72);
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
        background: rgba(17, 24, 40, 0.72);
        border-left: 1px solid {BORDE};
    }}
    /* El panel es angosto y apila muchos controles: la misma altura de
       boton/combo/campo que el resto de la ventana sumaba de sobra para
       que el contenido pareciera que se sale del espacio disponible. Mas
       compacto SOLO aqui -el resto de la ventana (burbujas, barra de
       entrada) conserva su tamano normal-. */
    QFrame#panel QPushButton, QFrame#panel QComboBox, QFrame#panel QLineEdit,
    QFrame#panel QCheckBox {{
        font-size: {tamano_texto - 2}px;
    }}
    QFrame#panel QPushButton {{
        padding: 6px 12px;
    }}
    QFrame#panel QComboBox {{
        padding: 5px 8px;
    }}
    QFrame#panel QLineEdit {{
        padding: 7px 12px;
    }}
    QLabel#tituloPanel {{
        color: {TEXTO_SUAVE}; font-size: {tamano_texto - 4}px;
        font-weight: 700; letter-spacing: 2px;
    }}
    QLabel#eyebrow {{
        color: {AZUL}; font-size: {tamano_texto - 5}px;
        font-weight: 700; letter-spacing: 3px;
    }}
    QFrame#tarjetaVidrio {{
        background: rgba(24, 32, 52, 0.55);
        border: 1px solid {BORDE};
        border-radius: 12px;
    }}
    QLabel#introTitulo {{
        font-size: {tamano_texto + 1}px; font-weight: 700;
    }}
    QLabel#introTexto {{
        color: {TEXTO_SUAVE}; font-size: {tamano_texto - 3}px;
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
    QPushButton#enviar {{
        background: {ACENTO_OSCURO};
        border: 1px solid {ACENTO};
        border-radius: 10px;
        padding: 10px 18px;
        font-weight: 700;
    }}
    QPushButton#enviar:hover {{ background: {ACENTO}; color: {FONDO}; }}
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
    QFrame#piePagina {{
        background: rgba(17, 24, 40, 0.78);
        border-top: 1px solid {BORDE};
    }}
    QLabel#metrica {{ color: {TEXTO_SUAVE}; font-size: {tamano_texto - 4}px; }}
    QLabel#metricaValor {{ color: {TEXTO}; font-size: {tamano_texto - 2}px; font-weight: 600; }}

    QFrame#pildoraEstado {{
        background: rgba(24, 32, 52, 0.75);
        border: 1px solid {BORDE};
        border-radius: 14px;
    }}
    QLabel#pildoraPunto {{ font-size: 11px; }}
    QLabel#pildoraTexto {{
        font-size: {tamano_texto - 4}px;
        font-weight: 700;
        letter-spacing: 2px;
    }}

    QLabel#pasoNumero {{
        color: {TEXTO_TENUE}; font-size: {tamano_texto - 3}px;
        font-weight: 700;
    }}
    QLabel#pasoNumeroActivo {{
        color: {AZUL}; font-size: {tamano_texto - 3}px;
        font-weight: 700;
    }}
    QLabel#pasoTexto {{ color: {TEXTO_TENUE}; font-size: {tamano_texto - 3}px; }}
    QLabel#pasoTextoActivo {{ color: {TEXTO}; font-size: {tamano_texto - 3}px; font-weight: 600; }}

    QPushButton#cambioVista {{
        background: transparent;
        border: 1px solid {BORDE};
        color: {TEXTO_TENUE};
        font-size: {tamano_texto - 4}px;
        padding: 7px 14px;
    }}
    QPushButton#cambioVista:hover {{ border-color: {ACENTO}; color: {TEXTO}; }}
    QLabel#estadoSimple {{
        font-size: {tamano_texto + 4}px;
        font-weight: 600;
        letter-spacing: 2px;
    }}
    QLabel#alertaSimple {{
        background: rgba(242, 84, 91, 0.14);
        border: 1px solid {PELIGRO};
        border-radius: 10px;
        color: {PELIGRO};
        font-weight: 600;
        padding: 10px 18px;
    }}
    """
