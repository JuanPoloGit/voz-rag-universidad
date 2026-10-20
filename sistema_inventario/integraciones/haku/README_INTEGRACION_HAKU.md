# Guía de Integración Técnica: HAKU Inventario y Componentes Gráficos

Este documento detalla la arquitectura de integración de **HAKU Inventario**, la interacción entre el backend y el frontend, los endpoints expuestos, los contratos de datos y las consideraciones clave para el desarrollador que implementará o extenderá el entorno gráfico del sistema (vistas web tradicionales y componentes conversacionales).

---

## 1. Arquitectura General y Separación de Responsabilidades

HAKU Inventario opera como un **asistente multimodal integrado** (voz, texto y formularios dinámicos) sobre el núcleo de Django:

```
┌────────────────────────────────────────────────────────────────────────┐
│                        CAPA CLIENTE / FRONTEND                         │
│                                                                        │
│  [ Panel Web HAKU ]       [ Widget Flotante ]      [ Vistas Tradicionales]
│   /api/haku/panel/         (widget_flotante)        /cuentas/ y /inventario/
│           │                       │                             │
│           └───────────────────────┼─────────────────────────────┘
│                                   ▼
│                    static/js/haku_voz.js (Push-to-Talk,
│                      AudioContext, VAD, Formularios)
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ HTTP / JSON / Audio Blob (HTTPS)
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                        CAPA SERVIDOR / BACKEND                         │
│                                                                        │
│  Django Request / Session ──► HakuSessionMiddleware ──► HakuApiBaseView │
│                                                                        │
│  [ Audio Pipeline ]        [ Lenguaje & Contexto ]    [ Servicios HAKU ]
│   • Decodificador FFmpeg    • Normalizador fonético    • Pre-confirmación
│   • Faster-Whisper (STT)    • Comprensión costeña      • Stock / Alertas
│   • Piper TTS (WAV síntesis)• Memoria en RAM (2 horas) • Mutaciones atómicas
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                      CAPA DE DATOS Y SEGURIDAD                         │
│                                                                        │
│  • Cuentas: UsuarioAudacIA (Roles: SUPERUSUARIO, ADMIN, USUARIO)       │
│  • Inventario: Producto, Existencia, Movimiento, Prestamo              │
│  • Auditoría Inmutable: Auditoria.objects.create(..., via_haku=True)   │
│  • Señales de Integridad: signals.py y signals_auditoria.py            │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Componentes de Interfaz Gráfica Implementados

Actualmente HAKU cuenta con dos interfaces visuales principales y un subsistema de formularios interactivos renderizados en tiempo de ejecución:

### A. Panel Completo de Voz y Chat (`/api/haku/panel/`)
- **Template:** `sistema_inventario/integraciones/haku/templates/haku/panel_voz.html`
- **Características:**
  - Diseño Dark Mode con tokens CSS (`--bg-main: #0B0F19`, `--primary: #38BDF8`, `--accent: #818CF8`).
  - Cabecera con telemetría en tiempo real: rol del operador, almacén asignado y estado del servicio.
  - Vúmetro de audio dinámico (`AudioContext` + `AnalyserNode`) y botón interactivo Push-to-Talk (o manteniendo pulsada la **Barra Espaciadora**).
  - Feed conversacional con burbujas de diálogo diferenciadas (usuario vs. HAKU).
  - Tarjeta de propuestas confirmables y contenedor para formularios dinámicos: `#haku-propuesta-card`.
  - Caja de entrada manual de texto (`#haku-input-texto`) para operadores sin micrófono.

### B. Widget Flotante Reutilizable (`widget_flotante.html`)
- **Template:** `sistema_inventario/integraciones/haku/templates/haku/widget_flotante.html`
- **Propósito:** Puede incluirse mediante `{% include "haku/widget_flotante.html" %}` en cualquier vista tradicional (`/cuentas/`, `/inventario/`) para abrir el panel conversacional en una pestaña o modal superpuesto.

### C. Formularios Interactivos Integrados en el Chat (`haku_voz.js`)
Para evitar ambigüedades de voz en la captura de datos críticos, HAKU no infiere contraseñas ni códigos complejos por audio. En su lugar, el backend responde con un tipo de evento de formulario y el frontend monta una tarjeta interactiva en el DOM:

1. **Formulario de Registro de Nuevo Producto (`formulario_crear_producto`):**
   - Campos: Nombre, categoría (select dinámico), almacén de destino, stock inicial, stock mínimo, código de barras, empaque y unidades por empaque.
   - Endpoint de persistencia: `POST /api/haku/producto/crear/`.
2. **Formulario de Edición de Producto (`formulario_editar_producto`):**
   - Campos editables: Nombre, categoría, stock mínimo, código de barras, empaque, unidades por empaque y descripción.
   - Endpoint de persistencia: `POST /api/haku/producto/editar/`.
3. **Formulario de Creación de Usuario (`formulario_crear_usuario`):**
   - Campos: Username, nombre completo, rol (filtrado dinámicamente según facultades del actor), almacén asignado (obligatorio si el rol es `USUARIO`) y contraseña inicial.
   - Endpoint de persistencia: `POST /api/haku/usuario/crear/`.
4. **Formulario de Edición de Usuario (`formulario_editar_usuario`):**
   - Campos: Nombre completo, rol permitido, almacén asignado, estado activo/inactivo y nueva contraseña opcional.
   - Endpoint de persistencia: `POST /api/haku/usuario/editar/`.

---

## 3. Catálogo de Endpoints de HAKU (API Backend)

Todas las vistas heredan de `HakuApiBaseView` (`LoginRequiredMixin`): exigen sesión autenticada de Django y token CSRF.

| Método | Endpoint | Propósito | Roles Permitidos |
|---|---|---|---|
| `POST` | `/api/haku/mensaje/` | Envío de comando conversacional por texto o audio transcrito. | Todos los autenticados |
| `POST` | `/api/haku/confirmar/` | Confirmación explícita de propuesta pendiente (con idempotency key). | Todos (según su almacén) |
| `POST` | `/api/haku/cancelar/` | Cancelación explícita de propuesta en curso. | Todos los autenticados |
| `GET` | `/api/haku/consultar-stock/` | Búsqueda directa de existencias por nombre o código (query `?q=...`). | Todos (restringido a su almacén si es `USUARIO`) |
| `GET` | `/api/haku/alertas/` | Alertas de stock bajo o productos agotados. | Administrador / Superusuario |
| `GET` | `/api/haku/prestamos-activos/` | Consulta de préstamos pendientes de devolución. | Administrador / Superusuario |
| `POST` | `/api/haku/voz/transcribir/` | Pipeline STT: decodificación FFmpeg + VAD + Faster-Whisper. Retorna JSON con texto transcrito. | Todos los autenticados |
| `POST` | `/api/haku/voz/sintetizar/` | Pipeline TTS: Piper TTS local. Retorna audio en bytes `audio/wav`. | Todos los autenticados |
| `POST` | `/api/haku/producto/crear/` | Creación segura de producto + auditoría. | Administrador / Superusuario |
| `POST` | `/api/haku/producto/editar/` | Edición de producto + auditoría. | Administrador / Superusuario |
| `POST` | `/api/haku/usuario/crear/` | Creación de usuario con autorización estricta. | Administrador (solo `USUARIO`) / Superusuario |
| `POST` | `/api/haku/usuario/editar/` | Edición de cuenta, cambio de rol o estado. | Administrador / Superusuario |
| `GET` | `/api/haku/estado/` | Estado de la sesión conversacional y propuesta activa en RAM. | Todos los autenticados |
| `GET` | `/api/haku/panel/` | Renderiza la interfaz HTML del panel interactivo. | Todos los autenticados |

---

## 4. Aspectos Clave a Tener en Cuenta para el Desarrollador de Frontend/Backend

### 1. Convivencia con las Vistas Tradicionales de `cuentas` e `inventario`
- El compañero que desarrolle las vistas web tradicionales (`/cuentas/login/`, `/cuentas/usuarios/`, `/inventario/productos/`) no debe temer colisiones con HAKU.
- Las vistas tradicionales usan plantillas HTML estándar de Django (`render(request, ...)`), mientras que HAKU consume endpoints JSON (`JsonResponse`).
- Ambos comparten los mismos modelos: `cuentas.UsuarioAudacIA`, `inventario.Producto`, `inventario.Existencia` y `inventario.Auditoria`.

### 2. Autenticación y CSRF en Peticiones Asíncronas
- En `settings.py`, `CSRF_COOKIE_HTTPONLY` está configurado en `False` intencionalmente para permitir que el frontend JavaScript lea la cookie `csrftoken` y la inyecte en el header `X-CSRFToken` de las llamadas `fetch()`.
- En `static/js/haku_voz.js`, se incluye la función helper `apiFetch(url, options)` que gestiona automáticamente el token CSRF y credenciales de sesión (`credentials: "same-origin"`).

### 3. Jerarquía Innegociable de Permisos y Señales de Base de Datos
- **Rol `USUARIO`:** Tiene prohibido acceder a creación/edición de productos y de usuarios (recibe `HTTP 403 Forbidden`). En consultas y movimientos, su alcance está confinado estrictamente a su `almacen_asignado`.
- **Rol `ADMINISTRADOR`:** Tiene acceso a todos los almacenes. Puede crear y editar productos y usuarios normales. **Bajo ninguna circunstancia puede crear superusuarios, editar superusuarios ni auto-promoverse**.
- **Rol `SUPERUSUARIO`:** Solo puede existir **un único superusuario activo** en el sistema. La señal `cuentas/signals.py` lanzará un `ValidationError` en base de datos si se intenta insertar otro superusuario o borrarlo físicamente.
- **Inactivación:** Si una cuenta tiene `is_active=False` o `eliminado_logicamente=True`, `HakuSessionMiddleware` revoca la sesión inmediatamente retornando `HTTP 401/403`.

### 4. Auditoría Inmutable
- Toda operación que muta datos (creación de producto, edición, movimiento, préstamo o usuario) debe registrarse en el modelo `inventario.Auditoria`.
- Las señales `inventario/signals_auditoria.py` impiden cualquier modificación (`update`) o borrado (`delete`) sobre registros de auditoría.
- **Nunca almacenar contraseñas ni secretos** en los diccionarios `valores_anteriores` ni `valores_nuevos`.

### 5. Memoria Operacional en RAM vs. Base de Datos
- HAKU maneja la memoria conversacional (último producto en foco, listas de candidatos de búsqueda y propuestas) mediante `LocMemCache` nativo de Django (`haku_contexto`).
- No se requiere Redis ni Memcached externo. La expiración es de 2 horas para el diálogo y 30 minutos para confirmar propuestas de stock.

### 6. Despliegue HTTPS Obligatorio para Audio
- La API del navegador `navigator.mediaDevices.getUserMedia` (micrófono) exige **origen seguro (HTTPS)** salvo en `localhost`.
- Para pruebas locales o red LAN, utilizar el script `iniciar_uvicorn_https.ps1` que levanta Uvicorn con los certificados generados en `certs/`.
