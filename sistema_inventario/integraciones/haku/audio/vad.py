"""Voice Activity Detection (VAD) para detección de habla y descarte de silencios/ruido."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np


@dataclass(frozen=True)
class VADResult:
    """Resultado del análisis de actividad vocal."""
    tiene_voz: bool
    motivo: str  # "VOZ_DETECTADA" | "AUDIO_SILENCIOSO" | "SOLO_RUIDO" | "AUDIO_VACIO"
    muestras_activas: np.ndarray
    duracion_original_seg: float
    duracion_voz_seg: float
    ratio_voz: float


class VoiceActivityDetector:
    """Detector de actividad de voz por energía adaptativa y tasa de cruce por cero (ZCR).

    Diseñado para operar en tiempo real (<2ms en CPU) sin modelos pesados ni GPU.
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        frame_duration_ms: int = 30,
        umbral_silencio_dbfs: float = -46.0,
        min_segundos_voz: float = 0.25,
        margen_silencio_ms: int = 150,
    ) -> None:
        self.sample_rate = sample_rate
        self.frame_len = int((frame_duration_ms / 1000.0) * sample_rate)
        self.umbral_silencio_dbfs = umbral_silencio_dbfs
        self.min_segundos_voz = min_segundos_voz
        self.margen_frames = int((margen_silencio_ms / 1000.0) * (sample_rate / self.frame_len))

    def procesar(self, samples: np.ndarray) -> VADResult:
        """Determina si la señal contiene habla humana y aísla las muestras activas."""
        total_len = len(samples)
        if total_len == 0:
            return VADResult(
                tiene_voz=False,
                motivo="AUDIO_VACIO",
                muestras_activas=np.zeros(0, dtype=np.float32),
                duracion_original_seg=0.0,
                duracion_voz_seg=0.0,
                ratio_voz=0.0,
            )

        duracion_original = total_len / self.sample_rate

        # 1. Chequeo rápido de energía global
        global_rms = float(np.sqrt(np.mean(samples ** 2)))
        global_rms_dbfs = 20.0 * math.log10(max(global_rms, 1e-6))

        if global_rms_dbfs < self.umbral_silencio_dbfs:
            return VADResult(
                tiene_voz=False,
                motivo="AUDIO_SILENCIOSO",
                muestras_activas=np.zeros(0, dtype=np.float32),
                duracion_original_seg=duracion_original,
                duracion_voz_seg=0.0,
                ratio_voz=0.0,
            )

        # 2. Análisis trama a trama (30ms por frame)
        num_frames = total_len // self.frame_len
        if num_frames == 0:
            return VADResult(
                tiene_voz=False,
                motivo="SOLO_RUIDO",
                muestras_activas=np.zeros(0, dtype=np.float32),
                duracion_original_seg=duracion_original,
                duracion_voz_seg=0.0,
                ratio_voz=0.0,
            )

        frames_voz = np.zeros(num_frames, dtype=bool)

        for i in range(num_frames):
            frame = samples[i * self.frame_len : (i + 1) * self.frame_len]
            f_rms = np.sqrt(np.mean(frame ** 2))
            f_rms_dbfs = 20.0 * math.log10(max(f_rms, 1e-6))

            # Cruce por cero para diferenciar sibilantes de zumbido de baja frecuencia
            zero_crossings = np.sum(np.abs(np.diff(np.sign(frame)))) / 2.0
            zcr = zero_crossings / len(frame)

            # Criterio de voz: energía sobre el umbral y ZCR dentro del rango del habla humana (0.02 - 0.55)
            if f_rms_dbfs > self.umbral_silencio_dbfs and 0.015 < zcr < 0.60:
                frames_voz[i] = True

        total_frames_voz = int(np.sum(frames_voz))
        ratio_voz = total_frames_voz / num_frames
        duracion_voz = total_frames_voz * (self.frame_len / self.sample_rate)

        # Si no supera el umbral mínimo de habla (ej. 250 ms continuos/acumulados)
        if duracion_voz < self.min_segundos_voz:
            return VADResult(
                tiene_voz=False,
                motivo="SOLO_RUIDO",
                muestras_activas=np.zeros(0, dtype=np.float32),
                duracion_original_seg=duracion_original,
                duracion_voz_seg=duracion_voz,
                ratio_voz=ratio_voz,
            )

        # 3. Recorte de silencios en los extremos (con margen de seguridad para no cortar sílabas)
        indices_voz = np.where(frames_voz)[0]
        start_frame = max(0, indices_voz[0] - self.margen_frames)
        end_frame = min(num_frames, indices_voz[-1] + self.margen_frames + 1)

        start_idx = start_frame * self.frame_len
        end_idx = min(total_len, end_frame * self.frame_len)

        muestras_recortadas = samples[start_idx:end_idx]

        return VADResult(
            tiene_voz=True,
            motivo="VOZ_DETECTADA",
            muestras_activas=muestras_recortadas,
            duracion_original_seg=duracion_original,
            duracion_voz_seg=len(muestras_recortadas) / self.sample_rate,
            ratio_voz=ratio_voz,
        )
