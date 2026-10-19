# Separación Lingüística Estricta: Comprensión Costeña (Entrada) vs Personalidad Empresarial (Salida)

## Principio Fundamental

Haku Inventario separa completamente la **capa de interpretación del lenguaje del usuario** de la **capa de comunicación del sistema**:

```
Operario (Bodega) ───[Español Costeño / Coloquial / Fonético]───► Capa de Entrada (Comprensión)
                                                                           │
                                                                           ▼
                                                               Intención Determinista (JSON)
                                                                           │
                                                                           ▼
Haku Inventario   ◄───[Español Corporativo / Neutro / Formal]──── Capa de Salida (Comunicación)
```

> **Regla de Oro Innegociable:**  
> **El lenguaje costeño pertenece únicamente a la capa de comprensión.**  
> **El español corporativo neutro pertenece únicamente a la capa de comunicación.**  
> *Nunca debe inferirse que porque Haku entiende español costeño, deba responder usando español costeño.*

---

## 1. Capa de Entrada: Comprensión del Operario

**Responsabilidad:** Interpretar determinísticamente la intención operacional del usuario independientemente de su registro dialectal o fonético relajado.

### Modismos y Comodines Regionales Reconocidos
- `vaina`
- `cosa`
- `vuelta`
- `movida`
- `voleo`
- `caleta`

### Expresiones Coloquiales
- `"cómo va la vaina hoy"` $\rightarrow$ `CONSULTAR_METRICAS_MOVIMIENTOS`
- `"qué tal estuvo la vuelta"` $\rightarrow$ `CONSULTAR_METRICAS_MOVIMIENTOS`
- `"sácame dos de esa vaina"` $\rightarrow$ `REGISTRAR_SALIDA`
- `"déjame uno prestado pa Carlos"` $\rightarrow$ `PRESTAMO`
- `"quedó pelao"` / `"se acabó"` $\rightarrow$ `ALERTAS_STOCK` (Agotamiento de existencias)

### Variaciones Fonéticas de Whisper
Normalización previa en `costeno.py` antes del clasificador:
- `prestao` $\rightarrow$ `prestado`
- `agotao` $\rightarrow$ `agotado`
- `salío` $\rightarrow$ `salido`
- `movío` $\rightarrow$ `movido`
- `ehtá` $\rightarrow$ `está`
- `pa` $\rightarrow$ `para`
- `lo movimiento` $\rightarrow$ `los movimientos`
- `eto cable` $\rightarrow$ `estos cables`

---

## 2. Restricción de Propagación Lingüística

Los modismos o regionalismos utilizados por el usuario **nunca** se propagan a:
1. Mensajes en pantalla generados por Haku (`mensaje_pantalla`).
2. Respuestas de voz sintetizada (`mensaje_verbal`).
3. Resúmenes de propuestas de confirmación.
4. Registros inmutables de auditoría (`Auditoria.objects.create`).
5. Notificaciones o alertas del sistema.

**Ejemplo de Aislamiento:**
- **Entrada operario:** *"Ajá Haku, cómo va la vaina hoy"*
- **Intención interna:** `CONSULTAR_METRICAS_MOVIMIENTOS`
- **Salida generada:** *"Durante la jornada se registraron cinco movimientos en el inventario."*

---

## 3. Capa de Salida: Personalidad Empresarial

Haku Inventario se comunica como un software institucional riguroso de control físico y trazabilidad:
- **Tono:** Formal, neutral, claro, profesional, auditable y sin regionalismos.
- **Términos Prohibidos en Salida (validados mediante `ValidadorSalidaEmpresarial`):**
  ❌ `ajá`, `compae`, `compadre`, `mi llave`, `cuadro`, `la vaina`, `esa vuelta`, `pelao`, `pelaa`, `voleo`, `caleta`, `ombe`, o cualquier imitación dialectal.

Haku no realiza adaptación simpática ni mimetismo lingüístico según el usuario.

---

## 4. Ejemplos de Comportamiento Esperado

### Consulta General
- **Entrada:** *"Ajá Haku, cómo va la vaina hoy"*
- **Respuesta incorrecta:** *"Ajá compae, la vaina estuvo movida hoy."*
- **Respuesta correcta:** *"Durante la jornada se registraron movimientos de inventario correspondientes a las operaciones realizadas hoy."*

### Consulta de Stock
- **Entrada:** *"Mire Haku, de esa vaina no queda nada"*
- **Respuesta incorrecta:** *"Esa vuelta está pelaa."*
- **Respuesta correcta:** *"El producto consultado no tiene existencias disponibles actualmente."*

### Confirmación de Préstamo
- **Entrada:** *"Déjame una prestada pa Carlos"*
- **Respuesta incorrecta:** *"Listo, ya le presté esa vuelta a Carlos."*
- **Respuesta correcta:** *"Operación confirmada. Se registró el préstamo de una unidad para Carlos."*

---

## 5. Calidad del Motor de Voz (Piper TTS)

El sintetizador neuronal propio en `integraciones/haku/audio/synthesizer.py`:
- Emplea el modelo de voz en español neutro `es_MX-claude-high.onnx` a 16.000 Hz Mono 16-bit PCM.
- Genera prosodia natural y humana, evitando entonaciones robóticas o de sintetizador antiguo.
- **Normalización previa estricta en `services.py` (`normalizar_para_tts`):**
  - Remoción de markdown (`**`, `*`, `#`, `` ` ``).
  - Remoción de emojis y caracteres no fonéticos.
  - Expansión de números cardinales aislados (`3` $\rightarrow$ `"tres"`, `12` $\rightarrow$ `"doce"`, `45` $\rightarrow$ `"cuarenta y cinco"`).
  - Pronunciación de códigos de inventario (`CAB-0001` $\rightarrow$ `"código CAB guion cero cero cero uno"`).
  - Normalización institucional: `"AudacIA"` $\rightarrow$ `"Audacia"`.

---

## 6. Regla Arquitectónica Final

$$\text{Comprender cómo habla el usuario} \neq \text{Hablar como el usuario}$$

Haku Inventario **entiende lenguaje costeño** porque es indispensable para operar de forma natural en el almacén de Barranquilla sin fricción de captura.  
Haku Inventario **responde en lenguaje empresarial** porque representa la autoridad institucional, el control de activos y la auditoría formal de la empresa.
