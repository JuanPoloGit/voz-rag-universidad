# Guía Operativa de Despliegue y Migración a Producción: PostgreSQL

**Sistema:** Sistema de Gestión de Inventario AudacIA  
**Arquitectura:** Motor Dual (Desarrollo: SQLite 3 / Producción: PostgreSQL 14+)  
**Versión de Guía:** 1.0 (Post-Auditoría de Seguridad y Resiliencia)

---

## 1. Visión General y Estrategia de Base de Datos Dual

El sistema está diseñado y probado para operar de manera híbrida:
- **Desarrollo y pruebas locales:** Opera con **SQLite** con modo WAL (`journal_mode=WAL`), cerrojos inmediatos (`synchronous=NORMAL`) y timeouts de contención de 30 segundos.
- **Producción:** Opera con **PostgreSQL**, aprovechando concurrencia a nivel de fila mediante `select_for_update()`, aislamiento transaccional estricto, índices btree compuestos y respaldo nativo binario comprimido (`pg_dump -Fc`).

La conmutación entre motores no requiere cambios en el código Python; se gestiona exclusivamente a través de las variables de entorno en el archivo `.env`.

---

## 2. Requisitos Previos en el Servidor de Producción

1. **PostgreSQL Server:** Versión 14, 15 o 16 instalada y en ejecución.
2. **Herramientas de Cliente PostgreSQL:** `pg_dump` y `pg_restore` deben estar presentes en el `PATH` del sistema para el correcto funcionamiento del subsistema de copias de seguridad.
3. **Dependencias Python:** El entorno virtual debe contar con el adaptador `psycopg2-binary` (ya especificado en `requirements.txt`).
4. **Permisos de Archivos:** La carpeta `sistema_inventario/backups/` debe tener permisos de lectura y escritura para el usuario que ejecuta el proceso web (ej. `www-data` o servicio systemd).

---

## 3. Aprovisionamiento de la Base de Datos en PostgreSQL

Conéctate como superusuario (`postgres`) a la consola `psql` y ejecuta:

```sql
-- 1. Crear el usuario del sistema con contraseña segura
CREATE USER audacia_user WITH ENCRYPTED PASSWORD 'COLOCAR_AQUI_PASSWORD_ALTA_SEGURIDAD';

-- 2. Crear la base de datos con codificación UTF-8
CREATE DATABASE audacia_inventario 
    WITH OWNER = audacia_user 
    ENCODING = 'UTF8' 
    LC_COLLATE = 'Spanish_Colombia.utf8' 
    LC_CTYPE = 'Spanish_Colombia.utf8';

-- 3. Otorgar privilegios de esquema
GRANT ALL PRIVILEGES ON DATABASE audacia_inventario TO audacia_user;
\c audacia_inventario
GRANT ALL ON SCHEMA public TO audacia_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO audacia_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO audacia_user;
```

---

## 4. Configuración de Variables de Entorno (`.env`)

En el directorio `sistema_inventario/.env`, ajusta las variables para producción:

```env
# ==============================================================================
# CONFIGURACIÓN GENERAL DE PRODUCCIÓN
# ==============================================================================
DEBUG=False
SECRET_KEY=wG5kM8jP2qR9tL4vY7nB1xZ6cE0uI3oS5aD8fH2jK4lM9nQ1  # Generada con generar_secret_key.py
ALLOWED_HOSTS=inventario.audacia.edu.co,192.168.1.100

# ==============================================================================
# MOTOR DE BASE DE DATOS (POSTGRESQL)
# ==============================================================================
DB_ENGINE=django.db.backends.postgresql
DB_NAME=audacia_inventario
DB_USER=audacia_user
DB_PASSWORD=COLOCAR_AQUI_PASSWORD_ALTA_SEGURIDAD
DB_HOST=127.0.0.1
DB_PORT=5432

# ==============================================================================
# CACHÉ MULTIPROCESO (BASE DE DATOS)
# ==============================================================================
CACHE_BACKEND=django.core.cache.backends.db.DatabaseCache

# ==============================================================================
# SEGURIDAD Y COOKIES
# ==============================================================================
SESSION_COOKIE_SECURE=True
CSRF_COOKIE_SECURE=True
CSRF_COOKIE_HTTPONLY=False
CSRF_TRUSTED_ORIGINS=https://inventario.audacia.edu.co
```

---

## 5. Runbook: Migración de Datos Paso a Paso (SQLite → PostgreSQL)

Sigue este procedimiento durante una ventana de mantenimiento planificada:

### Paso 5.1: Congelar operaciones y crear volcado de respaldo SQLite
1. Notificar a los usuarios y detener el servicio web para evitar escrituras concurrentes.
2. Generar el volcado de datos canónico desde la base de datos SQLite activa (excluyendo permisos autogenerados y sesiones volátiles):

```powershell
# En Windows PowerShell (con entorno virtual activo):
python sistema_inventario/manage.py dumpdata `
    --natural-foreign `
    --natural-primary `
    -e contenttypes `
    -e auth.permission `
    -e sessions `
    --indent 2 `
    --output sistema_inventario/backups/migracion_sqlite_a_pg.json
```

3. Generar una copia de seguridad física de la base SQLite como salvaguarda:
```powershell
python sistema_inventario/manage.py backup_inventario --force --no-input
```

### Paso 5.2: Conmutar configuración a PostgreSQL
Actualizar las variables `DB_ENGINE`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` en `.env` conforme a la sección 4.

### Paso 5.3: Crear tablas y esquemas en PostgreSQL
Ejecutar las migraciones estructuradas en orden:
```powershell
python sistema_inventario/manage.py migrate
```

Crear la tabla de caché de base de datos para entornos con múltiples workers:
```powershell
python sistema_inventario/manage.py createcachetable
```

### Paso 5.4: Cargar los datos migrados
Importar el archivo JSON generado en el Paso 5.1:
```powershell
python sistema_inventario/manage.py loaddata sistema_inventario/backups/migracion_sqlite_a_pg.json
```

### Paso 5.5: Sincronizar secuencias autonuméricas en PostgreSQL
En PostgreSQL, las claves primarias autoincrementales dependen de secuencias (`SERIAL`/`BIGSERIAL`). Tras un `loaddata`, estas secuencias deben sincronizarse con el valor máximo actual:

```powershell
# Generar y aplicar el SQL de reset de secuencias
python sistema_inventario/manage.py sqlsequencereset cuentas inventario | python sistema_inventario/manage.py dbshell
```

### Paso 5.6: Verificación rigurosa de integridad del inventario
Ejecutar el comando de auditoría estricta desarrollado en la Fase 1:
```powershell
python sistema_inventario/manage.py verificar_integridad_inventario
```

**Criterio de Aceptación:** La salida debe ser exactamente:
```
=== AUDITORIA DE INTEGRIDAD DE INVENTARIO (AUDACIA) ===
=======================================================
[OK] INTEGRIDAD COMPLETA: Todos los chequeos pasaron exitosamente sin inconsistencias.
```

---

## 6. Respaldos Periódicos en PostgreSQL

Con PostgreSQL configurado, el subsistema polimórfico `inventario.servicios.respaldo` conmuta automáticamente a `RespaldoPostgres`.

### 6.1 Generación automática mediante cron o tarea programada
Crear una tarea programada diaria (ej. medianoche) ejecutando:
```powershell
python sistema_inventario/manage.py backup_inventario --no-input
```
- **Formato generado:** Binario custom comprimido (`backup_YYYYMMDD_HHMMSS.pgdump`).
- **Integridad:** Archivo de huella digital `backup_YYYYMMDD_HHMMSS.pgdump.sha256`.
- **Seguridad:** La contraseña se transmite internamente vía `PGPASSWORD` en el subproceso, evitando exposición en listas de procesos (`ps`/Task Manager).

### 6.2 Restauración de una copia en caso de desastre
```powershell
python sistema_inventario/manage.py restaurar_inventario sistema_inventario/backups/backup_YYYYMMDD_HHMMSS.pgdump
```

---

## 7. Plan de Rollback / Vuelta Atrás hacia SQLite

Si se presentara alguna anomalía imprevista durante la ventana de mantenimiento y se decidiera regresar a SQLite:

1. Restaurar las variables de entorno en `.env`:
   ```env
   DB_ENGINE=django.db.backends.sqlite3
   DB_NAME=db.sqlite3
   ```
2. Si la base SQLite requiriera restauración desde el respaldo previo a la migración:
   ```powershell
   python sistema_inventario/manage.py restaurar_inventario sistema_inventario/backups/backup_PREVIO.sqlite3.gz --no-input
   ```
3. Reiniciar el servicio web.

---

## 8. Verificación de Despliegue de Seguridad

Antes de abrir el tráfico público al sistema, ejecuta el verificador formal de Django:

```powershell
python sistema_inventario/manage.py check --deploy
```

Garantiza que todas las directivas de seguridad (cookies seguras, HTTPS, `X-Content-Type-Options`, `X-Frame-Options`) estén en verde.
