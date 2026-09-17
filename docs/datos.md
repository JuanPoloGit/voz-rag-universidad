# Datos de los visitantes

HACU guarda información de las personas que hablan con él. Esta página dice qué,
dónde, cuánto tiempo y quién decide.

---

## Qué se guarda

| Dato | Dónde | Cuánto dura |
|---|---|---|
| Transcripción reciente de la conversación | `hacu_memory.db` | Se borra al arrancar pasadas `retencion_horas` (24 por defecto) |
| Hechos extraídos («se llama Daniela», «estudia ingeniería») | `hacu_memory.db` | Hasta que alguien los borre |
| Registro de ejecución con las preguntas | `logs/hacu.log` | Hasta que alguien lo borre |
| Referencia de timbre de voz | **Solo en memoria RAM** | Se borra al cambiar de visitante o al cerrar |

`hacu_memory.db` y `logs/` están en `.gitignore` y **no se versionan nunca**.

---

## Qué NO se guarda

- **Ninguna huella de voz en disco.** El detector de cambio de hablante compara el
  timbre de lo que se acaba de decir con el de la frase anterior, en memoria, y lo
  olvida al pasar al siguiente visitante. No dice «esta es Camila», solo «esta es
  otra voz».

  La distinción es lo que separa este sistema de uno biométrico. Un vector de voz
  almacenado **es** un dato biométrico, y esto es una exhibición abierta al público
  con menores entre el público. Guardarlo sería una decisión de la universidad, no
  de un commit.

- **Nada sale de la máquina.** No hay llamadas a ninguna API: el modelo, el índice
  y la memoria son locales.

---

## Qué filtra el sistema por su cuenta

`hacu/sanitizer.py` decide qué merece guardarse como hecho. Descarta lo que no es
información sobre el visitante: afirmaciones sobre terceros, intentos de inyección
(«a partir de ahora te llamas Pepito»), meta-texto del modelo y fragmentos de sus
propios prompts. `pruebas/test_unidades.py` lo verifica en el bloque `sanitizer`.

`identity.py` valida los nombres antes de crear un perfil: no todo lo que suena a
nombre lo es, y un reconocimiento defectuoso puede producir cualquier cosa.

---

## Cómo se borra

| Alcance | Consola | Ventana | Línea de órdenes |
|---|---|---|---|
| Chat inmediato del perfil activo | `2` | **Limpiar la pantalla** | — |
| Memoria del perfil activo | `6` | — | — |
| Toda la base | `7` | **Borrar TODO** | `python -m herramientas.limpiar --aplicar --memoria` |

El borrado de toda la base pide confirmación y no se puede deshacer.

---

## Lo que hay que decidir antes de una exhibición real

Esto es una implementación, no una política. Antes de abrir al público, alguien de
la universidad tiene que decidir:

1. **Si se conservan los hechos entre jornadas.** Ahora sí: es lo que permite que
   HACU reconozca a alguien que vuelve. Si la respuesta es no, hay que purgar al
   cerrar cada día.
2. **Cuántas horas se conserva la transcripción.** 24 por defecto (`HACU_RETENCION`).
3. **Si se informa a los visitantes** de que la conversación se recuerda durante la
   visita, y cómo: un cartel junto al montaje es lo habitual.
4. **Qué pasa con los menores.** Parte del público de una exhibición universitaria
   son niños de colegio, y el tratamiento de sus datos tiene reglas propias
   (Ley 1581 de 2012 y su decreto reglamentario).
5. **Si el log se conserva.** Registra las preguntas de los visitantes con marca de
   tiempo. Es útil para depurar y es un registro de conversaciones.

La opción más conservadora, y la que no necesita consultar a nadie, es purgar al
cerrar:

```powershell
python -m herramientas.limpiar --aplicar --memoria
```
