# AudacIA — Guía Práctica de Uso, Operación y Diagnóstico del Sistema

Esta guía está diseñada para que cualquier desarrollador, ingeniero de sistemas o asistente de IA pueda comprender con rapidez:
- Qué es AudacIA y cómo está concebida su arquitectura conceptual y operativa.
- La **regla de diseño innegociable: un único LLM centralizado ("Cerebro Central") para todos los agentes**.
- Cómo se diferencian estrictamente el modelo, el motor de inferencia, el Cerebro Central, los agentes y las interfaces.
- Por qué **un agente nunca es un modelo** y por qué está **terminantemente prohibido que un agente cargue su propio LLM**.
- El gobierno de recursos sobre la GPU NVIDIA GeForce RTX 5070 Ti (16 GB VRAM).
- El funcionamiento y diagnóstico de **HAKU Inventario** y **HACU Visitante**.
- Cómo poner en marcha, monitorear, diagnosticar y apagar el sistema en Windows.

---

## 1. Decisión Arquitectónica Fundamental: Un Único LLM Centralizado

AudacIA opera bajo una regla arquitectónica estricta y obligatoria:

> **AudacIA cuenta con un único LLM centralizado ("Cerebro Central").**  
> Todos los agentes —HAKU Inventario, HACU Visitante y cualquier agente que se construya en el futuro— utilizan **exclusivamente el Cerebro Central de AudacIA** como proveedor de lenguaje natural.

```text
                         AUDACIA
                            │
                            ▼
                  ┌───────────────────┐
                  │  CEREBRO CENTRAL  │
                  │                   │
                  │  LLM único        │
                  │  llama.cpp        │
                  │  llama-server     │
                  │  Gobierno de VRAM │
                  │  API local (8080) │
                  └─────────┬─────────┘
                            │
          ┌─────────────────┼─────────────────┐
          │                 │                 │
          ▼                 ▼                 ▼
        HAKU              HACU          AGENTE FUTURO
      Inventario        Visitante       Especializado
          │                 │                 │
      herramientas       RAG              herramientas
      BD inventario       memoria         reglas
      lógica negocio      voz Piper       memoria
      seguridad 2P        interfaz stand  etc.
```

### Qué significa esto en la práctica:
1. **Los agentes son diversos en software:** Pueden tener comportamientos radicalmente distintos, sus propios prompts e instrucciones del sistema, sus propias bases de datos, memorias operativas, índices RAG, herramientas, permisos y reglas de negocio.
2. **Ningún agente tiene un LLM propio:** Queda expresamente fuera de la arquitectura soportada cualquier diseño donde cada agente cargue o administre un modelo independiente (ej: *HAKU con Llama 8B propio + HACU con Llama 12B propio + Agente X con otro modelo*).
3. **Escalabilidad de agentes, no de modelos:** AudacIA escala agregando agentes especializados en código Python que consumen el servicio de lenguaje compartido, evitando la multiplicación destructiva de consumo de VRAM.

---

## 2. Diferenciación Fundamental: Modelo, Motor, Cerebro Central, Agente y Aplicación

Para mantener la claridad conceptual en todo el equipo, se distinguen rigurosamente cinco conceptos:

```text
+-----------------------------------------------------------------------------------+
|  1. MODELO                                                                        |
|     Red neuronal con capacidad de generar y comprender lenguaje.                   |
|     Ejemplo: Meta Llama 3.1 (archivo binario .gguf en disco).                     |
+-----------------------------------------------------------------------------------+
                                          │
                                          ▼
+-----------------------------------------------------------------------------------+
|  2. MOTOR DE INFERENCIA                                                           |
|     Software C++/CUDA que carga tensores en VRAM/RAM y calcula la inferencia.     |
|     Ejemplo: llama.cpp / bin/llama-server.exe compilado con NVIDIA CUDA 12.       |
+-----------------------------------------------------------------------------------+
                                          │
                                          ▼
+-----------------------------------------------------------------------------------+
|  3. CEREBRO CENTRAL AUDACIA                                                       |
|     Infraestructura central de lenguaje: expone la API HTTP local (puerto 8080),  |
|     gobierna la concurrencia, las colas, los timeouts, la VRAM y la salud.        |
+-----------------------------------------------------------------------------------+
                                          │
                  ┌───────────────────────┴───────────────────────┐
                  ▼                                               ▼
+-----------------------------------+   +-------------------------------------------+
|  4. AGENTE ESPECIALIZADO          |   |  4. AGENTE ESPECIALIZADO                  |
|     HAKU Inventario               |   |     HACU Visitante                        |
|     Lógica de inventario, stock,  |   |     RAG documental, memoria de stand,     |
|     almacenes, reglas de negocio, |   |     diálogo universitario, cliente del    |
|     seguridad 2P y cliente central|   |     Cerebro Central.                      |
+-----------------------------------+   +-------------------------------------------+
                  │                                               │
                  ▼                                               ▼
+-----------------------------------+   +-------------------------------------------+
|  5. APLICACIÓN / INTERFAZ         |   |  5. APLICACIÓN / INTERFAZ                 |
|     Panel web Django (/api/haku/) |   |     Ventana gráfica PyQt6/PySide6 o       |
|     con micrófono en navegador.   |   |     consola interactiva (run_hacu.py).    |
+-----------------------------------+   +-------------------------------------------+
```

### Regla: Agente $\neq$ Modelo
- **El modelo** son solo matrices de números congeladas en un archivo `.gguf`.
- **El agente** es una aplicación especializada completa: contiene reglas de dominio, validaciones, acceso a datos y flujos conversacionales.
- **El agente no posee el modelo:** El agente *consume* la capacidad de inferencia del Cerebro Central mediante un cliente oficial tipado.

---

## 3. El Cerebro Central como Infraestructura y Selección del Modelo

El Cerebro Central no debe reducirse a "un script que levanta un proceso". Es la **infraestructura unificada de lenguaje y razonamiento** de la plataforma.

```text
┌─────────────────────────────────────────────────────────┐
│                 CEREBRO CENTRAL AUDACIA                 │
│                                                         │
│   API Local / Gateway (/v1/chat/completions)           │
│   Control de Concurrencia (Slots en paralelo)           │
│   Gobernador de Colas y Prioridades                    │
│   Timeouts y Detección de Saturación                   │
│   Sondeo de Salud (/health) y Métricas                  │
│   Protección de VRAM ante CUDA OOM                      │
│                           │                             │
│                           ▼                             │
│               llama-server.exe (llama.cpp)              │
│                           │                             │
│                           ▼                             │
│                      ÚNICO LLM                          │
│         (Meta Llama 3.1 8B  /  Candidato 12B)          │
└─────────────────────────────────────────────────────────┘
```

### Modelo Actual y Candidatos:
- **Modelo Base Utilizado:** Actualmente se ha trabajado y evaluado con **Meta Llama 3.1 8B Instruct** (cuantización `Q4_K_M`, ~4.92 GB en disco y ~5.6 GB en VRAM con contexto).
- **Evidencia Experimental con 12B:** En pruebas con HACU Visitante se observó empíricamente que un modelo de **12B produjo respuestas más completas y con mejor retención contextual que el 8B**.
- **Desacoplamiento del Número de Parámetros:** La arquitectura de AudacIA **no está acoplada rígidamente a 8B ni a 12B**. El modelo concreto puede actualizarse centralizadamente bajo decisión técnica sin modificar una sola línea de código en los agentes.

### Cambio Centralizado de Modelo:
Cuando se decida promover un modelo superior (ej. migrar de 8B a 12B tras validar calidad, VRAM, latencia y concurrencia):
- El cambio se realiza **exclusivamente en el Cerebro Central** (sustituyendo el `.gguf` y ajustando `config_llm.json`).
- Los agentes (HAKU, HACU, etc.) continúan interactuando con la misma API local sin enterarse del cambio de pesos interno.

---

## 4. Política Estricta: Prohibición de Modelos Locales por Agente

Queda formalmente establecido como **política de arquitectura innegociable**:

> [!CAUTION]
> **PROHIBICIÓN ARQUITECTÓNICA ABSOLUTA:**  
> Ningún agente nuevo ni existente puede:
> 1. Instanciar bibliotecas de modelos locales como `Llama(...)`, `AutoModelForCausalLM`, etc.
> 2. Cargar archivos de pesos `.gguf`, `.safetensors` o similares para procesamiento de lenguaje.
> 3. Iniciar instancias secundarias de `llama-server.exe` ni otros servidores LLM.
> 4. Reservar VRAM de la GPU para modelos de lenguaje propios.
> 5. Conectarse directamente a proveedores externos de LLM saltándose el Cerebro Central.

Cualquier agente que requiera capacidades de comprensión, extracción o generación de texto debe implementarse como **cliente del Cerebro Central**.

### Política sobre Adaptación, Fine-Tuning y LoRA:
- **Especialización por software:** Los agentes se especializan principalmente mediante instrucciones del sistema, prompts estructurados, herramientas (*tool calling*), memoria operativa, RAG documental y lógica de negocio.
- **Sin adaptadores LoRA independientes por agente:** Queda prohibido un ecosistema de "modelo base + LoRA HAKU + LoRA HACU + LoRA Agente X" administrados de forma fragmentada. Si en el futuro se aprueba fine-tuning o LoRA, será una modificación gobernada del **modelo central**.

---

## 5. Gobierno Central de Recursos y VRAM (Hardware RTX 5070 Ti)

### 5.1. Justificación Física del Hardware
El servidor físico central cuenta con una tarjeta **NVIDIA GeForce RTX 5070 Ti de 16 GB de VRAM GDDR7** y **32 GB de RAM DDR5**.

Aunque es una máquina potente, **16 GB de VRAM es un recurso finito y compartido**:
- Cargar un modelo de 8B o 12B junto con su KV cache para ventanas de 8K a 16K tokens consume entre **5.5 GB y 9.5 GB de VRAM**.
- La transcripción local de voz (Faster-Whisper) requiere entre **350 MB y 1.5 GB de VRAM** según el modelo activo.
- Si cada agente intentara cargar su propio modelo, el sistema caería inmediatamente en **CUDA Out of Memory (OOM)**, degradación por paginación en disco (*swapping*), latencias de varios segundos y colapso de la API.

### 5.2. Simultaneidad y Concurrencia Finita
Un error habitual es asumir que tener un LLM centralizado permite infinitas consultas concurrentes instantáneas:
> **$N$ agentes no equivalen a $N$ inferencias simultáneas ilimitadas.**

La capacidad real de atención simultánea está gobernada por:
1. **Ranuras paralelas del motor (`--parallel`):** Configurado en 1 ranura para pruebas y 2 ranuras para el servidor institucional.
2. **Cola de espera (*Queue Governor*):** Cuando las ranuras están ocupadas, las peticiones entrantes deben encolarse de forma controlada o rechazarse con error de saturación si se supera el timeout.
3. **Presupuesto de Contexto:** Cada ranura activa reserva memoria para su propio KV cache. Ventanas excesivas saturan la memoria de video.

### 5.3. Taxonomía del Estado de Implementación del Gobierno de Recursos:
Para mantener honestidad técnica rigurosa:

| Componente de Gobierno | Estado Técnico Real | Descripción |
|---|:---:|---|
| **API Local OpenAI-Compatible** | `IMPLEMENTADO` | Endpoint `/v1/chat/completions` en puerto 8080 (`llama-server.exe`). |
| **Sondeo de Salud (/health)** | `IMPLEMENTADO` | `comprobar_salud_llm.ps1` y endpoint nativo `/health`. |
| **Cliente Central Oficial** | `IMPLEMENTADO` | `ClienteCerebroCentral` en `cerebro_audacia.conexion` con `LlmGovernor` y Circuit Breaker. |
| **Bloqueo de Audio en Django** | `IMPLEMENTADO` | `HAKU_GPU_LOCK` en `services.py`: cerrojo `threading.RLock()` local al worker web de Django (no inter-proceso). |
| **Marco de Contratos y Resiliencia** | `IMPLEMENTADO` | `AgentContract`, `AgentMetadata`, `ResilientAgentExecutor` probados con 73 tests unitarios. |
| **Gateway / Orquestador Central** | `IMPLEMENTADO PARCIALMENTE` | `OrquestadorAudacIA` implementado y probado con mocks; pendiente registrar agentes reales. |
| **Adaptadores de Contrato (HAKU / HACU)** | `ARQUITECTURA DEFINIDA` | Especificación de `HakuInventarioAdapter` y `HacuVisitanteAdapter` documentada (sin código). |
| **Colas de Prioridad entre Agentes** | `PENDIENTE DE IMPLEMENTACIÓN` | Asignación de prioridad operativa (HAKU > HACU) en ráfagas de saturación. |
| **Recuperación Dinámica ante CUDA OOM** | `PENDIENTE DE IMPLEMENTACIÓN` | Detección de OOM en memoria existe; reinicio automático del proceso `llama-server.exe` pendiente. |
| **Métricas en Producción (/metrics)** | `PENDIENTE DE IMPLEMENTACIÓN` | Endpoint Prometheus en `llama-server.exe` pendiente de habilitación con bandera `--metrics`. |

---

## 6. Los Diez Límites y Restricciones Innegociables de la Arquitectura

1. **Límite 1 — Un Único Proveedor de Lenguaje:** Todos los agentes se conectan exclusivamente al Cerebro Central si requieren inferencia generativa.
2. **Límite 2 — Sin LLM por Agente:** Ningún agente puede compilar, cargar o ejecutar modelos de lenguaje propios ni reservar VRAM.
3. **Límite 3 — VRAM Compartida:** La memoria de la GPU es finita (16 GB) y está administrada centralmente; ningún proceso puede monopolizarla.
4. **Límite 4 — Concurrencia Finita:** La escalabilidad de AudacIA es de *agentes conectados*, no de *inferencias simultáneas ilimitadas*. El número de ranuras de procesamiento paralelo es finito (`--parallel 1` o `2`).
5. **Límite 5 — Contexto Finito:** Cada petición consume memoria KV cache en función de sus tokens de entrada, historial y salida. Se aplican podas estrictas de contexto.
6. **Límite 6 — Manejo Controlado de Saturación:** Al llegar al límite de capacidad, el sistema encola con timeout de 10s o rechaza limpiamente (`LlmSaturadoError`), impidiendo bloqueos indefinidos.
7. **Límite 7 — Resiliencia ante Fallos Centrales:** Si el Cerebro Central no está disponible, los agentes con capacidades deterministas (como HAKU) continúan operando normalmente.
8. **Límite 8 — Cambio Centralizado de Modelo:** Las migraciones de modelo (ej. 8B a 12B) se realizan en el Cerebro Central sin alterar la lógica de negocio de los agentes, pero exigen pruebas de regresión completas.
9. **Límite 9 — Seguridad Determinista (No Delegada al LLM):** El LLM propone acciones mediante herramientas; la autorización, validación y mutación física en base de datos es 100% determinista y externa al modelo.
10. **Límite 10 — Escalabilidad Física Condicionada:** La plataforma puede admitir decenas de definiciones de agentes, pero el rendimiento concurrente total depende de los límites físicos del hardware local.

---

## 7. HAKU Inventario: Estado Actual y Auditoría Técnica

### 7.1. Qué es HAKU
HAKU es el **agente especializado en inventario**. Su tarea es gestionar existencias, almacenes, préstamos, movimientos y alertas de herramientas y materiales de laboratorio mediante interfaces web y voz.

- **Modelo de Origen:** Fue concebido utilizando **Meta Llama 3.1 8B** como modelo de referencia para el dominio de inventario.
- **Independencia Determinista Operativa:** Para garantizar 0% de alucinaciones en existencias reales, las mutaciones de stock operan con el protocolo de dos pasos ([ADR-001](file:///c:/Users/AudacIA/Documents/inventario/voz-rag-universidad/sistema_inventario/integraciones/haku/services.py)): HAKU genera una propuesta y exige confirmación humana explícita antes de ejecutar transacciones ACID con `select_for_update()`.
- **Estado Actual:** HAKU opera de forma **100% determinista** mediante su normalizador lingüístico formal y servicios de base de datos. **No realiza llamadas al Cerebro Central ni a ningún LLM externo.**

### 7.2. Auditoría Técnica y Estabilización (Fases 1 y 2 Completadas)
Durante pruebas de conversación iniciales se detectaron fallos de comprensión y herencia de contexto que fueron auditados y corregidos sistemáticamente:
- **El problema de la "Ponchadora Pegada":** Resuelto en Fase 1 mediante timeout de foco conversacional y purga de contexto. En Fase 2 se consolidó el **aislamiento absoluto de alcance**: las consultas con alcance `GLOBAL` o `ALMACEN` purgan irrevocablemente el foco previo (`producto_en_contexto = None`), impidiendo cualquier contaminación contextual.
- **Consultas globales y de colección:** Resuelto en Fase 2 mediante separación composicional en `normalizador.py`:
  - **Intención/Concepto:** `STOCK`, `UBICACION`, `PRESTAMO`, `MOVIMIENTO`, etc.
  - **Alcance:** `GLOBAL`, `ALMACEN`, `COLECCION`, `INDIVIDUAL`.
  - **Condición:** `DISPONIBLE`, `AGOTADO`, `BAJO_STOCK`, `EN_PRESTAMO`, `ALERTAS`.
- **Variaciones de voz de la región Caribe:** Mapeadas en el normalizador determinista y diccionario fonético sin requerir un LLM.
- **Verificación:** La suite de pruebas de HAKU cuenta con **342 pruebas unitarias y de integración superadas (100% éxito)** sin regresiones.

### 7.3. Flujo Futuro Integrado con el Cerebro Central
> [!NOTE]
> **Diseño de Integración Futura:** El siguiente diagrama representa la **arquitectura proyectada para cuando se autorice la integración de HAKU con el Cerebro Central**. Actualmente HAKU resuelve todas sus consultas y operaciones de forma determinista y local, sin invocar al LLM.

Cuando HAKU se conecte al Cerebro Central, **el LLM nunca escribirá directamente en la base de datos**:

```text
Operario Humano
       │ (1. Frase hablada o escrita)
       ▼
  HAKU Inventario (Agente)
       │ (2. Envía texto y contexto al Cerebro Central)
       ▼
  Cerebro Central (LLM Único)
       │ (3. Devuelve llamada a herramienta: ej. {accion: 'SALIDA', cant: 2})
       ▼
  HAKU Inventario (Agente)
       │ (4. Valida existencias en BD y reglas de negocio)
       │ (5. Genera PROPUESTA ACTIVA y solicita confirmación humana)
       ▼
Operario Humano
       │ (6. Dicta: "Confirmado" o pulsa botón en pantalla)
       ▼
  Servicio Transaccional HAKU
       │ (7. select_for_update() + Registro inmutable de Auditoría)
       ▼
  Base de Datos (PostgreSQL / SQLite)
```

---

## 8. HACU Visitante: Agente de Stand y Exhibición

HACU es el **agente conversacional de atención a visitantes y divulgación tecnológica** de la universidad:
- Cuenta con un corpus documental de proyectos universitarios, memoria conversacional local (SQLite) y síntesis de voz en español neutro (Piper TTS).
- Utilizará el Cerebro Central para responder con naturalidad, cordialidad y precisión sobre las investigaciones universitarias.
- En pruebas experimentales, el modelo de 12B mostró una redacción superior al 8B para el rol de expositor.

---

## 9. Matriz de Responsabilidades del Sistema

| Capa del Sistema | Responsabilidad Principal | Componente Real en el Repositorio |
|---|---|---|
| **Modelo de Lenguaje** | Red neuronal única compartida para comprender y generar lenguaje. | `Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf` (Candidato: 12B) |
| **Motor de Inferencia** | Ejecución local de tensores en GPU NVIDIA CUDA 12. | `cerebro_audacia/proveedor_modelo/bin/llama-server.exe` |
| **Cerebro Central** | Demonio HTTP (`:8080`), control de ranuras, timeouts y cliente unificado. | `cerebro_audacia/proveedor_modelo/` y `cerebro_audacia/conexion/` |
| **Agente HAKU** | Lógica de existencias, almacenes, normalizador determinista y seguridad 2P. | `sistema_inventario/integraciones/haku/` (100% determinista actual) |
| **Agente HACU** | Diálogo de divulgación, RAG documental y memoria de visitantes. | `hacu/` (`agente.py`, `memoria.py`, `run_hacu.py`) |
| **Transcripción (STT)**| Reconocimiento de voz local con lock intra-proceso de Django. | `Faster-Whisper` con `HAKU_GPU_LOCK` en `services.py` |
| **Síntesis (TTS)** | Lectura de respuestas habladas en stand. | `Piper TTS` (en HACU) / Web Speech API (en navegador HAKU) |
| **Base de Datos** | Estado persistente del inventario con transacciones ACID. | `sistema_inventario/inventario/models.py` (Django ORM) |
| **Marco de Gobernanza** | Contratos formales de ejecución segura para agentes. | `cerebro_audacia/agentes/` (`AgentContract`, `ResilientAgentExecutor`) |
| **Orquestador Central** | Coordinación de tool calling y triple barrera de seguridad. | `cerebro_audacia/agentes/orquestador.py` (`OrquestadorAudacIA`) |

---

## 10. Despliegue Físico y Operación en Windows

### 10.1. Mapa de Procesos Físicos
En ejecución sobre Windows 11 Pro conviven:
1. **Proceso 1 (`llama-server.exe`):** Puerto TCP 8080. Mantiene el LLM único cargado en la VRAM de la RTX 5070 Ti.
2. **Proceso 2 (Django ASGI / Uvicorn):** Puerto TCP 8000 (o 8443 HTTPS LAN). Aloja la API web de HAKU, base de datos y STT Faster-Whisper.
3. **Proceso 3 (Opcional - HACU Visitante):** CLI o ventana gráfica PySide6 de stand.

### 10.2. Procedimiento de Puesta en Marcha (PowerShell)

Desde `C:\Users\AudacIA\Documents\inventario\voz-rag-universidad`:

#### Terminal 1: Iniciar el Cerebro Central (LLM Único)
```powershell
powershell -ExecutionPolicy Bypass -File cerebro_audacia\proveedor_modelo\iniciar_llama_server.ps1
```
*(Para perfil de servidor institucional con 16K de contexto y 2 ranuras: añadir `-Perfil servidor_rtx5070ti`).*  
*Verificación:* Muestra `SERVICIO LLM LISTO Y OPERATIVO EN http://127.0.0.1:8080`.

#### Terminal 2: Iniciar HAKU Inventario
```powershell
.\.venv\Scripts\Activate.ps1
python sistema_inventario\manage.py check
python sistema_inventario\manage.py runserver 127.0.0.1:8000
```
*Verificación:* Acceder a `http://127.0.0.1:8000/api/haku/panel/` *(Utilice las credenciales del usuario administrador creado localmente)*.

#### Terminal 3 (Opcional): Iniciar HACU Asistente de Stand
```powershell
.\.venv\Scripts\Activate.ps1
python run_hacu.py --debug
```
*(Para modo pantalla completa de stand: `python run_hacu.py --ui --voz`).*

---

## 11. Diagnóstico, Resolución de Problemas y Apagado

### 11.1. Verificación Rápida de Salud del Cerebro Central
```powershell
powershell -ExecutionPolicy Bypass -File cerebro_audacia\proveedor_modelo\comprobar_salud_llm.ps1
```

### 11.2. Fallos Típicos y Soluciones
- **Puerto 8080 ocupado:** Un proceso previo de `llama-server.exe` quedó activo. Ejecute:
  ```powershell
  powershell -ExecutionPolicy Bypass -File cerebro_audacia\proveedor_modelo\detener_llama_server.ps1
  ```
- **Error CUDA Out of Memory:** Otra aplicación retiene VRAM en la GPU. Verifique con `nvidia-smi` y reduzca la ventana de contexto (`n_ctx`) en `config_llm.json` si es necesario.
- **Django no conecta a PostgreSQL:** Configure temporalmente `DB_ENGINE=sqlite` en `sistema_inventario/.env` para desarrollo local ágil.
- **HAKU no muta el stock tras dictar orden:** Comportamiento seguro por diseño. Exige confirmación explícita ("confirmar" por voz o clic en botón).

### 11.3. Procedimiento de Apagado Limpio
1. Detener Django en Terminal 2 (`Ctrl + C`).
2. Detener HACU en Terminal 3 (escribir `salir` o cerrar ventana).
3. Detener el Cerebro Central y liberar la VRAM:
   ```powershell
   powershell -ExecutionPolicy Bypass -File cerebro_audacia\proveedor_modelo\detener_llama_server.ps1
   ```

---

## 12. Tabla de Parámetros y Variables de Entorno

| Variable / Archivo | Parámetro | Valor por Defecto Real | Función |
|---|---|---|---|
| `config_llm.json` | `puerto` | `8080` | Puerto TCP del Cerebro Central. |
| `config_llm.json` | `n_ctx` | `8192` (dev) / `16384` (srv) | Ventana de contexto del modelo en tokens. |
| `config_llm.json` | `n_gpu_layers` | `33` | Carga el 100% de las capas a la GPU NVIDIA. |
| `config_llm.json` | `parallel` | `1` (dev) / `2` (srv) | Ranuras simultáneas de inferencia en el LLM. |
| `sistema_inventario/.env` | `DB_ENGINE` | `postgresql` / `sqlite` | Motor de base de datos relacional. |
| `sistema_inventario/.env` | `HAKU_CONTEXT_TIMEOUT` | `7200` (s) | TTL de retención de contexto en memoria RAM. |
| `sistema_inventario/.env` | `HAKU_PROPOSAL_TIMEOUT`| `1800` (s) | Tiempo de validez para confirmar una operación. |
| Variable de Entorno | `AUDACIA_LLM_URL` | `http://127.0.0.1:8080/v1/chat/completions` | Endpoint consumido por clientes Python. |
| Variable de Entorno | `AUDACIA_LLM_MAX_CONCURRENCY` | `1` | Límite del gobernador de concurrencia en cliente. |

---

*Documento actualizado conforme a la Decisión Arquitectónica Fundamental de Cerebro Central Único de AudacIA.*
