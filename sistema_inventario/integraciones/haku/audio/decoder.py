"""Decodificación de audio en memoria sin crear archivos temporales en disco.

Soporta:
- WAV PCM (16-bit, 24-bit, 32-bit float, mono/stereo a cualquier sample rate).
- WebM / OGG / FLAC mediante soundfile en memoria.
- Resampleo controlado a 16.000 Hz Mono Float32.
"""

from __future__ import annotations

import io
import logging
import wave
from typing import Tuple

import numpy as np

logger = logging.getLogger("integraciones.haku.audio")


class AudioDecodeError(Exception):
    """Lanzada cuando el buffer de audio recibido está corrupto o es incompatible."""
    pass


class AudioDecoder:
    """Decodificador de bytes de audio a arrays numpy float32 calibrados a 16kHz mono."""

    TARGET_SAMPLE_RATE = 16000

    @classmethod
    def decodificar_a_16k_mono(cls, audio_bytes: bytes) -> Tuple[np.ndarray, float]:
        """Decodifica un buffer de bytes y retorna (samples_float32, duracion_segundos).

        Garantiza:
        - 1 canal (mono)
        - 16.000 Hz
        - Dtype np.float32 en rango normalizado [-1.0, 1.0]
        """
        if not audio_bytes:
            raise AudioDecodeError("Buffer de audio vacío.")

        err_sf_msg = None
        err_wave_msg = None
        err_ffmpeg_msg = None
        err_pcm_msg = None

        # 1. Intentar con soundfile sobre io.BytesIO (soporta WAV, WebM, OGG, FLAC)
        try:
            import soundfile as sf
            with io.BytesIO(audio_bytes) as bio:
                data, sr = sf.read(bio, dtype="float32")
                return cls._normalizar_formato(data, sr)
        except Exception as exc:
            err_sf_msg = str(exc)
            logger.debug("soundfile en memoria no pudo decodificar (%s); intentando parser nativo WAV", exc)

        # 2. Fallback nativo: intentar con módulo wave estándar de Python
        try:
            with io.BytesIO(audio_bytes) as bio:
                with wave.open(bio, "rb") as wf:
                    sr = wf.getframerate()
                    canales = wf.getnchannels()
                    sampwidth = wf.getsampwidth()
                    num_frames = wf.getnframes()
                    raw_data = wf.readframes(num_frames)

                    if sampwidth == 2:  # 16-bit PCM
                        data = np.frombuffer(raw_data, dtype=np.int16).astype(np.float32) / 32768.0
                    elif sampwidth == 1:  # 8-bit unsigned
                        data = (np.frombuffer(raw_data, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
                    elif sampwidth == 4:  # 32-bit int o float
                        data = np.frombuffer(raw_data, dtype=np.int32).astype(np.float32) / 2147483648.0
                    else:
                        raise AudioDecodeError(f"Ancho de muestra no soportado: {sampwidth} bytes")

                    if canales > 1:
                        data = data.reshape(-1, canales)

                    return cls._normalizar_formato(data, sr)
        except Exception as exc:
            err_wave_msg = str(exc)
            logger.debug("Parser nativo wave falló: %s", exc)

        # 3. Fallback: decodificación en memoria mediante pipe de ffmpeg (soporte WebM/Opus, OGG, etc.)
        try:
            import subprocess
            cmd = ["ffmpeg", "-loglevel", "error", "-nostdin", "-threads", "1", "-i", "pipe:0", "-f", "wav", "-ar", "16000", "-ac", "1", "pipe:1"]
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            stdout_data, stderr_data = proc.communicate(input=audio_bytes, timeout=5)
            if proc.returncode == 0 and stdout_data:
                import soundfile as sf
                with io.BytesIO(stdout_data) as bio:
                    data, sr = sf.read(bio, dtype="float32")
                    return cls._normalizar_formato(data, sr)
            else:
                err_ffmpeg_msg = stderr_data.decode("utf-8", errors="replace") if stderr_data else f"code {proc.returncode}"
        except Exception as exc:
            err_ffmpeg_msg = str(exc)
            logger.debug("Decodificador ffmpeg pipe falló o no disponible: %s", exc)

        # 4. Fallback: buffer PCM 16-bit crudo a 16kHz
        try:
            data = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
            return cls._normalizar_formato(data, cls.TARGET_SAMPLE_RATE)
        except Exception as exc:
            err_pcm_msg = str(exc)
            raise AudioDecodeError(
                f"No se pudo decodificar el formato de audio: sf={err_sf_msg}, wave={err_wave_msg}, ffmpeg={err_ffmpeg_msg}, pcm={err_pcm_msg}"
            ) from exc

    @classmethod
    def _normalizar_formato(cls, data: np.ndarray, sample_rate: int) -> Tuple[np.ndarray, float]:
        """Convierte canales a mono y resamplea a 16.000 Hz si difiere."""
        # A mono si es estéreo o multicanal
        if data.ndim > 1:
            data = data.mean(axis=1)

        data = data.astype(np.float32)

        # Resampleo controlado si no está a 16 kHz
        if sample_rate != cls.TARGET_SAMPLE_RATE and len(data) > 0:
            data = cls._resamplear_lineal(data, sample_rate, cls.TARGET_SAMPLE_RATE)
            sample_rate = cls.TARGET_SAMPLE_RATE

        duracion = len(data) / sample_rate if sample_rate > 0 else 0.0
        return data, duracion

    @staticmethod
    def _resamplear_lineal(data: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
        """Resampleador lineal vectorial de alto rendimiento para arrays float32."""
        if src_rate == dst_rate:
            return data
        factor = dst_rate / src_rate
        num_nuevas_muestras = int(round(len(data) * factor))
        indices_orig = np.linspace(0, len(data) - 1, num_nuevas_muestras)
        resampled = np.interp(indices_orig, np.arange(len(data)), data)
        return resampled.astype(np.float32)
