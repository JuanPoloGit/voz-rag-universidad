# AUDACIA — PROTOTIPO AISLADO DE RED LAN, HTTPS, MICRÓFONO Y ZEROCONF

Carpeta independiente para pruebas de concepto de conectividad LAN y acceso al micrófono web en la red de la **Universidad Simón Bolívar (Unisimón)** a coste $0.

---

## 1. OBJETIVO Y LÍMITES DEL PROTOTIPO

Este directorio contiene una suite de prueba totalmente desacoplada de la aplicación principal:
- **No importa módulos de Django** ni componentes de Haku Inventario o HACU.
- **No toca bases de datos** ni dependencias de `requirements.txt`.
- **No transmite grabaciones**: el audio capturado se procesa y reproduce exclusivamente en la memoria RAM del navegador cliente.
- **Coste financiero: $0**. Emplea herramientas ya existentes en el computador servidor (Python estándar, OpenSSL integrado en Git for Windows y cmdlets nativos de PowerShell).

---

## 2. ESTRUCTURA DE LA CARPETA

```
red/
├── .gitignore               # Excluye certificados (*.pem, *.key) y registros temporales
├── index.html               # Página de diagnóstico autónoma (cero CDNs, cero fuentes externas)
├── servidor.py              # Servidor HTTP/HTTPS multihilo en Python estándar
├── generar_certificados.ps1 # Generación de certificado autofirmado con SANs usando OpenSSL local
├── iniciar_servidores.ps1   # Arranque dual (HTTP 8765 + HTTPS 8766) en primer o segundo plano
├── detener_servidores.ps1   # Cierre limpio de procesos en los puertos 8765 y 8766
├── README.md                # Este documento de instrucciones y operación
└── RESULTADOS_PRUEBA.md     # Registro de mediciones locales y plantilla para el segundo equipo
```

---

## 3. PARÁMETROS DE RED REALES DETECTADOS

Inspección realizada mediante comandos de solo lectura en el computador servidor:
- **Equipo:** `S4-AUDACIA-LT09.unisimon.edu.co`
- **Sistema Operativo:** Windows 11 Build 26200
- **Interfaz LAN Activa:** `Wi-Fi 2` (Intel Wi-Fi 6E AX211)
- **Dirección IPv4 Real:** `172.16.130.73`
- **Red:** `unisimon.edu.co` (Categoría: DomainAuthenticated)
- **Puertos de Prueba Utilizados:**
  - HTTP: `8765`
  - HTTPS: `8766`
  *(Ambos puertos verificados como 100% libres antes del enlace).*

---

## 4. INSTRUCCIONES DE USO REPRODUCIBLES

### Paso 1: Generar el Certificado de Prueba (Solo una vez)
Abre PowerShell y ejecuta:
```powershell
powershell -ExecutionPolicy Bypass -File red/generar_certificados.ps1
```
*Esto utiliza `openssl.exe` (ubicado en `C:\Users\AudacIA\AppData\Local\Programs\Git\usr\bin\openssl.exe`) para crear un certificado autofirmado válido por 30 días con los Subject Alternative Names (SAN):*
- `IP Address: 172.16.130.73`
- `IP Address: 127.0.0.1`
- `DNS: S4-AUDACIA-LT09.unisimon.edu.co`
- `DNS: localhost`
- `DNS: audacia-prueba.local`

Los archivos se guardan en `red/certificados/cert.pem` y `key.pem` (ignorados en Git).

---

### Paso 2: Iniciar los Servidores de Diagnóstico
Puedes iniciarlo en primer plano para ver los logs en vivo:
```powershell
powershell -ExecutionPolicy Bypass -File red/iniciar_servidores.ps1
```
O en segundo plano:
```powershell
powershell -ExecutionPolicy Bypass -File red/iniciar_servidores.ps1 -SegundoPlano
```
El servidor escuchará simultáneamente en:
- **HTTP:**  `http://172.16.130.73:8765/index.html`
- **HTTPS:** `https://172.16.130.73:8766/index.html`

---

### Paso 3: Detener los Servidores
Para finalizar completamente los procesos en escucha:
```powershell
powershell -ExecutionPolicy Bypass -File red/detener_servidores.ps1
```

---

## 5. CONFIGURACIÓN DEL FIREWALL DE WINDOWS (PARA ACCESO DESDE OTRO COMPUTADOR)

El Firewall de Windows bloquea por defecto conexiones entrantes hacia puertos no reconocidos.

> [!IMPORTANT]
> **Permisos de Administrador:** La configuración del firewall de Windows requiere ejecutar PowerShell como Administrador. Si la sesión no es elevada, Windows solicitará confirmación mediante una ventana emergente de Windows Defender al arrancar el servidor en Python.

### Para Administradores — Crear Regla Temporal Estricta:
Para permitir que otros computadores de la red universitaria alcancen los puertos de prueba:
```powershell
New-NetFirewallRule -DisplayName "AudacIA-Diagnostico-Red" `
    -Direction Inbound `
    -LocalPort 8765, 8766 `
    -Protocol TCP `
    -Action Allow `
    -Profile Domain, Private `
    -Description "Regla temporal para prueba de concepto de microfono y red LAN de AudacIA"
```

### Para Eliminar la Regla al Terminar las Pruebas:
```powershell
Remove-NetFirewallRule -DisplayName "AudacIA-Diagnostico-Red"
```

---

## 6. ESTADO DE ZEROCONF / mDNS

- **Inspección en Repositorio:** Se confirmó que `zeroconf` nunca ha sido instalado, utilizado ni probado en este repositorio.
- **Disponibilidad:** `import zeroconf` arroja `ModuleNotFoundError`.
- **Decisión Estricta:** En estricto cumplimiento de la regla de **no alterar dependencias ni instalar paquetes en los entornos existentes**, no se instaló la biblioteca.
- **Evaluación Técnica:** En redes universitarias de gran escala (como la de Unisimón), el tráfico de multidifusión (mDNS en `224.0.0.251:5353`) suele estar filtrado o aislado entre puntos de acceso Wi-Fi para evitar tormentas de broadcast. Por tanto, mDNS no es un mecanismo confiable para clientes no administrados en campus universitarios, independientemente de la disponibilidad de la librería.

---

## 7. EXPERIMENTO OPCIONAL: PERFIL AISLADO DE CHROME

Si se desea verificar que el bloqueo del micrófono bajo HTTP puro se debe exclusivamente a la política de seguridad del navegador y no a un fallo de hardware:

Ejecuta el siguiente comando en PowerShell (crea un perfil temporal aislado en `red/perfil_temp` sin alterar el Chrome habitual del usuario):
```powershell
$chrome = "C:\Program Files\Google\Chrome\Application\chrome.exe"
$tempDir = Join-Path (Get-Location) "red\perfil_temp"
& $chrome --user-data-dir="$tempDir" --unsafely-treat-insecure-origin-as-secure="http://172.16.130.73:8765" "http://172.16.130.73:8765/index.html"
```
Al abrir la página bajo este perfil de prueba, Chrome tratará `http://172.16.130.73:8765` como origen seguro de forma forzada, habilitando `getUserMedia()` sin HTTPS.

*Para limpiar el perfil de prueba:* simplemente borra la carpeta `red\perfil_temp`.

---

## 8. SEGURIDAD Y LIMPIEZA FINAL

Una vez concluidas todas las pruebas:
1. Ejecuta `powershell -File red/detener_servidores.ps1`.
2. Si creaste la regla de firewall, elimínala con `Remove-NetFirewallRule -DisplayName "AudacIA-Diagnostico-Red"`.
3. Para borrar los certificados de prueba generados: elimina la carpeta `red/certificados/`.
4. Ninguna información sensible, credencial institucional ni audio fue guardado ni transmitido.
