# CEREBRO AUDACIA — GOBERNANZA DE CONOCIMIENTO Y RAG LOCAL

## 1. Propósito
Establecer las reglas de frontera, indexación y recuperación documental de AudacIA, garantizando que la información no estructurada permanezca rigurosamente aislada de los datos operacionales de la empresa.

---

## 2. Límites y Reglas Innegociables
1. **Prohibición de Inventario en RAG:**
   - El stock, movimientos, existencias y estados de préstamos **jamás se indexan en RAG ni en Markdown**.
   - Los datos de inventario son transaccionales y mutan en tiempo real; su única fuente de verdad es PostgreSQL.
2. **Aislamiento por Agente:**
   - Cada fuente documental debe asignarse de forma explícita a un agente autorizado.
   - Haku Visitante solo tiene acceso a las fuentes institucionales públicas de `documents/` (proyectos, patentes, reseña de AudacIA y USB).
   - Documentos privados o actas internas nunca deben quedar expuestos a recuperación semántica pública.
3. **Manejo de Markdown sin Dependencia Editorial:**
   - La plataforma no depende de la ejecución de aplicaciones de terceros (como Obsidian) en el servidor.
   - Los archivos `.md` residen en el sistema de archivos local y pueden ser versionados mediante Git y editados con cualquier software de texto.

---

## 3. Relación con Recursos Existentes
- **Corpus Público Existente:** Carpeta `documents/` en la raíz del repositorio (12 archivos Markdown de proyectos y patentes).
- **Motor Vectorial Existente:** ChromaDB configurado en `hacu/rag.py` con embeddings `sentence-transformers` (`paraphrase-multilingual-MiniLM-L12-v2`).
- **Indexación Selectiva:** La sincronización vectorial es idempotente y se basa en hashes SHA-256 de las secciones `##` de los archivos Markdown.

---

## 4. Estado Actual
- **Estado:** Delimitación de políticas de conocimiento.
- **Implementación:** Ninguna base vectorial adicional ni sincronizador ha sido creado.
