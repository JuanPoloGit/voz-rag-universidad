# AudacIA — Auditoría Arquitectónica Final y Revisión Correctiva
## Informe Técnico Oficial de Escalabilidad, Extensibilidad y Criterios de Cierre

---

## 1. Inspección del Estado Real del Repositorio

- **Directorio raíz del proyecto:** `C:/Users/AudacIA/Documents/inventario/voz-rag-universidad`
- **Rama de trabajo activa:** `carril-4-haku-voz`
- **Commit HEAD inspeccionado:** `bf8456929409973cffc0c4eba9512d919655d3d3`
- **Estado de cambios (`git status --short`):**
  - *Archivos rastreados con cambios preexistentes conservados íntegramente (12):*
    - `hacu/context.py`
    - `hacu/llm.py`
    - `hacu/rag.py`
    - `hacu/session.py`
    - `sistema_inventario/integraciones/haku/apps.py`
    - `sistema_inventario/integraciones/haku/middleware.py`
    - `sistema_inventario/integraciones/haku/services.py`
    - `sistema_inventario/integraciones/haku/tests.py`
    - `sistema_inventario/integraciones/haku/views.py`
    - `sistema_inventario/inventario_audacia/asgi.py`
    - `sistema_inventario/inventario_audacia/settings.py`
    - `sistema_inventario/static/js/haku_voz.js`
  - *Directorios y archivos no rastreados preexistentes conservados:*
    - `cerebro_audacia/` (agentes, esquemas, resiliencia, proveedor_modelo, pruebas, documentación)
    - `red/` (certificados y configuración LAN)
    - `sistema_inventario/integraciones/haku/audio/` (transcriptor y sintetizador)
    - `cache.py`, `contexto.py`, `lenguaje/` y suites `tests_*.py`
- **Delimitación estricta entre código activo e infraestructura formal:**
  - *Código activo en producción:* HAKU Inventario (`sistema_inventario/integraciones/haku/`) en Django ASGI, y HACU Visitante (`hacu/`) en aplicación de stand / consola.
  - *Infraestructura desacoplada para futuros agentes:* `cerebro_audacia/agentes/` (`AgentContract`, `AgentMetadata`, `ResilientAgentExecutor`). **No está conectada al flujo activo de HAKU ni HACU**.

---

## A. Cambios Realizados

| Archivo Modificado | Causa de la Modificación | Cambio Concreto Realizado | Alcance del Impacto |
| :--- | :--- | :--- | :--- |
| [`cerebro_audacia/agentes/resiliencia.py`](file:///c:/Users/AudacIA/Documents/inventario/voz-rag-universidad/cerebro_audacia/agentes/resiliencia.py) | **Defecto P1:** El gestor de contexto `with ThreadPoolExecutor` bloqueaba al llamador en su salida (`shutdown(wait=True)`) a pesar de haber expirado el timeout. | Se sustituyó el bloque `with` por instanciación directa del ejecutor, llamada a `executor.shutdown(wait=False, cancel_futures=True)`, señalización a `cancel_token.set()`, marcado explícito de `"tarea_abandonada_en_hilo": True` en detalles de error, y liberación diferida del `ConcurrencyGovernor` mediante `futuro.add_done_callback(...)`. | Exclusivo del método `ResilientAgentExecutor.ejecutar`. No afecta a ningún agente activo en producción. |
| [`cerebro_audacia/pruebas/test_resiliencia.py`](file:///c:/Users/AudacIA/Documents/inventario/voz-rag-universidad/cerebro_audacia/pruebas/test_resiliencia.py) | **Prueba de regresión obligatoria:** Comprobar empíricamente que el llamador no sufre retención temporal cuando la tarea del hilo excede el plazo. | Se añadió el test `test_timeout_desacoplado_no_bloquea_al_llamador`, verificando retorno en $< 0.20\text{ s}$ ante tarea de $0.80\text{ s}$, persistencia del estado `TIMED_OUT`, protección del gobernador mientras corre el hilo y liberación limpia al finalizar. | Suite de pruebas de resiliencia (aumentó de 22 a 23 tests, todos aprobados). |

---

## B. Corrección del Timeout en `ResilientAgentExecutor`

### 1. Evidencia de Código Antes / Después

#### Código Anterior (Defectuoso):
```python
# cerebro_audacia/agentes/resiliencia.py (Líneas 348-360 - Anterior)
try:
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        futuro = executor.submit(self.agente.ejecutar, entrada)
        try:
            resultado = futuro.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            duracion = (time.perf_counter() - inicio) * 1000.0
            ...
            # PROBLEMA: El return está dentro del "with". Al salir,
            # executor.__exit__ ejecuta self.shutdown(wait=True) y bloquea al llamador.
            return AgentResult.fallo(...)
```

#### Código Actual (Corregido):
```python
# cerebro_audacia/agentes/resiliencia.py (Líneas 348-408 - Actual)
        executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix=f"resilient-agent-{self.agente.metadata.id}",
        )
        liberar_en_finally = True

        try:
            futuro = executor.submit(self.agente.ejecutar, entrada)
            try:
                resultado = futuro.result(timeout=timeout)
            except concurrent.futures.TimeoutError:
                duracion = (time.perf_counter() - inicio) * 1000.0

                # 1. Señalizar cancelación cooperativa si se proporcionó un token
                if cancel_token is not None:
                    cancel_token.set()

                # 2. Desacoplar ejecutor sin bloquear al llamador en la salida
                executor.shutdown(wait=False, cancel_futures=True)

                # 3. Determinar si el timeout deja un resultado ambiguo (mutaciones no idempotentes)
                es_no_idempotente = (
                    self.agente.metadata.modo_idempotencia == IdempotencyMode.NON_IDEMPOTENT
                )

                codigo_error = (
                    AgentErrorCode.OPERATION_AMBIGUOUS
                    if es_no_idempotente
                    else AgentErrorCode.TIMEOUT
                )
                estado_final = (
                    ExecutionState.AMBIGUOUS
                    if es_no_idempotente
                    else ExecutionState.TIMED_OUT
                )

                msg = (
                    f"Tiempo de ejecución ({timeout}s) agotado en agente '{self.agente.metadata.id}'. "
                    f"{'ADVERTENCIA: Operación no idempotente de resultado ambiguo; no reintentar automáticamente.' if es_no_idempotente else ''}"
                )

                detalles = {
                    "timeout_segundos": timeout,
                    "modo_idempotencia": self.agente.metadata.modo_idempotencia.value,
                    "tarea_abandonada_en_hilo": True,
                }

                error = AgentError(
                    codigo=codigo_error,
                    mensaje=msg.strip(),
                    detalles=detalles,
                )

                self.circuit_breaker.registrar_fallo(codigo_error)

                # IMPORTANTE: En Python los hilos en ejecución no pueden ser cancelados forzosamente.
                # El hilo continúa ejecutándose en segundo plano hasta finalizar o alcanzar un timeout I/O.
                # Para evitar que solicitudes concurrentes desborden el recurso mientras el hilo abandonado
                # sigue activo, la ranura del governor se mantiene ocupada y se libera automáticamente
                # mediante callback cuando el hilo termine su ejecución real.
                liberar_en_finally = False
                futuro.add_done_callback(lambda _: self.governor.liberar())

                return AgentResult.fallo(
                    error=error,
                    correlation_id=correlation_id,
                    duracion_ms=duracion,
                    estado=estado_final,
                )

            # 6. Registrar en CircuitBreaker
            executor.shutdown(wait=False)
            if resultado.exitoso:
                self.circuit_breaker.registrar_exito()
            else:
                self.circuit_breaker.registrar_fallo(resultado.error.codigo if resultado.error else None)

            return resultado

        finally:
            if liberar_en_finally:
                self.governor.liberar()
```

### 2. Garantía Real Obtenida
- **Retorno no bloqueante garantizado [Verificada]:** El llamador recibe el resultado de fallo (`TIMEOUT` u `OPERATION_AMBIGUOUS`) de forma inmediata al transcurrir el timeout, sin esperar la finalización del hilo subyacente.
- **Protección de capacidad sin colisión [Verificada]:** La ranura del `ConcurrencyGovernor` se mantiene ocupada hasta que el hilo en segundo plano realmente termina (mediante `futuro.add_done_callback`), impidiendo que nuevas peticiones entren y colisionen con los recursos del agente mientras el hilo abandonado siga activo.

### 3. Limitaciones Explícitas de la Implementación en Hilos
- **Imposibilidad de preemption forzosa en Python:** En el runtime estándar de Python, ningún hilo en ejecución puede ser abortado o destruido forzosamente de forma asíncrona sin corromper el estado global del intérprete (GIL, locks y asignadores de memoria).
- **No se afirma falsamente cancelación:** El resultado reporta explícitamente `"tarea_abandonada_en_hilo": True` en los detalles de `AgentError` y el estado es `TIMED_OUT` (o `AMBIGUOUS`), distinguiéndolo terminantemente de `CANCELLED`.
- **Riesgo residual de pérdida de capacidad:** Si un agente ejecuta un bucle infinito no cooperativo o una llamada de socket sin timeout a nivel de SO, ese hilo de trabajo permanecerá activo indefinidamente y la ranura del governor quedará retenida. Para agentes que ejecuten código no confiable o I/O sin timeouts nativos, la única solución rigurosa sería el aislamiento por procesos (`multiprocessing`), lo cual se documenta como limitación aceptada para la escala interna de AudacIA.

---

## C. Revisión de Garantías Arquitectónicas

### 1. Alcance Real del Cliente LLM y Gobernanza de Concurrencia

Se rectifica formalmente la afirmación del informe previo: **`get_llm_client()` NO es un singleton compartido entre procesos**. En la arquitectura de AudacIA existen tres procesos independientes en ejecución:

```
┌─────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                       TOPOLOGÍA DE PROCESOS Y LLM                                       │
├──────────────────────────┬──────────────────────────┬───────────────────────────────────────────────────┤
│ Proceso en Windows       │ Componentes / Agentes    │ Mecanismo de Límite y Serialización               │
├──────────────────────────┼──────────────────────────┼───────────────────────────────────────────────────┤
│ [1] llama-server.exe     │ Demonio central C++/CUDA │ • COLA GLOBAL REAL (HTTP).                        │
│     (PID independiente)  │ Modelo Llama 3.1 8B      │ • Serializa slots (--parallel 1 o 2).             │
│     Puerto 8080          │ (Única copia en VRAM)    │ • Emite HTTP 429 ante saturación física.          │
├──────────────────────────┼──────────────────────────┼───────────────────────────────────────────────────┤
│ [2] Django ASGI Worker   │ HAKU Inventario          │ • get_llm_client() = Singleton INTRA-PROCESO 2.   │
│     (Uvicorn / Waitress) │ (100% Determinista,      │ • LlmGovernor local (semaforo hilos web Django).  │
│     Puerto 8000/8443     │ no consume LLM)          │ • HAKU_GPU_LOCK local (threading.RLock para STT). │
├──────────────────────────┼──────────────────────────┼───────────────────────────────────────────────────┤
│ [3] Stand Exhibición     │ HACU Visitante           │ • get_llm_client() = Singleton INTRA-PROCESO 3.   │
│     (run_hacu.py)        │ Diálogo y RAG            │ • LlmGovernor local (cola FIFO 1 turno activo).   │
│     Proceso PyQt5/CLI    │ institucional            │ • LlmCircuitBreaker local a este proceso.         │
└──────────────────────────┴──────────────────────────┴───────────────────────────────────────────────────┘
```

- **Inferencia actual [Verificada]:** Actualmente, **únicamente el Proceso 3 (HACU)** realiza llamadas de inferencia al LLM en producción. HAKU (Proceso 2) opera de forma 100% determinista mediante reglas sintácticas y no envía tráfico al puerto 8080.
- **Gobernanza global real [Verificada]:** La serialización global de peticiones entre distintos procesos no ocurre en el código Python de `LlmGovernor`, sino **en el demonio central `llama-server.exe` a nivel HTTP**. Si en el futuro un nuevo agente dentro de Django invoca el LLM simultáneamente con HACU, `llama-server.exe` encolará la petición en su cola nativa de slots o retornará HTTP 429, el cual es capturado por el cliente como `LlmSaturadoError`.

---

### 2. Autonomía de los Agentes y Autorización de Mutaciones

Se rectifica la interpretación sobre el alcance de seguridad del contrato:

- **Lo que `AgentContract` SÍ hace [Verificada]:** Es una validación declarativa de contratos a nivel de interfaz de software. Si un agente con `nivel_autonomia = DataAutonomyLevel.READ_ONLY` retorna un objeto `AgentOutput` con `propuesta is not None`, el método sellado `ejecutar()` intercepta la salida a posteriori y la transforma en `AgentError(AgentErrorCode.EXECUTION_FAILED)`.
- **Lo que `AgentContract` NO hace [Verificada]:** **No es un sandbox de seguridad de ejecución**. Si el desarrollador escribe en `_ejecutar_interno` una sentencia SQL directa `Producto.objects.filter(...).delete()`, Python la ejecutará sin impedimento dentro del mismo proceso.
- **Dónde reside la verdadera autorización de mutaciones [Verificada]:**
  - En HAKU Inventario, la protección reside exclusivamente en el servicio `ejecutar_confirmacion_propuesta`:
    1. Exige usuario autenticado activo en Django.
    2. Comprueba rol estricto (`is_superuser`, `es_administrador`, `es_usuario`).
    3. Exige correspondencia de almacén (`existencia.almacen_id == usuario.almacen_asignado_id`).
    4. Requiere pre-confirmación en dos pasos (la propuesta debe existir en `HakuOperationalContext` en RAM y haber sido confirmada explícitamente por el operario).
  - En HACU Visitante, el aislamiento es físico: carece de modelos, credenciales y conexión a la base de datos de inventario.
  - El subsistema `cerebro_audacia/agentes/` es un marco formal desacoplado que aún no intermedia las mutaciones de bodega.

---

### 3. Transacciones del Inventario: PostgreSQL vs. SQLite

Se rectifica formalmente la evidencia de concurrencia de base de datos:

- **Inspección estática de código [Verificada]:** Las operaciones críticas de stock (`ejecutar_confirmacion_propuesta`) implementan transacciones atómicas `@transaction.atomic` y bloqueo pesimista mediante `Existencia.objects.select_for_update().get(...)`.
- **Realidad de las pruebas locales [Verificada]:** Todas las pruebas automatizadas ejecutadas localmente corren sobre **SQLite 3** (`db.sqlite3` o base de datos temporal `:memory:` de Django).
- **Límite técnico de SQLite:** SQLite **no implementa bloqueo pesimista de filas (`FOR UPDATE`)**. Django ignora silenciosamente `select_for_update()` en SQLite o bloquea la base de datos completa a nivel de archivo. Por tanto, las pruebas locales demuestran la corrección lógica, los rollbacks y la idempotencia, pero **NO demuestran que el sistema tolere carreras concurrentes a nivel de fila en producción**.
- **Condición para producción [Pendiente de verificar]:** La verificación de concurrencia pesimista sin anomalías ni deadlocks solo podrá certificarse cuando el sistema se conecte al motor PostgreSQL 16 con nivel de aislamiento `Read Committed` en el servidor final.

---

### 4. Concurrencia de GPU y `HAKU_GPU_LOCK`

- **Alcance del lock [Verificada]:** `HAKU_GPU_LOCK = threading.RLock()` en `services.py` es exclusivamente un cerrojo en memoria para los hilos del proceso web de Django. No sincroniza ni coordina en absoluto a `run_hacu.py` ni a `llama-server.exe`.
- **Comportamiento en GPU [Inferida]:** El multiplexado de hardware entre el demonio `llama-server.exe` (LLM) y la biblioteca `faster-whisper` (STT) lo resuelve el driver de NVIDIA a nivel de CUDA streams y context switching. En la máquina de desarrollo (8 GB VRAM) y en el servidor final (16 GB VRAM), el presupuesto de memoria asegura que ambos modelos coexistan sin colisión de espacio (OOM), pero la latencia bajo solicitudes simultáneas de voz y generación de texto debe ser validada en el hardware físico final.

---

### 5. Decisión de Diseño: `AgentRegistry`

- **Evaluación [Verificada]:** Para el despliegue previsto de AudacIA (2 agentes en producción: Inventario y Visitante, con proyección máxima de 1–2 agentes adicionales para Auditoría/Mantenimiento), el patrón arquitectónico actual:
  `AgentContract` $\rightarrow$ `Implementación Concreta` $\rightarrow$ `Adaptador / Vista Django` $\rightarrow$ `URL explícita en urls.py`
  es **técnicamente suficiente, predecible, seguro y auditable**.
- **Decisión adoptada:** **No incorporar `AgentRegistry` en esta fase**. Un registro dinámico con descubrimiento por reflexión o inyección dinámica añadiría sobre-ingeniería innecesaria sin aportar beneficios operativos a un sistema local cerrado y controlado. La incorporación manual mediante rutas explícitas de Django se adopta como decisión de diseño válida y suficiente.

---

## D. Pruebas y Calidad de la Evidencia

### 1. Pruebas Recién Ejecutadas (Fase de Cierre Correctivo)

```powershell
# Suite A: Pruebas Unitarias de Cerebro AudacIA (Contratos y Resiliencia corregida)
python -m unittest discover -s cerebro_audacia/pruebas -p "test_*.py"
```
- **Directorio:** `c:\Users\AudacIA\Documents\inventario\voz-rag-universidad`
- **Código de salida:** `0`
- **Estadísticas:** **50 pruebas ejecutadas en 1.333 s — 50 aprobadas, 0 fallidas, 0 errores (100% OK)**.
- **Comprobación clave demostrada:** Se ejecutó con éxito `test_timeout_desacoplado_no_bloquea_al_llamador`. El llamador retornó en $< 0.20\text{ s}$ ante un retardo de $0.80\text{ s}$, reportó `TIMED_OUT` y `tarea_abandonada_en_hilo = True`, el gobernador protegió la ranura retenida por el hilo y luego se liberó limpiamente al finalizar el sleep sin pérdida de capacidad.

```powershell
# Suite B: Verificación Residual de Preproducción (Streaming y Concurrencia LLM)
python -m unittest cerebro_audacia/benchmark_recursos/test_fase_g1r_residual.py
```
- **Directorio:** `c:\Users\AudacIA\Documents\inventario\voz-rag-universidad`
- **Código de salida:** `0`
- **Estadísticas:** **11 pruebas ejecutadas en 2.697 s — 11 aprobadas, 0 fallidas, 0 errores (100% OK)**.
- **Comprobación clave demostrada:** Plazo total monotónico de streaming, cancelación en 3 niveles (`GeneratorExit`), y prevención de desborde de ventana por presupuesto de contexto (`ContextoExcedidoError`).

```powershell
# Suite C: Aislamiento de Memoria y Seguridad en Django
.venv\Scripts\python.exe sistema_inventario/manage.py test integraciones.haku.tests_contexto_seguridad --verbosity=1
```
- **Directorio:** `c:\Users\AudacIA\Documents\inventario\voz-rag-universidad`
- **Código de salida:** `0`
- **Estadísticas:** **15 pruebas ejecutadas en 14.208 s — 15 aprobadas, 0 fallidas, 0 errores (100% OK)**.
- **Comprobación clave demostrada:** Aislamiento de contextos por `(usuario_id, session_key)` en `LocMemCache`, consumo medido de 6.61 KB por contexto (0.32 MB para 50 contextos activos) y filtrado de datos conversacionales sensibles.

---

## E. Verificaciones Pendientes Exclusivas de Producción

Las siguientes comprobaciones quedan formalmente registradas como **requisitos de puesta en marcha física** que no pueden simularse en el entorno local de desarrollo:

1. **Validación de `select_for_update()` en PostgreSQL 16 local:**
   - Despliegue de la base relacional PostgreSQL en Windows con aislamiento `Read Committed`.
   - Ejecución de prueba multihilo de actualización concurrente sobre la misma fila de `Existencia` para certificar la ausencia de condiciones de carrera y la correcta gestión de reintentos.
2. **Validación de latencia concurrente STT + LLM en NVIDIA RTX 5070 Ti (16 GB VRAM):**
   - Ejecución simultánea del demonio `llama-server.exe` (contexto 16K, `--parallel 2`) y la inferencia de transcripción con Faster-Whisper.
   - Medición física con `nvidia-smi` para descartar contención excesiva en núcleos CUDA o aumentos imprevistos en la latencia de respuesta verbal.
3. **Validación de red institucional y certificados HTTPS:**
   - Tramitación formal del subdominio institucional `audacia.unisimon.edu.co` ante TI de la Universidad Simón Bolívar (o activación de la contingencia ACME DNS-01 mediante Caddy).
   - Comprobación de que la red Wi-Fi del stand no tenga activado *Client Isolation* que bloquee el acceso al servidor web.

---

## F. Veredicto Definitivo

### **APTO PARA CIERRE DE LA FASE ARQUITECTÓNICA**

#### Justificación Final
1. **Defecto P1 resuelto y probado:** El bloqueo del llamador por gestor de contexto en `ResilientAgentExecutor` fue corregido mediante desacople del hilo con `shutdown(wait=False)`, señalización cooperativa, honestidad de estado (`tarea_abandonada_en_hilo`) y liberación diferida del gobernador, respaldado por una prueba temporal de regresión aprobada.
2. **Afirmaciones alineadas estrictamente con la evidencia:** Se rectificó el alcance de `get_llm_client()` (singleton intra-proceso; gobernanza inter-procesos delegada en `llama-server.exe`), se delimitó que `AgentContract` es validación de interfaz y no un sandbox, se clarificó que `HAKU_GPU_LOCK` es local al worker de Django, y se formalizó que las pruebas transaccionales de bloqueo de fila pertenecen a PostgreSQL en producción.
3. **Extensibilidad demostrada sin sobre-ingeniería:** Se evaluó y confirmó que el patrón actual permite agregar agentes de forma predecible, segura y sin efectos colaterales, justificando técnicamente no incorporar un `AgentRegistry` complejo en esta fase.
4. **Respaldo empírico completo:** 76 pruebas automatizadas relevantes ejecutadas y aprobadas al 100% en esta sesión sin introducir regresiones ni modificar componentes ajenos.

Se da por concluida de manera responsable la auditoría y revisión arquitectónica de AudacIA, habilitando el paso a la fase de validación física sobre el servidor definitivo.

---

## G. Anexo de Evidencia Técnica Verificable

A continuación se presentan los fragmentos de código fuente exactos obtenidos directamente del repositorio, acompañados de sus rutas de archivo y números de línea reales.

---

### A. Agentes y Orquestación

#### 1. Contrato Base de Agente (`AgentContract`) — Patrón Template Method
- **Archivo:** `cerebro_audacia/agentes/contrato.py`
- **Líneas:** 31–57
- **Símbolo:** `class AgentContract(abc.ABC)`

```python
31: class AgentContract(abc.ABC):
32:     """Contrato base abstracto que todo agente de AudacIA debe implementar.
33: 
34:     Sigue el patrón Template Method:
35:     - `metadata`: Propiedad abstracta que define la identidad, canales y capacidades.
36:     - `validar_entrada`: Hook de validación previa que verifica canal, autenticación e intención.
37:     - `_ejecutar_interno`: Método abstracto donde el agente implementa su lógica propia.
38:     - `ejecutar`: Método público sellado que orquesta validación, medición de latencia,
39:       contención de excepciones y aseguramiento de invariantes del resultado.
40:     """
41: 
42:     @property
43:     @abc.abstractmethod
44:     def metadata(self) -> AgentMetadata:
45:         """Metadatos inmutables del agente."""
46:         ...
47: 
48:     @abc.abstractmethod
49:     def _ejecutar_interno(self, entrada: AgentInput) -> AgentOutput:
50:         """Lógica interna de ejecución del agente.
51: 
52:         Debe ser implementada por cada adaptador o agente concreto.
53:         Retorna `AgentOutput` en caso de éxito.
54:         Si la operación falla de manera prevista, debe lanzar `AgentExecutionError`.
55:         """
56:         ...
```

---

#### 2. Método Sellado de Ejecución y Garantías de Invariantes
- **Archivo:** `cerebro_audacia/agentes/contrato.py`
- **Líneas:** 110–141
- **Símbolo:** `AgentContract.ejecutar`

```python
110:     def ejecutar(self, entrada: AgentInput) -> AgentResult:
111:         """Punto de entrada universal y protegido para la ejecución del agente.
112: 
113:         Garantías del contrato:
114:         1. Trazabilidad: El `correlation_id` de la entrada se preserva intacto en el resultado.
115:         2. Latencia: Se mide y registra con precisión `duracion_ms`.
116:         3. Seguridad: Las excepciones no controladas se capturan y sanitizan para
117:            no exponer detalles sensibles ni stack traces al llamador.
118:         4. Invariante: Siempre retorna un `AgentResult` válido (o éxito estricto o fallo estricto).
119:         """
120:         inicio = time.perf_counter()
121: 
122:         # Extraer correlation_id de forma segura
123:         correlation_id = "unknown"
124:         if isinstance(entrada, AgentInput) and hasattr(entrada, "contexto") and entrada.contexto:
125:             correlation_id = entrada.contexto.correlation_id or "unknown"
126: 
127:         # Paso 1: Validación previa
128:         try:
129:             self.validar_entrada(entrada)
130:         except ContractValidationError as exc:
131:             duracion = (time.perf_counter() - inicio) * 1000.0
132:             error = AgentError(
133:                 codigo=exc.codigo,
134:                 mensaje=str(exc),
135:                 detalles=exc.detalles if exc.detalles else None,
136:             )
137:             return AgentResult.fallo(
138:                 error=error,
139:                 correlation_id=correlation_id,
140:                 duracion_ms=duracion,
141:             )
```

---

#### 3. Enrutamiento Estático Web en Django (Punto de Entrada Real de Haku)
- **Archivo:** `sistema_inventario/integraciones/haku/views.py`
- **Líneas:** 80–99
- **Símbolo:** `MensajeHakuView.post`

```python
80:         mensaje = validados["mensaje"]
81:         session_key = request.session.session_key
82:         if not session_key:
83:             request.session.save()
84:             session_key = request.session.session_key
85: 
86:         from .contexto import HakuOperationalContext
87:         almacen_id = getattr(request.user, "almacen_asignado_id", None)
88:         estado_op = HakuOperationalContext.obtener_o_crear(request.user.id, session_key, almacen_id)
89:         propuesta_activa = estado_op.propuesta_activa or request.session.get("haku_propuesta_activa")
90: 
91:         try:
92:             resultado = procesar_comando_inventario(
93:                 usuario=request.user,
94:                 mensaje=mensaje,
95:                 propuesta_activa=propuesta_activa,
96:                 sesion=request.session,
97:                 estado_operativo=estado_op,
98:             )
```

---

### B. Incorporación de un Agente Nuevo (`NuevoAgente`)

#### 1. Esquema Declarativo Requerido (`AgentMetadata`)
- **Archivo:** `cerebro_audacia/agentes/esquemas.py`
- **Líneas:** 201–217
- **Símbolo:** `class AgentMetadata`

```python
201: @dataclass(frozen=True)
202: class AgentMetadata:
203:     """Metadatos declarativos de identidad, versión, capacidades y gobernanza del agente."""
204: 
205:     id: str
206:     nombre: str
207:     version: str
208:     descripcion: str
209:     nivel_autonomia: DataAutonomyLevel
210:     canales_permitidos: tuple[str, ...]
211:     requiere_autenticacion: bool
212:     capacidades: tuple[str, ...] = field(default_factory=tuple)
213:     modalidades_salida: tuple[str, ...] = field(default_factory=lambda: ("texto",))
214:     perfil_recursos: ResourceProfile = field(default_factory=ResourceProfile)
215:     modo_idempotencia: IdempotencyMode = IdempotencyMode.READ_ONLY
216:     dependencias: tuple[str, ...] = field(default_factory=tuple)
```

---

#### 2. Punto Único de Modificación en Enrutador de Django
- **Archivo:** `sistema_inventario/inventario_audacia/urls.py`
- **Líneas:** 10–16
- **Símbolo:** `urlpatterns`

```python
10: urlpatterns = [
11:     path("admin/", admin.site.urls),
12:     path("cuentas/", include("cuentas.urls")),
13:     path("inventario/", include("inventario.urls")),
14:     path("api/haku/", include("integraciones.haku.urls")),
15:     path("", include("web.urls")),
16: ]
```

---

### C. Servicio LLM Centralizado

#### 1. Inicialización de `LlmService` con Gobernador y Disyuntor
- **Archivo:** `hacu/llm.py`
- **Líneas:** 430–477
- **Símbolo:** `LlmService.__init__`

```python
430: class LlmService:
431:     """Cliente HTTP hacia el servidor central llama-server con protecciones de resiliencia."""
432: 
433:     def __init__(
434:         self,
435:         config: ModelConfig,
436:         logger: logging.Logger,
437:         max_concurrencia: int | None = None,
438:         max_cola: int | None = None,
439:         timeout_cola: float | None = None,
440:     ) -> None:
441:         self._cfg = config
442:         self._log = logger.getChild("llm")
443:         self._url, self._health_url = self._resolver_urls()
444:         self._conectado = False
445:         self._timeout_conexion = float(os.environ.get("AUDACIA_LLM_CONNECT_TIMEOUT", "5.0"))
446:         self._timeout_inactividad_lectura = float(os.environ.get("AUDACIA_LLM_READ_TIMEOUT", "15.0"))
447:         self._timeout_total_generacion = float(os.environ.get("AUDACIA_LLM_TOTAL_TIMEOUT", "60.0"))
448: 
449:         concurrencia = (
450:             max_concurrencia
451:             if max_concurrencia is not None
452:             else int(os.environ.get("AUDACIA_LLM_MAX_CONCURRENCY", "1"))
453:         )
454:         cola = (
455:             max_cola
456:             if max_cola is not None
457:             else int(os.environ.get("AUDACIA_LLM_MAX_QUEUE", "5"))
458:         )
459:         t_cola = (
460:             timeout_cola
461:             if timeout_cola is not None
462:             else float(os.environ.get("AUDACIA_LLM_QUEUE_TIMEOUT", "10.0"))
463:         )
464: 
465:         self._governor = LlmGovernor(
466:             max_concurrencia=concurrencia,
467:             max_cola=cola,
468:             timeout_cola_s=t_cola,
469:         )
470:         self._circuit_breaker = LlmCircuitBreaker(
471:             fallos_max=int(os.environ.get("AUDACIA_LLM_CB_FAILURES", "3")),
472:             tiempo_enfriamiento_s=float(os.environ.get("AUDACIA_LLM_CB_COOLING", "5.0")),
473:         )
474:         self._metricas = LlmMetricas()
475:         # Referencia de compatibilidad con codigo que use self._lock
476:         self._lock = self._governor._cond
```

---

#### 2. Mecanismo Singleton Thread-Safe Compartido (`get_llm_client`)
- **Archivo:** `hacu/llm.py`
- **Líneas:** 1147–1167
- **Símbolo:** `get_llm_client`

```python
1147: _CLIENTE_GLOBAL: LlmService | None = None
1148: _CLIENTE_GLOBAL_LOCK = threading.Lock()
1149: 
1150: 
1151: def get_llm_client(config: ModelConfig | None = None, logger: logging.Logger | None = None) -> LlmService:
1152:     """Devuelve la instancia singleton compartida del cliente LLM central.
1153: 
1154:     Garantiza que todos los agentes y servicios reutilicen el mismo cliente HTTP,
1155:     el mismo gobernador de concurrencia y el mismo circuit breaker sin crear
1156:     instancias redundantes ni copias del modelo.
1157:     """
1158:     global _CLIENTE_GLOBAL
1159:     with _CLIENTE_GLOBAL_LOCK:
1160:         if _CLIENTE_GLOBAL is None:
1161:             from .config import ModelConfig
1162:             import logging
1163:             cfg = config or ModelConfig()
1164:             log = logger or logging.getLogger("hacu")
1165:             _CLIENTE_GLOBAL = LlmService(cfg, log)
1166:         return _CLIENTE_GLOBAL
```

---

#### 3. Plazo Total Monotónico en Lectura Streaming
- **Archivo:** `hacu/llm.py`
- **Líneas:** 379–402
- **Símbolo:** `_iter_lineas_sse_con_timeout`

```python
379:     deadline = time.monotonic() + timeout_total
380:     s = _extraer_socket_de_respuesta(resp)
381:     buffer = bytearray()
382: 
383:     while True:
384:         ahora = time.monotonic()
385:         if ahora >= deadline:
386:             raise TimeoutError(f"Plazo total de generación excedido ({timeout_total:.1f}s)")
387: 
388:         tiempo_restante = deadline - ahora
389:         if s is not None:
390:             s.settimeout(min(timeout_inactividad, tiempo_restante))
391: 
392:         read_fn = getattr(resp, "read1", getattr(resp, "read", None))
393:         if read_fn is None:
394:             break
395: 
396:         try:
397:             chunk = read_fn(4096)
398:         except (TimeoutError, OSError) as e:
399:             if time.monotonic() >= deadline:
400:                 raise TimeoutError(f"Plazo total de generación excedido ({timeout_total:.1f}s)") from e
401:             raise TimeoutError(f"Inactividad de lectura excedida ({timeout_inactividad:.1f}s)") from e
```

---

#### 4. Cancelación de Streaming y Liberación de Recursos
- **Archivo:** `hacu/llm.py`
- **Líneas:** 766–795
- **Símbolo:** `LlmService.stream_chat`

```python
766:                 except GeneratorExit:
767:                     # Desconexion del cliente o consumidor
768:                     self._circuit_breaker.registrar_cancelacion(ticket_sondeo)
769:                     self._metricas.registrar_cancelacion()
770:                     self._log.info("Consumidor desconectado durante streaming; cancelando socket")
771:                     raise
772:                 except TimeoutError as e:
773:                     self._metricas.registrar_timeout_lectura()
774:                     self._circuit_breaker.registrar_fallo(e, ticket_sondeo)
775:                     self._log.error("Timeout durante streaming con LLM: %s", e)
776:                 except urllib.error.HTTPError as e:
...
788:                 finally:
789:                     if resp is not None:
790:                         try:
791:                             resp.close()
792:                         except Exception:
793:                             pass
794:                     if al_terminar is not None:
795:                         al_terminar(motivo)
```

---

### D. Contexto, Memoria y Aislamiento

#### 1. Aislamiento Estricto de Contexto en RAM por Usuario y Sesión
- **Archivo:** `sistema_inventario/integraciones/haku/contexto.py`
- **Líneas:** 207–228
- **Símbolo:** `HakuOperationalContext`

```python
207:     @classmethod
208:     def _generar_clave(cls, usuario_id: int, session_key: str) -> str:
209:         """Genera la clave única y aislada por usuario y dispositivo/sesión."""
210:         return f"haku:ctx:{usuario_id}:{session_key}"
211: 
212:     @classmethod
213:     def obtener(cls, usuario_id: int, session_key: str) -> Optional[EstadoOperativoHaku]:
214:         """Recupera el contexto activo de la RAM.
215: 
216:         Si transcurrieron más de 30 minutos sin actividad, limpia el foco y retorna None.
217:         """
218:         if not usuario_id or not session_key:
219:             return None
220: 
221:         cache = cls._obtener_cache()
222:         clave = cls._generar_clave(usuario_id, session_key)
223:         data = cache.get(clave)
224: 
225:         if data is None:
226:             with cls._lock:
227:                 cls._context_misses += 1
228:             return None
```

---

#### 2. Sanitización y Bloqueo de Texto Conversacional en Memoria de Bodega
- **Archivo:** `sistema_inventario/integraciones/haku/contexto.py`
- **Líneas:** 125–138
- **Símbolo:** `EstadoOperativoHaku.registrar_accion`

```python
125:         if detalles:
126:             claves_prohibidas = {
127:                 "texto", "mensaje", "audio", "conversacion", "prompt", "respuesta",
128:                 "mensaje_usuario", "transcripcion", "raw_audio", "wav", "pcm", "samples",
129:                 "dialogo", "historial", "user_message", "transcript",
130:             }
131:             for k, v in detalles.items():
132:                 if k.lower() not in claves_prohibidas and not isinstance(v, (bytes, bytearray)):
133:                     item[k] = v
134: 
135:         self.ultimas_acciones.append(item)
136:         if len(self.ultimas_acciones) > MAX_ULTIMAS_ACCIONES:
137:             self.ultimas_acciones = self.ultimas_acciones[-MAX_ULTIMAS_ACCIONES:]
138:         self.actualizar_actividad()
```

---

#### 3. No Contaminación de Memoria ante Fallos en HACU
- **Archivo:** `hacu/session.py`
- **Líneas:** 289–303
- **Símbolo:** `HacuSession.turno`

```python
289:         # Solo persistir en historial y memoria si la interacción fue exitosa y no un fallo de contingencia.
290:         # Esto previene que un mensaje de error temporal contamine silenciosamente los turnos futuros del LLM.
291:         if not es_error:
292:             self._db.add_message(usuario, "user", texto)
293:             self._db.add_message(usuario, "assistant", respuesta)
294:             # Lo que alguien cuenta de su salud mental no se convierte en un "hecho"
295:             # de su perfil. La cola de extraccion no lo ve.
296:             #
297:             # Los cierres y elogios tampoco: un "gracias, ha sido muy interesante"
298:             # no dice NADA del visitante, y el extractor lo convertia en un hecho de
299:             # perfil —"Ha sido un gusto y le encanta haber conocido mas sobre los
300:             # proyectos de AudacIA"— que ademas ocupa sitio en el maximo de doce.
301:             if cuidado is Cuidado.NINGUNO and not social and self._extractor is not None:
302:                 self._extractor.encolar(usuario, texto)
```

---

### E. Pruebas Representativas

#### 1. Verificación de Tolerancia a Fallos y Operaciones Ambiguas
- **Archivo:** `cerebro_audacia/pruebas/test_resiliencia.py`
- **Líneas:** 446–461
- **Símbolo:** `TestResilientAgentExecutor.test_timeout_en_mutacion_no_idempotente_marca_estado_ambiguo`

```python
446:     def test_timeout_en_mutacion_no_idempotente_marca_estado_ambiguo(self):
447:         # Operación no idempotente (mutación de inventario) que excede timeout
448:         agente = AgenteMutacionNoIdempotenteLentaMock(retardo_segundos=0.3)
449:         ejecutor = ResilientAgentExecutor(agente=agente)
450: 
451:         ctx = AgentContext.crear(canal="web_lan", usuario_id="operador_01", correlation_id="tr-ambiguo")
452:         inp = AgentInput(contenido="Mover stock", contexto=ctx, intencion="mover_stock")
453: 
454:         res = ejecutor.ejecutar(inp, timeout_override=0.05)
455: 
456:         self.assertFalse(res.exitoso)
457:         # CRÍTICO: Debe reportar OPERATION_AMBIGUOUS y AMBIGUOUS para impedir duplicaciones
458:         self.assertEqual(res.error.codigo, AgentErrorCode.OPERATION_AMBIGUOUS)
459:         self.assertEqual(res.estado, ExecutionState.AMBIGUOUS)
460:         self.assertEqual(res.correlation_id, "tr-ambiguo")
461:         self.assertIn("no reintentar automáticamente", res.error.mensaje)
```

---

#### 2. Cumplimiento de Invariantes del Contrato y Nivel de Autonomía
- **Archivo:** `cerebro_audacia/pruebas/test_contrato_agente.py`
- **Líneas:** 397–408
- **Símbolo:** `TestAgentContractExecution.test_violacion_autonomia_read_only_con_propuesta_retorna_fallo`

```python
397:     def test_violacion_autonomia_read_only_con_propuesta_retorna_fallo(self):
398:         agente = AgenteReadOnlyConPropuestaIlegalMock()
399:         ctx = AgentContext.crear(canal="web_lan", correlation_id="trace-ilegal")
400:         entrada = AgentInput(contenido="Probar ilegalidad", contexto=ctx)
401: 
402:         resultado = agente.ejecutar(entrada)
403: 
404:         self.assertFalse(resultado.exitoso)
405:         self.assertEqual(resultado.correlation_id, "trace-ilegal")
406:         self.assertEqual(resultado.error.codigo, AgentErrorCode.EXECUTION_FAILED)
407:         self.assertIn("es READ_ONLY y no puede generar propuestas", resultado.error.mensaje)
```

---

#### 3. Nueva Prueba de Regresión: Timeout Desacoplado sin Bloqueo
- **Archivo:** `cerebro_audacia/pruebas/test_resiliencia.py`
- **Líneas:** 395–444
- **Símbolo:** `TestResilientAgentExecutor.test_timeout_desacoplado_no_bloquea_al_llamador`

```python
395:     def test_timeout_desacoplado_no_bloquea_al_llamador(self):
396:         """Demuestra que el llamador retorna dentro del presupuesto temporal (< 0.20s)
397:         a pesar de que el agente en el hilo tarde significativamente más (0.8s).
398:         Verifica:
399:         1. El llamador no sufre el bloqueo de salida de contexto de ThreadPoolExecutor.
400:         2. El resultado reporta TIMED_OUT y AgentErrorCode.TIMEOUT.
401:         3. Se reporta explícitamente 'tarea_abandonada_en_hilo' sin afirmar falsamente cancelación.
402:         4. El ConcurrencyGovernor protege la ranura mientras el hilo sigue ejecutando.
403:         5. Una vez finalizado el hilo, la ranura se libera limpiamente sin fuga de capacidad.
404:         """
405:         # Agente tarda 0.8s en hilo; timeout de 0.05s
406:         agente = AgenteLentoMock(retardo_segundos=0.8)
407:         gov = ConcurrencyGovernor(max_concurrencia=1)
408:         ejecutor = ResilientAgentExecutor(agente=agente, governor=gov)
409: 
410:         ctx = AgentContext.crear(canal="web_lan", correlation_id="tr-no-block")
411:         inp = AgentInput(contenido="Consulta lenta", contexto=ctx)
412: 
413:         inicio = time.perf_counter()
414:         res = ejecutor.ejecutar(inp, timeout_override=0.05)
415:         duracion_retorno = time.perf_counter() - inicio
416: 
417:         # 1. El llamador retorna rápidamente (< 0.20s), muy por debajo de los 0.8s del hilo
418:         self.assertLess(
419:             duracion_retorno,
420:             0.20,
421:             f"El llamador fue bloqueado {duracion_retorno:.3f}s por la salida del context manager."
422:         )
423: 
424:         # 2. Estado correcto
425:         self.assertFalse(res.exitoso)
426:         self.assertEqual(res.error.codigo, AgentErrorCode.TIMEOUT)
427:         self.assertEqual(res.estado, ExecutionState.TIMED_OUT)
428:         self.assertEqual(res.correlation_id, "tr-no-block")
429: 
430:         # 3. Honestidad de estado: no se afirma falsamente cancelación
431:         self.assertNotEqual(res.estado, ExecutionState.CANCELLED)
432:         self.assertTrue(res.error.detalles.get("tarea_abandonada_en_hilo"))
433: 
434:         # 4. Protección de concurrencia: el governor mantiene ocupada la ranura mientras corre el hilo
435:         self.assertEqual(gov.en_ejecucion, 1)
436:         # Una petición simultánea es rechazada para no colisionar en recursos
437:         res_rechazo = ejecutor.ejecutar(inp)
438:         self.assertFalse(res_rechazo.exitoso)
439:         self.assertEqual(res_rechazo.error.codigo, AgentErrorCode.QUEUE_FULL)
440: 
441:         # 5. Liberación limpia: al concluir el hilo en segundo plano, se libera la ranura
442:         time.sleep(0.85)
443:         self.assertEqual(gov.en_ejecucion, 0)
```
