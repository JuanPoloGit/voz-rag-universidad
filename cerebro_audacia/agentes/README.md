# CEREBRO AUDACIA — REGISTRO Y ADAPTADORES DE AGENTES

## 1. Propósito
Alojar los adaptadores formales y el registro ligero que conectan los subsistemas existentes de AudacIA a la plataforma unificada, definiendo interfaces explícitas y controladas.

---

## 2. Contrato Mínimo de Agente (Conceptual)
Cada agente integrado debe declarar explícitamente sus límites y capacidades sin delegar la seguridad en archivos de texto:
- **`id`:** Identificador único en minúsculas (ej. `"haku_inventario"`, `"haku_visitante"`).
- **`canales_permitidos`:** Lista de canales de entrada autorizados (ej. `["web_lan", "rest_api"]` o `["kiosk_consola"]`).
- **`requiere_autenticacion`:** Booleano que exige sesión válida en Django (`UsuarioAudacIA`).
- **`herramientas_autorizadas`:** Catálogo estricto de funciones invocables en el backend.
- **`fuente_conocimiento`:** Declaración de la fuente documental asociada (o `None` si es puramente transaccional).
- **`modalidad_salida`:** Tipo de respuesta requerida (ej. `["texto", "audio_cliente_wav"]`).

---

## 3. Adaptadores Previstos

### A. Haku Inventario (`HakuInventarioAdapter`)
- **Rol:** Agente piloto de gestión de inventario para operarios de bodega.
- **Conexión:** Consume exclusivamente los servicios de `sistema_inventario/integraciones/haku/services.py` (`procesar_comando_inventario`, `ejecutar_confirmacion_propuesta`).
- **LLM / RAG:** `None` (0% probabilístico, 100% determinista).
- **Seguridad:** Requiere usuario autenticado, respeto al rol y validación estricta de almacén asignado en PostgreSQL.
- **Estado:** Congelado. No se modifica su código original.

### B. Haku Visitante (`HakuVisitanteAdapter`)
- **Rol:** Agente de información institucional para exposiciones y visitantes del stand.
- **Conexión:** Interfaz hacia los servicios de `hacu/session.py` y `hacu/rag.py`.
- **LLM / RAG:** Activo sobre colección pública ChromaDB y Llama 3.1 8B.
- **Seguridad:** Aislamiento total. Cero acceso a base de datos de inventario, modelos de Django o datos privados.

### C. Futuros Agentes (ej. Informes / Auditoría)
- Podrán incorporarse implementando el mismo contrato mínimo sin alterar el funcionamiento de los agentes existentes.

---

## 4. Estado Actual
- **Estado:** Definición conceptual del contrato y especificación de adaptadores.
- **Implementación:** Ningún adaptador de código activo ha sido instanciado todavía.
