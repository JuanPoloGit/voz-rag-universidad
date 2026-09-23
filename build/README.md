# Generar el manual en PDF

`docs/HACU-documentacion.pdf` es la documentación de `docs/` ensamblada en un solo
manual de ~71 páginas, con portada, índice y enlaces internos. No se escribe: se
genera, y hay que regenerarlo cada vez que cambia algo en `docs/`.

```bash
python build/armar_manual.py          # docs/*.md  ->  build/manual.md
cd build && pandoc metadatos.yaml manual.md \
  --from=markdown+pipe_tables+fenced_code_attributes+raw_attribute \
  --pdf-engine=xelatex \
  --top-level-division=chapter \
  --include-in-header=cabecera.tex \
  --highlight-style=tango \
  -o ../docs/HACU-documentacion.pdf
```

## Qué hace falta

`pandoc` y una distribución de TeX con XeLaTeX. En Windows, MiKTeX instala los
paquetes que falten la primera vez que se compila. En Debian o Ubuntu:

```bash
sudo apt install pandoc texlive-xetex texlive-latex-extra lmodern
```

Paquetes de LaTeX que usa la plantilla: `fvextra`, `tcolorbox`, `titlesec`,
`fancyhdr`, `mdframed`, `needspace`, `xurl`, `microtype`, `tikz`.

## Las tres piezas

| Archivo | Qué resuelve |
|---|---|
| `armar_manual.py` | Une los `.md` en el orden del manual, da identificadores estables a cada encabezado, convierte los enlaces entre archivos en referencias internas y sustituye los símbolos que ninguna fuente del sistema dibuja |
| `cabecera.tex` | Portada, encabezados, cajas de código, tablas y avisos |
| `metadatos.yaml` | Título, fuentes, márgenes e índice |

El orden de los capítulos está en `CAPITULOS`, dentro de `armar_manual.py`. Un
`.md` nuevo en `docs/` **no entra solo**: hay que añadirlo a esa lista.

## Lo que el script arregla y por qué

- **Identificadores estables.** Los enlaces entre documentos (`[x](voz.md#…)`) no
  significan nada dentro de un PDF de una pieza. Cada encabezado recibe un
  identificador propio y los enlaces se reescriben contra él. Si un ancla no
  existe, el script lo dice al terminar en vez de generar un enlace roto.
- **Etiquetas legibles.** «Ver `docs/instalacion.md`» pasa a «Ver *Instalación
  desde cero*»: en el PDF no hay archivos que abrir.
- **Símbolos.** Ninguna fuente instalada dibuja ✅, ❌, ⚠, 🎤 ni 🔧; saldrían como
  cajas negras. Se sustituyen por marcas de texto **solo en el PDF**: los `.md`
  del repositorio no se tocan.
- **Bloques de código largos.** Antes de uno de doce líneas o más se reserva
  espacio, para que un diagrama no se parta entre dos páginas.
- **Numeración.** Varios capítulos numeran sus propios pasos («3. llama-cpp-python
  con CUDA»). LaTeX no numera además las secciones, o saldría «2.3 3. llama-cpp».
