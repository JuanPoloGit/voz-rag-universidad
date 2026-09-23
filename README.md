# HACU — asistente expositor de AudacIA

HACU (*Hardware de Audacia de Comunicación Universitaria*) es un asistente
conversacional que responde sobre los proyectos de **AudacIA** y sobre la
**Universidad Simón Bolívar** durante una exhibición abierta al público. Escucha
por micrófono, responde en voz alta, recuerda a quien tiene delante durante la
visita y admite cuando no sabe algo.

Corre **enteramente en local**: el modelo, el índice vectorial y la memoria de los
visitantes no salen de la máquina. No hay llamadas a ninguna API.

```powershell
python run_hacu.py --ui --voz --pantalla-completa
```

---

## Arrancar en cinco minutos

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install llama-cpp-python --prefer-binary `
  --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu121

# El modelo (~4,9 GB) no viene en el repositorio:
#   -> models/Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf

pip install -r requirements-ui.txt     # ventana de exhibición (opcional)
pip install -r requirements-voz.txt    # micrófono y altavoz (opcional)
python -m hacu.voz --descargar         # voz de Piper (~110 MB)

python run_hacu.py --ui --voz
```

La primera ejecución descarga el modelo de embeddings (~470 MB) e indexa el
corpus. Las siguientes arrancan directamente.

Paso a paso completo, con verificación y errores típicos:
**[docs/instalacion.md](docs/instalacion.md)**.

| Comando | Qué hace |
|---|---|
| `python run_hacu.py` | Consola limpia, diagnóstico al log |
| `python run_hacu.py --debug` | Router, perfil activo, latencia y migraciones en pantalla |
| `python run_hacu.py --ui` | Ventana de exhibición, sin voz |
| `python run_hacu.py --voz-salida` | Consola: escribes tú, HACU responde en voz alta |
| `python run_hacu.py --ui --voz` | Ventana con micrófono y altavoz |
| `python run_hacu.py --ui --voz --pantalla-completa` | Lo que se proyecta el día de la exhibición |
| `python -m hacu.voz` | Diagnóstico de audio: qué micrófono y qué altavoz |
| `python -m pruebas.test_unidades` | 742 comprobaciones, ~2 s, sin GPU |
| `python -m herramientas.limpiar` | Qué sobra en la carpeta de trabajo |
| `.\iniciar_robot.ps1` | Lo mismo dentro de Docker con GPU |

---

## Documentación

| | |
|---|---|
| **[Instalación](docs/instalacion.md)** | Desde cero: CUDA, entorno, modelo, voz, verificación |
| **[GitHub](docs/github.md)** | Publicar el repositorio y replicar el proyecto en otra máquina |
| **[Docker](docs/docker.md)** | Construir la imagen, ejecutar con GPU, replicar en contenedor |
| **[Arquitectura](docs/arquitectura.md)** | Cómo está montado por dentro y por qué |
| **[Configuración](docs/configuracion.md)** | Todas las variables de entorno y campos de `config.py` |
| **[Corpus](docs/corpus.md)** | Qué sabe HACU, cómo se genera y cómo se actualiza |
| **[Voz](docs/voz.md)** | Micrófono, reconocimiento, síntesis, pronunciación, cambio de hablante |
| **[Interfaz](docs/interfaz.md)** | La ventana de exhibición y el panel del operador |
| **[Pruebas](docs/pruebas.md)** | Las cinco suites y cuándo se corre cada una |
| **[Operación](docs/operacion.md)** | Guion del día de la exhibición |
| **[Datos](docs/datos.md)** | Qué se guarda de los visitantes y quién decide |

Todo lo anterior, en un solo manual con portada e índice:
**[docs/HACU-documentacion.pdf](docs/HACU-documentacion.pdf)** (~71 páginas). Se
regenera desde `docs/` con [build/README.md](build/README.md).

Referencias generadas: `PRUEBAS.md` (las 60 entradas de la batería) y `GUION.md`
(los 100 turnos de la visita completa).

---

## Qué tiene de particular

**Cuatro niveles de profundidad.** Con 32 proyectos, «¿cuáles tienen?»,
«explícame cada uno» y «cuéntame todo sobre Mary» necesitan cosas distintas. La
pregunta decide cuánto se recupera, y el índice-catálogo se carga entero en vez de
buscarse — porque buscarlo devolvía 5 proyectos de 32.

**No suelta el hilo.** Un «¿y eso?» hereda el dominio del turno anterior, y la
última respuesta viaja recortada como ancla. Una pregunta sobre HACU mismo no
hereda: si no, una pregunta personal arrastraría documentación de un proyecto.

**Capas deterministas después del modelo.** Coletillas, adulación y fugas del
andamiaje se recortan en Python, no se le piden al modelo. Lo que se lee, lo que
se oye y lo que se guarda son exactamente el mismo texto.

**Se calla cuando le dicen que se calle.** El audio se entrega a la tarjeta en
trozos de 50 ms: medido, 10 ms hasta detenerse. Quien interrumpe no espera a que
termine la frase.

**Nota cuándo se acerca otra persona, sin identificar a nadie.** La referencia de
timbre vive en memoria durante la visita y se borra al cambiar de visitante. No
hay huella de voz en disco, y por tanto no hay base biométrica que custodiar.

**Cada umbral sale de una medida.** No hay un solo número en `config.py` que sea
una suposición; al lado de cada uno está qué se midió para elegirlo.

---

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
  prompts.py           system prompt, saludo y prompts de utilidad
  llm.py               único acceso a llama.cpp, serializado
  estilo.py            recorte determinista del cierre de turno
  extractor.py         memoria episódica en segundo plano
  sanitizer.py         qué merece guardarse como hecho
  memory.py            persistencia SQLite
  config.py            toda la configuración, con su justificación
  voz/                 micrófono, reconocimiento, síntesis, pronunciación
  interfaz/            la ventana de exhibición
documents/             corpus en dos niveles (índice, fichas, institucional)
herramientas/          generación del corpus y limpieza del árbol
pruebas/               suite de regresión y herramientas de medición
docs/                  esta documentación
```

---

## Limitaciones conocidas

- El reconocimiento está medido de extremo a extremo con `--autoprueba` (4/4
  nombres propios, ~1,9 s por frase en CPU). Lo que **no** se ha probado es la
  captura por micrófono real: nivel de entrada, ruido de sala y recorte de la
  primera sílaba solo se ven con hardware delante.
- Con altavoz abierto, la escucha automática hace que HACU se oiga a sí mismo.
  Pulsar-para-hablar lo evita; unos auriculares o un micrófono direccional también.
- El router es léxico. Las preguntas de seguimiento sin palabra clave se rescatan
  por distancia semántica, pero una referencia muy vaga puede traer el fragmento
  equivocado.
- Los nombres compuestos se limitan a dos palabras.
- Una sola estación: hay un perfil activo a la vez.
- La ruta de Docker no está verificada sobre la RTX 50 de esta máquina; la
  comprobación que falta está en [docs/docker.md](docs/docker.md#5-verificar-que-el-contenedor-usa-de-verdad-la-gpu).

---

## Requisitos

- GPU NVIDIA con CUDA y ≥ 8 GB de VRAM (desarrollado sobre una RTX 5070 Ti Laptop, 12 GB)
- Python 3.10 – 3.12
- ~12 GB de disco
- `models/Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf`

Corre sin GPU, pero en CPU un turno pasa de segundos a minutos: no sirve para una
exhibición.
