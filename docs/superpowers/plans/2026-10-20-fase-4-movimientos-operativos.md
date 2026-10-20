# Fase 4: Movimientos Operativos, Préstamos, Conteo Físico y Notificaciones — Plan de Implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implementar el motor completo de movimientos de stock, préstamos con seguimiento y devoluciones parciales/bajas, traslados internos y entre almacenes, ajustes por conteo físico con confirmación administrativa, reversión compensatoria de movimientos y un **sistema integral de notificaciones y alertas de stock bajo y crítico** en *Inventario AudacIA*, garantizando no negatividad de stock, aislamiento de transacciones con bloqueo y auditoría inmutable.

**Architecture:** La lógica de mutación reside exclusivamente en `inventario/servicios/operaciones.py` y `inventario/servicios/stock.py`. El sistema de alertas reside en `inventario/servicios/alertas.py`. Las vistas de Django (`inventario/views.py`) consumen únicamente estos servicios dentro del ámbito de almacén del usuario. Un procesador de contexto (`inventario/context_processors.py`) inyecta el estado de notificaciones a la interfaz global (`base.html`). Las operaciones críticas sobre existencias utilizan `transaction.atomic()` y `select_for_update()` para prevenir condiciones de carrera y garantizar idempotencia mediante claves únicas.

**Tech Stack:** Python 3.11+, Django 6.1.2, SQLite (modo de pruebas local) / PostgreSQL (producción), Django Templates / HTML5 / CSS3 / Vanilla JS.

**Spec:** [gemini-code-1791922781976.md](file:///C:/Users/jchrn/Documents/Trabajos/U/voz-rag-universidad/gemini-code-1791922781976.md) (§6, §8.4, §8.5, §9, §10, §14), [hoja_ruta_fases_3_4_5.md](file:///C:/Users/jchrn/.gemini/antigravity-cli/brain/86c62f5f-4691-4909-aa55-1db183432c14/hoja_ruta_fases_3_4_5.md) y requerimiento de notificaciones de stock crítico.

## Global Constraints

- **Prohibido modificar:** `sistema_inventario/integraciones/haku/**`, `sistema_inventario/cuentas/**`, `cerebro_audacia/**`, `scripts/**`.
- **Rama de trabajo:** `feature/gestion-inventario`. No tocar `Haku-Inventario` ni `main`.
- **Integridad de stock:** `stock_disponible` y `stock_en_prestamo` NUNCA pueden ser negativos bajo ninguna circunstancia.
- **Inmutabilidad de movimientos:** Los registros de `Movimiento` NUNCA se eliminan (`DELETE` prohibido); cualquier corrección se realiza vía reversión (`REVERSION`) compensatoria.
- **Auditoría inmutable:** Toda mutación de stock o estado de préstamo/conteo debe registrar un evento en `Auditoria`.
- **Control de acceso por almacén:** Los usuarios normales (`es_usuario_normal`) solo pueden operar en su almacén asignado. En traslados solo pueden ver el nombre de otros almacenes destino elegibles, sin visibilidad sobre su inventario.
- **Confirmación de conteo restringida:** Solo administradores (`es_administrador`) o el superusuario pueden confirmar y aplicar ajustes de conteo físico.
- **Sistema de alertas y notificaciones:**
  - **Stock crítico (agotado):** `stock_disponible == 0`.
  - **Stock bajo:** `0 < stock_disponible < stock_minimo`.
  - Se notifican inmediatamente tras salidas o préstamos, y se exponen en la barra de navegación para el almacén autorizado.

## Review Focus

1. **Carrera de concurrencia en salida/préstamo:** Múltiples hilos intentando descontar la última unidad simultáneamente; solo una transacción debe tener éxito y la otra fallar con excepción limpia sin generar stock negativo.
2. **Idempotencia de doble clic o reintento de red:** Envío duplicado de la misma `clave_idempotencia`; la segunda petición debe retornar el movimiento existente sin duplicar el descuento de existencias.
3. **Conversión matemática de empaques:** Si un producto define 12 unidades por paquete e ingresan 3 paquetes, el stock debe sumar exactamente 36 unidades base y la observación debe documentar "Ingreso de 3 Paquete(s) equivalentes a 36 unidades base".
4. **Ciclo de vida de préstamos y bajas:** Un préstamo de 10 unidades con devolución parcial de 4 deja saldo en 6 (`DEVUELTO_PARCIAL`), una baja por daño de 2 deja saldo en 4 (`DEVUELTO_PARCIAL`) restando 2 de `stock_en_prestamo` sin sumarlo a `stock_disponible`, y una devolución final de 4 cierra el préstamo (`CERRADO`).
5. **Reversión con stock insuficiente:** Si una entrada de 10 unidades fue consumida por una salida posterior dejando stock en 2, intentar revertir la entrada debe ser rechazado con error limpio indicando que provocaría stock negativo.
6. **Notificación de alertas de stock crítico:** Al realizar una salida o préstamo que agote el stock (`stock == 0`) o lo deje por debajo del mínimo, el sistema emite una alerta operativa (`messages.warning`) e incrementa el contador de notificaciones de la barra de navegación del usuario.

---

## Estructura de Archivos

- **Servicios:**
  - `sistema_inventario/inventario/servicios/operaciones.py`: Servicios de negocio para `registrar_entrada`, `registrar_salida`, `registrar_prestamo`, `registrar_devolucion_prestamo`, `registrar_baja_prestamo`, `registrar_traslado`, `registrar_conteo_fisico`, `confirmar_conteo_fisico` y `revertir_movimiento`.
  - `sistema_inventario/inventario/servicios/alertas.py`: Servicios de detección, consulta y consolidación de alertas de stock bajo y crítico (`obtener_alertas_stock`, `verificar_estado_alerta`).
  - `sistema_inventario/inventario/servicios/__init__.py`: Exportar los nuevos servicios para consumo unificado.
- **Context Processors:**
  - `sistema_inventario/inventario/context_processors.py`: Inyector de `alertas_resumen` en plantillas web para la campana de notificaciones.
- **Formularios:**
  - `sistema_inventario/inventario/forms.py`: Agregar `EntradaForm`, `SalidaForm`, `PrestamoForm`, `DevolucionPrestamoForm`, `BajaPrestamoForm`, `TrasladoForm`, `ConteoFisicoForm` y `ReversionForm`.
- **Vistas y URLs:**
  - `sistema_inventario/inventario/views.py`: Vistas CBV para listados, formularios y confirmaciones operativas, más `AlertasStockJSONView` y `AlertasStockListaView`.
  - `sistema_inventario/inventario/urls.py`: Rutas `/movimientos/`, `/movimientos/entrada/`, `/movimientos/salida/`, `/prestamos/`, `/prestamos/<id>/devolver/`, `/prestamos/<id>/baja/`, `/traslados/crear/`, `/conteos/`, `/conteos/<id>/confirmar/`, `/movimientos/<id>/revertir/`, `/alertas/`.
- **Plantillas HTML:**
  - `sistema_inventario/templates/base.html` (campana de notificaciones con badge y dropdown de alertas).
  - `sistema_inventario/inventario/templates/inventario/alertas_lista.html`
  - `sistema_inventario/inventario/templates/inventario/movimiento_lista.html`
  - `sistema_inventario/inventario/templates/inventario/movimiento_entrada.html`
  - `sistema_inventario/inventario/templates/inventario/movimiento_salida.html`
  - `sistema_inventario/inventario/templates/inventario/movimiento_traslado.html`
  - `sistema_inventario/inventario/templates/inventario/prestamo_lista.html`
  - `sistema_inventario/inventario/templates/inventario/prestamo_form.html`
  - `sistema_inventario/inventario/templates/inventario/prestamo_devolucion.html`
  - `sistema_inventario/inventario/templates/inventario/prestamo_baja.html`
  - `sistema_inventario/inventario/templates/inventario/conteo_lista.html`
  - `sistema_inventario/inventario/templates/inventario/conteo_form.html`
  - `sistema_inventario/inventario/templates/inventario/movimiento_revertir.html`
- **Pruebas:**
  - `sistema_inventario/inventario/tests/test_alertas_notificaciones.py`: Pruebas del motor de alertas (crítico, bajo, ámbito por usuario/almacén, context processor).
  - `sistema_inventario/inventario/tests/test_entradas_salidas.py`: Pruebas de entradas (con empaque) y salidas (validación de motivo/destinatario, no negatividad y alerta inmediata).
  - `sistema_inventario/inventario/tests/test_prestamos.py`: Pruebas de préstamos, devoluciones parciales y totales, y bajas por daño.
  - `sistema_inventario/inventario/tests/test_traslados.py`: Pruebas de traslados internos y entre almacenes con aislamiento de usuario normal.
  - `sistema_inventario/inventario/tests/test_conteos_reversiones.py`: Pruebas de conteo físico (registro y confirmación por admin) y reversión de movimientos.
  - `sistema_inventario/inventario/tests/test_concurrencia_movimientos.py`: Pruebas multihilo para condiciones de carrera e idempotencia.

---

### Task 1: Motor de Alertas y Notificaciones de Stock Bajo y Crítico

**Files:**
- Create: `sistema_inventario/inventario/servicios/alertas.py`
- Create: `sistema_inventario/inventario/context_processors.py`
- Modify: `sistema_inventario/inventario_audacia/settings.py` (registrar el context processor en `TEMPLATES.OPTIONS.context_processors`)
- Test: `sistema_inventario/inventario/tests/test_alertas_notificaciones.py`

**Steps:**
- [ ] Escribir tests en `inventario/tests/test_alertas_notificaciones.py` probando:
  - `obtener_alertas_stock`: detección precisa de productos críticos (`stock == 0`), bajo stock (`0 < stock < stock_minimo`) y normales.
  - Respeto al ámbito de almacén: usuario normal solo ve alertas de su almacén asignado; admin ve todos los almacenes.
  - `verificar_estado_alerta`: clasificación tras una operación de descuento.
  - Context processor: generación del resumen para la campana de notificaciones.
- [ ] Ejecutar el test y confirmar que falla.
- [ ] Implementar `inventario/servicios/alertas.py` y `inventario/context_processors.py`.
- [ ] Registrar `inventario.context_processors.alertas_stock` en `settings.TEMPLATES[0]["OPTIONS"]["context_processors"]`.
- [ ] Ejecutar `..\venv\Scripts\python.exe manage.py test inventario.tests.test_alertas_notificaciones` y verificar que pase.

---

### Task 2: Servicios de Entradas y Salidas con Empaques, No Negatividad y Alertas

**Files:**
- Create: `sistema_inventario/inventario/servicios/operaciones.py`
- Modify: `sistema_inventario/inventario/servicios/__init__.py`
- Test: `sistema_inventario/inventario/tests/test_entradas_salidas.py`

**Steps:**
- [ ] Escribir tests en `inventario/tests/test_entradas_salidas.py` para `registrar_entrada` (unidades directas, conversión con empaque, clave de idempotencia) y `registrar_salida` (motivo/destinatario obligatorios, rechazo por stock insuficiente, idempotencia, reporte de alerta si desciende a crítico/bajo).
- [ ] Ejecutar el test y confirmar que falla.
- [ ] Implementar `registrar_entrada` y `registrar_salida` en `inventario/servicios/operaciones.py` utilizando `transaction.atomic()`, `select_for_update()` en `Existencia`, cálculo de no negatividad, creación de `Movimiento`, invalidación/guardado para señales de caché de Haku, llamada a `verificar_estado_alerta` y llamada a `registrar_auditoria`.
- [ ] Ejecutar `..\venv\Scripts\python.exe manage.py test inventario.tests.test_entradas_salidas` y verificar que todas pasen.

---

### Task 3: Servicios de Préstamos, Devoluciones Parciales y Bajas por Daño

**Files:**
- Modify: `sistema_inventario/inventario/servicios/operaciones.py`
- Modify: `sistema_inventario/inventario/servicios/__init__.py`
- Test: `sistema_inventario/inventario/tests/test_prestamos.py`

**Steps:**
- [ ] Escribir tests en `inventario/tests/test_prestamos.py` cubriendo:
  - `registrar_prestamo`: traslada stock de `stock_disponible` a `stock_en_prestamo`, crea `Prestamo(estado=ABIERTO)`.
  - `registrar_devolucion_prestamo`: devolución parcial (reduce saldo, aumenta `stock_disponible`, reduce `stock_en_prestamo`, estado `DEVUELTO_PARCIAL`) y devolución total (estado `CERRADO`).
  - `registrar_baja_prestamo`: baja por daño (reduce saldo, reduce `stock_en_prestamo`, NO aumenta `stock_disponible`, estado `DEVUELTO_PARCIAL` o `CERRADO`).
- [ ] Ejecutar el test y verificar que falla.
- [ ] Implementar `registrar_prestamo`, `registrar_devolucion_prestamo` y `registrar_baja_prestamo` en `inventario/servicios/operaciones.py` con transacciones atómicas y auditoría.
- [ ] Ejecutar `..\venv\Scripts\python.exe manage.py test inventario.tests.test_prestamos` y verificar que todas pasen.

---

### Task 4: Servicios de Traslados Operativos (Internos y Entre Almacenes)

**Files:**
- Modify: `sistema_inventario/inventario/servicios/operaciones.py`
- Modify: `sistema_inventario/inventario/servicios/__init__.py`
- Test: `sistema_inventario/inventario/tests/test_traslados.py`

**Steps:**
- [ ] Escribir tests en `inventario/tests/test_traslados.py` cubriendo:
  - `registrar_traslado_interno`: actualización de asignación de ubicaciones de un producto en el mismo almacén.
  - `registrar_traslado_entre_almacenes`: descuento atómico en origen, aumento en destino (creación de `Existencia` si no existía), validación de permisos de usuario normal (solo origen en su almacén).
- [ ] Ejecutar el test y verificar que falla.
- [ ] Implementar `registrar_traslado_interno` y `registrar_traslado_entre_almacenes` en `inventario/servicios/operaciones.py`.
- [ ] Ejecutar `..\venv\Scripts\python.exe manage.py test inventario.tests.test_traslados` y verificar que pasen.

---

### Task 5: Servicios de Conteo Físico y Reversión de Movimientos

**Files:**
- Modify: `sistema_inventario/inventario/servicios/operaciones.py`
- Modify: `sistema_inventario/inventario/servicios/__init__.py`
- Test: `sistema_inventario/inventario/tests/test_conteos_reversiones.py`

**Steps:**
- [ ] Escribir tests en `inventario/tests/test_conteos_reversiones.py` para:
  - `registrar_conteo_fisico`: registro preliminar de conteo, cálculo automático de diferencia, no altera stock.
  - `confirmar_conteo_fisico`: rechazo a usuarios normales (solo administradores), ajuste atómico de `Existencia.stock_disponible`, creación de `Movimiento(tipo=AJUSTE_CONTEO)`.
  - `revertir_movimiento`: reversión de entradas, salidas y traslados; validación de no negatividad de stock (rechazo si provocaría stock < 0); marcado de `movimiento_original.estado = REVERTIDO`.
- [ ] Ejecutar el test y verificar que falla.
- [ ] Implementar `registrar_conteo_fisico`, `confirmar_conteo_fisico` y `revertir_movimiento` en `inventario/servicios/operaciones.py`.
- [ ] Ejecutar `..\venv\Scripts\python.exe manage.py test inventario.tests.test_conteos_reversiones` y verificar que pasen.

---

### Task 6: Formularios, Vistas Web, Notificaciones en Interfaz y Plantillas HTML

**Files:**
- Modify: `sistema_inventario/templates/base.html` (campana de notificaciones con badge interactivo)
- Modify: `sistema_inventario/inventario/forms.py`
- Modify: `sistema_inventario/inventario/views.py`
- Modify: `sistema_inventario/inventario/urls.py`
- Create:
  - `sistema_inventario/inventario/templates/inventario/alertas_lista.html`
  - `sistema_inventario/inventario/templates/inventario/movimiento_lista.html`
  - `sistema_inventario/inventario/templates/inventario/movimiento_entrada.html`
  - `sistema_inventario/inventario/templates/inventario/movimiento_salida.html`
  - `sistema_inventario/inventario/templates/inventario/movimiento_traslado.html`
  - `sistema_inventario/inventario/templates/inventario/prestamo_lista.html`
  - `sistema_inventario/inventario/templates/inventario/prestamo_form.html`
  - `sistema_inventario/inventario/templates/inventario/prestamo_devolucion.html`
  - `sistema_inventario/inventario/templates/inventario/prestamo_baja.html`
  - `sistema_inventario/inventario/templates/inventario/conteo_lista.html`
  - `sistema_inventario/inventario/templates/inventario/conteo_form.html`
  - `sistema_inventario/inventario/templates/inventario/movimiento_revertir.html`
- Test: Tests de vistas y notificaciones en interfaz.

**Steps:**
- [ ] Actualizar `base.html` para incorporar el centro de notificaciones (icono de campana con badge de conteo y modal/dropdown con el detalle de productos críticos y con bajo stock).
- [ ] Implementar `AlertasStockListaView` y `AlertasStockJSONView` en `inventario/views.py`.
- [ ] Implementar formularios en `inventario/forms.py` con validaciones de campos requeridos y filtrado de almacenes/ubicaciones según el usuario autenticado.
- [ ] Implementar vistas CBV operativas en `inventario/views.py` emitiendo `messages.warning` si la operación deja el stock en bajo o crítico.
- [ ] Registrar las rutas correspondientes en `inventario/urls.py`.
- [ ] Crear las plantillas HTML responsivas y limpias en `inventario/templates/inventario/`.
- [ ] Ejecutar pruebas de vistas y verificar respuestas HTTP y mensajes de alerta.

---

### Task 7: Pruebas de Concurrencia e Idempotencia Multihilo

**Files:**
- Create: `sistema_inventario/inventario/tests/test_concurrencia_movimientos.py`

**Steps:**
- [ ] Implementar `TestConcurrenciaMovimientos` con `ThreadPoolExecutor`:
  - Prueba de carrera: 10 hilos intentando retirar 1 unidad simultáneamente cuando solo hay 1 unidad en stock; verificar que exactamente 1 tiene éxito y 9 son rechazados con excepción, terminando con `stock_disponible == 0` (nunca negativo).
  - Prueba de doble clic / idempotencia: 5 hilos enviando la misma `clave_idempotencia` al mismo tiempo; verificar que solo se descuenta 1 vez.
- [ ] Ejecutar `..\venv\Scripts\python.exe manage.py test inventario.tests.test_concurrencia_movimientos` y verificar que pase sin deadlocks ni fallos.

---

### Task 8: Verificación QA, Auditoría Independiente y Commit

**Steps:**
- [ ] Despachar subagente QA independiente para pruebas adversariales y suite de regresión completa.
- [ ] Despachar subagente Auditor independiente para revisión de seguridad, restricciones de rol y cobertura de especificación.
- [ ] Subsanar cualquier observación emitida por QA y Auditoría.
- [ ] Ejecutar la suite de regresión completa (`manage.py check`, `cuentas`, `inventario`).
- [ ] Realizar el commit de la Fase 4 en `feature/gestion-inventario`.
