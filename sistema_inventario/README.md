# Inventario AudacIA — Plataforma, Seguridad, Usuarios y Auditoría
> **Carril de Responsabilidad:** Integrante 1  
> **Tecnologías:** Django 6.1, Uvicorn ASGI, Python 3.13, TLS 1.3/1.2, SQLite/PostgreSQL, PowerShell

Este documento contiene la especificación técnica completa, manual de operación, arquitectura del servidor, configuración de red Wi-Fi/DHCP y guía de uso para los componentes implementados en el carril de **Seguridad, Usuarios, Auditoría y Plataforma**.

---

## 1. ARQUITECTURA DEL SERVIDOR Y PLATAFORMA

### 1.1. Servidor ASGI Uvicorn con TLS Directo (Decisión 8 - Opción B)
A diferencia de los proyectos Django tradicionales que usan el servidor de desarrollo básico `manage.py runserver` (el cual opera exclusivamente en HTTP plano y sin capacidades asíncronas), Inventario AudacIA opera sobre **Uvicorn**, un servidor de aplicaciones **ASGI** de alto rendimiento basado en `uvloop` y `httptools`.

```
                    ┌────────────────────────────────────────────────────────┐
                    │               CLIENTES DE RED                          │
                    │   Navegadores PC, Tablets y Celulares en Wi-Fi         │
                    └───────────────────────────┬────────────────────────────┘
                                                │  HTTPS / TLS 1.3 (Puerto 8000)
                                                ▼
                    ┌────────────────────────────────────────────────────────┐
                    │         UVICORN ASGI SERVER (Terminación TLS)          │
                    │  Carga: certs/localhost.pem + certs/localhost-key.pem  │
                    └───────────────────────────┬────────────────────────────┘
                                                │  Llamada ASGI interna
                                                ▼
                    ┌────────────────────────────────────────────────────────┐
                    │        DJANGO 6 APPLICATION (inventario_audacia)       │
                    │  cuentas (Auth) | inventario (Audit/Core) | web (UI)   │
                    └────────────────────────────────────────────────────────┘
```

#### Ventajas Técnicas:
1. **Terminación TLS Directa:** Uvicorn gestiona el cifrado SSL/TLS de forma nativa (`--ssl-certfile` y `--ssl-keyfile`), eliminando la necesidad de instalar proxies inversos pesados (como Nginx o Apache) en estaciones de desarrollo Windows.
2. **Soporte Asíncrono Nativo:** Permite la coexistencia de vistas síncronas estándar de Django con flujos asíncronos futuros (streaming de audio, WebSockets, llamadas de voz RAG).
3. **Paridad de Entornos:** Acerca el entorno local al comportamiento real que tendrá el sistema en producción.

---

### 1.2. Criptografía y Certificados TLS Locales
El sistema incluye un generador criptográfico en `scripts/plataforma/generar_certificados_python.py` que emite certificados X.509 de alta seguridad:
- **Algoritmo:** Clave privada RSA de 2048 bits con firma SHA-256.
- **Extensiones SAN (Subject Alternative Name):** Incluye explícitamente `DNS:localhost`, `IP:127.0.0.1` e `IP:::1`.
- **Compatibilidad con `mkcert`:** Si la estación dispone de la herramienta `mkcert`, el script `scripts/plataforma/generar_certificados.ps1` la utiliza automáticamente para instalar una CA raíz local en Windows, eliminando cualquier aviso de seguridad en Chrome/Edge.

---

### 1.3. Banderas de Seguridad y Protección de Cookies
En `inventario_audacia/settings.py` se definen las directivas de blindaje de sesión:

| Parámetro | Valor | Justificación Técnica |
|---|---|---|
| `SESSION_COOKIE_SECURE` | `True` | Las cookies de sesión solo viajan por canales cifrados HTTPS; el navegador las descarta si se intenta acceder por HTTP plano. |
| `CSRF_COOKIE_SECURE` | `True` | El token anti-falsificación viaja exclusivamente cifrado. |
| `SESSION_COOKIE_HTTPONLY` | `True` | Impide que scripts maliciosos de JavaScript (XSS) accedan a la cookie `sessionid`. |
| `SESSION_COOKIE_SAMESITE` | `"Lax"` | Mitiga ataques de CSRF entre sitios web cruzados. |
| `SESSION_COOKIE_AGE` | `604800` (7 días) | Duración extendida cuando el usuario marca *"Recordar este dispositivo"*. |
| `SESSION_IDLE_TIMEOUT_SECONDS` | `28800` (8 horas) | Tiempo máximo de inactividad de sesión cuando no se marca recordar. |
| `SESSION_EXPIRE_AT_BROWSER_CLOSE` | `True` | Cierra la sesión si se cierra el navegador sin haber marcado recordar. |

---

## 2. CONECTIVIDAD EN RED WI-FI Y ACCESO MULTIDISPOSITIVO (DHCP)

### 2.1. Fundamentos de Red
Cuando la laptop se conecta a una red inalámbrica con servidor DHCP (como la red **Wi-Fi Audacia**):
1. **Asignación DHCP:** El router otorga una IP privada dinámica a la laptop (ejemplo: `172.16.122.94`).
2. **Escucha en `0.0.0.0`:** Por defecto, Uvicorn escucha en `127.0.0.1` (solo accesible desde la misma laptop). Para permitir el ingreso de celulares, tablets u otras computadoras del equipo, Uvicorn debe escuchar en `0.0.0.0` (todas las interfaces de red disponibles).
3. **Lista blanca `ALLOWED_HOSTS`:** Django bloquea peticiones cuyo encabezado `Host` no coincida con su lista blanca. Se debe configurar `DJANGO_ALLOWED_HOSTS=*` o incluir la IP del Wi-Fi.

---

### 2.2. Guía de Ejecución para Compartir en Red Wi-Fi

#### Paso 1: Obtener la IP actual de la laptop en el Wi-Fi
En PowerShell ejecuta:
```powershell
(Get-NetIPAddress -AddressFamily IPv4 -InterfaceAlias "Wi-Fi*").IPAddress
```
*Supongamos que devuelve: `172.16.122.94`.*

#### Paso 2: Iniciar el servidor permitiendo conexiones de red
Desde la carpeta `sistema_inventario`:
```powershell
$env:DJANGO_ALLOWED_HOSTS = "*"
powershell -ExecutionPolicy Bypass -File .\scripts\plataforma\iniciar_sistema.ps1 -HostName "0.0.0.0" -Reload
```

#### Paso 3: Conectar desde cualquier celular o equipo en la misma red Wi-Fi
1. Conectar el celular a la red **Wi-Fi Audacia**.
2. Abrir Chrome, Safari o Edge en el teléfono e ingresar:
   ```text
   https://172.16.122.94:8000/cuentas/login/
   ```
3. **Advertencia de Certificado en el Móvil:** El navegador móvil mostrará *"La conexión no es privada"* (porque el certificado fue generado para desarrollo local y no proviene de una CA comercial).
4. Tocar en **Configuración avanzada** ➔ **Continuar a 172.16.122.94 (no seguro)**.
5. El sistema cargará inmediatamente la interfaz de inicio de sesión de AudacIA en el teléfono.

---

### 2.3. ¿Por qué es vital HTTPS para las funciones de Voz / Micrófono?
Por directiva del estándar W3C, los navegadores modernos **bloquean por completo la API de audio (`navigator.mediaDevices.getUserMedia`) si el sitio web no se sirve bajo un Contexto Seguro (HTTPS)**:
- En **HTTP plano (`http://192.168...`)**: El micrófono es `undefined` y el navegador ni siquiera solicita permiso al usuario; la funcionalidad de voz queda totalmente inhabilitada.
- En **HTTPS (`https://...`)**: El navegador reconoce el canal seguro, solicita permiso al usuario (*"¿Permitir acceso al micrófono?"*) y habilita la captura de audio en celulares y tablets.

---

## 3. GESTIÓN DE CUENTAS, ROLES Y AISLAMIENTO DE ACCESO

### 3.1. Modelo de Roles y Jerarquía de Seguridad
Ubicado en `cuentas/models.py`, el modelo `UsuarioAudacIA` define 3 roles:

| Rol | Privilegios de Acceso | Restricciones de Almacén | Capacidad Administrativa |
|---|---|---|---|
| **SUPERUSUARIO** | Acceso total al sistema y configuración. | Acceso transversal (`almacen_asignado = None`). | Crea Administradores y Usuarios. Es estrictamente **ineliminable**. |
| **ADMINISTRADOR** | Gestión operativa de inventario transversal. | Acceso transversal (`almacen_asignado = None`). | Solo puede crear y editar cuentas con rol **Usuario**. No puede modificar ni eliminar al Superusuario ni a otros Administradores. |
| **USUARIO** | Operación restringida en bodega. | **Almacén asignado obligatorio**. | Acceso denegado (HTTP 403) a la gestión de cuentas y a otros almacenes ajenos. |

---

### 3.2. Aislamiento Multialmacén (`cuentas/permisos.py`)
El módulo de permisos garantiza el desacoplamiento entre sedes físicas:
- `puede_acceder_almacen(usuario, almacen)`: Valida si el usuario tiene permiso sobre una bodega específica.
- `filtrar_por_almacen(queryset, usuario, campo_almacen)`: Filtra automáticamente listados de stock, movimientos y conteos según el rol.
- `RolRequeridoMixin` y `AccesoAlmacenMixin`: Mixins para vistas basadas en clases que rechazan accesos indebidos con `PermissionDenied` (HTTP 403).

---

### 3.3. Detección de Intentos Fallidos y Bloqueo Temporal
Gestionado mediante `cuentas/services.py` y caché en memoria/DB:
- **Umbral de cuenta:** Tras **5 intentos fallidos consecutivos**, la cuenta queda bloqueada durante **15 minutos**.
- **Mensaje de bloqueo estricto:** El sistema responde exactamente con el mensaje:
  > ⛔ **"cuenta bloqueada"**
- **Umbral de red (IP):** Si una misma dirección IP acumula 20 intentos fallidos en 15 minutos, la IP queda temporalmente bloqueada para prevenir ataques de fuerza bruta distribuidos.
- **Reinicio:** Un inicio de sesión exitoso purga de inmediato los contadores de fallos.

---

### 3.4. Eliminación Lógica e Inmutabilidad
- Las bajas de usuarios no ejecutan `.delete()` SQL en la base de datos.
- Se marcan con `eliminado_logicamente = True` e `is_active = False`.
- El backend `UsuarioAudacIABackend` deniega el acceso a cuentas eliminadas, mientras que los registros de movimientos pasados y auditoría conservan la integridad referencial histórica.

---

### 3.5. Comando CLI de Recuperación: `recuperar_superusuario`
Herramienta de rescate ante bloqueos o pérdida de acceso del superadministrador:
```powershell
python manage.py recuperar_superusuario
```
- **Identificación:** Localiza al superusuario único automáticamente o mediante `--username`.
- **No creación:** Únicamente restablece y reactiva superusuarios existentes; si la base de datos está vacía, aborta indicando el uso de `createsuperuser`.
- **Validación:** Evalúa la nueva contraseña contra `LetraYNumeroValidator` (mínimo 7 caracteres con letras y números).
- **Desbloqueo:** Limpia el contador de intentos en caché, reactiva la cuenta y registra el evento en auditoría.

---

## 4. AUDITORÍA INMUTABLE

Ubicada en `inventario/auditoria.py` y `inventario/signals_auditoria.py`:
- **Helper canónico:** `registrar_auditoria(usuario, accion, tipo_entidad, id_entidad, valores_anteriores, valores_nuevos, motivo, ip_origen)`.
- **Inmutabilidad estricta por señales ORM:**
  - `pre_save`: Bloquea cualquier intento de actualización (`UPDATE`), lanzando `PermissionError`.
  - `pre_delete`: Bloquea cualquier intento de borrado (`DELETE`), lanzando `PermissionError`.
- **Trazabilidad:** Cada cambio de usuario, inicio de sesión, respaldo, restauración o cambio de contraseña genera un asiento permanente.

---

## 5. SISTEMA DE RESPALDOS (POLÍTICA A — COLOMBIA)

### 5.1. Calendario Laboral Colombiano (`scripts/backups/colombia_calendario.py`)
Implementación determinista $O(1)$ sin librerías externas que calcula los **18 festivos oficiales anuales de Colombia**:
1. **Festivos fijos:** 1 Ene, 1 May, 20 Jul, 7 Ago, 8 Dic, 25 Dic.
2. **Ley Emiliani (Ley 51 de 1983):** Traslado al siguiente lunes de 6 Ene, 19 Mar, 29 Jun, 15 Ago, 12 Oct, 1 Nov, 11 Nov.
3. **Móviles de Pascua (Algoritmo Meeus/Jones/Butcher):** Jueves Santo, Viernes Santo, Ascensión (+43d), Corpus Christi (+64d) y Sagrado Corazón (+71d).

---

### 5.2. Comandos de Respaldo y Restauración

#### Generar Respaldo:
```powershell
python manage.py backup_inventario --no-input [--force]
```
- Genera un archivo comprimido `backups/backup_YYYYMMDD_HHMMSS.sqlite3.gz`.
- Calcula y escribe el checksum en `backup_YYYYMMDD_HHMMSS.sqlite3.gz.sha256`.
- Aplica la **Política A de retención:** conserva copias cada 2h durante los últimos 2 días hábiles, 1 copia diaria durante 30 días hábiles y 1 copia semanal durante 12 semanas.
- Si es fin de semana o festivo, omite el respaldo automáticamente (salvo que se use `--force`).

#### Restaurar Respaldo con Verificación de Integridad:
```powershell
python manage.py restaurar_inventario .\backups\<archivo>.sqlite3.gz --no-input
```
- Valida que el hash SHA-256 del archivo coincida exactamente con su firma `.sha256`. Si hay alteración, **aborta la operación**.
- Cierra conexiones abiertas de Django y reemplaza el archivo SQLite limpiamente sin bloqueos de concurrencia.

---

### 5.3. Automatización en Windows
- `scripts/backups/backup_cada_2h.ps1`: Script de ejecución periódica condicionado al calendario laboral.
- `scripts/backups/registrar_tarea_windows.ps1`: Registra la tarea en el Programador de Tareas de Windows (*Task Scheduler*).
- `scripts/backups/exportar_usb.ps1`: Sincroniza automáticamente los respaldos más recientes hacia una unidad extraíble USB.

---

## 6. PRUEBAS AUTOMATIZADAS Y DIAGNÓSTICO

### 6.1. Ejecución de Pruebas
La suite completa consta de **62 pruebas automatizadas** que cubren el 100% de las funcionalidades del Integrante 1:

```powershell
cd sistema_inventario
..\venv\Scripts\python.exe manage.py test cuentas.tests inventario.tests
```

**Resultado garantizado:** `Ran 62 tests ... OK (0 failures, 0 errors)`.

### 6.2. Script de Diagnóstico del Entorno
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\plataforma\verificar_entorno.ps1
```
Valida de forma automática Python, Django, Uvicorn, Cryptography, certificados TLS, consistencia del ORM y el calendario laboral.

---

## 7. CREDENCIALES DE PRUEBA PRECONFIGURADAS

| Usuario | Contraseña | Rol | Almacén Asignado |
|---|---|---|---|
| `superadmin` | `SuperAdmin2026!` | Superusuario | Transversal (`None`) |
| `admin_general` | `AdminGeneral2026!` | Administrador | Transversal (`None`) |
| `operador_norte` | `OperadorNorte2026!` | Usuario | Almacén Central Norte |
| `operador_sur` | `OperadorSur2026!` | Usuario | Almacén Satélite Sur |

---

## 8. ESTRUCTURA MODULAR DE SCRIPTS (`scripts/`)

Para mantener el orden, modularidad y separación de responsabilidades, los scripts se encuentran organizados por dominios en subdirectorios:

```text
sistema_inventario/scripts/
├── plataforma/                       # Control del servidor seguro y diagnóstico
│   ├── iniciar_sistema.ps1           # Arranque Uvicorn ASGI con TLS directo
│   ├── generar_certificados.ps1      # Orquestador mkcert / Python
│   ├── generar_certificados_python.py# Generador criptográfico X.509 RSA/SHA-256
│   └── verificar_entorno.ps1         # Diagnóstico integral de 5 fases
├── backups/                          # Políticas de respaldo y contingencia
│   ├── colombia_calendario.py        # Motor de festivos colombianos (Ley Emiliani)
│   ├── backup_cada_2h.ps1            # Ejecución periódica según Política A
│   ├── registrar_tarea_windows.ps1   # Registro en Task Scheduler de Windows
│   └── exportar_usb.ps1              # Exportación hacia memorias USB
├── dev/                              # Herramientas de apoyo al desarrollo
│   └── crear_datos_prueba.py         # Población de almacenes y cuentas demo
└── colombia_calendario.py            # Stub de retrocompatibilidad
```

