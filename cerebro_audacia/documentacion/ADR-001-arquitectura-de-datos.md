# ADR-001: ARQUITECTURA DE DATOS, PERSISTENCIA Y PROPIEDAD EN AUDACIA

- **Estado:** Propuesto (Fase de Auditoría y Delimitación)
- **Fecha:** 2026-10-14
- **Ámbito:** `cerebro_audacia/`, `sistema_inventario/`, `hacu/`, `documents/`
- **Autor:** Antigravity Architect Agent

---

## 1. CONTEXTO Y PROBLEMA

AudacIA es un ecosistema multiagente y multiservicio concebido para operar de forma 100% local dentro de una red de área local (LAN) desconectada de servicios en la nube. Actualmente conviven en el repositorio dos subsistemas con naturalezas operativas y requerimientos de datos diametralmente opuestos:

1. **Sistema de Inventario (incluyendo Haku Inventario):** Ubicado en `sistema_inventario/` (con la extensión de voz en `sistema_inventario/integraciones/haku/`). Es un sistema transaccional de misión crítica que administra existencias físicas de bodega. Requiere cumplimiento estricto de propiedades ACID, inmutabilidad de movimientos confirmados, prohibición matemática de stock negativo y trazabilidad mediante auditoría no modificable.
2. **HACU Visitante:** Ubicado en `hacu/`. Es un asistente de stand y divulgación institucional en ferias y eventos. Emplea un modelo de lenguaje local (GGUF cuantizado vía llama.cpp), memoria conversacional multi-perfil y recuperación aumentada por generación (RAG) sobre documentos públicos en Markdown.

Ante la creación de `cerebro_audacia/` como núcleo de coordinación e integración multiagente, surge la necesidad de definir cómo deben organizarse las bases de datos y la persistencia antes de implementar contratos formales (`AgentContract`).

### Problemas y Riesgos Detectados:
- **Presuposición de Base de Datos Centralizada:** Asumir erróneamente que todos los agentes deben compartir la misma base de datos relacional rompería la separación de responsabilidades, acoplaría esquemas ajenos, expondría datos sensibles de bodega y podría inducir bloqueos o corrupción.
- **Presuposición de Base de Datos Aislada por Agente Obligatoria:** Asumir que cada nuevo microagente requiere un motor de base de datos independiente incrementaría innecesariamente la sobrecarga operativa, el consumo de memoria en el servidor local y la complejidad de administración de respaldos.
- **Riesgo de Inyección / Acceso SQL desde Modelos Estocásticos (LLM):** Permitir que agentes generativos posean credenciales directas a bases de datos relacionales o generen sentencias SQL de mutación viola el principio de autorización y determinismo.
- **Disparidad de Motores entre Desarrollo y Producción:** El entorno de desarrollo activo utiliza SQLite (`db.sqlite3`), donde los mecanismos de bloqueo de fila (`select_for_update()`) se omiten silenciosamente, lo cual oculta posibles fallas de concurrencia que solo se manifestarán bajo PostgreSQL en producción.
- **Falta de Delimitación de Responsabilidad:** Clarificar qué debe persistir `cerebro_audacia/` y qué debe permanecer como responsabilidad interna de cada componente.

---

## 2. ESTADO REAL COMPROBADO DE CADA MECANISMO DE PERSISTENCIA

Tras una inspección exhaustiva de los archivos del repositorio, la configuración activa y los datos en disco, se constata el siguiente estado verificado (diferenciando hechos comprobados de configuraciones previstas):

### 2.1. Base de Datos Relacional de Inventario (`sistema_inventario`)
* **Archivo de configuración:** `sistema_inventario/inventario_audacia/settings.py` (líneas 77–102) y `sistema_inventario/.env` (línea 7).
* **Motor activo comprobado:** **SQLite 3**. En `sistema_inventario/.env` está configurado explícitamente `DB_ENGINE=sqlite3`. La base de datos física existe en el repositorio en `sistema_inventario/db.sqlite3` (tamaño auditado: 427.008 bytes, 23 tablas).
* **Configuración prevista para PostgreSQL:** En `settings.py`, si `DB_ENGINE` no contiene "sqlite", se configura `django.db.backends.postgresql` con:
  - Base: `inventario_audacia`
  - Puerto: `5432`
  - Host: `localhost`
  - Opciones de conexión: `-c default_transaction_isolation=read committed`.
  > [!NOTE]
  > **Hallazgo en Settings:** El comentario de código en `settings.py:L96` afirma que el aislamiento es `SERIALIZABLE`, pero la opción real configurada en la línea 99 es `read committed`.
* **Soporte para Múltiples Conexiones o Routers:** En `settings.py` existe únicamente el alias de base de datos `"default"`. No existe configuración de `DATABASE_ROUTERS` ni routers de base de datos definidos en el proyecto.
* **Modelos y Entidades (Fuente de Verdad de Inventario):**
  Definidos en `sistema_inventario/inventario/models.py` y `sistema_inventario/cuentas/models.py`:
  - `cuentas.UsuarioAudacIA`: 4 usuarios registrados (1 Superusuario, 1 Administrador, 2 Usuarios con almacén asignado).
  - `inventario.Almacen`: 2 almacenes físicos ("Almacén Central", "Almacén Secundario").
  - `inventario.Categoria`: 3 categorías ("Cables y Conectores", "Herramientas", "Equipos Audiovisuales").
  - `inventario.Ubicacion`: Estanterías por almacén.
  - `inventario.Producto`: 4 productos con códigos internos inmutables autogenerados (ej. `CAB-0001`).
  - `inventario.Existencia`: 4 registros de stock físico (`stock_disponible`, `stock_en_prestamo`).
  - `inventario.Movimiento`: 3 movimientos confirmados con identificadores UUID y clave de idempotencia única.
  - `inventario.Prestamo`: 3 préstamos con seguimiento de destinatario y devoluciones parciales.
  - `inventario.DevolucionPrestamo`: Registro de devoluciones y bajas por daño.
  - `inventario.ConteoFisico`: Registro de conteos físicos y diferencias.
  - `inventario.Auditoria`: 2 registros inmutables con instantáneas JSON (`valores_anteriores`, `valores_nuevos`) y bandera `via_haku=True`.
* **Estado de Haku Inventario (`sistema_inventario/integraciones/haku/`):**
  - **Hecho comprobado:** Haku Inventario posee **0 modelos propios y 0 tablas de base de datos**. Su archivo `admin.py` documenta explícitamente: *"No hay modelos propios en integraciones.haku (consume modelos de inventario y cuentas)"*.
  - No tiene carpeta de migraciones (`migrations/`).

### 2.2. Almacenamiento en Memoria RAM, Cachés y Sesiones
* **Caché en RAM (`LocMemCache`):**
  Configurado en `settings.py:L125-163` utilizando `django.core.cache.backends.locmem.LocMemCache`:
  - `haku_contexto`: TTL 7200 segundos (2 horas), capacidad máxima 50 contextos LRU. Almacena turnos conversacionales del operador.
  - `haku_catalogo`: TTL 1800 segundos (30 minutos), capacidad máxima 500 productos.
  - `haku_ubicaciones`: TTL 900 segundos (15 minutos), capacidad máxima 500 registros.
  - `haku_stock`: TTL 15 segundos, capacidad máxima 200 registros (stock orientativo para voz).
  - *Comportamiento verificado:* **100% volátil**. Al reiniciar el proceso o servidor web, todo el contenido de `LocMemCache` desaparece.
* **Sesiones HTTP de Django:**
  - Configurado como `SESSION_ENGINE = "django.contrib.sessions.backends.db"`.
  - *Comportamiento verificado:* Las sesiones se persisten en la tabla relacional `django_session`. Las propuestas operativas pendientes de confirmación (`haku_propuesta_activa`) se almacenan dentro de la sesión del usuario en la base de datos, por lo que **sobreviven a un reinicio del servidor**, siempre que no haya vencido el tiempo de expiración (30 minutos).

### 2.3. Persistencia de HACU Visitante (`hacu/`)
* **Memoria Conversacional y Episódica (`hacu/memory.py` y `hacu/config.py`):**
  - Implementado como SQLite directo mediante la clase `HacuMemoryDB`.
  - Ruta configurada: `PROJECT_ROOT / "hacu_memory.db"`.
  - Opciones de conexión: `check_same_thread=False`, `PRAGMA journal_mode=WAL`, `PRAGMA synchronous=NORMAL`, sincronizado mediante `threading.RLock()`.
  - Esquema interno:
    - `short_term_history`: `id`, `user_id`, `role`, `content`, `timestamp`.
    - `episodic_memory`: `id`, `user_id`, `fact`, `timestamp`.
  - *Estado real en disco:* **El archivo `hacu_memory.db` no existe actualmente en el repositorio**. Se crea bajo demanda en la primera ejecución.
* **Almacén Vectorial RAG (`hacu/rag.py` y `hacu/config.py`):**
  - Motor: ChromaDB persistente (`chromadb.PersistentClient`).
  - Ruta configurada: `PROJECT_ROOT / "chroma_db"`.
  - Colecciones: `audacia_knowledge` y `universidad_knowledge` (con sufijo `_multi` si utiliza `paraphrase-multilingual-MiniLM-L12-v2`).
  - *Estado real en disco:* **El directorio `chroma_db/` no existe actualmente en el repositorio**. Las dependencias (`chromadb`, `sentence-transformers`, `langchain-text-splitters`) no están instaladas en el entorno virtual activo.
* **Fuente de Verdad Documental:**
  - Vive en `documents/`: 12 archivos Markdown (ej. `audacia_general.md`, `audacia_proyectos_salud.md`, `universidad_general.md`). Son archivos estáticos en texto plano versionados en Git.

### 2.4. Aislamiento e Interconexión Actual entre Componentes
* **Acceso Directo entre Componentes:**
  - **Hecho comprobado:** Existe **cero acoplamiento** a nivel de base de datos entre HACU Visitante y el Sistema de Inventario.
  - `hacu/` no contiene ningún import de Django, ni hace referencia a `db.sqlite3`, `inventario`, o `cuentas`.
  - `sistema_inventario/` no hace referencia a `chroma_db/`, `hacu_memory.db`, ni a los archivos de `documents/`.

### 2.5. Migraciones y Entornos de Prueba
* **Migraciones existentes:**
  - `sistema_inventario/cuentas/migrations/`: `0001_initial.py`, `0002_initial.py`.
  - `sistema_inventario/inventario/migrations/`: `0001_initial.py`.
* **Pruebas de persistencia ejecutadas:**
  - Las pruebas unitarias existentes (`sistema_inventario/integraciones/haku/tests_contexto.py`, `tests_lenguaje.py`) corren sobre SQLite en memoria.
  - No existen pruebas automatizadas de concurrencia real (múltiples conexiones simultáneas) ni pruebas que ejecuten PostgreSQL en el repositorio.

---

## 3. INTEGRIDAD TRANSACCIONAL Y MECANISMOS DE BLOQUEO

### 3.1. Implementación en Código (`sistema_inventario/integraciones/haku/services.py`)
La función transaccional crítica es `ejecutar_confirmacion_propuesta(usuario, propuesta)` (líneas 1220–1385). Se verificó que implementa los siguientes controles:
1. **Transacción atómica:** Envuelto completamente en `with transaction.atomic():`.
2. **Bloqueo pesimista con `select_for_update()`:**
   - Bloqueo de producto: `Producto.objects.select_for_update().get(id=propuesta["producto_id"], activo=True)`
   - Bloqueo de almacén: `Almacen.objects.select_for_update().get(id=propuesta["almacen_origen_id"], activo=True)`
   - Bloqueo de existencia: `Existencia.objects.select_for_update().get_or_create(...)`
   - Bloqueo de préstamo (en devoluciones): `Prestamo.objects.select_for_update().get(id=propuesta["prestamo_id"])`
3. **Validación de permisos y reglas de negocio dentro de la transacción:**
   - Revalida que el usuario tenga acceso al almacén (`usuario.tiene_acceso_almacen(almacen)`).
   - Revalida que la propuesta no haya expirado (`propuesta["expira_en"] < time.time()`).
   - Verifica la clave de idempotencia (`clave_idempotencia`) para evitar duplicaciones por reintentos de voz o red.
   - Prohíbe matemáticamente el stock negativo: lanza `ValidationError` si `stock_disponible < cantidad` en operaciones de salida.
   - Genera el registro inmutable de `Movimiento` con snapshot de stock (`stock_disponible_anterior` y `stock_disponible_posterior`).
   - Genera el registro inmutable de `Auditoria` (`via_haku=True`).
   - Invalida la entrada correspondiente en `LocMemCache` (`HakuCacheManager.invalidar_stock(...)`).

### 3.2. Brecha entre SQLite y PostgreSQL (Implicación Crítica)
* **Comportamiento en SQLite (Entorno Actual de Desarrollo):**
  SQLite no implementa bloqueo de filas (`SELECT ... FOR UPDATE`). El motor de SQLite acepta la sintaxis pero la ignora silenciosamente. SQLite utiliza bloqueo a nivel de archivo de base de datos durante las escrituras. Por tanto, en desarrollo no se produce verdadera serialización de filas concurrentes.
* **Comportamiento en PostgreSQL (Entorno Objetivo de Producción):**
  PostgreSQL implementa bloqueo pesimista a nivel de fila mediante `SELECT ... FOR UPDATE`. Si dos transacciones intentan confirmar una salida sobre el mismo producto en el mismo almacén al mismo milisegundo, la segunda transacción esperará a que la primera haga `COMMIT` o `ROLLBACK`. Si el nivel de aislamiento es `READ COMMITTED`, leerá el stock actualizado por la primera transacción y fallará con `Stock insuficiente` en lugar de generar un saldo negativo.
* **Estado de Validación:**
  En `tests_contexto.py:L295-303`, se prueba que una salida con stock insuficiente es rechazada y no altera la BD. Sin embargo, **esta prueba es monohilo (secuencial)**. **No hay pruebas automatizadas de concurrencia ni de condiciones de carrera en el repositorio**.

---

## 4. PROPUESTA DE ARQUITECTURA DE DATOS Y PERSISTENCIA

Recomendamos una **política de persistencia políglota y federada con propiedad descentralizada**, basada en tres principios rectores:

```
┌────────────────────────────────────────────────────────────────────────┐
│                   PRINCIPIOS DE PERSISTENCIA AUDACIA                  │
├──────────────────────────────────┬─────────────────────────────────────┤
│ 1. Fuente de Verdad Especializada│ Cada tipo de dato en su motor idóneo│
│ 2. Propiedad Estricta            │ Un único subsistema dueño por dato  │
│ 3. Cero SQL Directo desde LLM    │ Acceso vía APIs y servicios tipados │
└──────────────────────────────────┴─────────────────────────────────────┘
```

### 4.1. Clasificación de Persistencia por Capa

| Capa de Datos | Naturaleza | Fuente de Verdad | Motor Recomendado | Acceso Externo Permitido |
|---|---|---|---|---|
| **Operativa / Transaccional** | Existencias, productos, movimientos, usuarios, auditoría. | `sistema_inventario` | **PostgreSQL** (esquema relacional local) | Exclusivamente vía API REST / Servicios internos autenticados con token/sesión. |
| **Memoria Conversacional** | Turnos de diálogo, hechos de perfil, preferencias de visitante. | Asistente correspondiente (`hacu/` o futuros agentes) | **SQLite local** (modo WAL) o en memoria de proceso si es efímero. | Ninguno. Privado del agente. |
| **Conocimiento Documental** | Fichas técnicas, proyectos, reglamentos, documentación pública. | Archivos Markdown en `documents/` (versión en Git) | **ChromaDB local** (índice derivado, reconstruible) | Vía módulo de consulta/adaptador de conocimiento. |
| **Contexto Efímero de Voz** | Buffers de audio, transcripciones parciales, propuestas volátiles. | Memoria de la aplicación | **LocMemCache / RAM de proceso** | Ninguno. |

### 4.2. Criterios para Decidir el Almacenamiento

1. **Cuándo usar una Base de Datos Relacional Compartida (PostgreSQL):**
   - Cuando nuevos agentes requieran persistencia de entidades empresariales estructuradas que necesiten integridad referencial con el negocio, auditoría relacional o respaldo institucional unificado.
   - **Regla de Aislamiento:** Se debe compartir el **servidor PostgreSQL**, pero **separar esquemas (o bases de datos lógicas) y usuarios/credenciales**. Por ejemplo, una base de datos `audacia_operaciones` con esquema `inventario` propiedad del usuario `db_inventario`, y esquema `agente_x` propiedad de `db_agente_x`. Ningún agente tiene permisos de lectura/escritura sobre el esquema del otro a nivel de motor de base de datos.
2. **Cuándo usar SQLite en Archivo Embebido:**
   - Para memoria conversacional, historial de interacción o estado local de un agente que atiende a un solo dispositivo o stand físico (como HACU). No justifica la sobrecarga de una base de datos cliente-servidor.
   - Se debe activar siempre `PRAGMA journal_mode=WAL` para evitar bloqueos entre el hilo de interacción y tareas de fondo.
3. **Cuándo usar Almacén Vectorial (ChromaDB):**
   - Únicamente para recuperación semántica de fragmentos de texto no estructurado (RAG).
   - **Regla:** ChromaDB es un índice de búsqueda secundario; la fuente de verdad son siempre los documentos Markdown.
4. **Cuándo NO añadir almacenamiento:**
   - Si la información es un resumen transitorio de un flujo conversacional (usar memoria RAM con TTL).
   - Si la información ya pertenece a otro subsistema (se consulta bajo demanda mediante API; **está terminantemente prohibido duplicar existencias o movimientos en bases de datos de agentes**).

### 4.3. Procedimientos de Respaldo, Migración y Recuperación (DR)
* **PostgreSQL (Inventario y futuros servicios relacionales):**
  - Respaldo mediante tarea programada local (`pg_dump -Fc`) diario en la LAN, con retención de 30 días en almacenamiento secundario/NAS interno.
  - Migraciones gestionadas estrictamente a través de herramientas de esquema declarativo (ej. `python manage.py migrate`).
* **SQLite de Agentes (`hacu_memory.db`):**
  - Copia atómica mediante la API de respaldo de SQLite (`sqlite3_backup`) o copia de archivo tras un checkpoint de WAL (`PRAGMA wal_checkpoint(TRUNCATE)`).
* **ChromaDB / RAG:**
  - Política de *Cero Respaldo Pesado*: Debido a que el almacén vectorial es deterministamente generado a partir de `documents/`, la estrategia de recuperación ante corrupción es la reindexación automática idempotente desde el repositorio Git.

---

## 5. DELIMITACIÓN DE `cerebro_audacia/` RESPECTO A DATOS

### 5.1. Evaluación Crítica de la Hipótesis Inicial
La hipótesis inicial planteada sostiene:
> *"`cerebro_audacia/` debe definir contratos y políticas de integración, pero no poseer automáticamente todas las bases de datos ni concentrar todo el almacenamiento en una base central."*

**Evaluación técnica:** La hipótesis es **plenamente acertada y arquitectónicamente obligatoria**.
- Si `cerebro_audacia/` intentara ser el dueño directo de la base de datos de inventario, violaría la encapsulación del proyecto Django, comprometería la inmutabilidad de los movimientos y obligaría a replicar o saltarse la lógica de negocio implementada en `sistema_inventario/inventario/models.py`.
- Si `cerebro_audacia/` concentrara todo en una sola base de datos (mezclando vectores, memoria conversacional efímera y transacciones contables), generaría un punto único de falla, problemas de escalabilidad y sobrecarga de I/O en hardware de capacidad moderada.

### 5.2. Qué Debe Contener el Contrato de un Agente (`AgentContract`)
El contrato del agente en `cerebro_audacia/agentes/` debe definir:
1. **Identidad y Capacidades:** Identificador único del agente, nombre legible y lista de intenciones o acciones declaradas (ej. `consultar_stock`, `divulgar_proyecto`).
2. **Nivel de Autonomía de Datos:** Declaración de si el agente es de solo lectura (`READ_ONLY`) o puede proponer operaciones sujetas a confirmación (`PROPOSAL_BASED`). Ningún agente puede tener el nivel `ARBITRARY_MUTATION`.
3. **Mecanismo de Intercambio de Mensajes:** Definición de esquemas de entrada y salida (usando dataclasses o esquemas Pydantic / TypedDict) para interactuar con otros agentes.
4. **Requisitos de Memoria:** Si el agente requiere persistencia conversacional (sesión volátil vs memoria episódica) para que el runtime le asigne o no un storage aislado.
5. **Credenciales de Servicio (Tokens de API):** Tokens de autenticación interna para consultar endpoints locales autorizados.

### 5.3. Qué Debe Quedar Estrictamente en la Configuración Interna de cada Componente
Debe permanecer **privado y fuera del cerebro**:
- Cadenas de conexión (DSN), nombres de usuario y contraseñas de PostgreSQL.
- Rutas físicas a archivos SQLite o carpetas de ChromaDB en disco.
- Modelos ORM, consultas SQL internas y definiciones de tablas.
- Configuración de pools de conexión, índices específicos y niveles de aislamiento de transacciones.

---

## 6. MATRIZ DE PROPIEDAD Y ACCESO A DATOS

| Almacenamiento / Recurso | Subsistema Propietario | Motor / Formato | Permiso del Propietario | Permiso de HACU Visitante | Permiso de Haku Inventario | Permiso de Futuros Agentes / Cerebro |
|---|---|---|---|---|---|---|
| **Base de Datos de Inventario** (`db.sqlite3` / PostgreSQL) | `sistema_inventario` | RDBMS (PostgreSQL) | Lectura / Escritura total según rol de usuario | **DENEGADO** (0 acceso) | Lectura / Escritura vía servicios Django (`services.py`) | Lectura restringida vía API REST autorizada. Escritura prohibida salvo propuesta validada. |
| **Cachés de Inventario** (`haku_stock`, `haku_contexto`) | `sistema_inventario` | En RAM (`LocMemCache`) | Lectura / Escritura | **DENEGADO** | Lectura / Escritura | **DENEGADO** |
| **Memoria de Visitante** (`hacu_memory.db`) | `hacu` | SQLite (WAL) | Lectura / Escritura de turnos y hechos | Lectura / Escritura | **DENEGADO** | **DENEGADO** (privado de HACU) |
| **Índice Vectorial RAG** (`chroma_db/`) | `hacu` | ChromaDB (Parquet/SQLite) | Lectura semántica / Reindexación | Lectura semántica | **DENEGADO** | Lectura semántica solo si consultan a HACU o si se federan colecciones públicas. |
| **Documentos Institucionales** (`documents/`) | Repositorio AudacIA | Archivos Markdown | Lectura (estático en Git) | Lectura para ingesta | **DENEGADO** (no requerido) | Lectura pública como fuente documental. |
| **Estado Orquestador** (Futuro) | `cerebro_audacia` | Archivos de configuración / SQLite liviano | Lectura / Escritura de rutas y contratos | Consulta de contratos | Consulta de contratos | Registro y consulta de capacidades. |

---

## 7. DIAGRAMA TEXTUAL DE ARQUITECTURA DE PERSISTENCIA

```
═════════════════════════════════════════════════════════════════════════════════════
                       ARQUITECTURA DE PERSISTENCIA AUDACIA (LAN)
═════════════════════════════════════════════════════════════════════════════════════

   DISPOSITIVOS EN LAN (Navegadores / Micrófonos / Stands)
       │                                     │
       │ HTTP / WebSockets                   │ Audio / CLI / Web
       ▼                                     ▼
 ┌───────────────────────────┐         ┌───────────────────────────┐
 │   SISTEMA DE INVENTARIO   │         │      HACU VISITANTE       │
 │   (Django / Django Admin) │         │    (Stand / Exposición)   │
 │                           │         │                           │
 │  ┌─────────────────────┐  │         │  ┌─────────────────────┐  │
 │  │   Haku Inventario   │  │         │  │    Motor LLM Local  │  │
 │  │   (Integración Voz) │  │         │  │   (llama.cpp GGUF)  │  │
 │  └──────────┬──────────┘  │         │  └──────────┬──────────┘  │
 │             │             │         │             │             │
 │   Servicios │ Internos    │         │             │ Consultas   │
 │             ▼             │         │             ▼             │
 │  ┌─────────────────────┐  │         │  ┌─────────────────────┐  │
 │  │  Validación Negocio │  │         │  │ Memoria Conversac.  │  │
 │  │  select_for_update  │  │         │  │  (HacuMemoryDB)     │  │
 │  └──────────┬──────────┘  │         │  └──────────┬──────────┘  │
 └─────────────┼─────────────┘         └─────────────┼─────────────┘
               │                                     │
    Transacc.  │ SQL Directo                         │ SQLite WAL
    ACID       ▼                                     ▼
 ┌───────────────────────────┐         ┌───────────────────────────┐
 │   POSTGRESQL (Local LAN)  │         │     hacu_memory.db        │
 │   Base: inventario_audacia│         │    (short_term_history,   │
 │   (Productos, Existencias,│         │     episodic_memory)      │
 │    Movimientos, Auditoría)│         └───────────────────────────┘
 └───────────────────────────┘                       ▲
               ▲                                     │ Consulta
               │ API REST / JSON Autenticada         │ Semántica
               │ (No SQL Directo)                    ▼
 ┌─────────────┴───────────────────────────────────────────────────┐
 │                         CEREBRO AUDACIA                         │
 │        (Orquestación, Contratos de Agente, Gobernanza)          │
 │                                                                 │
 │  • Enrutamiento por capacidades                                 │
 │  • Cero posesión de bases de datos operativas ajenas            │
 │  • Adaptadores y contratos tipados (AgentContract)              │
 └─────────────────────────────┬───────────────────────────────────┘
                               │
               Lectura         │ Ingesta / Reindexación Idempotente
               Documental      ▼
               ┌───────────────────────────────┐
               │         ChromaDB Local        │
               │        (chroma_db/)           │
               │  - audacia_knowledge          │
               │  - universidad_knowledge      │
               └───────────────▲───────────────┘
                               │
                               │ Parsing & Chunking
                               │
               ┌───────────────┴───────────────┐
               │    documents/*.md (Git)       │
               │ (Fuente de Verdad Documental) │
               └───────────────────────────────┘

═════════════════════════════════════════════════════════════════════════════════════
 REGLA DE ORO: Ningún componente accede a la base de datos de otro componente sin
 pasar por su interfaz autorizada (API / Servicios de Dominio).
═════════════════════════════════════════════════════════════════════════════════════
```

---

## 8. RIESGOS, LIMITACIONES Y DECISIONES PENDIENTES

### 8.1. Riesgos Identificados
1. **Riesgo de Inconsistencia Transaccional por SQLite:** Mientras el desarrollo y las pruebas se realicen en SQLite, no hay garantía real de que el código concurrente esté protegido contra condiciones de carrera, dado que SQLite ignora `select_for_update()`.
2. **Discrepancia en Configuración de Aislamiento de PostgreSQL:** El comentario en `settings.py:L96` menciona `SERIALIZABLE`, pero el parámetro real inyectado es `options="-c default_transaction_isolation=read committed"`. Si bien `READ COMMITTED` es adecuado para la mayoría de los casos cuando se usa `select_for_update()`, la contradicción entre documentación y código debe subsanarse formalmente al migrar a PostgreSQL.
3. **Ausencia de Dependencias de HACU en Entorno Local:** La persistencia vectorial de ChromaDB y el modelo de embeddings multilingüe no pueden verificarse en ejecución actualmente debido a que las bibliotecas no están instaladas en el `.venv`.
4. **Volatilidad de Contextos en Haku Inventario:** `LocMemCache` borra los contextos de conversación si el servidor Django se reinicia durante una jornada operativa (aunque las propuestas en sesión HTTP sobrevivan).

### 8.2. Decisiones Pendientes (Para Siguientes Fases)
- **Decisión 1:** Definir si los futuros agentes requerirán un servicio de base de datos relacional propio o si se aprovisionarán esquemas aislados dentro de la misma instancia de PostgreSQL mediante scripts de aprovisionamiento automáticos.
- **Decisión 2:** Establecer la especificación del token o firma de autenticación que utilizará `cerebro_audacia` para autenticarse ante los endpoints del Sistema de Inventario.

---

## 9. CRITERIOS PARA VALIDAR POSTERIORMENTE POSTGRESQL Y CONCURRENCIA

Antes de declarar que el sistema de inventario y sus agentes están listos para producción en la LAN, se deberán cumplir los siguientes criterios obligatorios:

1. **Activación de PostgreSQL Local:**
   Configurar `DB_ENGINE=postgresql` en `sistema_inventario/.env`, levantar el servicio local en el puerto `5432`, ejecutar `python manage.py migrate` y verificar que las 23 tablas y restricciones relacionales se crean adecuadamente en PostgreSQL.
2. **Suite de Pruebas de Estrés y Concurrencia:**
   Implementar una prueba automatizada de concurrencia que:
   - Lance simultáneamente entre 5 y 10 hilos (`ThreadPoolExecutor`) o procesos concurrentes intentando ejecutar salidas sobre una `Existencia` con stock disponible limitado (ej. stock = 2, cada hilo pide 1 unidad).
   - Verifique que exactamente 2 operaciones tengan éxito y las restantes sean rechazadas limpiamente con `ValidationError` o reintentadas sin generar inconsistencias.
   - Verifique que el stock disponible final sea exactamente 0 y nunca un número negativo.
   - Verifique que se hayan creado exactamente 2 registros de `Movimiento` y 2 de `Auditoria`.
3. **Comprobación de Bloqueo de Fila Efectivo:**
   Habilitar el log de sentencias y bloqueos en PostgreSQL (`log_lock_waits = on`, `deadlock_timeout = 1000ms`) y comprobar en los registros del motor que las consultas emiten `SELECT ... FOR UPDATE` y retienen el bloqueo de la fila hasta el `COMMIT` de la transacción.

---

## 10. RELACIÓN DE ARCHIVOS INSPECCIONADOS Y COMANDOS EJECUTADOS

### Archivos Inspeccionados (Solo Lectura):
- `sistema_inventario/inventario_audacia/settings.py`: Inspección de `DATABASES`, `CACHES`, `INSTALLED_APPS`, sesiones y configuración transaccional.
- `sistema_inventario/.env`: Verificación de variables activas de entorno (`DB_ENGINE=sqlite3`, puertos, credenciales locales).
- `sistema_inventario/inventario/models.py`: Inspección de modelos de datos maestros (`Almacen`, `Producto`, `Categoria`, `Ubicacion`) y transaccionales (`Existencia`, `Movimiento`, `Prestamo`, `ConteoFisico`, `Auditoria`).
- `sistema_inventario/cuentas/models.py`: Inspección del modelo `UsuarioAudacIA`, roles y permisos de acceso a almacenes.
- `sistema_inventario/integraciones/haku/admin.py`: Verificación de ausencia de modelos propios.
- `sistema_inventario/integraciones/haku/services.py`: Inspección de lógica de `ejecutar_confirmacion_propuesta`, `transaction.atomic()` y `select_for_update()`.
- `sistema_inventario/integraciones/haku/cache.py`: Verificación del uso de `LocMemCache` y estrategias de invalidación.
- `sistema_inventario/integraciones/haku/tests_contexto.py`: Inspección de pruebas unitarias sobre transacciones y stock insuficiente.
- `hacu/config.py`: Inspección de rutas de `PROJECT_ROOT`, `ModelConfig`, `RagConfig` y `MemoryConfig`.
- `hacu/memory.py`: Inspección de `HacuMemoryDB`, tablas SQLite (`short_term_history`, `episodic_memory`), PRAGMA WAL y bloqueos con RLock.
- `hacu/rag.py`: Inspección de inicialización de `ChromaDB.PersistentClient`, nombres de colecciones e ingesta.
- `cerebro_audacia/README.md`: Revisión de principios y delimitación de responsabilidades.

### Comandos de Solo Lectura Ejecutados:
- `scratch/inspect_db.py`: Script de lectura de bajo nivel sobre `sistema_inventario/db.sqlite3` para inventariar tablas, columnas y registros existentes sin realizar mutaciones.
- `grep_search`: Búsqueda de ocurrencias de `DATABASES`, `transaction`, `select_for_update`, `chromadb`, y referencias cruzadas entre módulos.
- `list_dir`: Verificación de existencia de carpetas, migraciones y archivos en `cerebro_audacia/`, `hacu/`, `sistema_inventario/`.
