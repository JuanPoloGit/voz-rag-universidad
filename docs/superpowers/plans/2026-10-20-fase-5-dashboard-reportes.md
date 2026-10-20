# Fase 5: Interfaz, Dashboard y Reportes con Chart.js Local — Plan de Implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implementar el panel de control (Dashboard) operativo, el motor de informes filtrables avanzados (existencias, stock crítico, movimientos, préstamos activos y productos sin movimiento), y gráficos analíticos interactivos con Chart.js alojado localmente (100% offline) en la app `web/` de *Inventario AudacIA*.

**Architecture:** La lógica analítica y de reportes se estructura en `web/servicios/reportes.py`. Las vistas de presentación residen en `web/views.py`. Los gráficos consumen endpoints JSON locales renderizados mediante Chart.js almacenado en `static/vendor/chartjs/chart.umd.min.js` sin requerir conexión a Internet. Se respeta el ámbito de almacén del usuario (`es_usuario_normal`).

**Tech Stack:** Python 3.11+, Django 6.1.2, Chart.js (archivo estático local), HTML5 / CSS3 responsivo para móviles, tabletas y PC.

**Spec:** [gemini-code-1791922781976.md](file:///C:/Users/jchrn/Documents/Trabajos/U/voz-rag-universidad/gemini-code-1791922781976.md) (§4.3, §11, §14) y [hoja_ruta_fases_3_4_5.md](file:///C:/Users/jchrn/.gemini/antigravity-cli/brain/86c62f5f-4691-4909-aa55-1db183432c14/hoja_ruta_fases_3_4_5.md).

## Global Constraints

- **Prohibido modificar:** `sistema_inventario/integraciones/haku/**`, `sistema_inventario/cuentas/**`, `cerebro_audacia/**`, `scripts/**`.
- **Rama de trabajo:** `feature/gestion-inventario`.
- **Cero dependencias CDN / Nube:** Chart.js debe servirse 100% de manera estática y local (`static/vendor/chartjs/`).
- **Aislamiento de ámbito:** Los usuarios normales solo ven métricas, reportes y gráficos de su almacén asignado.
- **Sin venta ni cálculo monetario:** Estricto control físico (unidades), sin precios, compras ni facturación comercial.

## Review Focus

1. **Aislamiento de datos en Dashboard:** Un usuario asignado al Almacén A nunca debe ver en sus tarjetas métricas conteos de productos de otros almacenes.
2. **Chart.js local sin Internet:** La visualización de gráficos debe funcionar perfectamente en un entorno de red local cerrada sin acceso a la web.
3. **Reporte de productos sin movimiento en N días:** Cálculo matemático exacto de productos activos cuya fecha del último movimiento sea anterior a `ahora - N días` (o que nunca hayan tenido movimiento posterior a la creación).
4. **Respuesta responsiva en móviles:** Las tablas y gráficos deben ajustarse fluidamente en pantallas estrechas sin desbordar el contenedor.
5. **Rendimiento de agregaciones:** Las consultas del Dashboard y gráficos deben usar agregaciones del ORM (`annotate`, `aggregate`, `Count`, `Sum`) en lugar de bucles en Python.

---

## Estructura de Archivos

- **Estáticos:**
  - `sistema_inventario/static/vendor/chartjs/chart.umd.min.js`
- **Servicios de Reportes y Analítica:**
  - `sistema_inventario/web/servicios/__init__.py`
  - `sistema_inventario/web/servicios/reportes.py`: Servicios `obtener_metricas_dashboard`, `obtener_datos_grafico_categorias`, `obtener_datos_grafico_balance_movimientos`, `obtener_reporte_existencias`, `obtener_reporte_movimientos_periodo`, `obtener_reporte_productos_estancados`.
- **Vistas y URLs:**
  - `sistema_inventario/web/views.py`: `DashboardView`, `ReporteExistenciasView`, `ReporteMovimientosView`, `ReportePrestamosView`, `ReporteProductosEstancadosView`, `GraficoCategoriasJSONView`, `GraficoBalanceJSONView`.
  - `sistema_inventario/web/urls.py`: Rutas `/`, `/reportes/existencias/`, `/reportes/movimientos/`, `/reportes/prestamos/`, `/reportes/estancados/`, `/graficos/categorias/`, `/graficos/balance/`.
- **Plantillas HTML:**
  - `sistema_inventario/web/templates/web/dashboard.html`
  - `sistema_inventario/web/templates/web/reporte_existencias.html`
  - `sistema_inventario/web/templates/web/reporte_movimientos.html`
  - `sistema_inventario/web/templates/web/reporte_prestamos.html`
  - `sistema_inventario/web/templates/web/reporte_estancados.html`
- **Pruebas:**
  - `sistema_inventario/web/tests/test_reportes.py`: Pruebas de métricas, agregaciones y filtros por almacén.
  - `sistema_inventario/web/tests/test_dashboard_vistas.py`: Pruebas de integración de vistas y respuestas JSON.

---

### Task 1: Integración de Chart.js Local y Servicios Analíticos

**Files:**
- Create: `sistema_inventario/static/vendor/chartjs/chart.umd.min.js`
- Create: `sistema_inventario/web/servicios/__init__.py`
- Create: `sistema_inventario/web/servicios/reportes.py`
- Test: `sistema_inventario/web/tests/test_reportes.py`

**Steps:**
- [ ] Incorporar el archivo local de Chart.js en `static/vendor/chartjs/chart.umd.min.js`.
- [ ] Escribir tests en `web/tests/test_reportes.py` validando el cálculo de métricas del Dashboard (total productos, stock bajo, sin stock, préstamos abiertos) y la agregación de gráficos (categorías y balance entradas/salidas) con filtrado por rol y almacén.
- [ ] Implementar los servicios analíticos en `web/servicios/reportes.py`.
- [ ] Ejecutar `..\venv\Scripts\python.exe manage.py test web.tests.test_reportes` y verificar que pasen.

---

### Task 2: Dashboard Principal con Tarjetas Métricas, Alertas y Gráficos

**Files:**
- Modify: `sistema_inventario/web/views.py`
- Modify: `sistema_inventario/web/urls.py`
- Create: `sistema_inventario/web/templates/web/dashboard.html`
- Modify: `sistema_inventario/templates/base.html` (enlace a Dashboard como página de inicio)
- Test: `sistema_inventario/web/tests/test_dashboard_vistas.py`

**Steps:**
- [ ] Implementar `DashboardView`, `GraficoCategoriasJSONView` y `GraficoBalanceJSONView` en `web/views.py`.
- [ ] Registrar las rutas en `web/urls.py`.
- [ ] Crear la plantilla interactiva y responsiva `dashboard.html` mostrando métricas clave, estado de alertas y los lienzos `<canvas>` con Chart.js local.
- [ ] Escribir y ejecutar tests en `web/tests/test_dashboard_vistas.py`.

---

### Task 3: Informes Filtrables de Existencias, Movimientos y Préstamos

**Files:**
- Modify: `sistema_inventario/web/views.py`
- Modify: `sistema_inventario/web/urls.py`
- Create:
  - `sistema_inventario/web/templates/web/reporte_existencias.html`
  - `sistema_inventario/web/templates/web/reporte_movimientos.html`
  - `sistema_inventario/web/templates/web/reporte_prestamos.html`
  - `sistema_inventario/web/templates/web/reporte_estancados.html`
- Test: Tests de vistas de reportes en `web/tests/test_dashboard_vistas.py`.

**Steps:**
- [ ] Implementar las vistas de reportes en `web/views.py` con filtros por almacén, categoría, fechas y días de inactividad.
- [ ] Registrar las rutas correspondientes en `web/urls.py`.
- [ ] Diseñar las plantillas de reportes limpias y optimizadas para impresión/lectura en `web/templates/web/`.
- [ ] Ejecutar la suite de pruebas de reportes.

---

### Task 4: Verificación QA, Auditoría Independiente y Commit Final

**Steps:**
- [ ] Despachar subagente QA independiente para pruebas adversariales de reportes y Dashboard.
- [ ] Despachar subagente Auditor independiente para revisión de arquitectura y alcance.
- [ ] Subsanar cualquier salvedad emitida por QA y Auditoría.
- [ ] Ejecutar la suite de regresión completa (`manage.py check`, `cuentas`, `inventario`, `web`).
- [ ] Realizar el commit de la Fase 5 en `feature/gestion-inventario`.
