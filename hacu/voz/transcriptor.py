"""Reconocimiento de voz con faster-whisper.

faster-whisper (CTranslate2) y no openai-whisper: mismo modelo, entre tres y cinco
veces mas rapido y con cuantizacion int8, que es lo que permite meterlo en la misma
tarjeta donde ya vive el Llama de 8B sin quedarse sin VRAM.

El modelo se carga perezosamente en la primera transcripcion, no al construirlo:
arrancar la exhibicion ya tarda bastante cargando el LLM y el indice, y si alguien
levanta HACU sin microfono no tiene sentido pagar la carga.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from ..config import VozConfig


@dataclass(frozen=True)
class Confianza:
    """Lo que el propio Whisper piensa de lo que acaba de transcribir.

    Existe para no tener que preguntarle a un modelo de 8B si una frase "tiene
    sentido" —lo acertaria a medias y costaria una inferencia entera por turno—.
    faster-whisper ya calcula estas tres cifras en cada segmento y hasta ahora
    se tiraban a la basura:

    - `logprob`: la probabilidad media, en logaritmo, de los tokens elegidos.
      Cuanto mas cerca de 0, mas seguro. Es la senal principal.
    - `sin_voz`: probabilidad de que el audio no fuera habla.
    - `compresion`: el cociente de compresion del texto. Muy alto significa
      texto repetitivo, que es como se ve una alucinacion de Whisper.

    NO se fija aqui ningun umbral. El corte tiene que salir de medir sesiones
    reales de Daniel —frases bien oidas contra frases mal oidas— y no de un
    numero elegido a ojo, que es justo como se cuela un guardarrail que
    interrumpe al visitante cada tres preguntas.
    """

    logprob: float
    sin_voz: float
    compresion: float

    def __str__(self) -> str:
        return (f"logprob {self.logprob:+.2f} · sin_voz {self.sin_voz:.2f} "
                f"· compresion {self.compresion:.2f}")


def _confianza(trozos: list) -> Confianza | None:
    """El segmento MENOS seguro manda: basta una parte mal oida para dudar."""
    if not trozos:
        return None
    return Confianza(
        logprob=min(float(t.avg_logprob) for t in trozos),
        sin_voz=max(float(t.no_speech_prob) for t in trozos),
        compresion=max(float(t.compression_ratio) for t in trozos),
    )


class Transcriptor(Protocol):
    """Lo unico que el resto del sistema necesita saber del reconocimiento."""

    def transcribir(self, audio: np.ndarray) -> str: ...

    def cerrar(self) -> None: ...


class TranscriptorMudo:
    """Doble inerte: permite levantar la interfaz sin modelo de voz."""

    def transcribir(self, audio: np.ndarray) -> str:  # noqa: ARG002
        return ""

    def cerrar(self) -> None:
        return None


class TranscriptorWhisper:
    """faster-whisper sobre el audio del microfono."""

    # Audio mas corto que esto es un clic o un roce del boton, no una pregunta.
    _MINIMO_SEGUNDOS = 0.35

    def __init__(self, config: VozConfig, logger: logging.Logger) -> None:
        self._cfg = config
        self._log = logger.getChild("stt")
        self._modelo = None
        self._prompt = ", ".join(config.vocabulario)
        self.ultima_confianza: Confianza | None = None

    @property
    def cargado(self) -> bool:
        return self._modelo is not None

    def precargar(self) -> None:
        """Carga el modelo por adelantado para que la primera pregunta no espere."""
        self._asegurar_modelo()

    def transcribir(self, audio: np.ndarray) -> str:
        """Devuelve el texto reconocido, o cadena vacia si no habia nada que oir."""
        if audio.size < self._cfg.frecuencia * self._MINIMO_SEGUNDOS:
            return ""
        modelo = self._asegurar_modelo()
        segmentos, _ = modelo.transcribe(
            audio.astype(np.float32),
            language=self._cfg.idioma,
            task="transcribe",
            # Siembra los nombres propios de la exhibicion. Sin esto "Holosand"
            # se transcribe "olo san" y el router no reconoce el dominio.
            initial_prompt=self._prompt,
            beam_size=5,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        trozos = list(segmentos)
        texto = " ".join(s.text.strip() for s in trozos).strip()
        self.ultima_confianza = _confianza(trozos)
        self._log.debug("Transcrito (%d muestras) [%s]: %r",
                        audio.size, self.ultima_confianza, texto[:120])
        return texto

    def cerrar(self) -> None:
        self._modelo = None

    # ---------------------------------------------------------------- internos

    def _asegurar_modelo(self):
        if self._modelo is not None:
            return self._modelo
        try:
            from faster_whisper import WhisperModel  # noqa: PLC0415
        except ImportError as error:
            raise RuntimeError(
                "Falta faster-whisper. Instala 'pip install faster-whisper' "
                "o arranca sin voz."
            ) from error

        self._log.info("Cargando modelo de reconocimiento %s (%s, %s)",
                       self._cfg.modelo_stt, self._cfg.dispositivo_stt, self._cfg.computo_stt)
        self._modelo = self._abrir(WhisperModel, self._cfg.dispositivo_stt,
                                   self._cfg.computo_stt)
        if self._modelo is None:
            # Sin CUDA utilizable, `small` en CPU sigue siendo usable (~2 s por
            # frase). Es preferible a que la exhibicion se quede sin oido.
            self._log.warning("Sin CUDA para el reconocimiento; se recae en CPU")
            self._modelo = self._abrir(WhisperModel, "cpu", "int8")
        if self._modelo is None:
            raise RuntimeError(
                f"No se pudo abrir el modelo de reconocimiento '{self._cfg.modelo_stt}'. "
                "Si es la primera vez, hace falta conexion para descargarlo."
            )
        return self._modelo

    def _abrir(self, WhisperModel, dispositivo: str, computo: str):  # noqa: N803
        """Abre el modelo en ese dispositivo, la copia en disco antes que la red.

        Igual que con el embedding del RAG: faster-whisper consulta HuggingFace al
        abrir el modelo aunque ya este descargado, y en una sala sin red eso deja
        a HACU sin oido. El primer intento es estrictamente local; solo si no hay
        copia se descarga, que es lo que toca el primer dia.
        """
        for solo_local in (True, False):
            try:
                modelo = WhisperModel(
                    self._cfg.modelo_stt, device=dispositivo, compute_type=computo,
                    local_files_only=solo_local,
                )
                if not solo_local:
                    self._log.info("Modelo de reconocimiento descargado: %s",
                                   self._cfg.modelo_stt)
                return modelo
            except Exception as error:
                if solo_local:
                    self._log.info("El reconocedor no esta en la cache local (%s); se descarga",
                                   error)
                    continue
                self._log.warning("No se pudo abrir el reconocedor en %s/%s",
                                  dispositivo, computo, exc_info=True)
        return None
