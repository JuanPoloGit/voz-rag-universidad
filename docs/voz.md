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

Se corre **antes de levantar el modelo**. En una portátil con webcam, base de
conexiones y auriculares llega a haber cinco entradas de audio, y el sistema no
siempre elige la que uno cree. Los índices se fijan con `HACU_ENTRADA` y
`HACU_SALIDA`.

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

faster-whisper `small` en int8, ~0,5 GB de VRAM: convive con el Llama de 8B dentro
de los 12 GB de la portátil.

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
