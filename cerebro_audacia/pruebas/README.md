# CEREBRO AUDACIA — SUITE DE PRUEBAS AISLADAS

## 1. Propósito
Espacio destinado a alojar las pruebas unitarias y de integración del núcleo `cerebro_audacia/`, verificando contratos de agentes, adaptadores, mecanismos de seguridad y clientes de inferencia de forma totalmente aislada.

---

## 2. Principios de Prueba
1. **No Destructividad:**
   - Ninguna prueba debe modificar la base de datos de desarrollo (`db.sqlite3` ni PostgreSQL).
   - Las pruebas que requieran persistencia utilizarán bases de datos en memoria (`:memory:`) o el test runner transaccional aislado de Django.
2. **Aislamiento de Hardware:**
   - Las pruebas de adaptadores y lógica de enrutamiento se ejecutan con dobles de prueba (*mocks/stubs*) sin requerir GPU ni modelos pesados en VRAM.
   - Las pruebas de consumo de memoria y VRAM se separarán explícitamente y requerirán banderas de ejecución opcionales (`--gpu`).
3. **Validación de Límites:**
   - Verificación de que peticiones de un agente no autenticado sean bloqueadas.
   - Verificación de que una inyección de prompt no acceda a herramientas transaccionales.
   - Verificación de que un agente sin RAG declarado no invoque consultas semánticas.

---

## 3. Estado Actual
- **Estado:** Estructura inicial creada.
- **Implementación:** Ningún script de prueba activo ha sido escrito en este directorio todavía.
