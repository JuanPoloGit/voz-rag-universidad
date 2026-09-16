# HACU — asistente expositor de AudacIA

HACU (Hardware de Audacia de Comunicación Universitaria) es un asistente conversacional
que responde sobre los proyectos de **AudacIA** y sobre la **Universidad Simón Bolívar**
durante una exhibición abierta al público. Corre **enteramente en local**: el modelo, el
índice vectorial y la memoria de los visitantes no salen de la máquina.

## Requisitos

- GPU NVIDIA con CUDA (desarrollado sobre una RTX 5070 Ti Laptop)
- Python 3.10+
- El modelo `Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf` en `models/`

## Puesta en marcha

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:CMAKE_ARGS="-DGGML_CUDA=on"; pip install llama-cpp-python

pip install -r requirements-ui.txt     # ventana de exhibición (opcional)
pip install -r requirements-voz.txt    # micrófono y altavoz (opcional)
python -m hacu.voz --descargar         # voz de Piper (~110 MB)

python run_hacu.py --ui --voz
```

La primera ejecución descarga el modelo de embeddings (~470 MB) e indexa el corpus.
Las siguientes arrancan directamente.

| Comando | Qué hace |
|---|---|
| `python run_hacu.py` | Consola limpia, diagnóstico al log |
| `python run_hacu.py --debug` | Añade router, perfil activo, latencia y migraciones en pantalla |
| `python run_hacu.py --ui` | Ventana de exhibición, sin voz |
| `python run_hacu.py --voz-salida` | Consola: escribes tú, HACU responde en voz alta |
| `python run_hacu.py --ui --voz` | Ventana con micrófono y altavoz |
| `python run_hacu.py --ui --voz-salida` | Ventana con altavoz, sin micrófono |
| `python run_hacu.py --ui --voz --pantalla-completa` | Lo que se proyecta el día de la exhibición |
| `python -m hacu.voz` | Diagnóstico de audio: qué micrófono y qué altavoz |
| `.\iniciar_robot.ps1` | Lo mismo dentro de Docker con GPU |

## La ventana

Tres zonas, cada una para un público distinto. **Izquierda**: el núcleo animado,
el botón de hablar y el medidor de nivel — es lo que ve el visitante desde lejos y
lo único que necesita entender. **Centro**: la conversación, que crece token a
token mientras el modelo escribe. **Derecha**: los mandos del operador, que se
ocultan con F9 para que el público no vea la cocina.

El núcleo cambia de color según el estado: gris en espera, verde escuchando (los
anillos siguen el nivel real del micrófono), ámbar pensando, turquesa hablando.
Es el único indicador que se lee desde el fondo de una sala.

| Tecla | Efecto |
|---|---|
| Espacio (mantener) | Hablarle a HACU |
| `F9` | Oculta o muestra el panel del operador |
| `F11` | Pantalla completa |
| `Esc` | Callar a HACU en mitad de una frase |

Si no hay micrófono, el botón se deshabilita solo y queda el campo de texto: la
ventana funciona igual.

## Voz

```powershell
python -m hacu.voz                        # lista micrófonos y altavoces con su índice
python -m hacu.voz --probar               # graba 3 s y los reproduce
python -m hacu.voz --hablar "Hola, soy Hacu"
python -m hacu.voz --descargar            # baja la voz de Piper configurada
python -m hacu.voz --calibrar             # mide el ruido de la sala

# Diagnóstico sin depender del altavoz ni del micrófono de la máquina
python -m hacu.voz --hablar "Hola, soy Hacu" --guardar prueba.wav
python -m hacu.voz --transcribir grabacion.wav
```

`--guardar` vuelca la síntesis a un WAV sin reproducirla, y `--transcribir` hace
el camino inverso desde un fichero. Sirven para separar dos fallos que suenan
igual: que HACU corte la frase, o que la corte la salida de audio. Si el WAV está
entero, el problema no es de HACU.

Es lo primero que hay que correr al llegar a la sala, antes de levantar el modelo.
En una portátil con webcam, base de conexiones y auriculares llega a haber cinco
entradas, y el sistema no siempre elige la que uno cree. Los índices se fijan con
`HACU_ENTRADA` y `HACU_SALIDA`.

**Reconocimiento**: faster-whisper `small` en int8, que ocupa unos 0.5 GB de VRAM
y convive con el Llama de 8B dentro de los 12 GB de la portátil. Los nombres
propios de la exhibición van sembrados en el prompt inicial del reconocedor
(`vocabulario` en `VozConfig`): sin eso «Holosand» se transcribe «olo san» y el
router no reconoce el dominio.

**Síntesis**: Piper si está instalado, y si no la voz del sistema operativo (SAPI5
en Windows). El respaldo suena a robot de los noventa, pero existe en cualquier
máquina y hace que HACU hable el primer día sin descargar nada. HACU no espera a
terminar de generar para hablar: un segmentador trocea el stream en frases y
pronuncia la primera mientras el modelo escribe la segunda.

Piper se carga **dentro del proceso**, una sola vez al arrancar. El respaldo que
lanza `python -m piper` por frase sigue ahí, pero solo como último recurso:
medido, 0.21 s por frase en proceso frente a 2.52 s lanzando el proceso, y en una
máquina Windows con una voz `high` esa diferencia sube a más de diez segundos de
silencio en cada punto. El arranque dice qué motor se eligió, y avisa si acabó en
el lento. `HACU_TTS` acepta `piper-proceso`, `piper-externo`, `sistema` o `mudo`
para forzar uno.

`--voz-salida` monta la boca y no el oído: no descarga el modelo de
reconocimiento ni necesita entrada de audio. Sirve para probar la voz desde una
sesión remota, y también el día que el micrófono falle a media jornada — HACU
sigue hablando y el operador escribe las preguntas.

**Pulsar para hablar es el modo por defecto, y no por comodidad.** En una tarima
el altavoz alimenta al micrófono: con detección automática HACU se oye a sí mismo
y se responde solo. La escucha automática está implementada y se activa desde el
panel (calibra el ruido de sala al encenderla), pero es para sala controlada o
auriculares.

## Panel del operador

Durante la conversación, escribir un número ejecuta un comando en vez de hablar con HACU.

| | |
|---|---|
| `0` | Volver a mostrar el panel |
| `1` | Apagar |
| `2` | Limpiar el chat inmediato, conservando la memoria a largo plazo |
| `3` | Activar / desactivar el modo trivia |
| `4` | Auditar la memoria episódica del perfil activo |
| `5` | Cambiar el perfil de audiencia (general, técnico, infantil, artístico) |
| `6` | Restablecer la memoria del perfil activo |
| `7` | Purgar toda la base de datos, con confirmación |
| `8` | Gestionar el perfil: listar, fijar el nombre a mano, volver al anónimo |

## Configuración sin tocar código

| Variable | Efecto |
|---|---|
| `HACU_DEBUG=1` | Diagnóstico en consola |
| `HACU_RETENCION=8` | Horas de historial que se conservan (`0` desactiva la poda) |
| `HACU_FRAGMENTOS=4` | Fragmentos recuperados por consulta normal |
| `HACU_MULTILINGUE=0` | Acepta el embedding por defecto de Chroma; degrada el RAG a la mitad |
| `HACU_VOZ=1` | Activa micrófono y altavoz |
| `HACU_VOZ_SALIDA=1` | Solo altavoz: HACU habla pero no escucha |
| `HACU_VOZ_AUTO=1` | Escucha automática en vez de pulsar-para-hablar |
| `HACU_STT=medium` | Tamaño del modelo de reconocimiento |
| `HACU_TTS=sistema` | Motor de síntesis: `auto`, `piper`, `piper-proceso`, `piper-externo`, `sistema` o `mudo` |
| `HACU_VOZ_MODELO=es_ES-davefx-medium` | Otra voz de Piper |
| `HACU_ENTRADA=3` / `HACU_SALIDA=5` | Índices de micrófono y altavoz |
| `HACU_PANTALLA_COMPLETA=1` | La ventana arranca a pantalla completa |

Todo lo demás vive en `hacu/config.py`, con el porqué de cada valor documentado al lado.

Si el modelo multilingüe no carga —falta la librería o falla la descarga— **el arranque se
aborta a propósito**. Con el embedding por defecto la recuperación baja de 18/20 a 9/20
consultas, y HACU afirmaría no conocer proyectos que sí están documentados; para una
exhibición eso es peor que no arrancar. `HACU_MULTILINGUE=0` acepta ese modo de forma
consciente.

## Datos de los visitantes

El sistema guarda dos cosas por visitante: la transcripción reciente de la conversación y
un puñado de hechos permanentes que extrae de lo que dice. Ambos en `hacu_memory.db`,
que **no se versiona**.

La transcripción se borra automáticamente al arrancar cuando supera las horas de
`retencion_horas` (24 por defecto). Los hechos episódicos se conservan: son pocos y son lo
que permite que HACU reconozca a alguien que vuelve. El operador puede borrar en cualquier
momento con `6` (perfil activo) o `7` (todo).

Antes de una exhibición real conviene que alguien de la universidad revise esta política:
se trata de datos personales de público general, y parte de ese público son menores.

## Pruebas

```powershell
python -m pruebas.test_unidades     # regresión completa, ~2 s, sin GPU
python -m pruebas.recuperacion      # calidad del RAG, ~20 s, sin GPU
python -m pruebas.bateria --seco    # 60 entradas sueltas contra las capas deterministas
python -m pruebas.bateria --vivo    # 60 entradas sueltas contra el modelo real
python -m pruebas.conversacion      # una visita completa de 25 turnos encadenados
python -m pruebas.test_unidades voz # solo la capa de voz, sin micrófono
```

`test_unidades` es la que se corre después de cada cambio. `bateria --vivo` es la que se
corre antes de una exhibición: usa una base temporal y deja un informe en JSON y CSV en
`pruebas/informes/`. `PRUEBAS.md` describe las 60 entradas y su criterio de aceptación, y
se regenera con `python -m pruebas.bateria --listar > PRUEBAS.md`.

### La conversación de 25 turnos

La batería mide sesenta entradas sueltas y barajadas; mide el turno, no la conversación.
`pruebas/conversacion.py` mide lo contrario: **una sola visita con hilo**, en orden fijo,
con un mismo visitante, donde cada turno depende del anterior. Es la prueba que se parece
a lo que va a pasar en la tarima.

El guion (`pruebas/guion.py`) tiene seis tipos de turno:

| Tipo | Qué mide |
|---|---|
| `ESPECIFICA` | dato puntual documentado; la respuesta debe ser corta |
| `EXTENDIDA` | pide desarrollo; la respuesta debe pasar de ~500 caracteres |
| `SEGUIMIENTO` | «¿y eso para qué sirve?», «¿cómo es eso?»: no se sostiene sin el turno anterior |
| `CORRECCION` | el visitante afirma algo falso y HACU tiene el dato correcto |
| `SIN_DATO` | no está en el corpus; hay que admitirlo, incluso bajo presión |
| `PERSONAL` | acoge el nombre, lo recuerda, no revela de dónde lo sabe |

Cada turno declara qué términos del corpus **debe** contener la respuesta y qué
invenciones **no** puede contener, con comprobación sensible a la negación: «Holosand no
usa gafas» no cuenta como haber dicho «gafas». Los turnos G17 y G18 reproducen la
alucinación del observatorio astronómico y el desafío de fuentes que la siguió, de modo
que esa regresión queda cubierta por una comprobación y no por la memoria de nadie.

```powershell
python -m pruebas.conversacion --listar      # imprime el guion sin ejecutarlo
python -m pruebas.conversacion --desde G15   # arranca en un turno concreto
python -m pruebas.conversacion --tipo CORRECCION
```

`GUION.md` es la versión legible del guion completo, y se regenera con
`python -m pruebas.conversacion --listar > GUION.md`.

## Estructura

```
run_hacu.py            entrada
hacu/
  bootstrap.py         construye y cablea todo el sistema
  cli.py               consola del operador
  session.py           orquestación de un turno
  identity.py          quién es el visitante; valida nombres, migra perfiles
  routing.py           a qué corpus va la pregunta
  context.py           qué se le manda al modelo
  rag.py               indexación y recuperación sobre ChromaDB
  prompts.py           system prompt y prompts de utilidad
  llm.py               único acceso a llama.cpp, serializado
  estilo.py            recorte determinista del cierre de turno
  extractor.py         memoria episódica en segundo plano
  sanitizer.py         qué merece guardarse como hecho
  memory.py            persistencia SQLite
  config.py            toda la configuración, con su justificación
  voz/
    microfono.py       captura con sounddevice, pulsar-para-hablar y escucha
    deteccion.py       detección de voz por energía (lógica pura)
    transcriptor.py    faster-whisper
    sintetizador.py    Piper, voz del sistema y modo mudo
    segmentador.py     trocea el stream en frases pronunciables
    dispositivos.py    inventario y diagnóstico de audio
  interfaz/
    ventana.py         la ventana de exhibición
    widgets.py         núcleo animado, medidor de nivel, burbujas
    hilos.py           turnos y transcripción fuera del hilo de Qt
    estilos.py         paleta y hoja de estilos
documents/             corpus institucional en markdown
pruebas/               suite de regresión y herramientas de medición
```

Para añadir conocimiento basta editar o añadir un `.md` en `documents/`: el índice se
sincroniza solo al arrancar, y los fragmentos de versiones anteriores se eliminan. Los
archivos cuyo nombre contiene "audacia" van al corpus de AudacIA; el resto, al
institucional.

## Limitaciones conocidas

- La capa de voz está **probada solo en su lógica** (segmentación, detección,
  selección de motor): el micrófono y el altavoz reales no se han medido todavía.
- Con altavoz abierto, la escucha automática hace que HACU se oiga a sí mismo.
  Push-to-talk lo evita; unos auriculares o un micrófono direccional también.
- El router es léxico. Las preguntas de seguimiento sin palabra clave se rescatan por
  distancia semántica, pero una referencia muy vaga puede traer el fragmento equivocado.
- Los nombres compuestos se limitan a dos palabras.
- Una sola estación: hay un perfil activo a la vez.
