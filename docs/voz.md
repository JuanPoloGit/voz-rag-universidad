# La capa de voz

Oído y boca de HACU. Toda la capa es **opcional**: si falta una dependencia o no
hay tarjeta de sonido, `ServicioDeVoz.disponible` es `False` y HACU sigue
funcionando por escrito. Una exhibición sin sonido es mala; una exhibición caída
es peor.

---

## Diagnóstico: lo primero que se corre al llegar a la sala

```powershell
python -m hacu.voz                        # lista micrófonos y altavoces con su índice
python -m hacu.voz --probar               # graba 3 s y los reproduce
python -m hacu.voz --hablar "Hola, soy Hacu"
python -m hacu.voz --descargar            # baja la voz de Piper configurada
python -m hacu.voz --calibrar             # mide el ruido de la sala

# Sin depender del altavoz ni del micrófono de la máquina
python -m hacu.voz --hablar "Hola, soy Hacu" --guardar prueba.wav
python -m hacu.voz --transcribir grabacion.wav
python -m hacu.voz --autoprueba           # circuito completo sin micrófono
```

El diagnóstico sin argumentos, además de los dispositivos, avisa de lo que falta
sin impedir el arranque: la voz de Piper sin descargar y `resemblyzer` sin
instalar. Las dos degradan la exhibición en silencio, y por eso salen aquí y en el
arranque de HACU en vez de en un traceback a mitad de conversación.

Se corre **antes de levantar el modelo**. En una portátil con webcam, base de
conexiones y auriculares llega a haber cinco entradas de audio, y el sistema no
siempre elige la que uno cree. Los índices se fijan con `HACU_ENTRADA` y
`HACU_SALIDA` para el arranque, o **a mano desde el panel de operador** de la
ventana, que es lo cómodo cuando la diadema se conecta con HACU ya en marcha
(ver [interfaz.md](interfaz.md)).

`--guardar` vuelca la síntesis a un WAV sin reproducirla y `--transcribir` hace el
camino inverso. Sirven para separar dos fallos que suenan igual: que HACU corte la
frase, o que la corte la salida de audio. **Si el WAV está entero, el problema no
es de HACU.**

`--autoprueba` cierra el circuito sin hardware: sintetiza cuatro frases de
exhibición con Piper y se las transcribe a sí mismo, comprobando que los nombres
propios sobreviven al reconocedor. Es la única forma de saber si el reconocimiento
funciona en una máquina donde todavía no hay micrófono.

---

## Reconocimiento

faster-whisper `large-v3-turbo` en int8, ~1,0 GiB de VRAM: convive con el Llama de
8B dentro de los 12 GB de la portátil (8,20 GiB de los 10,5 disponibles a 16k,
según `herramientas.presupuesto_vram --stt large-v3-turbo`).

### Por qué ya no es `small`

Era `small` y el comentario del código decía que subir «mejora poco en español con
audio de cerca». El log de la primera sesión en vivo con voz dice lo contrario:

```
17:57:19  'Explícame cómo es la creatividad general de este'
17:57:31  'explícame la de la actividad general de Einstein'
17:57:48  'Explícame sobre la Relatividad General de Einstein.'
```

Tres intentos para una frase, con el vocabulario ya sembrado y `beam_size=5`. En
una sala con ruido, y con visitantes que no van a repetirse tres veces, eso es la
exhibición entera: HACU contesta con aplomo a algo que nadie dijo.

`large-v3-turbo` lleva el **mismo codificador** que `large-v3` —que es de donde
sale la precisión— con un decodificador de cuatro capas en vez de treinta y dos.
809M parámetros frente a los 244M de `small` y los 1550M de `large-v3`.

`HACU_STT=small` vuelve al anterior sin tocar código, y `HACU_STT=tiny` es la
salida si algún día no cabe.

### La confianza del reconocedor, registrada

`faster-whisper` calcula tres cifras en cada segmento y hasta ahora se tiraban a
la basura. Ahora van al log en cada transcripción:

```
Transcrito (83520 muestras) [logprob -0.87 · sin_voz 0.04 · compresion 1.62]: '...'
```

- `logprob`: probabilidad media de los tokens elegidos, en logaritmo. Cerca de 0
  es seguro. Es la señal principal.
- `sin_voz`: probabilidad de que el audio no fuera habla.
- `compresion`: muy alta significa texto repetitivo, que es como se ve una
  alucinación de Whisper.

Manda el segmento **peor**: basta una parte mal oída para dudar de la frase.

**No hay ningún umbral todavía, y es deliberado.** Para que HACU pida «¿me lo
repites?» cuando no entiende hace falta un corte, y ese corte tiene que salir de
medir sesiones reales —frases bien oídas contra frases mal oídas— y no de un
número elegido a ojo. Un umbral mal puesto interrumpe al visitante cada tres
preguntas, que es peor que el problema que arregla. Hay una prueba en el bloque
`confianza` que falla si alguien mete un umbral en la clase sin datos detrás.

La alternativa —preguntarle al modelo de 8B si la frase «tiene sentido»— se
descartó: acertaría a medias y cuesta una inferencia entera por turno.

Los nombres propios de la exhibición van sembrados en el prompt inicial del
reconocedor (`vocabulario` en `VozConfig`), y eso no es una precaución teórica.
Medido con `--autoprueba`, sobre las mismas frases:

| Frase | Con vocabulario | Sin vocabulario |
|---|---|---|
| «¿Qué proyectos tiene AudacIA?» | AudacIA | «a UDAC ya» |
| «…sobre Holosand y el sensor Kinect» | Holosand, Kinect | «olo San», «Kinex» |

Con el nombre destrozado, el router no reconoce el dominio y HACU responde que no
sabe de un proyecto que sí está documentado.

Si el reconocedor no cabe junto al modelo (`CUDA out of memory`), `HACU_STT=tiny`
baja el consumo, y `--voz-salida` lo quita del todo.

---

## Síntesis

Piper si está instalado; si no, la voz del sistema operativo (SAPI5 en Windows).
El respaldo suena a robot de los noventa, pero existe en cualquier máquina y hace
que HACU hable el primer día sin descargar nada.

HACU **no espera a terminar de generar para hablar**: un segmentador trocea el
stream en frases y pronuncia la primera mientras el modelo escribe la segunda.

Piper se carga **dentro del proceso**, una sola vez al arrancar. El respaldo que
lanza `python -m piper` por frase sigue ahí, pero solo como último recurso:
medido, **0,21 s por frase en proceso frente a 2,52 s lanzando el proceso**, y en
una máquina Windows con una voz `high` esa diferencia sube a más de diez segundos
de silencio en cada punto. El arranque dice qué motor se eligió y avisa si acabó
en el lento.

`HACU_TTS` acepta `piper-proceso`, `piper-externo`, `sistema` o `mudo` para forzar
uno.

### Cómo se escribe y cómo se dice

`hacu/voz/pronunciacion.py` reescribe el texto **solo para la voz**, en el último
paso antes de sintetizar: la pantalla y la memoria conservan la ortografía real.
El visitante ve «AudacIA» y oye «Audacia».

Hace falta porque la ortografía de la exhibición engaña al sintetizador. Medido
sintetizando la frase y transcribiendo lo sintetizado, que es la única forma de
saber qué sale por el altavoz sin tener a alguien escuchando:

| Escrito | Se oía | Se oye ahora |
|---|---|---|
| Hacu | «Aku» | «Jacu» |
| AudacIA | «Uga Ia», «Oda ya» | «Audacia» |
| IoT | «yote» | «Internet de las Cosas» |
| 3.000 m² | «temedos» | «metros cuadrados» |
| OEA | «olla», «Bahía» | «Organización de los Estados Americanos» |
| SkinnIA | «Skinny a» | «Skinia» |

Las siglas que el castellano ya deletrea bien (ROV, ROP, PCR, HLB) **no** están en
el léxico: no hay reglas de más. «OEA» se dice por su nombre completo porque Piper
no la pronuncia en ninguna grafía — se probó `OEA`, `O.E.A.` y `O E A`, y las tres
salían mal.

El léxico se amplía sin tocar código con `VozConfig.pronunciaciones`. Si se define,
**sustituye** al léxico medido: hay que incluir también las entradas que se quieran
conservar.

### La pausa que parecía de las tildes

En escena se oía una pausa marcada en las palabras acentuadas, como si el acento
partiera la palabra. **No era el acento.** Medido sobre la voz y la versión de
Piper de esta máquina:

| Qué se comprobó | Resultado |
|---|---|
| El texto que llega a Piper | NFC limpio, sin acentos descompuestos, sin caracteres de más |
| La fonemización de espeak-ng (`es-419`) | Correcta: `esta`→`ˈesta`, `está`→`estˈa`, `investigación`→`investˌiɣasjˈon` |
| Huecos con tilde vs. sin tilde | **Sin** tilde salen *más* pausas, no menos: la grafía sin acento descoloca el acento tónico |
| `noise_w` (variabilidad de duración) | Irrelevante: de 0.0 a 0.8 los huecos no cambian |

Lo que sí existe es que el modelo mete **silencios internos de 250 a 450 ms** en
los límites prosódicos —«Claro, con gusto.» traía 450 ms en la coma—. En
castellano esos límites caen justo detrás de la sílaba tónica, y de ahí la
impresión de que la culpa era de la tilde.

`hacu/voz/audio.py` recorta esos huecos a un tope (`pausa_maxima_ms`, 120 ms por
defecto, `HACU_PAUSA_MS=0` lo desactiva). Solo toca el aire: recorta el silencio
*interno*, deja el del principio y el del final, y **no altera ni una muestra de
voz**, así que la acentuación sale idéntica. Medido sobre las mismas frases: el
hueco mayor baja de 450 ms a 120 ms y la duración total solo cae un 2,4 %, que es
lo que se espera de quitar aire y no de hablar más rápido.

### Bilingüe: dos voces, una por idioma

Piper es monolingüe por modelo -no hay una sola voz que hable español e inglés-,
así que hablar los dos de verdad depende de tener las dos cargadas.
`hacu/idioma.py` detecta el idioma de cada frase (léxico funcional + tildes, sin
modelo de por medio) y `Locutor` elige con esa pista qué voz usar, frase a
frase.

`piper_voz_en` trae **por defecto** `en_US-hfc_female-medium` -no vacío-. Antes
sí lo estaba, y ese vacío era el bug real detrás de "el inglés suena como si lo
leyera desde el español": la voz en inglés puede estar descargada en la
máquina y aun así no usarse nunca, porque nada la fija sin
`HACU_VOZ_MODELO_EN` puesta a mano en cada arranque, y esa variable es fácil de
fijar una vez para probar y olvidar en el lanzador. Sin ella, cada frase en
inglés salía con el motor de **español** leyendo grafía inglesa: no es una
cuestión de acento, es el modelo de voz equivocado. Con un nombre por defecto,
`ruta_de_voz` la encuentra sola en cuanto está descargada
(`python -m hacu.voz --descargar --idioma en`); si no lo está, el arranque
avisa (ver `comprobar_dependencias`) y HACU cae al mismo respaldo de
siempre -hablar inglés con la voz en español antes que quedarse muda-, así que
el default no puede romper una máquina nueva que aún no descargó nada.

La detección de idioma también decide, turno a turno, en qué idioma responde
el modelo (`ContextBuilder._recordatorio_de_idioma` en `context.py`): la regla
21 del prompt de sistema ya lo pide, pero medido en vivo un 8B no la obedece
con fiabilidad entre otras veinte reglas, así que se refuerza aparte en cada
turno, igual que el nombre del visitante o el hilo de la conversación.
Como el corpus entero está en español, esa misma instrucción también le pide
explícitamente redactar el inglés desde cero -no traducir la frase española
palabra por palabra-, que es la otra mitad de "no se siente nativo": una
traducción literal se nota en el orden de las palabras aunque la voz sea la
correcta.

### El volumen que subía y bajaba

Piper normaliza **por pico y por frase**, y igualar picos no es igualar sonoridad.
Medido sobre nueve frases del mismo audio crudo:

| Ajuste de nivel | RMS medio | Dispersión |
|---|---|---|
| Por pico (lo que hacía Piper) | 0.1635 | **16.8 %** |
| Por RMS con techo de pico (`nivelar`) | 0.1677 | **13.9 %** |

El caso que más se notaba eran las frases cortas: a un «Sí.» la normalización por
pico le subía un 35 % la sonoridad respecto de una frase larga. Ahora HACU
normaliza por RMS con techo en 0.95, que es lo que juzga el oído, y la sonoridad
media se mantiene, así que al actualizar nadie tiene que tocar el volumen.

> `python -m hacu.voz --guardar` sigue escribiendo el WAV **crudo de Piper**, sin
> recorte ni nivelado: existe para aislar si el fallo es de la síntesis o de la
> salida de audio, y para eso hace falta ver la síntesis sin retocar.

---

## Callar a mitad de frase

Callar a HACU **corta de inmediato**, no en el siguiente punto: el audio se entrega
a la tarjeta en trozos de 50 ms en vez de frase entera. Medido: **10 ms hasta
detenerse y 36 ms de audio ya en vuelo.**

Vale para el botón «Callar a HACU», para `Esc`, y para cuando el visitante vuelve
a pulsar la barra espaciadora para hablar — que es lo importante: quien interrumpe
espera que el robot se calle, no que termine la frase.

---

## Quién está hablando

HACU nota cuando **se acerca otra persona** y cierra el perfil anterior, para que
el siguiente visitante no herede el nombre ni los hechos del que acaba de irse.

Lo que hace y lo que deliberadamente no hace:

- **No identifica a nadie.** Solo dice «esta es otra voz», nunca «esta es Camila».
- **No guarda nada.** La referencia de timbre vive en memoria durante la visita y
  se borra al pasar al siguiente visitante o al cerrar. No hay huella de voz en
  disco, y por tanto no hay base biométrica que custodiar.

La distinción importa: un vector de voz almacenado es un dato biométrico, y esto es
una exhibición abierta al público con menores entre el público. Guardarlo sería una
decisión de la universidad, no de un commit. Ver [datos.md](datos.md).

### Una frase rara no cambia de visitante

Hacen falta **dos intervenciones seguidas** por debajo del umbral para declarar
que hay otra persona. No es prudencia teórica. En la primera sesión en vivo con
voz, la misma persona dio estas similitudes, en orden:

```
0.554   ← "Cambio de hablante"
0.678   0.821   0.863   0.903
```

Ese 0.554 le partió el perfil en dos a mitad de conversación —HACU dejó de saber
cómo se llamaba— y era un valor atípico contra un timbre construido con una sola
frase de tres segundos. Las cuatro siguientes, la misma persona, suben sin parar.

El precio es un turno: si de verdad se acerca otra persona, su primera frase se le
atribuye todavía a la anterior. A cambio, una frase con ruido o dicha de lado ya
no borra la identidad de quien sí está delante. El contador se reinicia en cuanto
vuelve a reconocerse el timbre, así que dos dudas separadas por diez turnos no se
suman.

La similitud de cada intervención va al log para poder ajustar el umbral con datos
de sala en vez de a ojo.

Si falta `resemblyzer`, se dice **una vez, al arrancar**, y la función queda
apagada para la sesión. Antes se reintentaba en cada intervención y escupía el
traceback entero una vez por turno: en una exhibición eso es una pared de rojo en
la consola del operador que no aporta nada después de la primera línea.

Calibrado con dos voces distintas sintetizadas: **misma voz 0,844–0,947, voces
distintas 0,314–0,448**, y el umbral por defecto (0,65) cae en medio de ese hueco.
Con voces humanas y ruido de sala la separación será menor, así que cada
comparación queda en el log para poder recalibrar en la sala. `HACU_HABLANTES=0` lo
desactiva.

---

## Pulsar para hablar, y por qué es el modo por defecto

En una tarima el altavoz alimenta al micrófono: con detección automática HACU se
oye a sí mismo y se responde solo. No es comodidad, es que el otro modo no
funciona en una sala abierta.

La escucha automática está implementada y se activa desde el panel de la ventana
—calibra el ruido de sala al encenderla—, pero es para sala controlada o
auriculares. `HACU_VOZ_AUTO=1` la activa desde el arranque, y espera a que HACU
termine de saludar antes de calibrar: medir el ruido de sala mientras el altavoz
está sonando dejaría el umbral por encima de cualquier voz humana.

En consola, la escucha automática no está disponible: se habla con `9` o `v`, que
abren el micrófono hasta el siguiente Enter.

---

## Solo altavoz

`--voz-salida` monta la boca y no el oído: no descarga el modelo de reconocimiento
ni necesita entrada de audio. Sirve para probar la voz desde una sesión remota, y
también el día que el micrófono falle a media jornada — HACU sigue hablando y el
operador escribe las preguntas.

---

## Los archivos

| Archivo | Responsabilidad |
|---|---|
| `microfono.py` | Captura con sounddevice: pulsar-para-hablar y escucha hasta silencio |
| `deteccion.py` | Detección de voz por energía. Lógica pura, verificable sin hardware |
| `transcriptor.py` | faster-whisper, con el vocabulario sembrado |
| `sintetizador.py` | Piper en proceso, Piper externo, voz del sistema y modo mudo |
| `segmentador.py` | Trocea el stream de tokens en frases pronunciables |
| `pronunciacion.py` | Léxico de «cómo se escribe / cómo se dice» |
| `audio.py` | Recorte de huecos y nivelado por RMS. Aritmética pura, sin altavoz |
| `hablantes.py` | Cambio de timbre, efímero y sin identificar |
| `dispositivos.py` | Inventario y diagnóstico de audio |
| `__init__.py` | `ServicioDeVoz`: los cuatro verbos que ven la consola y la ventana |
| `__main__.py` | El diagnóstico de `python -m hacu.voz` |

---

## Limitaciones conocidas

- El reconocimiento está medido de extremo a extremo con `--autoprueba` (4/4
  nombres propios, ~1,9 s por frase en CPU). Lo que **no** se ha probado es la
  captura por micrófono real: el nivel de entrada, el ruido de sala y el recorte de
  la primera sílaba solo se ven con hardware delante.
- Con altavoz abierto, la escucha automática hace que HACU se oiga a sí mismo.
- Una sola estación: hay un perfil activo a la vez.
