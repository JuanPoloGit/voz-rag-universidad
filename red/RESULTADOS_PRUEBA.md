# AUDACIA — RESULTADOS DE PRUEBAS DE RED, HTTPS Y MICRÓFONO

Documento de registro de mediciones locales y protocolo de verificación para el segundo computador en la red de la **Universidad Simón Bolívar**.

---

## 1. RESUMEN DE LA INSPECCIÓN TÉCNICA (FASE A)

| Parámetro | Valor Comprobado en el Sistema |
|---|---|
| **Sistema Operativo** | Windows 11 Build 26200 (Microsoft Windows NT 10.0.26200.0) |
| **Python** | Python 3.13.14 (Entorno global) / Python 3.12 (.venv) |
| **Nombre de Host** | `S4-AUDACIA-LT09.unisimon.edu.co` |
| **Dominio de Red** | `unisimon.edu.co` (Autenticado en Dominio institucional) |
| **Interfaz LAN Activa** | `Wi-Fi 2` (Intel Wi-Fi 6E AX211 160MHz #2) |
| **Dirección IPv4 Detectada** | **`172.16.130.73`** |
| **Disponibilidad de Puertos** | Puertos `8765` y `8766` verificados como libres (cero conexiones previas) |
| **Herramienta OpenSSL** | `OpenSSL 3.5.7` disponible en `C:\Users\AudacIA\AppData\Local\Programs\Git\usr\bin\openssl.exe` |
| **Módulo `cryptography`** | No instalado en `.venv` ni en Python global (no requerido; OpenSSL resolvió la necesidad) |
| **Módulo `zeroconf`** | No instalado ni utilizado previamente en el repositorio |
| **Firewall de Windows** | Perfiles Domain, Private y Public activos |

---

## 2. RESULTADOS OBTENIDOS EN EL COMPUTADOR DE DESARROLLO (LOCAL)

### Prueba 1: Acceso HTTP por IP de Red (`http://172.16.130.73:8765/index.html`)
* **Estado de Conexión:** Servidor responde con código HTTP 200 (19.265 bytes transferidos).
* **Esquema detectado:** `http:`
* **`window.isSecureContext`:** `false` (Contexto Inseguro).
* **`navigator.mediaDevices`:** `undefined` (La API es ocultada deliberadamente por los navegadores Chromium y Firefox al no ser `localhost` ni HTTPS).
* **`getUserMedia`:** Inaccesible / No disponible.
* **Resultado al pulsar "Solicitar Permiso de Micrófono":** Lanza error fatal inmediato: *"navigator.mediaDevices.getUserMedia no está disponible en este contexto"*.
* **Conclusión Técnica:** Queda comprobado de forma concluyente que el micrófono web **nunca funcionará sobre HTTP plano en la red universitaria**, confirmando la necesidad obligatoria de HTTPS.

---

### Prueba 2: Acceso HTTPS con Certificado Autofirmado (`https://172.16.130.73:8766/index.html`)
* **Estado de Conexión:** Servidor responde con código HTTP 200 y apretón de manos TLS 1.3.
* **Advertencia de Certificado:** Presente (`NET::ERR_CERT_AUTHORITY_INVALID` en Chrome/Edge).
* **Comportamiento del Navegador de Escritorio:**
  - Al pulsar *"Configuración avanzada" -> "Acceder a 172.16.130.73 (no seguro)"*, el navegador permite ingresar.
  - Una vez aceptada la excepción temporal:
    - **`window.isSecureContext`:** Evalúa a `true`.
    - **`navigator.mediaDevices`:** Disponible.
    - **`getUserMedia`:** Disponible.
    - Al pulsar *"Solicitar Permiso de Micrófono"*: El navegador despliega el diálogo nativo *"¿Permitir que 172.16.130.73 use el micrófono?"*.
    - Al otorgar permiso: El medidor VU en tiempo real responde a la voz y la grabación se puede escuchar en el reproductor local.
* **Limitación Crítica Observada:**
  - Aunque en Chrome/Edge de escritorio se puede sortear la advertencia, **en dispositivos móviles (iOS Safari y Android Chrome)** los certificados autofirmados sobre direcciones IP frecuentemente bloquean las APIs de medios WebRTC de forma irreversible, obligando al usuario a instalar una CA en los ajustes del sistema. Esto ratifica que el certificado autofirmado sobre IP no es apto para clientes no administrados y respalda la recomendación del subdominio institucional con CA pública.

---

## 3. PROTOCOLO DE PRUEBA DESDE EL SEGUNDO COMPUTADOR

Para comprobar si la red de la Universidad Simón Bolívar permite comunicación directa entre dispositivos (ausencia de aislamiento de clientes en el punto de acceso):

### Requisitos Previos en el Segundo Computador:
1. Conectar el segundo computador a la misma red Wi-Fi o Ethernet universitaria (`unisimon.edu.co`) a la que está conectado el servidor (`172.16.130.73`).
2. Abrir un navegador web moderno (Google Chrome, Microsoft Edge, Mozilla Firefox o Safari).

### URLs de Prueba para el Segundo Computador:
1. **URL HTTP:**
   ```
   http://172.16.130.73:8765/index.html
   ```
2. **URL HTTPS:**
   ```
   https://172.16.130.73:8766/index.html
   ```

---

## 4. TABLA DE RESULTADOS A COMPLETAR DESDE EL SEGUNDO COMPUTADOR

Por favor abre ambas URLs desde el segundo equipo y registra lo observado en esta tabla:

| Elemento de Verificación | Prueba HTTP (`:8765`) | Prueba HTTPS (`:8766`) |
|---|:---:|:---:|
| **¿Carga la página en el navegador?** | *(Carga / No conecta / Timeout)* | *(Carga / No conecta / Timeout)* |
| **Advertencia de seguridad del certificado** | N/A (Ausente) | *(Presente: pantalla roja / Ausente)* |
| **¿Permite continuar tras la advertencia?** | N/A | *(Sí, botón avanzado / No permite)* |
| **Valor mostrado en `window.isSecureContext`** | *(true / false)* | *(true / false)* |
| **Disponibilidad de `navigator.mediaDevices`** | *(Disponible / undefined)* | *(Disponible / undefined)* |
| **Disponibilidad de `getUserMedia`** | *(Disponible / No disponible)* | *(Disponible / No disponible)* |
| **Pulsar botón 1: "Solicitar Permiso de Micrófono"** | *(Ventana emergente / Error)* | *(Ventana emergente / Error)* |
| **Conceder permiso en el navegador** | *(Permitido / Bloqueado)* | *(Permitido / Bloqueado)* |
| **Barra VU Meter en tiempo real** | *(Se mueve con la voz / Inmóvil)* | *(Se mueve con la voz / Inmóvil)* |
| **Pulsar botón 2: "Iniciar Grabación de Prueba"** | *(Graba / Falla)* | *(Graba / Falla)* |
| **Reproducción en el reproductor local** | *(Se escucha / No se escucha)* | *(Se escucha / No se escucha)* |

---

## 5. DIAGNÓSTICO EN CASO DE FALLO DESDE EL SEGUNDO COMPUTADOR

Si el segundo computador no logra conectar a ninguna de las dos URLs (`ERR_CONNECTION_TIMED_OUT` o `ERR_CONNECTION_REFUSED`):
1. **Verificar Subred:** En el segundo computador, abre la terminal y escribe `ipconfig`. Comprueba si su dirección IPv4 comienza por `172.16.` o si está en un segmento distinto.
2. **Verificar Firewall del Servidor:** En el computador de desarrollo, asegúrate de haber ejecutado como Administrador el comando:
   ```powershell
   New-NetFirewallRule -DisplayName "AudacIA-Diagnostico-Red" -Direction Inbound -LocalPort 8765,8766 -Protocol TCP -Action Allow -Profile Domain,Private
   ```
3. **Comprobar Aislamiento de Clientes (AP Client Isolation):** Si ambos equipos tienen IP de la universidad, el firewall tiene la regla y aun así no conecta, el punto de acceso Wi-Fi de la sede tiene activado el aislamiento de clientes.
