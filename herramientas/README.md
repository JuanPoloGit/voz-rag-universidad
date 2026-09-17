# Herramientas

## `extraer.py` y `construir_corpus.py` — el corpus

El corpus de `documents/` no se escribe a mano: se genera desde los informes en
Word, de modo que actualizar la documentación no obliga a transcribir nada y no se
pueden colar datos inventados.

```powershell
python herramientas/extraer.py <carpeta con los .docx> fuente.json
python herramientas/construir_corpus.py fuente.json documents/ [corpus_anterior.md]
```

El tercer argumento es opcional y aporta el detalle técnico de los proyectos
didácticos, que nunca estuvo en los `.docx`. Sin él el corpus se genera igual, pero
esas fichas salen más pobres; el generador avisa.

`breves.py` es lo único escrito a mano: el resumen de una línea de cada proyecto
para el índice-catálogo. Es compresión editorial de las descripciones del propio
informe, y existe porque el índice tiene que caber entero en el contexto para poder
enumerar los 32 proyectos de una vez.

Tras regenerar, **medir siempre**: `python -m pruebas.recuperacion --n 6 --detalle`.

Detalle completo en [../docs/corpus.md](../docs/corpus.md).

## `limpiar.py` — la carpeta de trabajo

Borra lo que el proyecto genera solo y puede volver a generar: bytecode, el log,
los informes de pruebas, grabaciones de diagnóstico y cachés de tooling. Por
defecto solo lista.

```powershell
python -m herramientas.limpiar                        # ver qué sobra
python -m herramientas.limpiar --aplicar              # borrarlo
python -m herramientas.limpiar --aplicar --memoria    # y la memoria de visitantes
python -m herramientas.limpiar --aplicar --indice     # y el índice vectorial
```

`--memoria` y `--indice` van aparte a propósito: la memoria son datos de personas
reales y el índice cuesta un par de minutos de reconstrucción.
