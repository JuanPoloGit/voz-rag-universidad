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
