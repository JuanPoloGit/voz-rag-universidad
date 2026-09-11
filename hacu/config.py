"""Configuracion central del sistema.

Toda ruta, umbral o limite del proyecto vive aqui. Ningun modulo construye rutas
por su cuenta: se inyecta la configuracion. Las rutas son absolutas y derivadas de
la raiz del repositorio, de modo que HACU arranca igual desde cualquier directorio
de trabajo (venv local, Docker o tarea programada).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent

# Tamano aproximado del system prompt mas la directriz de audiencia, en caracteres.
_CARACTERES_SISTEMA = 3400


def _bandera(nombre: str) -> bool:
    return os.getenv(nombre, "").strip().lower() in {"1", "true", "yes", "si"}


def _entero(nombre: str) -> int | None:
    bruto = os.getenv(nombre, "").strip()
    if not bruto:
        return None
    try:
        return int(bruto)
    except ValueError:
        return None


@dataclass(frozen=True)
class ModelConfig:
    """Parametros de carga e inferencia de llama.cpp."""

    model_path: Path = PROJECT_ROOT / "models" / "Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf"
    n_ctx: int = 16384
    n_gpu_layers: int = -1
    n_batch: int = 512
    n_threads: int = 8
    verbose: bool = False

    # Generacion conversacional (escena)
    chat_max_tokens: int = 1024
    chat_temperature: float = 0.3

    # Generacion de utilidad (extraccion / consolidacion de memoria)
    utility_max_tokens: int = 96
    consolidation_max_tokens: int = 320
    utility_temperature: float = 0.0


@dataclass(frozen=True)
class RagConfig:
    """Parametros de indexacion y recuperacion semantica."""

    doc_folder: Path = PROJECT_ROOT / "documents"
    db_path: Path = PROJECT_ROOT / "chroma_db"
    chunk_size: int = 800
    chunk_overlap: int = 150

    # Fragmentos recuperados segun la amplitud detectada en la pregunta.
    # Calibrado con `python -m pruebas.recuperacion` sobre el corpus real:
    # n=2 recuperaba 15/20 y n=4 llega a 18/20. Las consultas de catalogo
    # ("todos los proyectos") necesitan 10 para cubrir los seis proyectos.
    default_results: int = 4
    broad_results_audacia: int = 10
    broad_results_universidad: int = 8

    # Embeddings multilingues. El modelo por defecto de Chroma (all-MiniLM-L6-v2)
    # esta entrenado en ingles y sobre este corpus en espanol recuperaba 9/20
    # consultas con verdad documentada, frente a 18/20 del multilingue.
    # Requiere `pip install sentence-transformers`. Usa colecciones propias, asi
    # que al activarlo el corpus se reindexa una sola vez.
    multilingual_embeddings: bool = True
    multilingual_model: str = "paraphrase-multilingual-MiniLM-L12-v2"

    # Si el modelo multilingue no se puede cargar (falta la libreria, o la descarga
    # de HuggingFace falla), el motor cae al embedding en ingles y la recuperacion
    # se desploma a la mitad. Para una exhibicion eso es peor que no arrancar: HACU
    # afirmaria con total aplomo no conocer proyectos que si estan documentados.
    # Con esto en True el arranque se aborta; HACU_MULTILINGUE=0 acepta el modo
    # degradado de forma consciente.
    exigir_multilingue: bool = True

    # Rescate de preguntas de seguimiento: cuando el router no reconoce dominio
    # (GENERAL) se consulta igualmente el corpus y se usa el contexto solo si la
    # distancia es menor que este umbral. Medido: las preguntas de seguimiento con
    # respuesta documentada quedan por debajo de 0.77 y la conversacion pura por
    # encima de 0.80.
    umbral_rescate_general: float = 0.78


@dataclass(frozen=True)
class MemoryConfig:
    """Parametros de la memoria efimera y episodica."""

    db_path: Path = PROJECT_ROOT / "hacu_memory.db"
    history_messages: int = 6
    max_facts_per_profile: int = 12
    # Baja de 5 a 3: con el umbral anterior un perfil podia arrastrar hechos
    # contradictorios ('no es ingeniero, es medico' junto a 'es ingeniero de
    # sistemas') sin que nadie los resolviera.
    consolidation_threshold: int = 3
    # Depura al arrancar los perfiles invalidos y el meta-texto heredado.
    sanitize_on_startup: bool = True

    # Retencion del historial conversacional, en horas. El sistema guarda la
    # transcripcion literal de lo que dice cada visitante junto a su nombre; sin
    # limite, una jornada de exhibicion deja ese registro en disco para siempre.
    # 0 desactiva la poda. Los hechos episodicos no se ven afectados.
    retencion_horas: int = 24


@dataclass(frozen=True)
class AppConfig:
    """Configuracion raiz inyectada en todo el arbol de dependencias."""

    model: ModelConfig = field(default_factory=ModelConfig)
    rag: RagConfig = field(default_factory=RagConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)

    default_user: str = "visitante"
    log_file: Path = PROJECT_ROOT / "logs" / "hacu.log"
    debug_console: bool = False

    @classmethod
    def from_env(cls) -> "AppConfig":
        """Configuracion de operacion sin tocar codigo.

        HACU_DEBUG=1          diagnostico en consola
        HACU_RETENCION=8      horas de historial que se conservan (0 = sin poda)
        HACU_FRAGMENTOS=4     fragmentos recuperados por consulta normal
        HACU_MULTILINGUE=0    fuerza el embedding por defecto de Chroma
        """
        base = cls(debug_console=_bandera("HACU_DEBUG"))
        memoria = base.memory
        rag = base.rag

        retencion = _entero("HACU_RETENCION")
        if retencion is not None:
            memoria = replace(memoria, retencion_horas=retencion)
        fragmentos = _entero("HACU_FRAGMENTOS")
        if fragmentos is not None:
            rag = replace(rag, default_results=fragmentos)
        if os.getenv("HACU_MULTILINGUE", "").strip() == "0":
            rag = replace(rag, multilingual_embeddings=False, exigir_multilingue=False)

        return replace(base, memory=memoria, rag=rag)

    def presupuesto_contexto(self) -> tuple[int, int]:
        """(tokens estimados del prompt en el peor caso, tokens disponibles).

        Estimacion conservadora a 3.5 caracteres por token en espanol. Sirve para
        fallar ruidosamente al arrancar si alguien sube n_results o el historial
        sin recalcular: llama.cpp truncaria por la izquierda, comiendose el system
        prompt, y el fallo se veria como un cambio de personalidad inexplicable.
        """
        fragmentos = self.rag.broad_results_audacia * (self.rag.chunk_size + self.rag.chunk_overlap)
        hechos = self.memory.max_facts_per_profile * 80
        historial = self.memory.history_messages * self.model.chat_max_tokens * 4 // 2
        caracteres = _CARACTERES_SISTEMA + fragmentos + hechos + historial + 400
        prompt = int(caracteres / 3.5)
        return prompt, self.model.n_ctx - self.model.chat_max_tokens
