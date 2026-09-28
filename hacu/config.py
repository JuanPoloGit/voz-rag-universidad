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

from .prompts import MODO_TRIVIA, PERFILES_AUDIENCIA, SALUDO_INICIAL, SYSTEM_PROMPT_BASE

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent

# El system prompt se MIDE, no se estima. Estaba fijado en 3400 caracteres y las
# reglas fueron creciendo hasta 9000: el presupuesto de contexto se calculaba con
# 5600 caracteres de menos, que es justo el error que este calculo existe para
# evitar. La directriz de audiencia mas larga y el modo trivia se suman aparte
# porque solo uno de ellos esta activo a la vez.
def _caracteres_sistema() -> int:
    mayor_audiencia = max((len(d) for d in PERFILES_AUDIENCIA.values()), default=0)
    return len(SYSTEM_PROMPT_BASE) + mayor_audiencia + len(MODO_TRIVIA) + 40


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
    # Cache KV a 8 bits en vez de fp16. Es la palanca que decide si cabe mas
    # contexto: la cache crece LINEALMENTE con n_ctx y con esto ocupa la mitad.
    # Medido en aritmetica (`python -m herramientas.presupuesto_vram`): con el
    # modelo actual, n_ctx 32768 pasa de 9,68 a 7,68 GiB. La perdida de calidad
    # de q8_0 sobre la cache es la mas pequena de todas las cuantizaciones.
    # Requiere flash_attn en las versiones recientes de llama.cpp.
    kv_8bits: bool = False
    flash_attn: bool = True

    # Generacion conversacional (escena). La regla 3 pide 2-4 frases y el modelo
    # la ignoraba soltando monologos de nueve segundos; el tope lo fuerza por
    # hardware. 384 tokens son unas seis frases largas, suficiente para una
    # explicacion tecnica y demasiado poco para un discurso.
    chat_max_tokens: int = 384
    # Turnos que no piden desarrollo —un cierre, una negativa, una pregunta de un
    # solo dato—. Con 384 el modelo llenaba el hueco: cuatro parrafos recitando
    # el catalogo ante un "gracias", 954 caracteres para decir "de acuerdo, te
    # cuento otra cosa". El retenedor descarta la frase incompleta al llegar al
    # tope, asi que el corte nunca se oye.
    #
    # El numero sale de una medida, no de un redondeo: en la corrida del 21/09 un
    # turno se corto en 384 tokens y entrego 1379 caracteres, o sea 3,59
    # caracteres por token. El guion exige 420 caracteres en los turnos breves
    # —unos 25 segundos hablados, que en una sala ya es largo—, y 420/3,59 = 117.
    chat_max_tokens_breve: int = 116
    # Tope para las preguntas que piden desarrollo de verdad ("explicame
    # detalladamente cada proyecto"). Con 384 la respuesta se cortaba a mitad de
    # palabra en la cuarta frase; AudacIA tiene seis proyectos y describirlos en
    # una frase cada uno no es una respuesta. El tope sigue siendo un techo, no
    # un objetivo: la regla 3 del prompt manda brevedad por defecto.
    chat_max_tokens_extenso: int = 900
    chat_temperature: float = 0.3
    # Sin penalizacion de repeticion, dos visitantes que hacen la MISMA pregunta
    # reciben la respuesta calcada palabra por palabra, y dentro de una misma
    # respuesta el modelo podia atascarse repitiendo una frase. No es la
    # temperatura -a 0.3 ya es conservadora, y bajarla mas agrava el bucle en vez
    # de curarlo-: es que `stream_chat` no pasaba ningun parametro de repeticion
    # a llama.cpp. 1.15 es el valor recomendado de llama.cpp para chat; mas alto
    # empieza a evitar palabras necesarias (nombres propios, "AudacIA").
    chat_repeat_penalty: float = 1.15
    # Penaliza tokens ya usados EN ESTE turno, proporcional a cuantas veces
    # salieron. Es lo que corta de verdad un bucle ("...vision artificial, vision
    # artificial..."), que repeat_penalty por si solo no siempre frena.
    chat_frequency_penalty: float = 0.3
    # Penaliza cualquier token que ya aparecio, sin importar cuantas veces: da
    # variedad de vocabulario entre turnos consecutivos con la misma pregunta.
    chat_presence_penalty: float = 0.2

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
    # El troceo respeta las secciones `##`: una ficha de proyecto entra entera en
    # un fragmento y no se parte por la mitad. Solo se subdivide la seccion que
    # pase de este tamano. 2200 cubre la ficha mas larga del corpus actual.
    chunk_max_seccion: int = 2200

    # Fragmentos recuperados segun la amplitud detectada en la pregunta.
    # Calibrado con `python -m pruebas.recuperacion` sobre el corpus real:
    # con el corpus ampliado (53 fragmentos) n=4 recupera 33/36 y n=6 llega a 36/36.
    # ("todos los proyectos") necesitan 10 para cubrir los seis proyectos.
    default_results: int = 6
    # Desde que existe el indice-catalogo, la anchura la da el indice y no un
    # monton de fichas: no hace falta traer diez fragmentos para enumerar.
    broad_results_audacia: int = 6
    broad_results_universidad: int = 8
    # Fichas que acompanan al indice cuando piden un repaso de cada proyecto.
    fragmentos_resumen: int = 4
    # Fragmentos institucionales cuando preguntan por el centro y no por un
    # proyecto. Solo hay diez en total, asi que seis cubren casi cualquier
    # pregunta sin arrastrar fichas que estorban.
    fragmentos_centro: int = 6

    # Embeddings multilingues. El modelo por defecto de Chroma (all-MiniLM-L6-v2)
    # esta entrenado en ingles y sobre este corpus en espanol recupera 35/36
    # consultas con verdad documentada, frente a 36/36 del multilingue. La brecha
    # era 24/36 contra 35/36 antes del rescate lexico, que no depende del idioma
    # del embedding. OJO: esas 36 consultas casi siempre nombran lo que buscan
    # ("¿que es Neupeek?"), que es donde el rescate lexico brilla; una pregunta
    # vaga de tarima sigue dependiendo del embedding.
    # (Medido con `python -m pruebas.recuperacion --n 6`.)
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
    # `small` era el modelo por defecto y el comentario de aqui decia que
    # `medium` "mejora poco en espanol con audio de cerca". El log de la sesion
    # en vivo del 21/09 dice lo contrario, y con tres intentos seguidos sobre la
    # misma frase:
    #
    #   'Explicame como es la creatividad general de este'
    #   'explicame la de la actividad general de Einstein'
    #   'Explicame sobre la Relatividad General de Einstein.'
    #
    # Tres veces para una frase. En una sala con ruido y con visitantes que no
    # van a repetirse tres veces, eso es la exhibicion entera.
    #
    # `large-v3-turbo` tiene el MISMO codificador que large-v3 —que es de donde
    # sale la precision— con un decodificador de cuatro capas en vez de treinta
    # y dos. 809M parametros frente a los 244M de `small` y los 1550M de
    # large-v3: la precision del grande casi al coste del mediano.
    #
    # VRAM: `small` en int8_float16 ocupa ~0.5 GiB medidos. Escalando por
    # parametros sobre ese ancla, turbo ronda 1.1 GiB —un giga mas—, y el
    # presupuesto a 16k tiene 2,8 GiB de holgura. `herramientas.presupuesto_vram
    # --stt large-v3-turbo` hace la cuenta; `nvidia-smi` da la de verdad.
    #
    # HACU_STT=small vuelve al anterior sin tocar codigo.
    modelo_stt: str = "large-v3-turbo"
    dispositivo_stt: str = "cuda"
    computo_stt: str = "int8_float16"
    # None deja que Whisper detecte el idioma de cada frase por su cuenta -lo que
    # pide el comportamiento bilingue: forzar "es" sobre audio en ingles no lo
    # traduce, lo transcribe mal, tratando de encajar sonidos ingleses en
    # ortografia espanola. HACU_IDIOMA="es" vuelve a fijarlo, para una sala donde
    # se sepa de antemano que todo el publico habla espanol y se prefiera la
    # robustez de no tener que adivinar sobre audios muy cortos.
    idioma: str | None = None
    # Los nombres propios de la exhibicion son justo lo que peor transcribe un
    # modelo generico: "Holosand" sale "olo san", "AudacIA" sale "audacia" en
    # minuscula o "au da sia". Sembrar el vocabulario en el prompt inicial los
    # rescata sin reentrenar nada.
    vocabulario: tuple[str, ...] = (
        "AudacIA", "Hacu", "Universidad Simon Bolivar", "Holosand", "Orion",
        "Proyecto Tanque", "MacondoLab", "Adaptia", "Eureka", "Kinect",
        "Soil Sensor", "Barranquilla", "Jose Consuegra",
    )

    # --- Quien esta hablando ----------------------------------------------
    # Distingue que se acerco OTRA persona, sin identificar a nadie y sin guardar
    # nada: la referencia de timbre vive en memoria durante la visita. Evita que
    # el siguiente visitante herede el perfil del anterior.
    detectar_cambio_de_hablante: bool = True
    # Similitud del coseno por debajo de la cual se considera otra persona.
    # Medido con dos voces sinteticas distintas: misma voz 0.844-0.947, voces
    # distintas 0.314-0.448. Con gente real y ruido de sala el hueco se estrecha;
    # el log registra cada comparacion para poder recalibrar en la propia sala.
    umbral_hablante: float = 0.65
    # Menos audio que esto no da para decidir un timbre: se deja pasar sin tocar
    # la referencia, que es preferible a reiniciar el perfil por un monosilabo.
    minimo_segundos_hablante: float = 1.2

    # --- Sintesis ----------------------------------------------------------
    # "piper" es la voz buena y offline; "sistema" usa el sintetizador del sistema
    # operativo (SAPI5 en Windows, espeak-ng en Linux) para que la exhibicion hable
    # aunque falte el modelo de Piper; "mudo" desactiva la sintesis.
    motor_tts: str = "auto"
    # Nombre de la voz de Piper. es_MX-claude-high es espanol latino neutro, que
    # es lo que menos chirria en Barranquilla; es_ES-davefx-medium suena peninsular.
    # Se descarga con `python -m hacu.voz --descargar`.
    piper_voz: str = "es_MX-claude-high"
    # Voz de Piper para cuando la frase esta en ingles (bilingue real: Piper es
    # monolingue por modelo, no hay una sola voz que hable los dos idiomas).
    # None = no hay voz en ingles montada todavia y HACU sigue hablando ingles
    # con la voz en espanol -suena con acento, pero nunca se queda muda-. Se
    # descarga igual que la de espanol: `python -m hacu.voz --descargar --idioma en`
    # despues de fijar HACU_VOZ_MODELO_EN=<nombre-de-la-voz>.
    piper_voz_en: str | None = None
    carpeta_voces: Path = PROJECT_ROOT / "models" / "voz"
    # Solo para el binario suelto de Piper (las versiones anteriores a piper-tts).
    piper_exe: Path | None = None
    velocidad_tts: float = 1.0
    volumen_tts: float = 0.9
    # Tope del silencio *interno* de una frase, en milisegundos. El modelo de voz
    # mete huecos de 250 a 450 ms en los limites prosodicos y en una sala suenan
    # a duda. 0 desactiva el recorte y deja el audio de Piper tal cual.
    pausa_maxima_ms: int = 120
    # Al hablar por frases, el visitante oye la primera mientras el modelo genera
    # la segunda. Por debajo de este minimo la frase se acumula con la siguiente:
    # trocear "Si." de su continuacion suena entrecortado.
    minimo_frase: int = 12
    maximo_frase: int = 240
    # Reescrituras que se aplican SOLO al texto que va al sintetizador: la
    # pantalla y la memoria conservan la ortografia original. Vacio usa el lexico
    # por defecto de `hacu.voz.pronunciacion`; una tupla propia lo sustituye.
    pronunciaciones: tuple[tuple[str, str], ...] = ()


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
    # La vista publica es solo el nucleo animado; el operador salta a la Pro con
    # su boton, Ctrl+M o el atajo. HACU_VISTA_PRO=1 arranca ya en la Pro (para
    # depurar en el sitio sin tener que cambiar de vista cada vez).
    vista_simple_al_arrancar: bool = True


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
    # Lo primero que dice HACU al arrancar, antes de que nadie le pregunte nada.
    # Vacio = no saluda (util en el harness de pruebas, que mide turnos limpios).
    saludo_inicial: str = SALUDO_INICIAL
    # Adonde mandar a un visitante que lo esta pasando mal. VACIO A PROPOSITO:
    # un telefono de crisis inventado es peor que ninguno, y en la primera prueba
    # real el modelo ofrecio, por su cuenta, un servicio de ayuda *en Venezuela*
    # estando el montaje en Barranquilla. Lo rellena el centro con lo que diga
    # Bienestar Universitario. Mientras este vacio, HACU remite a la persona que
    # atiende el stand, que es la respuesta correcta en cualquier caso.
    recursos_de_ayuda: str = ""

    @classmethod
    def from_env(cls) -> "AppConfig":
        """Configuracion de operacion sin tocar codigo.

        HACU_DEBUG=1          diagnostico en consola
        HACU_RETENCION=8      horas de historial que se conservan (0 = sin poda)
        HACU_HISTORIAL=24     mensajes de conversacion que viajan en cada turno
                              (subir n_ctx sin subir esto no cambia NADA: agranda
                               el envase y deja la holgura sin usar)
        HACU_DB=/datos/hacu.db  donde vive la memoria (en contenedor, un volumen)
        HACU_FRAGMENTOS=4     fragmentos recuperados por consulta normal
        HACU_MULTILINGUE=0    fuerza el embedding por defecto de Chroma
        HACU_MODELO=ruta.gguf modelo alternativo, para comparar sin tocar codigo
        HACU_CTX=8192         ventana de contexto (un modelo mas grande deja menos VRAM)
        HACU_KV8=1            cache KV a 8 bits: la mitad de VRAM por token de contexto
        HACU_VOZ=1            activa microfono y altavoz
        HACU_VOZ_SALIDA=1     solo altavoz: HACU habla pero no escucha
        HACU_HABLANTES=0      no distinguir cuando cambia la persona que habla
        HACU_VOZ_AUTO=1       escucha sola en vez de pulsar-para-hablar
        HACU_STT=medium       tamano del modelo de reconocimiento
        HACU_TTS=sistema      motor de sintesis: auto | piper | sistema | mudo
        HACU_PIPER=ruta.exe   binario de Piper si no esta en el PATH
        HACU_IDIOMA=es          fuerza el idioma del reconocimiento ("" = detectar solo)
        HACU_VOZ_MODELO=es_ES-davefx-medium  voz de Piper
        HACU_VOZ_MODELO_EN=en_US-hfc_female-medium  voz de Piper para el ingles
        HACU_ENTRADA=3        indice del microfono (ver --diagnostico)
        HACU_SALIDA=5         indice del altavoz
        HACU_PAUSA_MS=120     tope del silencio interno de una frase (0 = sin recorte)
        HACU_PANTALLA_COMPLETA=1  la ventana arranca a pantalla completa
        HACU_VISTA_PRO=1       arranca en la vista Pro en vez de la simple
        HACU_SALUDO="..."     otra frase de apertura ("" = arrancar sin saludo)
        HACU_AYUDA="..."      recursos de ayuda del centro, para el modo cuidado
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
        if _bandera("HACU_KV8"):
            modelo = replace(modelo, kv_8bits=True)

        retencion = _entero("HACU_RETENCION")
        if retencion is not None:
            memoria = replace(memoria, retencion_horas=retencion)
        # En contenedor la memoria tiene que caer en un volumen montado: dentro de
        # la imagen se pierde en cada `docker run --rm`.
        ruta_db = _texto("HACU_DB")
        if ruta_db:
            memoria = replace(memoria, db_path=Path(ruta_db))
        # La palanca que de verdad gasta el contexto. `verificar_presupuesto`
        # aborta el arranque si la combinacion no cabe, asi que pasarse se nota
        # al instante y no en mitad de una visita.
        historial = _entero("HACU_HISTORIAL")
        if historial is not None and historial > 0:
            memoria = replace(memoria, history_messages=historial)
        fragmentos = _entero("HACU_FRAGMENTOS")
        if fragmentos is not None:
            rag = replace(rag, default_results=fragmentos)
        if os.getenv("HACU_MULTILINGUE", "").strip() == "0":
            rag = replace(rag, multilingual_embeddings=False, exigir_multilingue=False)

        if _bandera("HACU_VOZ"):
            voz = replace(voz, activa=True)
        if _bandera("HACU_VOZ_SALIDA"):
            voz = replace(voz, solo_salida=True)
        if os.getenv("HACU_HABLANTES", "").strip() == "0":
            voz = replace(voz, detectar_cambio_de_hablante=False)
        if _bandera("HACU_VOZ_AUTO"):
            voz = replace(voz, deteccion_automatica=True)
        stt = _texto("HACU_STT")
        if stt:
            voz = replace(voz, modelo_stt=stt)
        # Cadena vacia es "detectar solo", que ya es el default: no hay que
        # distinguirla de "no definida" como en el saludo, asi que _texto sirve tal cual.
        idioma = _texto("HACU_IDIOMA")
        if idioma:
            voz = replace(voz, idioma=idioma)
        tts = _texto("HACU_TTS")
        if tts:
            voz = replace(voz, motor_tts=tts.lower())
        piper = _texto("HACU_PIPER")
        if piper:
            voz = replace(voz, piper_exe=Path(piper))
        voz_modelo = _texto("HACU_VOZ_MODELO")
        if voz_modelo:
            voz = replace(voz, piper_voz=voz_modelo)
        voz_modelo_en = _texto("HACU_VOZ_MODELO_EN")
        if voz_modelo_en:
            voz = replace(voz, piper_voz_en=voz_modelo_en)
        entrada = _entero("HACU_ENTRADA")
        if entrada is not None:
            voz = replace(voz, dispositivo_entrada=entrada)
        salida = _entero("HACU_SALIDA")
        if salida is not None:
            voz = replace(voz, dispositivo_salida=salida)
        pausa = _entero("HACU_PAUSA_MS")
        if pausa is not None:
            voz = replace(voz, pausa_maxima_ms=max(0, pausa))
        if _bandera("HACU_PANTALLA_COMPLETA"):
            interfaz = replace(interfaz, pantalla_completa=True)
        if _bandera("HACU_VISTA_PRO"):
            interfaz = replace(interfaz, vista_simple_al_arrancar=False)

        # Cadena vacia es una eleccion valida (arrancar callado), asi que aqui no
        # sirve `_texto`, que la confunde con "no definida".
        saludo = os.getenv("HACU_SALUDO")
        if saludo is not None:
            base = replace(base, saludo_inicial=saludo.strip())
        ayuda = _texto("HACU_AYUDA")
        if ayuda:
            base = replace(base, recursos_de_ayuda=ayuda)

        return replace(base, memory=memoria, rag=rag, model=modelo, voz=voz, interfaz=interfaz)

    def presupuesto_contexto(self) -> tuple[int, int]:
        """(tokens estimados del prompt en el peor caso, tokens disponibles).

        Estimacion conservadora a 3.5 caracteres por token en espanol. Sirve para
        fallar ruidosamente al arrancar si alguien sube n_results o el historial
        sin recalcular: llama.cpp truncaria por la izquierda, comiendose el system
        prompt, y el fallo se veria como un cambio de personalidad inexplicable.
        """
        fragmentos = self.rag.broad_results_audacia * self.rag.chunk_max_seccion
        hechos = self.memory.max_facts_per_profile * 80
        historial = self.memory.history_messages * self.model.chat_max_tokens_extenso * 4 // 2
        caracteres = _caracteres_sistema() + fragmentos + hechos + historial + 400
        prompt = int(caracteres / 3.5)
        return prompt, self.model.n_ctx - self.model.chat_max_tokens_extenso
