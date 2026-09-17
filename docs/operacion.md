# El día de la exhibición

Guion de operación: qué hacer antes de abrir, durante la jornada y al cerrar.

---

## La noche anterior

```powershell
cd D:\Proyectos\voz-rag-universidad
.\venv\Scripts\Activate.ps1

python -m pruebas.test_unidades        # 383 en verde
python -m pruebas.recuperacion         # 36/36
python -m pruebas.conversacion         # la visita completa, contra el modelo real
```

Si la conversación falla en algún turno, hay tiempo de mirarlo. A las nueve de la
mañana con público delante, no.

Comprobar que el modelo está donde se espera y que la GPU lo acepta:

```powershell
python -c "from llama_cpp import llama_supports_gpu_offload as g; print('GPU:', g())"
```

---

## Al llegar a la sala, antes de levantar el modelo

**Primero el audio.** Es lo que más falla y lo más rápido de diagnosticar.

```powershell
python -m hacu.voz                     # ¿qué micrófono y qué altavoz hay?
python -m hacu.voz --probar            # graba 3 s y los reproduce
python -m hacu.voz --hablar "Hola, soy Hacu. Bienvenido a AudacIA."
```

En una portátil con webcam, base de conexiones y auriculares llega a haber cinco
entradas. Si el sistema no elige la correcta, se fija por índice:

```powershell
$env:HACU_ENTRADA="3"
$env:HACU_SALIDA="5"
```

Si el arranque avisa de que el motor de síntesis es el lento (`piper-externo`),
hay pausas de segundos en cada punto:

```powershell
python -m hacu.voz --descargar
```

---

## Arrancar

```powershell
python run_hacu.py --ui --voz --pantalla-completa
```

El arranque tarda: carga el modelo en la VRAM, sincroniza el corpus y precalienta.
Termina en `✅ Sistema listo`, HACU saluda y queda a la espera.

Si algo no arranca, hay tres escalones de respaldo, cada uno más pequeño:

| Problema | Respaldo |
|---|---|
| Falla el micrófono | `python run_hacu.py --ui --voz-salida` — HACU habla, el operador escribe |
| Falla todo el audio | `python run_hacu.py --ui` — solo texto, la ventana funciona igual |
| Falla la ventana | `python run_hacu.py` — la consola hace lo mismo |
| Falla la GPU | Arranca en CPU: un turno tarda minutos. **No sirve**; mejor cancelar la demostración que enseñar eso |

---

## Durante la jornada

### Entre visitante y visitante

**Nuevo visitante** en el panel (`F9` si está oculto). Cierra el perfil, limpia la
pantalla y vuelve a saludar. Sin eso, el siguiente hereda el nombre y los hechos
del anterior.

Si el visitante habla por micrófono, HACU lo hace solo: nota el cambio de timbre y
cierra el perfil sin que nadie pulse nada.

### Ajustar al público

El desplegable **Audiencia** cambia el registro sin cambiar el contenido:
*General* por defecto, *Infantil* con un colegio, *Técnico* con profesores o
ingenieros, *Artístico* con gente de humanidades.

**Modo trivia** convierte a HACU en el que pregunta. Funciona muy bien con grupos
de niños y con colas.

### Cuando se pone pesado

`Esc` o **Callar a HACU** corta la frase de inmediato. También corta volver a
pulsar la barra espaciadora: el visitante que interrumpe no tiene que esperar.

### Cuando dice algo raro

HACU está construido para admitir que no sabe algo, y las pruebas lo verifican.
Si aun así afirma algo que no está en el corpus, es un fallo que merece anotarse:
la pregunta exacta y lo que respondió. Queda en `logs/hacu.log` con marca de
tiempo.

Lo que **no** hay que hacer es discutir con él delante del público. **Nuevo
visitante** y seguir.

### Si se queda colgado

`Ctrl+Q` en la ventana, o `Ctrl+C` en la terminal. Cierra bien: drena la cola de
memoria, cierra la base y suelta la VRAM. Volver a arrancar tarda menos que la
primera vez —el corpus ya está indexado— pero sigue siendo un minuto largo.

---

## Al cerrar

La transcripción de las conversaciones se borra sola al arrancar cuando supera las
`retencion_horas` (24 por defecto). Los hechos de cada perfil se conservan.

Si la política del centro es **no conservar nada** de la jornada:

```powershell
python -m herramientas.limpiar --aplicar --memoria
```

O desde el panel, **Borrar TODO**.

Ver [datos.md](datos.md) antes de decidir. Es una decisión de la universidad, no
del operador.

---

## Chuleta

| Situación | Tecla o mando |
|---|---|
| Hablarle | Mantener **Espacio** |
| Callarlo | **Esc** |
| Ocultar el panel del operador | **F9** |
| Entrar o salir de pantalla completa | **F11** |
| Siguiente visitante | Botón **Nuevo visitante** |
| Cerrar | **Ctrl+Q** |
| Ver lo que recuerda de alguien | Botón **Ver lo que recuerda** |
| Borrar todo | Botón **Borrar TODO** |
