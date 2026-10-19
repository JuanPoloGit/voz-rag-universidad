"""Normalización dinámica de volumen con limitador suave para Whisper."""

from __future__ import annotations

import math
import numpy as np


class AudioNormalizer:
    """Normaliza el nivel RMS a un umbral acústico óptimo (-18 dBFS) sin distorsión."""

    def __init__(
        self,
        target_rms_dbfs: float = -18.0,
        max_boost_db: float = 24.0,
        max_attenuation_db: float = 30.0,
        peak_limit: float = 0.92,
    ) -> None:
        self.target_rms_dbfs = target_rms_dbfs
        self.max_boost_factor = 10.0 ** (max_boost_db / 20.0)
        self.min_attenuation_factor = 10.0 ** (-max_attenuation_db / 20.0)
        self.peak_limit = peak_limit

    def normalizar(self, samples: np.ndarray) -> np.ndarray:
        """Ajusta el volumen RMS y limita picos para evitar recorte digital."""
        if len(samples) == 0:
            return samples

        current_rms = float(np.sqrt(np.mean(samples ** 2)))
        if current_rms < 1e-6:
            return samples

        current_rms_dbfs = 20.0 * math.log10(current_rms)

        # Ganancia requerida
        delta_db = self.target_rms_dbfs - current_rms_dbfs
        factor = 10.0 ** (delta_db / 20.0)

        # Limitar factor de amplificación para no inflar el piso de ruido
        factor = max(self.min_attenuation_factor, min(self.max_boost_factor, factor))

        scaled = samples * factor

        # Limitador suave (Soft Knee Limiter / tanh) si los picos superan el umbral
        max_peak = float(np.max(np.abs(scaled)))
        if max_peak > self.peak_limit:
            # Saturación armónica suave para evitar el 'hard clipping'
            scaled = np.tanh(scaled / self.peak_limit) * self.peak_limit

        return scaled.astype(np.float32)
