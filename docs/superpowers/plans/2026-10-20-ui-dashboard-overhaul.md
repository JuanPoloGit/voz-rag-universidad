# UI Dashboard Overhaul — Sidebar, Dark Theme, Chart.js Fix & Datos de Prueba

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform the inventory system UI from a navbar-based layout to a professional sidebar dashboard with dark navy theme matching the reference site (`inventarioaudacia.netlify.app`), fix Chart.js static file serving, apply the "1-minute readability" golden rule to the dashboard, ensure full responsiveness, and populate the database with test data for manual QA.

**Architecture:** Single `base.html` rewrite with CSS custom properties drives the entire dark theme. Sidebar navigation replaces the top navbar. A `.env` file enables Django's `DEBUG=True` for local development, which fixes static file serving (resolving the Chart.js 404). All 30+ child templates inherit the new layout automatically via `{% extends "base.html" %}`, with targeted updates to inline styles that conflict with the dark palette. Login page updated to share the same color DNA.

**Tech Stack:** Django 6.1.2 templates, pure CSS (custom properties + flexbox + grid), Chart.js 4.4.1 local, Google Fonts served locally (Inter + Space Grotesk WOFF2), system monospace stack.

**Spec:** Reference design analysis from `inventarioaudacia.netlify.app` (detailed CSS tokens documented in subagent report). Original project spec: `gemini-code-1791922781976.md` §7-§14.

## Global Constraints

- Branch: `feature/gestion-inventario` — all commits here.
- Zero CDN external requests — all assets (fonts, Chart.js) served from `static/`.
- Do NOT touch: `integraciones/haku/**` templates or backend, `cerebro_audacia/**`, `scripts/**`.
- Login template (`cuentas/templates/cuentas/login.html`) — update for visual consistency only (user-requested override of scope boundary). No backend changes to `cuentas/`.
- All 202 existing tests must pass after every task.
- Python 3.13 / Django 6.1.2 compatibility.

## Review Focus

1. **Sidebar collapse on mobile (≤980px):** User on phone should see hamburger toggle, not a broken sidebar overlapping content. Verify with browser DevTools responsive mode.
2. **Chart.js render after theme change:** Dark backgrounds require `Chart.defaults.color` and `Chart.defaults.borderColor` overrides — charts with white labels on white backgrounds would be invisible.
3. **Inline styles in child templates:** Many templates have hardcoded `color: var(--text-muted)`, `background: white`, `background: #f1f5f9` — these will clash with dark panels. Each must be audited.
4. **Form widget rendering:** Django's default `{{ form.field }}` renders `<input>` without classes. The CSS must target bare `input`, `select`, `textarea` elements inside forms to apply dark styling.
5. **Contrast ratio:** `--text-dim` (#93a2d6) on `--bg-panel` (#0d1940) must meet WCAG AA 4.5:1 for body text. Verify with contrast checker.

---

### Task 1: Fix Static Files & Environment — Create `.env` and Download Local Fonts

**Files:**
- Create: `sistema_inventario/.env`
- Create: `sistema_inventario/static/fonts/inter-400.woff2`
- Create: `sistema_inventario/static/fonts/inter-500.woff2`
- Create: `sistema_inventario/static/fonts/inter-600.woff2`
- Create: `sistema_inventario/static/fonts/inter-700.woff2`
- Create: `sistema_inventario/static/fonts/space-grotesk-600.woff2`
- Create: `sistema_inventario/static/fonts/space-grotesk-700.woff2`

**Interfaces:**
- Consumes: Nothing.
- Produces: `DJANGO_DEBUG=True` enabling `runserver` static serving; font files at `static/fonts/` for `@font-face` in Task 2.

- [ ] **Step 1: Create `.env` file for development**

```ini
DJANGO_SECRET_KEY=dev-insecure-key-local-testing-only
DJANGO_DEBUG=True
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
SESSION_COOKIE_SECURE=False
```

- [ ] **Step 2: Download Inter font WOFF2 files locally**

Use Python `urllib` to download Inter (400, 500, 600, 700) from Google Fonts GitHub release or API. Save to `static/fonts/`.

- [ ] **Step 3: Download Space Grotesk font WOFF2 files locally**

Download Space Grotesk (600, 700) similarly. Save to `static/fonts/`.

- [ ] **Step 4: Verify the dev server serves static files**

Run: `cd sistema_inventario; ..\venv\Scripts\python.exe manage.py runserver --noreload`
Navigate to `http://127.0.0.1:8000/static/vendor/chartjs/chart.umd.min.js`
Expected: JavaScript content (not HTML 404), `Content-Type: application/javascript`.

- [ ] **Step 5: Run full test suite**

Run: `cd sistema_inventario; ..\venv\Scripts\python.exe manage.py test cuentas inventario web`
Expected: 202 tests OK, 0 failures.

---

### Task 2: Rewrite `base.html` — Sidebar Dashboard Layout with Dark Theme

**Files:**
- Modify: `sistema_inventario/templates/base.html` (complete rewrite)

**Interfaces:**
- Consumes: Font files from Task 1 at `static/fonts/`.
- Produces: CSS custom properties (`--bg-deep`, `--bg-panel`, `--accent`, `--text`, etc.); HTML structure with `.sidebar`, `.topbar`, `.main-content` regions; `{% block sidebar_active %}` for child templates to mark active nav item; `{% block content %}` unchanged.

- [ ] **Step 1: Define the complete CSS design token system**

CSS custom properties matching the reference site palette:
```css
:root {
    --bg-deep: #050b24;
    --bg-deep-2: #081234;
    --bg-panel: #0d1940;
    --bg-panel-2: #111f4d;
    --bg-elevated: #152a5c;
    --border: #1e2e63;
    --border-soft: #182552;
    --accent: #3b6ff0;
    --accent-bright: #5c8bff;
    --accent-2: #57e8cf;
    --text: #eef2ff;
    --text-dim: #93a2d6;
    --text-faint: #5f6ea3;
    --crit: #ff6b7a;
    --crit-bg: rgba(255,107,122,0.12);
    --warn: #ffbd5c;
    --warn-bg: rgba(255,189,92,0.12);
    --ok: #4ee6ab;
    --ok-bg: rgba(78,230,171,0.10);
    --radius: 14px;
    --radius-sm: 9px;
    --shadow-lg: 0 20px 50px -20px rgba(0,0,0,0.6);
}
```

- [ ] **Step 2: Build `@font-face` declarations for local fonts**

Use `{% load static %}` with `{% static 'fonts/inter-400.woff2' %}` etc. for all font files.
Define font families: `'Inter'` for body, `'Space Grotesk'` for headings/KPIs.

- [ ] **Step 3: Build the sidebar HTML structure**

Sidebar (236px fixed, sticky full height) with navigation groups:
- **Brand** — "Inventario AudacIA" with accent color
- **Principal** — Dashboard
- **Catálogo** — Almacenes, Ubicaciones, Categorías, Productos
- **Operaciones** — Entrada, Salida, Traslados, Préstamos, Conteos
- **Reportes** — Existencias, Movimientos, Préstamos, Estancados
- **Sistema** — Alertas, Usuarios (admin only)
- **Footer** — User badge + logout

Each nav item uses inline SVG icon (24×24, stroke-based). Active state uses `--accent` background.

- [ ] **Step 4: Build the topbar HTML structure**

Sticky top bar with `backdrop-filter: blur(10px)`:
- Left: Page title from `{% block page_title %}{% endblock %}`
- Right: Notification bell with alert badge + user info pill

- [ ] **Step 5: Build the main content wrapper**

```html
<div class="app-layout">
    <aside class="sidebar">...</aside>
    <div class="main-wrapper">
        <header class="topbar">...</header>
        <main class="main-content">
            {% if messages %}...{% endif %}
            {% block content %}{% endblock %}
        </main>
    </div>
</div>
```

- [ ] **Step 6: Add mobile responsive hamburger toggle**

At ≤980px: sidebar hidden, hamburger button in topbar toggles `.sidebar.open`. Mobile nav uses `<select>` dropdown as fallback. Overlay closes sidebar on tap outside.

- [ ] **Step 7: Style all base components for dark theme**

Cards, tables (`data-table`), buttons (`.btn-primary`, `.btn-secondary`, `.btn-danger`), form controls, badges, alerts — all updated to dark palette. Buttons get gradient backgrounds and hover lift effect. Tables get dark headers and `--border-soft` row separators.

- [ ] **Step 8: Verify base layout renders correctly**

Run dev server, navigate to `http://127.0.0.1:8000/`, verify sidebar appears on desktop and collapses on mobile (DevTools 375px width).

---

### Task 3: Update Login Page for Visual Consistency

**Files:**
- Modify: `sistema_inventario/cuentas/templates/cuentas/login.html`

**Interfaces:**
- Consumes: Nothing (standalone page, no `base.html` extension).
- Produces: Login page sharing same `--bg-deep`, `--accent`, `--bg-panel` color DNA.

- [ ] **Step 1: Update CSS colors to match new palette**

Replace current color values:
- `body background: #0f172a` → `#050b24` with subtle radial gradients
- `card background: #1e293b` → `#0d1940` with gradient to `#081234`
- `input background: #0f172a` → `#111f4d`
- `input border: #334155` → `#1e2e63`
- `h1 color: #38bdf8` → Use `--accent: #3b6ff0` and `--accent-2: #57e8cf`
- `button background: #0284c7` → gradient `135deg, #3b6ff0, #2f56c9` with box-shadow glow
- `button hover: #0369a1` → `translateY(-1px)` + brighter glow
- Error box: use `--crit` and `--crit-bg`

- [ ] **Step 2: Add Inter font via inline `@font-face` or `<link>` to static**

Use `{% load static %}` and reference local Inter font files.

- [ ] **Step 3: Add subtle brand styling**

Add "Inventario" in regular weight + "AudacIA" in accent-2 (teal) color to the h1. Border-radius of card to 18px matching dialog radius.

- [ ] **Step 4: Test login flow**

Navigate to `/cuentas/login/`, verify visual match with new palette. Login with `admin_general`/`AdminGeneral2026!`, verify redirect to dashboard with sidebar layout.

---

### Task 4: Redesign Dashboard — Golden Rule (≤1 Minute Readability)

**Files:**
- Modify: `sistema_inventario/web/templates/web/dashboard.html` (complete rewrite)

**Interfaces:**
- Consumes: `base.html` layout from Task 2; context variables `metricas`, `ultimos_movimientos`, `prestamos_recientes` from `web/views.py` (unchanged).
- Produces: Dashboard page with 4-column KPI cards, 2-column chart row, activity tables — all dark-themed and scannable in <60 seconds.

- [ ] **Step 1: Redesign KPI cards row**

4-column grid (2-column on mobile) with gradient background cards:
- **Stock Total** — large number in `--ok` green, icon, label
- **Artículos Registrados** — in `--accent` blue
- **Alertas Activas** — in `--crit` red (if >0) or `--text-faint` (if 0)
- **En Préstamo** — in `--warn` amber

Each card: `background: linear-gradient(160deg, var(--bg-panel), var(--bg-panel-2))`, decorative pseudo-element orb, large 32px number, 12px label, 11.5px footnote.

- [ ] **Step 2: Redesign chart section**

2-column grid (1.35fr / 1fr, single column on mobile):
- Left: Bar chart "Balance de Movimientos" — dark theme config
- Right: Doughnut chart "Distribución por Categoría" — dark theme config

Configure Chart.js defaults for dark theme:
```javascript
Chart.defaults.color = '#93a2d6';
Chart.defaults.borderColor = '#1e2e63';
```

Chart backgrounds: category colors from reference palette (`#5c8bff`, `#57e8cf`, `#ffbd5c`, `#ff8fb1`, `#c48bff`).

- [ ] **Step 3: Redesign activity tables**

2-column grid:
- Left: "Últimos Movimientos" panel with dark-styled table, type badges using `--ok`/`--crit`/`--accent` pill badges
- Right: "Préstamos Pendientes" panel with action buttons

Tables use: dark headers (`--text-faint`, uppercase, 10.5px), `--border-soft` row separators, row hover `rgba(92,139,255,0.05)`.

- [ ] **Step 4: Remove clutter — apply golden rule**

- Remove the `(§11)` spec reference from subtitle
- Remove quick-action buttons from dashboard header (they're in sidebar)
- Keep KPI cards concise: number + label + one footnote line
- Tables show max 5 rows with "Ver todos →" link
- Zero unnecessary borders or padding

- [ ] **Step 5: Add `{% block page_title %}` and `{% block sidebar_active %}`**

```django
{% block page_title %}Panel de Control{% endblock %}
{% block sidebar_active %}dashboard{% endblock %}
```

- [ ] **Step 6: Verify dashboard renders with Chart.js**

Run dev server, navigate to `/`, verify:
- KPI cards show data (even if 0)
- Charts render with dark theme (no white backgrounds, labels visible)
- Tables display correctly
- No console errors for Chart.js

---

### Task 5: Audit and Update All Child Templates for Dark Theme

**Files:**
- Modify: All 30+ templates in `inventario/templates/inventario/*.html` and `web/templates/web/reporte_*.html`
- Modify: `cuentas/templates/cuentas/usuario_lista.html`, `cuentas/templates/cuentas/usuario_form.html`, `cuentas/templates/cuentas/cambiar_password.html`

**Interfaces:**
- Consumes: `base.html` dark theme from Task 2.
- Produces: All views render correctly with dark theme. No white backgrounds, no invisible text, no broken inline styles.

- [ ] **Step 1: Audit all templates for hardcoded light-theme styles**

Search for patterns that break on dark backgrounds:
- `background: white` or `background: #fff` or `background-color: #ffffff`
- `background: #f1f5f9` or `#f8fafc` (light grays)
- `color: var(--text-muted)` → replace with `color: var(--text-dim)`
- `color: var(--text-main)` → replace with `color: var(--text)`
- `border: 1px solid var(--border)` → keep (redefined in new palette)
- `background: var(--success-bg)` → replace with `var(--ok-bg)`
- `background: var(--danger-bg)` → replace with `var(--crit-bg)`
- `background: var(--warning-bg)` → replace with `var(--warn-bg)`

- [ ] **Step 2: Update badge/pill styles across all templates**

Replace inline badge styles with consistent classes:
- Status "Activo": `background: var(--ok-bg); color: var(--ok);`
- Status "Inactivo": `background: var(--crit-bg); color: var(--crit);`
- Movement types: use accent colors from reference palette
- Stock status badges: OK/Bajo/Crítico using `--ok`/`--warn`/`--crit`

- [ ] **Step 3: Add `{% block page_title %}` and `{% block sidebar_active %}` to every template**

Each template gets the correct active sidebar item and page title. Example for `almacen_lista.html`:
```django
{% block page_title %}Almacenes{% endblock %}
{% block sidebar_active %}almacenes{% endblock %}
```

- [ ] **Step 4: Update form templates for dark inputs**

Django form widgets (`{{ form.field }}`) render bare HTML inputs. Ensure CSS in `base.html` targets:
```css
input, select, textarea {
    background: var(--bg-panel-2);
    border: 1px solid var(--border);
    color: var(--text);
    border-radius: var(--radius-sm);
    padding: 10px 12px;
}
```

- [ ] **Step 5: Update report templates for dark theme**

`reporte_existencias.html`, `reporte_movimientos.html`, `reporte_prestamos.html`, `reporte_estancados.html` — update filter bars, result tables, and empty-state messages.

- [ ] **Step 6: Visual smoke test of every major view**

Navigate to each URL path and verify no broken styling:
- `/inventario/almacenes/`, `/inventario/ubicaciones/`, `/inventario/categorias/`, `/inventario/productos/`
- `/inventario/movimientos/`, `/inventario/prestamos/`, `/inventario/conteos/`
- `/inventario/alertas/`
- `/reportes/existencias/`, `/reportes/movimientos/`, `/reportes/prestamos/`, `/reportes/estancados/`
- `/cuentas/usuarios/`

- [ ] **Step 7: Run full test suite**

Run: `cd sistema_inventario; ..\venv\Scripts\python.exe manage.py test cuentas inventario web`
Expected: 202 tests OK, 0 failures.

---

### Task 6: Populate Test Data for Manual QA

**Files:**
- Create: `sistema_inventario/inventario/management/commands/poblar_datos_prueba.py`

**Interfaces:**
- Consumes: Models `Almacen`, `Ubicacion`, `Categoria`, `Producto`, `Existencia`, `Movimiento`, `Prestamo` from `inventario/models.py`; `UsuarioAudacIA` from `cuentas/models.py`.
- Produces: Management command `python manage.py poblar_datos_prueba` that populates the database with realistic test data.

- [ ] **Step 1: Create the management command**

Data to create:
- **Categorías** (5): Herramientas Manuales, Material Eléctrico, Equipos de Protección, Papelería Técnica, Químicos y Solventes
- **Ubicaciones** (6): 3 per almacén (E01-N01, E01-N02, E02-N01 for each)
- **Productos** (15-20): Distributed across categories with realistic names, stock mínimo between 3-10, empaque info
- **Existencias iniciales**: Stock between 5-50 units per product
- **Movimientos** (10-15): Mix of entries, exits, transfers across last 30 days
- **Préstamos** (3-4): 2 active, 1 partially returned, 1 fully returned
- **Conteos** (1-2): 1 preliminary (pending confirmation), 1 confirmed

Use `get_or_create` for idempotency. Wrap in transaction.

- [ ] **Step 2: Run the command**

Run: `cd sistema_inventario; ..\venv\Scripts\python.exe manage.py poblar_datos_prueba`
Expected: Success message with counts of created records.

- [ ] **Step 3: Verify data appears in the UI**

Navigate to dashboard → verify KPI numbers populated, charts have data, activity tables show entries.
Navigate to products → verify 15+ products with stock levels.
Navigate to alerts → verify some products appear as low/critical stock.

- [ ] **Step 4: Run full test suite (regression check)**

Run: `cd sistema_inventario; ..\venv\Scripts\python.exe manage.py test cuentas inventario web`
Expected: 202 tests OK, 0 failures (test data does not interfere with test DB).

---

### Task 7: Final Verification and Commit

**Files:**
- No new files. Git operations only.

**Interfaces:**
- Consumes: All changes from Tasks 1-6.
- Produces: Single clean commit on `feature/gestion-inventario`.

- [ ] **Step 1: Run `manage.py check`**

Run: `cd sistema_inventario; ..\venv\Scripts\python.exe manage.py check`
Expected: 0 issues.

- [ ] **Step 2: Run full test suite**

Run: `cd sistema_inventario; ..\venv\Scripts\python.exe manage.py test cuentas inventario web`
Expected: 202 tests OK, 0 failures.

- [ ] **Step 3: Git status review**

Run: `git status; git diff --stat`
Review all changed files — ensure no unintended modifications to forbidden directories.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "feat(ui): Rediseño completo - Sidebar Dashboard, Dark Theme, Chart.js Fix y Datos de Prueba"
```

- [ ] **Step 5: Verify clean tree**

Run: `git status`
Expected: `nothing to commit, working tree clean`
