# La ventana de exhibición

```powershell
python run_hacu.py --ui --voz --pantalla-completa
```

Tres zonas, cada una para un público distinto.

**Izquierda** — el núcleo animado, el botón de hablar y el medidor de nivel. Es lo
que ve el visitante desde lejos y lo único que necesita entender.

**Centro** — la conversación en burbujas, que crece token a token mientras el
modelo escribe.

**Derecha** — los mandos del operador, que se ocultan con `F9` para que el público
no vea la cocina.

El núcleo cambia de color según el estado: **gris** en espera, **verde**
escuchando (los anillos siguen el nivel real del micrófono), **ámbar** pensando,
**turquesa** hablando. Es el único indicador que se lee desde el fondo de una sala.

---

## Teclado

| Tecla | Efecto |
|---|---|
| Espacio (mantener) | Hablarle a HACU |
| `Enter` en el recuadro | Enviar lo escrito |
| `F9` | Oculta o muestra el panel del operador |
| `F11` | Pantalla completa |
| `Ctrl+T` | Abrir la transcripción de la conversación |
| `Esc` | Callar a HACU en mitad de una frase, y recuperar el teclado |
| `Ctrl+Q` | Cerrar (a pantalla completa no hay barra de título) |

**El teclado se queda en la ventana, no en el recuadro de texto.** El recuadro
solo lo toma si haces clic en él, y lo suelta al enviar. De lo contrario la barra
espaciadora escribía un espacio en vez de abrir el micrófono, y había que hacer
clic fuera del recuadro para poder hablarle.

`Ctrl+C` en la terminal también cierra la ventana, y cierra **bien**: el bucle de
eventos de Qt vive en C++ y no devuelve el control al intérprete, así que la señal
necesita un latido que le abra hueco y un manejador que pase por el cierre normal,
en vez de matar el proceso con el modelo y la base a medias.

Si no hay micrófono, el botón se deshabilita solo y queda el campo de texto: la
ventana funciona igual.

---

## El panel del operador (`F9`)

### Visitante

| Mando | Qué hace |
|---|---|
| Etiqueta de perfil | Nombre activo y cuántos hechos recuerda de él. Se refresca sola cada 2 s, porque el extractor de memoria trabaja en segundo plano y termina después del turno |
| **Fijar nombre a mano** | Para cuando el reconocimiento destroza un nombre |
| **Nuevo visitante** | Cierra el perfil, limpia la pantalla y vuelve a saludar. Es el botón entre visita y visita |

### Audiencia

| Mando | Qué hace |
|---|---|
| Desplegable | General, Técnico, Infantil o Artístico. Cambia el registro, no el contenido |
| **Modo trivia** | HACU propone preguntas sobre la universidad y los proyectos, una a una |

### Voz

| Mando | Qué hace |
|---|---|
| **Escucha automática** | Calibra el ruido de sala y escucha sin pulsar nada. Con altavoz abierto HACU se oye a sí mismo: es para sala controlada o auriculares |
| **Callar a HACU** | Corta la frase en curso de inmediato (≈10 ms). Igual que `Esc` |
| **Micrófono** | Elige por qué entrada oye HACU. Surte efecto en la siguiente escucha, no a mitad de una |
| **Altavoz** | Elige por dónde habla. El motor cierra su flujo de audio y lo reabre en la tarjeta nueva, así que surte efecto en la siguiente frase |
| **Buscar dispositivos** | Vuelve a inventariar el audio. Para cuando se conecta una diadema con HACU ya arrancado |

Los dos desplegables ofrecen siempre **Predeterminado**, que es dejar elegir al
sistema, y solo listan lo que sirve: un altavoz no aparece como micrófono. El
nombre completo de cada tarjeta está en el *tooltip* de su línea, porque el panel
es estrecho y los nombres de PortAudio son largos. Elegir a mano deja constancia
en la transcripción, de modo que al revisar una sesión rara se sabe por qué
dispositivo entraba y salía el audio.

Esto sustituye a tener que averiguar el índice con `python -m hacu.voz` y
relanzar con `HACU_ENTRADA`/`HACU_SALIDA`. Esas dos variables siguen valiendo
para fijar el arranque; el panel las pisa en caliente.

### Memoria

| Mando | Qué hace |
|---|---|
| **Copiar la conversación** | Abre la conversación entera en texto plano, ya seleccionada: `Ctrl+C` y listo. También la guarda como `.txt`. Evita tener que capturar la pantalla mensaje a mensaje |
| **Ver lo que recuerda** | Los hechos del perfil activo, tal cual están guardados |
| **Limpiar la pantalla** | Borra las burbujas y el chat inmediato; conserva los hechos |
| **Borrar TODO** | Purga la base entera, con confirmación. No se puede deshacer |

---

## Qué pasa al arrancar

1. Se monta la ventana y se aplican los estilos.
2. Se anota cualquier problema de la capa de voz (sin micrófono, motor lento…).
3. Se recuerdan las dos cosas que el operador necesita: la barra espaciadora y
   `Ctrl+Q`.
4. **HACU saluda**: aparece su primera burbuja y, si hay altavoz, la dice en voz
   alta.

El saludo es texto fijo, no una respuesta del modelo, pero se registra como turno
suyo para que la respuesta del visitante llegue con la pregunta delante. Ver
[configuracion.md](configuracion.md#el-saludo-de-apertura).

---

## Cómo se verifica sin mirarla

El bloque `interfaz` de la suite monta la ventana de verdad contra un modelo
falso, en el backend `offscreen` de Qt: corre en cualquier máquina, sin tarjeta
gráfica y sin GPU.

```powershell
python -m pruebas.test_unidades interfaz
```

No comprueba que sea bonita —eso se mira— sino que se monta, que el turno llega al
hilo correcto, que el texto que se pinta es el mismo que se habla, que los atajos
existen, que el foco del teclado se comporta, que la ventana abre saludando y que
la transcripción contiene lo que se vio en pantalla.

La transcripción se arma del hilo de burbujas y no de la base de datos, a
propósito: la base guarda solo los últimos mensajes y solo los del perfil activo,
y lo que interesa copiar es la sesión entera, con los cambios de visitante y los
avisos del sistema incluidos.

---

## Los archivos

| Archivo | Responsabilidad |
|---|---|
| `ventana.py` | La ventana: montaje, estado, turnos, mandos |
| `widgets.py` | Núcleo animado, medidor de nivel, botón de hablar, burbujas, métricas |
| `hilos.py` | Turnos, transcripción y tareas largas fuera del hilo de Qt |
| `estilos.py` | Paleta, hoja de estilos y rótulos de estado |

La ventana no sabe nada de llama.cpp, de ChromaDB ni de PortAudio: habla con
`HacuSession` y con `ServicioDeVoz`, y todo lo lento va a un hilo.
