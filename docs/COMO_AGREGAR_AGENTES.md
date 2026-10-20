# AudacIA — Guía Práctica de Extensión y Creación de Nuevos Agentes

**Propósito:** Manual técnico para que un desarrollador de sistemas, ingeniero de IA o agente autónomo de programación pueda diseñar, implementar, probar e integrar un agente nuevo en AudacIA sin romper la arquitectura existente.

---

## 1. Decisión Arquitectónica Fundamental: Un Único LLM Centralizado

Antes de escribir una sola línea de código para un nuevo agente, es obligatorio asimilar la regla arquitectónica central de AudacIA:

> **AudacIA opera con un único LLM centralizado ("Cerebro Central").**  
> Todos los agentes —HAKU Inventario, HACU Visitante y cualquier nuevo agente— utilizan **exclusivamente el Cerebro Central** como proveedor de lenguaje y razonamiento.

```text
                         AUDACIA
                            │
                            ▼
                 ┌────────────────────┐
                 │   CEREBRO CENTRAL  │
                 │                    │
                 │   ÚNICO LLM        │
                 │  llama-server.exe  │
                 │  Gobierno de VRAM  │
                 │  Concurrencia      │
                 └─────────┬──────────┘
                           │
          ┌────────────────┼────────────────┐
          ▼                ▼                ▼
        HAKU              HACU          AGENTE NUEVO
     Inventario         Visitante       (Especializado)
          │                │                │
      herramientas       RAG              herramientas
      BD inventario       memoria         reglas
      reglas negocio      voz             memoria
      cliente central     cliente central cliente central
```

### 1.1. Concepto Fundamental: Agente $\neq$ Modelo
Un agente en AudacIA **NO es un modelo de lenguaje**. Un agente es una **unidad de software especializada y autónoma** que encapsula lógica de dominio, datos y herramientas:

```text
AGENTE NUEVO
 ├── 1. Instrucciones de dominio y prompts especializados
 ├── 2. Estado de sesión y memoria operativa local
 ├── 3. Herramientas ejecutables (JSON Schema / funciones Python)
 ├── 4. Acceso a datos o RAG documental (si aplica)
 ├── 5. Reglas de negocio y validaciones deterministas
 ├── 6. Políticas de permisos y confirmación humana (ADR-001)
 ├── 7. Contratos formales de resiliencia (AgentContract)
 └── 8. Cliente oficial del Cerebro Central (ClienteCerebroCentral, si requiere LLM)
```

**El modelo de lenguaje pertenece a la infraestructura central.** Los agentes nunca son dueños de un modelo.

### 1.2. Tres Arquetipos de Agentes en AudacIA
Un agente **no tiene que utilizar el LLM simplemente por existir dentro de AudacIA**. La arquitectura soporta tres modalidades funcionales:

1. **Agente Determinista (0 llamadas al LLM):**
   - Ejecuta funciones, consulta bases de datos, valida permisos o aplica reglas matemáticas sin invocar al modelo de lenguaje.
   - **Ventajas:** Latencia ultra-baja (< 10 ms), 0 consumo de VRAM, 0 tokens y 0% de alucinaciones.
   - **Ejecución:** Se ejecuta directamente mediante `AgentContract` envuelto en `ResilientAgentExecutor`. No requiere registrarse en el orquestador ni consumir ranuras de inferencia.
   - **Ejemplo real:** HAKU Inventario en su estado actual, con normalización lingüística determinista y servicios transaccionales en base de datos.
2. **Agente Asistido por LLM:**
   - Utiliza el Cerebro Central exclusivamente para interpretar lenguaje abierto, sintetizar información, o redactar respuestas en lenguaje natural cordial, con herramientas y permisos estrictamente acotados.
   - **Ejemplo:** Agentes de divulgación institucional o asistentes de redacción de reportes.
3. **Agente Híbrido:**
   - Utiliza el LLM central para interpretar la intención y proponer llamadas a herramientas (`tool_calls`), pero delega la autorización, validación de reglas de negocio y mutación física a servicios deterministas en Python.
   - **Invariante:** Las reglas de negocio, los permisos (BOLA/IDOR) y la integridad de las transacciones **jamás quedan bajo la autoridad del modelo**.
   - **Ejemplo:** Diseño proyectado para la integración futura de HAKU mediante tool calling con confirmación en dos pasos.

### 1.3. Componentes Especializados No-LLM
Whisper STT (transcripción de audio) y Piper TTS (síntesis de voz) son componentes especializados de entrada/salida acústica, no modelos de lenguaje de propósito general, y operan bajo sus propios presupuestos y cerrojos locales.

### 1.4. Organización Futura de Agentes vs. Estructura Actual
Posterior a la culminación de HAKU Inventario, se contempla migrar hacia una carpeta común de agentes:
```text
agentes/
├── haku_inventario/
├── hacu_visitante/
└── agente_futuro/
```
> [!IMPORTANT]
> **Aclaración sobre la estructura actual:** El esquema anterior es una **propuesta de organización futura**, no la estructura activa del repositorio. Actualmente el código reside en `sistema_inventario/integraciones/haku/`, `hacu/` y `cerebro_audacia/agentes/`. **No mueva carpetas ni modifique importaciones en este momento.** Pertenecer a la futura carpeta `agentes/` tampoco obligará a que un agente utilice un LLM.

---

## 2. Prohibición Absoluta de Modelos Locales por Agente

Para garantizar la estabilidad del servidor central con la GPU NVIDIA RTX 5070 Ti (16 GB VRAM) y evitar la saturación destructiva de memoria, rige la siguiente **política de restricción innegociable**:

> [!CAUTION]
> **PROHIBICIÓN ARQUITECTÓNICA DE CÓDIGO:**
> Al crear un agente nuevo, queda **TERMINANTEMENTE PROHIBIDO**:
> - Instanciar bibliotecas de modelos locales como `Llama(...)` o `AutoModelForCausalLM`.
> - Cargar archivos binarios `.gguf`, `.bin` o `.safetensors`.
> - Iniciar o invocar procesos secundarios de `llama-server.exe` u otros servidores de inferencia.
> - Crear servidores HTTP o sockets paralelos para servir modelos propios.
> - Reservar memoria VRAM en la GPU para un modelo específico del agente.
> - Conectarse directamente a proveedores de IA externos (OpenAI, Anthropic, etc.) saltándose el Cerebro Central.

Un desarrollador jamás debe preguntarse *"¿qué modelo debería cargar mi agente?"*. La respuesta arquitectónica es siempre: **Ninguno. El agente consume el Cerebro Central de AudacIA si requiere capacidades generativas, o se resuelve mediante lógica determinista.**

---

## 3. Flujo Oficial de Creación de un Agente en 10 Pasos

El desarrollo de un nuevo agente sigue una secuencia rigurosa de 10 pasos:

```text
 1. Definir dominio del agente
        │
        ▼
 2. Definir responsabilidades (qué hace y qué NO hace)
        │
        ▼
 3. Definir herramientas (JSON Schema de funciones requeridas)
        │
        ▼
 4. Definir memoria (estado de sesión y contexto operativo)
        │
        ▼
 5. Definir RAG (corpus documental y recuperación, si aplica)
        │
        ▼
 6. Definir reglas y permisos (autorización determinista y confirmación 2P)
        │
        ▼
 7. Definir interfaces (panel web, voz, API interna o consola)
        │
        ▼
 8. Implementar cliente oficial del Cerebro Central (si requiere LLM)
        │
        ▼
 9. Registrar y configurar el agente (programático en OrquestadorAudacIA)
        │
        ▼
10. Probarlo bajo los límites de recursos del Cerebro Central
```

### Detalle de cada paso:
1. **Definir Dominio:** Acotar el ámbito exacto (ej. compras, control de calidad, analítica predictiva, mantenimiento de laboratorio).
2. **Definir Responsabilidades:** Redactar la ficha técnica con límites explícitos de lo que el agente resolverá y lo que expresamente rechazará.
3. **Definir Herramientas:** Diseñar las funciones deterministas y sus esquemas JSON (`parameters`, tipos, campos obligatorios).
4. **Definir Memoria:** Establecer cómo mantiene el contexto en la sesión (ej. en memoria local o SQLite) sin contaminar otros turnos ni sesiones concurrentes.
5. **Definir RAG (si aplica):** Si requiere consultar documentos estáticos (manuales, reglamentos), preparar el índice vectorial local en disco (ChromaDB / SQLite).
6. **Definir Reglas y Permisos:** Establecer qué roles pueden invocar al agente y garantizar que cualquier mutación física exija **confirmación en dos pasos** (`PROPOSAL_BASED`). La autorización debe verificarse en el servicio de negocio, jamás confiando en el LLM.
7. **Definir Interfaces:** Especificar los canales autorizados (`web_lan`, `consola`, `api_interna`).
8. **Implementar Cliente Oficial del Cerebro Central (si aplica):**
   - Si el agente es determinista: **omita el cliente LLM**.
   - Si requiere LLM: importe `from cerebro_audacia.conexion import obtener_cliente_cerebro_central, ClienteCerebroCentral, ConfiguracionCerebroCentral`.
   - La configuración se carga automáticamente de `config_llm.json` o de variables de entorno (`AUDACIA_LLM_URL`, `AUDACIA_LLM_MAX_CONCURRENCY`).
   - El cliente provee métodos verificados:
     - `cliente.comprobar_salud()`: retorna diccionario con `disponible`, `circuit_breaker`, `ranuras_activas`, `en_cola`.
     - `cliente.obtener_modelo_activo()`: consulta el modelo servido por `:8080`.
     - `cliente.generar(mensajes=..., max_tokens=...)`: envía petición bajo gobierno de concurrencia y circuit breaker.
9. **Registrar y Configurar el Agente (Mecanismo Real Existente):**
   - **Registro programático en memoria:** Se realiza instanciando `orquestador = OrquestadorAudacIA()` y llamando a `orquestador.registrar_agente(agente=agente, esquema_herramienta=esquema)`.
   - **Ejecución directa (sin orquestador):** Si el agente es determinista o un servicio interno, puede ejecutarse directamente mediante `ResilientAgentExecutor(agente=agente).ejecutar(entrada)` sin requerir el orquestador.
   - **Lo que NO existe actualmente:** No existe autodescubrimiento por escaneo de carpetas en disco, ni decoradores de registro dinámico, ni base de datos de registro de agentes. Los adaptadores `HakuInventarioAdapter` y `HacuVisitanteAdapter` están formalmente especificados en arquitectura pero **no implementados en código**.
10. **Probar bajo Límites de Recursos:** Validar el comportamiento del agente frente a timeouts, ranuras ocupadas, ráfagas concurrentes y respuestas degradadas.

---

## 4. Los Cuatro Niveles de Compatibilidad

Al concebir un agente, clasifíquelo en uno de estos cuatro niveles operativos:

| Nivel | Significado Práctico para el Desarrollador | Ejemplo Típico |
| :--- | :--- | :--- |
| **Nivel 1 — Directo** | Puede implementarse directamente con la arquitectura actual. Cumple `AgentContract`, es interactivo (< 5 s) y usa recursos estándar. | Agente determinista de auditoría de stock, consultas de catálogo. |
| **Nivel 2 — Con Adaptador** | Código o subsistema existente con interfaz propia que requiere un adaptador para acoplarse al orquestador central sin reescribir su lógica interna. | Normalizador determinista de HAKU Inventario, motor RAG de HACU Visitante. |
| **Nivel 3 — Bajo Controles** | Viable en el servidor, pero requiere infraestructura o controles adicionales (workers/colas para tareas largas, allowlist para APIs externas, o modelos auxiliares compactos como OCR). | Tareas batch masivas, OCR en CPU, consumo de API externa autorizada. |
| **Nivel 4 — Fuera de Alcance Actual** | No es viable garantizarlo en el servidor físico actual (RTX 5070 Ti 16 GB VRAM) por exceder memoria, o por violar la seguridad. | Modelos multimodales gigantes (> 10 GB VRAM), ejecución arbitraria de código. |

---

## 5. Taxonomía del Estado de Implementación

Para no confundir código activo con arquitectura proyectada, clasifique las piezas según este estándar:

```text
[ IMPLEMENTADO ]
Código existente en el repositorio, probado con tests unitarios y operativo.
Ejemplos: AgentContract, ResilientAgentExecutor, llama-server.exe (:8080), Whisper STT con HAKU_GPU_LOCK.

[ IMPLEMENTADO PARCIALMENTE ]
Código escrito pero desacoplado o requiriendo ajustes de integración.
Ejemplos: OrquestadorAudacIA (implementado pero sin conectar a HAKU todavía), HAKU (operando en modo determinista local).

[ ARQUITECTURA DEFINIDA ]
Diseño formalmente acordado, con interfaces y contratos definidos, pendiente de código.
Ejemplos: HakuInventarioAdapter, HacuVisitanteAdapter, flujo seguro de tool calling unificado.

[ PENDIENTE DE IMPLEMENTACIÓN ]
Capacidad planificada que requiere validación empírica en hardware final.
Ejemplos: Colas de prioridad entre agentes en saturación, recuperación automática ante CUDA OOM.
```

---

## 6. Qué NO Debe Modificar un Agente Nuevo (Regla de "No Tocar")

Crear un agente significa **agregar código nuevo y conectarlo mediante los contratos existentes, no alterar el núcleo de la plataforma**.

Un agente nuevo **NO debe modificar**:
- `cerebro_audacia/agentes/contrato.py` (`AgentContract`)
- `cerebro_audacia/agentes/esquemas.py` (`AgentInput`, `AgentOutput`, etc.)
- `cerebro_audacia/agentes/resiliencia.py` (`ResilientAgentExecutor`, circuit breakers, gobernadores)
- `cerebro_audacia/agentes/orquestador.py` (`OrquestadorAudacIA`)
- `cerebro_audacia/conexion/` (`ClienteCerebroCentral`, `config_llm.json`, excepciones)
- `hacu/llm.py` (cliente HTTP de HACU Visitante)
- Mecanismos centrales de autenticación o sesión de Django
- `sistema_inventario/integraciones/haku/`, salvo que el trabajo sea específicamente extender la operativa interna de HAKU.

---

## 7. Dónde Colocar un Agente Nuevo

- **Agentes del núcleo analítico de AudacIA:**  
  Ubíquelos en [`cerebro_audacia/agentes/<nombre_agente>.py`](file:///C:/Users/AudacIA/Documents/inventario/voz-rag-universidad/cerebro_audacia/agentes/).
- **Agentes de dominio de negocio en Django:**  
  Si el agente pertenece a una aplicación de negocio (ej. compras, mantenimiento, auditoría contable), colóquelo dentro de su propia app: `sistema_inventario/<dominio>/agentes.py`.
- **Regla respecto a HAKU y HACU:**  
  No ubique agentes desacoplados dentro de `sistema_inventario/integraciones/haku/` ni dentro de `hacu/`. Esos módulos pertenecen a sus respectivos subsistemas y no son el repositorio general de extensiones.

---

## 8. ¿Cuándo Necesita LLM y Cuándo Debe Ser Determinista?

| Tipo de Tarea | Enfoque Obligatorio | Justificación Técnica |
| :--- | :---: | :--- |
| Cálculos numéricos, reglas fijas, balances, consultas a base de datos, validación de stock | **Determinista (Python puro / ORM)** | Cero milisegundos de cola, cero consumo de VRAM, 100% de predictibilidad matemática. |
| Resumen de textos extensos, explicación contextual al usuario, categorización de lenguaje abierto | **Cerebro Central (API :8080)** | El LLM central procesa el lenguaje libre y utiliza herramientas para obtener los datos precisos. |

---

## 9. Manejo de Autenticación, Autorización y Mutaciones

La seguridad de un agente no depende de la "buena voluntad" del LLM:

1. **Identidad Confiable:** La identidad (`usuario_id`) se inyecta desde la sesión autenticada de Django o token seguro en el servidor. **Nunca se lee de los argumentos generados por el LLM ni de parámetros del cliente**.
2. **Autorización sobre el Recurso (Anti-BOLA/IDOR):** Estar autenticado no significa tener acceso a todo. Si el agente consulta el almacén 2, el orquestador y el servicio de negocio deben validar que el usuario tenga asignado el almacén 2.
3. **Mutaciones en Dos Pasos (ADR-001):** Si un agente altera datos (crea o modifica stock), debe declarar `nivel_autonomia=DataAutonomyLevel.PROPOSAL_BASED`. El agente emite una propuesta en `AgentOutput(propuesta={...})`, pero **no ejecuta la mutación física en la base de datos**. La mutación física requiere confirmación humana en un paso transaccional posterior.

---

## 10. Ficha Técnica de Limitaciones (Plantilla Obligatoria)

Cada agente nuevo debe incluir en su documentación o docstring una ficha técnica clara:

```text
=====================================================================
FICHA TÉCNICA DE LIMITACIONES — [Nombre del Agente]
=====================================================================
- Qué hace:            [Descripción concreta en 1-2 líneas]
- Qué NO hace:         [Límites explícitos y exclusiones]
- Qué datos lee:       [Tablas, modelos o archivos leídos]
- Qué datos modifica:  [Ninguno / Solo propuestas / Mutación confirmada]
- ¿Requiere LLM?:      [No / Sí (utiliza Cerebro Central :8080)]
- ¿Requiere GPU?:      [No (el LLM corre en GPU centralizada)]
- Consumo aproximado:  [CPU baja/media, RAM < 50 MB, VRAM 0 MB]
- Concurrencia máx:    [Número de ejecuciones simultáneas admitidas]
- Timeout:             [Tiempo máximo de ejecución en segundos]
- Canales permitidos:  [consola, web_lan, api_interna]
- Autenticación:       [Requerida / Opcional]
- Autorización:        [Roles necesarios o recursos acotados]
- Dependencias:        [Librerías externas o servicios locales]
- Limitaciones:        [Puntos ciegos o casos no cubiertos]
=====================================================================
```

---

## 11. Ejemplo Práctico Completo: Paso a Paso

> [!NOTE]
> **Aviso de código didáctico:** El siguiente `AgenteDiagnosticoAlmacen` es un **ejemplo didáctico y plantilla de referencia** para ilustrar la implementación de `AgentContract`. No es un módulo activo en producción ni una entidad preexistente en la base de datos.

### 11.1. Implementación del Agente (`cerebro_audacia/agentes/diagnostico.py`)

```python
"""Agente de Diagnóstico Operativo de Almacén — Ejemplo de Extensión Segura."""

from __future__ import annotations
from typing import Any, Mapping

from cerebro_audacia.agentes.contrato import AgentContract
from cerebro_audacia.agentes.esquemas import (
    AgentContext,
    AgentErrorCode,
    AgentExecutionError,
    AgentInput,
    AgentMetadata,
    AgentOutput,
    DataAutonomyLevel,
    IdempotencyMode,
    ResourceKind,
    ResourceProfile,
)


class AgenteDiagnosticoAlmacen(AgentContract):
    """Agente determinista de consulta de operatividad de almacenes."""

    @property
    def metadata(self) -> AgentMetadata:
        return AgentMetadata(
            id="diagnostico-almacen",
            nombre="Diagnóstico Operativo de Almacén",
            version="1.0.0",
            descripcion="Evalúa la operatividad y alertas de un almacén específico.",
            nivel_autonomia=DataAutonomyLevel.READ_ONLY,
            canales_permitidos=("consola", "web_lan", "api_interna"),
            requiere_autenticacion=True,
            capacidades=("diagnosticar",),
            perfil_recursos=ResourceProfile(
                recursos_requeridos=(ResourceKind.CPU,),
                max_concurrencia=3,
                timeout_segundos=5.0,
                soporta_cancelacion=False,
            ),
            modo_idempotencia=IdempotencyMode.READ_ONLY,
        )

    def _ejecutar_interno(self, entrada: AgentInput) -> AgentOutput:
        parametros = entrada.parametros or {}
        almacen = parametros.get("almacen", "Almacén Central")

        if not isinstance(almacen, str) or not almacen.strip():
            raise AgentExecutionError(
                mensaje_seguro="El nombre del almacén no puede estar vacío.",
                codigo=AgentErrorCode.INVALID_INPUT,
            )

        # Lógica de negocio determinista (ejemplo ilustrativo seguro)
        datos_diagnostico = {
            "almacen": almacen.strip(),
            "estado": "OPERATIVO",
            "alertas_activas": 0,
            "disponibilidad_pct": 99.2,
        }

        return AgentOutput(
            respuesta=f"Almacén '{almacen}': Estado OPERATIVO con 99.2% de disponibilidad y 0 alertas.",
            datos=datos_diagnostico,
            modalidad="texto",
            propuesta=None,  # Invariante READ_ONLY: jamás emite propuesta de mutación
        )
```

### 11.2. Prueba Aislada sin LLM (`cerebro_audacia/pruebas/test_diagnostico.py`)

```python
import unittest
from cerebro_audacia.agentes.esquemas import AgentContext, AgentInput
from cerebro_audacia.agentes.resiliencia import ResilientAgentExecutor
from cerebro_audacia.agentes.diagnostico import AgenteDiagnosticoAlmacen


class TestAgenteDiagnostico(unittest.TestCase):
    def setUp(self):
        self.agente = AgenteDiagnosticoAlmacen()
        self.ejecutor = ResilientAgentExecutor(agente=self.agente)

    def test_ejecucion_exitosa(self):
        ctx = AgentContext.crear(canal="web_lan", usuario_id="supervisor_01")
        entrada = AgentInput(
            contenido="Evaluar bodega",
            contexto=ctx,
            intencion="diagnosticar",
            parametros={"almacen": "Bodega Principal"},
        )
        res = self.ejecutor.ejecutar(entrada)
        self.assertTrue(res.exitoso)
        self.assertIn("OPERATIVO", res.salida.respuesta)
        self.assertEqual(res.salida.datos["disponibilidad_pct"], 99.2)

    def test_rechazo_canal_no_permitido(self):
        ctx = AgentContext.crear(canal="canal_desconocido", usuario_id="supervisor_01")
        entrada = AgentInput(contenido="Evaluar", contexto=ctx, intencion="diagnosticar")
        res = self.ejecutor.ejecutar(entrada)
        self.assertFalse(res.exitoso)
        self.assertEqual(res.error.codigo.value, "CHANNEL_NOT_ALLOWED")


if __name__ == "__main__":
    unittest.main()
```

### 11.3. Conexión al Cerebro Central vía `OrquestadorAudacIA`

```python
from cerebro_audacia.agentes.orquestador import OrquestadorAudacIA
from cerebro_audacia.agentes.diagnostico import AgenteDiagnosticoAlmacen
from cerebro_audacia.agentes.esquemas import AgentContext
from cerebro_audacia.conexion import obtener_cliente_cerebro_central

# 1. Definición explícita de la herramienta JSON Schema para el LLM central
HERRAMIENTA_DIAGNOSTICO = {
    "type": "function",
    "function": {
        "name": "diagnostico-almacen",
        "description": "Evalúa el estado de operatividad y alertas de un almacén específico.",
        "parameters": {
            "type": "object",
            "properties": {
                "almacen": {
                    "type": "string",
                    "description": "Nombre del almacén a diagnosticar (ej. 'Almacén Central')."
                }
            },
            "required": ["almacen"]
        }
    }
}

# 2. Inicialización del cliente oficial del Cerebro Central y del orquestador
# (OrquestadorAudacIA() instancia automáticamente obtener_cliente_cerebro_central() si llm_client es None)
llm_central = obtener_cliente_cerebro_central()
orquestador = OrquestadorAudacIA(llm_client=llm_central)

agente = AgenteDiagnosticoAlmacen()
orquestador.registrar_agente(agente=agente, esquema_herramienta=HERRAMIENTA_DIAGNOSTICO)

# 3. Procesamiento seguro de mensaje de usuario
contexto_usuario = AgentContext.crear(
    canal="web_lan",
    usuario_id="supervisor_almacen_1",
)

resultado = orquestador.procesar_mensaje(
    mensaje="¿Cómo se encuentra el Almacén Central hoy?",
    contexto=contexto_usuario,
    sesion_id="sesion-web-105",
)

if resultado.exitoso:
    print("Respuesta del Cerebro Central:", resultado.respuesta)
    print("Herramientas ejecutadas:", resultado.herramientas_ejecutadas)
else:
    print(f"Error ({resultado.error.codigo.value}): {resultado.respuesta}")
```

---

## 12. Flujo de Trabajo en una Rama Git Aislada (`feat/agente-nombre`)

Para crear o extender un agente sin poner en riesgo la estabilidad del proyecto ni generar colisiones con otros módulos, siga estrictamente este protocolo:

### 12.1. Creación de Rama Descriptiva
Cree una rama aislada a partir de la rama de trabajo autorizada:
```powershell
git checkout -b feat/agente-<nombre>
```
*(Ejemplos: `feat/agente-mantenimiento`, `feat/agente-alertas-laboratorio`).*

### 12.2. Inspección Previa de Contratos y Servicios
Antes de redactar código:
1. Inspeccione los contratos en `cerebro_audacia/agentes/contrato.py` y los tipos en `esquemas.py`.
2. Verifique si la lógica requerida ya existe en un servicio determinista de negocio (ej. en `sistema_inventario/inventario/services.py`). **Reutilice los servicios existentes; no reinvente la rueda ni duplique consultas SQL.**
3. Determine si el agente realmente necesita capacidades del Cerebro Central o si debe resolverse de forma 100% determinista.

### 12.3. Delimitación de Archivos Editables vs. Módulos Compartidos
| Tipo de Módulo | Ruta en el Repositorio | Política de Modificación |
|---|---|:---:|
| **Código propio del nuevo agente** | `cerebro_audacia/agentes/<nombre>.py` o `sistema_inventario/<app>/agentes.py` | **EDITABLE** |
| **Pruebas unitarias del agente** | `cerebro_audacia/pruebas/test_<nombre>.py` o `sistema_inventario/<app>/tests_agente.py` | **EDITABLE** |
| **Contratos base y esquemas** | `cerebro_audacia/agentes/contrato.py`, `esquemas.py` | **SOLO LECTURA** |
| **Infraestructura de resiliencia** | `cerebro_audacia/agentes/resiliencia.py`, `orquestador.py` | **SOLO LECTURA** |
| **Conexión al Cerebro Central** | `cerebro_audacia/conexion/` | **SOLO LECTURA** |
| **Código operativo de HAKU/HACU** | `sistema_inventario/integraciones/haku/`, `hacu/` | **SOLO LECTURA** |

> [!CAUTION]
> **Regla de detención ante cambios compartidos:**  
> Si durante el desarrollo descubre que su agente necesita modificar contratos compartidos (`AgentContract`, `AgentMetadata`), configuraciones del Cerebro Central (`config_llm.json`) o permisos globales del sistema: **DETENGA LA MODIFICACIÓN COLATERAL**. No aplique parches silenciosos en módulos ajenos. Señale la necesidad técnica en su informe para que sea evaluada por el equipo de arquitectura.

### 12.4. Implementación y Pruebas Aisladas
1. Implemente el agente heredando de `AgentContract`.
2. Diseñe pruebas unitarias sin dependencias externas:
   - Valide las entradas válidas y el rechazo de canales no autorizados.
   - Si utiliza `OrquestadorAudacIA`, use mocks para el cliente LLM en sus pruebas unitarias.
3. Ejecute la suite de pruebas del módulo para asegurar cero regresiones:
   ```powershell
   python -m unittest discover -s cerebro_audacia/pruebas -p "test_*.py"
   ```

### 12.5. Entrega y Cierre de Rama
Al culminar el desarrollo:
1. No fusione (`merge`) la rama por iniciativa propia.
2. Prepare un resumen técnico que documente:
   - Archivos creados y funciones implementadas.
   - Pruebas ejecutadas y tasa de aprobación.
   - Pruebas que requieren validación física en GPU real (si aplica).
   - Ficha técnica de limitaciones y consumo estimado.
   - Puntos de integración pendientes.

---

## 13. Checklist de Verificación para Nuevos Agentes

Antes de dar por finalizado un agente nuevo, verifique cada elemento:

- [ ] **Sin LLM Propio:** El agente **NO** carga modelos `.gguf`, no instancia `Llama(...)` ni reserva VRAM.
- [ ] **Cliente Oficial:** Si requiere capacidades de lenguaje, consume el Cerebro Central (`:8080`) vía `obtener_cliente_cerebro_central()`.
- [ ] **Propósito Único:** El agente tiene una responsabilidad de dominio claramente definida.
- [ ] **Vía Determinista Prioritaria:** Cálculos, consultas a base de datos y validaciones no pasan por el LLM.
- [ ] **Ubicación:** Está ubicado en `cerebro_audacia/agentes/` o en su propia app Django (futuro: `agentes/`).
- [ ] **Contrato:** Hereda de `AgentContract` e implementa `_ejecutar_interno`.
- [ ] **Metadatos:** Define `AgentMetadata` explícito con `id`, `nombre`, `version`, `capacidades` y `canales_permitidos`.
- [ ] **Perfil de Recursos:** Define `ResourceProfile` con `timeout_segundos` realistas y `max_concurrencia`.
- [ ] **Autonomía:** `nivel_autonomia` es `READ_ONLY` o `PROPOSAL_BASED` (nunca mutación física directa).
- [ ] **Idempotencia:** Declara `modo_idempotencia` (`READ_ONLY`, `IDEMPOTENT` o `NON_IDEMPOTENT`).
- [ ] **Cancelación:** Si no implementa sondeo cooperativo, declara `soporta_cancelacion=False`.
- [ ] **Independencia Inicial:** Su lógica se probó y resolvió de forma aislada sin requerir el LLM.
- [ ] **Pruebas Propias:** Cuenta con pruebas unitarias que cubren entradas válidas, errores y canales no autorizados.
- [ ] **Seguridad de Recursos:** Valida autorizaciones específicas en el servicio de negocio (ej. acceso a almacén BOLA/IDOR).
- [ ] **Ficha Técnica:** Sus limitaciones, consumo y requisitos están documentados.
- [ ] **Integridad del Núcleo:** No modifica contratos ni clases base compartidas.
- [ ] **Adaptación al Orquestador:** Cuenta con JSON Schema y se registra limpiamente en `OrquestadorAudacIA`.
- [ ] **Aislamiento en Rama Git:** El trabajo se realizó en una rama `feat/agente-<nombre>` sin modificaciones colaterales.

---

*Guía actualizada y armonizada conforme a la Decisión Arquitectónica Fundamental de Cerebro Central Único de AudacIA.*
