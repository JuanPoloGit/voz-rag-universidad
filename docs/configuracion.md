# Configuración

Toda ruta, umbral y límite del proyecto vive en `hacu/config.py`, con la
justificación de cada valor al lado. Ningún módulo construye rutas por su cuenta:
se les inyecta la configuración. Las rutas son absolutas y derivadas de la raíz
del repositorio, así que HACU arranca igual desde cualquier directorio de trabajo.

## Quién manda sobre quién

```
argumentos de la línea de órdenes   >   variables de entorno   >   valores por defecto
```

`run_hacu.aplicar_argumentos()` aplica los argumentos **encima** de lo que ya
resolvió `AppConfig.from_env()`. Así, `--sin-voz` gana aunque `HACU_VOZ=1` esté
puesta en la sesión.

---

## Variables de entorno

Lo que se puede cambiar sin tocar código ni reinstalar nada.

| Variable | Efecto |
|---|---|
| `HACU_DEBUG=1` | Diagnóstico en consola: router, perfil, latencia, migraciones |
| `HACU_MODELO=ruta.gguf` | Otro modelo (relativo a `models/`, o ruta absoluta) |
| `HACU_CTX=8192` | Ventana de contexto; un modelo mayor deja menos VRAM |
| `HACU_KV8=1` | Caché KV a 8 bits: la mitad de VRAM por token de contexto. Es la palanca que decide si caben 32k (ver `herramientas/presupuesto_vram.py`) |
| `HACU_HISTORIAL=24` | Mensajes de conversación que viajan en cada turno. **Subir `HACU_CTX` sin subir esto no cambia nada**: agranda el envase y deja la holgura sin usar |
| `HACU_RETENCION=8` | Horas de historial que se conservan (`0` desactiva la poda) |
| `HACU_DB=/app/datos/hacu.db` | Dónde vive la memoria. En contenedor, apuntar a un volumen montado |
| `HACU_FRAGMENTOS=4` | Fragmentos recuperados por consulta normal |
| `HACU_MULTILINGUE=0` | Acepta el embedding por defecto de Chroma; **degrada el RAG a la mitad** |
| `HACU_SALUDO="..."` | Otra frase de apertura. `""` arranca sin saludar |
| `HACU_AYUDA="..."` | Recursos de ayuda del centro, para el modo cuidado. Vacío por defecto |
| `HACU_VOZ=1` | Activa micrófono y altavoz |
| `HACU_VOZ_SALIDA=1` | Solo altavoz: HACU habla pero no escucha |
| `HACU_VOZ_AUTO=1` | Escucha automática en vez de pulsar-para-hablar (solo en la ventana) |
| `HACU_HABLANTES=0` | No distinguir cuándo cambia la persona que habla |
| `HACU_STT=medium` | Tamaño del modelo de reconocimiento: `tiny`, `base`, `small`, `medium` |
| `HACU_TTS=sistema` | Motor de síntesis: `auto`, `piper`, `piper-proceso`, `piper-externo`, `sistema`, `mudo` |
| `HACU_PIPER=ruta.exe` | Binario de Piper si no está en el PATH |
| `HACU_VOZ_MODELO=es_ES-davefx-medium` | Otra voz de Piper para español |
| `HACU_VOZ_MODELO_EN=en_US-lessac-medium` | Otra voz de Piper para inglés (por defecto `en_US-hfc_female-medium`, ver [voz.md](voz.md)) |
| `HACU_ENTRADA=3` | Índice del micrófono (ver `python -m hacu.voz`) |
| `HACU_SALIDA=5` | Índice del altavoz |
| `HACU_PAUSA_MS=120` | Tope del silencio interno de una frase, en ms (`0` deja el audio de Piper tal cual) |
| `HACU_PANTALLA_COMPLETA=1` | La ventana arranca a pantalla completa |

```powershell
# PowerShell: valen para la sesión actual
$env:HACU_DEBUG="1"; $env:HACU_STT="tiny"
python run_hacu.py --ui --voz
```

```bash
HACU_DEBUG=1 HACU_STT=tiny python run_hacu.py --ui --voz
```

---

## Argumentos de `run_hacu.py`

| Argumento | Efecto |
|---|---|
| `--debug` | Como `HACU_DEBUG=1` |
| `--ui` | Ventana de exhibición en vez de consola |
| `--voz` | Micrófono y altavoz |
| `--voz-salida` | Solo altavoz |
| `--sin-voz` | Fuerza el modo escrito, ganando a las variables de entorno |
| `--pantalla-completa` | La ventana arranca a pantalla completa |

---

## Los bloques de `config.py`

### `ModelConfig` — el modelo

| Campo | Por defecto | Qué decide |
|---|---|---|
| `model_path` | `models/Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf` | Qué GGUF se carga |
| `n_ctx` | `16384` | Ventana de contexto |
| `n_gpu_layers` | `-1` | Todas las capas a la GPU |
| `chat_max_tokens` | `384` | Techo de una respuesta normal. La regla 3 pide 2-4 frases y el modelo soltaba monólogos de nueve segundos |
| `chat_max_tokens_extenso` | `900` | Techo cuando se pide desarrollo. Enumerar 32 proyectos no cabe en 384 |
| `chat_temperature` | `0.3` | Baja: en una exhibición importa más la fidelidad que la variedad |
| `utility_*` | — | Extracción y consolidación de memoria, con salida JSON restringida |

### `RagConfig` — el corpus y su recuperación

| Campo | Por defecto | Qué decide |
|---|---|---|
| `doc_folder` | `documents/` | De dónde sale el conocimiento |
| `db_path` | `chroma_db/` | Dónde vive el índice vectorial |
| `chunk_max_seccion` | `2200` | Una ficha `##` entra entera si cabe aquí |
| `default_results` | `6` | Fragmentos por consulta normal |
| `broad_results_audacia` / `_universidad` | `6` / `8` | Consultas amplias |
| `fragmentos_resumen` | `4` | Fichas que acompañan al índice en nivel `RESUMEN` |
| `fragmentos_centro` | `6` | Fragmentos institucionales en nivel `CENTRO` |
| `multilingual_model` | `paraphrase-multilingual-MiniLM-L12-v2` | El embedding |
| `exigir_multilingue` | `True` | **Aborta el arranque** si el multilingüe no está |

Sobre esa última: con el embedding por defecto de Chroma —entrenado en inglés— la
recuperación sobre el corpus en español baja de 39/39 a 38/39 consultas, y HACU
afirmaría no conocer proyectos que sí están documentados. Para una exhibición eso
es peor que no arrancar. `HACU_MULTILINGUE=0` acepta el modo degradado a
sabiendas.

### `MemoryConfig` — la memoria de los visitantes

| Campo | Por defecto | Qué decide |
|---|---|---|
| `db_path` | `hacu_memory.db` | La base SQLite. **No se versiona** |
| `history_messages` | `10` | Mensajes de historial que viajan al modelo (5 intercambios) |
| `max_facts_per_profile` | `12` | Hechos por visitante |
| `consolidation_threshold` | `10` | Cuándo se depuran los hechos |
| `retencion_horas` | `24` | Al arrancar se borra el historial más viejo. `0` desactiva |
| `sanitize_on_startup` | `True` | Limpia perfiles y hechos inválidos al arrancar |

Ver [datos.md](datos.md).

### `VozConfig` — oído y boca

Detalle completo en [voz.md](voz.md). Lo que más se toca:

| Campo | Por defecto | Qué decide |
|---|---|---|
| `modelo_stt` | `small` | ~0,5 GB de VRAM; convive con el Llama de 8B en 12 GB |
| `vocabulario` | AudacIA, Holosand, Orion… | Nombres propios sembrados en el reconocedor |
| `motor_tts` | `auto` | Piper en proceso → Piper externo → voz del sistema → mudo |
| `piper_voz` | `es_MX-claude-high` | Voz descargable con `python -m hacu.voz --descargar` |
| `piper_voz_en` | `en_US-hfc_female-medium` | Voz para cuando la frase está en inglés. Descargable con `python -m hacu.voz --descargar --idioma en`; sin descargar, HACU sigue hablando inglés con la voz en español |
| `pronunciaciones` | `()` | Léxico propio; vacío usa el medido de `pronunciacion.py` |
| `detectar_cambio_de_hablante` | `True` | Nota cuándo se acerca otra persona, sin identificarla |
| `umbral_hablante` | `0.65` | Misma voz 0,844–0,947; voces distintas 0,314–0,448 |
| `minimo_frase` / `maximo_frase` | `12` / `240` | Troceo del stream en frases pronunciables |
| `deteccion_automatica` | `False` | Escucha automática desde el arranque (solo ventana) |

### `InterfazConfig` — la ventana

| Campo | Por defecto | Qué decide |
|---|---|---|
| `pantalla_completa` | `False` | Arrancar a pantalla completa |
| `ancho` / `alto` | `1280` / `800` | Tamaño de ventana |
| `tamano_texto` | `17` | Base del texto. En una tarima el público lee de lejos |
| `mostrar_panel_operador` | `True` | Panel derecho visible al arrancar |

### `AppConfig` — la raíz

| Campo | Por defecto | Qué decide |
|---|---|---|
| `default_user` | `visitante` | Perfil anónimo |
| `log_file` | `logs/hacu.log` | Dónde va el log |
| `debug_console` | `False` | Diagnóstico en pantalla |
| `saludo_inicial` | «Hola, soy Hacu. Bienvenido a AudacIA. ¿Serías tan amable de decirme cuál es tu nombre?» | La primera frase. `""` arranca callado |
| `recursos_de_ayuda` | `""` | Adónde mandar a quien lo está pasando mal. Vacío a propósito: lo rellena el centro ([datos.md](datos.md)) |

---

## El saludo de apertura

Es **texto fijo**, no una respuesta del modelo: una frase literal puesta delante
de un 8B es exactamente lo que acaba recitando en los turnos siguientes (ver
[arquitectura.md](arquitectura.md#contaminación-la-regla-que-más-veces-se-ha-roto)).

Sí se registra en el historial como turno de HACU, y eso es lo importante: cuando
el visitante conteste «Daniela», el modelo verá la pregunta justo encima. Sin eso,
el primer mensaje de la conversación sería un nombre suelto sin nada que lo
explique.

Tutea porque la regla 15 obliga a tutear sin cambiar de trato a mitad de
conversación, y el saludo entra en el historial: un «usted» ahí arrastraría al
modelo a usted durante el resto de la visita. Si se cambia con `HACU_SALUDO`,
conviene mantener el tuteo por el mismo motivo.

En la ventana se repite al pulsar **Nuevo visitante**: quien acaba de acercarse
tiene que oír la invitación a decir su nombre.

---

## Cambiar el presupuesto de contexto sin romperlo

Subir `HACU_FRAGMENTOS`, `history_messages` o `chat_max_tokens_extenso` hace
crecer el prompt. Si deja de caber en `n_ctx`, **el arranque se aborta** con
`ConfiguracionInviable` en vez de truncarse en escena.

```powershell
python -c "from hacu.config import AppConfig; p,d = AppConfig().presupuesto_contexto(); print(f'{p} de {d}')"
```
