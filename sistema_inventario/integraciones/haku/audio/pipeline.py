"""Pipeline orquestador de audio para Haku Inventario."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

import numpy as np
from django.conf import settings

from .analyzer import AudioAnalyzer, SignalMetrics
from .decoder import AudioDecoder, AudioDecodeError
from .normalizer import AudioNormalizer
from .vad import VoiceActivityDetector, VADResult

logger = logging.getLogger("integraciones.haku.audio")


@dataclass(frozen=True)
class AudioProcessingResult:
    """Resultado del pipeline de procesamiento de audio."""
    estado: str  # "VOZ_DETECTADA" | "SIN_VOZ"
    motivo: str  # "VOZ_DETECTADA" | "AUDIO_SILENCIOSO" | "SOLO_RUIDO" | "ERROR_DECODIFICACION"
    audio_limpio: Optional[np.ndarray]
    metricas_originales: Optional[SignalMetrics]
    metricas_procesadas: Optional[SignalMetrics]
    duracion_segundos: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "estado": self.estado,
            "motivo": self.motivo,
            "duracion_seg": round(self.duracion_segundos, 3),
            "metricas_originales": self.metricas_originales.to_dict() if self.metricas_originales else {},
            "metricas_procesadas": self.metricas_procesadas.to_dict() if self.metricas_procesadas else {},
        }


class AudioPipeline:
    """Orquestador completo: Decodificación -> Análisis -> VAD -> Normalización."""

    def __init__(
        self,
        vad_activado: bool = True,
        normalizacion_activada: bool = True,
        target_rms_dbfs: float = -18.0,
        umbral_silencio_dbfs: float = -46.0,
    ) -> None:
        self.vad_activado = vad_activado
        self.normalizacion_activada = normalizacion_activada
        self.vad = VoiceActivityDetector(umbral_silencio_dbfs=umbral_silencio_dbfs)
        self.normalizer = AudioNormalizer(target_rms_dbfs=target_rms_dbfs)

    @classmethod
    def desde_settings(cls) -> AudioPipeline:
        """Instancia el pipeline leyendo las variables de configuración de Django."""
        vad = getattr(settings, "HAKU_AUDIO_VAD", True)
        norm = getattr(settings, "HAKU_AUDIO_NORMALIZATION", True)
        rms = float(getattr(settings, "HAKU_AUDIO_TARGET_RMS_DBFS", -18.0))
        silencio = float(getattr(settings, "HAKU_AUDIO_SILENCE_THRESHOLD_DBFS", -46.0))
        return cls(
            vad_activado=vad,
            normalizacion_activada=norm,
            target_rms_dbfs=rms,
            umbral_silencio_dbfs=silencio,
        )

    def procesar(self, audio_bytes: bytes) -> AudioProcessingResult:
        """Ejecuta el pipeline completo sobre un buffer binario de audio."""
        # 1. Decodificación en memoria a 16 kHz Mono Float32
        try:
            samples_16k, duracion = AudioDecoder.decodificar_a_16k_mono(audio_bytes)
        except AudioDecodeError as exc:
            logger.warning("Fallo en decodificación de audio: %s", exc)
            return AudioProcessingResult(
                estado="SIN_VOZ",
                motivo="ERROR_DECODIFICACION",
                audio_limpio=None,
                metricas_originales=None,
                metricas_procesadas=None,
                duracion_segundos=0.0,
            )

        # 2. Análisis inicial de señal
        metricas_orig = AudioAnalyzer.analizar(samples_16k)

        # 3. Voice Activity Detection (VAD)
        if self.vad_activado:
            res_vad: VADResult = self.vad.procesar(samples_16k)
            if not res_vad.tiene_voz:
                logger.info(
                    "Audio descartado por VAD (%s): RMS=%.1f dBFS, Duracion=%.2fs",
                    res_vad.motivo, metricas_orig.rms_dbfs, metricas_orig.duracion_segundos
                )
                return AudioProcessingResult(
                    estado="SIN_VOZ",
                    motivo=res_vad.motivo,
                    audio_limpio=None,
                    metricas_originales=metricas_orig,
                    metricas_procesadas=None,
                    duracion_segundos=metricas_orig.duracion_segundos,
                )
            samples_a_procesar = res_vad.muestras_activas
        else:
            samples_a_procesar = samples_16k

        # 4. Normalización dinámica de volumen
        if self.normalizacion_activada:
            samples_limpios = self.normalizer.normalizar(samples_a_procesar)
        else:
            samples_limpios = samples_a_procesar

        metricas_proc = AudioAnalyzer.analizar(samples_limpios)

        return AudioProcessingResult(
            estado="VOZ_DETECTADA",
            motivo="VOZ_DETECTADA",
            audio_limpio=samples_limpios,
            metricas_originales=metricas_orig,
            metricas_procesadas=metricas_proc,
            duracion_segundos=metricas_proc.duracion_segundos,
        )
