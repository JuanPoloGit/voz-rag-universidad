# GUÍA DE DESPLIEGUE LOCAL SEGURO HTTPS EN WINDOWS
## Inventario AudacIA — Plataforma, Seguridad y Alta Disponibilidad

Este documento define la arquitectura, configuración, despliegue y operación del entorno local HTTPS para el sistema **Inventario AudacIA**, implementado por el **Integrante 1** (Carril de Seguridad, Usuarios, Auditoría y Plataforma).

---

## 1. ARQUITECTURA Y JUSTIFICACIÓN TÉCNICA

Por especificación del proyecto (§10 y Decisión 8 acordada), la aplicación opera exclusivamente bajo el protocolo **HTTPS**:

- **Protección de cookies de sesión:** Las cookies de autenticación (`sessionid`) y protección anti-falsificación (`csrftoken`) operan con las banderas `SESSION_COOKIE_SECURE = True`, `CSRF_COOKIE_SECURE = True` y `SESSION_COOKIE_HTTPONLY = True`. En un canal HTTP plano, los navegadores modernos descartan o bloquean estas credenciales.
- **Cifrado en tránsito:** Toda transmisión de credenciales y datos maestros de almacenes e inventario viaja cifrada mediante TLS 1.3 / TLS 1.2, eliminando riesgos de intercepción en redes locales o corporativas.
- **Decisión 8 (Opción B):** Despliegue mediante el servidor ASGI de alto rendimiento **Uvicorn** con terminación TLS directa integrada (`--ssl-keyfile` y `--ssl-certfile`), eliminando dependencias de proxies inversos pesados en estaciones de desarrollo Windows.

---

## 2. REQUISITOS PREVIOS

1. **Sistema Operativo:** Windows 10 / Windows 11 (64-bit).
2. **Python:** 3.12 o 3.13 en el entorno virtual `venv/`.
3. **PowerShell:** 5.1 o PowerShell 7+.
4. **Dependencias instaladas:**
   - Django >= 5.0
   - Uvicorn >= 0.20
   - Cryptography >= 40.0
   - Python-dotenv

---

## 3. GESTIÓN DE CERTIFICADOS TLS

El proyecto admite dos métodos de generación de certificados locales en `sistema_inventario/certs/`:

### Método A: Mediante `mkcert` (Recomendado para cero advertencias en navegador)

`mkcert` instala una Autoridad Certificadora (CA) raíz local en el almacén de certificados de Windows, lo que permite a Chrome, Edge y Firefox reconocer el certificado local como 100% confiable (candado verde):

1. **Instalar `mkcert` (vía gestor de paquetes de Windows):**
   ```powershell
   winget install FiloSottile.mkcert
   # O alternativamente con Chocolatey:
   # choco install mkcert
   ```
2. **Generar los certificados automáticamente:**
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\sistema_inventario\scripts\plataforma\generar_certificados.ps1
   ```
   El script ejecutará `mkcert -install` y emitirá `localhost.pem` y `localhost-key.pem` con Nombres Alternativos del Sujeto (SAN): `localhost`, `127.0.0.1` y `::1`.

### Método B: Mediante generador integrado Python (Cero dependencias externas)

Si la estación no dispone de `winget` ni permisos de administrador para instalar herramientas externas, el script detecta la ausencia de `mkcert` y conmuta automáticamente al generador integrado [`generar_certificados_python.py`](file:///C:/Users/jchrn/Documents/Trabajos/U/voz-rag-universidad/sistema_inventario/scripts/plataforma/generar_certificados_python.py), emitiendo certificados RSA de 2048 bits con firma SHA-256 válidos por 365 días.

---

## 4. VERIFICACIÓN Y DIAGNÓSTICO DEL ENTORNO

Antes de iniciar el sistema, ejecute el script de diagnóstico integral:

```powershell
powershell -ExecutionPolicy Bypass -File .\sistema_inventario\scripts\plataforma\verificar_entorno.ps1
```

Este script evalúa de forma automatizada:
1. Ruta y versión del intérprete de Python en `venv/`.
2. Presencia de `django`, `uvicorn`, `cryptography` y `sqlite3`.
3. Integridad y tamaño de los archivos de certificado TLS.
4. Ejecución del chequeo de consistencia de Django (`manage.py check`).
5. Validación de los 18 festivos anuales del calendario colombiano (Ley Emiliani).

---

## 5. INICIO DEL SERVIDOR HTTPS

Para levantar el servidor ASGI seguro:

```powershell
# Inicio estándar en el puerto 8000
powershell -ExecutionPolicy Bypass -File .\sistema_inventario\scripts\plataforma\iniciar_sistema.ps1

# Inicio en modo desarrollo con recarga automática en cambios de código
powershell -ExecutionPolicy Bypass -File .\sistema_inventario\scripts\plataforma\iniciar_sistema.ps1 -Reload

# Inicio en puerto alternativo
powershell -ExecutionPolicy Bypass -File .\sistema_inventario\scripts\plataforma\iniciar_sistema.ps1 -Port 8443
```

### URLs de acceso:
- **Consola de Acceso:** [https://localhost:8000/cuentas/login/](https://localhost:8000/cuentas/login/)
- **Gestión de Usuarios:** [https://localhost:8000/cuentas/usuarios/](https://localhost:8000/cuentas/usuarios/)
- **Cierre del servidor:** Presionar `Ctrl + C` en la terminal.

---

## 6. SISTEMA DE RESPALDOS Y RECUPERACIÓN (POLÍTICA A)

El sistema incluye comandos administrativos para la protección y restauración de datos:

### Generación de respaldos:
```powershell
cd sistema_inventario
..\venv\Scripts\python.exe manage.py backup_inventario --no-input

# Forzar respaldo en fin de semana o fuera de horario laboral:
..\venv\Scripts\python.exe manage.py backup_inventario --no-input --force
```

### Restauración con verificación SHA-256:
```powershell
cd sistema_inventario
..\venv\Scripts\python.exe manage.py restaurar_inventario .\backups\backup_YYYYMMDD_HHMMSS.sqlite3.gz --no-input
```

### Programación automática cada 2 horas (Windows Task Scheduler):
```powershell
# Registrar tarea en el programador de Windows (lunes a viernes cada 2 horas)
powershell -ExecutionPolicy Bypass -File .\sistema_inventario\scripts\backups\registrar_tarea_windows.ps1

# Desinstalar tarea programada:
powershell -ExecutionPolicy Bypass -File .\sistema_inventario\scripts\backups\registrar_tarea_windows.ps1 -Desinstalar
```

### Exportación a memoria USB:
```powershell
# Detección automática de unidad USB
powershell -ExecutionPolicy Bypass -File .\sistema_inventario\scripts\backups\exportar_usb.ps1

# Destino específico
powershell -ExecutionPolicy Bypass -File .\sistema_inventario\scripts\backups\exportar_usb.ps1 -DestinoUSB E:\AudacIA_Backups
```

---

## 7. RESOLUCIÓN DE PROBLEMAS COMUNES (TROUBLESHOOTING)

| Síntoma | Causa probable | Solución |
|---|---|---|
| Advertencia *"Su conexión no es privada"* (NET::ERR_CERT_AUTHORITY_INVALID) | Se generó el certificado con el método autofirmado B en lugar de `mkcert`. | Para desarrollo, hacer clic en "Avanzado" -> "Continuar a localhost (no seguro)", o instalar `mkcert` y regenerar con `.\sistema_inventario\scripts\plataforma\generar_certificados.ps1`. |
| Error `[WinError 10048] Only one usage of each socket address` | El puerto 8000 ya está siendo utilizado por otro proceso. | Iniciar en otro puerto: `.\sistema_inventario\scripts\plataforma\iniciar_sistema.ps1 -Port 8443`. |
| La cookie de sesión se borra inmediatamente al iniciar sesión | La aplicación se abrió por HTTP (`http://`) en lugar de HTTPS (`https://`). | Abrir siempre usando el esquema `https://`. La directiva `SESSION_COOKIE_SECURE=True` exige HTTPS. |
| Respaldo omitido: *"Fecha es no laboral en Colombia"* | El comando detectó fin de semana o día festivo según la Ley Emiliani (Política A). | Es el comportamiento normal de la Política A. Si desea forzar la copia manual, agregue el parámetro `--force`. |
