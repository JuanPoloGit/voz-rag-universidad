"""Subpaquete de procesamiento y análisis de señal de audio para Haku Inventario.

Responsabilidades:
1. Decodificación en memoria (sin archivos temporales en disco) de WAV PCM y WebM.
2. Voice Activity Detection (VAD) para descartar silencios y ruidos no vocales.
3. Normalización dinámica de volumen (RMS a -18 dBFS) con limitador suave anti-clipping.
4. Extracción de métricas de audio (RMS, pico, SNR, duración).
"""

from .analyzer import AudioAnalyzer, SignalMetrics
from .decoder import AudioDecoder, AudioDecodeError
from .normalizer import AudioNormalizer
from .pipeline import AudioPipeline, AudioProcessingResult
from .synthesizer import HakuSynthesizer, HakuSynthesizerError
from .transcriber import HakuTranscriber, TranscripcionError
from .vad import VoiceActivityDetector, VADResult

__all__ = [
    "AudioAnalyzer",
    "SignalMetrics",
    "AudioDecoder",
    "AudioDecodeError",
    "AudioNormalizer",
    "VoiceActivityDetector",
    "VADResult",
    "AudioPipeline",
    "AudioProcessingResult",
    "HakuTranscriber",
    "TranscripcionError",
    "HakuSynthesizer",
    "HakuSynthesizerError",
]

