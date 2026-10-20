"""Análisis de métricas acústicas de señal de audio."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass(frozen=True)
class SignalMetrics:
    """Métricas cuantitativas de una señal de audio PCM float32."""
    duracion_segundos: float
    sample_rate: int
    num_muestras: int
    canales: int
    rms_dbfs: float
    pico_dbfs: float
    piso_ruido_dbfs: float
    snr_estimado_db: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "duracion_seg": round(self.duracion_segundos, 3),
            "sample_rate": self.sample_rate,
            "canales": self.canales,
            "rms_dbfs": round(self.rms_dbfs, 2),
            "pico_dbfs": round(self.pico_dbfs, 2),
            "piso_ruido_dbfs": round(self.piso_ruido_dbfs, 2),
            "snr_db": round(self.snr_estimado_db, 2),
        }


class AudioAnalyzer:
    """Calculador de características acústicas sobre arrays float32."""

    @staticmethod
    def analizar(samples: np.ndarray, sample_rate: int = 16000) -> SignalMetrics:
        """Analiza un array numpy float32 unidimensional en rango [-1.0, 1.0]."""
        if samples.ndim > 1:
            samples = samples.mean(axis=1)

        total_samples = len(samples)
        if total_samples == 0:
            return SignalMetrics(
                duracion_segundos=0.0,
                sample_rate=sample_rate,
                num_muestras=0,
                canales=1,
                rms_dbfs=-100.0,
                pico_dbfs=-100.0,
                piso_ruido_dbfs=-100.0,
                snr_estimado_db=0.0,
            )

        duracion = total_samples / sample_rate
        pico = float(np.max(np.abs(samples)))
        pico_dbfs = 20.0 * math.log10(max(pico, 1e-6))

        # RMS global
        rms = float(np.sqrt(np.mean(samples ** 2)))
        rms_dbfs = 20.0 * math.log10(max(rms, 1e-6))

        # Estimación de piso de ruido (percentil 10 de la energía por tramas de 30ms)
        frame_len = max(1, int(0.03 * sample_rate))
        num_frames = total_samples // frame_len

        if num_frames > 2:
            energies = []
            for i in range(num_frames):
                frame = samples[i * frame_len : (i + 1) * frame_len]
                f_rms = np.sqrt(np.mean(frame ** 2))
                energies.append(f_rms)
            piso_rms = float(np.percentile(energies, 10))
        else:
            piso_rms = rms * 0.2

        piso_ruido_dbfs = 20.0 * math.log10(max(piso_rms, 1e-6))
        snr = max(0.0, rms_dbfs - piso_ruido_dbfs)

        return SignalMetrics(
            duracion_segundos=duracion,
            sample_rate=sample_rate,
            num_muestras=total_samples,
            canales=1,
            rms_dbfs=rms_dbfs,
            pico_dbfs=pico_dbfs,
            piso_ruido_dbfs=piso_ruido_dbfs,
            snr_estimado_db=snr,
        )
