"""Capa de voz de HACU: oido y boca.

`ServicioDeVoz` es la unica pieza que ven la interfaz y la consola. Por dentro
coordina microfono, reconocimiento y sintesis, y por fuera ofrece cuatro verbos:
escuchar, transcribir, hablar y callarse.

Toda la capa es opcional. Si falta una dependencia o no hay tarjeta de sonido,
`ServicioDeVoz.disponible` es False y HACU sigue funcionando por escrito: una
exhibicion sin sonido es mala, pero una exhibicion caida es peor.
"""

from __future__ import annotations

import logging

from ..config import VozConfig
from .deteccion import DetectorDeVoz, Estado, ParametrosVoz, nivel_rms, umbral_desde_ruido
from .dispositivos import Dispositivo, comprobar, listar_dispositivos
from .microfono import AudioNoDisponible, Microfono
from .segmentador import SegmentadorDeFrases
from .sintetizador import (
    Sintetizador,
    SintetizadorMudo,
    cadena_de_motores,
    crear_sintetizador,
    localizar_piper,
    sintetizar_a_archivo,
)
from .transcriptor import Transcriptor, TranscriptorMudo, TranscriptorWhisper

__all__ = [
    "AudioNoDisponible", "DetectorDeVoz", "Dispositivo", "Estado", "Locutor",
    "Microfono", "ParametrosVoz", "SegmentadorDeFrases", "ServicioDeVoz",
    "Sintetizador", "SintetizadorMudo", "Transcriptor", "TranscriptorMudo",
    "TranscriptorWhisper", "cadena_de_motores", "comprobar", "crear_sintetizador",
    "listar_dispositivos",
    "localizar_piper", "nivel_rms", "sintetizar_a_archivo", "umbral_desde_ruido",
]


class Locutor:
    """Convierte el stream de tokens de un turno en frases habladas.

    Vive un solo turno. Se alimenta desde el `on_token` de `HacuSession`, que ya
    entrega el texto filtrado por las capas de estilo: lo que se oye es exactamente
    lo que se lee y lo que se guarda.
    """

    def __init__(self, sintetizador: Sintetizador, config: VozConfig) -> None:
        self._tts = sintetizador
        self._segmentador = SegmentadorDeFrases(config.minimo_frase, config.maximo_frase)
        self.frases: list[str] = []

    def alimentar(self, fragmento: str) -> None:
        for frase in self._segmentador.alimentar(fragmento):
            self.frases.append(frase)
            self._tts.decir(frase)

    def cerrar(self) -> None:
        resto = self._segmentador.cerrar()
        if resto:
            self.frases.append(resto)
            self._tts.decir(resto)


class ServicioDeVoz:
    """Oido y boca de HACU, con apagado elegante si falta hardware."""

    def __init__(self, config: VozConfig, logger: logging.Logger) -> None:
        self._cfg = config
        self._log = logger.getChild("voz")
        self._micro: Microfono | None = None
        self._transcriptor: Transcriptor = TranscriptorMudo()
        self._tts: Sintetizador = SintetizadorMudo()
        self._umbral = 0.0
        self.problemas: list[str] = []

        if not (config.activa or config.solo_salida):
            return

        # El oido es opcional por separado: `solo_salida` monta la boca y se
        # ahorra el modelo de reconocimiento entero.
        if config.activa and not config.solo_salida:
            try:
                self._micro = Microfono(config, logger)
                self._transcriptor = TranscriptorWhisper(config, logger)
            except AudioNoDisponible as error:
                self.problemas.append(str(error))
                self._log.warning("Sin captura de audio: %s", error)
        self._tts = crear_sintetizador(config, logger)
        self.problemas.extend(comprobar(config) if self._micro else [])

    # ------------------------------------------------------------------ estado

    @property
    def disponible(self) -> bool:
        """True si al menos se puede oir al visitante."""
        return self._micro is not None

    @property
    def puede_hablar(self) -> bool:
        return not isinstance(self._tts, SintetizadorMudo)

    @property
    def motor(self) -> str:
        """Que sintetizador acabo eligiendose. Importa: uno de ellos es lento."""
        return getattr(self._tts, "nombre", "desconocido")

    @property
    def hablando(self) -> bool:
        return self._tts.hablando

    @property
    def nivel(self) -> float:
        return self._micro.nivel if self._micro else 0.0

    # ----------------------------------------------------------------- escuchar

    def calibrar(self) -> float:
        """Mide el ruido de sala. Solo hace falta en escucha automatica."""
        if self._micro is None:
            return 0.0
        self._umbral = self._micro.calibrar()
        return self._umbral

    def iniciar_escucha(self) -> None:
        """Pulsar para hablar: abre el microfono. Calla a HACU si estaba hablando."""
        if self._micro is None:
            return
        self.silenciar()
        self._micro.iniciar()

    def detener_escucha(self) -> str:
        """Cierra el microfono y devuelve lo que dijo el visitante."""
        if self._micro is None:
            return ""
        return self._transcriptor.transcribir(self._micro.detener())

    def escuchar_una_frase(self, cancelado=None) -> str:
        """Escucha automatica: espera una frase entera. Bloquea, va en un hilo."""
        if self._micro is None:
            return ""
        audio = self._micro.escuchar_hasta_silencio(self._umbral or 0.02, cancelado)
        return self._transcriptor.transcribir(audio)

    def precargar(self) -> None:
        """Carga el modelo de reconocimiento por adelantado."""
        if isinstance(self._transcriptor, TranscriptorWhisper):
            self._transcriptor.precargar()

    # ------------------------------------------------------------------- hablar

    def locutor(self) -> Locutor:
        """Un locutor para el turno que empieza."""
        return Locutor(self._tts, self._cfg)

    def decir(self, texto: str) -> None:
        self._tts.decir(texto)

    def esperar_a_que_calle(self, timeout: float | None = None) -> bool:
        """Bloquea hasta que HACU termine de hablar."""
        return self._tts.esperar(timeout)

    def silenciar(self) -> None:
        self._tts.silenciar()

    def transcribir_archivo(self, ruta) -> str:
        """Transcribe un WAV del disco. Permite probar el reconocimiento sin microfono."""
        import soundfile  # noqa: PLC0415  (solo lo necesita esta ruta de diagnostico)

        audio, frecuencia = soundfile.read(str(ruta), dtype="float32", always_2d=False)
        if getattr(audio, "ndim", 1) > 1:
            audio = audio.mean(axis=1)
        if frecuencia != self._cfg.frecuencia:
            self._log.info("El fichero esta a %d Hz; faster-whisper remuestrea solo", frecuencia)
        return self._transcriptor.transcribir(audio)

    def cerrar(self, drenar: bool = False) -> None:
        """Apaga la capa de voz. Por defecto corta: quien cierra ya no escucha."""
        if not drenar:
            self.silenciar()
        if self._micro is not None:
            self._micro.cerrar()
        self._transcriptor.cerrar()
        self._tts.cerrar(drenar=drenar)
