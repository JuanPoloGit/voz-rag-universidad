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

`herramientas/breves.py` es **lo único escrito a mano**: el resumen de una línea de
cada proyecto para el índice-catálogo. Es compresión editorial de las
descripciones del propio informe, y existe porque el índice tiene que caber entero
en el contexto para poder enumerar los 32 proyectos de una vez.

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

   La referencia actual es **36/36** con el embedding multilingüe (27/36 con el de
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
