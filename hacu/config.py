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


def _texto(nombre: str) -> str | None:
    bruto = os.getenv(nombre, "").strip()
    return bruto or None


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

    # Generacion conversacional (escena). La regla 3 pide 2-4 frases y el modelo
    # la ignoraba soltando monologos de nueve segundos; el tope lo fuerza por
    # hardware. 384 tokens son unas seis frases largas, suficiente para una
    # explicacion tecnica y demasiado poco para un discurso.
    chat_max_tokens: int = 384
    # Tope para las preguntas que piden desarrollo de verdad ("explicame
    # detalladamente cada proyecto"). Con 384 la respuesta se cortaba a mitad de
    # palabra en la cuarta frase; AudacIA tiene seis proyectos y describirlos en
    # una frase cada uno no es una respuesta. El tope sigue siendo un techo, no
    # un objetivo: la regla 3 del prompt manda brevedad por defecto.
    chat_max_tokens_extenso: int = 900
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
    # Mensajes del historial que viajan en cada turno (5 intercambios). Antes eran
    # 6 (3 intercambios): en conversacion real el visitante dice "y eso por que?"
    # o "cuentame mas" y la referencia ya se habia salido de la ventana. Subirlo
    # es barato desde que las respuestas estan topadas a 384 tokens.
    history_messages: int = 10
    max_facts_per_profile: int = 12
    # Umbral de consolidacion POR VOLUMEN. Se dispara solo al rozar el maximo:
    # cada pasada es una oportunidad de que el LLM descarte un hecho valido, y en
    # la bateria con umbral 3 perdimos "Ya no le gusta la robotica" de un perfil
    # que no tenia ninguna contradiccion. Las contradicciones se resuelven en el
    # momento por su propio disparador, que es determinista y no espera al volumen.
    consolidation_threshold: int = 10
    # Depura al arrancar los perfiles invalidos y el meta-texto heredado.
    sanitize_on_startup: bool = True

    # Retencion del historial conversacional, en horas. El sistema guarda la
    # transcripcion literal de lo que dice cada visitante junto a su nombre; sin
    # limite, una jornada de exhibicion deja ese registro en disco para siempre.
    # 0 desactiva la poda. Los hechos episodicos no se ven afectados.
    retencion_horas: int = 24


@dataclass(frozen=True)
class VozConfig:
    """Captura, reconocimiento y sintesis. Toda la capa de audio se apaga con `activa`."""

    activa: bool = False
    # Altavoz si, microfono no. Es el modo util cuando no hay entrada de audio
    # —una sesion remota, un microfono que se rompe a media jornada— y evita
    # cargar el modelo de reconocimiento, que no pinta nada si nadie va a hablar.
    solo_salida: bool = False

    # --- Captura -----------------------------------------------------------
    # 16 kHz mono es lo que espera Whisper. Subir la frecuencia obliga a remuestrear
    # y no aporta nada: el modelo trabaja internamente a 16 kHz.
    frecuencia: int = 16000
    canales: int = 1
    # Bloque de captura. 30 ms es el tamano clasico de trama de VAD: suficiente
    # resolucion para cortar al final de una frase sin recortar la ultima silaba.
    bloque_ms: int = 30
    dispositivo_entrada: int | None = None
    dispositivo_salida: int | None = None

    # --- Modo de escucha ---------------------------------------------------
    # Pulsar-para-hablar por defecto, y no por comodidad: en una tarima el altavoz
    # alimenta al microfono, y con deteccion automatica HACU se escucha a si mismo
    # y se responde solo. La deteccion por energia queda para una sala controlada
    # o para cuando haya auriculares.
    deteccion_automatica: bool = False
    # Umbral relativo al ruido ambiente medido al arrancar. 3.0 significa "tres
    # veces la energia del silencio de la sala".
    umbral_voz: float = 3.0
    calibracion_ms: int = 1200
    silencio_final_ms: int = 700
    minimo_voz_ms: int = 300
    maximo_grabacion_ms: int = 20000

    # --- Reconocimiento ----------------------------------------------------
    # `small` en int8_float16 ocupa ~0.5 GB de VRAM y deja sitio al Llama 8B
    # (~5.5 GB) dentro de los 12 GB de la portatil. `medium` sube a ~1.5 GB y
    # mejora poco en espanol con audio de cerca.
    modelo_stt: str = "small"
    dispositivo_stt: str = "cuda"
    computo_stt: str = "int8_float16"
    idioma: str = "es"
    # Los nombres propios de la exhibicion son justo lo que peor transcribe un
    # modelo generico: "Holosand" sale "olo san", "AudacIA" sale "audacia" en
    # minuscula o "au da sia". Sembrar el vocabulario en el prompt inicial los
    # rescata sin reentrenar nada.
    vocabulario: tuple[str, ...] = (
        "AudacIA", "Hacu", "Universidad Simon Bolivar", "Holosand", "Orion",
        "Proyecto Tanque", "MacondoLab", "Adaptia", "Eureka", "Kinect",
        "Soil Sensor", "Barranquilla", "Jose Consuegra",
    )

    # --- Sintesis ----------------------------------------------------------
    # "piper" es la voz buena y offline; "sistema" usa el sintetizador del sistema
    # operativo (SAPI5 en Windows, espeak-ng en Linux) para que la exhibicion hable
    # aunque falte el modelo de Piper; "mudo" desactiva la sintesis.
    motor_tts: str = "auto"
    # Nombre de la voz de Piper. es_MX-claude-high es espanol latino neutro, que
    # es lo que menos chirria en Barranquilla; es_ES-davefx-medium suena peninsular.
    # Se descarga con `python -m hacu.voz --descargar`.
    piper_voz: str = "es_MX-claude-high"
    carpeta_voces: Path = PROJECT_ROOT / "models" / "voz"
    # Solo para el binario suelto de Piper (las versiones anteriores a piper-tts).
    piper_exe: Path | None = None
    velocidad_tts: float = 1.0
    volumen_tts: float = 0.9
    # Al hablar por frases, el visitante oye la primera mientras el modelo genera
    # la segunda. Por debajo de este minimo la frase se acumula con la siguiente:
    # trocear "Si." de su continuacion suena entrecortado.
    minimo_frase: int = 12
    maximo_frase: int = 240


@dataclass(frozen=True)
class InterfazConfig:
    """Ventana de exhibicion."""

    pantalla_completa: bool = False
    ancho: int = 1280
    alto: int = 800
    # Tamano base del texto de la conversacion. En una tarima el publico lee de
    # lejos, asi que el minimo util es bastante mayor que en una app de escritorio.
    tamano_texto: int = 17
    mostrar_panel_operador: bool = True


@dataclass(frozen=True)
class AppConfig:
    """Configuracion raiz inyectada en todo el arbol de dependencias."""

    model: ModelConfig = field(default_factory=ModelConfig)
    rag: RagConfig = field(default_factory=RagConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    voz: VozConfig = field(default_factory=VozConfig)
    interfaz: InterfazConfig = field(default_factory=InterfazConfig)

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
        HACU_MODELO=ruta.gguf modelo alternativo, para comparar sin tocar codigo
        HACU_CTX=8192         ventana de contexto (un modelo mas grande deja menos VRAM)
        HACU_VOZ=1            activa microfono y altavoz
        HACU_VOZ_SALIDA=1     solo altavoz: HACU habla pero no escucha
        HACU_VOZ_AUTO=1       escucha sola en vez de pulsar-para-hablar
        HACU_STT=medium       tamano del modelo de reconocimiento
        HACU_TTS=sistema      motor de sintesis: auto | piper | sistema | mudo
        HACU_PIPER=ruta.exe   binario de Piper si no esta en el PATH
        HACU_VOZ_MODELO=es_ES-davefx-medium  voz de Piper
        HACU_ENTRADA=3        indice del microfono (ver --diagnostico)
        HACU_SALIDA=5         indice del altavoz
        HACU_PANTALLA_COMPLETA=1  la ventana arranca a pantalla completa
        """
        base = cls(debug_console=_bandera("HACU_DEBUG"))
        memoria = base.memory
        rag = base.rag
        modelo = base.model
        voz = base.voz
        interfaz = base.interfaz

        ruta = os.getenv("HACU_MODELO", "").strip()
        if ruta:
            candidata = Path(ruta)
            if not candidata.is_absolute():
                candidata = PROJECT_ROOT / "models" / candidata
            modelo = replace(modelo, model_path=candidata)
        ctx = _entero("HACU_CTX")
        if ctx is not None:
            modelo = replace(modelo, n_ctx=ctx)

        retencion = _entero("HACU_RETENCION")
        if retencion is not None:
            memoria = replace(memoria, retencion_horas=retencion)
        fragmentos = _entero("HACU_FRAGMENTOS")
        if fragmentos is not None:
            rag = replace(rag, default_results=fragmentos)
        if os.getenv("HACU_MULTILINGUE", "").strip() == "0":
            rag = replace(rag, multilingual_embeddings=False, exigir_multilingue=False)

        if _bandera("HACU_VOZ"):
            voz = replace(voz, activa=True)
        if _bandera("HACU_VOZ_SALIDA"):
            voz = replace(voz, solo_salida=True)
        if _bandera("HACU_VOZ_AUTO"):
            voz = replace(voz, deteccion_automatica=True)
        stt = _texto("HACU_STT")
        if stt:
            voz = replace(voz, modelo_stt=stt)
        tts = _texto("HACU_TTS")
        if tts:
            voz = replace(voz, motor_tts=tts.lower())
        piper = _texto("HACU_PIPER")
        if piper:
            voz = replace(voz, piper_exe=Path(piper))
        voz_modelo = _texto("HACU_VOZ_MODELO")
        if voz_modelo:
            voz = replace(voz, piper_voz=voz_modelo)
        entrada = _entero("HACU_ENTRADA")
        if entrada is not None:
            voz = replace(voz, dispositivo_entrada=entrada)
        salida = _entero("HACU_SALIDA")
        if salida is not None:
            voz = replace(voz, dispositivo_salida=salida)
        if _bandera("HACU_PANTALLA_COMPLETA"):
            interfaz = replace(interfaz, pantalla_completa=True)

        return replace(base, memory=memoria, rag=rag, model=modelo, voz=voz, interfaz=interfaz)

    def presupuesto_contexto(self) -> tuple[int, int]:
        """(tokens estimados del prompt en el peor caso, tokens disponibles).

        Estimacion conservadora a 3.5 caracteres por token en espanol. Sirve para
        fallar ruidosamente al arrancar si alguien sube n_results o el historial
        sin recalcular: llama.cpp truncaria por la izquierda, comiendose el system
        prompt, y el fallo se veria como un cambio de personalidad inexplicable.
        """
        fragmentos = self.rag.broad_results_audacia * (self.rag.chunk_size + self.rag.chunk_overlap)
        hechos = self.memory.max_facts_per_profile * 80
        historial = self.memory.history_messages * self.model.chat_max_tokens_extenso * 4 // 2
        caracteres = _CARACTERES_SISTEMA + fragmentos + hechos + historial + 400
        prompt = int(caracteres / 3.5)
        return prompt, self.model.n_ctx - self.model.chat_max_tokens_extenso
