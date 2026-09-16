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
from typing import Protocol

import numpy as np

from ..config import VozConfig


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
        texto = " ".join(s.text.strip() for s in segmentos).strip()
        self._log.debug("Transcrito (%d muestras): %r", audio.size, texto[:120])
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
        try:
            self._modelo = WhisperModel(
                self._cfg.modelo_stt,
                device=self._cfg.dispositivo_stt,
                compute_type=self._cfg.computo_stt,
            )
        except Exception:
            # Sin CUDA utilizable, `small` en CPU sigue siendo usable (~2 s por
            # frase). Es preferible a que la exhibicion se quede sin oido.
            self._log.warning("Sin CUDA para el reconocimiento; se recae en CPU", exc_info=True)
            self._modelo = WhisperModel(self._cfg.modelo_stt, device="cpu", compute_type="int8")
        return self._modelo
