# ADR-002: TOPOLOGÍA DE RED Y ACCESO HTTPS EN LA RED DE LA UNIVERSIDAD SIMÓN BOLÍVAR (PRESUPUESTO $0)

- **Estado:** Propuesto — Revisión Institucional Definitiva
- **Fecha:** 2026-10-15
- **Ámbito:** Servidor central Windows de AudacIA, red del campus de la Universidad Simón Bolívar (Unisimón), clientes móviles/escritorio no administrados, interfaces web de voz.
- **Autor:** Antigravity Architect Agent

---

## 1. CONTEXTO INSTITUCIONAL Y RESTRICCIONES DETERMINANTES

La arquitectura de red de AudacIA se diseña exclusivamente para operar dentro de la infraestructura de red existente de la **Universidad Simón Bolívar (Unisimón)**. El servidor central es un computador administrado bajo **Windows**.

### 1.1. Requisitos Innegociables Confirmados
1. **Coste Adicional: $0 Estricto:** No se permite la compra de equipos de red (prohibición total de micro-routers, puntos de acceso dedicados o hardware auxiliar), ni la contratación de VPS, servicios de nube, túneles comerciales o certificados de pago.
2. **Cero Acceso Administrativo a la Infraestructura de Campus:** No se tiene acceso a routers perimetrales, servidores DHCP, servidores DNS institucionales, switches de distribución ni puntos de acceso Wi-Fi de la universidad.
3. **Administración Exclusiva de Windows en el Servidor:** Solo se tiene control sobre la configuración del sistema operativo del computador servidor.
4. **Acceso Exclusivo desde la Red Universitaria:** Los usuarios acceden desde dispositivos heterogéneos y no administrados (smartphones, tabletas, portátiles de operarios, docentes o visitantes) conectados a la red del campus por Wi-Fi o Ethernet.
5. **Cero Exposición a Internet Externo:** El sistema de inventario y los servicios internos **jamás deben ser accesibles desde Internet**. Queda estrictamente prohibido el uso de *port forwarding*, zonas DMZ, túneles públicos (ej. Cloudflare Tunnels, ngrok) o publicación perimetral.
6. **Autonomía Operativa Local:** Los servicios esenciales deben ejecutarse íntegramente en el servidor local.
7. **Requisito de Micrófono Web (`getUserMedia`) a $0:** Haku Inventario requiere captura de voz en el navegador. La especificación W3C y los navegadores modernos (Chromium, Safari iOS, Firefox) bloquean el micrófono si no se ejecuta bajo un **Contexto Seguro** (`window.isSecureContext === true`), exigiendo HTTPS con certificado válido sin requerir la instalación manual de certificados raíz privados en los dispositivos de los usuarios.

---

## 2. DISTINCIÓN EXPRESA DE LOS CINCO ASPECTOS DE RED

Para disipar cualquier ambigüedad técnica, se delimitan formalmente los cinco aspectos exigidos:

```
┌────────────────────────────────────────────────────────────────────────┐
│                   DELIMITACIÓN DE FRONTERAS DE RED                    │
├────────────────────────────────────────────────────────────────────────┤
│ 1. Acceso desde la Red Universitaria: Conectividad privada en campus.  │
│ 2. Cero Acceso desde Internet: Sin puertos públicos ni túneles.        │
│ 3. HTTPS y Micrófono: Contexto seguro confiado nativamente por SO.     │
│ 4. Operación sin Internet: Continuidad local de servicios y DNS.       │
│ 5. Limitaciones de Infraestructura: Restricciones de red no gestionada.│
└────────────────────────────────────────────────────────────────────────┘
```

### 2.1. Acceso a los Servicios desde la Red Universitaria
* El servidor reside en una dirección IP privada perteneciente al direccionamiento interno del campus (rangos RFC 1918: `10.x.x.x`, `172.16-31.x.x` o `192.168.x.x`).
* Los clientes se conectan a través de las redes inalámbricas del campus (ej. Wi-Fi institucional o Eduroam) o cableado Ethernet de laboratorios/oficinas.
* La comunicación entre el navegador del cliente y el servidor ocurre enteramente dentro del tráfico interno de la universidad.

### 2.2. Ausencia Total de Acceso Directo desde Internet Externo
* El firewall perimetral de la Universidad Simón Bolívar bloquea de forma predeterminada cualquier intento de conexión entrante desde el exterior hacia IPs privadas del campus.
* AudacIA no implementa técnicas de evasión perimetral, *hole punching* ni túneles reversos hacia la nube.
* Si un usuario intenta ingresar desde fuera del campus (desde su hogar o mediante datos móviles sin VPN institucional), la conexión es inalcanzable e invisible.

### 2.3. Funcionamiento de la Interfaz Web con HTTPS y Micrófono
* El estándar W3C estipula que `navigator.mediaDevices.getUserMedia()` solo está disponible en contextos seguros:
  - `http://IP_PRIVADA`: El navegador oculta la API de medios; el micrófono web **no funciona**.
  - `https://IP_PRIVADA` con certificado autofirmado: Muestra pantallas rojas de bloqueo; en Safari iOS y Android Chrome la captura de audio queda bloqueada o degradada.
  - `https://dominio_valido` con certificado de CA pública reconocida: El navegador otorga el contexto seguro de inmediato, permitiendo solicitar permiso de micrófono con un solo clic.

### 2.4. Funcionamiento Durante una Interrupción de Internet Externo
* Los procesos de negocio (Django, inventario, Faster-Whisper, Piper, SQLite, PostgreSQL) residen al 100% en el servidor y continúan operando sin Internet.
* El apretón de manos TLS (TLS Handshake) ocurre localmente entre el cliente y el proxy en el servidor, utilizando los certificados previamente almacenados en disco.
* **El factor condicionante es la resolución de nombres (DNS):**
  - Si el nombre del servidor es resuelto por un DNS interno de la universidad o reside en la caché del cliente, el sistema opera con total normalidad sin Internet.
  - Si depende de DNS público externo y la caché del cliente expira o ingresa un cliente nuevo, la resolución fallará durante el corte de Internet.

### 2.5. Limitaciones Derivadas de No Poder Administrar la Red Institucional
* **Aislamiento de Clientes en Wi-Fi (*Client Isolation*):** En redes Wi-Fi de invitados abiertas, las universidades suelen aislar los clientes inalámbricos entre sí y bloquear el tráfico hacia servidores internos. Si esto ocurre, los clientes deben conectarse a la Wi-Fi institucional/docente o Ethernet.
* **Asignación de IP del Servidor:** Al no poder crear reservas DHCP en el router de campus, se debe solicitar a TI una IP estática o identificar una IP libre fuera del pool DHCP para evitar colisiones.
* **Protección contra DNS Rebinding:** Los cortafuegos institucionales pueden descartar respuestas de servidores DNS externos que apunten a IPs privadas de la universidad.

---

## 3. EVALUACIÓN DE ALTERNATIVAS A COSTE $0

Se evalúan tres alternativas viables sin incurrir en ningún gasto financiero:

```
┌────────────────────────────────────────────────────────────────────────┐
│                     ALTERNATIVAS A PRESUPUESTO $0                      │
├─────────────────────────┬──────────────────────────────────────────────┤
│ Opción 1 (Institucional)│ Subdominio unisimon.edu.co + DNS/Cert TI     │
│ Opción 2 (Autónoma $0)  │ Subdominio público gratuito (DuckDNS) + ACME │
│ Opción 3 (Degradada $0) │ IP Privada Directa + Certificado Autofirmado │
└─────────────────────────┴──────────────────────────────────────────────┘
```

---

### Opción 1: Subdominio Institucional con DNS y Certificado de Unisimón (Recomendación Principal)
* **Mecanismo:**
  Aprovechar la infraestructura existente de la Universidad Simón Bolívar (`unisimon.edu.co`). El equipo de AudacIA tramita ante la Dirección de Tecnologías de la Información (TI / Redes) de Unisimón:
  1. La asignación de un subdominio interno: ej. `audacia.unisimon.edu.co` (o `inventario-audacia.unisimon.edu.co`).
  2. Un registro `A` en los servidores DNS internos del campus (los que el DHCP universitario entrega a todos los usuarios en Wi-Fi y Ethernet) apuntando a la IP privada fija del servidor Windows.
  3. La provisión de un certificado TLS emitido por la entidad certificadora institucional (Unisimón cuenta con acuerdos de certificados educativos de confianza pública, ej. Sectigo/DigiCert vía RENATA/RedCLARA, o certificado Wildcard `*.unisimon.edu.co` para servicios universitarios).
* **Coste:** **$0**.
* **Seguridad y Acceso Externo:**
  - Cero exposición: El registro DNS interno solo resuelve dentro del campus. No hay puertos abiertos hacia Internet.
  - Cumple al 100% el requisito de contexto seguro para el micrófono web en cualquier teléfono, tableta o PC sin instalar nada.
* **Comportamiento ante Caída de Internet:**
  - **100% Inmune:** El DNS que resuelve el nombre está dentro del campus (servidores DNS de Unisimón). Los clientes nuevos y recurrentes resuelven el nombre y operan localmente aunque la salida a Internet del campus se corte.
* **Requisitos que Dependen de Colaboración de TI Unisimón:**
  - Asignación oficial de IP estática/reserva en la subred de AudacIA.
  - Creación del registro DNS en el servidor DNS interno de la universidad.
  - Entrega del certificado TLS en formato PEM/PFX para instalar en el proxy Caddy del servidor Windows.

---

### Opción 2: Subdominio Público Gratuito + ACME DNS-01 (Alternativa Autónoma a $0)
* **Mecanismo (Si TI Unisimón no puede gestionar la Opción 1 inmediatamente):**
  1. Registrar un subdominio gratuito en un servicio dinámico de reputación consolidada: ej. `audacia-usb.duckdns.org` (Coste: $0).
  2. Apuntar el registro `A` en DuckDNS hacia la IP privada del servidor Windows dentro del campus (ej. `10.x.x.x` o `172.16.x.x`).
  3. Configurar en el servidor Windows el proxy inverso Caddy (código abierto, $0) para solicitar y renovar automáticamente un certificado TLS gratuito de Let's Encrypt mediante el desafío **ACME DNS-01** usando el token de API de DuckDNS.
  4. Caddy realiza la validación creando un registro TXT temporal en DuckDNS saliendo a Internet por HTTPS. **No se requiere ningún puerto abierto hacia el servidor**.
* **Coste:** **$0**.
* **Seguridad y Acceso Externo:**
  - El servidor sigue estando en una IP privada de campus. Ningún usuario externo de Internet puede acceder a los servicios porque el firewall de Unisimón bloquea el tráfico entrante.
  - Contexto seguro (`isSecureContext = true`) habilitado y micrófono operativo sin advertencias.
* **Limitaciones y Condiciones de Fallo:**
  - *Dependencia de Internet para Nuevos Clientes:* Si un cliente que nunca ha ingresado intenta conectar durante una caída de Internet, la consulta DNS al servidor externo de DuckDNS fallará. Los clientes que ya ingresaron seguirán operando mientras dure la caché DNS local del sistema operativo (TTL de 24 horas).
  - *Riesgo de DNS Rebinding:* Si el firewall perimetral de Unisimón tiene activada la protección contra *DNS Rebinding*, descartará las respuestas de DuckDNS que contengan IPs privadas. Debe verificarse con una prueba previa de un minuto.

---

### Opción 3: Acceso Directo por IP Privada con Certificado Autofirmado (Opción Degradada)
* **Mecanismo:** Acceder mediante `https://IP_PRIVADA` utilizando un certificado autofirmado generado en Windows con PowerShell / OpenSSL.
* **Coste:** **$0**.
* **Comportamiento Offline:** 100% inmune a caídas de Internet y DNS.
* **Limitación Crítica Ineludible:**
  - Viola el requisito de cero configuración en clientes. Los usuarios deben saltarse advertencias rojas de seguridad.
  - En dispositivos móviles (iOS Safari y Android Chrome), los certificados autofirmados no confiables **desactivan o bloquean las APIs de captura WebRTC/Audio**, impidiendo el uso de Haku Inventario por voz a menos que se instale y confíe manualmente una CA raíz en cada dispositivo.

---

## 4. MATRIZ COMPARATIVA DE SOLUCIONES A PRESUPUESTO $0

| Criterio de Evaluación | Opción 1: Subdominio Institucional Unisimón | Opción 2: Subdominio Gratuito DuckDNS + ACME | Opción 3: IP Privada Directa Autofirmada |
|---|:---:|:---:|:---:|
| **Coste financiero adicional** | **$0** | **$0** | **$0** |
| **¿Requiere compra de hardware de red?** | **NO ($0)** | **NO ($0)** | **NO ($0)** |
| **¿Requiere acceso al router de campus?** | **NO** | **NO** | **NO** |
| **¿Expone servicios a Internet externo?** | **CERO (IP Privada)** | **CERO (IP Privada)** | **CERO (IP Privada)** |
| **Cero configuración en clientes BYOD** | **SÍ (100% transparente)** | **SÍ (100% transparente)** | **NO (Alerta roja / Bloqueo)** |
| **Acceso a micrófono (`getUserMedia`)** | **Habilitado nativamente** | **Habilitado nativamente** | **Bloqueado o degradado** |
| **Operación offline durante cortes WAN** | **Total (DNS interno campus)**| **Parcial (solo clientes en caché)**| **Total** |
| **Dependencia de gestión externa** | Coordinación con TI Unisimón | Autonomía técnica del equipo | Autonomía técnica del equipo |
| **Riesgo técnico principal** | Plazo de respuesta de TI | Bloqueo por DNS Rebind en campus | Micrófono web inoperable en móviles |

---

## 5. RECOMENDACIÓN ÚNICA Y HOJA DE RUTA VIABLE

### 5.1. Recomendación Arquitectónica Principal
Adoptar la **Opción 1 (Subdominio Institucional `audacia.unisimon.edu.co`)** como la solución definitiva y formal del proyecto, complementada temporalmente por la **Opción 2 (Subdominio gratuito DuckDNS con Let's Encrypt)** como vía de desarrollo y pruebas autónomas inmediatas a $0 mientras se formaliza el trámite con TI Unisimón.

### 5.2. Solicitud Concreta a la Dirección de TI de Unisimón
Para habilitar la Opción 1 sin coste alguno para la universidad, se debe presentar un memorando técnico solicitando únicamente tres parámetros estándar de intranet:
1. **Asignación de IP Fija en Campus:** Definición de una dirección IP privada estática (o reserva en su DHCP) para la interfaz de red del servidor Windows de AudacIA en la VLAN correspondiente.
2. **Registro DNS Intranet:** Creación de un registro `A` en los servidores DNS internos de campus:
   `audacia.unisimon.edu.co` $\rightarrow$ `IP_PRIVADA_ASIGNADA`.
3. **Certificado TLS Institucional:** Provisión de un certificado firmado válido para `audacia.unisimon.edu.co` (o inclusión en el certificado wildcard `*.unisimon.edu.co` administrado por la universidad).

### 5.3. Qué Hacer si TI Unisimón Tarda o Rechaza la Solicitud
Si la gestión institucional toma tiempo o no es aprobada:
1. Se activa de inmediato la **Opción 2**: subdominio gratuito `audacia-usb.duckdns.org` con Caddy en Windows y Let's Encrypt DNS-01 ($0).
2. Se realiza la prueba de verificación de *DNS Rebinding* en la red de campus: si el DNS de campus lo resuelve, el sistema funciona con HTTPS y micrófono en toda la universidad a coste $0.
3. Si el firewall de campus bloquea el DNS Rebind y TI Unisimón no colabora, se documentará formalmente que el micrófono web en dispositivos móviles no administrados queda restringido por políticas de seguridad del perímetro institucional, requiriendo que los operarios de bodega utilicen estaciones fijas cableadas o portátiles con excepción de contexto seguro configurada en el navegador (`--unsafely-treat-insecure-origin-as-secure`).
