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
    QVBoxLayout,
    QWidget,
)

from ..bootstrap import Componentes
from ..config import AppConfig
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
    MedidorNivel,
    Metrica,
    NucleoHacu,
    separador,
    titulo_panel,
)

_REFRESCO_NIVEL_MS = 40
# El recuento de hechos no cambia deprisa; cada dos segundos sobra y no castiga
# la base de datos.
_REFRESCO_PERFIL_MS = 2000


class VentanaHacu(QMainWindow):
    """La ventana completa: conversacion, voz y mandos."""

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
        raiz = QWidget()
        self.setCentralWidget(raiz)
        vertical = QVBoxLayout(raiz)
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
        # La ventana se queda el teclado: es quien atiende la barra espaciadora.
        raiz.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFocus()

    def _cabecera(self) -> QFrame:
        marco = QFrame()
        marco.setObjectName("cabecera")
        marco.setFixedHeight(74)
        fila = QHBoxLayout(marco)
        fila.setContentsMargins(24, 12, 24, 12)
        fila.setSpacing(16)

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

        # El indicador va en su propio bloque de ancho fijo y alineado a la
        # derecha: si se deja al layout, "Escuchando" es mas ancho que "En espera"
        # y el rotulo se recorta contra el borde de la ventana.
        indicador = QWidget()
        indicador.setObjectName("transparente")
        indicador.setFixedWidth(190)
        derecha = QHBoxLayout(indicador)
        derecha.setContentsMargins(0, 0, 0, 0)
        derecha.setSpacing(9)
        derecha.addStretch(1)
        self._punto_estado = QLabel("●")
        self._texto_estado = QLabel(ROTULO_ESTADO[EstadoUI.REPOSO])
        self._texto_estado.setObjectName("estadoTexto")
        self._texto_estado.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        derecha.addWidget(self._punto_estado)
        derecha.addWidget(self._texto_estado)
        fila.addWidget(indicador)
        return marco

    def _columna_voz(self) -> QWidget:
        columna = QWidget()
        columna.setFixedWidth(348)
        vertical = QVBoxLayout(columna)
        vertical.setContentsMargins(22, 22, 22, 22)
        vertical.setSpacing(14)

        self._nucleo = NucleoHacu()
        vertical.addWidget(self._nucleo, 1)

        self._medidor = MedidorNivel()
        vertical.addWidget(self._medidor)

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

        vertical.addWidget(separador())
        vertical.addWidget(titulo_panel("Memoria"))
        auditar = QPushButton("Ver lo que recuerda")
        auditar.clicked.connect(self._auditar_memoria)
        vertical.addWidget(auditar)
        limpiar = QPushButton("Limpiar la pantalla")
        limpiar.clicked.connect(self._limpiar_conversacion)
        vertical.addWidget(limpiar)
        purgar = QPushButton("Borrar TODO")
        purgar.setObjectName("peligro")
        purgar.clicked.connect(self._purgar)
        vertical.addWidget(purgar)

        vertical.addStretch(1)
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
        QShortcut(QKeySequence("F11"), self, activated=self._alternar_pantalla)
        QShortcut(QKeySequence("Esc"), self, activated=self._callar)
        # A pantalla completa no hay barra de titulo que cerrar, y Alt+F4 no es
        # algo que se le pida a quien atiende una exhibicion.
        QShortcut(QKeySequence("Ctrl+Q"), self, activated=self.close)

    def _callar(self) -> None:
        """Esc: corta la voz y recupera el teclado si se habia quedado en el texto."""
        self._voz.silenciar()
        self._entrada.clearFocus()
        self.setFocus()

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
        self._texto_estado.setText(ROTULO_ESTADO[estado])
        color = COLOR_ESTADO[estado]
        self._punto_estado.setStyleSheet(f"color: {color}; font-size: 20px;")
        self._texto_estado.setStyleSheet(f"color: {color};")

    def _refrescar_nivel(self) -> None:
        escuchando = self._estado is EstadoUI.ESCUCHANDO
        nivel = self._voz.nivel
        self._nucleo.set_nivel(nivel)
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

    @Slot()
    def _dejar_de_escuchar(self) -> None:
        if self._estado is not EstadoUI.ESCUCHANDO:
            return
        self._cambiar_estado(EstadoUI.PENSANDO)
        self._pista.setText("Transcribiendo…")
        self._transcripcion = TrabajadorTranscripcion(self._voz, self._log, self)
        self._transcripcion.transcrito.connect(self._con_transcripcion)
        self._transcripcion.fallo.connect(self._con_fallo)
        self._transcripcion.start()

    @Slot(str, bool, float)
    def _con_transcripcion(self, texto: str, otro_hablante: bool, similitud: float) -> None:
        self._transcripcion = None
        self._pista.setText("Mantén pulsada la barra espaciadora")
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
        self._dar_la_bienvenida()

    def _cambiar_audiencia(self, perfil: str) -> None:
        self._comp.sesion.estado.perfil_audiencia = perfil
        self._anotar(f"Audiencia: {perfil}.")

    def _cambiar_trivia(self, activo: bool) -> None:
        self._comp.sesion.estado.trivia = activo
        self._anotar("Modo trivia " + ("activado." if activo else "desactivado."))

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
        borrados = self._comp.db.purge_all()
        self._comp.sesion.identidad.reiniciar()
        self._limpiar_conversacion()
        self._anotar(f"Memoria borrada: {borrados} perfiles.")
        self._m_perfil.set(self._comp.sesion.usuario_activo)

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
