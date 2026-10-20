# AUDACIA — AUDITORÍA DE ESCALABILIDAD MULTIAGENTE Y ARQUITECTURA DEL LLM CENTRAL

**Fase:** Fase 4 — Auditoría Multiagente, Pruebas de Estrés y Escalabilidad del LLM Central  
**Fecha:** 15 de Octubre de 2026  
**Repositorio:** `voz-rag-universidad`  
**Entorno de Auditoría:** Windows 11 Pro, NVIDIA RTX 2000 Ada Generation Laptop GPU (8.188 MiB VRAM), 16 GB RAM DDR5 (15,51 GiB utilizables en Windows)  
**Servicio LLM Auditado:** `llama-server.exe` (build b11669 con CUDA 12.4), modelo `Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf`

---

## 1. Resumen Ejecutivo y Dictamen Arquitectónico

AudacIA persigue una arquitectura donde **un único LLM central compartido** sirve de cerebro a todos los agentes presentes y futuros:
- **Agente Visitante (HACU)**: Asistente conversacional con recuperación RAG y memoria episódica.
- **Agente de Inventario (HAKU)**: Asistente operativo para control de almacén, existencias, préstamos y movimientos por voz/texto.
- **Agente de Auditoría (Futuro)**: Agente analítico de solo lectura para inspección de transacciones y conciliación de saldos.
- **Cualquier agente futuro**: Debe compartir los mismos pesos del modelo en VRAM.

### Dictamen Técnico
1. **Compartición en VRAM del LLM:** Actualmente, la infraestructura local cuenta con un servidor de inferencia centralizado de alto rendimiento (`llama-server.exe`) alojado en `cerebro_audacia/proveedor_modelo/bin/`, escuchando en `http://127.0.0.1:8080`. Este proceso mantiene en GPU los 33 bloques offloaded de Llama 3.1 8B Instruct Q4_K_M ocupando **5.177 MiB de VRAM**, con un working set en RAM de ~4.847 MiB.
2. **Riesgo Crítico de Duplicación Detectado en Código:**
   - En el código de **HACU Visitante** (`hacu/llm.py`), la clase `LlmService` todavía contiene una llamada a `from llama_cpp import Llama`, instanciando el modelo directamente en el proceso Python mediante enlaces de biblioteca C/CUDA. Si un proceso ejecuta `LlmService.cargar()` mientras `llama-server` está corriendo, intentará reservar otros ~5.2 GB en la GPU, provocando un fallo inmediato por **CUDA Out of Memory** (ya que la GPU dispone de 8.188 MiB).
   - En el código de **HAKU Inventario** (`sistema_inventario/integraciones/haku/services.py`), se definió un lock `HAKU_GPU_LOCK = threading.RLock()`. Este lock es exclusivo de un solo hilo/proceso Python y no coordina procesos externos ni múltiples trabajadores web ASGI/WSGI.
3. **Riesgo en el Servicio de Transcripción (STT):**
   - El transcriptor `HakuTranscriber` (`sistema_inventario/integraciones/haku/audio/transcriber.py`) carga Faster-Whisper base (CUDA float16, ~325 MiB VRAM). Aunque usa un patrón singleton en memoria (`_instancia`), en un despliegue multi-worker de Django (ej. Uvicorn o Gunicorn con $N$ procesos), **cada worker cargará su propia copia de Whisper en GPU**, multiplicando el consumo: $4 \text{ workers} \times 325 \text{ MiB} \approx 1.300 \text{ MiB}$.
4. **Veredicto:** La arquitectura actual **puede y debe compartir un único LLM**, pero requiere formalizar que **ningún agente instancie pesos locales**. Toda llamada inferencial debe dirigirse a la API HTTP del LLM central (`http://127.0.0.1:8080/v1/chat/completions`), gobernada por los contratos de resiliencia (`cerebro_audacia/agentes/resiliencia.py`).

---

## 2. Inspección Detallada de Agentes y Componentes

### 2.1. Agente Visitante (HACU)
- **Ubicación:** `hacu/` (`cli.py`, `session.py`, `context.py`, `rag.py`, `llm.py`, `memory.py`).
- **Estado de Implementación:** **Completamente implementado y funcional** como aplicación de consola/exhibición interactiva.
- **Responsabilidad Real:** Atención conversacional para visitantes de la exhibición AudacIA y la Universidad. Informa sobre catálogo de proyectos, personas, historia y laboratorios.
- **Instrucciones y Contexto:**
  - System prompt estructurado en `hacu/prompts.py` con directrices de personalidad, brevedad, tono institucional y reglas de contención en crisis.
  - Ensamblado dinámico en `hacu/context.py` integrando: saludo/apertura, hechos aprendidos del visitante en SQLite, fragmentos documentales recuperados por RAG, historial podado de los últimos turnos y regla de cierre.
  - Longitud típica de prompt: entre 850 y 2.200 tokens.
- **Herramientas y Fuentes de Información:**
  - Corpus Markdown local en disco (`hacu/corpus/`).
  - Motor RAG local en `hacu/rag.py` sobre colecciones vectoriales ChromaDB persistentes (`audacia_knowledge` y `universidad_knowledge`).
  - Memoria episódica local en SQLite (`hacu_memory.db`) con extractor asíncrono en segundo plano (`hacu/extractor.py`).
  - Módulo de búsqueda web externa de respaldo para consultas de cultura general no cubiertas en el corpus.
- **Consultas y Modificaciones al Backend:**
  - No interactúa con la base de datos de Django ni con el ORM de inventario.
  - Modifica su propia base de datos SQLite local para registrar conversaciones y hechos aprendidos.
- **Permisos Necesarios:** Acceso público / visitante sin autenticación.
- **Mecanismo de Invocación del LLM:**
  - `LlmService.stream_chat()` en `hacu/llm.py`. Emplea un lock reentrante en Python (`threading.RLock`) que serializa la generación entre el hilo interactivo y el hilo de extracción de memoria.
  - **Vulnerabilidad de Duplicación:** Carga directa de `llama_cpp.Llama`. Para integrarse a la arquitectura central, debe sustituirse el motor interno por un cliente HTTP OpenAI-compatible que consuma `llama-server`.
- **Aislamiento de Sesiones y Documentos:**
  - Sesiones identificadas por `usuario` resuelto mediante `IdentityResolver` (nombre de pila o hash de sesión).
  - El corpus documental es estático y de solo lectura. No hay riesgo de cruce de datos confidenciales.

### 2.2. Agente de Inventario (HAKU)
- **Ubicación:** `sistema_inventario/integraciones/haku/` (`services.py`, `views.py`, `contexto.py`, `audio/transcriber.py`, `lenguaje/`).
- **Estado de Implementación:** **En desarrollo activo**. Posee la capa de audio STT, normalización lingüística regional costeña, sistema transaccional de propuestas y endpoints de integración web.
- **Responsabilidad Real:** Gestión de existencias, préstamos de equipos de laboratorio, registro de movimientos y resolución no presuntiva de siglas y códigos técnicos.
- **Instrucciones y Contexto:**
  - Normalizador determinista y fonético en `lenguaje/costeno.py`, `lenguaje/siglas.py` y `lenguaje/normalizador.py`.
  - Gestor de contexto transaccional en `contexto.py` con propuestas en caché efímera (tiempo de vida: 30 minutos).
  - Longitud típica de contexto: 350 a 850 tokens (esquemas JSON de inventario).
- **Herramientas y Fuentes de Información:**
  - Base de datos relacional de Django (PostgreSQL / SQLite) vía ORM: modelos `Producto`, `Existencia`, `Movimiento`, `Prestamo`, `Ubicacion`.
  - Motor STT local `HakuTranscriber` con Faster-Whisper base (CUDA float16).
  - Síntesis de voz planificada con Piper TTS.
- **Consultas y Modificaciones al Backend:**
  - Lectura amplia del inventario (consultas de stock, ubicación, préstamos activos).
  - Escritura transaccional en dos fases (`HakuProposalService`): primero genera una propuesta estructurada (`propuesta_id`) y solo tras confirmación explícita del usuario persiste el movimiento en base de datos.
- **Permisos Necesarios:** Usuario autenticado en Django con permisos de rol de almacén (`is_staff` o permisos específicos sobre la aplicación `inventario`).
- **Mecanismo de Invocación del LLM:**
  - Actualmente procesa las intenciones mediante análisis de patrones, reglas fonéticas y gramática de inventario sin invocar LLM para la lógica crítica de negocio.
  - Posee un lock `HAKU_GPU_LOCK` preparado para serializar llamadas al LLM cuando se incorpore la generación de lenguaje natural.
- **Riesgo de Duplicación:**
  - En LLM: Nulo si se conecta a `llama-server` vía HTTP.
  - En STT: Alto si Uvicorn corre con múltiples workers, ya que cada proceso cargará su instancia de `WhisperModel`.
- **Aislamiento de Usuarios:**
  - Aislamiento total gobernado por Django: `request.user`, `request.session`, middleware de autenticación y CSRF.
  - Las propuestas transaccionales se asocian a la sesión del usuario autenticado; un usuario concurrente no puede confirmar la propuesta de otro.

### 2.3. Agente de Auditoría (Futuro)
- **Ubicación:** No implementado aún. Se prevé su inclusión bajo `cerebro_audacia/agentes/` implementando `AgentContract`.
- **Responsabilidad Real:** Análisis de anomalías en inventario, trazabilidad de préstamos vencidos, detección de patrones sospechosos de pérdidas y generación de informes ejecutivos de control interno.
- **Herramientas:** ORM de Django en modo solo lectura (`django-auditlog`, tablas históricas de `Movimiento` y `Transaccion`).
- **Permisos:** Restringido a usuarios con rol `Auditor` o superusuarios.
- **Invocación LLM:** Consumidor cliente del LLM central vía `ResilientAgentExecutor`.

---

## 3. Matriz de Convivencia y Riesgo de Recursos

| Componente | Proceso Ejecutor | VRAM Medida (MiB) | RAM Medida (MiB) | Riesgo de Duplicación | Mitigación Arquitectónica Obligatoria |
|---|---|---|---|---|---|
| **LLM Central (Llama 3.1 8B)** | `llama-server.exe` (independiente) | **5.177 MiB** | 4.847 MiB | Crítico si los agentes usan `llama_cpp.Llama` | Centralizar como único daemon HTTP (`127.0.0.1:8080`). Prohibir importación nativa en agentes. |
| **STT (Faster-Whisper base)** | Proceso Django (Uvicorn) | **325 MiB** | ~280 MiB | Medio (1 copia por worker de Uvicorn) | Ejecutar Uvicorn con 1 worker + asincronía asyncio, o aislar STT en un micro-daemon local. |
| **Embeddings RAG (MiniLM)** | Proceso HACU | ~120 MiB (opcional) | ~470 MiB | Bajo (in-memory en CPU) | Mantener en CPU para no fragmentar VRAM de GPU. |
| **Backend Web (Django/Uvicorn)** | Worker ASGI | 0 MiB (sin GPU) | ~95 MiB | Ninguno | Sin acceso directo a VRAM; puramente HTTP/ORM. |
| **Escritorio / Sistema OS** | `dwm.exe` / OS | 232 MiB | ~9.570 MiB | N/A | Consumo basal ineludible de Windows 11. |
| **Total Coexistencia Medida** | **Sistema + LLM + STT** | **5.734 MiB** | **~14.700 MiB** | — | **Margen libre en GPU: 2.454 MiB (30,0% libre)** |

---

## 4. Análisis de Aislamiento y Seguridad entre Usuarios

Para cumplir el requisito de atender hasta 10 usuarios concurrentes sin cruce de información:

1. **Aislamiento de Contexto en el LLM Central:**
   - `llama-server` opera de manera no reentrante por ranura (*slot*). Cada solicitud HTTP entrante a `/v1/chat/completions` transporta su array completo de `messages`.
   - El servidor no almacena estado conversacional cruzado entre ranuras: al finalizar una solicitud, el contexto de esa ranura queda disponible para la siguiente petición.
   - En consecuencia, el LLM central es intrínsecamente seguro frente a fugas de memoria o contaminación cruzada entre agentes o usuarios.
2. **Aislamiento de Sesiones en el Backend:**
   - **Visitante (HACU):** Utiliza identificadores efímeros de sesión. La memoria persistente se aísla por `usuario_id` en SQLite mediante transacciones ACID locales.
   - **Inventario (HAKU):** Utiliza sesiones estándar de Django basadas en cookies seguras (`sessionid`) firmadas criptográficamente. El middleware de Django garantiza que cada petición opera sobre el `request.user` autenticado.
3. **Aislamiento de Permisos:**
   - La arquitectura separa el canal de visitante (anónimo / exhibición) del canal de inventario (autenticado y autorizado mediante RBAC).
   - El agente visitante no tiene acceso a las credenciales ni a la base de datos de inventario.

---

## 5. Hallazgos Críticos y Recomendaciones de Implementación

### Hallazgo 1: Incompatibilidad entre `hacu/llm.py` y `llama-server`
- **Problema:** `hacu/llm.py` instancia `llama_cpp.Llama` localmente. Si `llama-server` está corriendo (ocupando 5.177 MiB de los 8.188 MiB disponibles), ejecutar `hacu` provocará un error de memoria CUDA.
- **Recomendación:** Modificar `hacu/llm.py` para que utilice `urllib.request` o `requests` contra `http://127.0.0.1:8080/v1/chat/completions` con soporte de streaming (`stream=True`). De este modo, HACU se convierte en un cliente ligero que consume 0 MiB adicionales de VRAM.

### Hallazgo 2: Escalabilidad de Trabajadores Web vs STT
- **Problema:** En Uvicorn o Gunicorn, elevar el número de workers (`--workers 4`) para absorber concurrencia web provocaría que cada worker cargue `Faster-Whisper` en GPU, multiplicando por 4 el consumo de 325 MiB (~1.300 MiB) y disputando VRAM.
- **Recomendación:** Mantener la arquitectura de backend basada en **1 worker ASGI con alta concurrencia asíncrona (Uvicorn con event-loop de asyncio)**, o desacoplar la transcripción de audio en un daemon HTTP local único en el puerto `8081`, análogo a `llama-server`.

### Hallazgo 3: Gestión de Concurrencia en el LLM (`n_parallel=1` vs Ranuras)
- **Problema:** Con `n_parallel=1`, `llama-server` procesa una sola inferencia a la vez y encola las demás en memoria FIFO. Diez solicitudes concurrentes no fallan por HTTP, pero la décima solicitud experimentará el tiempo acumulado de las nueve anteriores.
- **Recomendación:** Evaluar experimentalmente en las pruebas de estrés si habilitar 2 o 4 ranuras (`--parallel 2`, `--parallel 4`) reduce la latencia percibida por usuario sin agotar los 8 GB de VRAM de la GPU de desarrollo.

---

## 6. Conclusión de la Auditoría Arquitectónica

La arquitectura de AudacIA **está en condiciones de operar con un único LLM central compartido**. No existe necesidad técnica de desplegar modelos LLM independientes para Visitante, Inventario o Auditoría. La capacidad de GPU en desarrollo (8 GB) es suficiente para albergar el LLM 8B Q4_K_M y Whisper base de forma permanente.

Las modificaciones pendientes no son de reemplazo de modelos, sino de **desacoplamiento de clientes**: migrar los agentes hacia adaptadores HTTP que consuman el servicio centralizado.
