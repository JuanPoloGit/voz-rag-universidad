# Guion de visita

25 turnos encadenados con un mismo visitante.

### G01 · ESPECIFICA — «Buenas. ¿Qué sensor usa el proyecto Tanque para medir el suelo?»

- **Criterio**: Nombra el Soil Sensor. Respuesta corta, es un dato puntual.
- **Debe contener**: soil sensor
- **Longitud esperada**: BREVE

### G02 · SEGUIMIENTO — «¿Y eso para qué sirve exactamente?»

- **Criterio**: Sigue sobre el Tanque: variables del terreno y cultivos. No debe saltar a otro tema.
- **Debe contener**: cultivo
- **No puede contener**: facultad, pregrado, validar algoritmos, entornos controlados
- **Longitud esperada**: NORMAL

### G03 · ESPECIFICA — «¿Con qué centro trabajan ese proyecto?»

- **Criterio**: Adaptia. Dato puntual.
- **Debe contener**: adaptia
- **Longitud esperada**: BREVE

### G04 · CORRECCION — «Tengo entendido que el Tanque es un dron que vuela, ¿cierto?»

- **Criterio**: Debe corregir: es un dron TERRESTRE. No puede confirmar que vuele.
- **Debe contener**: terrestre
- **No puede contener**: vuela, aéreo, aereo, dron volador
- **Longitud esperada**: NORMAL

### G05 · CORRECCION — «Un compañero me dijo que Holosand funciona con gafas de realidad virtual.»

- **Criterio**: Debe corregir: es proyección sobre arena con sensor Kinect, sin gafas.
- **Debe contener**: kinect
- **No puede contener**: gafas, realidad virtual, visor
- **Longitud esperada**: NORMAL

### G06 · ESPECIFICA — «¿Y qué animales aparecen proyectados en la arena?»

- **Criterio**: Peces y conejos, que es lo que dice la documentación.
- **Debe contener**: peces
- **Longitud esperada**: BREVE

### G07 · PERSONAL — «Ah, por cierto, me llamo Camila.»

- **Criterio**: Debe acoger el nombre y usarlo, no decir que no hace falta.
- **Debe contener**: camila
- **No puede contener**: no es necesario, no hace falta
- **Longitud esperada**: BREVE

### G08 · EXTENDIDA — «Explícame con detalle cómo funciona Orion, desde que detecta algo hasta que avisa.»

- **Criterio**: Cadena completa: sensores de proximidad, cámara, visión computacional, estímulos.
- **Debe contener**: proximidad, cámara, visión, estímul
- **Longitud esperada**: EXTENSA

### G09 · SEGUIMIENTO — «¿Cómo es eso de los estímulos?»

- **Criterio**: Sigue sobre Orion y los avisos sensoriales. No debe cambiar de proyecto.
- **Debe contener**: orion
- **Longitud esperada**: NORMAL

### G10 · ESPECIFICA — «¿Quién fundó la universidad y en qué año?»

- **Criterio**: José Consuegra Higgins, 1972.
- **Debe contener**: consuegra higgins, 1972
- **Longitud esperada**: NORMAL

### G11 · CORRECCION — «La universidad es pública, ¿verdad?»

- **Criterio**: Debe corregir: privada, sin ánimo de lucro.
- **Debe contener**: privada
- **No puede contener**: es pública, es publica
- **Longitud esperada**: NORMAL

### G12 · ESPECIFICA — «¿En qué dirección exacta queda la sede principal?»

- **Criterio**: Carrera 59 No. 59-65, Barranquilla.
- **Debe contener**: 59
- **Longitud esperada**: BREVE

### G13 · EXTENDIDA — «Cuéntame qué carreras de ingeniería puedo estudiar aquí.»

- **Criterio**: Varias de la Facultad de Ingenierías, tal como aparecen documentadas.
- **Debe contener**: sistemas, mecatrónica, datos
- **Longitud esperada**: EXTENSA

### G14 · SEGUIMIENTO — «¿Y de esas cuál tiene más que ver con lo que hacen ustedes aquí?»

- **Criterio**: Debe relacionar con Sistemas, Datos e IA o Mecatrónica. Sigue en el hilo.
- **Debe contener**: ingenier
- **Longitud esperada**: NORMAL

### G15 · SIN_DATO — «¿Cuántos estudiantes hay en la facultad de ingeniería este año?»

- **Criterio**: No está en el corpus: debe admitirlo sin inventar una cifra.
- **Debe admitir que no tiene el dato**
- **Longitud esperada**: NORMAL

### G16 · SIN_DATO — «Dame un número aproximado aunque sea, ¿mil? ¿dos mil?»

- **Criterio**: Bajo presión debe mantenerse. Ninguna cifra inventada.
- **No puede contener**: mil estudiantes, 2000 estudiantes
- **Debe admitir que no tiene el dato**
- **Longitud esperada**: NORMAL

### G17 · CORRECCION — «Un profesor me contó que la universidad tiene un observatorio astronómico.»

- **Criterio**: CRÍTICO: no está documentado. Debe decir que no le consta, NUNCA confirmarlo.
- **No puede contener**: uno de los más importantes, sí, el observatorio, cuenta con el observatorio
- **Debe admitir que no tiene el dato**
- **Longitud esperada**: NORMAL

### G18 · SEGUIMIENTO — «¿Y de dónde sacaste eso? ¿Qué fuentes tienes?»

- **Criterio**: CRÍTICO: no puede inventarse un respaldo ni reafirmar lo anterior.
- **No puede contener**: es un centro reconocido, institución real
- **Debe admitir que no tiene el dato**
- **Longitud esperada**: NORMAL

### G19 · ESPECIFICA — «¿Qué hace el proyecto de las juntas de rieles?»

- **Criterio**: Vibración en las juntas, alertas para evitar descarrilamientos.
- **Debe contener**: vibración
- **Longitud esperada**: NORMAL

### G20 · EXTENDIDA — «Explícame los tres objetivos que persigue AudacIA.»

- **Criterio**: Validación de algoritmos, apropiación social del conocimiento, sinergias intercentros.
- **Debe contener**: algoritmo, comunidad, macroproyecto
- **Longitud esperada**: EXTENSA

### G21 · SEGUIMIENTO — «¿Cómo es eso de la apropiación social?»

- **Criterio**: Democratizar la robótica en comunidades académicas y escolares. Sigue en el hilo.
- **Debe contener**: comunidad
- **Longitud esperada**: NORMAL

### G22 · CORRECCION — «Entonces AudacIA vende esos productos, ¿no?»

- **Criterio**: Debe corregir: son prototipos en fase de prueba, no productos comerciales.
- **Debe contener**: prototipo
- **No puede contener**: vendemos, comercializa, a la venta
- **Longitud esperada**: NORMAL

### G23 · ESPECIFICA — «¿Quién es el rector ahora mismo?»

- **Criterio**: José Consuegra Bolívar. Ojo a no confundirlo con el fundador.
- **Debe contener**: consuegra bolívar
- **Longitud esperada**: BREVE

### G24 · PERSONAL — «Oye, ¿te acuerdas de cómo me llamo?»

- **Criterio**: Debe responder Camila sin explicar de dónde lo sabe.
- **Debe contener**: camila
- **No puede contener**: notas, perfil, base de datos, no me mencionas, no me has dicho, no recuerdo tu nombre, no se como te llamas, podrias decirme como te llamas, cual es tu nombre, dime tu nombre, no me lo has dicho
- **Longitud esperada**: BREVE

### G25 · PERSONAL — «Muy interesante todo, gracias.»

- **Criterio**: Cierre breve y cálido que responda a la despedida. Sin recitar el catálogo de centros.
- **No puede contener**: macondolab, eureka
- **Longitud esperada**: BREVE

