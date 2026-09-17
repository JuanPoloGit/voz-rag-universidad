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

| Capa | Qué hace | Por qué así |
|---|---|---|
| `FROM nvidia/cuda:12.1.1-devel-ubuntu22.04` | Base con CUDA y compilador | `devel`, no `runtime`: hace falta `nvcc` para llama.cpp |
| Python 3.10 + `build-essential`, `cmake`, `git` | Toolchain | Compilar llama.cpp si no hay rueda |
| `COPY requirements.txt` → `pip install` | Dependencias del núcleo | Capa propia, **antes** del código: cambiar una línea de Python no reinstala ChromaDB |
| Descarga de `paraphrase-multilingual-MiniLM-L12-v2` | Embeddings en `/opt/hf-cache` | Sin esto, cada `docker run --rm` sin volumen de caché bajaba ~470 MB |
| `pip install llama-cpp-python --extra-index-url .../cu121` | Motor del modelo | Se instala aparte de `requirements.txt` porque depende de CUDA |
| `COPY . .` | El código | Última capa: es lo que más cambia |
| `CMD ["python", "run_hacu.py"]` | Consola de HACU | — |

`.dockerignore` deja fuera `models/`, `chroma_db/`, `logs/`, `hacu_memory.db*` y
los informes de pruebas. La imagen pesa lo que pesan las dependencias, no lo que
pesan los datos: el modelo se monta en tiempo de ejecución.

---

## 3. Construir

```powershell
cd D:\Proyectos\voz-rag-universidad
docker build -t robot-expositor .
```

La primera construcción tarda: descarga la base de CUDA (varios GB), instala
`sentence-transformers` con PyTorch y baja el modelo de embeddings. Las
siguientes reutilizan las capas y, si solo cambió código Python, terminan en
segundos.

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
quieres persistente, añade `-v "$PWD/hacu_memory.db:/app/hacu_memory.db"` y lee
[datos.md](datos.md) antes.

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

### ⚠️ Si tu GPU es de la serie RTX 50 (Blackwell)

La imagen se construye sobre **CUDA 12.1**, y las ruedas que instala son de
`cu121`. Esa versión de CUDA es **anterior** a la arquitectura de las RTX 50
(`sm_120`), así que el código nativo para tu tarjeta no está en la rueda. Puede
salir adelante compilando PTX sobre la marcha en el primer arranque, y puede no
salir. **Compruébalo con la orden de arriba antes de confiar en el contenedor
para una exhibición.**

Si sale `False`, o si el primer turno tarda minutos, sube las dos versiones —son
dos líneas del `Dockerfile`:

```dockerfile
FROM nvidia/cuda:12.8.1-devel-ubuntu22.04
...
RUN pip install --no-cache-dir llama-cpp-python --prefer-binary \
    --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu128
```

Elige la serie (`cu124`, `cu126`, `cu128`…) que soporte tu controlador —la
versión de CUDA que sale arriba a la derecha en `nvidia-smi`— y que exista en
<https://abetlen.github.io/llama-cpp-python/whl/>. Si no hay rueda para esa
combinación, quita el `--extra-index-url` y deja que compile: la imagen ya trae
`cmake` y `build-essential`, y `CMAKE_ARGS="-DGGML_CUDA=on"` ya está puesto. La
construcción pasa de minutos a media hora, pero genera código para tu tarjeta.

> Esta ruta de Docker **no está verificada sobre la RTX 5070 Ti de esta máquina**:
> el desarrollo y todas las medidas del proyecto se hicieron sobre la instalación
> nativa de Windows. La comprobación de arriba es justo la que falta.

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
cualquier fallo posterior es del modelo o de la GPU, no del código. Serán **365
comprobaciones y no 383**: el bloque de la interfaz se omite solo, porque la imagen
no lleva PySide6 a propósito.

---

## 7. Cuando algo falla

| Síntoma | Causa | Solución |
|---|---|---|
| `could not select device driver "" with capabilities: [[gpu]]` | Falta NVIDIA Container Toolkit (Linux) o el backend WSL 2 (Windows) | Sección 1 |
| `manifest for nvidia/cuda:12.1.1-devel-ubuntu22.04 not found` | Ese tag ya no se publica | Buscar uno vigente en <https://hub.docker.com/r/nvidia/cuda/tags> y cambiar el `FROM` |
| El arranque avisa `No se detecto GPU NVIDIA` | Falta `--gpus all` | Añadirlo |
| `No such file or directory: '/app/models/Meta-Llama-...gguf'` | El modelo no está montado (`.dockerignore` lo excluye a propósito) | Montar `models/` |
| `❌ Falta la interfaz grafica` | La imagen no lleva PySide6, y es correcto | La ventana va en nativo |
| Va lentísimo dentro y rápido fuera | El modelo está en CPU | Sección 5 |
| La construcción tarda muchísimo en `sentence-transformers` | Está bajando PyTorch (~2 GB) | Normal la primera vez; después se cachea |
| `chroma_db` da errores raros tras usarlo desde Windows y desde el contenedor | Versiones distintas de ChromaDB sobre el mismo índice | `python -m herramientas.limpiar --aplicar --indice` y dejar que se reconstruya |

El log del contenedor queda en `logs/hacu.log` del anfitrión si `logs/` está
montado.
