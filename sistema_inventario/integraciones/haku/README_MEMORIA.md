# Haku Inventario — Memory Layer (Memoria Operacional Temporal)

## 1. Arquitectura y Separación de Responsabilidades

Haku Inventario implementa una capa de memoria en dos niveles completamente desacoplada:

```
Usuario
   │
Django Authentication (django.contrib.sessions)
   │ 
   ├── Duración: 7 días
   ├── Almacenamiento: Base de datos / Cookie firmada
   └── Responsabilidad: Identidad, roles y permisos de acceso
   │
Haku Operational Context (LocMemCache RAM)
   │
   ├── Memoria Conversacional / Contexto Operativo:
   │   ├── Duración (TTL): 2 horas (7200 segundos de inactividad)
   │   ├── Configuración: HAKU_CONTEXT_TIMEOUT = 7200
   │   └── Responsabilidad: Foco operacional, resolución de referencias ("ese cable", "de esos")
   │
   └── Propuestas Transaccionales (Entrada, Salida, Préstamo, Devolución):
       ├── Duración (TTL): 30 minutos (1800 segundos)
       ├── Configuración: HAKU_PROPOSAL_TIMEOUT = 1800
       └── Responsabilidad: Pre-confirmación en 2 pasos de movimientos que mutan stock
```

> **Principio de Diseño:**
> *"No es memoria conversacional completa, es memoria operacional temporal para continuidad operativa, confirmaciones y referencias de productos."*

---

## 2. Parámetros del Sistema y Desacoplamiento de TTLs

| Parámetro | Configuración | Valor | Justificación Operativa |
|---|---|---|---|
| **Autenticación Web** | `SESSION_COOKIE_AGE` | 7 días | Duración de sesión web en Django. Permite operar durante turnos de trabajo. |
| **Memoria Conversacional** | `HAKU_CONTEXT_TIMEOUT` | 2 horas (7200s) | Retiene el último producto consultado, búsqueda y candidatos para consultas diferidas. |
| **Propuestas Transaccionales** | `HAKU_PROPOSAL_TIMEOUT` | 30 minutos (1800s) | Evita errores humanos de confirmación contra estados físicos de inventario obsoletos. |
| **Capacidad Máxima** | `MAX_ENTRIES` | 50 entradas | Concurrencia de hasta 50 sesiones simultáneas en RAM (población real: 2-10 usuarios). |
| **Política de Desalojo** | `CULL_FREQUENCY=2` | LRU | Desalojo fraccional automático en LocMemCache si se alcanza el límite. |
| **Candidatos Máximos** | `MAX_CANDIDATOS` | 3 elementos | Datos mínimos útiles (`id`, `nombre`, `codigo`) para desambiguación. |
| **Últimas Acciones** | `MAX_ULTIMAS_ACCIONES` | 5 elementos | Acciones operacionales deslizantes puramente estructurales (sin texto ni audio). |

---

## 3. Jerarquía Estricta de Resolución Lingüística

Al recibir referencias demostrativas o coloquiales costeñas (`"ese producto"`, `"ese cable"`, `"el anterior"`, `"de esos"`), la intención se resuelve según la siguiente prioridad estricta:

1. **Prioridad 1 — Último producto explícitamente mencionado en el mensaje actual:**
   - Si el mensaje contiene código o nombre (ej. *"sácame dos de esos cables HDMI"*), se prioriza de inmediato el producto del texto.
2. **Prioridad 2 — Último producto consultado:**
   - Si no hay producto en el mensaje, se toma el producto en foco activo en memoria RAM (`estado_operativo.producto_id`).
3. **Prioridad 3 — Último candidato válido:**
   - Si no hay producto en foco pero existen candidatos previos en contexto, se utiliza el candidato de desambiguación (`candidatos[0]`).
4. **Protección contra acciones antiguas:**
   - Nunca se utiliza una acción antigua de `ultimas_acciones` si existe riesgo de equivocación.
   - **Secuencia validada:** `Busca HDMI` -> `Consulta stock HDMI` -> `Busca ponchadora` -> `Sácame dos de esos` = **Ponchadora** (NUNCA HDMI).

---

## 4. Revalidación Transaccional en Base de Datos

Al confirmar una propuesta transaccional, **NUNCA** se confía en la información en memoria. En una transacción atómica `select_for_update()` se revalidan en tiempo real:

1. **Usuario activo:** Se rechaza de inmediato si el usuario fue desactivado o eliminado.
2. **Permisos y Almacén:** Se verifica que el almacén siga activo y que el usuario mantenga permisos de acceso.
3. **Producto:** Se bloquea el registro en DB y se verifica que no haya sido archivado o dado de baja.
4. **Existencia y Stock disponible:** Se evalúa en tiempo real si el stock físico es suficiente para la operación (prohibición estricta de saldo negativo).

---

## 5. Concurrencia de Transcripción y Convivencia de Procesos

Para gobernar el acceso a recursos y asegurar estabilidad ante ráfagas concurrentes:
- **`HAKU_GPU_LOCK` (Intra-proceso):** Cerrojo reentrante (`threading.RLock`) exclusivo del worker web de Django. Serializa solicitudes de audio concurrentes entre hilos web. Si una solicitud espera más del timeout configurado (10s), devuelve `HakuOcupadoError` controlado (HTTP 503) con mensaje *"HACU Inventario ocupado procesando otra solicitud. Intenta nuevamente."*.
- **Convivencia Inter-procesos (HACU Visitante y HACU Inventario):** No se realiza mediante cerrojos de Python (los cuales son invisibles entre procesos de Windows). Se gobierna mediante:
  1. **Hardware / Driver CUDA:** Multiplexado temporal nativo a nivel de contexto CUDA en GPU.
  2. **Fallback automático a CPU int8 en Faster-Whisper:** Si la memoria VRAM no puede asignar el modelo en GPU, se degrada elegantemente a CPU sin colapsar el proceso ni generar `CUDA Out of Memory`.
  3. **Inferencia LLM compartida:** Centralizada en el demonio HTTP `llama-server.exe` (`127.0.0.1:8080`), cuya cola nativa de slots serializa peticiones entre distintos clientes.
- **Garantía:** Cero bloqueos de servidor, captura de excepciones controladas y preservación estricta de la integridad de los datos.

---

## 6. Simulación de Reinicio de Servidor (Pérdida de RAM)

Si el servidor Django se reinicia o se vacía la memoria RAM:
1. `HakuOperationalContext` en `LocMemCache` se limpia a estado inicial.
2. La sesión web del usuario en SQLite (`django_session`) permanece **válida e intacta** (7 días).
3. Si el usuario solicita *"sácame tres de esos"*, el sistema responde:
   `"No tengo un producto asociado actualmente. Indícame qué producto deseas retirar."`
4. No se ejecuta ninguna transacción errónea y la sesión web **no se cierra**.

---

## 7. Medición Real de Consumo de Memoria RAM (50 Contextos)

Medición profunda realizada en `tests_contexto_seguridad.py` con 50 contextos completamente cargados (1 foco + 3 candidatos + 1 propuesta + 5 acciones operacionales):

- **Memoria total para 50 contextos:** ~338 KB (**0.32 MB**).
- **Memoria promedio por contexto:** ~6.7 KB.
- **Límite operacional establecido:** < 10 MB RAM.
- **Margen de seguridad:** El sistema consume apenas el **3.2%** del límite máximo permitido de 10 MB.

