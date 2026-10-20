# AUDACIA — PLAN DE VALIDACIÓN Y ACEPTACIÓN DEL SERVIDOR DEFINITIVO

**Destino:** Servidor Central Institucional AudacIA  
**Hardware Previsto:** NVIDIA GeForce RTX 5070 Ti (16 GB VRAM GDDR7), 32 GB RAM DDR5  
**Fecha de Elaboración:** 15 de Octubre de 2026  
**Propósito:** Protocolo estandarizado de validación rápida y aceptación técnica una vez ensamblado y configurado el servidor físico definitivo.  
**Criterio Rector:** Evitar campañas de estrés agresivas durante el montaje inicial. Ejecutar un conjunto corto, representativo y progresivo de pruebas para certificar la estabilidad del LLM central compartido.

---

## 1. Alcance y Principios de la Validación

1. **Aislamiento de Métricas:** Los resultados del servidor definitivo se registrarán en una carpeta independiente (`cerebro_audacia/benchmark_recursos/resultados_servidor_5070ti/`) para no mezclar ni sobreescribir las mediciones históricas obtenidas en el equipo de desarrollo portátil (RTX 2000 Ada).
2. **Modelo Central Compartido Único:** Comprobar rigurosamente que el servidor levanta una sola instancia de `llama-server.exe` en `127.0.0.1:8080`, sirviendo a todos los agentes y usuarios concurrentes.
3. **Escalonamiento Prudente:** No saltar directamente a 10 usuarios concurrentes sin validar previamente la estabilidad a 1, 3 y 4 clientes.
4. **Criterio de Parada Inmediata:** Si en cualquier etapa la VRAM supera el 90% (~14,7 GB), la temperatura de la GPU excede 83°C o se producen errores 500/599, detener la prueba, registrar el log y diagnosticar antes de reanudar.

---

## 2. Lista de Comprobaciones Previas al Arranque (Checklist Físico y SO)

| Paso | Acción de Verificación | Comando / Herramienta | Resultado Esperado |
|---|---|---|---|
| **P-1** | Detección física de GPU | `nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv` | `NVIDIA GeForce RTX 5070 Ti, 16384 MB` (o valor equivalente reportado por el driver). |
| **P-2** | Detección de RAM del Sistema | PowerShell: `Get-CimInstance Win32_PhysicalMemory \| Measure-Object -Property Capacity -Sum` | `Sum: 34359738368` (32 GB de RAM física instalada). |
| **P-3** | Disponibilidad de CUDA y Driver | `nvcc --version` o `nvidia-smi` | CUDA Toolkit / Driver compatible con CUDA 12.4+. |
| **P-4** | Integridad del Binario y DLLs | `Test-Path cerebro_audacia/proveedor_modelo/bin/llama-server.exe` | `True` con bibliotecas `cublas64_12.dll`, `cudart64_12.dll` presentes. |
| **P-5** | Integridad del Modelo GGUF | Comprobar SHA-256 de `Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf` en `models/` | Coincidencia exacta con el hash de desarrollo (`7b064f584...`). |
| **P-6** | Puertos de Red Libres | `Get-NetTCPConnection -LocalPort 8080 -State Listen` | Sin procesos previos escuchando en el puerto 8080. |

---

## 3. Protocolo Escalonado de Validación (8 Pasos)

### Paso 1: Verificación de Detección de Hardware y Estado Basal
- **Objetivo:** Medir el consumo basal del sistema operativo en el servidor antes de cargar servicios de IA.
- **Procedimiento:** Ejecutar un muestreo de 10 segundos en reposo.
- **Criterio de Aceptación:**
  - VRAM en reposo $\le 600\text{ MiB}$.
  - RAM física libre $\ge 24\text{ GiB}$ (de los 32 GB totales).

### Paso 2: Carga del Servicio LLM Central Compartido
- **Objetivo:** Iniciar `llama-server.exe` con el perfil `servidor_rtx5070ti` y certificar que solo existe un proceso en memoria.
- **Configuración Inicial Recomendada en Servidor:**
  ```json
  {
    "modelo": "Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf",
    "n_ctx": 16384,
    "parallel": 2,
    "flash_attn": true,
    "kv_8bits": true,
    "n_gpu_layers": 33
  }
  ```
  *(Nota: Gracias a los 16 GB de VRAM, el servidor permite un contexto expandido de 16k tokens y 2 ranuras nativas en paralelo).*
- **Comando:**
  `powershell -File cerebro_audacia/proveedor_modelo/iniciar_llama_server.ps1 -Perfil servidor_rtx5070ti -Esperar`
- **Criterio de Aceptación:**
  - Proceso único `llama-server.exe` visible en el Administrador de Tareas.
  - VRAM ocupada tras la carga: entre 5.300 y 6.200 MiB.
  - Margen libre en GPU $\ge 10.000\text{ MiB}$ (~10 GB de VRAM libre).
  - Endpoint `http://127.0.0.1:8080/health` responde `{"status": "ok"}` o `{"status": "ready"}`.

### Paso 3: Petición Corta Individual (Warmup y Línea Base)
- **Objetivo:** Medir la latencia base en frío y en caliente con un único cliente.
- **Procedimiento:** Enviar una consulta breve (Clase A_corta).
- **Criterio de Aceptación:**
  - Latencia total $\le 2,5\text{ s}$.
  - TTFT (Time-To-First-Token) $\le 0,35\text{ s}$.
  - Rendimiento $\ge 35\text{ tokens/s}$.
  - 0 errores HTTP.

### Paso 4: Prueba Representativa de 3 Solicitudes Simultáneas
- **Objetivo:** Validar la concurrencia de nivel intermedio para tres personas consultando simultáneamente.
- **Procedimiento:** Lanzamiento sincronizado por barrera de 3 clientes virtuales con consultas mixtas (A_corta, B_mediana, C_larga_rag). 2 rondas.
- **Criterio de Aceptación:**
  - 100% de solicitudes completadas con código HTTP 200.
  - Latencia P50 $\le 4,5\text{ s}$.
  - TTFT P50 $\le 2,0\text{ s}$.
  - Sin incremento acumulativo de VRAM tras finalizar la ráfaga.

### Paso 5: Prueba Representativa de 4 Solicitudes Simultáneas
- **Objetivo:** Validar la capacidad en el umbral típico de una exhibición con 4 visitantes concurrentes.
- **Procedimiento:** Lanzamiento sincronizado por barrera de 4 clientes virtuales (A, B, C, D). 2 rondas.
- **Criterio de Aceptación:**
  - Latencia P50 $\le 6,5\text{ s}$.
  - Latencia máxima observada $\le 12,0\text{ s}$.
  - Throughput agregado $\ge 35\text{ tokens/s}$.

### Paso 6: Prueba de Carga del Objetivo Central (10 Solicitudes Simultáneas)
- **Condición Previa Obligatoria:** Los Pasos 4 y 5 deben haber concluido con 100% de éxito.
- **Procedimiento:** Ráfaga coordinada de 10 clientes enviando peticiones simultáneas distribuidas en el catálogo de prompts. 2 rondas.
- **Criterio de Aceptación:**
  - 100% de solicitudes resueltas (0 timeouts, 0 desconexiones).
  - VRAM pico durante la ráfaga $\le 8.500\text{ MiB}$ (quedando $\ge 7,5\text{ GB}$ libres en la RTX 5070 Ti).
  - Recuperación de VRAM inmediata al estado basal post-inferencia.

### Paso 7: Validación de Uso Sostenido (10 Usuarios, 5 a 10 Minutos)
- **Procedimiento:** Ejecutar 10 usuarios virtuales con pausas de reflexión (1,5 a 3,5 s) durante 5 minutos.
- **Criterio de Aceptación:**
  - Estabilidad térmica continua de GPU.
  - VRAM libre estabilizada sin fugas de memoria ($\Delta\text{VRAM} \le 150\text{ MiB}$).
  - Cero fallos HTTP o caídas del servicio.

### Paso 8: Confirmación de Servicio Multiagente Integrado
- **Objetivo:** Certificar que el backend de Django (Uvicorn HTTPS) puede servir a los agentes reutilizando el LLM central sin colisiones.
- **Procedimiento:**
  1. Iniciar el backend Django en `https://127.0.0.1:8000`.
  2. Enviar consultas simultáneas desde dos clientes autenticados de Inventario y dos visitantes de HACU.
  3. Verificar que ambas rutas consumen el puerto 8080 sin instanciar pesos locales.
- **Criterio de Aceptación:**
  - Ambas peticiones se resuelven en paralelo o encoladas ordenadamente.
  - Ningún worker web eleva el consumo de VRAM por encima de lo atribuido a Whisper STT.

---

## 4. Criterios para Decidir si se Requiere Ampliar Pruebas

| Escenario Observado en el Servidor | Diagnóstico Técnico | Acción Requerida |
|---|---|---|
| **Todas las etapas pasan holgadamente** (Latencias bajas, 0 errores, VRAM < 8 GB). | **Aprobación Definitiva.** El hardware y software cumplen plenamente los requisitos. | **Detener pruebas.** Pasar a fase de despliegue en producción. No forzar cargas sintéticas innecesarias. |
| **Latencia P95 a 10 usuarios supera 20 segundos**, pero sin errores ni problemas de memoria. | Contención en ranuras de inferencia. La GPU tiene potencia pero las colas se acumulan. | Probar elevar `--parallel` a 3 o 4 slots, aprovechando que sobran ~8 GB de VRAM en la RTX 5070 Ti. Repetir únicamente el Paso 6. |
| **Aparecen errores HTTP 500 / Timeout en ráfagas de 10 usuarios.** | El timeout del cliente HTTP en el backend es inferior a la espera en cola. | Ajustar el timeout del cliente en Django a 60 segundos y habilitar streaming progresivo al usuario. |
| **La VRAM crece progresivamente durante el uso sostenido.** | Posible fuga en el caché KV o retención de slots por conexiones caídas. | Verificar parámetro `--sleep-idle-seconds` y `--cache-idle-slots` en `llama-server`. |

---

## 5. Plantilla de Registro de Resultados del Servidor

```markdown
# REGISTRO DE VALIDACIÓN — SERVIDOR FÍSICO RTX 5070 Ti
- **Fecha de Ensamblaje y Prueba:** [AAAA-MM-DD]
- **Técnico Responsable:** [Nombre / Cargo]
- **GPU Detectada:** [Modelo exacto reportado por nvidia-smi]
- **VRAM Total:** [MiB] | VRAM Basal: [MiB]
- **RAM Física:** [GiB] | RAM Basal SO: [GiB]

### Resumen de Resultados
- Paso 3 (1 Cliente): Latencia = ___ s | TTFT = ___ s | Rendimiento = ___ t/s
- Paso 4 (3 Clientes): P50 = ___ s | Max = ___ s | Tasa Éxito = 100%
- Paso 5 (4 Clientes): P50 = ___ s | Max = ___ s | Tasa Éxito = 100%
- Paso 6 (10 Clientes Pico): P50 = ___ s | P95 = ___ s | VRAM Pico = ___ MiB
- Paso 7 (10 Usuarios Sostenido): Solicitudes Completadas = ___ | Tasa Éxito = ___%

### Veredicto del Servidor
[ ] APROBADO PARA PRODUCCIÓN
[ ] CONDICIONADO A AJUSTE DE PARÁMETROS
[ ] RECHAZADO POR FALLO DE HARDWARE / VRAM
```
