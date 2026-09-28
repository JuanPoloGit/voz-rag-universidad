# Instalación desde cero

Levantar HACU en una máquina que no lo ha visto nunca. Al final de esta página el
sistema arranca, responde y se le puede hablar.

Si lo que buscas es **replicar el proyecto desde el repositorio**, empieza por
[github.md](github.md) y vuelve aquí en el paso «Entorno de Python». Si prefieres
el contenedor, ve a [docker.md](docker.md).

---

## 1. Lo que hace falta antes de empezar

| | Mínimo | Con lo que está desarrollado |
|---|---|---|
| GPU | NVIDIA con ≥ 8 GB de VRAM | RTX 5070 Ti Laptop, 12 GB |
| Controlador | El que soporte tu CUDA | — |
| RAM | 16 GB | 32 GB |
| Disco | ~12 GB libres | — |
| Python | 3.10 – 3.12 | 3.11 |
| Sistema | Windows 10/11 o Linux | Windows 11 |

HACU corre **sin GPU**, pero en CPU un turno pasa de segundos a minutos: no sirve
para una exhibición. El arranque lo dice en la primera línea (`nvidia-smi`).

Comprueba el controlador antes de nada:

```powershell
nvidia-smi
```

La esquina superior derecha de esa tabla dice la **versión de CUDA que soporta tu
controlador**. Apúntala: decide qué rueda de `llama-cpp-python` instalar en el
paso 3.

### Si la máquina no tiene Python

```powershell
python --version        # Linux/macOS: python3 --version
```

Si eso falla o marca 2.x, hay que instalarlo antes de seguir.

**Windows** — con `winget` (viene ya en Windows 10/11):

```powershell
winget install Python.Python.3.11
```

Cierra y vuelve a abrir la terminal después: `winget` no actualiza el PATH de una
sesión ya abierta, así que `python` sigue "sin reconocerse" aunque la instalación
haya ido bien. Si en vez de esto se instala a mano desde
<https://www.python.org/downloads>, hay que marcar la casilla **"Add python.exe
to PATH"** del instalador — sin eso pasa lo mismo.

**Linux (Debian/Ubuntu):**

```bash
sudo apt update && sudo apt install python3.11 python3.11-venv python3-pip
```

No instales la version mas nueva "porque si": PySide6 y PyTorch (este ultimo
detras de `sentence-transformers`) tardan en publicar rueda para un Python
recien salido, y sin rueda `pip` intenta compilar desde el fuente y falla a
medio instalar. Quedate en el rango de la tabla de arriba (3.10-3.12; con 3.11
es con lo que esta desarrollado) hasta confirmar que las demas dependencias ya
soportan una version mas nueva.

---

## 2. Entorno de Python

```powershell
git clone <URL-DEL-REPOSITORIO> voz-rag-universidad
cd voz-rag-universidad

python -m venv venv
.\venv\Scripts\Activate.ps1          # Linux/macOS: source venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Si PowerShell se niega a ejecutar el script de activación:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

El entorno hay que activarlo **en cada terminal nueva**, no solo la primera vez.
Cuando está activo, el prompt lleva `(venv)` delante; si no lo lleva, `python` es
el del sistema y el arranque falla con `ModuleNotFoundError` en el primer import
que no sea de la biblioteca estándar.

`requirements.txt` instala el núcleo: ChromaDB, los embeddings multilingües y el
troceador de texto. **No** instala `llama-cpp-python` con CUDA — eso es el paso
siguiente, porque depende de tu tarjeta.

### Alternativa con Anaconda Navigator

En laboratorios de universidad es habitual que el equipo traiga Anaconda
preinstalado y no dé permisos para instalar Python aparte — si es tu caso, esto
reemplaza el `venv` de arriba, no se hace ademas de.

**Por la interfaz:**

1. Pestaña **Environments** → **Create** → nombre `hacu`, Python **3.11** → Create.
   (Si Anaconda no está siquiera instalado, hace falta que alguien con permisos
   en esa máquina lo instale primero — Navigator no se instala a sí mismo.)
2. En la fila de `hacu`, la flecha ▶ → **Open Terminal**. Abre una consola con
   ese entorno ya activo: no hace falta `conda activate` a mano ni recordar
   activarlo en cada terminal nueva, como sí pasa con `venv`.
3. Desde esa terminal, exactamente los mismos comandos de siempre:

```powershell
cd D:\ruta\a\voz-rag-universidad
python -m pip install --upgrade pip
pip install -r requirements.txt
```

**Por línea de órdenes** (Anaconda Prompt, si Navigator no está a mano):

```powershell
conda create -n hacu python=3.11 pip -y
conda activate hacu
cd D:\ruta\a\voz-rag-universidad
pip install -r requirements.txt
```

Todo lo que sigue en esta página —`llama-cpp-python`, `requirements-voz.txt`,
`requirements-ui.txt`, `python run_hacu.py`— es idéntico: `pip` funciona igual
dentro de un entorno de conda que dentro de un `venv`. La única regla real es
**no instalar nada de esto en el entorno `base`**: ese lo comparten todos los
proyectos que usen esa instalación de Anaconda, y una versión concreta que pide
HACU (de ChromaDB, de PyTorch...) puede chocar con lo que ya tenga otra cosa.

Si `git` tampoco está reconocido en esa terminal: `conda install -c anaconda git
-y` lo agrega al entorno, o, si es solo para bajar el código una vez y no hace
falta actualizarlo después con `git pull`, el botón verde **Code → Download
ZIP** de GitHub evita instalar git del todo.

---

## 3. llama-cpp-python con CUDA

Hay dos caminos. El primero tarda un minuto; el segundo, entre diez y cuarenta.

### 3a. Rueda precompilada (empieza por aquí)

```powershell
pip install llama-cpp-python `
  --prefer-binary `
  --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu121
```

Cambia `cu121` por la serie que corresponda a tu CUDA (`cu122`, `cu124`, `cu125`…).
**Si tu GPU es de la serie RTX 50 (Blackwell), `cu121` puede no traer código
nativo para tu arquitectura**: funcionará compilando sobre la marcha, o no
funcionará en absoluto. Verifica con el paso 6 antes de darlo por bueno.

### 3b. Compilar en la máquina

Hace falta un compilador de C++ y CMake (en Windows, las *Build Tools for Visual
Studio* con la carga de trabajo «Desarrollo para escritorio con C++»).

```powershell
$env:CMAKE_ARGS="-DGGML_CUDA=on"
$env:FORCE_CMAKE="1"
pip install llama-cpp-python --no-cache-dir
```

```bash
CMAKE_ARGS="-DGGML_CUDA=on" FORCE_CMAKE=1 pip install llama-cpp-python --no-cache-dir
```

Compilar es más lento pero genera código para **tu** tarjeta, así que es la salida
cuando ninguna rueda encaja.

---

## 4. El modelo

HACU espera este archivo exacto:

```
models/Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf
```

Son ~4,9 GB. **No está en el repositorio** y no debe estarlo (ver
[github.md](github.md#qué-no-entra-en-el-repositorio-y-por-qué)). Se descarga de
Hugging Face, de cualquiera de los repositorios que publican la cuantización
`Q4_K_M` de `Meta-Llama-3.1-8B-Instruct`:

```powershell
pip install huggingface-hub
huggingface-cli download <repo-de-hugging-face> Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf `
  --local-dir models
```

> El nombre del repositorio de Hugging Face depende de quién publique la
> cuantización y cambia con el tiempo; búscalo en
> <https://huggingface.co/models?search=Meta-Llama-3.1-8B-Instruct-GGUF> y
> comprueba que el archivo pesa ~4,9 GB. Llama 3.1 exige aceptar su licencia y
> `huggingface-cli login` antes de descargar.

Otro modelo GGUF sirve sin tocar código:

```powershell
$env:HACU_MODELO="otro-modelo.gguf"     # relativo a models/, o ruta absoluta
$env:HACU_CTX="8192"                    # un modelo mayor deja menos VRAM
```

---

## 5. Extras opcionales: ventana y voz

Los dos son **opcionales a propósito**. El núcleo tiene que poder arrancar en una
máquina sin tarjeta de sonido y sin entorno gráfico.

```powershell
pip install -r requirements-ui.txt      # ventana de exhibición (PySide6)
pip install -r requirements-voz.txt     # micrófono, reconocimiento y síntesis
python -m hacu.voz --descargar          # voz de Piper, ~110 MB
```

En Linux, `sounddevice` necesita además PortAudio del sistema:

```bash
sudo apt install portaudio19-dev
```

**Comprueba que la capa de voz quedó entera**, porque instalada a medias no falla
al arrancar: degrada en escena.

```powershell
python -m hacu.voz
```

Lista micrófonos y altavoces, y además avisa de las dos ausencias que no impiden
arrancar pero se notan con público delante: la voz de Piper sin descargar (Piper
arranca un proceso por frase, ~2,5 s contra 0,2 s) y `resemblyzer` sin instalar
(HACU no nota cuándo se acerca otra persona).

Detalle completo de la capa de voz en [voz.md](voz.md).

---

## 6. Primer arranque y verificación

```powershell
python run_hacu.py --debug
```

La primera vez descarga el modelo de embeddings (~470 MB) e indexa el corpus:
tarda un par de minutos. Las siguientes arrancan directamente.

**Solo el primer arranque necesita conexión.** Una vez descargados, el embedding
del RAG y el modelo de reconocimiento se abren desde la caché local y HACU no
vuelve a consultar la red: la sala de la exhibición puede no tener Wi-Fi. Si
alguna vez ves que el arranque se queda esperando a `huggingface.co`, es que algo
no está en la caché — no que el sistema necesite internet para funcionar.

Lo que tiene que aparecer, en este orden:

```
--- DIAGNOSTICO DE HARDWARE ---
✅ GPU detectada: ...
🔧 Cargando el modelo en la VRAM...
🔧 Sincronizando corpus institucional...
🔧 Precalentando el modelo...
✅ Sistema listo (precalentado en N.Ns).

--- HACU ---
Hola, soy Hacu. Bienvenido a AudacIA. ¿Serías tan amable de decirme cuál es tu nombre?
```

**Verifica que el modelo está de verdad en la GPU**, no solo que la GPU existe:

```powershell
python -c "from llama_cpp import llama_supports_gpu_offload as g; print('GPU:', g())"
```

Si imprime `False`, `llama-cpp-python` se instaló sin CUDA: vuelve al paso 3 y usa
3b. Con el sistema arrancado, `nvidia-smi` debe mostrar ~5 GB ocupados y el
precalentamiento debe tardar **segundos, no minutos**.

Lista de verificación completa:

```powershell
python -m pruebas.test_unidades     # 742 comprobaciones, ~2 s, sin GPU
python -m pruebas.recuperacion      # calidad del RAG sobre el corpus real
python -m hacu.voz                  # inventario de micrófonos y altavoces
python -m hacu.voz --autoprueba     # circuito de voz completo sin micrófono
```

`test_unidades` en verde y `recuperacion` en 39/39 significan que el sistema está
sano. Ver [pruebas.md](pruebas.md).

---

## 7. Arrancar como se arranca el día de la exhibición

```powershell
python run_hacu.py --ui --voz --pantalla-completa
```

| Comando | Qué hace |
|---|---|
| `python run_hacu.py` | Consola limpia, diagnóstico al log |
| `python run_hacu.py --debug` | Router, perfil, latencia y migraciones en pantalla |
| `python run_hacu.py --ui` | Ventana de exhibición, sin voz |
| `python run_hacu.py --voz-salida` | Consola: escribes tú, HACU responde en voz alta |
| `python run_hacu.py --ui --voz` | Ventana con micrófono y altavoz |
| `python run_hacu.py --ui --voz --pantalla-completa` | Lo que se proyecta |
| `python -m hacu.voz` | Diagnóstico de audio |

---

## 8. Cuando algo falla

| Síntoma | Causa | Qué hacer |
|---|---|---|
| `ModuleNotFoundError: No module named '...'` | Estás usando el Python del sistema, no el del entorno | Activar el entorno. Lo delata el prompt: sin `(venv)` delante, es el otro Python |
| `No se detecto GPU NVIDIA` | `nvidia-smi` no está en el PATH | Reinstalar el controlador |
| Arranca pero va lentísimo | `llama-cpp-python` sin CUDA | Paso 3b |
| `ConfiguracionInviable: El prompt estimado...` | El presupuesto no cabe en `n_ctx` | Bajar `HACU_FRAGMENTOS`, o subir `HACU_CTX` |
| `ConfiguracionInviable: El embedding multilingue no esta disponible` | Falta `sentence-transformers`, o no está descargado y no hay conexión | Reinstalarlo; con red, el primer arranque lo baja. `HACU_MULTILINGUE=0` acepta el modo degradado **a sabiendas** |
| `getaddrinfo failed` hacia `huggingface.co` en el arranque | Sin DNS, y el modelo no está en la caché | Conectar una vez para descargarlo; después ya no hace falta |
| `Falta la interfaz grafica` | Sin PySide6 | `pip install -r requirements-ui.txt` |
| `🎤 Oido: NO disponible` | Sin micrófono o sin `sounddevice` | `python -m hacu.voz`; ver [voz.md](voz.md) |
| Pausas larguísimas entre frases | La voz de Piper no está en `models/voz/`, así que arranca un proceso por frase (~2,5 s contra 0,2 s) | `python -m hacu.voz --descargar` |
| `Falta resemblyzer para distinguir voces` | La capa de voz está instalada a medias | `pip install -r requirements-voz.txt`. Si `webrtcvad` no compila en Windows, arranca con `HACU_HABLANTES=0` y sigue sin esa función |
| `CUDA out of memory` al cargar Whisper | El STT no cabe junto al Llama | `HACU_STT=tiny`, o `--voz-salida` |

El log completo queda en `logs/hacu.log`.
