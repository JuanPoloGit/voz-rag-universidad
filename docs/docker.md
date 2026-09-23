# Docker: construir, ejecutar y replicar

El contenedor sirve para **reproducir el entorno del modelo sin pelearse con
CUDA en cada máquina**: la imagen trae la cadena de compilación, las dependencias
y los embeddings ya descargados. Es la vía cómoda para levantar HACU en un
servidor, en otra portátil o en la máquina de un compañero.

> **Lo que el contenedor NO da.** La imagen instala solo `requirements.txt`: no
> lleva PySide6 ni la capa de voz, y un contenedor no tiene ni pantalla ni
> tarjeta de sonido del anfitrión. Dentro de Docker, HACU es **consola y texto**.
> `run_hacu.py --ui` responde `❌ Falta la interfaz grafica` y termina. Para la
> exhibición —ventana, micrófono y altavoz— se usa la instalación nativa de
> [instalacion.md](instalacion.md). Esto no es una carencia que haya que
> arreglar: exponer audio y GPU a la vez desde un contenedor añade una capa de
> cosas que pueden fallar el día de la exhibición.

---

## 1. Antes de construir

| | Windows | Linux |
|---|---|---|
| Motor | Docker Desktop con backend **WSL 2** | Docker Engine |
| GPU | Controlador NVIDIA con soporte WSL-CUDA | **NVIDIA Container Toolkit** |
| Disco | ~15 GB para la imagen | ídem |

En Linux, sin el Container Toolkit `--gpus all` no existe:

```bash
sudo apt install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

Comprueba que Docker ve la tarjeta **antes** de construir nada:

```powershell
docker run --rm --gpus all nvidia/cuda:12.1.1-base-ubuntu22.04 nvidia-smi
```

Si eso no imprime la tabla de `nvidia-smi`, el problema es del anfitrión y no de
HACU. Arréglalo aquí; más adelante solo se vuelve más difícil de diagnosticar.

---

## 2. Qué hay dentro de la imagen

`Dockerfile`, de arriba abajo:

El `Dockerfile` tiene **dos etapas**: una compila `llama-cpp-python` contra CUDA y
la otra solo ejecuta. La imagen final no lleva `nvcc` ni el compilador de C++, y
eso son unos 3,5 GB menos.

**Etapa 1 — `constructor`** (`nvidia/cuda:12.8.2-devel-ubuntu22.04`)

| Capa | Qué hace | Por qué así |
|---|---|---|
| Python 3.10 + `build-essential`, `cmake`, `ninja-build` | Toolchain | Compilar llama.cpp |
| `pip wheel --no-binary llama-cpp-python` | Construye la rueda con `-DGGML_CUDA=on -DCMAKE_CUDA_ARCHITECTURES=$CUDA_ARCH` | `--no-binary` impide que pip se traiga la rueda de PyPI **sin CUDA**, que no da error: solo corre en CPU |

**Etapa 2 — final** (`nvidia/cuda:12.8.2-runtime-ubuntu22.04`)

| Capa | Qué hace | Por qué así |
|---|---|---|
| Python 3.10 + `libgomp1` | Runtime mínimo | Sin `nvcc`: no se compila nada aquí |
| `COPY requirements*.txt` → `pip install` | Dependencias del núcleo | Capa propia, **antes** del código: cambiar una línea de Python no reinstala ChromaDB |
| `CON_VOZ=1` → `libportaudio2`, `libsndfile1` + `requirements-voz.txt` | Capa de voz, opcional | `pip install sounddevice` instala el enlace, no PortAudio; sin la biblioteca del sistema el fallo sale al abrir el micrófono |
| `CON_UI=1` → libs de X + `requirements-ui.txt` | Ventana, opcional | Qt necesita media docena de bibliotecas que la imagen de CUDA no trae |
| `COPY --from=constructor` → `pip install` de la rueda | Motor del modelo | Ya compilado en la etapa 1 |
| Descarga de `paraphrase-multilingual-MiniLM-L12-v2` | Embeddings en `/opt/hf-cache` | Sin esto, cada `docker run --rm` sin volumen de caché bajaba ~470 MB |
| `COPY . .` | El código | Última capa: es lo que más cambia |
| `USER hacu` (uid 1000) | No corre como root | Lo que el contenedor escribe en un volumen montado salía con propietario `root` y después no había quien lo borrara |
| `CMD ["python", "run_hacu.py"]` | Consola de HACU | La ventana y la voz no cruzan la frontera del contenedor sin más trabajo |

`.dockerignore` deja fuera `models/`, `chroma_db/`, `logs/`, `hacu_memory.db*` y
los informes de pruebas. La imagen pesa lo que pesan las dependencias, no lo que
pesan los datos: el modelo se monta en tiempo de ejecución.

### Los cuatro `--build-arg`

| Argumento | Por defecto | Para qué |
|---|---|---|
| `CUDA_ARCH` | `120` | Arquitectura de la GPU: `120` Blackwell (RTX 50xx), `89` Ada (RTX 40xx), `86` Ampere (RTX 30xx) |
| `RUEDA_CUDA` | *(vacío)* | Con un valor (`cu125`, `cu124`…) baja la rueda ya compilada de ese índice en vez de compilar. **No sirve para Blackwell**: el índice llega hasta `cu125` |
| `CON_VOZ` | `0` | `1` instala micrófono, reconocimiento y síntesis |
| `CON_UI` | `0` | `1` instala PySide6 y las bibliotecas de X |

---

## 3. Construir

```powershell
cd D:\Proyectos\voz-rag-universidad
docker build -t robot-expositor .
```

Eso construye la imagen de consola para Blackwell (RTX 50xx), compilando
`llama-cpp-python` desde el fuente. **La primera construcción tarda entre 30 y 50
minutos**: descarga dos bases de CUDA (varios GB), compila llama.cpp para
`sm_120`, instala PyTorch y baja el modelo de embeddings. Las siguientes
reutilizan las capas y, si solo cambió código Python, terminan en segundos.

En una GPU anterior a Blackwell se salta la compilación entera y baja a unos diez
minutos:

```powershell
docker build --build-arg RUEDA_CUDA=cu125 --build-arg CUDA_ARCH=89 -t robot-expositor .
```

Con la capa de voz y la ventana dentro (lee antes la sección 4, porque en Windows
ni el audio ni la ventana salen del contenedor sin trabajo extra):

```powershell
docker build --build-arg CON_VOZ=1 --build-arg CON_UI=1 -t robot-expositor .
```

> El `Dockerfile` usa `RUN --mount=type=cache`, que necesita **BuildKit**. Docker
> Desktop lo trae activado; en un Docker antiguo de Linux, `DOCKER_BUILDKIT=1
> docker build ...`.

---

## 4. Ejecutar

Lo más corto es el script que ya está en el proyecto:

```powershell
.\iniciar_robot.ps1
```

Que es exactamente esto:

```powershell
docker run --gpus all -it --rm `
  -v "${PWD}:/app" `
  robot-expositor
```

| Parte | Para qué |
|---|---|
| `--gpus all` | Expone la GPU. Sin esto, HACU corre en CPU y un turno tarda minutos |
| `-it` | Terminal interactiva: HACU es una conversación, necesita entrada |
| `--rm` | Borra el contenedor al salir. El estado vive en el volumen, no en el contenedor |
| `-v "${PWD}:/app"` | Monta la carpeta del proyecto completa |

**Ese único montaje hace todo el trabajo**: dentro del contenedor, `/app` es la
carpeta del anfitrión, así que el modelo de `models/`, el corpus de `documents/`,
el índice `chroma_db/` y la memoria `hacu_memory.db` son los mismos archivos que
se ven en Windows. El índice que construye el contenedor queda en el disco del
anfitrión y no hay que reconstruirlo en el siguiente arranque.

En Linux el script equivalente es:

```bash
docker run --gpus all -it --rm -v "$PWD:/app" robot-expositor
```

Con variables de configuración ([configuracion.md](configuracion.md)):

```powershell
docker run --gpus all -it --rm -v "${PWD}:/app" `
  -e HACU_DEBUG=1 -e HACU_RETENCION=8 `
  robot-expositor
```

### Montar solo lo necesario

`-v "${PWD}:/app"` monta **todo**, incluido el código: lo que corre es el código
del anfitrión, no el que se copió en la imagen. Va muy bien mientras se
desarrolla —se edita en Windows y se ejecuta dentro sin reconstruir— pero
significa que la imagen no es autosuficiente. Para desplegar de verdad, monta
solo los datos y deja que el código venga de la imagen:

```bash
docker run --gpus all -it --rm \
  -v "$PWD/models:/app/models:ro" \
  -v "$PWD/documents:/app/documents:ro" \
  -v "$PWD/chroma_db:/app/chroma_db" \
  -v "$PWD/logs:/app/logs" \
  robot-expositor
```

`hacu_memory.db` queda deliberadamente fuera: sin él, cada arranque empieza sin
memoria de visitantes, que es lo correcto en una demostración pública. Si la
quieres persistente, monta una carpeta en `/app/datos` y apunta ahí la memoria con
`HACU_DB` —montar un fichero suelto que todavía no existe hace que Docker cree un
directorio con ese nombre, y entonces SQLite no abre nada—:

```bash
docker run --gpus all -it --rm \
  -v "$PWD/models:/app/models:ro" \
  -v "$PWD/datos:/app/datos" \
  -e HACU_DB=/app/datos/hacu_memory.db \
  robot-expositor
```

Lee [datos.md](datos.md) antes: eso guarda en disco lo que dicen los visitantes.

### Con Compose

Si prefieres no recordar la orden larga, un `docker-compose.yml` como este hace
lo mismo (no está en el repositorio; créalo si lo quieres):

```yaml
services:
  hacu:
    build: .
    image: robot-expositor
    stdin_open: true
    tty: true
    volumes:
      - ./models:/app/models:ro
      - ./documents:/app/documents:ro
      - ./chroma_db:/app/chroma_db
      - ./logs:/app/logs
    environment:
      HACU_DEBUG: "1"
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
```

```powershell
docker compose run --rm hacu
```

---

## 5. Verificar que el contenedor usa de verdad la GPU

Que `--gpus all` no proteste no significa que el modelo esté en la tarjeta.

```powershell
docker run --rm --gpus all robot-expositor `
  python -c "from llama_cpp import llama_supports_gpu_offload as g; print('GPU:', g())"
```

Tiene que imprimir `GPU: True`. Y con HACU arrancado, `nvidia-smi` en el
anfitrión debe mostrar ~5 GB ocupados y el precalentamiento debe tardar segundos.

### Por qué la imagen compila en vez de bajar una rueda

La portátil de la exhibición lleva una RTX 5070 Ti: **Blackwell, `sm_120`**. Los
hechos, comprobados uno a uno:

- El índice de ruedas de `llama-cpp-python` publica hasta **`cu125`**
  (`cu126` y `cu128` devuelven 404). Comprobado el 21/09/2026 contra
  <https://abetlen.github.io/llama-cpp-python/whl/>.
- CUDA 12.5 **no conoce `sm_120`**. No solo le falta el código nativo: tampoco
  genera PTX que el controlador pueda traducir al vuelo, porque esa arquitectura
  no existía cuando se publicó.

Conclusión: **para una RTX 50 no hay rueda que valga**. Hay que compilar contra
CUDA 12.8, que es lo que hace la etapa 1 del `Dockerfile` por defecto. De ahí los
30–50 minutos de la primera construcción.

Para tarjetas anteriores la rueda sí sirve y se ahorra todo eso:

```powershell
docker build --build-arg RUEDA_CUDA=cu125 --build-arg CUDA_ARCH=89 -t robot-expositor .
```

> La ruta de Docker **sigue sin verificarse sobre la RTX 5070 Ti de esta
> máquina**: el desarrollo y todas las medidas del proyecto se hicieron sobre la
> instalación nativa de Windows. Lo que cambió es que la imagen ya no está
> construida sobre una versión de CUDA que se sabe incompatible. La comprobación
> de `GPU: True` de arriba sigue siendo la que falta.

---

## 6. Replicar el proyecto entero con Docker

Desde una máquina que solo tiene Docker y el controlador NVIDIA:

```bash
# 1. El código
git clone https://github.com/<usuario>/voz-rag-universidad.git
cd voz-rag-universidad

# 2. El modelo (~4,9 GB; no viene en el repositorio ni en la imagen)
mkdir -p models
#    Ver instalacion.md, paso 4
#    -> models/Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf

# 3. La imagen
docker build -t robot-expositor .

# 4. Comprobar que la GPU está dentro
docker run --rm --gpus all robot-expositor \
  python -c "from llama_cpp import llama_supports_gpu_offload as g; print('GPU:', g())"

# 5. Comprobar que el sistema está sano (sin GPU, ~2 s)
docker run --rm robot-expositor python -m pruebas.test_unidades

# 6. Arrancar
docker run --gpus all -it --rm -v "$PWD:/app" robot-expositor
```

El paso 5 es barato y vale mucho: si la suite pasa dentro del contenedor, el
enrutado, el estilo, la memoria y la profundidad de recuperación están intactos, y
cualquier fallo posterior es del modelo o de la GPU, no del código. Serán **710
comprobaciones y no 742**: el bloque de la interfaz se omite solo, porque la imagen
por defecto no lleva PySide6 a propósito (con `--build-arg CON_UI=1` pasan las 742).

---

## 7. Cuando algo falla

| Síntoma | Causa | Solución |
|---|---|---|
| `could not select device driver "" with capabilities: [[gpu]]` | Falta NVIDIA Container Toolkit (Linux) o el backend WSL 2 (Windows) | Sección 1 |
| `manifest for nvidia/cuda:12.8.2-...` not found | Ese tag ya no se publica | Buscar uno vigente en <https://hub.docker.com/r/nvidia/cuda/tags> y pasarlo con `--build-arg CUDA_VERSION=` |
| `the --mount option requires BuildKit` | Docker antiguo sin BuildKit | `DOCKER_BUILDKIT=1 docker build ...` |
| La construcción se queda media hora en `Building wheel for llama-cpp-python` | Está compilando para tu GPU | Es lo esperado en Blackwell. En otras tarjetas, `--build-arg RUEDA_CUDA=cu125` |
| `nvcc fatal: Unsupported gpu architecture 'compute_120'` | `CUDA_VERSION` anterior a 12.8 | Dejar el valor por defecto, o subirlo |
| `could not load the Qt platform plugin "xcb"` | Imagen construida sin `CON_UI=1`, o sin servidor X | Sección 2; la ventana va en nativo |
| `PortAudioError: Error querying device -1` | Imagen sin `CON_VOZ=1`, o sin tarjeta de sonido dentro del contenedor | El audio va en nativo |
| El arranque avisa `No se detecto GPU NVIDIA` | Falta `--gpus all` | Añadirlo |
| `No such file or directory: '/app/models/Meta-Llama-...gguf'` | El modelo no está montado (`.dockerignore` lo excluye a propósito) | Montar `models/` |
| `❌ Falta la interfaz grafica` | La imagen no lleva PySide6, y es correcto | La ventana va en nativo |
| Va lentísimo dentro y rápido fuera | El modelo está en CPU | Sección 5 |
| La construcción tarda muchísimo en `sentence-transformers` | Está bajando PyTorch (~2 GB) | Normal la primera vez; después se cachea |
| `chroma_db` da errores raros tras usarlo desde Windows y desde el contenedor | Versiones distintas de ChromaDB sobre el mismo índice | `python -m herramientas.limpiar --aplicar --indice` y dejar que se reconstruya |

El log del contenedor queda en `logs/hacu.log` del anfitrión si `logs/` está
montado.
