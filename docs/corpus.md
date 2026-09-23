# El corpus: lo que HACU sabe

Todo lo que HACU afirma sobre AudacIA y sobre la Universidad Simón Bolívar sale de
`documents/`. Si algo no está ahí, HACU tiene que admitir que no lo sabe — y las
pruebas comprueban que lo hace.

---

## Los dos niveles

Con 32 proyectos, una sola forma de guardar la información no sirve para todas las
preguntas. El corpus está en dos niveles:

| Archivo | Nivel | Para qué |
|---|---|---|
| `audacia_indice_proyectos.md` | índice | los 32 proyectos a una línea; sirve para **enumerar** |
| `audacia_proyectos_salud.md` | fichas | una sección `##` por proyecto, con su detalle |
| `audacia_proyectos_ambiente.md` | fichas | medio ambiente, agro e industria |
| `audacia_proyectos_cultura.md` | fichas | cultura, arte y turismo |
| `audacia_proyectos_hardware.md` | fichas | hardware, mecatrónica y robótica |
| `audacia_proyectos_educacion.md` | fichas | educación y EdTech |
| `audacia_proyectos_energia.md` | fichas | energía, servicios públicos y programas estratégicos |
| `audacia_proyectos_didacticos.md` | fichas | proyectos didácticos y experimentales |
| `audacia_centro.md` | institucional | personas, sedes, patentes, publicaciones, reconocimientos |
| `audacia_centros_hermanos.md` | institucional | **escrito a mano**: MacondoLab y CICV, las unidades que NO son AudacIA |
| `universidad_simon_bolivar.md` | institucional | historia, programas, ecosistema Eureka |

**El nombre del archivo decide el corpus**: los que contienen «audacia» van a la
colección de AudacIA; el resto, a la institucional. No hay configuración que
mantener sincronizada.

La indexación respeta los encabezados `##`: cada ficha entra **entera** en un
fragmento y no se parte por la mitad. Cada fragmento lleva su tipo (`indice`,
`ficha`, `institucional`), y eso es lo que permite pedirle al índice que enumere y
a las fichas que expliquen. Ver
[arquitectura.md](arquitectura.md#profundidad-la-pregunta-decide-cuánto-se-trae).

---

## El corpus no se escribe a mano

Se genera desde los informes en Word, de modo que actualizar la documentación no
obliga a transcribir nada y no se pueden colar datos inventados.

```powershell
# 1. Extraer el texto de los .docx a un JSON intermedio
python herramientas/extraer.py <carpeta con los .docx> fuente.json

# 2. Construir el corpus en dos niveles
python herramientas/construir_corpus.py fuente.json documents/ [corpus_anterior.md]

# 3. MEDIR. Siempre. Antes de comitear nada.
python -m pruebas.recuperacion --n 6 --detalle
```

`herramientas/breves.py` es lo único escrito a mano **dentro del generador**: el
resumen de una línea de cada proyecto para el índice-catálogo. Es compresión
editorial de las descripciones del propio informe, y existe porque el índice
tiene que caber entero en el contexto para poder enumerar los 32 proyectos de una
vez.

### Documentos escritos a mano junto a los generados

`construir_corpus.py` escribe solo los archivos que él nombra y después lista la
carpeta, así que **un `.md` con nombre propio sobrevive a la regeneración**. Es la
vía para lo que no está en los informes en Word.

`audacia_centros_hermanos.md` es el único hoy. Existe porque MacondoLab y el CICV
se nombraban en el corpus sin tener nada que los explicara, y HACU contestaba,
con razón, que no tenía el dato —falló en las tres corridas de 100 turnos—. El
prefijo `audacia_` no es decorativo: es lo que manda el archivo a la colección de
AudacIA, que es la que consulta una pregunta como «pero MacondoLab es parte de
AudacIA, ¿no?».

Tres reglas para añadir otro:

1. **Prefijo `audacia_`** si debe responder a preguntas sobre el centro; sin él va
   a la colección institucional.
2. **Una sección `##` por tema**, por debajo de `chunk_max_seccion` (2 200
   caracteres) para que entre entera en un fragmento, y con el **título igual al
   nombre de lo que explica**: a igualdad de palabras compartidas, el rescate
   léxico prefiere la pieza cuyo título lleva la palabra.
3. **Sin URLs ni notas para el lector dentro del texto.** Un modelo de 8B recita
   literalmente lo que se le pone delante: se ha observado cuatro veces. Las
   fuentes van en el commit, no en el corpus.

Y medir siempre después: `python -m pruebas.recuperacion --n 6 --detalle`.

### El tercer argumento, y por qué avisa

El detalle técnico de los **proyectos didácticos** nunca estuvo en los informes en
Word: se escribió a mano en el corpus anterior (`documents/audacia_unisimón.md`,
hoy borrado del árbol). Sin ese archivo el corpus se regenera igual, pero las
fichas didácticas salen más pobres que las que hay ahora en disco, y el fallo es
silencioso: los archivos aparecen, solo que con menos dentro. Por eso el generador
**avisa a gritos** cuando falta.

Si el archivo estuvo alguna vez en el repositorio, se recupera sin resucitarlo:

```powershell
git log --oneline --all -- "documents/audacia_unisimón.md"
git show <commit>:"documents/audacia_unisimón.md" > audacia_anterior.md
python herramientas/construir_corpus.py fuente.json documents/ audacia_anterior.md
```

---

## Después de cambiar el corpus

1. **Reindexar** — no hay que hacer nada: `bootstrap.py` sincroniza al arrancar.
   Los fragmentos de documentos que ya no están en la carpeta se eliminan del
   índice, así que borrar un archivo lo borra también del conocimiento de HACU.
2. **Medir la recuperación**:

   ```powershell
   python -m pruebas.recuperacion --n 6 --detalle
   ```

   La referencia actual es **39/39** con el embedding multilingüe (38/39 con el de
   Chroma por defecto). Una consulta que baja es un proyecto que HACU dejará de
   encontrar.
3. **Comprobar que el índice sigue completo**: `pruebas/recuperacion.py` verifica
   que el índice-catálogo entrega los 32 proyectos. Si se añade uno al informe y
   no a `breves.py`, esa comprobación lo caza.
4. **Correr el guion**:

   ```powershell
   python -m pruebas.conversacion
   ```

   Varios turnos citan datos concretos del corpus; si un dato se movió de sitio, se
   ve aquí.

Si el índice se queda en un estado raro (por ejemplo tras usar el mismo `chroma_db`
desde Windows y desde el contenedor):

```powershell
python -m herramientas.limpiar --aplicar --indice
```

Se reconstruye entero en el siguiente arranque, un par de minutos.

---

## Advertencias de seguridad de cada montaje

HACU tiene una regla de prudencia física: nunca invita a tocar, manipular, abrir,
conectar ni llevarse nada a la boca, y ante la duda remite a quien atiende el
stand. Eso es **conducta**, y vive en el system prompt.

Lo que **no** hace, y no debe hacer, es inventarse riesgos concretos ni
tranquilizar sobre lo que no le consta: decir que una arena es atóxica o que un
montaje es apto para niños sin tenerlo documentado es exactamente la clase de
invención que el resto del sistema existe para evitar.

Si quieres que avise de algo específico —«no toques los circuitos del Tanque»,
«la arena de Holosand no se come»— tiene que estar **en el corpus**, y solo lo
puede escribir quien conoce el montaje. La vía es una línea más en la ficha del
proyecto, dentro del informe en Word del que se genera el corpus:

```
* **Seguridad:** <qué no se debe hacer y por qué, en una frase>
```

También valen `Precaución:`, `Advertencia:` y `Cuidado:`.

Esa línea **no se queda en el corpus esperando a que el modelo se acuerde**. Al
recuperar la ficha, `hacu/context.py` la extrae y la pone en las notas del turno
como instrucción explícita: dila, con naturalidad, sin añadir ninguna que no esté
y sin decir que algo es seguro. Con seis fragmentos delante, un 8B se queda con lo
vistoso, y la línea de seguridad es justo la que menos luce.

Mientras nadie escriba ninguna, el mecanismo no hace nada: no hay advertencias
inventadas por defecto.

Al regenerar, esa línea entra en la ficha y HACU la recupera con el resto del
proyecto. Después, medir:

```powershell
python -m pruebas.recuperacion --n 6 --detalle
```

---

## Escribir a mano, si hace falta

Nada impide añadir un `.md` directamente a `documents/`. Las reglas:

- **El nombre decide el corpus**: con «audacia» dentro va a AudacIA; si no, a la
  colección institucional.
- **Una sección `##` por unidad de conocimiento.** Es la unidad de recuperación:
  todo lo que tiene que llegar junto al modelo va bajo el mismo encabezado.
- **Ninguna sección de más de ~2 200 caracteres** (`chunk_max_seccion`) si se
  quiere que entre entera en un fragmento.
- **Sin frases de andamiaje.** Lo que se escriba en el corpus puede acabar
  recitado literalmente por el modelo.
- Después: medir con `pruebas/recuperacion.py`.

Lo que se añada a mano se pierde en la siguiente regeneración desde los `.docx`.
Si un dato tiene que sobrevivir, tiene que estar en el informe o en `breves.py`.
