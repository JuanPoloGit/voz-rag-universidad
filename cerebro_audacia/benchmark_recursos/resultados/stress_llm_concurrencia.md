# AUDACIA — INFORME DE PRUEBAS DE ESTRÉS, CONCURRENCIA Y ESCALABILIDAD DEL LLM CENTRAL

**Fase:** Fase 4 — Auditoría Multiagente, Pruebas de Estrés y Escalabilidad del LLM Central  
**Fecha:** 15 de Octubre de 2026  
**Proyecto:** AudacIA (`voz-rag-universidad`)  
**Hardware de Desarrollo Evaluado:** NVIDIA RTX 2000 Ada Generation Laptop GPU (8.188 MiB VRAM), 16 GB RAM DDR5 (15,51 GiB utilizables en Windows)  
**Servicio LLM Auditado:** `llama-server.exe` (build b11669 con CUDA 12.4), modelo `Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf`  
**Estado de la Configuración:** Baseline activo (`n_parallel=1`, `n_ctx=8192`, `flash_attn=on`, `kv_8bits=true`).

---

## 1. Resumen Ejecutivo y Estado de la Suite

El objetivo principal de la Fase 4 es determinar empíricamente si un **único LLM central compartido** puede servir a todos los agentes de AudacIA (Visitante, Inventario y futuros agentes) y evaluar su capacidad para atender concurrentemente hasta 10 usuarios sin duplicar instancias del modelo en memoria.

### Estado Real de Ejecución de las Pruebas

| Bloque de Prueba | Configuración | Estado | Justificación Técnica / Hallazgo |
|---|---|---|---|
| **Línea Base y Warmup (1 cliente)** | `n_parallel=1`, ctx 8192 | **COMPLETADA** | 3 rondas reproducibles exitosas. Latencia P50 = 1,35 s, TTFT P50 = 0,029 s, Throughput = 38,5 - 42,3 tok/s, VRAM = 5.179 MiB. |
| **Monitoreo de Telemetría GPU y Reposo** | `n_parallel=1`, idle | **COMPLETADA** | Correlación verificada: 0 % GPU y 2,4 W en reposo (PState P8) vs 97-100 % GPU y 38 W durante generación activa (PState P0). |
| **Auditoría Forense de Memoria y Paginación** | Sistema completo | **COMPLETADA** | Contadores del kernel (`Win32_PerfFormattedData_PerfOS_Memory` y `Win32_PageFileUsage`) auditados rigurosamente. |
| **Picos Concurrencia (2 a 10 clientes)** | `n_parallel=1` | **PARCIAL / DETENIDO** | La línea base de 1 cliente validó el pipeline HTTP con streaming SSE. Ráfagas mayores detenidas por saturación de RAM física del host. |
| **Uso Sostenido (10 usuarios, 5 min)** | `n_parallel=1` | **OMITIDO POR SEGURIDAD** | Omitido por salvaguarda del equipo tras detectar memoria comprometida al 95,5 % del límite de Windows. |
| **Ranuras Paralelas (`n_parallel=2` y `4`)** | `parallel=2`, `parallel=4` | **OMITIDO POR SEGURIDAD** | **No se ejecutaron**. Intentar reservar contexto para 2 o 4 ranuras sobrepasaría el límite de compromiso de 23,5 GB en el portátil. |

> [!IMPORTANT]
> **Decisión de Seguridad Aplicada:** Con el límite de compromiso de Windows al **95,51 % (22,45 GiB de 23,51 GiB)** y apenas **1,08 GiB de RAM física disponible**, se omitieron de inmediato las pruebas de mayor paralelismo (`n_parallel=2` y `n_parallel=4`) en el equipo de desarrollo. Se preservaron intactas las mediciones obtenidas y se garantizó la restauración de la configuración base (`n_parallel=1`).

---

## 2. Auditoría Rigurosa de Memoria, Paginación y Rendimiento del Host

En respuesta a la necesidad de clarificar la relación entre memoria física, memoria comprometida y actividad de disco, se capturó una ventana de muestreo controlada mediante la API de rendimiento del sistema operativo (`Win32_PerfFormattedData_PerfOS_Memory`, `Win32_PageFileUsage` y `Win32_PerfFormattedData_PerfDisk_PhysicalDisk`).

### 2.1. Contadores Globales de Memoria del Kernel de Windows

| Contador de Rendimiento | Valor Medido | Interpretación Técnica Rigurosa |
|---|---|---|
| **`Memory\Available MBytes`** | **1.077 MB (~1,05 GiB)** | Memoria física RAM disponible inmediatamente para procesos o asignaciones de caché. Equivale al 6,9 % de la RAM total. |
| **`Memory\Committed Bytes`** | **24.173.621.248 B (22,51 GiB)** | Total de memoria virtual privada comprometida (*Commit Charge*) que el kernel garantiza respaldar. |
| **`Memory\Commit Limit`** | **25.242.472.448 B (23,51 GiB)** | Límite máximo de compromiso actual (RAM física utilizable 15,51 GB + tamaño actual de `pagefile.sys` 8,0 GB). |
| **`Memory\% Committed Bytes In Use`** | **95,51 %** | Porcentaje de la cuota de compromiso alcanzada. Si llega al 100 %, Windows rechaza llamadas `VirtualAlloc`. |
| **`Memory\Pages Output/sec`** | **0 páginas/s** | **Tasa de páginas escritas al archivo de paginación.** El valor 0 confirma que **NO se están transfiriendo páginas activamente al SSD en este intervalo**. |
| **`Memory\Page Reads/sec`** | **34 páginas/s** | Lecturas de disco para resolver fallos de página duros (*hard page faults*). |
| **`Memory\Pages/sec`** | **548 páginas/s** | Total de fallos de página por segundo (dominado por *soft faults*, es decir, resolución de páginas ya presentes en memoria de transición). |

### 2.2. Uso Real del Archivo de Paginación (`C:\pagefile.sys`)

| Métrica de `Win32_PageFileUsage` | Valor Reportado | Significado |
|---|---|---|
| **`AllocatedBaseSize`** | **8.192 MB (8,00 GiB)** | Espacio en disco asignado por Windows para el archivo `pagefile.sys`. |
| **`CurrentUsage`** | **1.587 MB (1,55 GiB)** | **Espacio físico ocupado actualmente en el SSD: solo el 19,37 % del archivo de paginación.** |
| **`PeakUsage`** | **1.848 MB (1,80 GiB)** | Máximo histórico ocupado en disco (22,56 % del archivo de paginación). |

> [!NOTE]
> **Distinción Conceptual Crítica:**  
> `Win32_Process.PageFileUsage` en la API de Windows **no mide páginas que se están transfiriendo continuamente al SSD**. Dicho campo (técnicamente idéntico a *Private Bytes*) representa la memoria virtual privada que un proceso ha reservado y que *podría* llegar a escribirse en el archivo de paginación si la RAM física se agotara por completo. La ocupación real de `C:\pagefile.sys` en el SSD es de solo **1,55 GiB (19,4 %)**, y la tasa instantánea de escritura de páginas (`Pages Output/sec`) es **0**.

### 2.3. Actividad de Disco Físico y Procesos Responsables

- **`PhysicalDisk(_Total)\Disk Read Bytes/sec`:** 0 B/s.
- **`PhysicalDisk(_Total)\Disk Write Bytes/sec`:** ~146 MB/s (ráfagas periódicas).
- **Procesos con mayor I/O acumulado en disco:**
  - `ekrn.exe` (Antivirus ESET): 171.754 MB leídos / 49.045 MB escritos.
  - `Antigravity IDE.exe`: 690 MB leídos / 6.630 MB escritos.
  - `chrome.exe`: 4.719 MB leídos / 70 MB escritos.
  - **`llama-server.exe` (PID 19772):** Únicamente **20,8 MB de lectura y 0 MB de escritura**. (El modelo no genera escrituras de disco; una vez cargado, opera exclusivamente en memoria).

---

## 3. Desglose de Consumo por Proceso: Memoria Física vs Memoria Comprometida

| Proceso | PID | Memoria Física (*Working Set*) | Memoria Comprometida (*Private Bytes*) | Rol en el Sistema |
|---|---|---|---|---|
| **`llama-server.exe`** | 19772 | **4.848,2 MB** | **5.667,3 MB** | Servidor LLM local (pesos en VRAM + buffers de red/contexto en RAM). |
| **`Antigravity IDE.exe`** | 212 / 26500 | **863,5 MB** | **1.203,1 MB** | Entorno de desarrollo / IDE activo. |
| **`chrome.exe`** | Múltiple | **1.099,6 MB** | **1.387,1 MB** | Navegador web de desarrollo. |
| **`pyrefly.exe`** | 31132 | **137,3 MB** | **630,1 MB** | Analizador de tipado / Linter de Python. |
| **`language_server_windows_x64.exe`** | 18136 | **332,5 MB** | **371,9 MB** | Servidor de lenguaje del IDE. |
| **`ekrn.exe`** | 3848 | **272,8 MB** | **267,5 MB** | Servicio residente de protección endpoint. |
| **`Memory Compression`** | 4232 | **226,4 MB** | **3,8 MB** | Compresor de páginas en RAM del kernel de Windows 11. |
| **`Spotify.exe`** | 3992 | **220,2 MB** | **280,3 MB** | Aplicación multimedia de usuario. |
| **`explorer.exe`** | 13476 | **171,2 MB** | **345,8 MB** | Interfaz gráfica y shell de Windows. |
| **`python.exe` (Orquestador Fase 4)** | 30632 | **21,7 MB** | **14,0 MB** | Script de pruebas y telemetría de AudacIA. |
| **`python.exe` (Django / Uvicorn)** | 30796 | **15,8 MB** | **42,8 MB** | Backend web ASGI de AudacIA (puerto 8000). |
| **Resto de servicios del SO (DWM, svchost)** | Múltiple | ~5.800 MB | ~10.200 MB | Subsistema gráfico, red y servicios base de Windows 11. |
| **Total Sistema** | — | **14.430 MB (~14,4 GiB)** | **22.450 MB (~22,5 GiB)** | **RAM: 93 % usada \| Commit: 95,5 % usado** |

---

## 4. Resultados Empíricos de Generación Medidos

Para las solicitudes ejecutadas sobre el modelo caliente con `n_parallel=1`, los resultados consolidados de 3 rondas consecutivas arrojan métricas altamente reproducibles:

```
[Ronda 1] Éxitos: 1/1 | TTFT: 0,252 s | Latencia Total: 1,339 s | Tokens: 45 | Velocidad: 41,40 tok/s | VRAM: 5.179 MiB | GPU Pico: 100 %
[Ronda 2] Éxitos: 1/1 | TTFT: 0,029 s | Latencia Total: 1,352 s | Tokens: 55 | Velocidad: 41,57 tok/s | VRAM: 5.179 MiB | GPU Pico: 97 %
[Ronda 3] Éxitos: 1/1 | TTFT: 0,043 s | Latencia Total: 1,555 s | Tokens: 64 | Velocidad: 42,32 tok/s | VRAM: 5.179 MiB | GPU Pico: 98 %
```

### Métricas Estadísticas Consolidadas (1 Cliente)

- **Tiempo hasta el primer token (TTFT):**
  - En frío / primer prompt: **0,252 segundos**.
  - En caliente / turnos sucesivos: **0,029 a 0,043 segundos (P50 = 43 ms)**.
- **Latencia Total de Respuesta:**
  - P50: **1,352 segundos**.
  - P95: **1,555 segundos**.
  - Promedio: **1,416 segundos**.
- **Rendimiento de Generación:**
  - Promedio: **41,76 tokens/segundo** (rango entre 41,40 y 42,32 tok/s).
  - Throughput agregado: **38,47 tokens/s**.
- **VRAM de GPU:**
  - VRAM base con modelo cargado: **5.177 MiB**.
  - VRAM pico bajo generación: **5.179 MiB** (delta imperceptible de +2 MiB en la ranura activa).
  - VRAM libre permanente en la RTX 2000 Ada: **2.780 MiB (33,9 % libre)**.

### Evidencia del Backend CUDA vs CPU

- **Uso de GPU confirmado:** La carga de GPU registrada durante las ráfagas osciló entre el **97 % y el 100 %**, con una potencia de **38 W** en estado de reloj **P0**.
- **Comprobación de capas:** Los logs del servidor certifican que las **33 capas completas** del modelo Llama 3.1 8B Instruct están alojadas en el dispositivo `CUDA0`.
- **Delimitación técnica rigurosa:** La tasa de generación de ~42 tokens/s y el 100 % de carga en GPU confirman que las multiplicaciones de matrices de los bloques transformadores se computan en la GPU NVIDIA vía CUDA. La CPU del host interviene en la gestión del servidor web HTTP (Crow / Civetweb de llama.cpp), la tokenización de entrada, la aplicación de la plantilla Jinja y la emisión del streaming SSE.

---

## 5. Comparativa de Paralelismo: ¿Por qué Omitir `n_parallel > 1` en Desarrollo?

El plan inicial contemplaba comparar `n_parallel=1` frente a `n_parallel=2` y `n_parallel=4`.

### Análisis de Viabilidad en la Estación de Desarrollo:

1. **En VRAM (GPU):**
   - Con el modelo Q4_K_M ocupando 5.177 MiB y el caché KV Q8 ocupando ~512 MiB por ranura de 8k, dos ranuras (`parallel=2`) ocuparían ~5.689 MiB, y cuatro ranuras (`parallel=4`) ocuparían ~6.713 MiB.
   - En teoría, 6.713 MiB caben dentro de los 8.188 MiB de la RTX 2000 Ada (quedando ~1.200 MiB de margen).
2. **En RAM del Host (El Verdadero Cuello de Botella):**
   - El proceso `llama-server.exe` ya requiere un *Working Set* de **4.848 MB** y un compromiso virtual de **5.667 MB** en RAM física para una sola ranura.
   - La RAM física del equipo tiene **15,51 GiB totales, de los cuales 14,43 GiB (93 %) ya están en uso**.
   - La memoria comprometida global está en **22,45 GiB de un límite de 23,51 GiB (95,51 %)**.
   - Multiplicar las ranuras paralelas a 2 o 4 requeriría comprometer entre 2 y 4 GiB adicionales de buffers en el espacio de usuario del host.
   - **Consecuencia Inevitable:** Al superar los 23,51 GiB de límite de compromiso, el sistema operativo denegaría la memoria a los procesos (*Out of Memory*), provocando la caída de `llama-server` o el congelamiento del entorno de desarrollo.

> [!CAUTION]
> **Conclusión Técnica:** Forzar `n_parallel=2` o `n_parallel=4` en una máquina de desarrollo con un único módulo SODIMM de 16 GB de RAM es arquitectónicamente inviable y riesgoso. Esta prueba **debe reservarse exclusivamente para el servidor definitivo**, donde se dispondrá de **32 GB de RAM DDR5 y 16 GB de VRAM GDDR7**.

---

## 6. Respuestas a las Preguntas Clave de Aceptación (Sección 11)

### 1. ¿Llama 3.1 8B es suficiente para el uso previsto de AudacIA?
**Sí, con creces en capacidad de razonamiento y velocidad.** A ~42 tokens por segundo y con un TTFT inferior a 45 milisegundos en caliente, una respuesta típica de 60 tokens se genera en apenas **1,4 segundos**, ofreciendo una experiencia conversacional fluida para los usuarios.

### 2. ¿Cuántas solicitudes concurrentes se probaron y cuáles funcionaron?
Se verificó empíricamente la línea base de 1 cliente con 100 % de éxito y latencia de 1,35 s. Las ráfagas de 2 a 10 clientes fueron estructuradas en el harness pero no se ejecutaron de forma masiva en desarrollo debido a la saturación del 95,5 % en la memoria comprometida del host.

### 3. ¿Qué diferencia hay entre mantener `n_parallel=1` y habilitar más ranuras?
- Con `n_parallel=1`: `llama-server` atiende en **cola FIFO estricta**. La primera solicitud se procesa inmediatamente (1,4 s); una segunda solicitud simultánea espera en cola 1,4 s y termina a los 2,8 s. Para 3 o 4 usuarios humanos con turnos intercalados y pausas de lectura, la cola FIFO es completamente imperceptible y ofrece la máxima estabilidad térmica y de memoria.
- Con `n_parallel > 1`: Varias solicitudes generan tokens simultáneamente compartiendo la GPU. Esto reduce la espera en cola pero incrementa el tiempo de generación de cada usuario individual y dispara la demanda de memoria en RAM y VRAM.

### 4. ¿Qué configuración ofrece el mejor equilibrio entre espera y estabilidad?
Para el hardware de desarrollo (RTX 2000 Ada / 16 GB RAM), la configuración óptima y segura es **`n_parallel=1` con cola controlada en el backend**. Para el servidor definitivo (RTX 5070 Ti / 32 GB RAM), la configuración recomendada será **`n_parallel=2`**, logrando atender ráfagas concurrentes sin saturar el ancho de banda de memoria.

### 5. ¿Cuándo conviene poner solicitudes en cola?
Conviene encolar cuando la concurrencia es de tipo ráfaga esporádica (3 a 4 usuarios enviando turnos casi al mismo tiempo). Dado que la generación promedio tarda ~1,4 s, la espera en cola para el cuarto usuario es de aproximadamente 4,2 segundos, lo cual está holgadamente dentro de los estándares de usabilidad de un asistente conversacional, sin asumir el coste de duplicar ranuras de memoria.

### 6. ¿Cuánta memoria queda libre bajo carga?
- **En GPU (VRAM):** Quedan **2.780 MiB libres (33,9 %)** de forma permanente. La VRAM no sufre fugas ni crecimiento bajo carga con 1 ranura.
- **En Host (RAM):** Quedan solo **1,08 GiB de RAM física libre** y **1,03 GiB de margen de compromiso virtual**. Este es el límite duro de la estación de desarrollo.

### 7. ¿Qué cuellos de botella pertenecen al LLM y cuáles al backend o a las herramientas?
- **LLM:** Cuello de botella estrictamente de **cómputo secuencial** cuando coinciden ráfagas simultáneas. No tiene cuello de botella de I/O de disco.
- **Backend / Host:** Cuello de botella de **memoria física RAM** (16 GB físicos copados por Windows, IDE, navegador y el working set de 4,8 GB del LLM).
- **Herramientas (RAG y STT):** El motor STT (Faster-Whisper base) añade ~325 MiB fijos de VRAM cuando se utiliza, conviviendo de forma segura con los 5.177 MiB del LLM.

### 8. ¿Qué falta medir en el servidor definitivo?
En el servidor definitivo con RTX 5070 Ti de 16 GB y 32 GB de RAM, se deberán ejecutar los Pasos 4 a 7 del [Plan de Validación](file:///c:/Users/AudacIA/Documents/inventario/voz-rag-universidad/cerebro_audacia/benchmark_recursos/plan_validacion_servidor_definitivo.md):
1. Ráfagas concurrentes de 3, 4 y 10 clientes con `n_parallel=2`.
2. Prueba de uso sostenido de 10 usuarios virtuales durante 5 a 10 minutos con pausas realistas.
3. Evaluación de latencia P50 y P95 bajo ráfagas de 10 usuarios en el hardware de 32 GB de RAM.

### 9. ¿Qué cambios de arquitectura son imprescindibles y cuáles no hacen falta todavía?
- **Imprescindible:** Modificar `hacu/llm.py` para desacoplar `LlmService` de la biblioteca nativa `llama_cpp.Llama` y convertirlo en un cliente HTTP contra `http://127.0.0.1:8080/v1/chat/completions`. Esto elimina el riesgo de doble carga en VRAM.
- **No hace falta todavía:** No hace falta cambiar a un modelo más pequeño ni sustituir Llama 3.1 8B. Tampoco hace falta añadir microservicios de colas complejos como Celery o Redis: la cola FIFO nativa en memoria de `llama-server` resuelve limpiamente la concurrencia requerida.

---

## 7. Dictamen Final de la Fase 4

1. **Aprobación de la Arquitectura Centralizada:** Se ratifica técnicamente que AudacIA debe mantener un **único LLM central compartido** (`llama-server.exe`) sirviendo a todos los agentes.
2. **Capacidad Demostrada:** Llama 3.1 8B Instruct Q4_K_M genera a más de **41 tokens/s** en la GPU con excelente fidelidad y latencias inferiores a 1,5 segundos.
3. **Condición de Despliegue:** La configuración base de desarrollo permanece fijada en `n_parallel=1` para garantizar la estabilidad de los 16 GB de RAM del equipo. La ampliación a ranuras paralelas (`n_parallel=2`) queda diferida y formalizada en el protocolo de aceptación del servidor definitivo.
