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
python run_hacu.py
```

La primera ejecución descarga el modelo de embeddings (~470 MB) e indexa el corpus.
Las siguientes arrancan directamente.

| Comando | Qué hace |
|---|---|
| `python run_hacu.py` | Modo exhibición: consola limpia, diagnóstico al log |
| `python run_hacu.py --debug` | Añade router, perfil activo, latencia y migraciones en pantalla |
| `.\iniciar_robot.ps1` | Lo mismo dentro de Docker con GPU |

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
python -m pruebas.bateria --seco    # 60 entradas contra las capas deterministas
python -m pruebas.bateria --vivo    # 60 entradas contra el modelo real
```

`test_unidades` es la que se corre después de cada cambio. `bateria --vivo` es la que se
corre antes de una exhibición: usa una base temporal y deja un informe en JSON y CSV en
`pruebas/informes/`. `PRUEBAS.md` describe las 60 entradas y su criterio de aceptación, y
se regenera con `python -m pruebas.bateria --listar > PRUEBAS.md`.

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
documents/             corpus institucional en markdown
pruebas/               suite de regresión y herramientas de medición
```

Para añadir conocimiento basta editar o añadir un `.md` en `documents/`: el índice se
sincroniza solo al arrancar, y los fragmentos de versiones anteriores se eliminan. Los
archivos cuyo nombre contiene "audacia" van al corpus de AudacIA; el resto, al
institucional.

## Limitaciones conocidas

- **No hay voz.** La interfaz es una consola de texto pese al nombre del proyecto.
  `HacuSession.turno()` acepta un `on_token`, que es el punto de enganche para un
  sintetizador por frases.
- El router es léxico. Las preguntas de seguimiento sin palabra clave se rescatan por
  distancia semántica, pero una referencia muy vaga puede traer el fragmento equivocado.
- Los nombres compuestos se limitan a dos palabras.
- Una sola estación: hay un perfil activo a la vez.
