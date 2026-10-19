# AUDACIA — ARQUITECTURA GENERAL DEL SISTEMA
## Diseño Maestro de Infraestructura, Red LAN, Servicios, Inferencia, Agentes y Persistencia en la Universidad Simón Bolívar

- **Versión:** 1.2.0 (Alcance Definitivo: Red Universitaria Simón Bolívar a Coste $0)
- **Fecha:** 2026-10-15
- **Estado:** Propuesto — Revisión Institucional Aprobada
- **Ámbito:** Servidor central Windows de AudacIA, red del campus de la Universidad Simón Bolívar (Unisimón), clientes móviles/escritorio no administrados, interfaces web de voz.

---

## 1. INTRODUCCIÓN Y REQUISITOS INNEGOCIABLES

Este documento establece la arquitectura maestra integral de AudacIA, diseñada exclusivamente para operar dentro de la infraestructura de red existente de la **Universidad Simón Bolívar (Unisimón)** bajo un presupuesto estricto de **$0 de coste adicional**.

### Requisitos Innegociables Confirmados:
1. **Coste Adicional: $0 Estricto:** No se permite compra de equipos de red (prohibición de micro-routers, puntos de acceso dedicados o hardware auxiliar), ni contratación de VPS, servicios cloud, túneles de pago ni certificados comerciales.
2. **Cero Acceso a la Infraestructura de Red de Campus:** No se tiene acceso administrativo al router, servidor DHCP, DNS institucional, switches ni puntos de acceso Wi-Fi de la universidad.
3. **Administración Exclusiva de Windows en el Servidor:** Solo se tiene control sobre la configuración del sistema operativo Windows del computador servidor central.
4. **Acceso Exclusivo desde la Red Universitaria:** Los usuarios acceden desde dispositivos heterogéneos no administrados (smartphones, tabletas, portátiles) conectados por Wi-Fi o Ethernet a la red de Unisimón.
5. **Cero Exposición a Internet Externo:** El inventario y las aplicaciones internas **jamás se exponen a Internet**. Queda estrictamente prohibido el uso de *port forwarding*, zonas DMZ o túneles públicos (ngrok, Cloudflare Tunnels).
6. **Autonomía Operativa Local:** Los procesos esenciales de negocio se ejecutan localmente en el servidor.
7. **Requisito de Micrófono Web (`getUserMedia`) a $0:** Haku Inventario opera por voz en el navegador. La especificación W3C y los navegadores modernos exigen un **Contexto Seguro** (`window.isSecureContext === true`), requiriendo HTTPS con certificado válido sin exigir certificados privados instalados manualmente en los clientes.

---

## 2. TOPOLOGÍA FÍSICA Y PRESUPUESTO DE HARDWARE / VRAM

### 2.1. Comparativa de Hardware
* **Entorno de Trabajo y Desarrollo Actual:** Portátil con NVIDIA RTX 2000 Ada Generation Laptop GPU, 8.188 MiB (~8.0 GB GDDR6), 32 GB RAM de sistema, Windows 11.
* **Futuro Servidor Central Objetivo:** Servidor dedicado administrado bajo Windows con NVIDIA RTX 5070 Ti, 16.384 MiB (16.0 GB GDDR7), 32 GB RAM o superior.

### 2.2. Formulación Aritmética Exacta de la Memoria de Video (VRAM)
La memoria total consumida en la GPU se rige por:

$$\text{VRAM Total} = \text{Pesos del Modelo (GGUF)} + \text{KV Cache}(n\_ctx) + \text{Buffer de Cómputo/Activaciones} + \text{Sobrecarga de Contexto CUDA / SO}$$

Donde el tamaño de la memoria de contexto (KV Cache) depende de la arquitectura exacta:

$$\text{Bytes por Token KV} = 2 \times \text{Capas} \times \text{Cabezas KV} \times \text{Dimensión de Cabeza} \times \text{Bytes por Valor}$$

$$\text{KV Cache Total (GiB)} = \frac{\text{Bytes por Token KV} \times n\_ctx}{1024^3}$$

#### A. Desglose de Modelos Evaluados:

1. **Llama 3.1 8B Instruct:**
   - Arquitectura: 32 capas, 8 cabezas KV (Grouped-Query Attention), dimensión de cabeza = 128.
   - Bytes por token en FP16 (2 bytes): $2 \times 32 \times 8 \times 128 \times 2 = 131.072\text{ bytes} = 128\text{ KiB/token}$.
   - Bytes por token en Q8_0 (`kv_8bits=True`, 1 byte): $65.536\text{ bytes} = 64\text{ KiB/token}$.
   - Tamaño de pesos: `Q4_K_M` = **4.58 GiB**; `Q6_K` = **6.15 GiB**.
   - Tamaño de KV Cache según contexto:
     - A $n\_ctx = 8.192$: **1.00 GiB** (FP16) / **0.50 GiB** (Q8_0).
     - A $n\_ctx = 16.384$: **2.00 GiB** (FP16) / **1.00 GiB** (Q8_0).
     - A $n\_ctx = 32.768$: **4.00 GiB** (FP16) / **2.00 GiB** (Q8_0).

2. **Qwen 2.5 14B Instruct (Arquitectura Diferente):**
   - Arquitectura: 48 capas, 8 cabezas KV, dimensión de cabeza = 128.
   - Bytes por token en FP16 (2 bytes): $2 \times 48 \times 8 \times 128 \times 2 = 196.608\text{ bytes} = 192\text{ KiB/token}$ (**50% más memoria KV por token que Llama 3.1 8B**).
   - Bytes por token en Q8_0 (1 byte): $98.304\text{ bytes} = 96\text{ KiB/token}$.
   - Tamaño de pesos: `Q4_K_M` = **8.38 GiB**; `Q5_K_M` = **9.76 GiB**.
   - Tamaño de KV Cache según contexto:
     - A $n\_ctx = 8.192$: **1.50 GiB** (FP16) / **0.75 GiB** (Q8_0).
     - A $n\_ctx = 16.384$: **3.00 GiB** (FP16) / **1.50 GiB** (Q8_0).
     - A $n\_ctx = 32.768$: **6.00 GiB** (FP16) / **3.00 GiB** (Q8_0).

3. **Faster-Whisper (Reconocimiento de Voz):**
   - `small` (244M params): **0.50 GiB** en int8_float16 sobre CUDA `[HECHO MEDIDO EN REPOSITORIO]`.
   - `base` (74M params): **0.33 GiB** en FP16 sobre CUDA `[HECHO MEDIDO EN REPOSITORIO]`.
   - `large-v3-turbo` (809M params): **~1.00 GiB en int8**; **~1.60 - 1.80 GiB en FP16** `[ESTIMACIÓN ARITMÉTICA, PENDIENTE DE MEDICIÓN EN SERVIDOR]`.

4. **Sobrecarga del Sistema Operativo Windows:**
   - En Windows (Desktop Window Manager y servicios de pantalla): **0.80 a 1.20 GiB**.

---

### 2.3. Presupuestos Consolidados por Entorno

#### Entorno 1: Computador de Desarrollo Actual (8.0 GB VRAM — RTX 2000 Ada en Windows)
- Margen retenido por Windows: **1.50 GiB**.
- VRAM neta disponible para procesos de IA: **6.50 GiB**.
- **Configuración Viable:**
  - LLM: Llama 3.1 8B `Q4_K_M` (pesos: 4.58 GiB + buffer cómputo: ~0.50 GiB) = **5.08 GiB**.
  - Contexto: $n\_ctx = 8.192$ con `kv_8bits=True` = **0.50 GiB**.
  - STT: Faster-Whisper `base` (FP16) o `small` (int8) = **0.35 - 0.50 GiB**.
  - **Consumo Total:** $5.08 + 0.50 + 0.50 = \mathbf{6.08\text{ GiB}}$.
  - **Margen libre:** **~0.42 GiB**.
- *Restricción Estricta en Desarrollo:* Cualquier intento de subir a 16k tokens en FP16 (+1.5 GiB), usar Llama 3.1 8B en `Q6_K` (+1.57 GiB), cargar Faster-Whisper `large-v3-turbo` (+0.50 GiB adicional) o correr Qwen 2.5 14B provocará un **fallo irrecuperable de CUDA Out-Of-Memory (OOM)**.

#### Entorno 2: Futuro Servidor Central (16.0 GB VRAM — RTX 5070 Ti en Windows)
- Margen retenido por Windows: **1.00 GiB**.
- VRAM neta disponible para IA: **15.00 GiB**.

* **Opción Servidor A (Máxima Velocidad y Estabilidad — Recomendada):**
  - LLM: Llama 3.1 8B `Q6_K` (pesos: 6.15 GiB + buffer: ~0.60 GiB) = **6.75 GiB**.
  - Contexto: $n\_ctx = 16.384$ con FP16 = **2.00 GiB** (o 1.00 GiB en Q8_0).
  - STT: Faster-Whisper `large-v3-turbo` en int8 = **~1.00 GiB** (estimado).
  - **Consumo Total Estimado:** $6.75 + 2.00 + 1.00 = \mathbf{9.75\text{ GiB}}$.
  - **Margen Libre Confortable:** **~5.25 GiB** (permite absorber ráfagas concurrentes o elevar contexto a 32k tokens).

* **Opción Servidor B (Modelo Mayor — Qwen 2.5 14B):**
  - LLM: Qwen 2.5 14B `Q4_K_M` (pesos: 8.38 GiB + buffer: ~0.80 GiB) = **9.18 GiB**.
  - Contexto: $n\_ctx = 16.384$ forzosamente con `kv_8bits=True` = **1.50 GiB** (en FP16 consumiría 3.0 GiB, dejando margen crítico).
  - STT: Faster-Whisper `small` int8 = **0.50 GiB** (o `large-v3-turbo` int8 = 1.00 GiB).
  - **Consumo Total Estimado:** $9.18 + 1.50 + 1.00 = \mathbf{11.68\text{ GiB}}$.
  - **Margen Libre:** **~3.32 GiB**.

---

## 3. ARQUITECTURA DE PROCESOS E INFERENCIA

Para garantizar que **cada modelo resida exactamente UNA VEZ en VRAM**, se define una separación estricta por procesos en Windows:

```
┌────────────────────────────────────────────────────────────────────────┐
│             TOPOLOGÍA DE PROCESOS EN WINDOWS (1 INSTANCIA/GPU)         │
├────────────────────────────────────────────────────────────────────────┤
│                                                                        │
│  [PROCESO 1: llama-server.exe Daemon] ──► Carga Llama 3.1 8B (1 vez)   │
│  • Escucha en 127.0.0.1:8080 (HTTP OpenAI-compatible).                 │
│  • Cola secuencial FIFO (1 turno activo para KV Cache estable).        │
│                                                                        │
│  [PROCESO 2: audacia-stt-daemon] ───────► Carga Faster-Whisper (1 vez) │
│  • Escucha en 127.0.0.1:8001 (Microservicio local de transcripción).   │
│  • Procesa buffers de audio en cola secuencial protegida.              │
│                                                                        │
│  [PROCESO 3: Django Web Server (Waitress/Uvicorn)] ──► 0 MB en VRAM    │
│  • Procesa HTTP/WebSockets y lógica determinista de inventario.        │
│  • Delega transcripción a :8001 y síntesis a Piper (CPU multihilo).   │
│                                                                        │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 4. RED UNIVERSITARIA, DNS Y ACCESO HTTPS A PRESUPUESTO $0

De acuerdo con lo establecido en [ADR-002-red-lan-y-acceso.md](file:///c:/Users/AudacIA/Documents/inventario/voz-rag-universidad/cerebro_audacia/documentacion/ADR-002-red-lan-y-acceso.md):

### 4.1. Distinción Expresa de los Cinco Aspectos de Red
1. **Acceso desde la Red Universitaria:** La plataforma reside en una IP privada interna de campus (`10.x.x.x` o `172.16-31.x.x`). Los clientes acceden por Wi-Fi o Ethernet de Unisimón.
2. **Cero Acceso desde Internet Externo:** El cortafuegos perimetral de Unisimón bloquea conexiones entrantes de Internet. No hay NAT 1:1, port forwarding ni túneles. Los servicios son inaccesibles desde fuera del campus.
3. **HTTPS y Micrófono Web (`getUserMedia`):** Habilitado mediante un dominio que posea certificado de CA pública reconocida, garantizando el contexto seguro (`isSecureContext = true`) sin instalar certificados manuales en los clientes.
4. **Operación Durante Interrupciones de Internet Externo:** Toda la lógica de negocio, bases de datos e inferencia operan localmente. Si la salida a Internet de la universidad cae, los clientes con DNS institucional o caché local activa continúan operando con normalidad.
5. **Limitaciones por No Administrar la Red Institucional:** El servidor depende de una IP estática configurada en Windows fuera del pool DHCP para evitar colisiones. En redes Wi-Fi con *Client Isolation* activo, el acceso solo será posible desde Wi-Fi institucional o Ethernet cableada.

---

### 4.2. Evaluación de Alternativas de Acceso a Coste $0

```
┌────────────────────────────────────────────────────────────────────────┐
│                     ALTERNATIVAS A PRESUPUESTO $0                      │
├──────────────────────────────────┬─────────────────────────────────────┤
│ 1. Subdominio Institucional      │ audacia.unisimon.edu.co             │
│    (Recomendación Principal)     │ • DNS interno del campus (TI)       │
│                                  │ • Certificado institucional Unisimón│
│                                  │ • 100% offline, seguro y a coste $0 │
├──────────────────────────────────┼─────────────────────────────────────┤
│ 2. Subdominio Gratuito Autónomo  │ audacia-usb.duckdns.org             │
│    (Alternativa de Contingencia) │ • A-Record -> IP privada de campus  │
│                                  │ • Let's Encrypt ACME DNS-01 ($0)    │
│                                  │ • Autónomo sin esperar trámite TI   │
└──────────────────────────────────┴─────────────────────────────────────┘
```

#### Plan de Acción para la Opción 1 (Institucional Unisimón):
AudacIA solicitará a la Dirección de TI de la Universidad Simón Bolívar:
1. Asignación de IP fija privada en la subred de laboratorios.
2. Registro `A` en los servidores DNS internos del campus: `audacia.unisimon.edu.co` $\rightarrow$ `IP_PRIVADA`.
3. Inclusión en el certificado TLS institucional (wildcard `*.unisimon.edu.co` o certificado firmado).

#### Contingencia con la Opción 2 (DuckDNS Autónomo a $0):
Si la gestión ante TI de Unisimón toma tiempo, se utiliza de inmediato un subdominio gratuito (`audacia-usb.duckdns.org`) apuntando a la IP privada de campus con Caddy en Windows renovando automáticamente certificados gratuitos de Let's Encrypt mediante el desafío ACME DNS-01 ($0 y sin puertos abiertos).

---

## 5. DELIMITACIÓN CLARA DE SUBSISTEMAS Y EL ACCESO A HACU VISITANTE

### 5.1. Clarificación del Modelo de Despliegue de HACU Visitante (`hacu/`)
`[HECHO VERIFICADO EN REPOSITORIO]` El código actual de HACU ([run_hacu.py](file:///c:/Users/AudacIA/Documents/inventario/voz-rag-universidad/run_hacu.py)) es una **aplicación de escritorio (PyQt) o terminal (CLI)** diseñada para hardware local:
- Captura audio directamente del micrófono físico enchufado a la máquina.
- Reproduce sonido por los altavoces locales con Piper.
- Renderiza la ventana gráfica en la pantalla del computador.
- **NO EXISTE NINGUNA API WEB NI ENDPOINT HTTP EN `hacu/` ACTUALMENTE.**

#### Alternativas de Despliegue para HACU:
1. **Alternativa 1 — Kiosko / Estación Física de Stand (Modelo Actual):**
   HACU se ejecuta en el PC del stand con su pantalla táctil, micrófono y altavoces USB locales. Consulta al proveedor de inferencia (`llama-server`) en el servidor central vía LAN. No requiere interfaz web para clientes.
2. **Alternativa 2 — Servidor Central con Periféricos Físicos:**
   Solo aplica si el servidor central se ubica físicamente en el stand de exhibición. Inviable si el servidor reside en un rack o sala técnica cerrada.
3. **Alternativa 3 — Futura API Web para Navegadores (`[FASE POSTERIOR]`):**
   Requerirá desarrollar un adaptador web (ej. endpoints FastAPI o Django) que desacople el core de diálogo (`hacu/session.py`, `context.py`, `rag.py`) de la interfaz Qt, permitiendo que navegadores en la LAN conversen por texto/audio.

---

### 5.2. Haku Inventario (`sistema_inventario/integraciones/haku/`)
- `[CÓDIGO PRESENTE NO VALIDADO EN PRODUCCIÓN]`
- 100% determinista. Piloto de voz para bodega.
- Backend en Django. Frontend web cliente captura audio mediante `getUserMedia()` y lo envía a `/api/haku/voz/transcribir/`.
- Reglas de negocio atómicas en base de datos. Congelado funcionalmente.

---

### 5.3. Cerebro Audacia (`cerebro_audacia/`)
- `[ESTRUCTURA INICIAL — CERO CÓDIGO EJECUTABLE]`
- Define contratos (`AgentContract`), enrutamiento por intenciones y gobernanza de documentos.
- **Límites innegociables:** No posee la base de datos de inventario, no ejecuta sentencias SQL libres y no intermedia peticiones web ordinarias de Django.

---

## 6. PERSISTENCIA POLÍGLOTA, AUDITORÍA Y SEGURIDAD

De acuerdo con lo formalizado en [ADR-001-arquitectura-de-datos.md](file:///c:/Users/AudacIA/Documents/inventario/voz-rag-universidad/cerebro_audacia/documentacion/ADR-001-arquitectura-de-datos.md):

1. **Fuente de Verdad de Inventario:**
   - Desarrollo: SQLite 3 (`db.sqlite3`) `[ACTIVO]`.
   - Producción Objetivo: PostgreSQL 16 local en Windows `[CONFIGURACIÓN PREVISTA]`.
   - Bloqueo pesimista `select_for_update()` solo opera eficazmente en PostgreSQL.
2. **Memoria de HACU:**
   - SQLite local en modo WAL ([hacu/memory.py](file:///c:/Users/AudacIA/Documents/inventario/voz-rag-universidad/hacu/memory.py)). Cero acceso a datos de bodega.
3. **RAG Documental:**
   - Almacén vectorial ChromaDB. Los 12 archivos Markdown de [documents/](file:///c:/Users/AudacIA/Documents/inventario/voz-rag-universidad/documents/) son la fuente de verdad estática. ChromaDB es un índice derivado reconstruible.
4. **Verificación Real de Inmutabilidad de Auditoría:**
   - La tabla [Auditoria](file:///c:/Users/AudacIA/Documents/inventario/voz-rag-universidad/sistema_inventario/inventario/models.py#L815) restringe permisos a nivel de aplicación Django (`add`, `view`).
   - `[ACLARACIÓN CRÍTICA]`: **No es inmutabilidad criptográfica**. Un superusuario en consola (`manage.py shell`) o un administrador con acceso directo a `psql` / `sqlite3` puede modificar o borrar registros. Se debe restringir el acceso a nivel de credenciales del motor de base de datos.

---

## 7. MATRIZ DE DEGRADACIÓN ANTE FALLOS

| Escenario de Falla | Componentes Afectados | Componentes Operativos | Experiencia del Usuario |
|---|---|---|---|
| **Internet Externo Caído (Campus 100% activo)** | Renovación de certificados TLS (solo si vence ventana de 90 días) y resolución en opción DuckDNS para clientes nuevos. | **Inventario completo, Haku Voz, HACU Stand, RAG, Inferencia LLM, Autenticación.** | Con Opción 1 (DNS Unisimón), 100% de los clientes operan sin notar el corte de Internet. |
| **Proveedor Inferencia Caído (`llama-server`)** | Respuestas de lenguaje natural de HACU. | **Haku Inventario (100% determinista), Django Web, Gestión de bodega, Auditoría.** | HACU advierte que el módulo de diálogo está en mantenimiento. Las operaciones físicas de bodega continúan al 100%. |
| **Daemon STT Caído (Faster-Whisper)** | Comandos hablados de voz en bodega. | **Interfaz web táctil de Django, formularios, consultas de stock, diálogo de HACU.** | La interfaz web de Haku conmuta automáticamente a modo teclado/formulario. |
| **Punto de Acceso Wi-Fi de Campus Reiniciado** | Conectividad inalámbrica local. | El servidor sigue activo para clientes Ethernet. | Sin red Wi-Fi momentánea. Requiere esperar reconexión automática del AP. |
| **Corrupción de Base Vectorial (ChromaDB)** | Recuperación de fragmentos documentales. | **Inventario, Haku Voz, LLM sin contexto documental.** | Se ejecuta script de reindexación automática desde `documents/` en ~30 segundos. |

---

## 8. DECISIONES PENDIENTES DE VERIFICACIÓN INSTITUCIONAL Y DE HARDWARE

Las siguientes 6 decisiones arquitectónicas no pueden resolverse en el repositorio y exigen comprobación in situ en la Universidad Simón Bolívar o sobre el hardware final:

| Decisión Pendiente | Condición Desconocida | Método de Verificación Obligatorio |
|---|---|---|
| **1. Trámite de Subdominio en TI Unisimón** | Disponibilidad y plazo de TI de Unisimón para crear `audacia.unisimon.edu.co` y entregar certificado institucional. | Solicitud formal de intranet ante la Dirección de TI de Unisimón. |
| **2. DNS Rebind Protection en Firewall de Campus** | Si el firewall institucional descarta resoluciones públicas a IPs privadas en la Opción 2. | Ejecutar `nslookup audacia-usb.duckdns.org` desde la Wi-Fi de Unisimón. |
| **3. Client Isolation en Wi-Fi de Campus** | Si la red Wi-Fi institucional permite tráfico hacia la IP del servidor Windows. | Prueba de conexión TCP al puerto 443 del servidor desde un smartphone conectado a la Wi-Fi de campus. |
| **4. Concurrencia STT + LLM en RTX 5070 Ti** | Latencia real bajo competencia de núcleos CUDA entre Faster-Whisper y llama.cpp en Windows. | Prueba de estrés simultánea de generación de tokens y transcripción de voz en el servidor final. |
| **5. Transacciones Concurrentes en PostgreSQL** | Comportamiento real de `select_for_update()` bajo múltiples hilos en PostgreSQL bajo Windows. | Suite de pruebas multihilo con `ThreadPoolExecutor` en PostgreSQL local. |
| **6. Ubicación Física de HACU** | Si operará en kiosko con PC propio en stand o si debe correr en el servidor central. | Definición logística del evento o exhibición en campus. |
