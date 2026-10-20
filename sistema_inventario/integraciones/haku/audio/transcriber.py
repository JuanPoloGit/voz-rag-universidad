"""Motor de transcripción Speech-to-Text (STT) para Haku Inventario.

Usa faster-whisper (CTranslate2) de forma 100% autónoma y local con aceleración GPU (CUDA).
REGLAS ARQUITECTÓNICAS:
- Módulo confinado al dominio Haku Inventario (Integrante 4).
- NO importa ni depende de 'hacu' (Haku Visitante).
- No ejecuta inferencia de LLM ni genera diálogo conversacional general.
- Ejecuta en memoria sobre arrays numpy float32 calibrados a 16kHz mono.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from typing import Optional

import numpy as np
from django.conf import settings

logger = logging.getLogger("integraciones.haku.audio")


def configurar_rutas_cuda_windows() -> None:
    """Configura las rutas de bibliotecas dinámicas (DLL) para NVIDIA CUDA en Windows.

    Python 3.8+ en Windows no busca DLLs en las carpetas de site-packages de forma predeterminada.
    CTranslate2 requiere 'cublas64_12.dll' y bibliotecas afines provistas por los paquetes
    oficiales nvidia-cublas-cu12 y nvidia-cuda-nvrtc-cu12, o por CUDA_PATH del sistema.
    """
    if sys.platform != "win32":
        return

    rutas_candidatas = [
        # Entorno virtual activo (site-packages nvidia)
        os.path.join(sys.prefix, "Lib", "site-packages", "nvidia", "cublas", "bin"),
        os.path.join(sys.prefix, "Lib", "site-packages", "nvidia", "cuda_nvrtc", "bin"),
        os.path.join(sys.prefix, "Lib", "site-packages", "nvidia", "cudnn", "bin"),
    ]

    cuda_path = os.environ.get("CUDA_PATH")
    if cuda_path:
        rutas_candidatas.append(os.path.join(cuda_path, "bin"))

    for ruta in rutas_candidatas:
        if os.path.isdir(ruta):
            # Agregar a PATH del proceso para llamadas nativas LoadLibrary de CTranslate2
            path_actual = os.environ.get("PATH", "")
            if ruta not in path_actual:
                os.environ["PATH"] = ruta + os.pathsep + path_actual
            # Registrar directorio en el cargador de DLLs de Python 3.8+
            if hasattr(os, "add_dll_directory"):
                try:
                    os.add_dll_directory(ruta)
                except Exception as exc:
                    logger.debug("No se pudo agregar directorio DLL '%s': %s", ruta, exc)


class TranscripcionError(Exception):
    """Error originado en la carga o ejecución del motor STT."""
    pass


class HakuTranscriber:
    """Transcriptor Speech-to-Text local basado en faster-whisper con soporte CUDA y fallback CPU."""

    _instancia: Optional[HakuTranscriber] = None
    _lock = threading.Lock()

    def __init__(
        self,
        modelo_nombre: Optional[str] = None,
        dispositivo: Optional[str] = None,
        computo: Optional[str] = None,
    ) -> None:
        self.modelo_nombre = modelo_nombre or getattr(settings, "HAKU_STT_MODEL", "base")
        self.dispositivo_solicitado = dispositivo or getattr(settings, "HAKU_STT_DEVICE", "cuda")
        self.computo_solicitado = computo or getattr(settings, "HAKU_STT_COMPUTE", "float16")
        self.dispositivo_efectivo: Optional[str] = None
        self.computo_efectivo: Optional[str] = None
        self.motivo_fallback: Optional[str] = None
        self._model = None
        self._init_lock = threading.Lock()

    @classmethod
    def obtener_instancia(cls) -> HakuTranscriber:
        """Obtiene o crea la instancia singleton del transcriptor."""
        if cls._instancia is None:
            with cls._lock:
                if cls._instancia is None:
                    cls._instancia = cls()
        return cls._instancia

    @classmethod
    def reset_instancia(cls) -> None:
        """Reinicia la instancia (útil para pruebas unitarias)."""
        with cls._lock:
            cls._instancia = None

    def _asegurar_modelo(self):
        """Carga perezosa del modelo faster-whisper con fallback seguro CPU int8."""
        if self._model is not None:
            return self._model

        with self._init_lock:
            if self._model is not None:
                return self._model

            # Configurar rutas DLL si estamos en Windows
            configurar_rutas_cuda_windows()

            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise TranscripcionError(
                    "Falta la biblioteca faster-whisper. Ejecuta 'pip install faster-whisper'."
                ) from exc

            import ctranslate2

            # Intentar CUDA si está disponible y solicitado
            num_cuda = 0
            try:
                num_cuda = ctranslate2.get_cuda_device_count()
            except Exception as exc_count:
                logger.debug("Error consultando dispositivos CUDA: %s", exc_count)

            if num_cuda > 0 and self.dispositivo_solicitado in ("auto", "cuda"):
                computo = "float16" if self.computo_solicitado in ("auto", "float16") else self.computo_solicitado
                try:
                    logger.info(
                        "Iniciando carga de faster-whisper '%s' en GPU CUDA (dispositivos=%d, compute_type=%s)...",
                        self.modelo_nombre,
                        num_cuda,
                        computo,
                    )
                    modelo_cuda = WhisperModel(self.modelo_nombre, device="cuda", compute_type=computo)

                    # Verificación real obligatoria mediante inferencia con array de 1 segundo
                    dummy = np.zeros(16000, dtype=np.float32)
                    segs, _ = modelo_cuda.transcribe(dummy, language="es")
                    _ = list(segs)

                    self.dispositivo_efectivo = "cuda"
                    self.computo_efectivo = computo
                    self.motivo_fallback = None
                    self._model = modelo_cuda
                    logger.info(
                        "Modelo faster-whisper verificado y activo en GPU CUDA (device=cuda, compute_type=%s).",
                        computo,
                    )
                    return self._model
                except Exception as exc_cuda:
                    self.motivo_fallback = str(exc_cuda)
                    logger.warning(
                        "Aceleración CUDA solicitada pero no operativa (%s). Activando fallback controlado a CPU int8.",
                        exc_cuda,
                    )

            # Fallback a CPU int8
            try:
                logger.info(
                    "Cargando faster-whisper '%s' en CPU (int8)%s...",
                    self.modelo_nombre,
                    f" [Motivo fallback: {self.motivo_fallback}]" if self.motivo_fallback else "",
                )
                self._model = WhisperModel(self.modelo_nombre, device="cpu", compute_type="int8")
                self.dispositivo_efectivo = "cpu"
                self.computo_efectivo = "int8"
                logger.info("Modelo faster-whisper cargado con éxito en CPU (device=cpu, compute_type=int8).")
                return self._model
            except Exception as exc_cpu:
                raise TranscripcionError(
                    f"No se pudo cargar el modelo faster-whisper '{self.modelo_nombre}': {exc_cpu}"
                ) from exc_cpu

    def es_cuda(self) -> bool:
        """Indica si el transcriptor activo está corriendo sobre GPU CUDA."""
        self._asegurar_modelo()
        return self.dispositivo_efectivo == "cuda"

    def transcribir(self, samples_16k: np.ndarray, idioma: str = "es", beam_size: int = 5) -> str:
        """Transcribe un array de audio float32 a 16.000 Hz mono.

        Retorna texto limpio (sin espacios residuales ni alucinaciones de audio corto).
        """
        if samples_16k is None or len(samples_16k) == 0:
            return ""

        # Descarta audio inferior a 250 ms (clic o ruido ultracorto)
        if len(samples_16k) < 16000 * 0.25:
            return ""

        modelo = self._asegurar_modelo()

        # Semilla contextual de dominio Haku Inventario para sesgar a Whisper y evitar alucinaciones
        prompt_haku = (
            "Haku Inventario AudacIA: ¿cuántas entradas hubo hoy?, ¿cuántas salidas se hicieron hoy?, "
            "¿qué movimientos hubo hoy?, consulta el inventario, ¿cómo va el almacén?, stock disponible, "
            "ponchadora RJ45, catálogo de productos, préstamos activos, Almacén Central."
        )

        try:
            segmentos, _info = modelo.transcribe(
                samples_16k.astype(np.float32),
                language=idioma,
                task="transcribe",
                beam_size=beam_size,
                initial_prompt=prompt_haku,
                vad_filter=True,
                condition_on_previous_text=False,
            )
            texto = " ".join(s.text.strip() for s in segmentos).strip()
            return texto
        except RuntimeError as exc:
            if "cublas" in str(exc).lower() or "cuda" in str(exc).lower():
                logger.error(
                    "Fallo de GPU/CUDA en inferencia runtime (%s). Recayendo en CPU int8.",
                    exc,
                )
                self.dispositivo_efectivo = "cpu"
                self.computo_efectivo = "int8"
                self.motivo_fallback = str(exc)
                from faster_whisper import WhisperModel
                self._model = WhisperModel(self.modelo_nombre, device="cpu", compute_type="int8")
                segs, _ = self._model.transcribe(
                    samples_16k.astype(np.float32),
                    language=idioma,
                    task="transcribe",
                    beam_size=beam_size,
                    initial_prompt=prompt_haku,
                    vad_filter=True,
                    condition_on_previous_text=False,
                )
                return " ".join(s.text.strip() for s in segs).strip()
            raise TranscripcionError(f"Error en inferencia Whisper: {exc}") from exc
        except Exception as exc:
            logger.exception("Fallo durante la transcripción en faster-whisper: %s", exc)
            raise TranscripcionError(f"Error en inferencia Whisper: {exc}") from exc
