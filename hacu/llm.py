"""Servicio de inferencia local.

Unico punto de acceso al modelo. Serializa las llamadas con un lock porque
`llama_cpp.Llama` no es thread-safe: el hilo de memoria en segundo plano y el hilo
interactivo comparten el mismo contexto de KV-cache, y solaparlos corrompe la
generacion o revienta el proceso.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from enum import Enum
from typing import Any

from .config import PROJECT_ROOT, ModelConfig

Mensaje = dict[str, str]
_BLOQUE_JSON = re.compile(r"\{.*\}", re.DOTALL)


def detectar_gpu() -> str | None:
    """Nombre de la GPU segun nvidia-smi, o None. Evita depender de torch (~2.5 GB)."""
    try:
        salida = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    primera = salida.stdout.strip().splitlines()
    return primera[0].strip() if primera else None


class Aliento(str, Enum):
    """Cuanto aire tiene el turno. Lo decide lo que PIDEN, no lo que escriben.

    Una pregunta de tres palabras puede necesitar seis frases ("¿y Orion?") y un
    parrafo de agradecimiento se contesta con una. Atar el techo a la longitud de
    la entrada es justo el error que hacia que un "gracias, ha sido muy
    interesante" recibiera cuatro parrafos.
    """

    BREVE = "BREVE"      # turno social: cierre, elogio, acuse de recibo
    NORMAL = "NORMAL"    # el caso corriente de tarima
    EXTENSO = "EXTENSO"  # piden desarrollo o catalogo entero


class LlmService:
    """Fachada sobre llama.cpp con acceso serializado y salida JSON restringida."""

    def __init__(self, config: ModelConfig, logger: logging.Logger) -> None:
        self._cfg = config
        self._log = logger.getChild("llm")
        self._lock = threading.RLock()
        self._llm: Any | None = None

    # ------------------------------------------------------------------ carga

    def cargar(self) -> None:
        """Carga el modelo en VRAM. Idempotente."""
        if self._llm is not None:
            return
        if not self._cfg.model_path.exists():
            raise FileNotFoundError(f"No se encontro el modelo GGUF en {self._cfg.model_path}")

        self._preparar_entorno_windows()
        from llama_cpp import Llama  # import diferido: acelera el arranque en fallos tempranos

        self._llm = Llama(
            model_path=str(self._cfg.model_path),
            n_ctx=self._cfg.n_ctx,
            n_gpu_layers=self._cfg.n_gpu_layers,
            n_batch=self._cfg.n_batch,
            n_threads=self._cfg.n_threads,
            verbose=self._cfg.verbose,
            **self._opciones_de_cache(Llama),
        )
        self._log.info("Modelo cargado: %s", self._cfg.model_path.name)

    def _opciones_de_cache(self, Llama: type) -> dict[str, object]:  # noqa: N803
        """`type_k`/`type_v` y `flash_attn`, si esta version de llama.cpp los trae.

        Se consultan en vez de darlos por hechos: el proyecto tiene que arrancar
        con la rueda que haya instalada, y una version antigua que no conozca
        estos argumentos reventaria en el constructor. Sin ellos HACU funciona
        igual, solo que la cache KV ocupa el doble.
        """
        import inspect  # noqa: PLC0415

        admitidos = inspect.signature(Llama.__init__).parameters
        opciones: dict[str, object] = {}
        if self._cfg.flash_attn and "flash_attn" in admitidos:
            opciones["flash_attn"] = True
        if not self._cfg.kv_8bits:
            return opciones
        if "type_k" not in admitidos or "type_v" not in admitidos:
            self._log.warning(
                "Esta version de llama-cpp-python no admite cache KV cuantizada; "
                "se usa fp16 y el contexto ocupa el doble."
            )
            return opciones
        try:
            from llama_cpp import GGML_TYPE_Q8_0  # noqa: PLC0415
        except ImportError:
            import llama_cpp  # noqa: PLC0415

            GGML_TYPE_Q8_0 = getattr(llama_cpp, "GGML_TYPE_Q8_0", 8)  # noqa: N806
        opciones["type_k"] = GGML_TYPE_Q8_0
        opciones["type_v"] = GGML_TYPE_Q8_0
        self._log.info("Cache KV a 8 bits: la mitad de VRAM por token de contexto")
        return opciones

    @staticmethod
    def _preparar_entorno_windows() -> None:
        """Registra las DLL de CUDA del venv en Windows antes de importar llama_cpp."""
        os.environ.setdefault("GGML_CUDA_FORCE_CUBLAS", "1")
        if os.name != "nt":
            return
        dll_path = PROJECT_ROOT / "venv" / "Lib" / "site-packages" / "llama_cpp" / "lib"
        if dll_path.exists():
            os.add_dll_directory(str(dll_path))

    def precalentar(self) -> float:
        """Genera un token de descarte para pagar el primer prefill antes de la exhibicion.

        Sin esto, el primer visitante absorbe la inicializacion del contexto y ve
        una latencia varias veces mayor que la del resto de la jornada.
        """
        inicio = time.perf_counter()
        with self._lock:
            try:
                self._modelo.create_chat_completion(
                    messages=[{"role": "user", "content": "hola"}], max_tokens=1, temperature=0.0
                )
            except Exception:
                self._log.warning("Fallo el precalentamiento del modelo", exc_info=True)
                return 0.0
        transcurrido = time.perf_counter() - inicio
        self._log.info("Modelo precalentado en %.2fs", transcurrido)
        return transcurrido

    def cerrar(self) -> None:
        """Libera la VRAM de forma ordenada."""
        with self._lock:
            self._llm = None

    # ------------------------------------------------------------- inferencia

    def _tope(self, aliento: "Aliento | None", extenso: bool) -> int:
        """Techo de tokens del turno. `extenso` es el atajo heredado de EXTENSO."""
        if aliento is None:
            aliento = Aliento.EXTENSO if extenso else Aliento.NORMAL
        return {
            Aliento.BREVE: self._cfg.chat_max_tokens_breve,
            Aliento.NORMAL: self._cfg.chat_max_tokens,
            Aliento.EXTENSO: self._cfg.chat_max_tokens_extenso,
        }[aliento]

    def stream_chat(
        self,
        mensajes: Sequence[Mensaje],
        extenso: bool = False,
        al_terminar: Callable[[str | None], None] | None = None,
        aliento: "Aliento | None" = None,
    ) -> Iterator[str]:
        """Genera la respuesta de escena token a token manteniendo el lock durante todo el stream.

        `aliento` decide el techo de tokens en tres niveles; `extenso` se
        conserva como atajo del nivel EXTENSO. El techo se decide por lo que el
        turno PIDE, no por lo largo que venga: hay preguntas de tres palabras
        que necesitan seis frases y parrafos enteros de agradecimiento que se
        contestan con una.

        `extenso` sube el tope solo en las preguntas que piden desarrollo, sin
        relajarlo para todas: con el tope corto, "explicame cada proyecto" se
        cortaba a mitad de la cuarta frase. `al_terminar` recibe el motivo de finalizacion
        que devuelve llama.cpp ("stop" o "length"): es la unica forma fiable de
        saber que la respuesta se corto a mitad de palabra y no por voluntad del
        modelo.
        """
        motivo: str | None = None
        with self._lock:
            stream = self._modelo.create_chat_completion(
                messages=list(mensajes),
                max_tokens=self._tope(aliento, extenso),
                temperature=self._cfg.chat_temperature,
                stream=True,
            )
            for chunk in stream:
                eleccion = chunk["choices"][0]
                motivo = eleccion.get("finish_reason") or motivo
                contenido = eleccion.get("delta", {}).get("content")
                if contenido:
                    yield contenido
        if al_terminar is not None:
            al_terminar(motivo)

    def completar_json(self, prompt: str, max_tokens: int) -> dict[str, Any] | None:
        """Ejecuta una tarea de utilidad exigiendo un objeto JSON como salida.

        La decodificacion restringida es lo que impide que el modelo escriba
        preambulos ("Aqui te presento el perfil final:") que antes se guardaban
        como si fueran hechos del visitante.
        """
        with self._lock:
            try:
                respuesta = self._modelo.create_chat_completion(
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=max_tokens,
                    temperature=self._cfg.utility_temperature,
                    response_format={"type": "json_object"},
                )
            except Exception:  # gramatica JSON no disponible en este build
                self._log.debug("response_format JSON no soportado; usando modo texto", exc_info=True)
                try:
                    respuesta = self._modelo.create_chat_completion(
                        messages=[{"role": "user", "content": prompt}],
                        max_tokens=max_tokens,
                        temperature=self._cfg.utility_temperature,
                    )
                except Exception:
                    self._log.warning("Fallo la tarea de utilidad del LLM", exc_info=True)
                    return None

        texto = respuesta["choices"][0]["message"]["content"].strip()
        return self._parsear_json(texto)

    def _parsear_json(self, texto: str) -> dict[str, Any] | None:
        """Tolera el caso en que el modelo envuelva el JSON en texto o vallas markdown."""
        try:
            datos = json.loads(texto)
        except json.JSONDecodeError:
            bloque = _BLOQUE_JSON.search(texto)
            if not bloque:
                self._log.debug("Salida de utilidad no parseable: %r", texto[:200])
                return None
            try:
                datos = json.loads(bloque.group(0))
            except json.JSONDecodeError:
                self._log.debug("Bloque JSON invalido: %r", bloque.group(0)[:200])
                return None
        return datos if isinstance(datos, dict) else None

    @property
    def _modelo(self) -> Any:
        if self._llm is None:
            raise RuntimeError("El modelo no ha sido cargado. Llama a LlmService.cargar() primero.")
        return self._llm
