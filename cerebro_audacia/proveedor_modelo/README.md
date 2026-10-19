# CEREBRO AUDACIA — PROVEEDOR DE MODELOS DE INFERENCIA LOCAL (LLM)

## 1. Propósito
Centralizar y gestionar el servicio daemon de inferencia local del Modelo de Lenguaje Principal (LLM) para los agentes de AudacIA, ejecutado de forma nativa e independiente en la GPU del servidor mediante `llama-server` (OpenAI-compatible HTTP en `http://127.0.0.1:8080`).

---

## 2. Perfiles de Hardware Soportados

| Parámetro | Perfil Desarrollo (`desarrollo_rtx2000ada`) | Perfil Servidor Final (`servidor_rtx5070ti`) |
| :--- | :--- | :--- |
| **GPU Objetivo** | NVIDIA RTX 2000 Ada Laptop (8,188 MiB VRAM) | NVIDIA RTX 5070 Ti (16,384 MiB VRAM) |
| **Modelo Principal** | Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf | Meta-Llama-3.1-8B-Instruct-Q6_K.gguf |
| **Tamaño de Pesos** | ~4.58 GiB (~4.92 GB) | ~6.15 GiB (~6.60 GB) |
| **Contexto (`n_ctx`)** | 8,192 tokens | 16,384 tokens (extensible a 32,768) |
| **Capas a GPU (`n_gpu_layers`)** | 33 (todas las capas offloaded a CUDA) | 33 (todas las capas offloaded a CUDA) |
| **Flash Attention (`-fa`)** | `true` | `true` |
| **Caché KV a 8 bits (`kv_8bits`)** | `true` (requerido para no exceder 8 GB VRAM) | `false` (FP16 para máxima fidelidad) |
| **Concurrencia (`--parallel`)** | 1 (cola secuencial FIFO estricta) | 2 (slots paralelos) |
| **Presupuesto VRAM Estimado** | ~5.08 GiB (Margen libre: ~1.4 GB) | ~6.75 GiB (Margen libre: ~8.2 GB) |

---

## 3. Herramientas y Scripts Operativos

El directorio contiene la suite de scripts reproducibles para Windows PowerShell y Python:

* `config_llm.json`: Especificación formal declarativa de perfiles, límites de contexto y endpoints.
* `iniciar_llama_server.ps1`:
  - Previene instancias duplicadas verificando procesos y el puerto 8080.
  - Consulta telemetría de GPU (`nvidia-smi`) para validar VRAM libre.
  - Verifica la presencia del binario `llama-server.exe` y del archivo `.gguf`.
  - Inicia el proceso en segundo plano con redirección de logs (`llama_server.log`).
  - Realiza sondeo de salud contra `http://127.0.0.1:8080/health` hasta que el modelo está completamente listo en VRAM.
* `detener_llama_server.ps1`: Detención ordenada de procesos huérfanos, liberación del puerto 8080 y verificación de VRAM liberada.
* `comprobar_salud_llm.ps1`: Inspección rápida de disponibilidad (`/health`), modelos cargados (`/v1/models`) y estado de GPU.
* `consultar_logs_llm.ps1`: Visualización y seguimiento en tiempo real de `llama_server.log` y errores en `llama_server_err.log`.
* `validar_api_llm.py`: Cliente de validación determinista que prueba:
  1. Disponibilidad `/health`.
  2. Identificación de modelo `/v1/models`.
  3. Generación factual en español.
  4. Instrucción de múltiples pasos.
  5. Extracción estructurada JSON.

---

## 4. Requisitos Previos para la Puesta en Marcha Física

Para que `iniciar_llama_server.ps1` complete la carga en la máquina de desarrollo:

1. **Binario `llama-server.exe`:**
   - Descargar la versión oficial de llama.cpp para Windows CUDA (ej. `llama-b3900-bin-win-cuda-cu12.4-x64.zip`):
     https://github.com/ggerganov/llama.cpp/releases
   - Extraer los ejecutables y DLLs de CUDA en `cerebro_audacia/proveedor_modelo/bin/` (o en la carpeta `bin/` en la raíz).

2. **Modelo GGUF:**
   - Descargar el archivo `Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf` (~4.9 GB):
     ```powershell
     pip install huggingface-hub
     huggingface-cli download bartowski/Meta-Llama-3.1-8B-Instruct-GGUF Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf --local-dir models
     ```
