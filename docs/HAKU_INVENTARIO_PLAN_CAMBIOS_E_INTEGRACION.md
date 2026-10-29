# PLAN DE CAMBIOS E INTEGRACIÓN TÉCNICA — HAKU INVENTARIO

## 1. Objetivo

El objetivo de esta fase es consolidar y ampliar funcionalmente **HAKU Inventario** como un agente conversacional y motor de servicios modular, seguro y de alta disponibilidad, preparado para integrarse limpiamente como una sección independiente dentro de la futura página web general de AudacIA (desarrollada en paralelo por otro compañero de equipo en otra rama de Git).

Se abordarán de manera prioritaria e innegociable:
1. La ampliación funcional del módulo para resolver consultas de reposición («¿Qué hace falta por pedir?», productos agotados, bajo mínimo y consumo estimado sin inventar datos).
2. La distinción funcional y técnica estricta entre **PRÉSTAMO** (salida temporal con seguimiento de devolución y destinatario obligatorio) y **DOTACIÓN** (salida definitiva de inventario).
3. La **normalización robusta de nombres de personas** para destinatarios de préstamos, garantizando que el sistema jamás asigne nombres incorrectos derivados de transcripciones de voz defectuosas y exigiendo entrada escrita en caso de duda.
4. Soporte para consumibles de uso gradual vs. artículos reutilizables.
5. Optimización de rendimiento mediante **caché en memoria con políticas de consistencia e invalidación atómica** (`transaction.on_commit`).
6. Delimitación de contratos de API REST/JSON e interfaz gráfica modular para que el desarrollador de la web general pueda embeber HAKU sin colisiones de código ni sobreescritura de archivos compartidos.

---

## 2. Alcance y Separación de Responsabilidades

| Responsabilidad de HAKU Inventario (Esta Rama: `Haku-Inventario`) | Responsabilidad de la Futura Web General (Rama Paralela) | Fuera del Alcance (Excluido) |
|---|---|---|
| • Lógica conversacional y comprensión semántica de inventario (`integraciones/haku/lenguaje/`). | • Maquetación global del portal institucional de AudacIA. | • HAKU Visitante y sus submódulos. |
| • Servicios de negocio de inventario, stock, movimientos, préstamos y reposición (`integraciones/haku/services.py`). | • Layouts y plantillas base generales (`base.html` institucional, header principal, footer global). | • Cerebro Central y su orquestación. |
| • Endpoints JSON bajo `/api/haku/` (consulta, propuesta, confirmación, reposición). | • Enrutador principal de navegación general del sitio. | • Cambios destructivos en esquemas de BD preexistentes o reseteo de Git. |
| • Interfaz modular propia de HAKU (`panel_voz.html`, componente autónomo de chat/voz y tarjetas de datos). | • Integración del contenedor iframe, web component o div de anclaje de HAKU en la barra de navegación web. | • Reescritura de modelos base de `inventario` o `cuentas` que rompa compatibilidad. |
| • Cache manager (`HakuCacheManager`), invalidaciones y aislamiento de contexto conversacional. | • Estilos CSS globales ajenos al módulo HAKU. | • Modificación de ramas ajenas o rebase/merge no coordinado. |

---

## 3. Estado Inicial Verificado en el Repositorio

- **Rama Git actual:** `Haku-Inventario` (commit `846a622`).
- **Estado de trabajo:** Existen cambios no confirmados propios de la fase anterior (multialmacén, histórico temporal, `UsuarioAudacIA` con M2M a almacenes y campo `acceso_todos_almacenes`). No hay worktrees concurrentes en conflicto.
- **Modelos clave existentes:**
  - `inventario.Producto`: Soporta `stock_minimo`, `stock_inicial`, `codigo_interno`, `activo`, `archivado`.
  - `inventario.Existencia`: Maneja `stock_disponible` y `stock_en_prestamo` por almacén.
  - `inventario.Movimiento`: Inmutable, con `Tipo.PRESTAMO`, `Tipo.SALIDA`, `Tipo.ENTRADA`, `Tipo.DEVOLUCION`, `clave_idempotencia`, `destinatario` (CharField), `stock_disponible_anterior` y `posterior`.
  - `inventario.Prestamo`: Vinculado a `movimiento`, `producto`, `almacen`, `destinatario` (CharField), con `cantidad_original`, `cantidad_pendiente_devolucion`, `estado` (`ABIERTO`, `DEVUELTO_PARCIAL`, `CERRADO`).
  - `cuentas.UsuarioAudacIA`: Usuarios del sistema con roles `SUPERUSUARIO`, `ADMINISTRADOR`, `USUARIO`.
- **Caché actual:** `HakuCacheManager` en `integraciones/haku/cache.py` gestiona alias `haku_catalogo`, `haku_ubicaciones` y `haku_stock` con invalidación por señales de Django (`post_save`/`post_delete`).
- **Lenguaje y normalización:** `normalizador.py` cuenta con motor semántico `SemanticFrame` y `IntencionTipo`, pero carece de intención específica de reposición («¿Qué hace falta por pedir?») y el matcher de destinatarios por voz es susceptible a errores si no se valida contra personas registradas o se pide confirmación tipográfica.

---

## 4. Archivos Previstos para Modificar

1. `sistema_inventario/integraciones/haku/lenguaje/normalizador.py`:
   - *Función actual:* Extracción de intenciones, entidades y contexto conversacional.
   - *Motivo del cambio:* Incorporar la intención `CONSULTAR_FALTANTES_REPOSICION`, patrones de frases («¿Qué hace falta por pedir?», «¿Qué está por acabarse?», «¿Qué tengo que comprar?»), distinción de préstamos vs. dotaciones, y detección estricta de ambigüedad en nombres de destinatarios.
2. `sistema_inventario/integraciones/haku/services.py`:
   - *Función actual:* Orquestación de lógica de negocio, preparación de propuestas de stock y confirmaciones atómicas.
   - *Motivo del cambio:* Implementar el servicio `consultar_faltantes_reposicion` (agotados, bajo mínimo, estimación prudente de consumo sin inventar), distinguir préstamos y dotaciones en las salidas, requerir nombre escrito si el destinatario no es seguro, y soporte para consumibles graduales.
3. `sistema_inventario/integraciones/haku/cache.py`:
   - *Función actual:* Gestor de caché en RAM para catálogo, ubicaciones y stock.
   - *Motivo del cambio:* Añadir invalidación post-commit (`transaction.on_commit`), caché para faltantes/reposición e índice de destinatarios canónicos sin exponer datos entre usuarios.
4. `sistema_inventario/integraciones/haku/views.py`:
   - *Función actual:* Controladores de API JSON y renderizado de panel.
   - *Motivo del cambio:* Exponer endpoint `/api/haku/faltantes-reposicion/` y enriquecer respuestas conversacionales con estructuras JSON consumibles por la web.
5. `sistema_inventario/integraciones/haku/urls.py`:
   - *Función actual:* Rutas internas de HAKU.
   - *Motivo del cambio:* Registrar nuevas rutas especializadas manteniendo retrocompatibilidad total.
6. `sistema_inventario/static/js/haku_voz.js`:
   - *Función actual:* Controlador cliente de chat, Push-to-Talk y renderizado de tarjetas.
   - *Motivo del cambio:* Añadir interfaz de aclaración de destinatario (campo de texto interactivo cuando la voz sea dudosa), presentación visual de faltantes de reposición y confirmación explícita con nombre canónico visible.

---

## 5. Archivos Nuevos Previstos

1. `sistema_inventario/integraciones/haku/servicios_personas.py`:
   - *Finalidad:* Módulo modular y aislado para normalización Unicode, comparación fonética prudente y resolución de destinatarios canónicos (a partir de usuarios del sistema y préstamos históricos), protegiendo la identidad de las personas.
2. `sistema_inventario/integraciones/haku/tests_ampliacion_haku.py`:
   - *Finalidad:* Suite completa de pruebas unitarias y de integración que cubra: reposición/faltantes, préstamos vs dotaciones, normalización y solicitud de escritura de nombres, consumibles graduales, invalidación de caché y concurrencia.
3. `docs/HAKU_INVENTARIO_PLAN_CAMBIOS_E_INTEGRACION.md`:
   - *Finalidad:* Este documento de coordinación, auditoría y transferencia.

---

## 6. Archivos Compartidos que se Evitarán Proactivamente

Para no interferir con la rama del desarrollador de la web general, se establecen las siguientes protecciones:
- **`inventario/views.py` y `cuentas/views.py`:** No se tocarán las vistas tradicionales de la web. Toda la API de HAKU reside en `integraciones/haku/views.py`.
- **`inventario/urls.py` y `inventario_audacia/urls.py`:** Las rutas de HAKU se confinan a `integraciones/haku/urls.py` (cargadas bajo `/api/haku/`), por lo que el enrutador general no sufrirá modificaciones.
- **Plantillas base globales (`templates/base.html`):** HAKU empaqueta su interfaz en `templates/haku/panel_voz.html` y `templates/haku/widget_flotante.html`. El desarrollador de la web general solo necesitará invocar un include o consumir el endpoint JSON sin que nosotros toquemos su layout maestro.
- **Estilos globales (`static/css/styles.css`):** HAKU mantiene su encapsulamiento CSS en sus propias plantillas y tokens.

---

## 7. Contrato entre Frontend y Backend de HAKU

Todas las peticiones respetan `Content-Type: application/json` (o `multipart/form-data` en audio), autenticación por sesión Django y CSRF token via `X-CSRFToken`.

### A. Endpoint Conversacional Principal: `POST /api/haku/mensaje/`
- **Payload Request:**
  ```json
  {
    "mensaje": "¿Qué hace falta por pedir?",
    "modo": "texto"
  }
  ```
- **Response Estructurada:**
  ```json
  {
    "tipo": "consulta",
    "mensaje_pantalla": "### Productos que requieren reposición:\n...",
    "mensaje_verbal": "Hay 3 productos que requieren reposición...",
    "datos": {
      "agotados": [...],
      "bajo_stock": [...],
      "proximos_a_agotarse": [...],
      "recomendaciones": [...]
    }
  }
  ```

### B. Endpoint Especializado de Reposición: `GET /api/haku/faltantes-reposicion/`
- **Query Params:** `?almacen_id=1&categoria_id=2`
- **Response:**
  ```json
  {
    "total_faltantes": 4,
    "items": [
      {
        "producto_id": 12,
        "codigo": "CAB-0001",
        "nombre": "Cable UTP Cat 6",
        "categoria": "Redes",
        "almacen": "Almacén Principal",
        "stock_fisico": 2,
        "stock_prestado": 2,
        "stock_disponible": 0,
        "stock_minimo": 10,
        "motivo_alerta": "SIN_STOCK_DISPONIBLE",
        "consumo_estimado": "Sin historial suficiente",
        "recomendacion_compra": "Sugerido alcanzar stock mínimo (faltan 10 uds)"
      }
    ]
  }
  ```

### C. Flujo de Solicitud de Escritura de Destinatario: `POST /api/haku/mensaje/`
- Cuando el usuario dice: *"Préstale un taladro a Car"* o *"Préstale un taladro"*:
- **Response (Aclaración con solicitud de escritura):**
  ```json
  {
    "tipo": "aclaracion",
    "esperando": "destinatario",
    "requiere_escritura": true,
    "mensaje_pantalla": "No pude identificar con seguridad el nombre. Escríbelo para evitar registrar a otra persona por error.",
    "mensaje_verbal": "No pude identificar con seguridad el nombre. Por favor escríbelo.",
    "propuesta_parcial": {
      "tipo_operacion": "PRESTAMO",
      "producto_id": 5,
      "cantidad": 1
    }
  }
  ```

### D. Endpoint de Confirmación Atómica e Idempotente: `POST /api/haku/confirmar/`
- **Headers:** `Content-Type: application/json`, `X-CSRFToken: ...`
- **Payload Request:**
  ```json
  {
    "propuesta_id": "b3e5c709-1a22-441f-82ff-650a3f9119c2",
    "idempotency_key": "opcional-uuid-cliente"
  }
  ```
  - **`propuesta_id` (Obligatorio, string):** Identificador UUID de la propuesta activa generada por el servidor. El backend valida que exista una propuesta activa para el usuario y que coincida exactamente con este ID; si no coincide, devuelve HTTP 400 (`"El ID de la propuesta no coincide con la propuesta activa."`). Si no hay propuesta activa o caducó, devuelve HTTP 404 (`"No hay ninguna propuesta pendiente de confirmación."`).
  - **`idempotency_key` (Opcional, string):** Clave de idempotencia única. Si el cliente no la envía, el servidor la genera determinísticamente como `haku-{propuesta_id}-{hash}`.
  - **Seguridad anti-manipulación:** El cliente **no** puede enviar `producto_id`, `cantidad`, `almacen_id` ni `destinatario`. El servidor recupera la propuesta fidedigna almacenada en memoria de sesión / `HakuOperationalContext` y ejecuta la transacción con `select_for_update()`.
- **Response Exitosa (HTTP 200):**
  ```json
  {
    "exito": true,
    "mensaje_pantalla": "Transacción confirmada con éxito. Movimiento #42 registrado...",
    "mensaje_verbal": "Operación confirmada. Se registraron 2 unidades...",
    "movimiento_id": 42,
    "stock_nuevo": 1
  }
  ```
- **Response Idempotente Repetida (HTTP 200):**
  ```json
  {
    "exito": true,
    "mensaje_pantalla": "Operación previamente confirmada (Movimiento #42).",
    "mensaje_verbal": "Esta operación ya había sido confirmada previamente.",
    "movimiento_id": 42
  }
  ```
- **Response Cancelación: `POST /api/haku/cancelar/`**
  - Payload: `{"propuesta_id": "..."}`
  - Descarta la propuesta activa en el servidor y retorna HTTP 200 confirmando cancelación.

---

## 8. Interfaz Propia de HAKU e Integración en la Futura Web

HAKU provee dos mecanismos limpios para ser incorporado en la futura página web de AudacIA:
1. **Inclusión como Widget Modular:**
   En cualquier plantilla Django de la web general, basta añadir:
   ```html
   {% include "haku/widget_flotante.html" %}
   ```
   Esto inyecta el botón flotante y el modal con `panel_voz.html` sin alterar las variables de contexto ni colisionar con la hoja de estilos de la web.
2. **Consumo Desacoplado via API (Headless / SPA):**
   Si la web general implementa un frontend moderno (Vue/React/Vanilla JS), consume directamente los endpoints `/api/haku/mensaje/`, `/api/haku/confirmar/`, `/api/haku/faltantes-reposicion/`.

---

## 9. Caché: Reutilización, Invalidación y Consistencia

1. **Backend de Caché:** `LocMemCache` (RAM en proceso Django, alias `haku_stock`, `haku_catalogo`, `haku_ubicaciones`). No se añade Redis para no inflar dependencias de infraestructura innecesariamente.
2. **Estrategia de Expiración:**
   - Catálogo de productos y categorías: TTL 30 minutos.
   - Destinatarios canónicos y personas frecuentes: TTL 15 minutos.
   - Stock orientativo para consultas rápidas: TTL 15 segundos.
   - Consultas agregadas de reposición/faltantes: TTL 60 segundos.
3. **Invalidación Atómica Inmediata:**
   - Toda mutación de stock, préstamo, devolución o dotación invalida las claves afectadas en caché.
   - Se utiliza `transaction.on_commit(lambda: HakuCacheManager.invalidar_stock(...))` garantizando que la caché nunca se invalide si la transacción de base de datos hace rollback.
   - Aislamiento estricto por `almacen_id` y permisos de usuario.

---

## 10. Préstamos y Normalización de Nombres de Personas

1. **Distinción Préstamo vs. Dotación:**
   - `PRÉSTAMO`: Salida temporal (`Movimiento.Tipo.PRESTAMO` + creación de `Prestamo` con saldo pendiente). Exige destinatario obligatorio y fecha/hora automática.
   - `DOTACIÓN`: Salida definitiva (`Movimiento.Tipo.SALIDA` con motivo identificando dotación). Descuenta stock disponible definitivamente y no genera registro en `Prestamo`.
2. **Resolución Canónica de Nombres:**
   - Se compara contra nombres y usernames de `UsuarioAudacIA` y destinatarios históricos de `Prestamo`.
   - Normalización Unicode NFD (eliminación de tildes sólo para match, preservando caracteres canónicos para guardado), eliminación de puntuación espuria y trim de espacios.
   - La letra `ñ` se respeta escrupulosamente (no se transforma en `n`).
   - Si la coincidencia exacta normalizada tiene certidumbre >= 90%, se toma el nombre canónico oficial.
   - Si la coincidencia es dudosa, ambigua o proviene de voz con baja confianza, **se detiene la preparación del préstamo y se solicita que el usuario escriba el nombre en el campo de texto**.
   - En la tarjeta de propuesta, el usuario ve el nombre canónico y puede editarlo libremente antes de confirmar.

---

## 11. Compatibilidad, Seguridad y Riesgos de Concurrencia

- **Idempotencia:** Cada propuesta de mutación genera un UUID único (`idempotency_key`). Dobles clics o reintentos no duplican movimientos.
- **Concurrencia:** Bloqueo pesimista `select_for_update()` en transacciones atómicas de Django para `Existencia` y `Prestamo`.
- **Permisos:** Un usuario con rol `USUARIO` solo puede consultar y operar sobre sus almacenes autorizados (`acceso_todos_almacenes=False`).
- **Retrocompatibilidad:** El campo `destinatario` en `Movimiento` y `Prestamo` es un `CharField(max_length=255)`, lo que permite operar sin migraciones destructivas ni romper datos históricos.

---

## 12. Plan de Pruebas Obligatorias

1. **Pruebas Funcionales:**
   - Reposición («¿Qué hace falta por pedir?», agotados, bajo mínimo).
   - Distinción préstamo vs dotación.
   - Destinatario ausente o ambiguo: confirmación de que HAKU detiene el flujo y solicita escribirlo.
   - Normalización de nombres (tildes, `ñ`, mayúsculas, espacios, homónimos).
   - Consumibles graduales vs artículos completos.
2. **Pruebas de Caché:**
   - Verificación de hits, misses, TTL e invalidación inmediata post-commit tras préstamo y devolución.
3. **Pruebas de Seguridad y Concurrencia:**
   - Intento de operación en almacén no autorizado (debe retornar 403).
   - Idempotencia ante reenvío de propuesta.
   - Transacción simultánea sobre la misma existencia.
4. **Pruebas de Regresión:**
   - Ejecución de las suites preexistentes (`tests_fase_correccion_funcional.py`, `tests_lenguaje.py`, `tests_contexto_seguridad.py`).

---

## 13. Dependencias Externas y Coordinación con la Rama de la Web General

Para que el desarrollador de la web general ensamble HAKU cuando se integren las ramas, únicamente deberá:
1. Incluir en el menú principal un enlace al panel `/api/haku/panel/` o incrustar `{% include "haku/widget_flotante.html" %}` en su layout.
2. Asegurar que las cookies de sesión y CSRF se envíen en las peticiones asíncronas (`credentials: "same-origin"`).
3. **No se requieren migraciones adicionales de base de datos** para los modelos core ni instalación de nuevos paquetes en `requirements.txt`.

---

## 14. Actualización Después de Implementar y Auditoría Técnica Postimplementación

### Archivos efectivamente modificados y creados
- **Creados:**
  - `sistema_inventario/integraciones/haku/servicios_personas.py`: Motor determinista de resolución y normalización de nombres de personas.
  - `sistema_inventario/integraciones/haku/tests_ampliacion_haku.py`: Suite de 11 pruebas integrales para reposición, caché, personas, préstamos, dotaciones, devoluciones parciales y seguridad de propuestas.
- **Modificados:**
  - `sistema_inventario/integraciones/haku/cache.py`: Soporte de caché de faltantes/reposición con clave de aislamiento de scope (`scope_id`), invalidación atómica y hooks en `on_commit`.
  - `sistema_inventario/integraciones/haku/lenguaje/normalizador.py`: Soporte de `CONSULTAR_FALTANTES_REPOSICION`, extracción de dotaciones vs préstamos, extracción robusta de destinatarios depurando almacenes y resolución de producto en mutaciones con almacén.
  - `sistema_inventario/integraciones/haku/services.py`: Lógica de reposición con filtrado estricto multialmacén según permisos del usuario, cálculo de compras hacia el mínimo sin inventar consumo futuro, validación de personas en préstamos y blindaje de propuestas.
  - `sistema_inventario/integraciones/haku/views.py`: Endpoint `FaltantesReposicionView` utilizando `procesar_comando_inventario` y validación de `ConfirmarPropuestaView` (rechaza manipulación de ID por el cliente).
  - `sistema_inventario/integraciones/haku/urls.py`: Ruta `/api/haku/faltantes-reposicion/`.
  - `sistema_inventario/static/js/haku_voz.js`: Modal de resolución de destinatario, edición antes de confirmar y consumo de API.

### Hallazgos de la auditoría y correcciones aplicadas
1. **Contrato de Confirmación e Idempotencia:**
   - La API `POST /api/haku/confirmar/` exige obligatoriamente `propuesta_id`. El campo `idempotency_key` es opcional (si el cliente no lo provee, el servidor genera determinísticamente `haku-{propuesta_id}-{hash}`).
   - La operación es estrictamente anti-manipulación: el cliente no puede alterar producto, almacén ni cantidades; el backend extrae la propuesta de la sesión del servidor y la ejecuta con `select_for_update()`. Ante reintentos con la misma clave de idempotencia, el backend retorna éxito informando el movimiento original sin duplicar transacciones.
2. **Caché y Seguridad ante Modificación de Permisos:**
   - Para evitar que un usuario normal reciba datos cacheados tras perder acceso a un almacén o cambiar su rol, se implementó una **firma determinista de permisos** en la clave de caché (`user_{id}_alms_{firma_ids}`).
   - Adicionalmente, se conectó un receptor de señal `post_save` en `UsuarioAudacIA` que invalida de inmediato la caché de faltantes y catálogo ante cualquier modificación de cuentas o permisos.
3. **Métrica de Similitud Textual de Destinatarios (Umbral 0.70):**
   - El valor comparado con el umbral 0.70 en `HakuPersonasService` no es una probabilidad estadística, sino una puntuación de similitud léxica calculada mediante `difflib.SequenceMatcher.ratio()`.
   - Cuando el origen es voz, se exige certidumbre casi exacta (ratio >= 0.88 y un único candidato despegado) para sugerir un canónico; si hay dos candidatos con diferencia < 0.10 o si la similitud es menor, el sistema detiene el proceso y emite `requiere_escritura = True`.
4. **Préstamos, Dotaciones y Devoluciones:** Se verificó que las dotaciones no crean registros en `Prestamo`, que los préstamos registran saldo y fecha automáticamente, y que las devoluciones parciales decrementan saldo manteniendo el préstamo abierto hasta el saldo cero.

### Pruebas ejecutadas y resultados
1. `manage.py test integraciones.haku.tests_ampliacion_haku`:
   - 12 tests ejecutados, **12 OK (0 fallos, 0 errores)**.
2. `manage.py test integraciones.haku.tests_ampliacion_haku integraciones.haku.tests_fase_correccion_funcional`:
   - 49 tests ejecutados, **49 OK (0 fallos, 0 errores)**.
3. `manage.py test integraciones.haku.tests_contexto_seguridad integraciones.haku.tests_contexto`:
   - 30 tests ejecutados, **30 OK (0 fallos, 0 errores)**.
4. `manage.py test integraciones.haku.tests_lenguaje.MatrizAccionesTransaccionalesCostenasTestCase integraciones.haku.tests_lenguaje.MatrizErroresFoneticosWhisperTestCase`:
   - 40 tests ejecutados, **40 OK (0 fallos, 0 errores)**.

### Contratos reales disponibles para la web
- `POST /api/haku/mensaje/`: Motor conversacional por voz y texto (incluye aclaraciones interactivas con `requiere_escritura: true`).
- `GET /api/haku/faltantes-reposicion/?almacen_id={id}`: Resumen de alertas de inventario y compras requeridas por almacén, con validación de permisos en backend.
- `POST /api/haku/confirmar/`: Confirmación transaccional e idempotente (`payload: {"propuesta_id": "...", "idempotency_key": "opcional"}`).
- `POST /api/haku/cancelar/`: Cancelación de propuesta activa en servidor (`payload: {"propuesta_id": "..."}`).

### Estado de aceptación
- Todos los criterios de aceptación se encuentran **Implementados y probados** localmente mediante suites de pruebas automatizadas y contratos HTTP validados.

