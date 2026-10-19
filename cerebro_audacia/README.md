# CEREBRO AUDACIA — NÚCLEO DE COORDINACIÓN E INTEGRACIÓN MULTIAGENTE LOCAL

## 1. Propósito y Misión
`cerebro_audacia/` es el espacio arquitectónico desacoplado destinado a alojar la orquestación, contratos, adaptadores e interfaces de integración para los distintos asistentes y servicios locales de AudacIA.

Este directorio nace bajo un principio de ingeniería estricto: **desacoplamiento sin reescritura ni duplicación**. No reemplaza a Django, no absorbe la lógica transaccional de inventario y no altera los componentes de stand público existentes.

---

## 2. Principios Arquitectónicos Innegociables

1. **Preservación de lo existente:**
   - **Haku Inventario** permanece en `sistema_inventario/integraciones/haku/` como el primer agente piloto transaccional de control físico. Su desarrollo funcional está en pausa temporal.
   - **Haku Visitante** permanece en `hacu/` como el agente de atención en exposiciones.
2. **Determinismo vs. Generación:**
   - Las operaciones de inventario (stock, entradas, salidas, préstamos) son **estrictamente deterministas**. No utilizan LLM ni RAG en su flujo de producción.
   - Los Modelos de Lenguaje (LLM) se reservan exclusivamente para agentes que requieran diálogo abierto, síntesis explicativa o consulta sobre texto no estructurado (ej. Visitante).
3. **Fuentes de Verdad Absolutas:**
   - La base de datos relacional (PostgreSQL en despliegue / SQLite en desarrollo) es la única fuente de verdad para existencias, movimientos y usuarios.
   - Las bases vectoriales (RAG) o documentos Markdown son fuentes opcionales exclusivas para conocimiento documental público y jamás intervienen en datos transaccionales.
4. **Seguridad y Autorización en el Backend:**
   - Ningún prompt, archivo Markdown o configuración de agente puede otorgar permisos efectivos. La autorización reside obligatoriamente en Django (`UsuarioAudacIA`, roles, almacén asignado y validaciones `select_for_update()`).
5. **Operación 100% Local y Desconectada:**
   - Cero dependencias de nube, APIs comerciales de terceros o exposición a Internet. El sistema opera dentro de la red local (LAN) de AudacIA.

---

## 3. Estructura de Áreas Conceptuales

| Directorio | Propósito | Estado Actual |
|---|---|:---:|
| `documentacion/` | Registro de decisiones de arquitectura, especificaciones de contratos y presupuestos de VRAM/hardware. | Estructura inicial documentada |
| `proveedor_modelo/` | Abstracción para el acceso a modelos de inferencia local compartidos (ej. daemon/servidor local LLM). | Estructura inicial documentada |
| `agentes/` | Definición del contrato formal de agente y adaptadores de conexión hacia los subsistemas reales. | Estructura inicial documentada |
| `conocimiento/` | Gobernanza de fuentes documentales, reglas de ingesta RAG y límites de acceso por agente. | Estructura inicial documentada |
| `pruebas/` | Banco de pruebas unitarias aisladas para contratos, adaptadores y enrutamiento, sin tocar la base de datos de producción. | Estructura inicial documentada |

---

## 4. Estado Actual del Módulo
- **Fase:** Esqueleto inicial de arquitectura y delimitación de responsabilidades.
- **Implementación:** Ningún servicio, cliente de LLM ni adaptador de código activo ha sido instanciado todavía.
