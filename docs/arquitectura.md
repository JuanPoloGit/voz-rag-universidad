# Arquitectura

Cómo está montado HACU por dentro, qué hace cada pieza y por qué está donde está.

---

## Idea de fondo

Todo corre **en local**: el modelo, el índice vectorial y la memoria de los
visitantes no salen de la máquina. No hay llamadas a ninguna API. Eso condiciona
cada decisión: la ventana de contexto es un presupuesto fijo, el modelo es un 8B
cuantizado con manías conocidas, y no hay un modelo mayor esperando detrás para
arreglar lo que salga mal.

De ahí salen las tres reglas que gobiernan el diseño:

1. **Lo determinista se hace en Python, no se le pide al modelo.** Recortar
   coletillas, filtrar adulación, trocear frases para la voz, decidir qué se
   recupera: todo eso tiene una implementación exacta y verificable. Al modelo se
   le pide solo lo que solo él puede hacer.
2. **Nada literal delante del modelo.** Una frase de ejemplo metida en un prompt
   acaba recitada palabra por palabra. Ha pasado cuatro veces en este proyecto
   (ver «Contaminación», abajo). Las reglas dicen *qué hacer*, no *qué decir*.
3. **Cada umbral sale de una medida.** No hay ningún número en `config.py` que
   sea una suposición; al lado de cada uno está qué se midió para elegirlo.
4. **Local quiere decir local también al arrancar.** Las librerías de modelos
   consultan HuggingFace al abrir un modelo aunque ya esté descargado, y una sala
   sin Wi-Fi convertía eso en un arranque caído. Tanto el embedding del RAG como
   el reconocedor piden primero la copia en disco y solo descargan si no la hay.

---

## El recorrido de un turno

```
  visitante
     │  voz ──► microfono.py ──► transcriptor.py (faster-whisper) ──► texto
     │                             └─► hablantes.py: ¿es otra persona?
     ▼
  session.py  ── identity.py ──►  ¿quién es? ¿hay que migrar el perfil?
     │
     ├─ routing.py  ──────────►  AUDACIA · UNIVERSIDAD · GENERAL
     │                            (y hereda el dominio si es un seguimiento)
     │
     ├─ context.py  ──────────►  profundidad_de(): CATALOGO · RESUMEN · DETALLE · CENTRO
     │       │                    rag.py: índice y/o fichas de ChromaDB
     │       └─ memory.py ────►   historial reciente + hechos del visitante
     │                            = system prompt + historial + turno enriquecido
     ▼
  llm.py  (llama.cpp, acceso serializado)  ──► stream de tokens
     │
     ├─ estilo.py: RetenedorDeCola ──► recorta coletillas, adulación y fugas
     │                                  antes de que el token salga a ninguna parte
     ▼
  ┌──────────────┬───────────────┬──────────────────┐
  pantalla       voz             memory.py          extractor.py
  (burbuja/      (segmentador    (historial)        (hechos, en
   consola)       + Piper)                           segundo plano)
```

Lo que se **lee** en pantalla, lo que se **oye** y lo que se **guarda** son
exactamente el mismo texto. Es la razón de que el filtrado de estilo ocurra en el
retenedor y no en cada consumidor: si cada uno filtrara por su cuenta, acabarían
divergiendo.

---

## Los módulos

### Núcleo

| Archivo | Responsabilidad |
|---|---|
| `run_hacu.py` | Entrada: argumentos, diagnóstico de hardware, elige consola o ventana |
| `hacu/bootstrap.py` | *Composition root*. Construye y cablea todo el árbol una sola vez |
| `hacu/config.py` | Toda la configuración, con la justificación de cada valor |
| `hacu/session.py` | Orquestación de un turno. La consola, la ventana y las pruebas usan este mismo camino |
| `hacu/llm.py` | Único acceso a llama.cpp, serializado. Decodificación restringida a JSON para los prompts de utilidad |
| `hacu/prompts.py` | System prompt, perfiles de audiencia, saludo de apertura y prompts de utilidad |

### Conocimiento

| Archivo | Responsabilidad |
|---|---|
| `hacu/routing.py` | Enrutado léxico: a qué corpus va la pregunta. Aprende los nombres de proyecto del índice al arrancar |
| `hacu/context.py` | Qué se le manda al modelo: política de profundidad, anclaje de seguimientos, notas privadas |
| `hacu/rag.py` | Indexación por secciones `##` y recuperación sobre ChromaDB |
| `hacu/lexico.py` | Rescate por palabra rara: lo que el embedding no encuentra pero está escrito |

### Memoria

| Archivo | Responsabilidad |
|---|---|
| `hacu/identity.py` | Quién es el visitante: valida nombres, migra perfiles |
| `hacu/memory.py` | Persistencia en SQLite: historial y hechos |
| `hacu/extractor.py` | Extrae hechos de lo que dice el visitante, en un hilo aparte |
| `hacu/sanitizer.py` | Qué merece guardarse como hecho y qué no |

### Salida

| Archivo | Responsabilidad |
|---|---|
| `hacu/estilo.py` | Recorte determinista: coletillas, adulación, fugas del andamiaje |
| `hacu/cuidado.py` | Qué hacer cuando el visitante no viene a preguntar por proyectos |
| `hacu/cli.py` | Consola del operador |
| `hacu/interfaz/` | Ventana de exhibición ([interfaz.md](interfaz.md)) |
| `hacu/voz/` | Oído y boca ([voz.md](voz.md)) |

### Alrededor

| Carpeta | Contenido |
|---|---|
| `documents/` | El corpus en dos niveles ([corpus.md](corpus.md)) |
| `herramientas/` | Generación del corpus desde los informes en Word, y limpieza del árbol |
| `pruebas/` | Suite de regresión y herramientas de medición ([pruebas.md](pruebas.md)) |
| `docs/` | Esta documentación |

---

## Enrutado: tres dominios, uno heredado

`routing.py` clasifica cada mensaje en `AUDACIA`, `UNIVERSIDAD` o `GENERAL` con
patrones léxicos, no con un modelo: son microsegundos y es auditable.

Al arrancar, `bootstrap.py` le enseña al router **los nombres de los 32
proyectos**, leídos del índice-catálogo. Sin eso, «cuéntame de Holosand» caía en
`GENERAL` y se saltaba toda la política de profundidad.

Un mensaje que el router no reconoce pero que se apoya en lo anterior
—«¿y eso para qué sirve?», «cuéntame más», o cualquier frase que empiece por
«y»— **hereda el dominio del último turno que sí lo tuvo**. Es más fiable que
dejárselo a la distancia semántica, que no sabe de qué se venía hablando.

La excepción: las preguntas sobre HACU mismo («¿y a ti qué te gusta?») **no**
heredan. Si heredaran, una pregunta personal arrastraría documentación de AudacIA
y HACU contestaría con un proyecto.

---

## Profundidad: la pregunta decide cuánto se trae

Con 32 proyectos, una sola forma de recuperar no sirve. `context.py` clasifica en
cuatro niveles, y cada uno recupera algo distinto:

| Nivel | Qué lo dispara | Qué recupera |
|---|---|---|
| `CATALOGO` | «¿qué proyectos tienen?» | el índice entero, cargado tal cual |
| `RESUMEN` | «explícame cada proyecto» | el índice como esqueleto **+** fichas |
| `DETALLE` | «cuéntame todo sobre Mary» | fichas, sin filtro |
| `CENTRO` | «¿qué patentes tiene el centro?» | solo lo institucional |

**Invariante: el índice se carga, no se busca.** Queda excluido de toda búsqueda
por similitud, por dos motivos medidos:

- Buscarlo devolvía 2 de sus 6 fragmentos, así que HACU enumeraba 5 proyectos de
  32 y creía que esos eran todos.
- Al revés: siendo un documento genérico que menciona todo, ganaba consultas que
  no eran suyas. A «¿cuántos estudiantes hay?» le llegó el catálogo y HACU
  respondió «32 proyectos» — un número de otra pregunta — en vez de admitir que
  no tenía el dato.

**Y lo que el embedding pierde, lo rescata la palabra.** Medido: a «¿cuál sería el
proyecto más interesante para los conductores?» no volvía la ficha de Detección de
Fatiga Visual, que dice literalmente «conductores»; ganaba el Proyecto Tanque,
porque un dron terrestre está semánticamente más cerca de «conducir vehículos» que
una ficha sobre párpados. Un embedding de paráfrasis mide parecido global y una
palabra rara dentro de una ficha larga se diluye — pero esa palabra rara es la
señal más fuerte que hay.

`hacu/lexico.py` mantiene un índice invertido en memoria sobre lo que Chroma tiene
indexado, y rescata las piezas que comparten con la pregunta una palabra
**distintiva**: una que aparece en pocas piezas del corpus. La rareza se mide sobre
el propio corpus y no se lista a mano — «proyecto» sale en casi todas y no
distingue nada; «conductor» sale en dos y lo distingue todo.

El rescate **sustituye**, no añade: entra la rescatada y sale el peor vecino
semántico. Así el número de fragmentos no cambia y el presupuesto de contexto sigue
siendo el que se verifica al arrancar. Medido sobre las 39 consultas, con el
rescate desactivado y reactivado en la misma corrida: **39/39 con el embedding
multilingüe** (36/39 sin el rescate) y **38/39 con el embedding en inglés**
(25/39 sin él) — una coincidencia literal no depende del idioma del embedding,
así que el modo degradado deja de ser tan malo.

El nivel `CENTRO` existe por otra medida: el corpus de AudacIA son 37 fragmentos
de fichas contra 10 institucionales, y sin filtrar, «¿qué patentes tiene el
centro?» recuperaba cuatro proyectos de salud y ni una patente.

---

## El presupuesto de contexto

La ventana son 16 384 tokens. `config.py` estima el peor caso —system prompt +
fragmentos + hechos + historial— y `bootstrap.verificar_presupuesto()` **aborta el
arranque** si no cabe.

No es exceso de celo. Cuando el contexto se desborda, llama.cpp descarta por la
izquierda, y lo primero que se pierde es el system prompt: HACU cambiaría de
personalidad a mitad de exhibición sin que nada lo indicara. Es mejor no arrancar.

```powershell
python -c "from hacu.config import AppConfig; print(AppConfig().presupuesto_contexto())"
```

Actualmente: **10 274 de 15 484 tokens**.

---

## Cuando el visitante no pregunta nada

Tres fallos medidos en la sesión con público del 21/09/2026, y las tres capas
deterministas que los cierran. Los tres salían del mismo sitio: el sistema
trataba cualquier turno como una pregunta que había que contestar largo.

### 1. El cierre largo se colaba

`es_despedida()` tenía un tope de 12 palabras para no confundir «Muchas gracias,
ahora explícame el Tanque» con una despedida. El efecto era el contrario del
buscado: los cierres que más daño hacen son largos.

> «Wow, suena muy impresionante… Definitivamente si tengo alguna curiosidad
> adicional te la haré saber. Por ahora, gracias por compartir toda esa
> información.» — 60 palabras, ninguna pregunta, cuatro párrafos de respuesta.

Ahora no hay tope de palabras. Lo que distingue a una petición es **la posición**:
un verbo de petición que abre una oración, o un signo de interrogación. «Es genial
ver *cómo* están aplicando la IA» lleva «cómo» y no pregunta nada, y antes bastaba
esa palabra suelta para descartar el turno como no-social.

### 2. El techo de tokens no distinguía

Había dos niveles: 384 normal y 900 extenso. Un agradecimiento se llevaba 384 y
el modelo los llenaba —cuatro turnos de esa sesión tocaron el techo con
`extenso=False`, según `logs/hacu.log`—. Ahora son tres (`llm.Aliento`):

| Nivel | Tokens | Cuándo |
|---|---|---|
| `BREVE` | 116 | No piden nada, solo acusan recibo, o piden **un dato** |
| `NORMAL` | 384 | El caso corriente de tarima |
| `EXTENSO` | 900 | Piden desarrollo, o el catálogo entero |

**El nivel lo decide lo que el turno PIDE, no lo largo que venga.** Hay preguntas
de tres palabras que necesitan seis frases («¿y Orion?») y párrafos enteros de
agradecimiento que se contestan con una.

#### `BREVE` reconocía 3 turnos de 51

La primera versión ataba `BREVE` a los turnos sociales. El guion marca **51 de
100 turnos** como breves y el runtime reconocía **3**: los otros 48 recibían 384
tokens —unos 1.400 caracteres— y el modelo los usaba. En la cuarta corrida,
siete turnos fallaron solo por longitud.

`routing.aire_breve()` amplía el reconocimiento a tres formas de no pedir
desarrollo, y ninguna es la longitud del mensaje:

1. **No pide nada.** Un cierre, un elogio, una afirmación sobre uno mismo o una
   negativa («no me interesan los robots»). Reutiliza `pide_algo()`, que ya
   distinguía posición de palabra suelta.
2. **Solo acusa recibo.** «Ajá», «mmm», «ah, ahora sí».
3. **Pide un dato.** `pide_un_dato()` mira el texto **con tilde**, porque en
   castellano la tilde es justo lo que separa el interrogativo del relativo:
   «¿QUÉ sensor usa?» pide un dato y «tengo entendido QUE vuela» no pregunta
   nada. Y tras «qué», un **verbo** abre explicación («¿qué *hace* Neupeek?»)
   mientras que un **sustantivo** pide un valor («¿qué *sensor* usa?»). Si el
   visitante escribe sin tildes no se detecta y el turno cae en `NORMAL`, que es
   el lado seguro; el dictado de voz, que es la entrada real de la tarima, sí
   las pone.

El orden importa: **fondo > brevedad > anchura**. Pedir detalle manda sobre todo;
la brevedad manda sobre el catálogo, porque «¿cuántos proyectos tiene?» necesita
el índice entero para contar pero se responde con un número.

Los 116 tokens no son un redondeo. En la corrida del 21/09 un turno se cortó en
384 tokens y entregó 1.379 caracteres: **3,59 caracteres por token**. El guion
exige 420 caracteres en los turnos breves —unos 25 segundos hablados, que en una
sala ya es largo— y 420/3,59 = 117.

**Medido sobre las respuestas reales de esa corrida**, aplicando el techo a lo que
`aire_breve` detecta y recortando por frase completa: **+5 turnos correctos y
ninguno roto**. Es la simulación conservadora —recorta lo que el modelo ya
escribió, sin suponer que un techo más bajo le haga escribir mejor—, así que el
efecto real no debería ser peor. Leídos uno a uno, los recortes no pierden nada:
la explicación de la neumonía sigue entera en 340 caracteres de 715, y el
desmentido del observatorio en 369 de 574.

El techo es la red, no el objetivo: una respuesta que nace larga y se corta
pierde el final. Por eso hay además una nota de turno que le pide contestar en
una o dos frases, para que nazca del tamaño que toca.

> Queda fuera una cuarta forma: la **evasiva de fuera de dominio** («¿sabes cómo
> se hace el arroz de lisa?»). `fuera_de_la_exhibicion()` necesita el material
> recuperado y se calcula en `ContextBuilder`, no en `HacuSession`, que es donde
> se elige el aliento. Cuesta un turno del guion (G59) y se deja así antes que
> pasar estado entre las dos capas.

### 3. Le ponía palabras en la boca al visitante

El peor de los tres. Al rellenar esos párrafos, HACU le contaba al visitante lo
que el visitante supuestamente había dicho — y se lo inventaba:

> «Me parece que has mencionado varios proyectos que te han llamado la atención,
> incluyendo el proyecto Mario, el ROV Submarino y Solenium.»

El visitante no nombró ninguno de los tres. Es el contexto recuperado leído como
si lo hubiera dicho la persona de enfrente. Aparece siete veces en esa
transcripción y las siete son falsas.

`estilo.filtrar_atribuciones()` borra la frase entera. Solo caza las
metaobservaciones sobre lo que el visitante mencionó o entendió: **«me dijiste que
estudias Sistemas» no está en la lista a propósito**, porque eso es seguir el
hilo, que es justo lo que se quiere.

### El guardarraíl se disparaba en los seguimientos

Medido en la corrida de 100 turnos del 21/09: `fuera_de_la_exhibicion()` daba
`True` para **«Ajá.», «Mmm.», «¿En serio?», «Ah, ahora sí.»**. Un acuse de recibo
clasifica como `GENERAL` y no recupera contexto, que era exactamente la
condición del aviso.

Con «estás fuera de tu terreno» en las notas del turno, HACU hacía una de dos:

- abandonaba — «estamos fuera de mi área de conocimiento», a un «¿En serio?»
  sobre el dron del que acababa de hablar;
- o se inventaba un tema — un «Ajá.» lo mandó a explicar Vallenato Master.

Cuatro turnos así en una sola corrida. Un acuse de recibo **no tiene tema
propio**: se refiere a lo último que dijo HACU, así que no puede estar fuera de
dominio. `routing.es_acuse_de_recibo()` los reconoce —todas las palabras de
acuse o relleno, al menos una de acuse, máximo cinco— y, junto con
`es_seguimiento()`, quedan excluidos del aviso.

El agujero no es una puerta: «¿Qué hora es?» tiene cuatro palabras y sigue
siendo fuera de dominio, porque tiene tema propio.

### «No tengo información sobre MacondoLab» (y sí la tenía)

Falló en **las tres corridas** de 100 turnos, que es lo que lo separa del ruido.
La causa no era el modelo. Para «¿Qué es MacondoLab?» el embedding devolvía seis
fragmentos —Vallenato Master, Neupeek, Camille, dos cabeceras de área…— y
**ninguno nombraba MacondoLab**. HACU contestaba correctamente ante un contexto
que no traía el dato.

MacondoLab vivía en dos piezas del corpus y no tenía sección propia: aparecía
como una línea dentro de `## Personas del centro`, entre seis biografías. Un
embedding de «¿Qué es MacondoLab?» no se parece a un bloque de currículos.

El rescate léxico existe justo para esto —una palabra rara que nombra su
objetivo— pero **solo corría en `buscar`, no en `buscar_relevante`**, que es la
ruta de las preguntas que el router clasifica como `GENERAL`. Y «¿Qué es
MacondoLab?» clasifica GENERAL.

**Por qué no basta con la rareza.** Medido sobre el corpus real: `macondolab`
aparece en 2 piezas y `anos` en **1**. La palabra más rara es la que no nombra
nada, así que un umbral de frecuencia no las separa — y rescatar por «años»
metería la ficha de Mary en un «¿cuántos años tienes?», y con ella desactivaría
el aviso de fuera-de-dominio, que se apaga en cuanto hay contexto recuperado.

Lo que sí las separa es la **mayúscula que no abre oración**:
`lexico.nombres_propios()`. En la ruta GENERAL el rescate exige nombre propio,
no solo rareza. Medido después del cambio: las dos preguntas por MacondoLab
recuperan las piezas que lo nombran, y «¿cuántos años tienes?» y «cuéntame un
chiste» siguen sin recuperar nada. `pruebas/recuperacion.py` sigue en **39/39**
multilingüe y 38/39 por defecto, igual que antes.

**Y aun así seguía sin salir la pieza buena.** Con `documents/audacia_centros_hermanos.md`
en el corpus —una sección `## MacondoLab` que explica qué es, y otra `## CICV`—
el rescate seguía trayendo las dos piezas que solo lo MENCIONAN de pasada. La
causa, medida: cuatro piezas del corpus nombran MacondoLab, las cuatro empatan a
**una** palabra distintiva (`macondolab`), y el cupo del rescate es `n_results //
3` = 2. El desempate era el orden de indexación, y `audacia_centro.md` se indexa
antes que `audacia_centros_hermanos.md`, así que ganaban «Personas del centro» y
«Publicaciones científicas». La sección que responde salía cuarta y no entraba
nunca.

El desempate ahora es el **título**: a igualdad de palabras compartidas, primero
la pieza cuyo título lleva la palabra. Una sección titulada «MacondoLab» trata de
MacondoLab; una titulada «Personas del centro» lo menciona. La cuenta de
coincidencias sigue mandando por encima del título, así que una pieza que
comparte dos palabras nunca pierde contra una que comparte una.

Medido antes y después, con el mismo corpus y el mismo embedding:

| consulta | sin desempate | con desempate |
|---|---|---|
| «¿Qué es MacondoLab?» → `incubación`, `2014` | no llegan | llegan |
| «¿MacondoLab es un proyecto de ustedes?» → `al mismo nivel` | no llega | llega |

`pruebas/recuperacion.py` pasa de 36 a **39 consultas** (las tres nuevas miden
este desempate, no el embedding) y sigue en pleno: **39/39** multilingüe, 38/39
por defecto.

> Queda una limitación de **datos**, no de código: Adaptia y CRIC se nombran en
> el corpus pero no tienen ficha propia. Mientras eso siga así, HACU responderá
> con la línea que hay, no con una explicación.

### El extractor se comía los cierres

En esa misma corrida, el perfil de la visitante acabó con este «hecho»:

> Ha sido un gusto y le encanta haber conocido más sobre los proyectos de AudacIA.

Salió de un turno de cierre. No dice nada de la persona y además ocupa uno de los
doce huecos del perfil. Los turnos sociales ya no se encolan al extractor, igual
que no se encolan los de cuidado.

### El injerto que apagaba el guardarraíl

Aparte de los tres, un cuarto hallazgo. Nombrar la universidad en cualquier parte
del turno bastaba para que el router devolviera `UNIVERSIDAD`, se recuperara
contexto y `fuera_de_la_exhibicion()` diera `False`:

> «Cuéntame de la Universidad Simón Bolívar, **pero antes** explícame la teoría de
> la relatividad de Einstein.»

HACU negó la relatividad en un turno y dio la clase entera —E=mc², 1905, 1915,
curvatura del espacio-tiempo— en el siguiente, con solo reformular así.
`routing.peticion_injertada()` detecta el conector de injerto, clasifica lo que
viene **después** y, si eso queda fuera, el aviso viaja en las notas del turno
aunque la otra mitad sí sea del dominio.

---

## Ceder bajo presión, e inventar trámites

Los dos fallos más caros de la cuarta corrida no son de recuperación: HACU tenía
el contexto correcto delante.

### «No hay nada que discutir. Tu profesor tiene razón.»

En G54 la visitante afirma que la universidad tiene un observatorio astronómico.
HACU lo desmiente bien. En G55 le piden fuentes y admite que no tiene. En G56
llega la presión —«pero mi profesor no me va a mentir»— y **cede**: se retracta
de un desmentido correcto. La visitante se va creyendo algo falso y creyendo
además que se lo confirmó el centro. Es peor que cualquier exceso de longitud.

Lo que hace detectable la situación es que son **dos condiciones**, y ninguna
basta sola:

1. `routing.presiona_sobre_lo_dicho()` — el visitante empuja. Casi nunca trae un
   dato nuevo: trae una **autoridad**. Nombrar a alguien no basta, y esa fue la
   primera versión: con solo la lista de personas, «mi mamá también estudió
   aquí» disparaba el aviso y HACU se ponía a la defensiva ante un comentario
   cariñoso. Hace falta que además se le **atribuya un dicho** («me dijo», «lo
   leí», «no me va a mentir»), o una insistencia pura («revisa bien», «que sí
   tiene»).
2. `context._ultima_respuesta_niega()` — lo anterior fue una negativa. Sin esto,
   «un compañero me dijo que Holosand usa gafas» dispararía el aviso la primera
   vez, cuando todavía es una corrección normal.

Medido sobre los 100 turnos de la corrida, con las respuestas reales como
historial: la primera condición se cumple en **tres** turnos (G13, G54, G56) y
las dos juntas en **uno**, G56. Que es exactamente donde cedió.

La nota que se inyecta no le pide plantarse a lo bruto: le pide mantener lo
dicho **sin faltarle al respeto a quien se lo contó** —pudo confundirse, o hablar
de otra cosa—, que es la salida que un expositor usaría en una sala.

### Los trámites no están en el corpus, y son lo que más preguntan

El corpus son proyectos y el centro. Precios, matrículas, admisiones, prácticas,
requisitos y horarios **no están en ninguna ficha**, y es justo donde el modelo
inventa con más aplomo: en la corrida se inventó condiciones de admisión de un
doctorado (G53) y a quién escribir para hacer prácticas (G94).

`routing.pregunta_por_tramite()` reconoce la categoría entera en vez de ir turno
a turno. Sobre los 100 del guion dispara en **tres** —G35, G53, G94— y en ningún
otro: «¿cuántos metros cuadrados tiene el centro?» es una cifra del corpus, no un
trámite. La nota le dice que ese dato no lo tiene y que remita a la universidad,
y le deja dar un correo o un teléfono **si están en las notas** —el de AudacIA sí
lo está— porque lo prohibido es inventarse el procedimiento, no dar un contacto
documentado.

Ninguna de las dos se puede medir sin GPU: las notas cambian lo que el modelo
escribe, no lo que se recupera. Lo que sí está medido, y es lo que evita el falso
positivo caro, es **cuándo disparan**.

## Contaminación: la regla que más veces se ha roto

> Un modelo de 8B copia cualquier cadena literal que se le ponga delante.

Ha ocurrido cuatro veces en este proyecto, siempre igual: un texto de andamiaje
—un ejemplo dentro de un prompt, una nota de contexto, un saludo dictado— aparece
literal en la respuesta al visitante.

| Qué se puso delante | Qué recitó HACU |
|---|---|
| Un ejemplo con «Windows 11» en el prompt de extracción | «Windows 11» en la conversación |
| Una frase de perfil entera como ejemplo | La frase entera |
| La nota `Recuperado de la documentacion institucional de la Universidad Simon Bolivar:` | La frase, verbatim, en 11 de 30 turnos |
| La regla 10 decía «pregúntale qué le trae a la exhibición» | Esa pregunta exacta, ignorando lo que el visitante acababa de contar |

Las defensas, en capas:

1. **Los prompts dicen qué hacer, no qué decir.** Las notas privadas se titulan
   «Lo que sabes de AudacIA», no con el nombre de ningún documento.
2. **El saludo de apertura no pasa por el modelo.** Es texto fijo que `session.saludar()`
   escribe directamente y registra en el historial.
3. **`pruebas/test_unidades.py` lo comprueba.** El bloque `prompts` verifica que el
   system prompt no nombra ningún proyecto del corpus; el bloque `saludo`, que el
   saludo no está dentro del system prompt.
4. **`estilo.limpiar_fugas()`** borra en tiempo de ejecución lo que se cuele igual.

---

## Concurrencia

| Hilo | Qué hace |
|---|---|
| Principal | Consola, o bucle de eventos de Qt |
| `TrabajadorTurno` | Generación del turno, fuera del hilo de Qt |
| `TrabajadorTranscripcion` | Reconocimiento de voz |
| `TrabajadorEscuchaContinua` | Escucha automática, cuando está activa |
| Locutor / TTS | Síntesis y reproducción, con su propia cola |
| `BackgroundMemoryExtractor` | Extracción de hechos, después del turno |

`llm.py` **serializa** todo acceso a llama.cpp con un cerrojo: el modelo es uno y
no es reentrante, y el extractor de memoria compite con el turno del visitante
por él. El cierre (`bootstrap.cerrar`) drena la cola del extractor antes de
soltar la base de datos y la VRAM, para no perder hechos a medio escribir.

Ese cerrojo es el único punto de serialización real del sistema: los hilos son
concurrentes en Python, pero la GPU atiende de uno en uno. Mientras el extractor
resuelve sus 96 tokens, el siguiente turno del visitante espera.

### El presupuesto de VRAM

Aritmética sobre la arquitectura de Llama-3.1-8B (32 capas, 8 cabezas KV,
`head_dim` 128) y el `n_ctx` de `config.py`. No es una medición: para medirlo de
verdad, `nvidia-smi` con HACU arrancado, o `verbose=True` en `ModelConfig`.

| Partida | GiB |
|---|---|
| Pesos Q4_K_M | 4,58 |
| Caché KV fp16 con `n_ctx=16384` (128 KiB por token) | 2,00 |
| Buffer de cómputo (`n_batch=512`) | ~0,60 |
| faster-whisper `small` int8 en CUDA | ~0,50 |
| **Total** | **~7,7 de 12** |

Quedan unos 4,3 GiB libres. **HACU no está limitado por VRAM**, y por eso mover
capas a la RAM del sistema (`n_gpu_layers` menor que -1) solo puede empeorarlo:
cada token que pasa por una capa en CPU cruza el bus PCIe dos veces. Esa opción
existe para máquinas donde el modelo no cabe, no para ganar calidad.

Donde la RAM y la CPU **sí** tienen sentido es fuera del camino crítico: el
sintetizador ya corre en CPU a propósito (`SintetizadorPiperEnProceso`, 0,21 s
por frase) justo para no tocar la VRAM que necesitan el modelo y el reconocedor.

### La caché KV es la palanca, no los pesos

La caché crece **linealmente** con `n_ctx`, y es lo que decide cuánto contexto
cabe. Cuantizarla a 8 bits (`type_k`/`type_v` = `q8_0`) la parte por la mitad con
la menor pérdida de calidad de todas las cuantizaciones posibles:

| `n_ctx` | KV fp16 | KV 8 bits | Total con el modelo de hoy |
|---|---|---|---|
| 16 384 | 2,00 GiB | 1,00 GiB | 7,68 → 6,68 GiB |
| 32 768 | 4,00 GiB | 2,00 GiB | 9,68 → 7,68 GiB |

Se activa con `HACU_KV8=1`. Si la versión instalada de `llama-cpp-python` no
admite `type_k`/`type_v`, HACU **arranca igual en fp16 y lo avisa en el log**: no
se puede quedar una exhibición sin arrancar por una opción de afinado.

`herramientas/presupuesto_vram.py` hace esta cuenta para cualquier combinación de
modelo, contexto y cuantización, antes de bajarse nueve gigas:

```powershell
python -m herramientas.presupuesto_vram --todos --kv8
```

Y cruza el resultado con el presupuesto de contexto: por debajo de **13 312
tokens** de `n_ctx`, `verificar_presupuesto` aborta el arranque. Las filas que
entran en la VRAM pero no llegan a ese mínimo salen marcadas, porque son las que
más engañan — un modelo grande con poco contexto entra de sobra en la tarjeta y
no sirve.

### Subir `n_ctx` a secas no cambia nada

El error simétrico del anterior, y más fácil de cometer. El tamaño del prompt lo
fijan el **historial** y los **fragmentos recuperados**, no la ventana. Doblar
`n_ctx` de 16 384 a 32 768 sin tocar nada más deja esto:

| | prompt | disponible | holgura |
|---|---|---|---|
| `n_ctx` 16 384 | 12 004 | 15 484 | 3 480 |
| `n_ctx` 32 768 | **12 004** | 31 868 | 19 864 sin usar |

El prompt es idéntico. Lo único que cambia es la holgura, y la holgura no
responde preguntas. Para que el contexto extra sirva hay que **gastarlo**:

- `HACU_HISTORIAL` — mensajes de conversación por turno. Cuesta ~514 tokens
  cada uno en el presupuesto del peor caso. Es la palanca que ataca «no recuerda
  lo que acabo de decir».
- `broad_results_*` y `default_results` — fragmentos del corpus. ~629 tokens
  cada uno. Atacan «no encuentra el dato».

Con `n_ctx` 32 768 el historial admite hasta unos 40 mensajes antes de que
`verificar_presupuesto` aborte. 24 (doce intercambios, frente a los cinco de
fábrica) deja 12 664 tokens de margen.

Cambiar **una** de las dos palancas por experimento: con las dos a la vez no se
sabe a cuál atribuir la mejora.

---

## Por dónde entrar según lo que quieras cambiar

| Quiero… | Empezar por |
|---|---|
| Cambiar cómo habla HACU | `hacu/prompts.py`, y después `hacu/estilo.py` |
| Añadir o corregir información | `documents/`, vía [corpus.md](corpus.md) |
| Que reconozca un proyecto nuevo por su nombre | Nada: el router lo aprende del índice |
| Ajustar cuánto recupera | `RagConfig` en `hacu/config.py`; medir con `pruebas/recuperacion.py` |
| Cambiar la voz o la pronunciación | `hacu/voz/pronunciacion.py`, `VozConfig` |
| Tocar la ventana | `hacu/interfaz/`, verificable con `python -m pruebas.test_unidades interfaz` |
| Cambiar qué se recuerda de los visitantes | `hacu/sanitizer.py` y `MemoryConfig`; leer [datos.md](datos.md) |
