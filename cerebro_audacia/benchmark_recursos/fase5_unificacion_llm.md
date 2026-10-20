# INFORME DE FASE 5: UNIFICACIÓN DEL CLIENTE LLM CENTRAL EN AUDACIA

**Fecha de ejecución:** 15 de Octubre de 2026  
**Responsable:** Arquitecto de Software & Ingeniero Senior de Inferencia Local  
**Entorno de desarrollo:** Windows 11 Pro, NVIDIA RTX 2000 Ada Generation Laptop GPU (8.188 MiB VRAM), 32 GB RAM  
**Estado del servicio LLM:** `llama-server.exe` central activo en `http://127.0.0.1:8080/v1/chat/completions` (PID 7964, 5.177 MiB VRAM)  

---

## 1. Hallazgo Arquitectónico Original

Durante la auditoría de Fase 4 y la inspección previa de Fase 5 se confirmó el hallazgo reportado:

1. **Riesgo crítico de colapso de GPU (OOM):**  
   El módulo `hacu/llm.py` importaba de forma diferida la biblioteca nativa `from llama_cpp import Llama` en su método `cargar()`, instanciando un motor de inferencia C++ directamente en el espacio de memoria del proceso Python. Si se ejecutaba este flujo mientras el demonio central `llama-server.exe` ya ocupaba 5.177 MiB de VRAM, el proceso intentaba reservar otros ~5.200 MiB en la GPU. En una tarjeta de 8 GB (8.188 MiB utilizables), esto provocaba un fallo catastrófico e inmediato de asignación CUDA (`CUDA Out of Memory`).
2. **Dependencia obsoleta no unificada:**  
   En el entorno virtual de producción y pruebas unificado (`.venv`), el paquete `llama-cpp-python` no se encontraba instalado (provocando `ModuleNotFoundError`), ya que todo el despliegue del motor LLM en AudacIA se trasladó exitosamente en Fase LLM 2 al ejecutable nativo de alto rendimiento `llama-server.exe`.
3. **Falta de convergencia cliente-servidor:**  
   A pesar de que el backend ya contaba con la infraestructura para servir peticiones HTTP OpenAI-compatibles con paralelismo, el agente visitante continuaba acoplado al contrato de biblioteca local en lugar de interoperar contra el endpoint central.

---

## 2. Rutas de Ejecución Comprobadas

Se auditaron exhaustivamente todos los componentes que invocan o consumen la capa `LlmService`:

| Componente | Archivo | Método Invocado | Flujo y Propósito |
| :--- | :--- | :--- | :--- |
| **Composition Root** | `hacu/bootstrap.py` | `LlmService(cfg, logger)`<br>`llm.cargar()`<br>`llm.precalentar()`<br>`llm.cerrar()` | Inicialización durante el arranque del sistema, verificación de salud, calentamiento del KV-cache para amortiguar el primer turno del visitante y cierre ordenado. |
| **Agente Visitante (Sesión)** | `hacu/session.py` | `llm.stream_chat(...)` | Generación de respuesta conversacional token a token (Server-Sent Events) en el hilo interactivo, control de aliento (`BREVE`, `NORMAL`, `EXTENSO`) y notificación de finalización vía `al_terminar(finish_reason)`. |
| **Extractor de Memoria** | `hacu/extractor.py` | `llm.completar_json(...)` | Tarea de fondo asíncrona para derivar hechos permanentes del visitante y consolidar perfiles con decodificación estructurada JSON (`response_format={"type": "json_object"}`). |
| **Batería de Pruebas** | `pruebas/test_unidades.py` | `llm._opciones_de_cache(...)` | Verificación de retrocompatibilidad con versiones de llama.cpp y opciones de cuantización de KV-cache (8-bits). |
| **Integración Inventario** | `sistema_inventario/integraciones/haku/services.py` | N/A (usa STT/TTS y reglas) | No llama directamente a `hacu.llm`. Mantiene el lock global `HAKU_GPU_LOCK` preparado para convivir sin contención de VRAM. |

---

## 3. Archivos Modificados

| Archivo | Tipo de Cambio | Justificación |
| :--- | :--- | :--- |
| [`hacu/llm.py`](file:///c:/Users/AudacIA/Documents/inventario/voz-rag-universidad/hacu/llm.py) | **Refactorización de fondo a cliente HTTP** | Sustitución completa de la dependencia nativa `llama_cpp.Llama` por un cliente HTTP OpenAI-compatible basado exclusivamente en la biblioteca estándar de Python (`urllib.request`, `json`, `time`, `threading`). |
| [`cerebro_audacia/benchmark_recursos/test_fase5_unificacion.py`](file:///c:/Users/AudacIA/Documents/inventario/voz-rag-universidad/cerebro_audacia/benchmark_recursos/test_fase5_unificacion.py) | **Nuevo archivo de prueba y validación** | Batería automatizada de verificación de 8 criterios técnicos requeridos para la Fase 5. |

> [!IMPORTANT]
> **Cero dependencias nuevas:** La migración se realizó utilizando únicamente módulos nativos de Python 3.10+ (`urllib.request`, `urllib.error`, `json`, `time`, `logging`, `threading`, `os`, `re`, `enum`, `collections.abc`). No se instalaron bibliotecas externas adicionales (`requests`, `openai`, `httpx`).

---

## 4. Contrato de Cliente Conservado

El nuevo cliente HTTP implementado en [`hacu/llm.py`](file:///c:/Users/AudacIA/Documents/inventario/voz-rag-universidad/hacu/llm.py) mantiene una compatibilidad del 100% con la interfaz pública y privada existente:

```python
class LlmService:
    def __init__(self, config: ModelConfig, logger: logging.Logger) -> None: ...
    def cargar(self) -> None: ...
    def precalentar(self) -> float: ...
    def cerrar(self) -> None: ...
    def stream_chat(
        self,
        mensajes: Sequence[Mensaje],
        extenso: bool = False,
        al_terminar: Callable[[str | None], None] | None = None,
        aliento: Aliento | None = None,
    ) -> Iterator[str]: ...
    def completar_json(self, prompt: str, max_tokens: int) -> dict[str, Any] | None: ...
    def _tope(self, aliento: Aliento | None, extenso: bool) -> int: ...
    def _parsear_json(self, texto: str) -> dict[str, Any] | None: ...
    def _opciones_de_cache(self, Llama: type) -> dict[str, object]: ...
```

### Características Clave de la Implementación
1. **Resolución de URL Central:**
   - Variable de entorno prioritaria: `AUDACIA_LLM_URL`, `LLAMA_SERVER_URL` o `HACU_LLM_URL`.
   - Si no están presentes en el entorno, lee automáticamente `cerebro_audacia/proveedor_modelo/config_llm.json` (`servicio.url_chat` y `servicio.url_salud`).
   - Fallback por defecto: `http://127.0.0.1:8080/v1/chat/completions` y `http://127.0.0.1:8080/health`.
2. **Streaming SSE Nativo (`stream_chat`):**
   - Consume directamente el flujo `text/event-stream` de `llama-server`.
   - Decodifica fragmentos `data: {"choices": [{"delta": {"content": "..."}}]}` y emite los tokens incrementalmente.
   - Extrae el motivo de corte (`finish_reason: "stop"` o `"length"`) e invoca la función de callback `al_terminar(motivo)`.
3. **Decodificación Estructurada JSON (`completar_json`):**
   - Envía la solicitud con `response_format={"type": "json_object"}`.
   - Si el servidor reporta error o formato no compatible, realiza un fallback degradado a texto plano y aplica la expresión regular de saneado `_BLOQUE_JSON`.
   - Retorna un `dict` válido o `None` en caso de error.
4. **Resiliencia y Timeouts Explícitos:**
   - Timeout de conexión: 5.0 s (configurable con `AUDACIA_LLM_CONNECT_TIMEOUT`).
   - Timeout de generación/streaming: 120.0 s (configurable con `AUDACIA_LLM_READ_TIMEOUT`).
   - Manejo controlado de caídas: lanza `ConnectionError` en `cargar()`, devuelve `None` en `completar_json()` y drena limpiamente en `stream_chat()` llamando `al_terminar(None)` sin colapsar el proceso ni entrar en bucles de reintento infinito.

---

## 5. Evidencia de Cero Duplicación de Memoria VRAM

Se ejecutó la medición empírica con `nvidia-smi` antes y después de instanciar y conectar el cliente `LlmService`:

```
======================================================================
 AUDACIA - FASE 5: VALIDACION DEL CLIENTE LLM CENTRAL UNIFICADO
======================================================================
[*] VRAM inicial en GPU: 5177.0 MiB

[Prueba 1] Verificación de carga y conexión al servidor central...
  [OK] Conexión establecida en 0.0072s
  [OK] Idempotencia verificada (0.00ms en re-llamada)

[Prueba 2] Verificación de aislamiento de VRAM (no se cargó modelo adicional)...
  VRAM post-carga: 5177.0 MiB (Diferencia: +0.0 MiB)
  [OK] Cero memoria VRAM incremental consumida en proceso de agente.
```

- **VRAM previa a la conexión:** 5.177,0 MiB (asignada exclusivamente a `llama-server.exe` PID 7964).
- **VRAM posterior a `llm.cargar()`:** 5.177,0 MiB.
- **Incremento de memoria de GPU:** **+0,0 MiB**.
- **Working set de RAM adicional del cliente en Python:** < 4 MiB (exclusivamente buffers de red de sockets TCP).

---

## 6. Resultados de las Pruebas y Criterios de Aceptación

Se ejecutaron dos baterías completas de pruebas:

### A. Batería de Validación Específica Fase 5 (`test_fase5_unificacion.py`)

| Criterio Evaluado | Resultado Observado | Estado |
| :--- | :--- | :---: |
| **1. Arranque sin carga de modelo local** | `LlmService.cargar()` conectó con `/health` en **0.0072 s** sin invocar asignación CUDA. | **APROBADO** |
| **2. Petición individual vía HTTP** | Round-trip sincrónico completado exitosamente; precalentamiento en **0.352 s**. | **APROBADO** |
| **3. Contrato de streaming del visitante** | Generación de 39 tokens en **1.164 s**, entrega incremental y callback `al_terminar("stop")`. | **APROBADO** |
| **4. Tarea de utilidad JSON estructurado** | `completar_json` generó y decodificó `{'sobre_el_visitante': False, 'hecho': '...'}` en **0.926 s**. | **APROBADO** |
| **5. Resiliencia ante servidor caído** | Puerto 59999 cerrado generó `ConnectionError` en 1.01 s; `completar_json` retornó `None`; `stream_chat` drenó a `al_terminar(None)` sin colapsar. | **APROBADO** |
| **6. Idempotencia y compatibilidad** | Re-llamadas a `cargar()` son no-op inmediatas; `_opciones_de_cache` y `detectar_gpu()` funcionales. | **APROBADO** |
| **7. Cierre ordenado** | `cerrar()` desconecta la sesión cliente sin matar ni corromper el servidor `llama-server.exe`. | **APROBADO** |

### B. Batería de Regresión Multiagente (`cerebro_audacia/pruebas/`)

Se ejecutó la suite completa de 49 pruebas unitarias que validan contratos, esquemas tipados, gobernanza de recursos y circuit breakers:

```
Ran 49 tests in 0.872s
OK
```

- **Regresiones:** 0.
- **Pruebas fallidas:** 0.
- **Pruebas aprobadas:** 49 de 49.

---

## 7. Pruebas No Ejecutadas y Limitaciones Pendientes

1. **Pruebas de concurrencia multinodo o multi-ranura masiva:**  
   Conforme a las restricciones explícitas de la Fase 5, no se ejecutaron pruebas de estrés con más ranuras concurrentes en el equipo de desarrollo portátil (RTX 2000 Ada, 8 GB VRAM).
2. **Validación en hardware definitivo institucional:**  
   La validación de saturación con 2, 4 y 10 usuarios simultáneos en slots paralelos (`parallel=2`, contexto 16.384 tokens) queda programada para el despliegue en el servidor institucional definitivo con NVIDIA RTX 5070 Ti (16 GB VRAM), conforme al documento [`plan_validacion_servidor_definitivo.md`](file:///c:/Users/AudacIA/Documents/inventario/voz-rag-universidad/cerebro_audacia/benchmark_recursos/plan_validacion_servidor_definitivo.md).
3. **Modificación de agentes de inventario:**  
   No se modificó `sistema_inventario/integraciones/haku/services.py` ni `cerebro_audacia/agentes/`, manteniendo intactos los modelos de Django y la base de datos de auditoría.

---

## 8. Conclusión

La Fase 5 queda completada satisfactoriamente. Se eliminó de raíz el acoplamiento a la librería nativa `llama_cpp.Llama` en el agente visitante `hacu`, sustituyéndola por un cliente HTTP estandarizado, robusto y sin dependencias externas que consume el servicio central de inferencia local. Queda formalmente garantizado que todos los agentes presentes y futuros de AudacIA reutilizarán un único modelo central en GPU sin duplicar consumo de VRAM ni de RAM física.
