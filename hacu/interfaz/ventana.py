"""Ventana de exhibicion de HACU.

Tres zonas, cada una para un publico distinto:

- Columna izquierda: el nucleo animado, el boton de hablar y el medidor. Es lo
  que ve el visitante desde lejos, y lo unico que necesita entender.
- Centro: la conversacion en burbujas, que crece token a token.
- Panel derecho: los mandos del operador. Se oculta con F9 para que el publico no
  vea la cocina.

La ventana no sabe nada de llama.cpp, de ChromaDB ni de PortAudio: habla con
`HacuSession` y con `ServicioDeVoz`, y todo lo lento va a un hilo.
"""

from __future__ import annotations

import logging
import signal
from datetime import datetime

from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedLayout,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..bootstrap import Componentes
from ..config import AppConfig
from ..cuidado import AVISO_OPERADOR, Cuidado
from ..prompts import PERFILES_AUDIENCIA
from ..session import ResultadoTurno
from ..voz import ServicioDeVoz
from .estilos import COLOR_ESTADO, ROTULO_ESTADO, EstadoUI, hoja
from .hilos import (
    TrabajadorEscuchaContinua,
    TrabajadorTarea,
    TrabajadorTranscripcion,
    TrabajadorTurno,
)
from .widgets import (
    BotonHablar,
    BurbujaMensaje,
    DialogoTranscripcion,
    FondoCuadricula,
    MarcaRombo,
    MedidorNivel,
    Metrica,
    NucleoHacu,
    PildoraEstado,
    SecuenciaFlujo,
    etiqueta_campo,
    separador,
    titulo_panel,
)

_REFRESCO_NIVEL_MS = 40
# El recuento de hechos no cambia deprisa; cada dos segundos sobra y no castiga
# la base de datos.
_REFRESCO_PERFIL_MS = 2000


# El nombre entero de la tarjeta va en el tooltip de cada linea del desplegable.
_TOOLTIP = Qt.ItemDataRole.ToolTipRole


class VentanaHacu(QMainWindow):
    """La ventana completa: conversacion, voz y mandos.

    Vive en dos vistas apiladas (`QStackedWidget`), no en dos ventanas: comparten
    el mismo `_boton`, el mismo hilo de turno y la misma sesion, y solo una de las
    dos puede tocar `llama_cpp.Llama` a la vez (ver `LlmService`). Dos ventanas
    independientes habrian duplicado esa maquinaria y arriesgado que ambas
    dispararan un turno a la vez.

    - Simple: lo que ve el publico. Solo el nucleo animado y un boton discreto
      "Vista Pro" para el operador. Nada de conversacion en pantalla, nada de
      panel: si el nucleo respira, HACU esta ahi.
    - Pro: la ventana de siempre, intacta.
    """

    _INDICE_PRO = 0
    _INDICE_SIMPLE = 1

    def __init__(self, componentes: Componentes, config: AppConfig, voz: ServicioDeVoz) -> None:
        super().__init__()
        self._comp = componentes
        self._cfg = config
        self._voz = voz
        self._log: logging.Logger = componentes.logger.getChild("interfaz")
        self._estado = EstadoUI.REPOSO
        self._turno: TrabajadorTurno | None = None
        self._transcripcion: TrabajadorTranscripcion | None = None
        self._escucha: TrabajadorEscuchaContinua | None = None
        self._tareas: list[TrabajadorTarea] = []
        self._burbuja_actual: BurbujaMensaje | None = None
        # `HACU_VOZ_AUTO=1` pide escucha automatica desde el arranque, pero no se
        # puede calibrar mientras HACU saluda: el altavoz entraria en la medida de
        # ruido de sala y el umbral quedaria por encima de cualquier voz humana.
        # Queda armada y el reloj de nivel la activa en cuanto la sala calla.
        self._auto_pendiente = config.voz.deteccion_automatica

        self.setWindowTitle("HACU · AudacIA · Universidad Simón Bolívar")
        self.resize(config.interfaz.ancho, config.interfaz.alto)
        self.setStyleSheet(hoja(config.interfaz.tamano_texto))
        self._montar()
        self._atajos()

        self._reloj = QTimer(self)
        self._reloj.timeout.connect(self._refrescar_nivel)
        self._reloj.start(_REFRESCO_NIVEL_MS)

        # El extractor de memoria trabaja en segundo plano y termina despues del
        # turno, asi que el recuento de hechos del panel se quedaba en el valor
        # viejo: parecia que HACU no recordaba nada de nadie.
        self._reloj_perfil = QTimer(self)
        self._reloj_perfil.timeout.connect(self._refrescar_perfil)
        self._reloj_perfil.start(_REFRESCO_PERFIL_MS)

        self._saludar()
        if config.interfaz.pantalla_completa:
            self.showFullScreen()

    # ------------------------------------------------------------------ montaje

    def _montar(self) -> None:
        self._vistas = QStackedWidget()
        self.setCentralWidget(self._vistas)
        self._vistas.addWidget(self._vista_pro())       # _INDICE_PRO
        self._vistas.addWidget(self._vista_simple())    # _INDICE_SIMPLE
        inicio = (self._INDICE_SIMPLE if self._cfg.interfaz.vista_simple_al_arrancar
                  else self._INDICE_PRO)
        self._vistas.setCurrentIndex(inicio)
        # La ventana se queda el teclado: es quien atiende la barra espaciadora,
        # tanto si se ve la vista simple como la Pro.
        self._vistas.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFocus()

    def _vista_pro(self) -> QWidget:
        """La vista de siempre, ahora sobre `FondoCuadricula` en vez de un
        relleno solido: mismo truco de capas que `_vista_simple`
        (`QStackedLayout.StackAll` con `FondoCuadricula` debajo y el
        contenido real -objectName "transparente"- encima), para que la
        cuadricula se note por los bordes translucidos de la cabecera, el
        panel y el pie -las tres zonas ya pasaron a fondos "de vidrio" en
        `estilos.hoja`-.
        """
        raiz = QWidget()
        capas = QStackedLayout(raiz)
        capas.setContentsMargins(0, 0, 0, 0)
        capas.setStackingMode(QStackedLayout.StackingMode.StackAll)
        capas.addWidget(FondoCuadricula())

        contenido = QWidget()
        contenido.setObjectName("transparente")
        vertical = QVBoxLayout(contenido)
        vertical.setContentsMargins(0, 0, 0, 0)
        vertical.setSpacing(0)

        vertical.addWidget(self._cabecera())

        cuerpo = QHBoxLayout()
        cuerpo.setContentsMargins(0, 0, 0, 0)
        cuerpo.setSpacing(0)
        cuerpo.addWidget(self._columna_voz(), 0)
        cuerpo.addWidget(self._columna_conversacion(), 1)
        self._panel = self._panel_operador()
        cuerpo.addWidget(self._panel, 0)
        vertical.addLayout(cuerpo, 1)

        vertical.addWidget(self._pie())
        self._panel.setVisible(self._cfg.interfaz.mostrar_panel_operador)

        capas.addWidget(contenido)
        # Mismo motivo que en `_vista_simple`: en StackAll el widget que pinta
        # Y recibe el mouse ARRIBA es el CORRIENTE (currentIndex), no el
        # ultimo anadido -por defecto se habria quedado en el fondo.
        capas.setCurrentWidget(contenido)
        return raiz

    def _vista_simple(self) -> QWidget:
        """Lo que ve el publico: el nucleo pensando y nada mas.

        Sin burbujas, sin panel de operador: el visitante que mira de lejos no
        necesita leer nada, solo ver que HACU esta despierto y en que estado. El
        boton "Vista Pro" es deliberadamente discreto (esquina, sin color de
        acento) porque es para quien atiende el stand, no para el publico.

        El nucleo ocupa la ventana ENTERA -pensado para una pantalla ancha de
        stand, incluida la ultrawide de 3840x1080- y los rotulos (marca, botón
        "Vista Pro", alerta, estado, pista) flotan encima en una capa aparte,
        con `QStackedLayout.StackAll`: las dos capas se ven a la vez en vez de
        turnarse, y ninguna le quita area a la otra como pasaba cuando iban
        apiladas una debajo de la otra en un solo `QVBoxLayout`.
        """
        raiz = QWidget()
        capas = QStackedLayout(raiz)
        capas.setContentsMargins(0, 0, 0, 0)
        capas.setStackingMode(QStackedLayout.StackingMode.StackAll)

        self._nucleo_simple = NucleoHacu()
        capas.addWidget(self._nucleo_simple)

        rotulos = QWidget()
        rotulos.setObjectName("transparente")
        vertical = QVBoxLayout(rotulos)
        vertical.setContentsMargins(0, 0, 0, 0)
        vertical.setSpacing(0)

        esquina = QHBoxLayout()
        esquina.setContentsMargins(26, 20, 18, 0)
        esquina.setSpacing(12)
        marca = QLabel("HACU")
        marca.setObjectName("marca")
        esquina.addWidget(marca)
        esquina.addStretch(1)
        a_vista_pro = QPushButton("Vista Pro")
        a_vista_pro.setObjectName("cambioVista")
        a_vista_pro.setCursor(Qt.CursorShape.PointingHandCursor)
        a_vista_pro.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        a_vista_pro.clicked.connect(lambda: self._vistas.setCurrentIndex(self._INDICE_PRO))
        esquina.addWidget(a_vista_pro)
        vertical.addLayout(esquina)

        # Oculta hasta que haga falta: el aviso de cuidado (Cuidado.CRISIS) tenia
        # que verse en pantalla segun docs/operacion.md, y con la vista simple de
        # por medio el operador podia no estar mirando la Pro para notarlo.
        self._alerta_simple = QLabel()
        self._alerta_simple.setObjectName("alertaSimple")
        self._alerta_simple.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._alerta_simple.setWordWrap(True)
        self._alerta_simple.setVisible(False)
        margen = QHBoxLayout()
        margen.setContentsMargins(40, 16, 40, 0)
        margen.addWidget(self._alerta_simple)
        vertical.addLayout(margen)

        # El nucleo se ve por debajo de todo este hueco: aqui no hace falta
        # nada, es justo el espacio que antes ocupaba el widget del cerebro.
        vertical.addStretch(1)

        self._texto_estado_simple = QLabel(ROTULO_ESTADO[EstadoUI.REPOSO])
        self._texto_estado_simple.setObjectName("estadoSimple")
        self._texto_estado_simple.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vertical.addWidget(self._texto_estado_simple)

        self._pista_simple = QLabel()
        self._pista_simple.setObjectName("pista")
        self._pista_simple.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._pista_simple.setWordWrap(True)
        if self._voz.disponible:
            self._pista_simple.setText("Mantén pulsada la barra espaciadora para hablarle a HACU")
        else:
            self._pista_simple.setText("Sin micrófono: entra a Vista Pro para escribirle a HACU")
        vertical.addWidget(self._pista_simple)
        vertical.addSpacing(40)

        capas.addWidget(rotulos)
        # `StackAll` pone "arriba" -en pintura Y en clicks- al widget
        # CORRIENTE (`currentIndex`), no al ultimo anadido: sin esto el
        # corriente se queda por defecto en el indice 0 (el nucleo), que no
        # maneja mouse events y por eso se tragaba cualquier click antes de
        # que llegara al boton "Vista Pro".
        capas.setCurrentWidget(rotulos)
        return raiz

    def _cabecera(self) -> QFrame:
        marco = QFrame()
        marco.setObjectName("cabecera")
        marco.setFixedHeight(74)
        fila = QHBoxLayout(marco)
        fila.setContentsMargins(24, 12, 24, 12)
        fila.setSpacing(16)

        fila.addWidget(MarcaRombo())

        titulos = QVBoxLayout()
        titulos.setSpacing(0)
        marca = QLabel("HACU")
        marca.setObjectName("marca")
        submarca = QLabel("AUDACIA · UNIVERSIDAD SIMÓN BOLÍVAR")
        submarca.setObjectName("submarca")
        titulos.addWidget(marca)
        titulos.addWidget(submarca)
        fila.addLayout(titulos)
        fila.addStretch(1)

        a_vista_simple = QPushButton("‹ Vista simple")
        a_vista_simple.setObjectName("cambioVista")
        a_vista_simple.setCursor(Qt.CursorShape.PointingHandCursor)
        a_vista_simple.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        a_vista_simple.clicked.connect(lambda: self._vistas.setCurrentIndex(self._INDICE_SIMPLE))
        fila.addWidget(a_vista_simple)

        # Capsula punto+rotulo, reemplaza el punto y el texto sueltos de
        # antes -mismo patron que el `.status-pill` de la referencia del
        # tutor-.
        self._pildora_estado = PildoraEstado()
        fila.addWidget(self._pildora_estado)
        return marco

    def _columna_voz(self) -> QWidget:
        columna = QWidget()
        columna.setObjectName("transparente")  # deja ver FondoCuadricula en los margenes
        columna.setFixedWidth(348)
        vertical = QVBoxLayout(columna)
        vertical.setContentsMargins(22, 22, 22, 22)
        vertical.setSpacing(14)

        # Tarjeta de vidrio con el nombre del agente -equivalente al panel
        # de intro (eyebrow + titulo + descripcion) de la referencia-.
        intro = QFrame()
        intro.setObjectName("tarjetaVidrio")
        intro_v = QVBoxLayout(intro)
        intro_v.setContentsMargins(14, 11, 14, 12)
        intro_v.setSpacing(3)
        ojo_intro = QLabel("AUDACIA · AGENTE")
        ojo_intro.setObjectName("eyebrow")
        intro_v.addWidget(ojo_intro)
        titulo_intro = QLabel("HACU")
        titulo_intro.setObjectName("introTitulo")
        intro_v.addWidget(titulo_intro)
        descripcion_intro = QLabel("Asistente conversacional del stand, cien por ciento local.")
        descripcion_intro.setObjectName("introTexto")
        descripcion_intro.setWordWrap(True)
        intro_v.addWidget(descripcion_intro)
        vertical.addWidget(intro)

        self._nucleo = NucleoHacu()
        vertical.addWidget(self._nucleo, 1)

        # Misma tarjeta de vidrio para el medidor: la "actividad" de la
        # referencia, con su propio rotulo eyebrow.
        actividad = QFrame()
        actividad.setObjectName("tarjetaVidrio")
        actividad_v = QVBoxLayout(actividad)
        actividad_v.setContentsMargins(14, 10, 14, 12)
        actividad_v.setSpacing(7)
        ojo_actividad = QLabel("ACTIVIDAD")
        ojo_actividad.setObjectName("eyebrow")
        actividad_v.addWidget(ojo_actividad)
        self._medidor = MedidorNivel()
        actividad_v.addWidget(self._medidor)
        vertical.addWidget(actividad)

        self._boton = BotonHablar()
        # El boton nunca toma el foco de teclado: si lo tuviera, Qt se quedaria
        # con la barra espaciadora para su propio "pulsar boton" y la ventana no
        # llegaria a ver la tecla.
        self._boton.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._boton.pulsado.connect(self._empezar_a_escuchar)
        self._boton.soltado.connect(self._dejar_de_escuchar)
        vertical.addWidget(self._boton)

        self._pista = QLabel("Mantén pulsada la barra espaciadora")
        self._pista.setObjectName("pista")
        self._pista.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._pista.setWordWrap(True)
        vertical.addWidget(self._pista)

        if not self._voz.disponible:
            self._boton.setEnabled(False)
            self._pista.setText("Sin micrófono: escribe abajo para hablar con HACU")
        return columna

    def _columna_conversacion(self) -> QWidget:
        columna = QWidget()
        vertical = QVBoxLayout(columna)
        vertical.setContentsMargins(0, 0, 0, 0)
        vertical.setSpacing(0)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        lienzo = QWidget()
        self._hilo_mensajes = QVBoxLayout(lienzo)
        self._hilo_mensajes.setContentsMargins(24, 22, 24, 22)
        self._hilo_mensajes.setSpacing(14)
        self._hilo_mensajes.addStretch(1)
        self._scroll.setWidget(lienzo)
        vertical.addWidget(self._scroll, 1)

        barra = QFrame()
        barra.setObjectName("piePagina")
        fila = QHBoxLayout(barra)
        fila.setContentsMargins(24, 14, 24, 14)
        fila.setSpacing(12)
        self._entrada = QLineEdit()
        # Solo toma el teclado si alguien hace clic en ella. Por defecto se lo
        # quedaba nada mas abrir, y entonces la barra espaciadora escribia un
        # espacio en vez de abrir el microfono: habia que hacer clic fuera del
        # recuadro para poder hablarle, que es justo lo contrario de lo que
        # necesita quien atiende una exhibicion.
        self._entrada.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._entrada.setPlaceholderText("…o escribe aquí y pulsa Enter")
        self._entrada.returnPressed.connect(self._enviar_escrito)
        enviar = QPushButton("Enviar")
        enviar.setObjectName("enviar")
        enviar.setCursor(Qt.CursorShape.PointingHandCursor)
        enviar.clicked.connect(self._enviar_escrito)
        fila.addWidget(self._entrada, 1)
        fila.addWidget(enviar)
        vertical.addWidget(barra)
        return columna

    def _panel_operador(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("panel")
        panel.setFixedWidth(272)
        vertical = QVBoxLayout(panel)
        vertical.setContentsMargins(18, 20, 18, 20)
        vertical.setSpacing(11)

        vertical.addWidget(titulo_panel("Flujo"))
        self._secuencia_flujo = SecuenciaFlujo()
        vertical.addWidget(self._secuencia_flujo)
        vertical.addWidget(separador())

        # Botones de previsualizacion: cambian solo el color/animacion del
        # nucleo (ver `_previsualizar_estado`), para que el operador vea como
        # se ve cada estado sin esperar a que ocurra de verdad. No tocan
        # `self._estado`: la pastilla y el flujo siguen mostrando el estado
        # real, y el proximo evento real vuelve a mandar en el nucleo.
        vertical.addWidget(titulo_panel("Vista previa del núcleo"))
        # El panel es angosto (272px fijos): una cuadricula 2x2 dejaba cada
        # boton en ~114px, demasiado estrecho para "Escuchando" a este tamano
        # de letra. Una lista de una columna, igual que el resto de botones
        # del panel, usa el ancho completo y queda consistente con ellos.
        for estado in EstadoUI:
            boton = QPushButton(ROTULO_ESTADO[estado])
            boton.setObjectName("modoPreview")
            boton.setCursor(Qt.CursorShape.PointingHandCursor)
            boton.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            boton.setToolTip("Solo cambia el color del núcleo, no el estado real")
            boton.clicked.connect(lambda _checked=False, e=estado: self._previsualizar_estado(e))
            vertical.addWidget(boton)
        vertical.addWidget(separador())

        vertical.addWidget(titulo_panel("Visitante"))
        self._etiqueta_perfil = QLabel("—")
        self._etiqueta_perfil.setWordWrap(True)
        vertical.addWidget(self._etiqueta_perfil)
        self._nombre = QLineEdit()
        self._nombre.setPlaceholderText("Fijar nombre a mano")
        self._nombre.returnPressed.connect(self._fijar_nombre)
        vertical.addWidget(self._nombre)
        anonimo = QPushButton("Nuevo visitante")
        anonimo.clicked.connect(self._nuevo_visitante)
        vertical.addWidget(anonimo)

        vertical.addWidget(separador())
        vertical.addWidget(titulo_panel("Audiencia"))
        self._audiencia = QComboBox()
        self._audiencia.addItems(list(PERFILES_AUDIENCIA))
        self._audiencia.setCurrentText(self._comp.sesion.estado.perfil_audiencia)
        self._audiencia.currentTextChanged.connect(self._cambiar_audiencia)
        vertical.addWidget(self._audiencia)
        self._trivia = QCheckBox("Modo trivia")
        self._trivia.toggled.connect(self._cambiar_trivia)
        vertical.addWidget(self._trivia)

        vertical.addWidget(separador())
        vertical.addWidget(titulo_panel("Voz"))
        self._continua = QCheckBox("Escucha automática")
        self._continua.setEnabled(self._voz.disponible)
        self._continua.setChecked(False)
        self._continua.toggled.connect(self._cambiar_escucha_continua)
        vertical.addWidget(self._continua)
        callar = QPushButton("Callar a HACU")
        callar.clicked.connect(self._voz.silenciar)
        callar.setEnabled(self._voz.puede_hablar)
        vertical.addWidget(callar)

        # El sistema no siempre elige la tarjeta que uno cree: en una portatil con
        # webcam, base de conexiones y diadema hay cinco entradas. Aqui se elige a
        # mano, sin variables de entorno ni reiniciar la aplicacion.
        vertical.addWidget(etiqueta_campo("Micrófono"))
        self._caja_microfono = QComboBox()
        self._caja_microfono.setToolTip("Se aplica en la siguiente escucha")
        vertical.addWidget(self._caja_microfono)
        vertical.addWidget(etiqueta_campo("Altavoz"))
        self._caja_altavoz = QComboBox()
        self._caja_altavoz.setToolTip("Se aplica en la siguiente frase")
        vertical.addWidget(self._caja_altavoz)
        refrescar = QPushButton("Buscar dispositivos")
        refrescar.clicked.connect(self._cargar_dispositivos)
        vertical.addWidget(refrescar)
        self._cargar_dispositivos()
        self._caja_microfono.currentIndexChanged.connect(self._cambiar_entrada)
        self._caja_altavoz.currentIndexChanged.connect(self._cambiar_salida)

        vertical.addWidget(separador())
        vertical.addWidget(titulo_panel("Memoria"))
        auditar = QPushButton("Ver lo que recuerda")
        auditar.clicked.connect(self._auditar_memoria)
        vertical.addWidget(auditar)
        transcribir = QPushButton("Copiar la conversación")
        transcribir.setToolTip("Abre la conversación entera en texto plano (Ctrl+T)")
        transcribir.clicked.connect(self._mostrar_transcripcion)
        vertical.addWidget(transcribir)
        limpiar = QPushButton("Limpiar la pantalla")
        limpiar.clicked.connect(self._limpiar_conversacion)
        vertical.addWidget(limpiar)
        purgar = QPushButton("Borrar TODO")
        purgar.setObjectName("peligro")
        purgar.clicked.connect(self._purgar)
        vertical.addWidget(purgar)

        vertical.addStretch(1)
        vertical.addWidget(separador())
        salir = QPushButton("Salir de HACU")
        salir.setObjectName("peligro")
        salir.setToolTip("Cierra la aplicación entera (pide confirmación)")
        salir.clicked.connect(self._confirmar_salida)
        vertical.addWidget(salir)
        ayuda = QLabel("F9 oculta este panel · F11 pantalla completa · Esc callar")
        ayuda.setObjectName("pista")
        ayuda.setWordWrap(True)
        vertical.addWidget(ayuda)
        return panel

    def _pie(self) -> QFrame:
        marco = QFrame()
        marco.setObjectName("piePagina")
        marco.setFixedHeight(56)
        fila = QHBoxLayout(marco)
        fila.setContentsMargins(24, 8, 24, 8)
        fila.setSpacing(28)
        self._m_perfil = Metrica("perfil", self._comp.sesion.usuario_activo)
        self._m_intencion = Metrica("corpus", "—")
        self._m_latencia = Metrica("latencia", "—")
        self._m_velocidad = Metrica("tokens/s", "—")
        self._m_voz = Metrica("voz", self._resumen_voz())
        for metrica in (self._m_perfil, self._m_intencion, self._m_latencia,
                        self._m_velocidad, self._m_voz):
            fila.addWidget(metrica)
        fila.addStretch(1)
        return marco

    def _atajos(self) -> None:
        QShortcut(QKeySequence("F9"), self, activated=self._alternar_panel)
        QShortcut(QKeySequence("Ctrl+T"), self, activated=self._mostrar_transcripcion)
        QShortcut(QKeySequence("F11"), self, activated=self._alternar_pantalla)
        QShortcut(QKeySequence("Ctrl+M"), self, activated=self._alternar_vista)
        QShortcut(QKeySequence("Esc"), self, activated=self._callar)
        # A pantalla completa no hay barra de titulo que cerrar, y Alt+F4 no es
        # algo que se le pida a quien atiende una exhibicion. Pasa por la misma
        # confirmacion que el boton "Salir de HACU": un Ctrl+Q sin querer en
        # plena visita no deberia cerrar la aplicacion de una.
        QShortcut(QKeySequence("Ctrl+Q"), self, activated=self._confirmar_salida)

    def _callar(self) -> None:
        """Esc: corta la voz y recupera el teclado si se habia quedado en el texto."""
        self._voz.silenciar()
        self._entrada.clearFocus()
        self.setFocus()

    def _confirmar_salida(self) -> None:
        """Cierra HACU, pero solo tras confirmar.

        A diferencia del resto de atajos, cerrar la aplicacion en plena
        exhibicion no tiene vuelta atras -corta a quien este hablando con
        HACU en ese momento-, asi que pasa por el mismo patron de
        confirmacion que `_purgar` en vez de actuar directo.
        """
        respuesta = QMessageBox.question(
            self, "Cerrar HACU",
            "Se va a cerrar la aplicación. Si hay alguien hablando con HACU "
            "ahora mismo, se corta la conversación.\n\n¿Continuar?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if respuesta is QMessageBox.StandardButton.Yes:
            self.close()

    # ------------------------------------------------------------------ eventos

    def keyPressEvent(self, evento) -> None:  # noqa: N802
        if evento.key() == Qt.Key.Key_Space and not evento.isAutoRepeat() \
                and not self._entrada.hasFocus():
            self._boton.apretar()
            return
        super().keyPressEvent(evento)

    def keyReleaseEvent(self, evento) -> None:  # noqa: N802
        if evento.key() == Qt.Key.Key_Space and not evento.isAutoRepeat():
            self._boton.soltar()
            return
        super().keyReleaseEvent(evento)

    def closeEvent(self, evento) -> None:  # noqa: N802
        if self._escucha is not None:
            self._escucha.detener()
            self._escucha.wait(2000)
        self._voz.cerrar()
        super().closeEvent(evento)

    # -------------------------------------------------------------------- estado

    def _cambiar_estado(self, estado: EstadoUI) -> None:
        self._estado = estado
        self._nucleo.set_estado(estado)
        self._nucleo_simple.set_estado(estado)
        self._pildora_estado.set_estado(estado)
        self._secuencia_flujo.set_estado(estado)
        color = COLOR_ESTADO[estado]
        self._texto_estado_simple.setText(ROTULO_ESTADO[estado])
        self._texto_estado_simple.setStyleSheet(f"color: {color};")

    def _previsualizar_estado(self, estado: EstadoUI) -> None:
        """Boton de "Vista previa del núcleo": solo cambia como se ve el
        nucleo, para que el operador lo muestre sin esperar a que el estado
        ocurra de verdad. No toca `self._estado` ni la pastilla/el flujo -el
        proximo evento real (`_cambiar_estado`) vuelve a mandar-.
        """
        self._nucleo.set_estado(estado)
        self._nucleo_simple.set_estado(estado)

    def _refrescar_nivel(self) -> None:
        escuchando = self._estado is EstadoUI.ESCUCHANDO
        nivel = self._voz.nivel
        self._nucleo.set_nivel(nivel)
        self._nucleo_simple.set_nivel(nivel)
        self._medidor.set_nivel(nivel, escuchando)
        if self._estado is EstadoUI.HABLANDO and not self._voz.hablando:
            self._cambiar_estado(EstadoUI.REPOSO)
        if self._auto_pendiente and not self._voz.hablando:
            self._auto_pendiente = False
            if self._voz.disponible:
                self._continua.setChecked(True)   # dispara la calibracion
        if self._escucha is not None:
            self._escucha.pausar(self._voz.hablando or self._turno is not None)

    def _refrescar_perfil(self) -> None:
        """Mantiene al dia el recuento de hechos, que llega despues del turno."""
        descripcion = self._descripcion_perfil()
        if descripcion != self._etiqueta_perfil.text():
            self._etiqueta_perfil.setText(descripcion)

    def _resumen_voz(self) -> str:
        oido = "oído" if self._voz.disponible else "sin oído"
        boca = "voz" if self._voz.puede_hablar else "muda"
        return f"{oido} · {boca}"

    # -------------------------------------------------------------------- hablar

    @Slot()
    def _empezar_a_escuchar(self) -> None:
        if not self._voz.disponible or self._turno is not None:
            return
        self._voz.iniciar_escucha()
        self._cambiar_estado(EstadoUI.ESCUCHANDO)
        self._pista.setText("Suelta cuando termines")
        self._pista_simple.setText("Suelta cuando termines")

    @Slot()
    def _dejar_de_escuchar(self) -> None:
        if self._estado is not EstadoUI.ESCUCHANDO:
            return
        self._cambiar_estado(EstadoUI.PENSANDO)
        self._pista.setText("Transcribiendo…")
        self._pista_simple.setText("Transcribiendo…")
        self._transcripcion = TrabajadorTranscripcion(self._voz, self._log, self)
        self._transcripcion.transcrito.connect(self._con_transcripcion)
        self._transcripcion.fallo.connect(self._con_fallo)
        self._transcripcion.start()

    @Slot(str, bool, float)
    def _con_transcripcion(self, texto: str, otro_hablante: bool, similitud: float) -> None:
        self._transcripcion = None
        self._pista.setText("Mantén pulsada la barra espaciadora")
        if self._voz.disponible:
            self._pista_simple.setText("Mantén pulsada la barra espaciadora para hablarle a HACU")
        if not texto.strip():
            self._cambiar_estado(EstadoUI.REPOSO)
            self._anotar("No se entendió nada. Acércate al micrófono e inténtalo otra vez.")
            return
        if otro_hablante:
            self._cerrar_perfil_por_voz(similitud)
        self._lanzar_turno(texto.strip())

    def _cerrar_perfil_por_voz(self, similitud: float) -> None:
        """Suena otra persona: se cierra el perfil anterior antes de responderle.

        Es lo que evita que el siguiente visitante herede el nombre y los hechos
        del anterior. No identifica a nadie: solo nota que el timbre cambio.
        """
        anterior = self._comp.sesion.usuario_activo
        self._comp.sesion.identidad.reiniciar()
        self._voz.olvidar_hablante()
        self._etiqueta_perfil.setText(self._descripcion_perfil())
        self._m_perfil.set(self._comp.sesion.usuario_activo)
        detalle = f" (similitud {similitud:.2f})" if similitud >= 0 else ""
        self._anotar(f"👥 Suena otra persona{detalle}. Se cerró el perfil de {anterior}.")

    @Slot()
    def _enviar_escrito(self) -> None:
        texto = self._entrada.text().strip()
        if not texto or self._turno is not None:
            return
        self._entrada.clear()
        # Devuelve el teclado a la ventana: tras enviar por escrito, la barra
        # espaciadora tiene que volver a servir para hablar.
        self._entrada.clearFocus()
        self.setFocus()
        self._lanzar_turno(texto)

    def _lanzar_turno(self, texto: str) -> None:
        self._anadir_burbuja(self._comp.sesion.usuario_activo, es_hacu=False, texto=texto)
        self._burbuja_actual = self._anadir_burbuja("Hacu", es_hacu=True)
        self._cambiar_estado(EstadoUI.PENSANDO)
        self._entrada.setEnabled(False)
        self._boton.setEnabled(False)

        locutor = self._voz.locutor() if self._voz.puede_hablar else None
        self._turno = TrabajadorTurno(self._comp.sesion, texto, locutor, self._log, self)
        self._turno.token.connect(self._con_token)
        self._turno.listo.connect(self._con_turno)
        self._turno.fallo.connect(self._con_fallo)
        self._turno.start()

    @Slot(str)
    def _con_token(self, fragmento: str) -> None:
        if self._burbuja_actual is None:
            return
        if self._estado is not EstadoUI.HABLANDO:
            self._cambiar_estado(EstadoUI.HABLANDO)
        self._burbuja_actual.anadir(fragmento)
        self._al_fondo()

    @Slot(object)
    def _con_turno(self, resultado: ResultadoTurno) -> None:
        self._turno = None
        self._entrada.setEnabled(True)
        self._boton.setEnabled(self._voz.disponible)
        self._m_perfil.set(resultado.usuario)
        self._m_intencion.set(resultado.intencion.value.title())
        self._m_latencia.set(f"{resultado.segundos:.2f} s")
        self._m_velocidad.set(f"{resultado.tokens_por_segundo:.0f}")
        self._etiqueta_perfil.setText(self._descripcion_perfil())
        if resultado.cuidado is Cuidado.CRISIS:
            # El operador tiene que enterarse AHORA, no al leer el log de noche.
            # Un dialogo modal delante del visitante seria peor: esto se queda en
            # el hilo, destacado, y en el rotulo del pie.
            self._anotar("⚠  " + AVISO_OPERADOR)
            self._alerta_simple.setText("⚠  " + AVISO_OPERADOR)
            self._alerta_simple.setVisible(True)
            self._log.warning("Turno de cuidado atendido con el texto fijo")
        if resultado.migrado:
            self._anotar(f"Perfil migrado a {resultado.usuario}.")
        if not self._voz.hablando:
            self._cambiar_estado(EstadoUI.REPOSO)

    @Slot(str)
    def _con_fallo(self, mensaje: str) -> None:
        self._turno = None
        self._transcripcion = None
        self._entrada.setEnabled(True)
        self._boton.setEnabled(self._voz.disponible)
        self._cambiar_estado(EstadoUI.ERROR)
        self._anotar(f"Error: {mensaje}")

    # ------------------------------------------------------------- conversacion

    def _anadir_burbuja(self, autor: str, es_hacu: bool, texto: str = "") -> BurbujaMensaje:
        burbuja = BurbujaMensaje(autor, es_hacu, texto)
        self._hilo_mensajes.insertWidget(self._hilo_mensajes.count() - 1, burbuja)
        self._al_fondo()
        return burbuja

    def _anotar(self, texto: str) -> None:
        etiqueta = QLabel(texto)
        etiqueta.setObjectName("pista")
        etiqueta.setWordWrap(True)
        etiqueta.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hilo_mensajes.insertWidget(self._hilo_mensajes.count() - 1, etiqueta)
        self._al_fondo()

    def _al_fondo(self) -> None:
        QTimer.singleShot(0, lambda: self._scroll.verticalScrollBar().setValue(
            self._scroll.verticalScrollBar().maximum()))

    def _saludar(self) -> None:
        self._cambiar_estado(EstadoUI.REPOSO)
        self._etiqueta_perfil.setText(self._descripcion_perfil())
        for problema in self._voz.problemas:
            self._anotar(f"⚠️  {problema}")
        self._anotar("HACU está listo. Mantén pulsada la barra espaciadora para hablarle, "
                     "o escribe abajo.")
        self._anotar("Para cerrar: Ctrl+Q, o Ctrl+C en la terminal.")
        self._dar_la_bienvenida()

    def _dar_la_bienvenida(self) -> None:
        """La primera frase de HACU: burbuja normal, hablada si hay altavoz.

        Es texto fijo y no una respuesta del modelo (ver `HacuSession.saludar`),
        pero se ve y se oye igual que cualquier otro turno suyo, porque para el
        visitante lo es. Se repite al pasar al siguiente visitante: quien acaba de
        acercarse tiene que oir la invitacion a decir su nombre.
        """
        texto = self._comp.sesion.saludar(self._cfg.saludo_inicial)
        if not texto:
            return
        self._anadir_burbuja("Hacu", es_hacu=True, texto=texto)
        if self._voz.puede_hablar:
            self._voz.decir(texto)
            # El reloj de nivel devuelve el estado a REPOSO en cuanto calla.
            self._cambiar_estado(EstadoUI.HABLANDO)

    def transcripcion(self) -> str:
        """La conversacion en texto plano, tal como se ve en pantalla.

        Se arma del hilo de burbujas y no de la base de datos por dos motivos: la
        base guarda solo los ultimos mensajes y solo los del perfil activo, y aqui
        interesa la sesion entera, con los cambios de visitante y los avisos del
        sistema incluidos. Lo que se copia es lo que ocurrio.
        """
        lineas = [
            "HACU · AudacIA · Universidad Simón Bolívar",
            f"Transcripción de la conversación — {datetime.now().strftime('%d/%m/%Y %H:%M')}",
            "=" * 72,
            "",
        ]
        for i in range(self._hilo_mensajes.count()):
            widget = self._hilo_mensajes.itemAt(i).widget()
            if isinstance(widget, BurbujaMensaje):
                cuerpo = widget.texto.strip()
                if not cuerpo:
                    continue
                lineas.append(f"[{widget.hora}] {widget.autor.upper()}:")
                lineas.extend(f"    {linea}" for linea in cuerpo.splitlines())
                lineas.append("")
            elif isinstance(widget, QLabel) and widget.text().strip():
                lineas.append(f"    · {widget.text().strip()} ·")
                lineas.append("")
        return "\n".join(lineas)

    def _mostrar_transcripcion(self) -> None:
        dialogo = DialogoTranscripcion(self.transcripcion(), self)
        dialogo.show()

    def _limpiar_conversacion(self) -> None:
        while self._hilo_mensajes.count() > 1:
            elemento = self._hilo_mensajes.takeAt(0)
            if elemento.widget():
                elemento.widget().deleteLater()
        self._comp.db.clear_short_term(self._comp.sesion.usuario_activo)
        self._anotar("Pantalla y conversación reciente limpiadas.")

    # ----------------------------------------------------------------- operador

    def _descripcion_perfil(self) -> str:
        usuario = self._comp.sesion.usuario_activo
        hechos = self._comp.db.count_episodes(usuario)
        return f"{usuario} · {hechos} hecho{'s' if hechos != 1 else ''} recordado" \
               f"{'s' if hechos != 1 else ''}"

    def _fijar_nombre(self) -> None:
        nombre = self._nombre.text().strip()
        if not nombre:
            return
        fijado = self._comp.sesion.identidad.fijar_manualmente(nombre)
        self._nombre.clear()
        if fijado is None:
            self._anotar(f"«{nombre}» no es un nombre válido: no se fijó.")
            return
        self._etiqueta_perfil.setText(self._descripcion_perfil())
        self._m_perfil.set(fijado)
        self._anotar(f"Perfil activo: {fijado}.")

    def _nuevo_visitante(self) -> None:
        self._comp.sesion.identidad.reiniciar()
        self._voz.olvidar_hablante()
        self._etiqueta_perfil.setText(self._descripcion_perfil())
        self._m_perfil.set(self._comp.sesion.usuario_activo)
        self._limpiar_conversacion()
        self._alerta_simple.setVisible(False)
        self._dar_la_bienvenida()

    def _cambiar_audiencia(self, perfil: str) -> None:
        self._comp.sesion.estado.perfil_audiencia = perfil
        self._anotar(f"Audiencia: {perfil}.")

    def _cambiar_trivia(self, activo: bool) -> None:
        self._comp.sesion.estado.trivia = activo
        self._anotar("Modo trivia " + ("activado." if activo else "desactivado."))

    def _cargar_dispositivos(self) -> None:
        """Rellena los dos desplegables sin disparar los `currentIndexChanged`."""
        dispositivos = self._voz.dispositivos()
        for caja, filtro, actual in (
            (self._caja_microfono, lambda d: d.es_entrada, self._voz.entrada_actual),
            (self._caja_altavoz, lambda d: d.es_salida, self._voz.salida_actual),
        ):
            caja.blockSignals(True)
            caja.clear()
            caja.addItem("Predeterminado", None)
            for d in dispositivos:
                if not filtro(d):
                    continue
                caja.addItem(f"[{d.indice}] {d.nombre[:46]}", d.indice)
                # El panel es estrecho y los nombres de tarjeta son largos: el
                # nombre entero queda en el tooltip de cada linea.
                caja.setItemData(caja.count() - 1, d.etiqueta(), _TOOLTIP)
            posicion = caja.findData(actual)
            caja.setCurrentIndex(posicion if posicion >= 0 else 0)
            caja.setEnabled(caja.count() > 1)
            caja.blockSignals(False)
        if not dispositivos:
            sin = "No se detectó ninguna tarjeta de audio"
            self._caja_microfono.setToolTip(sin)
            self._caja_altavoz.setToolTip(sin)

    def _cambiar_entrada(self) -> None:
        self._voz.usar_entrada(self._caja_microfono.currentData())
        self._anotar(f"Micrófono: {self._caja_microfono.currentText()}")

    def _cambiar_salida(self) -> None:
        self._voz.usar_salida(self._caja_altavoz.currentData())
        self._anotar(f"Altavoz: {self._caja_altavoz.currentText()}")

    def _cambiar_escucha_continua(self, activo: bool) -> None:
        if not self._voz.disponible:
            return
        if not activo:
            if self._escucha is not None:
                self._escucha.detener()
                self._escucha = None
            self._pista.setText("Mantén pulsada la barra espaciadora")
            return
        self._anotar("Calibrando el ruido de la sala…")
        tarea = TrabajadorTarea(self._voz.calibrar, self)
        tarea.terminado.connect(self._arrancar_escucha_continua)
        tarea.fallo.connect(self._con_fallo)
        self._tareas.append(tarea)
        tarea.start()

    @Slot(object)
    def _arrancar_escucha_continua(self, umbral: object) -> None:
        self._anotar(f"Escucha automática activa (umbral {float(umbral):.4f}). "
                     "Con altavoz abierto HACU puede oírse a sí mismo.")
        self._pista.setText("Escucha automática: habla cuando quieras")
        self._escucha = TrabajadorEscuchaContinua(self._voz, self._log, self)
        self._escucha.transcrito.connect(self._con_transcripcion)
        self._escucha.fallo.connect(self._con_fallo)
        self._escucha.start()

    def _auditar_memoria(self) -> None:
        usuario = self._comp.sesion.usuario_activo
        hechos = self._comp.db.get_all_episodes(usuario)
        cuerpo = "\n".join(f"• {h}" for h in hechos) or "No recuerda nada de esta persona."
        QMessageBox.information(self, f"Memoria de {usuario}", cuerpo)

    def _purgar(self) -> None:
        respuesta = QMessageBox.question(
            self, "Borrar toda la memoria",
            "Se borran los perfiles, las conversaciones y los hechos de TODOS los "
            "visitantes. No se puede deshacer.\n\n¿Continuar?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if respuesta is not QMessageBox.StandardButton.Yes:
            return
        borrados = self._comp.sesion.olvidar_todo()
        self._voz.olvidar_hablante()
        self._limpiar_conversacion()
        self._alerta_simple.setVisible(False)
        self._anotar(f"Memoria borrada: {borrados} perfiles.")
        self._m_perfil.set(self._comp.sesion.usuario_activo)
        self._etiqueta_perfil.setText(self._descripcion_perfil())
        self._audiencia.setCurrentText(self._comp.sesion.estado.perfil_audiencia)
        self._trivia.setChecked(self._comp.sesion.estado.trivia)
        self._dar_la_bienvenida()

    def _alternar_vista(self) -> None:
        """Ctrl+M: salta entre la vista simple y la Pro sin buscar el boton."""
        actual = self._vistas.currentIndex()
        self._vistas.setCurrentIndex(
            self._INDICE_PRO if actual == self._INDICE_SIMPLE else self._INDICE_SIMPLE
        )

    def _alternar_panel(self) -> None:
        self._panel.setVisible(not self._panel.isVisible())

    def _alternar_pantalla(self) -> None:
        self.showNormal() if self.isFullScreen() else self.showFullScreen()


def lanzar(componentes: Componentes, config: AppConfig, voz: ServicioDeVoz) -> int:
    """Levanta la aplicacion Qt y cede el control hasta que se cierra la ventana."""
    aplicacion = QApplication.instance() or QApplication([])
    aplicacion.setApplicationName("HACU")
    ventana = VentanaHacu(componentes, config, voz)
    ventana.show()

    # Ctrl+C en la terminal. El bucle de eventos de Qt vive en C++ y no devuelve
    # el control al interprete, asi que la senal SIGINT se queda encolada hasta
    # que Python vuelve a ejecutar algo: sin esto aterrizaba dentro del
    # temporizador del medidor, imprimia un KeyboardInterrupt y la ventana
    # seguia viva. El latido garantiza ese hueco cada 150 ms, y el manejador
    # cierra la ventana de verdad (pasa por `closeEvent`) en vez de matar el
    # proceso y dejar el modelo y la base a medias.
    def _apagar(*_: object) -> None:
        print("\n🛑 Cerrando HACU...")
        ventana.close()
        aplicacion.quit()

    latido = QTimer()
    latido.timeout.connect(lambda: None)
    latido.start(150)
    anterior = signal.signal(signal.SIGINT, _apagar)
    try:
        return aplicacion.exec()
    finally:
        latido.stop()
        signal.signal(signal.SIGINT, anterior)
