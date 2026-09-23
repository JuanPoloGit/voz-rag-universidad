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

---

## `presupuesto_vram.py` — qué modelo cabe

Elegir modelo a ojo sale caro: nueve gigas de descarga para descubrir que no
arranca. Esta herramienta hace la cuenta antes.

```powershell
python -m herramientas.presupuesto_vram --todos          # la tabla entera
python -m herramientas.presupuesto_vram --todos --kv8    # con la caché a 8 bits
python -m herramientas.presupuesto_vram --modelo qwen3-8b-q6_k --ctx 32768 --kv8
```

Suma tres partidas: los pesos del `.gguf`, la caché KV (que crece **linealmente**
con `n_ctx` y es la que decide de verdad) y el buffer de cómputo, más el medio
giga del reconocedor si está en la GPU. Reserva 1,5 GiB de margen: la tarjeta es
la de una portátil y además dibuja la pantalla.

Los tamaños de los `.gguf` son reales, consultados en huggingface.co el
21/09/2026. Sale con código 1 si la combinación no cabe, así que sirve en un
script. **No sustituye a medir**: `nvidia-smi` con HACU arrancado da la cifra de
verdad.

La columna `⚠ sin rol de sistema` importa más de lo que parece: HACU son nueve
mil caracteres de mensaje de sistema, y hay familias de modelos cuya plantilla de
chat lo descarta en silencio.

**Caber en la tarjeta no basta.** El presupuesto de contexto —system prompt,
corpus recuperado, hechos del perfil e historial— impone un `n_ctx` mínimo por
debajo del cual `bootstrap` se niega a arrancar. Con la configuración actual son
**13 312 tokens**. La tabla cruza las dos comprobaciones, porque las filas que
fallan así son justo las que mejor pintan: un modelo grande con poco contexto
entra de sobra en la VRAM y no arranca. Es lo que pasa con `qwen3-14b` en 12 GB —
la única fila que le entraba era a 8k, y a 8k HACU no levanta.
