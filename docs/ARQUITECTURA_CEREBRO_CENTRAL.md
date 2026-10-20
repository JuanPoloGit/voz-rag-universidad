# AudacIA — Arquitectura y Gobierno del Cerebro Central

**Documento Normativo y de Diseño:** Especificación formal de la infraestructura central de lenguaje y razonamiento de la plataforma AudacIA.

---

## 1. Principio y Decisión Arquitectónica Fundamental

> **AudacIA opera con un único LLM centralizado ("Cerebro Central").**  
> Todos los agentes especializados del ecosistema —HAKU Inventario, HACU Visitante y cualquier agente futuro— utilizan **exclusivamente el Cerebro Central** como proveedor de lenguaje y capacidades de razonamiento.

```text
                         AUDACIA
                            │
                            ▼
               ┌─────────────────────────┐
               │     CEREBRO CENTRAL     │
               │                         │
               │   Único LLM Compartido  │
               │   llama-server.exe      │
               │   Gobierno de VRAM      │
               │   API Local (Port 8080) │
               │   Control Concurrencia  │
               └────────────┬────────────┘
                            │
          ┌─────────────────┼─────────────────┐
          │                 │                 │
          ▼                 ▼                 ▼
        HAKU              HACU          AGENTE FUTURO
     Inventario         Visitante       Especializado
          │                 │                 │
      herramientas       RAG              herramientas
      BD inventario       memoria         reglas
      reglas negocio      voz             memoria
      seguridad 2P        interfaz stand  etc.
```

### 1.1. Regla Innegociable de Escalabilidad
- **Escalabilidad de agentes, no de modelos:** AudacIA escala agregando agentes especializados en código Python que interactúan con herramientas, memorias y reglas de negocio, compartiendo una única instancia del modelo en memoria de video.
- **Prohibición de modelos por agente:** Queda expresamente fuera de la arquitectura soportada cualquier diseño donde los agentes carguen, administren o inicien modelos de lenguaje locales independientes.
- **Diferenciación entre tareas deterministas y de lenguaje:** Esta regla no obliga a pasar todas las tareas por el LLM. Las consultas a bases de datos, validaciones de permisos, reglas de negocio y cálculos operan de forma determinista mediante funciones Python. Un agente puede ser 100% determinista (como HAKU Inventario en su estado actual, con cero llamadas a LLMs) o híbrido.
- **Componentes especializados exentos:** La prohibición se refiere estrictamente a modelos de lenguaje (LLM). Componentes especializados como Whisper STT (transcripción) o modelos de visión/OCR mantienen sus propios recursos bajo el presupuesto aprobado.

### 1.2. Organización Futura de Agentes vs. Estructura Actual
Posterior a la finalización y estabilización definitiva de HAKU Inventario, se contempla migrar hacia una organización unificada de agentes:
```text
agentes/
├── haku_inventario/
├── hacu_visitante/
└── agente_futuro/
```
> [!IMPORTANT]
> **Aclaración sobre la estructura actual:** El esquema anterior representa una **propuesta de organización futura**, no la distribución activa del repositorio. Actualmente los agentes residen en `sistema_inventario/integraciones/haku/`, `hacu/` y `cerebro_audacia/agentes/`. **No deben moverse carpetas ni alterarse rutas de importación en este momento.** Pertenecer a la futura carpeta `agentes/` tampoco implicará que el agente deba requerir un LLM.

---

## 2. El Cerebro Central como Infraestructura

El Cerebro Central no es simplemente un ejecutable en segundo plano; es una **capa de infraestructura de servicio** que desacopla a los agentes de los detalles físicos del hardware y del motor de inferencia.

Es fundamental distinguir entre las **capacidades nativas del motor** (`llama-server.exe`) y los **mecanismos de la capa de aplicación de AudacIA** (`cerebro_audacia.conexion`):

```text
┌─────────────────────────────────────────────────────────────────┐
│                     CEREBRO CENTRAL AUDACIA                     │
│                                                                 │
│   [ Capa de Aplicación AudacIA (cerebro_audacia.conexion) ]     │
│        • Cliente oficial único (ClienteCerebroCentral)         │
│        • Gobernador de concurrencia y cola FIFO (LlmGovernor)  │
│        • Timeouts de conexión (5s) y espera en cola (10s)       │
│        • Circuit Breaker ante caídas persistentes (3 fallos)   │
│        • Bloqueo estricto de parámetros de modelo en agentes   │
│                                                                 │
│   [ Capa Nativa del Motor de Inferencia (llama-server.exe) ]    │
│        • Endpoint HTTP local compatible con OpenAI (:8080)       │
│        • Ranuras de inferencia paralelas (--parallel 1 / 2)    │
│        • Sondeo de salud nativo (/health)                       │
│        • Compilado con NVIDIA CUDA 12                           │
│        • Pesos únicos: Meta Llama 3.1 8B (Candidato: 12B)      │
│        • Endpoint /metrics: Pendiente de habilitación           │
└─────────────────────────────────────────────────────────────────┘
```

### 2.1. Ruta Operativa Rápida: Iniciar, Comprobar y Consumir

El Cerebro Central funciona como infraestructura pura y **no requiere iniciar ni importar HAKU Inventario ni HACU Visitante**:

1. **Iniciar el Servidor de Inferencia (PowerShell):**
   ```powershell
   powershell -ExecutionPolicy Bypass -File cerebro_audacia\proveedor_modelo\iniciar_llama_server.ps1 -Perfil servidor_rtx5070ti
   ```
2. **Comprobar Disponibilidad:**
   - Script oficial:
     ```powershell
     powershell -ExecutionPolicy Bypass -File cerebro_audacia\proveedor_modelo\comprobar_salud_llm.ps1
     ```
   - HTTP directo: `curl http://127.0.0.1:8080/health` (debe retornar `{"status":"ok"}`).
3. **Consumo desde Python mediante el Cliente Oficial:**
   ```python
   from cerebro_audacia.conexion import obtener_cliente_cerebro_central

   # 1. Obtener instancia singleton configurada
   cliente = obtener_cliente_cerebro_central()

   # 2. Comprobar salud operativa y estado del Circuit Breaker
   estado = cliente.comprobar_salud()
   print("Disponibilidad:", estado["disponible"], "Circuit Breaker:", estado["circuit_breaker"])

   # 3. Solicitar inferencia respetando concurrencia y timeouts
   respuesta = cliente.generar(
       mensajes=[{"role": "user", "content": "¿Cuáles son las políticas de laboratorio?"}],
       max_tokens=256,
   )
   print("Respuesta:", respuesta.texto)
   print("Tokens generados:", respuesta.tokens_completados)
   ```
4. **Validación Automatizada de la Infraestructura (Sin Dependencias Externas):**
   ```powershell
   python -m unittest discover -s cerebro_audacia/pruebas -p "test_*.py"
   ```
   *(Ejecuta las 73 pruebas unitarias y de resiliencia en ~2.1 s sin requerir Django, SQLite ni servicios de stand).*

---

## 3. Presupuesto Físico y Gobierno de VRAM (Hardware RTX 5070 Ti)

### 3.1. Justificación del Hardware y Estimaciones de VRAM
El servidor central de AudacIA cuenta con:
- **GPU:** NVIDIA GeForce RTX 5070 Ti (16 GB de VRAM GDDR7 dedicados).
- **RAM:** 32 GB de RAM DDR5.
- **Host:** Windows 11 Pro en servidor institucional dedicado.

16 GB de VRAM es un presupuesto adecuado para un servidor de laboratorio, pero **finito y compartido**. Los consumos presentados en la tabla son **estimaciones de ingeniería sujetas a la configuración real** (cuantización, tamaño de contexto, caché KV, ranuras concurrentes y overhead del sistema operativo), no garantías fijas:

| Componente en VRAM | Consumo Estimado | Margen Restante | Notas Técnicas |
|---|:---:|:---:|---|
| **Llama 3.1 8B Q4_K_M (33 capas GPU)** | ~4.7 GB a ~5.8 GB | ~10.2 GB libres | Modelo base con contexto 8K. |
| **Candidato: Llama 12B Q4_K_M** | ~7.2 GB a ~8.9 GB | ~7.1 GB libres | Contexto 8K–16K (experimental). |
| **KV Cache (2 ranuras paralelas, 16K)** | ~1.0 GB a ~2.0 GB | Variable | Configurado con `kv_8bits: true` (q8_0). |
| **Whisper STT (Transcripción de voz)** | ~350 MB a ~1.2 GB | Coexistente en VRAM | Opera dentro del worker de Django. |
| **Margen de Seguridad / Sistema Operativo** | ~1.0 GB a ~1.5 GB | Reserva obligatoria | Pantalla, buffer de cómputo y SO. |

> [!WARNING]
> **Alcance real de `HAKU_GPU_LOCK`:**  
> `HAKU_GPU_LOCK` (definido en `sistema_inventario/integraciones/haku/services.py` como `threading.RLock()`) es un cerrojo exclusivo en memoria para sincronizar hilos concurrentes **dentro del proceso web de Django**. **No coordina ni sincroniza procesos externos como `llama-server.exe` ni `run_hacu.py`**. La coexistencia entre Whisper y el LLM no depende de un lock inter-proceso, sino del amplio margen libre de VRAM (~9-10 GB) en la RTX 5070 Ti.

Si cada agente intentara cargar un modelo de 8B o 12B propio, el consumo combinado superaría inmediatamente los 16 GB, provocando **CUDA Out Of Memory**, degradación por paginación en disco (*thrashing*) y colapso del sistema.

### 3.2. Concurrencia Finita y Tratamiento de Saturación
- **La capacidad no es ilimitada:** Contar con un único modelo no significa que el sistema pueda atender infinitas solicitudes simultáneas en 0 ms.
- **Ranuras Físicas:** El motor `llama-server.exe` opera con un número finito de ranuras concurrentes (`--parallel 1` en desarrollo, `--parallel 2` en servidor final).
- **Gobernanza de Cola y Saturación:**
  1. La cola de espera y el límite de 10 segundos están gobernados por la capa de aplicación (`LlmGovernor` en `cerebro_audacia.conexion`).
  2. Si la cola supera el límite máximo de espera o los slots están ocupados tras el timeout, el cliente rechaza con `LlmSaturadoError` o `LlmColaExpiradaError` para permitir que el agente responda con un mensaje controlado y no bloquee al usuario.
  3. **Estado de las prioridades entre agentes:** Actualmente las colas de la capa de aplicación operan en modo FIFO. La priorización diferenciada (ej. HAKU > HACU) es una capacidad clasificada como `PENDIENTE DE IMPLEMENTACIÓN`.

---

## 4. Selección del Modelo Central y Procedimiento de Migración

### 4.1. Estado Actual y Candidatos
- **Modelo Base Documentado y Operativo:** Meta Llama 3.1 8B Instruct (cuantización `Q4_K_M`, archivo `Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf`).
- **Candidato Experimental (12B):** Durante pruebas preliminares con HACU Visitante, un modelo de 12B mostró respuestas más completas y mejor vocabulario en español. Se mantiene como candidato sujeto a benchmarks reproducibles en el hardware final.
- **Independencia de Parámetros:** AudacIA **no está ligada contractualmente a 8B ni a 12B**. La arquitectura se define como *"Cerebro Central Único"*, y la elección de pesos específicos es una decisión de configuración de infraestructura.

### 4.2. Procedimiento Centralizado de Migración y Advertencia de Regresión
Cuando se evalúe promover un modelo superior (ej. 12B):
1. **Validación en Benchmark:** Pruebas rigurosas de latencia (P50/P95), consumo de VRAM con 2 ranuras y tool calling en ráfaga sobre la RTX 5070 Ti.
2. **Sustitución en el Cerebro Central:** Se actualizan la ruta del archivo `.gguf` y los parámetros de contexto en `cerebro_audacia/proveedor_modelo/config_llm.json`.
3. **Puntos de Entrada Intactos:** Los agentes no requieren reconfigurar endpoints, pues consumen la misma API HTTP local (`/v1/chat/completions`).
4. **Advertencia Obligatoria de Regresión:** Un cambio de modelo **no garantiza un comportamiento idéntico**. Antes de aprobar la sustitución, debe ejecutarse una batería completa de pruebas que descarte regresiones en:
   - Adherencia a esquemas JSON y llamadas a herramientas (*tool calling*).
   - Calidad y tono de las respuestas en español.
   - Latencia interactiva (tiempo al primer token $\le 2.5\text{ s}$).
   - Consumo de memoria VRAM y estabilidad térmica.

---

## 5. Prohibiciones Absolutas de Arquitectura

Para proteger la integridad del sistema, quedan prohibidas de forma explícita las siguientes prácticas:

1. **PROHIBIDO:** Cargar modelos de lenguaje `.gguf`, `.safetensors` o instanciar `Llama(...)` dentro del código de un agente.
2. **PROHIBIDO:** Levantar instancias secundarias de `llama-server.exe` u otros servidores de inferencia en puertos alternativos.
3. **PROHIBIDO:** Reservar VRAM de la GPU para modelos de lenguaje propios de un agente.
4. **PROHIBIDO:** Consumir proveedores de lenguaje externos (OpenAI, Anthropic, etc.) saltándose el Cerebro Central.
5. **PROHIBIDO:** Crear "zoológicos de LoRA" fragmentados donde cada agente administre adaptadores independientes sobre la GPU.
6. **PROHIBIDO:** Delegar autoridad de seguridad, permisos o transacciones de base de datos directamente al LLM. El LLM es un asesor lingüístico; la autorización y la integridad de datos residen en los servicios deterministas de negocio.

---

## 6. Política sobre Fine-Tuning y Adaptadores LoRA

- **Especialización Primaria por Software:** La especialización de los agentes en AudacIA se logra mediante:
  - Instrucciones del sistema y prompts de dominio.
  - Esquemas estrictos de herramientas (*JSON Schema / Tool Calling*).
  - Estado de sesión y memoria operacional local.
  - RAG documental (en agentes como HACU).
  - Lógica determinista de validación de negocio (en agentes como HAKU).
- **Fine-Tuning / LoRA Centralizado:** Si en el futuro se determina que el modelo general requiere especialización en vocabulario técnico o sintaxis institucional, se aplicará como una **actualización del modelo del Cerebro Central**, gobernada, evaluada y versionada centralmente.

---

## 7. Taxonomía del Estado de Implementación

Para asegurar total transparencia técnica entre lo que ya existe y lo que está proyectado:

| Componente | Estado Real | Descripción Técnica |
|---|:---:|---|
| **Servicio de Inferencia Local** | `IMPLEMENTADO` | `llama-server.exe` con CUDA 12 escuchando en `http://127.0.0.1:8080`. |
| **Scripts de Ciclo de Vida** | `IMPLEMENTADO` | `iniciar_llama_server.ps1`, `detener_llama_server.ps1`, `comprobar_salud_llm.ps1`. |
| **Supervisor de Proceso Desacoplado** | `IMPLEMENTADO` | `SupervisorLlamaServer` en `proveedor_modelo.supervisor` con bloqueo exclusivo, archivo de estado, backoff exponencial, detección de caídas y protección contra bucles de OOM (`AGOTADO`). |
| **Cliente Central Oficial con Resiliencia** | `IMPLEMENTADO` | `ClienteCerebroCentral` en `cerebro_audacia.conexion` con Circuit Breaker, LlmGovernor, validación de presupuesto de contexto, detección estricta de CUDA OOM (`LlmOomError`), captor de respuestas truncadas (`LlmRespuestaInvalidaError`) y telemetría volátil thread-safe. |
| **Lock de GPU para Audio en Django** | `IMPLEMENTADO` | `HAKU_GPU_LOCK` en `services.py`: cerrojo `threading.RLock()` local al proceso Django. |
| **Marco de Contratos y Resiliencia** | `IMPLEMENTADO` | `AgentContract`, `AgentMetadata`, `ResilientAgentExecutor` probados con suite de tests. |
| **Orquestador Central con Triple Barrera** | `IMPLEMENTADO PARCIALMENTE` | `OrquestadorAudacIA` implementado y probado con mocks; pendiente conectar agentes reales. |
| **Adaptadores de Contrato (HAKU / HACU)** | `ARQUITECTURA DEFINIDA` | Especificación de `HakuInventarioAdapter` y `HacuVisitanteAdapter` documentada (sin código). |
| **Colas de Prioridad entre Agentes** | `PENDIENTE DE IMPLEMENTACIÓN` | Asignación de prioridad operativa (HAKU > HACU) en ráfagas de saturación. |
| **Recuperación Automática y Protección OOM** | `IMPLEMENTADO` | Supervisor con límite de reinicios consecutivos (3), aislamiento en `ResourceTracker` y tipificación de excepción `LlmOomError`. |
| **Métricas en Producción (/metrics)** | `PENDIENTE DE IMPLEMENTACIÓN` | Endpoint Prometheus en `llama-server.exe` pendiente de habilitación con bandera `--metrics`. |

---

## 8. Decisiones Arquitectónicas Pendientes

Las siguientes decisiones técnicas se resolverán con base en los benchmarks sobre el hardware definitivo (RTX 5070 Ti 16 GB):

1. **Modelo Oficial Definitivo (8B vs. 12B):** Se evaluará si el modelo 12B mantiene latencias interactivas ($\le 2.5\text{ s}$ para el primer token) bajo 2 ranuras concurrentes en la RTX 5070 Ti, o si se conserva 8B con mayor ventana de contexto.
2. **Priorización de Ranuras:** Determinar si HAKU (inventario operativo de laboratorio) tendrá prioridad de desalojo o ranura reservada frente a HACU (visitantes en stand).
3. **Métricas en Producción:** Habilitar la bandera `--metrics` de Prometheus en `iniciar_llama_server.ps1` para registrar throughput y latencias reales.

---

*Documento formalizado y aprobado como parte de la Arquitectura de Cerebro Central Único de AudacIA.*
