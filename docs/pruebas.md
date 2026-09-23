# Pruebas

Cinco herramientas, cada una para una pregunta distinta.

| Orden | Qué mide | Cuánto tarda | ¿GPU? |
|---|---|---|---|
| `python -m pruebas.test_unidades` | Regresión completa de las capas deterministas y el turno | ~2 s | no |
| `python -m pruebas.recuperacion` | Calidad del RAG sobre el corpus real | ~20 s | no |
| `python -m pruebas.bateria --seco` | 60 entradas sueltas contra las capas deterministas | ~5 s | no |
| `python -m pruebas.bateria --vivo` | Las mismas 60 contra el modelo real | minutos | sí |
| `python -m pruebas.conversacion` | Una visita completa de 100 turnos encadenados | 20-35 min | sí |

**Después de cada cambio**: `test_unidades`. **Antes de una exhibición**:
`bateria --vivo` y `conversacion`.

---

## `test_unidades` — la que se corre siempre

742 comprobaciones, sin GPU, sin red, sin tarjeta de sonido y sin dependencias
extra. Cada una existe porque algo se rompió de verdad alguna vez.

```powershell
python -m pruebas.test_unidades           # todo
python -m pruebas.test_unidades estilo    # solo los bloques que casen
python -m pruebas.test_unidades voz saludo
```

Los bloques: `routing`, `seguimiento`, `nombres`, `lexico`, `identity`, `sanitizer`,
`estilo`, `calidez`, `truncado`, `memory`, `context`, `profundidad`, `cuidado`, `session`,
`saludo`, `prompts`, `guion`, `cierre`, `voz`, `hablantes`, `pronunciacion`,
`interfaz`, `config`.

El bloque `interfaz` monta la ventana de verdad en el backend `offscreen` de Qt,
así que corre igual en una máquina sin pantalla. El bloque `prompts` comprueba la
invariante de contaminación: que el system prompt no nombre ningún proyecto del
corpus.

Tarda dos segundos y no necesita GPU. **No se comitea en rojo.**

---

## `recuperacion` — ¿encuentra HACU lo que tiene documentado?

Mide el RAG solo, sin el modelo: 39 consultas contra el corpus real, comprobando
que cada una recupera el fragmento que le corresponde.

```powershell
python -m pruebas.recuperacion --n 6 --detalle
```

Referencia actual: **39/39 con el embedding multilingüe, 38/39 con el de Chroma
por defecto**. Esa diferencia es la razón de que el arranque se aborte si el
multilingüe no está disponible.

Esa consulta que fallaba —**«¿Qué publicaciones científicas tienen?»**— la arregló
el rescate léxico: la sección se llama «Publicaciones científicas» y la palabra es
rara en el corpus, así que entra por coincidencia literal aunque el embedding no la
acerque.

Ojo con leer demasiado en el 38/39 del embedding en inglés: estas 39 consultas casi
siempre nombran lo que buscan («¿Qué es Neupeek?»), que es justo donde el rescate
léxico brilla. Una pregunta de tarima que no nombra nada sigue dependiendo del
embedding, y por eso el arranque sigue exigiendo el multilingüe.

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
python -m pruebas.conversacion               # los 100 turnos
python -m pruebas.conversacion --listar      # imprime el guion sin ejecutarlo
python -m pruebas.conversacion --desde G51   # segunda mitad
python -m pruebas.conversacion --hasta G50   # primera mitad
python -m pruebas.conversacion --tipo CORRECCION
python -m pruebas.conversacion --voz         # además, hablando
```

Son 100 turnos, entre veinte y treinta y cinco minutos de GPU. Se puede partir en
dos sesiones con `--hasta G50` y `--desde G51`.

El guion (`pruebas/guion.py`) tiene once tipos de turno. Los seis primeros miden
**datos**; los cinco últimos miden **conversación**, que es lo que separa a un
asistente de exhibición de un buscador con voz:

| Tipo | Qué mide | Turnos |
|---|---|---|
| `ESPECIFICA` | dato puntual documentado; la respuesta debe ser corta | 23 |
| `EXTENDIDA` | pide desarrollo; la respuesta debe pasar de ~500 caracteres | 5 |
| `SEGUIMIENTO` | «¿y eso para qué sirve?»: no se sostiene sin el turno anterior | 9 |
| `CORRECCION` | el visitante afirma algo falso y HACU tiene el dato correcto | 10 |
| `SIN_DATO` | no está en el corpus; hay que admitirlo, incluso bajo presión | 5 |
| `PERSONAL` | acoge el nombre, lo recuerda, no revela de dónde lo sabe | 4 |
| `VAGA` | «ajá», «mmm», «¿y eso?», «no entendí»: solo se entiende con el turno inmediatamente anterior | 15 |
| `AFIRMACION` | el visitante afirma en vez de preguntar; hay que reaccionar, no dar una clase | 9 |
| `NEGATIVA` | «no», «no me interesa», «no me repitas»: hay que aceptarlo sin insistir | 7 |
| `ALEATORIA` | fuera de la exhibición; sentido común sin inventar ni sermonear | 9 |
| `ANCLA` | vuelve a algo dicho 30 o más turnos atrás; mide memoria larga | 4 |

Cada turno declara qué términos del corpus **debe** contener la respuesta y qué
invenciones **no** puede contener, con comprobación sensible a la negación:
«Holosand no usa gafas» no cuenta como haber dicho «gafas».

**Cómo se mide perder el hilo.** Un turno vago no tiene respuesta única, así que
no se puede exigir una. Lo que sí tiene firma observable es el fallo: cuando HACU
pierde el hilo, se pone a hablar de otro proyecto. Por eso casi todos los turnos
`VAGA` prohíben explícitamente el nombre de proyectos que no vienen a cuento, y
la suite comprueba que ninguno se queda sin exigir ni prohibir nada — un turno
vago que lo acepta todo no mide nada.

**Regresiones cubiertas por turnos concretos:**

- `G54`–`G56`: la alucinación del observatorio astronómico, el desafío de fuentes
  que la siguió y la presión social («mi profesor no me va a mentir»).
- `G75`–`G79`: los niveles de profundidad — contar los proyectos, desglosarlos y
  corregir una cifra mal entendida.
- `G42`, `G89`–`G91`: las anclas largas. `G91` pregunta a noventa turnos de
  distancia si el dron volaba: es la comprobación de que HACU no se contradice a
  sí mismo a lo largo de una visita entera.
- `G50` y `G96`: recordar lo que dijo el visitante de sí mismo (que estudia
  Sistemas), no solo su nombre.
- `G03`, `G06`, `G20`, `G45` y `G72`: prohíben **irse a cualquier proyecto** del
  catálogo y **abandonar el hilo** («eso queda fuera de mi área»). Con una lista
  de cinco nombres se escapaba lo importante: en la corrida del 21/09 un «Ajá.»
  se fue a hablar de Vallenato Master y **el turno aprobó**.
- `G63`, `G92` y `G99`: **literales de la sesión con público del 21/09/2026**, no
  inventados. El injerto de Einstein («cuéntame de la universidad, pero antes
  explícame la relatividad»), y los dos cierres largos que se colaban por el tope
  de 12 palabras. `G99` prohíbe además «has mencionado» y «has entendido»: ahí
  HACU le atribuyó al visitante tres proyectos que nunca nombró.

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
