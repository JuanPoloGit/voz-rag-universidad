# AUDACIA — AUDITORÍA DE CIERRE, CORRECCIONES P0/P1 Y CERTIFICACIÓN PREPRODUCCIÓN (FASE G.1-R)

**Proyecto:** AudacIA (`voz-rag-universidad`)  
**Fecha de Cierre Técnico:** 15 de Octubre de 2026 (Auditoría Integral, Verificación Residual y Certificación Preproducción — Fase G.1-R)  
**Entorno de Desarrollo Auditado (Medición FÍSICA Directa):**  
- **Sistema Operativo:** Windows 11 Pro 64-bit (10.0.26100)
- **GPU:** NVIDIA RTX 2000 Ada Generation Laptop GPU (8.188 MiB VRAM / ~8.0 GiB GDDR6), Driver 596.58
- **Memoria RAM Física Real:** **16 GB DDR5 5600 MT/s** (1 módulo físico Samsung `M425R2GA3PB0-CWMOL` de 17.179.869.184 bytes = 15,51 GiB útiles).  
  *(Aclaración documental obligatoria: el equipo de desarrollo cuenta físicamente con 16 GB de RAM física y alcanzó el 92,0 % de carga durante pruebas previas; se prohíben expresamente campañas masivas de 10 usuarios o estrés sostenido en este portátil).*
- **Motor LLM Central Activo en Desarrollo:** `Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf` servido mediante proceso único `llama-server.exe` (build `b11669-620d2225c`, PID 7964, `n_ctx=8192`, VRAM asignada ~5.187 MiB).  
**Entorno de Producción Objetivo:** Servidor Central Institucional, NVIDIA GeForce RTX 5070 Ti (16.384 MiB VRAM GDDR7), 32 GB RAM DDR5.  
**Objetivo Operativo:** Servir hasta 10 usuarios humanos concurrentes desde múltiples clientes y diversos agentes (Visitante, Inventario/HAKU, Memoria) sin caídas del servidor web, del LLM ni de los agentes.

---

## 1. Clasificación Rigurosa del Estado Técnico (Fase G.1-R)

Se clasifica el estado de cada capacidad en tres categorías estrictas, sin inferir certificación a partir de modelos teóricos ni mocks:
1. **VERIFICADO:** Demostrado mediante pruebas automatizadas ejecutables directamente sobre el código real del repositorio.
2. **PARCIALMENTE VERIFICADO:** Verificado bajo condiciones simuladas, mocks HTTP o limitado al entorno mononodo de desarrollo.
3. **PENDIENTE EN PRODUCCIÓN:** Hipótesis y cargas que exigen el hardware físico definitivo (RTX 5070 Ti de 16 GB VRAM y 32 GB RAM).

### Matriz de Certificación Técnica

| Capacidad / Componente | Clasificación | Evidencia Ejecutable y Estado Técnico |
|---|---|---|
| **Límites de Presupuesto e Invariantes A-F (P0)** | **VERIFICADA** | Comprobado en `test_fase_g1r_residual.py` (Tests 1–5): eliminación del mínimo forzado ficticio `max(64, ...)` en `stream_chat`, control estricto de margen operacional (`MIN_TOKENS_SALIDA = 16`), excepción observable `ContextoExcedidoError` cuando el contenido obligatorio excede el contexto de la ranura, y validación estricta de parámetros en `ContextBuilder` (`n_ctx > 0`, `margen_salida > 0`, `max_tokens > 0`). Poda en parejas de historial y poda iterativa finita de RAG. |
| **Contraste Empírico con Tokenizador Real LLaMA 3.1 (P0)** | **VERIFICADA** | Comprobado en `test_fase_g1r_residual.py` (Test 6): comparación directa contra `/tokenize` de `llama-server.exe` usando la plantilla Jinja completa de LLaMA 3.1 con tokens especiales. El estimador heurístico demuestra una cota conservadora segura (margen de 1.09x a 3.20x sobre tokens reales), evitando desbordamientos de contexto. Se documenta como aproximación conservadora calibrada, sin sobrecargar la inferencia con llamadas HTTP adicionales en cada petición. |
| **Propiedad Estricta del Sondeo en HALF_OPEN y Políticas HTTP (P1)** | **VERIFICADA** | Comprobado en `test_fase_g1r_residual.py` (Tests 7 y 8): aislamiento por ticket único de petición (`ticket_sondeo`). Cancelaciones o fallos de peticiones concurrentes rechazadas no alteran el sondeo en curso del propietario legítimo. Política HTTP explícita: 400, 401, 403, 404 y 422 se clasifican como errores de cliente (no penalizan caídas de motor); 429 se mapea a `LlmSaturadoError` (backpressure); 408 y 5xx incrementan fallos de disponibilidad y abren el circuito; `GeneratorExit` libera sondeo sin computar caídas. |
| **Lector SSE y Ciclo de Vida del Socket (P1)** | **VERIFICADA** | Comprobado en `test_fase_g1r_residual.py` (Tests 9 y 10): lectura compatible con `read1` y `read`, ensamblado de líneas fragmentadas, soporte transparente de delimitadores `\n` y `\r\n`, emisión de buffers finales sin salto de línea, y plazo total monotónico que corta goteo de bytes lentos sin `\n` (anti-slowloris). Cierre ordenado de socket y liberación única de ranura. |
| **Aislamiento ASGI y Delimitación de Capas (P1)** | **VERIFICADA** | Comprobado en `test_fase_g1r_residual.py` (Test 11) y `test_fase_g1_certificacion.py` (Test 5): delimitación formal entre Capa A (pruebas unitarias), Capa B (integración HTTP local) y Capa C (campaña de producción). Con el LLM saturado al 100%, las peticiones excedentes reciben rechazo rápido en <50ms (0.04ms medidos internamente) mientras que las consultas de inventario responden en <10ms. |
| **Cancelación en 3 Niveles (A: Generador, B: Socket/Permiso, C: llama-server)** | **VERIFICADA** | Comprobado en `test_segunda_pasada_resiliencia.py` (Test 4), `test_fase_g_cierre.py` (Test 2) y `test_resiliencia_sobrecarga.py` (Escenario 6): nivel A (`close()` del generador), nivel B (cierre inmediato del socket y decremento del gobernador a 0 activos) y nivel C (ranura liberada en `llama-server.exe` en 68.75 ms y petición subsecuente exitosa). `GeneratorExit` no incrementa fallos ni penaliza el circuito. |
| **Configuración Efectiva de llama-server.exe y Asignación de Contexto** | **PARCIALMENTE VERIFICADA** | Verificado en log de arranque de `llama.cpp` (b11669-620d2225c): `-c 16384 --parallel 2` divide la memoria en `n_slots = 2, n_ctx_slot = 8192`. Cada ranura dispone de 8.192 tokens efectivos. Configuración para RTX 5070 Ti actualizada en `config_llm.json` con `Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf` y KV cache `q8_0` (VRAM estimada 6.316 MB). Pendiente validar en tarjeta física RTX 5070 Ti. |
| **Concurrencia Django/Uvicorn Multi-Worker** | **PARCIALMENTE VERIFICADA** | Verificado que las vistas son síncronas en Django ASGI (`ThreadPoolExecutor`). Para topología de `--workers 1`, el gobernador en memoria gestiona la cola central. Para `--workers N`, se documenta que la coordinación recae en las ranuras nativas de `llama-server.exe` o requiere semáforo distribuido. |
| **Seguridad de Red en Producción (Uvicorn 0.0.0.0:8000)** | **PARCIALMENTE VERIFICADA** | Verificado el script de arranque seguro `iniciar_uvicorn_https.ps1` con certificados TLS locales (`red/certificados/`). Se documentan los controles requeridos para producción: terminación TLS (HTTPS directo o proxy inverso Nginx/Caddy), restricción estricta de `llama-server.exe` a `127.0.0.1:8080` (loopback), reglas de firewall institucional para el puerto 8000 y sesiones autenticadas de Django. |
| **Campaña de Carga Nominal de 10 Usuarios (600s sostenidos)** | **PENDIENTE EN PRODUCCIÓN** | **No ejecutable en portátil de desarrollo (16 GB RAM física).** Queda reservada para ejecución física en el servidor definitivo con RTX 5070 Ti y 32 GB RAM. Los objetivos TTFT P50 <= 1.5s y Latencia P95 <= 6.0s son metas de producción que no se declaran certificadas a partir de mocks. |
| **Ráfaga de Sobrecarga de 15–20 Usuarios Concurrentes** | **PENDIENTE EN PRODUCCIÓN** | **No ejecutable en portátil de desarrollo.** Queda reservada para el servidor institucional. El mecanismo de contrapresión (HTTP 503 con `Retry-After: 5` en rechazo rápido <50ms) está verificado unitariamente. |

---

## 2. Auditoría Detallada de Correcciones Residuales (Fase G.1-R)

### 2.1 Presupuesto de Contexto y Gestión de Invariantes (P0)

- **Defecto Detectado:**
  En implementaciones anteriores, `stream_chat()` aplicaba `margen_contexto = max(64, n_ctx - estimar_tokens_prompt(messages))`. Si un prompt superaba el contexto total de la ranura (ej. 8.250 tokens en `n_ctx=8192`), la diferencia era negativa (-58), pero `max(64, -58)` asignaba artificialmente 64 tokens de salida y enviaba el prompt inválido a `llama-server.exe`, provocando una falla catastrófica de *context length exceeded* en el motor C++. Asimismo, si el contenido obligatorio (sistema + consulta) por sí solo no cabía en el contexto, no se producía una excepción controlada.
- **Solución Implementada:**
  1. En `hacu/context.py` se definió `ContextoExcedidoError(ValueError)`.
  2. En `ContextBuilder.__init__` se validan los parámetros de entrada: `n_ctx > 0`, `margen_salida_tokens > 0`, `max_prompt_chars > 0` y `margen_salida_tokens < n_ctx`.
  3. En `ContextBuilder.build_messages()`:
     - Se poda el historial en parejas (`user` + `assistant`).
     - Si aún supera el presupuesto, se aplica poda iterativa del contexto RAG (`max_chars_rag = int(max_chars_rag * 0.8)`). El bucle tiene cota finita (`max_chars_rag > 50`), evitando bucles infinitos. Si todavía no cabe, se omite el RAG por completo.
     - Si el contenido obligatorio por sí solo (sistema + consulta) supera el contexto físico de la ranura (`self._n_ctx`), se lanza de forma inmediata y observable `ContextoExcedidoError`.
  4. En `stream_chat()`, `create_chat_completion()` y `_stream_adapter()` de `hacu/llm.py`:
     - Se valida que `max_tokens > 0` (lanzando `ValueError` si es nulo o negativo).
     - Si el prompt supera `n_ctx`, se lanza `ContextoExcedidoError` antes de enviar la petición.
     - Se define `MIN_TOKENS_SALIDA = 16`. Si `margen_contexto < MIN_TOKENS_SALIDA`, se aborta controladamente con `ContextoExcedidoError`.
     - Si el margen es válido, se fija `tope_tokens = min(tope_tokens, margen_contexto)`.

### 2.2 Verificación Empírica del Estimador frente a la Plantilla Real LLaMA 3.1 (P0)

- **Metodología de Validación:**
  Se formuló un prompt multiturono completo con sistema institucional, historial, anclas y pregunta del visitante. Se formateó exactamente con los tokens delimitadores de LLaMA 3.1:
  `<|begin_of_text|><|start_header_id|>system<|end_header_id|>\n\n...<|eot_id|>`  
  Se envió al endpoint `/tokenize` de `llama-server.exe` (`Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf`) y se contrastó con `estimar_tokens_prompt()`:
  - Tokens reales en llama.cpp: 71 tokens.
  - Tokens estimados por AudacIA: 84 tokens.
  - Margen de seguridad: 1.18x conservador.
- **Directriz de Rendimiento:**
  Se documenta formalmente que el estimador es una aproximación conservadora calibrada empíricamente. Se descarta realizar llamadas HTTP a `/tokenize` en cada turno de conversación para no penalizar la latencia con viajes de ida y vuelta adicionales innecesarios.

### 2.3 Propiedad Estricta del Sondeo en HALF_OPEN y Políticas HTTP (P1)

- **Aislamiento por Ticket de Sondeo:**
  Para evitar que cancelaciones voluntarias de peticiones concurrentes rechazadas en `HALF_OPEN` liberen indebidamente el sondeo activo de otra solicitud, `LlmCircuitBreaker` ahora asocia un identificador único de propietario (`ticket_sondeo = object()`).
  - `puede_ejecutar(propietario)` registra al dueño del sondeo en `HALF_OPEN`.
  - `registrar_cancelacion(propietario)` solo libera `_sondeo_en_vuelo = False` si el llamador coincide con el propietario legítimo.
  - `registrar_fallo(excepcion, propietario)` y `registrar_exito(propietario)` ignoran llamadas de peticiones no autorizadas.
- **Política de Códigos HTTP:**
  - **400, 401, 403, 404, 422:** Clasificados explícitamente como errores de cliente o de configuración de petición. No incrementan fallos de motor ni abren el circuito.
  - **429:** Clasificado como saturación y contrapresión de cola (`LlmSaturadoError`). No penaliza la disponibilidad del motor.
  - **408 (Request Timeout) y 5xx (500, 502, 503, 504):** Clasificados como fallos de servicio. Incrementan `_fallos_consecutivos` y abren el circuito al alcanzar el umbral.
  - **`GeneratorExit`:** Clasificado como desconexión voluntaria del llamador; libera el sondeo pero nunca incrementa fallos.

### 2.4 Robustez del Lector SSE y Ciclo de Vida del Socket (P1)

- Se adaptó `_iter_lineas_sse_con_timeout()` para soportar tanto `resp.read1` como `resp.read` estándar.
- Se implementó tolerancia a saltos de línea tanto `\n` como `\r\n` (CRLF).
- Se garantizó la emisión de líneas residuales sin delimitador final al terminar el stream.
- Se verificó que las lecturas lentas sin saltos de línea (ataque Slowloris) sean interrumpidas estrictamente por el plazo monotónico absoluto (`time.monotonic() >= deadline`).

### 2.5 Delimitación de Evidencia ASGI y Seguridad de Red

- **Delimitación de Tres Capas:**
  - **Capa A (Pruebas Unitarias):** Pruebas que invocan directamente vistas o métodos de servicio (`tests.py`, `test_contrato_agente.py`).
  - **Capa B (Integración Local HTTP):** Pruebas que simulan peticiones concurrentes a la aplicación local con el gobernador LLM saturado. Se midió un rechazo rápido en <50ms (0.04ms) y atención de consultas de stock en <10ms.
  - **Capa C (Campaña Física en Producción):** Requiere despliegue en el servidor definitivo con RTX 5070 Ti y 32 GB RAM.
- **Seguridad en Despliegue de Red:**
  El comando de producción escucha en `0.0.0.0:8000`. Se definen los siguientes requisitos obligatorios de seguridad:
  1. Uso del script `iniciar_uvicorn_https.ps1` con certificados TLS locales (`red/certificados/`) o despliegue tras proxy inverso (Nginx / Caddy) con terminación SSL/TLS.
  2. Restricción del puerto del LLM (`llama-server.exe`) estrictamente a la interfaz local `127.0.0.1:8080`, bloqueando cualquier acceso desde `0.0.0.0`.
  3. Reglas de firewall del sistema operativo (Windows Defender Firewall) limitando el puerto 8000 a la subred de la institución.
  4. Autenticación y control de acceso basados en sesión de Django (`LoginRequiredMixin`, `HakuSessionMiddleware`) y CSRF activado.

---

## 3. Matriz de Resultados de Todas las Suites de Regresión

Se ejecutaron las 6 suites de prueba consolidadas en el repositorio. Las **97 pruebas automatizadas pasaron al 100% con cero fallos y cero regresiones**:

| Suite de Prueba | Archivo de Prueba | Pruebas | Duración | Estado |
|---|---|---|---|---|
| **Verificación Residual Fase G.1-R** | `test_fase_g1r_residual.py` | 11 / 11 | 0.600 s | **100% OK** |
| **Certificación Fase G.1** | `test_fase_g1_certificacion.py` | 9 / 9 | 1.933 s | **100% OK** |
| **Cierre Técnico Fase G** | `test_fase_g_cierre.py` | 8 / 8 | 3.246 s | **100% OK** |
| **Resiliencia Segunda Pasada (Fase F)** | `test_segunda_pasada_resiliencia.py` | 8 / 8 | 1.768 s | **100% OK** |
| **Batería de Resiliencia y Sobrecarga** | `test_resiliencia_sobrecarga.py` | 12 / 12 | 16.200 s | **100% OK** |
| **Contratos y Resiliencia de Agentes** | `cerebro_audacia/pruebas/test_*.py` | 49 / 49 | 1.120 s | **100% OK** |
| **TOTAL GENERAL** | **6 Suites Consolidadas** | **97 / 97** | **24.867 s** | **100% APROBADO** |

---

## 4. Protocolo y Comandos Definitivos para Producción (RTX 5070 Ti)

### 4.1 Comandos de Arranque del Motor LLM Central

```powershell
# En el servidor institucional (RTX 5070 Ti 16 GB VRAM, 32 GB RAM DDR5):
.\llama-server.exe `
    -m "modelos/Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf" `
    -c 16384 `
    --parallel 2 `
    --cache-type-k q8_0 `
    --cache-type-v q8_0 `
    --n-gpu-layers 99 `
    --host 127.0.0.1 `
    --port 8080 `
    --cont-batching
```

### 4.2 Variables de Entorno de Producción (`.env`)

```ini
AUDACIA_LLM_HOST=http://127.0.0.1:8080
AUDACIA_LLM_MAX_CONCURRENCY=2
AUDACIA_LLM_MAX_QUEUE=10
AUDACIA_LLM_CONNECT_TIMEOUT=5.0
AUDACIA_LLM_READ_TIMEOUT=15.0
AUDACIA_LLM_TOTAL_TIMEOUT=60.0
AUDACIA_LLM_N_CTX=8192
AUDACIA_LLM_MARGEN_SALIDA=1500
```

### 4.3 Comando de Arranque Seguro de Django ASGI con HTTPS

```powershell
# Opción 1: Mediante script nativo seguro con certificados TLS
powershell -ExecutionPolicy Bypass -File .\iniciar_uvicorn_https.ps1 -HostIp 0.0.0.0 -Puerto 8000

# Opción 2: Mediante Uvicorn monoproceso directo tras proxy inverso
uvicorn inventario_audacia.asgi:application `
    --host 0.0.0.0 `
    --port 8000 `
    --workers 1 `
    --lifespan off
```

---

## 5. Criterio de Cierre Técnico de la Fase G.1-R

La revisión residual y auditoría de cierre preproducción queda **completada y aprobada a nivel de código**:
1. Todos los defectos reproducibles (P0 y P1) han sido corregidos con cambios mínimos e instrumentados con pruebas automáticas directas.
2. La totalidad de las 97 pruebas del repositorio pasan con 100% de éxito.
3. Se delimita rigurosamente que las pruebas nominales de 10 usuarios durante 600 segundos y las ráfagas de 15–20 usuarios permanecen como **PENDIENTE EN PRODUCCIÓN** hasta su ejecución física en el servidor definitivo (RTX 5070 Ti y 32 GB RAM).
