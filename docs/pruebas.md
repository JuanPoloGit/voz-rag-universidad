# Pruebas

Cinco herramientas, cada una para una pregunta distinta.

| Orden | Qué mide | Cuánto tarda | ¿GPU? |
|---|---|---|---|
| `python -m pruebas.test_unidades` | Regresión completa de las capas deterministas y el turno | ~2 s | no |
| `python -m pruebas.recuperacion` | Calidad del RAG sobre el corpus real | ~20 s | no |
| `python -m pruebas.bateria --seco` | 60 entradas sueltas contra las capas deterministas | ~5 s | no |
| `python -m pruebas.bateria --vivo` | Las mismas 60 contra el modelo real | minutos | sí |
| `python -m pruebas.conversacion` | Una visita completa de 30 turnos encadenados | minutos | sí |

**Después de cada cambio**: `test_unidades`. **Antes de una exhibición**:
`bateria --vivo` y `conversacion`.

---

## `test_unidades` — la que se corre siempre

383 comprobaciones, sin GPU, sin red, sin tarjeta de sonido y sin dependencias
extra. Cada una existe porque algo se rompió de verdad alguna vez.

```powershell
python -m pruebas.test_unidades           # todo
python -m pruebas.test_unidades estilo    # solo los bloques que casen
python -m pruebas.test_unidades voz saludo
```

Los bloques: `routing`, `seguimiento`, `nombres`, `identity`, `sanitizer`,
`estilo`, `calidez`, `truncado`, `memory`, `context`, `profundidad`, `session`,
`saludo`, `prompts`, `guion`, `cierre`, `voz`, `hablantes`, `pronunciacion`,
`interfaz`, `config`.

El bloque `interfaz` monta la ventana de verdad en el backend `offscreen` de Qt,
así que corre igual en una máquina sin pantalla. El bloque `prompts` comprueba la
invariante de contaminación: que el system prompt no nombre ningún proyecto del
corpus.

Tarda dos segundos y no necesita GPU. **No se comitea en rojo.**

---

## `recuperacion` — ¿encuentra HACU lo que tiene documentado?

Mide el RAG solo, sin el modelo: 36 consultas contra el corpus real, comprobando
que cada una recupera el fragmento que le corresponde.

```powershell
python -m pruebas.recuperacion --n 6 --detalle
```

Referencia actual: **36/36 con el embedding multilingüe, 27/36 con el de Chroma
por defecto**. Esa diferencia es la razón de que el arranque se aborte si el
multilingüe no está disponible.

También comprueba que el índice-catálogo entrega **los 32 proyectos**: si se añade
uno al informe y no a `herramientas/breves.py`, se ve aquí.

Es la prueba que se corre **después de tocar el corpus** ([corpus.md](corpus.md)).

---

## `bateria` — 60 entradas sueltas

Mide el turno, no la conversación: 60 entradas barajadas, cada una con su
intención esperada, su efecto sobre el perfil y su criterio de aceptación.

```powershell
python -m pruebas.bateria --seco          # capas deterministas
python -m pruebas.bateria --vivo          # contra el modelo real
python -m pruebas.bateria --listar        # imprime el catálogo sin ejecutarlo
python -m pruebas.bateria --semilla 7     # fija el orden
```

`--vivo` usa una base de datos temporal y deja un informe en JSON y CSV en
`pruebas/informes/`. Esos informes **no se versionan**: miden una versión concreta
del corpus y envejecen mal.

`PRUEBAS.md` describe las 60 entradas y su criterio. Se regenera:

```powershell
python -m pruebas.bateria --listar > PRUEBAS.md
```

---

## `conversacion` — una visita con hilo

La batería mide entradas sueltas y barajadas. Esto mide lo contrario: **una sola
visita**, en orden fijo, con un mismo visitante, donde cada turno depende del
anterior. Es la prueba que se parece a lo que va a pasar en la tarima.

```powershell
python -m pruebas.conversacion               # los 30 turnos
python -m pruebas.conversacion --listar      # imprime el guion sin ejecutarlo
python -m pruebas.conversacion --desde G15   # arranca en un turno concreto
python -m pruebas.conversacion --tipo CORRECCION
python -m pruebas.conversacion --voz         # además, hablando
```

El guion (`pruebas/guion.py`) tiene seis tipos de turno:

| Tipo | Qué mide |
|---|---|
| `ESPECIFICA` | dato puntual documentado; la respuesta debe ser corta |
| `EXTENDIDA` | pide desarrollo; la respuesta debe pasar de ~500 caracteres |
| `SEGUIMIENTO` | «¿y eso para qué sirve?»: no se sostiene sin el turno anterior |
| `CORRECCION` | el visitante afirma algo falso y HACU tiene el dato correcto |
| `SIN_DATO` | no está en el corpus; hay que admitirlo, incluso bajo presión |
| `PERSONAL` | acoge el nombre, lo recuerda, no revela de dónde lo sabe |

Cada turno declara qué términos del corpus **debe** contener la respuesta y qué
invenciones **no** puede contener, con comprobación sensible a la negación:
«Holosand no usa gafas» no cuenta como haber dicho «gafas».

Los turnos G17 y G18 reproducen la alucinación del observatorio astronómico y el
desafío de fuentes que la siguió, de modo que esa regresión queda cubierta por una
comprobación y no por la memoria de nadie. Los turnos G26 a G30 recorren los
niveles de profundidad: contar los proyectos, enumerar un área entera y dar la
cátedra de uno solo sin soltar el hilo.

`GUION.md` es la versión legible del guion. Se regenera:

```powershell
python -m pruebas.conversacion --listar > GUION.md
```

---

## Cómo leer un fallo

Un fallo de `test_unidades` es **siempre** un fallo del código: no hay modelo
detrás, todo es determinista.

Un fallo de `bateria --vivo` o de `conversacion` puede ser del código o del
modelo. Antes de tocar nada, repetir ese turno solo:

```powershell
python -m pruebas.conversacion --desde G17
```

Si el fallo no se repite, era variabilidad del modelo. Si se repite, es real.

Y hay una tercera posibilidad, que ha pasado varias veces: **el criterio de la
prueba estaba mal**. Un turno que exige la palabra «cultivo» cuando el corpus dice
«agricultura» falla sin que nada esté roto. Antes de cambiar el sistema, leer la
respuesta completa y preguntarse si de verdad está mal.

---

## Limpiar después

```powershell
python -m herramientas.limpiar               # lista lo prescindible
python -m herramientas.limpiar --aplicar     # lo borra
```

Los informes de pruebas, el log y el bytecode se acumulan entre corridas. La base
de memoria y el índice vectorial van aparte (`--memoria`, `--indice`) porque uno
son datos de personas y el otro cuesta un par de minutos de reconstrucción.
