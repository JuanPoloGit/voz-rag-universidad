# CEREBRO AUDACIA — DOCUMENTACIÓN DE ARQUITECTURA E INTERFACES

## 1. Propósito
Este directorio centraliza la documentación técnica, diagramas conceptuales, contratos de interfaz y registros de decisiones arquitectónicas (ADRs) de la plataforma multiagente AudacIA.

---

## 2. Límites y Responsabilidades
- **Qué incluye:**
  - Definiciones de contratos entre agentes y el núcleo.
  - Especificación de interfaces de comunicación (REST, sockets, llamadas internas).
  - Presupuestos de memoria (VRAM/RAM) y políticas de concurrencia en GPU.
  - Guías de aislamiento y seguridad para red LAN.
- **Qué NO incluye:**
  - Documentación de usuario final o manuales operativos de bodega (residen en `sistema_inventario/web/` o manuales de Haku).
  - Documentación de exhibición del stand de Haku Visitante (reside en `docs/`).
  - Archivos con credenciales, tokens o variables de entorno reales.

---

## 3. Documentación Preexistente Vinculada
Para no duplicar el conocimiento ya establecido en el repositorio, se vinculan las fuentes de verdad técnicas:
- **Haku Visitante:** `docs/arquitectura.md`, `docs/configuracion.md`, `docs/corpus.md`, `docs/voz.md`.
- **Haku Inventario:** `sistema_inventario/integraciones/haku/README_LENGUAJE.md` (separación dialecto costeño / salida neutra) y `sistema_inventario/integraciones/haku/README_MEMORIA.md` (memoria operacional en RAM y desacoplamiento de TTLs).
- **Presupuestos de Hardware:** `herramientas/presupuesto_vram.py` (cálculo formal de pesos, caché KV y modelos candidatos).

---

## 4. Documentación y Guías del Sistema
- **Informe Oficial de Auditoría y Cierre:** [`informe_auditoria_final_audacia.md`](informe_auditoria_final_audacia.md) (Auditoría arquitectónica, corrección del timeout en `ResilientAgentExecutor` y certificación de suites).
- **Manual Operativo Global:** [`docs/GUIA_OPERATIVA_AUDACIA.md`](../../docs/GUIA_OPERATIVA_AUDACIA.md) (Puesta en marcha desde cero, diagnóstico y uso de `llama-server.exe`, Django y troubleshooting).
- **Manual de Incorporación de Agentes:** [`docs/COMO_AGREGAR_AGENTES.md`](../../docs/COMO_AGREGAR_AGENTES.md) (Procedimiento reproducible de 11 pasos para implementar, probar y gobernar agentes con `AgentContract`).
- **Arquitectura General:** [`ARQUITECTURA-GENERAL-AUDACIA.md`](ARQUITECTURA-GENERAL-AUDACIA.md).
- **Decisiones de Arquitectura (ADR):** [`ADR-001-arquitectura-de-datos.md`](ADR-001-arquitectura-de-datos.md) y [`ADR-002-red-lan-y-acceso.md`](ADR-002-red-lan-y-acceso.md).
