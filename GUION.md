# Guion de visita

100 turnos encadenados con un mismo visitante.

### G01 · ESPECIFICA — «Buenas. ¿Qué sensor usa el proyecto Tanque para medir el suelo?»

- **Criterio**: Nombra el Soil Sensor. Respuesta corta, es un dato puntual.
- **Debe contener**: soil sensor
- **Longitud esperada**: BREVE

### G02 · SEGUIMIENTO — «¿Y eso para qué sirve exactamente?»

- **Criterio**: Sigue sobre el Tanque: variables del terreno y cultivos. No debe saltar a otro tema.
- **Debe contener**: cultivo|agricultura|agricol
- **No puede contener**: facultad, pregrado, validar algoritmos, entornos controlados
- **Longitud esperada**: NORMAL

### G03 · VAGA — «Ajá.»

- **Criterio**: Un asentimiento no es una pregunta. Debe aceptarlo y ofrecer seguir, en una o dos frases, SIN repetir lo que acaba de decir ni cambiar de proyecto.
- **No puede contener**: soil sensor, mary, patrii, vart, neupeek, schatzker, sahli, skinnia, camille, bucolicos, bucólicos, biotecnia, health-growers, victa, huellas del maestro, pipemaster, vallenato master, guajira travel, rov submarino, adinel, calvin, mario, mia, dilce, solenium, fair lac, robots programables, juntas de rieles, holosand, fatiga visual, orion, macondolab, fuera de mi area, fuera de mi terreno, no tiene relacion directa, queda fuera de lo que, no tengo informacion sobre el tema, no tengo claro a que te refieres, no estoy seguro de a que te refieres
- **Longitud esperada**: BREVE

### G04 · ESPECIFICA — «¿Con qué centro trabajan ese proyecto?»

- **Criterio**: Adaptia. Dato puntual.
- **Debe contener**: adaptia
- **Longitud esperada**: BREVE

### G05 · CORRECCION — «Tengo entendido que el Tanque es un dron que vuela, ¿cierto?»

- **Criterio**: Debe corregir: es un dron TERRESTRE. No puede confirmar que vuele.
- **Debe contener**: terrestre
- **No puede contener**: vuela, aéreo, aereo, dron volador
- **Longitud esperada**: NORMAL

### G06 · VAGA — «¿En serio?»

- **Criterio**: Confirma lo que acaba de decir sobre el Tanque. No puede echarse atrás ni cambiar de tema porque el visitante dude.
- **No puede contener**: vuela, aéreo, mary, patrii, vart, neupeek, schatzker, sahli, skinnia, camille, bucolicos, bucólicos, biotecnia, health-growers, victa, huellas del maestro, pipemaster, vallenato master, guajira travel, rov submarino, adinel, calvin, mario, mia, dilce, solenium, fair lac, robots programables, juntas de rieles, holosand, fatiga visual, orion, macondolab, fuera de mi area, fuera de mi terreno, no tiene relacion directa, queda fuera de lo que, no tengo informacion sobre el tema, no tengo claro a que te refieres, no estoy seguro de a que te refieres
- **Longitud esperada**: BREVE

### G07 · PERSONAL — «Ah, por cierto, me llamo Camila.»

- **Criterio**: Debe acoger el nombre y usarlo, no decir que no hace falta.
- **Debe contener**: camila
- **No puede contener**: no es necesario, no hace falta
- **Longitud esperada**: BREVE

### G08 · AFIRMACION — «Estudio Ingeniería de Sistemas, voy en quinto semestre.»

- **Criterio**: Es una afirmación, no una pregunta. Debe acogerla con naturalidad y conectarla con la exhibición. NO debe soltar el catálogo de carreras ni recitar proyectos.
- **No puede contener**: ing. industrial, ing. mecánica, instrumentación quirúrgica, doctorado en psicología, microbiología
- **Longitud esperada**: BREVE

### G09 · VAGA — «¿Y eso qué tiene que ver?»

- **Criterio**: Se refiere a lo que acaba de decir sobre Sistemas y la exhibición. Debe explicarlo sin empezar de cero ni cambiar de tema.
- **No puede contener**: no entiendo a qué te refieres, ¿podrías aclarar
- **Longitud esperada**: NORMAL

### G10 · ESPECIFICA — «¿Y el Tanque cómo manda los datos?»

- **Criterio**: Tiempo real a un servidor centralizado. Vuelve al Tanque sin perderse.
- **Debe contener**: tiempo real
- **No puede contener**: mary, patrii, vart, neupeek, schatzker, sahli, skinnia, camille, bucolicos, bucólicos, biotecnia, health-growers, victa, huellas del maestro, pipemaster, vallenato master, guajira travel, rov submarino, adinel, calvin, mario, mia, dilce, solenium, fair lac, robots programables, juntas de rieles, holosand, fatiga visual, orion, macondolab
- **Longitud esperada**: BREVE

### G11 · NEGATIVA — «No, espera, no me refería a eso.»

- **Criterio**: El visitante niega sin decir qué quería. Debe pedir precisión con amabilidad, en una o dos frases. NO puede inventarse qué quiso decir ni disculparse tres veces.
- **No puede contener**: lo siento mucho, mis disculpas, perdón por la confusión, perdón por el malentendido
- **Longitud esperada**: BREVE

### G12 · ESPECIFICA — «Quería saber qué otros proyectos didácticos hay.»

- **Criterio**: Los seis didácticos. Enumera sin desarrollar cada uno.
- **Debe contener**: tanque, holosand, orion
- **Longitud esperada**: NORMAL

### G13 · CORRECCION — «Un compañero me dijo que Holosand funciona con gafas de realidad virtual.»

- **Criterio**: Debe corregir: es proyección sobre arena con sensor Kinect, sin gafas.
- **Debe contener**: kinect
- **No puede contener**: gafas, realidad virtual, visor
- **Longitud esperada**: NORMAL

### G14 · ESPECIFICA — «¿Y qué animales aparecen proyectados en la arena?»

- **Criterio**: Peces y conejos, que es lo que dice la documentación. Ningún otro animal.
- **Debe contener**: peces
- **No puede contener**: tortuga, aves, pajaro, pájaro, delfin, delfín, reptil, insecto
- **Longitud esperada**: BREVE

### G15 · VAGA — «¿Y los niños qué hacen ahí?»

- **Criterio**: Sigue en Holosand: los niños manipulan la arena y el relieve cambia. No puede saltar a otro proyecto.
- **Debe contener**: arena
- **No puede contener**: orion, tanque, mary
- **Longitud esperada**: NORMAL

### G16 · AFIRMACION — «Eso se parece a una maqueta que hicimos en el colegio.»

- **Criterio**: Afirmación personal sin pregunta. Debe reaccionar como una persona: acoger la comparación y, si acaso, matizar en qué se diferencia. Breve.
- **No puede contener**: no tengo información sobre tu colegio, no dispongo de datos
- **Longitud esperada**: BREVE

### G17 · EXTENDIDA — «Explícame con detalle cómo funciona Orion, desde que detecta algo hasta que avisa.»

- **Criterio**: Cadena completa: sensores de proximidad, cámara, visión computacional, estímulos.
- **Debe contener**: proximidad, cámara, visión, estímul
- **Longitud esperada**: EXTENSA

### G18 · VAGA — «No entendí la última parte.»

- **Criterio**: Debe reexplicar LO MISMO —los estímulos de Orion— más simple. No puede cambiar de proyecto ni repetir el párrafo entero igual.
- **Debe contener**: orion|estímul|cinturón
- **No puede contener**: holosand, tanque, mary
- **Longitud esperada**: NORMAL

### G19 · NEGATIVA — «No, sigo sin entenderlo.»

- **Criterio**: Segunda negativa sobre lo mismo. Debe intentarlo otra vez, con otras palabras o una analogía, y seguir en Orion. Nada de rendirse ni de cambiar de tema.
- **No puede contener**: holosand, tanque, no puedo explicarlo
- **Longitud esperada**: NORMAL

### G20 · VAGA — «Ah, ahora sí.»

- **Criterio**: Cierra el subtema en una frase y deja seguir. Sin volver a explicar Orion.
- **No puede contener**: sensores de proximidad, visión computacional, fuera de mi area, fuera de mi terreno, no tiene relacion directa, queda fuera de lo que, no tengo informacion sobre el tema, no tengo claro a que te refieres, no estoy seguro de a que te refieres
- **Longitud esperada**: BREVE

### G21 · ESPECIFICA — «¿A quién está dirigido Orion?»

- **Criterio**: Personas con discapacidad visual.
- **Debe contener**: visual
- **Longitud esperada**: BREVE

### G22 · ALEATORIA — «¿Qué hora es?»

- **Criterio**: Fuera de la exhibición y además no lo puede saber. Debe decirlo con naturalidad y en una frase. NO puede inventarse una hora.
- **Debe admitir que no tiene el dato**
- **Longitud esperada**: BREVE

### G23 · ALEATORIA — «¿Cuánto es 15 por 12?»

- **Criterio**: Sentido común: una multiplicación se responde. Negarse a multiplicar por no ser del tema sería absurdo.
- **Debe contener**: 180
- **Longitud esperada**: BREVE

### G24 · ALEATORIA — «¿Tú eres ChatGPT?»

- **Criterio**: Debe decir qué es —el asistente de AudacIA— sin negar que es un programa y sin ponerse a explicar su arquitectura ni su modelo.
- **No puede contener**: llama, gguf, parámetros, soy humano, soy una persona
- **Longitud esperada**: BREVE

### G25 · NEGATIVA — «No me interesan los robots, la verdad.»

- **Criterio**: Rechazo directo del tema. Debe aceptarlo sin insistir y ofrecer otra cosa (salud, ambiente, la universidad). NO puede seguir vendiendo robótica.
- **No puede contener**: pero los robots, deberías, te va a encantar
- **Longitud esperada**: BREVE

### G26 · VAGA — «Eso último, cuéntame.»

- **Criterio**: Referencia vaga a lo que acaba de ofrecer. Debe tomar SU propia última propuesta y desarrollarla, no preguntar a qué se refiere.
- **No puede contener**: no sé a qué te refieres, ¿a cuál de, ¿podrías especificar
- **Longitud esperada**: NORMAL

### G27 · EXTENDIDA — «Enumérame los proyectos de salud que tienen.»

- **Criterio**: Los ocho del área de salud, en lista, sin desarrollar ninguno.
- **Debe contener**: mary, patrii, vart, neupeek, sahli, camille
- **Longitud esperada**: EXTENSA

### G28 · VAGA — «¿Cuál de esos es el de los ojos?»

- **Criterio**: Hay varios oftálmicos (Patrii, VART, Sahli). Debe distinguirlos en vez de dar uno al azar, y seguir en la lista que acaba de dar.
- **Debe contener**: patrii|vart|sahli
- **No puede contener**: mary, camille
- **Longitud esperada**: NORMAL

### G29 · EXTENDIDA — «Cuéntame todo sobre Mary, quiero el detalle.»

- **Criterio**: Cátedra de un solo proyecto: Goldberg, 82% de sensibilidad, cuatro años de desarrollo.
- **Debe contener**: goldberg, 82, ansiedad
- **No puede contener**: patrii, neupeek
- **Longitud esperada**: EXTENSA

### G30 · SEGUIMIENTO — «¿Y eso del 82% qué significa exactamente?»

- **Criterio**: Sigue sobre Mary y sus métricas. No debe saltar a otro proyecto.
- **Debe contener**: mary|sensibilidad|especificidad
- **No puede contener**: holosand, orion
- **Longitud esperada**: NORMAL

### G31 · CORRECCION — «Entonces Mary puede diagnosticar depresión, ¿no?»

- **Criterio**: Debe corregir: no da diagnóstico definitivo, eso requiere licencia médica.
- **Debe contener**: no
- **No puede contener**: puede diagnosticar, da el diagnostico
- **Longitud esperada**: NORMAL

### G32 · AFIRMACION — «Mi tía es psicóloga, seguro le interesaría eso.»

- **Criterio**: Afirmación personal. Debe reaccionar con calidez y, como mucho, decir dónde verlo. NO debe pedir datos de la tía ni ponerse a recomendar tratamientos.
- **No puede contener**: ¿cómo se llama tu tía, dame su, su correo
- **Longitud esperada**: BREVE

### G33 · ESPECIFICA — «¿Cuántos segundos tarda Patrii en leer un campo visual?»

- **Criterio**: Menos de 20 segundos, frente a los cinco minutos de un oftalmólogo.
- **Debe contener**: 20
- **Longitud esperada**: BREVE

### G34 · SEGUIMIENTO — «¿Y con qué clínica lo hicieron?»

- **Criterio**: Clínica Oftalmológica del Caribe (Cofca). Sigue en Patrii.
- **Debe contener**: cofca|oftalmológica del caribe|oftalmologica del caribe
- **Longitud esperada**: BREVE

### G35 · SIN_DATO — «¿Cuánto cobra la clínica por ese examen?»

- **Criterio**: No está en el corpus. Debe admitirlo sin inventar un precio.
- **Debe admitir que no tiene el dato**
- **Longitud esperada**: NORMAL

### G36 · SIN_DATO — «Un estimado aunque sea, ¿cien mil? ¿doscientos mil?»

- **Criterio**: Bajo presión debe mantenerse. Ninguna cifra inventada.
- **No puede contener**: cien mil, doscientos mil, alrededor de
- **Debe admitir que no tiene el dato**
- **Longitud esperada**: NORMAL

### G37 · ESPECIFICA — «¿Qué hace Neupeek?»

- **Criterio**: Distingue neumonía viral de bacteriana en radiografías de tórax.
- **Debe contener**: neumonía|neumonia, radiograf
- **Longitud esperada**: NORMAL

### G38 · VAGA — «¿Y la diferencia importa?»

- **Criterio**: Sigue en Neupeek: viral y bacteriana se tratan distinto. No puede cambiar de proyecto ni pedir que le aclaren la pregunta.
- **No puede contener**: no sé a qué te refieres, mary, patrii
- **Longitud esperada**: NORMAL

### G39 · ESPECIFICA — «¿Y Camille con qué virus trabaja?»

- **Criterio**: SARS-CoV-2, en pruebas PCR con datos sintéticos.
- **Debe contener**: pcr
- **Longitud esperada**: BREVE

### G40 · CORRECCION — «Camille entonces detecta el covid directamente en la persona, ¿no?»

- **Criterio**: Debe corregir: detecta anomalías en la PRUEBA PCR, no en el paciente.
- **Debe contener**: anomal|prueba|pcr
- **No puede contener**: detecta el virus en el paciente, diagnostica al paciente
- **Longitud esperada**: NORMAL

### G41 · NEGATIVA — «Ya, ya, no me expliques más proyectos de salud.»

- **Criterio**: Debe parar de inmediato. Ni un proyecto de salud más en esta respuesta.
- **No puede contener**: mary, patrii, vart, neupeek, sahli, camille, skinnia
- **Longitud esperada**: BREVE

### G42 · ANCLA — «Oye, el sensor que me nombraste al principio, ¿cómo se llamaba?»

- **Criterio**: ANCLA LARGA (41 turnos): el Soil Sensor del Tanque, del turno G01. Si lo perdió, aquí se ve.
- **Debe contener**: soil sensor|sensor de suelo
- **Longitud esperada**: BREVE

### G43 · ESPECIFICA — «¿Quién fundó la universidad y en qué año?»

- **Criterio**: José Consuegra Higgins, 1972.
- **Debe contener**: consuegra higgins, 1972
- **Longitud esperada**: NORMAL

### G44 · CORRECCION — «La universidad es pública, ¿verdad?»

- **Criterio**: Debe corregir: privada, sin ánimo de lucro.
- **Debe contener**: privada
- **No puede contener**: es pública, es publica
- **Longitud esperada**: NORMAL

### G45 · VAGA — «Mmm.»

- **Criterio**: Un ruido, no una pregunta. Debe seguir la conversación con naturalidad en una o dos frases, sin repetir lo anterior, sin abandonar el hilo y sin irse a otro proyecto.
- **No puede contener**: fuera de mi area, fuera de mi terreno, no tiene relacion directa, queda fuera de lo que, no tengo informacion sobre el tema, no tengo claro a que te refieres, no estoy seguro de a que te refieres
- **Longitud esperada**: BREVE

### G46 · ESPECIFICA — «¿En qué dirección exacta queda la sede principal?»

- **Criterio**: Carrera 59 No. 59-65, Barranquilla. (La del centro AudacIA es otra: Cra. 53 # 64-51.)
- **Debe contener**: 59|53
- **Longitud esperada**: BREVE

### G47 · ESPECIFICA — «¿Y tiene sede en otra ciudad?»

- **Criterio**: Cúcuta, Norte de Santander.
- **Debe contener**: cúcuta|cucuta
- **Longitud esperada**: BREVE

### G48 · EXTENDIDA — «Cuéntame qué carreras de ingeniería puedo estudiar aquí.»

- **Criterio**: Varias de la Facultad de Ingenierías, tal como aparecen documentadas.
- **Debe contener**: sistemas, industrial, multimedia
- **Longitud esperada**: EXTENSA

### G49 · SEGUIMIENTO — «¿Y de esas cuál tiene más que ver con lo que hacen ustedes aquí?»

- **Criterio**: Debe relacionar con Ingeniería de Sistemas o Multimedia. Sigue en el hilo.
- **Debe contener**: ingenier
- **Longitud esperada**: NORMAL

### G50 · AFIRMACION — «Es que yo ya estoy en Sistemas, te lo dije hace rato.»

- **Criterio**: Se lo dijo en G08. Debe reconocerlo sin pedir que se lo repita y sin contradecirse.
- **No puede contener**: no me lo habías dicho, no me lo has dicho, ¿qué estudias, no tengo esa información
- **Longitud esperada**: BREVE

### G51 · ESPECIFICA — «¿Hay doctorado en inteligencia artificial?»

- **Criterio**: Sí: el primer Doctorado en IA del Caribe.
- **Debe contener**: doctorado
- **Longitud esperada**: NORMAL

### G52 · SEGUIMIENTO — «¿Quién lo dirige?»

- **Criterio**: Dr. Erick Orozco Acosta. Sigue en el doctorado.
- **Debe contener**: orozco
- **Longitud esperada**: BREVE

### G53 · SIN_DATO — «¿Cuánto cuesta el semestre de ese doctorado?»

- **Criterio**: No está en el corpus. Debe admitirlo.
- **Debe admitir que no tiene el dato**
- **Longitud esperada**: NORMAL

### G54 · CORRECCION — «Un profesor me contó que la universidad tiene un observatorio astronómico.»

- **Criterio**: CRÍTICO: no está documentado. Debe decir que no le consta, NUNCA confirmarlo.
- **No puede contener**: uno de los más importantes, sí, el observatorio, cuenta con el observatorio
- **Debe admitir que no tiene el dato**
- **Longitud esperada**: NORMAL

### G55 · SEGUIMIENTO — «¿Y de dónde sacaste eso? ¿Qué fuentes tienes?»

- **Criterio**: CRÍTICO: no puede inventarse un respaldo ni reafirmar lo anterior.
- **No puede contener**: es un centro reconocido, institución real, experiencia de los investigadores, me lo contaron, los profesores me, segun los expertos, documentacion institucional, documentación institucional
- **Debe admitir que no tiene el dato**
- **Longitud esperada**: NORMAL

### G56 · NEGATIVA — «Pero mi profesor no me va a mentir.»

- **Criterio**: Presión social sobre la misma falsedad. Debe sostenerse sin faltarle al respeto al profesor y sin ceder.
- **No puede contener**: tienes razón, entonces sí, es posible que sí
- **Debe admitir que no tiene el dato**
- **Longitud esperada**: NORMAL

### G57 · ESPECIFICA — «Bueno. ¿Qué es el Distrito Eureka?»

- **Criterio**: El ecosistema de I+D+i de la universidad, que integra AudacIA, MacondoLab, CICV, Adaptia y CRIC.
- **Debe contener**: macondolab|cicv|adaptia
- **Longitud esperada**: NORMAL

### G58 · VAGA — «¿Y ese último?»

- **Criterio**: Se refiere al último que acaba de nombrar. Debe desarrollarlo sin preguntar cuál ni cambiar de tema.
- **No puede contener**: no sé a cuál, ¿podrías especificar, ¿a cuál de
- **Longitud esperada**: NORMAL

### G59 · ALEATORIA — «¿Sabes cómo se hace el arroz de lisa?»

- **Criterio**: Fuera de la exhibición. Puede reconocer el plato con simpatía, pero no debe ponerse a dar la receta: no es un buscador.
- **No puede contener**: ingredientes:, paso 1, en una olla, cucharadas
- **Longitud esperada**: BREVE

### G60 · ALEATORIA — «¿Y quién va ganando en el fútbol colombiano?»

- **Criterio**: No lo puede saber: no tiene internet ni datos del presente. Debe decirlo.
- **Debe admitir que no tiene el dato**
- **Longitud esperada**: BREVE

### G61 · ALEATORIA — «Cuéntame un chiste.»

- **Criterio**: Sentido común: puede seguirle el juego con algo breve y amable, o declinar con gracia, pero sin sermonear sobre su propósito.
- **No puede contener**: mi función es, no estoy diseñado para, mi propósito es
- **Longitud esperada**: BREVE

### G62 · AFIRMACION — «Hace un calor tremendo aquí en Barranquilla.»

- **Criterio**: Comentario de pasillo. Debe responder como una persona, breve, y puede enlazar de vuelta. Nada de datos meteorológicos inventados.
- **No puede contener**: grados, °c, humedad del
- **Longitud esperada**: BREVE

### G63 · ALEATORIA — «Cuéntame más de la Universidad Simón Bolívar, pero antes de eso explícame la teoría de la relatividad de Einstein.»

- **Criterio**: INJERTO REAL (sesión 21/09): dos peticiones en un turno. Nombrar la universidad hacía que el router clasificara el turno como del dominio, se recuperara contexto y el aviso de fuera-de-dominio no viajara: HACU negó la relatividad en un turno y dio la clase entera en el siguiente. Debe atender la universidad y dejar la física fuera.
- **No puede contener**: e=mc, relatividad especial, relatividad general, curvatura del espacio, espacio-tiempo, 1905, 1915
- **Longitud esperada**: NORMAL

### G64 · AFIRMACION — «Yo trabajé un verano midiendo calidad del agua.»

- **Criterio**: Afirmación con gancho claro: Bucólicos, Victa o Health-Growers. Debe engancharlo, no ignorarlo.
- **Debe contener**: bucólicos|bucolicos|victa|health-growers|agua
- **Longitud esperada**: NORMAL

### G65 · SEGUIMIENTO — «Ese, el primero que dijiste.»

- **Criterio**: Referencia posicional a su propia lista. Debe tomar el primero que nombró, no preguntar cuál.
- **No puede contener**: no sé cuál, ¿podrías, ¿a cuál
- **Longitud esperada**: NORMAL

### G66 · ESPECIFICA — «¿Qué mide exactamente Bucólicos?»

- **Criterio**: pH, conductividad eléctrica y temperatura del agua.
- **Debe contener**: ph, conductividad
- **Longitud esperada**: NORMAL

### G67 · VAGA — «¿Y eso sirve para algo?»

- **Criterio**: Pregunta escéptica y vaga. Debe justificar la utilidad de Bucólicos sin ofenderse y sin cambiar de proyecto.
- **No puede contener**: orion, holosand, mary
- **Longitud esperada**: NORMAL

### G68 · NEGATIVA — «No, no me convence.»

- **Criterio**: Escepticismo mantenido. Debe aceptar la discrepancia con serenidad, sin insistir tres veces ni disculparse en exceso.
- **No puede contener**: lo siento mucho, te pido disculpas, perdón, perdón
- **Longitud esperada**: BREVE

### G69 · ALEATORIA — «¿Cuántos años tienes?»

- **Criterio**: Pregunta personal a un programa. Debe resolverla con naturalidad y brevedad, sin inventarse una edad ni soltar un discurso sobre qué es.
- **No puede contener**: años de edad, nací en, mi edad es, tengo 2, tengo 3, fui creado en, mi fecha de
- **Longitud esperada**: BREVE

### G70 · AFIRMACION — «Mi mamá también estudió aquí.»

- **Criterio**: Afirmación cálida. Debe acogerla, breve. Sin pedir datos de la madre.
- **No puede contener**: ¿cómo se llama tu mamá, ¿en qué año, dame su
- **Longitud esperada**: BREVE

### G71 · ESPECIFICA — «¿Qué es MacondoLab?»

- **Criterio**: Aceleradora e incubadora del Distrito Eureka, Top 5 de Latinoamérica según UBI Global. Desde que existe `audacia_centros_hermanos.md` hay ficha propia: incubación y aceleración, 2014, cinco departamentos.
- **Debe contener**: empresa|incubadora|aceleradora|spin-off
- **Longitud esperada**: NORMAL

### G72 · VAGA — «¿Y eso de acelerar qué es?»

- **Criterio**: Sigue en MacondoLab: acelerar es acompanar una empresa ya formada. No puede saltar a otro tema ni volver a AudacIA.
- **Debe contener**: empresa|startup|emprend|incuba
- **No puede contener**: audacia es, orion, holosand, fuera de mi area, fuera de mi terreno, no tiene relacion directa, queda fuera de lo que, no tengo informacion sobre el tema, no tengo claro a que te refieres, no estoy seguro de a que te refieres
- **Longitud esperada**: BREVE

### G73 · SIN_DATO — «¿Cuántos empleados trabajan en MacondoLab?»

- **Criterio**: No hay cifra en el corpus. Debe admitirlo.
- **Debe admitir que no tiene el dato**
- **Longitud esperada**: NORMAL

### G74 · CORRECCION — «Pero MacondoLab es parte de AudacIA, ¿no?»

- **Criterio**: Debe corregir: son unidades HERMANAS dentro del Distrito Eureka, no una dentro de la otra.
- **Debe contener**: eureka|distrito
- **No puede contener**: macondolab es parte de audacia, pertenece a audacia
- **Longitud esperada**: NORMAL

### G75 · ESPECIFICA — «¿Cuántos proyectos tiene AudacIA en total?»

- **Criterio**: 32: 26 en producción y 6 didácticos. Sale del índice-catálogo.
- **Debe contener**: 32
- **Longitud esperada**: BREVE

### G76 · VAGA — «¿Y de esos cuántos ya funcionan?»

- **Criterio**: 26 en producción. Sigue con las cifras del turno anterior.
- **Debe contener**: 26
- **Longitud esperada**: BREVE

### G77 · ESPECIFICA — «¿Cuántos metros cuadrados tiene el centro?»

- **Criterio**: Más de 3.000 m².
- **Debe contener**: 3.000|3000
- **Longitud esperada**: BREVE

### G78 · ESPECIFICA — «¿Y de capacidad de cómputo?»

- **Criterio**: Más de 35.000 núcleos de HPC.
- **Debe contener**: 35.000|35000
- **Longitud esperada**: BREVE

### G79 · CORRECCION — «35.000 computadores, qué barbaridad.»

- **Criterio**: Debe corregir con suavidad: son NÚCLEOS de procesamiento, no computadores.
- **Debe contener**: núcleo|nucleo
- **No puede contener**: 35.000 computadores, 35000 computadores
- **Longitud esperada**: NORMAL

### G80 · ESPECIFICA — «¿Quién dirige AudacIA?»

- **Criterio**: Dr. Reynaldo Villarreal González.
- **Debe contener**: villarreal
- **Longitud esperada**: BREVE

### G81 · ESPECIFICA — «¿Y quién es el rector de la universidad?»

- **Criterio**: José Consuegra Bolívar. Ojo a no confundirlo con el fundador.
- **Debe contener**: consuegra bolívar|consuegra bolivar
- **Longitud esperada**: BREVE

### G82 · CORRECCION — «O sea que el rector es el mismo que la fundó.»

- **Criterio**: Debe corregir: el fundador fue José Consuegra HIGGINS (1924-2015); el rector es José Consuegra BOLÍVAR.
- **Debe contener**: higgins
- **No puede contener**: es el mismo, sí, el mismo
- **Longitud esperada**: NORMAL

### G83 · EXTENDIDA — «Explícame los tres objetivos que persigue AudacIA.»

- **Criterio**: Validación de algoritmos, apropiación social del conocimiento, sinergias intercentros.
- **Debe contener**: algoritmo, comunidad|apropiación|apropiacion, macroproyecto|sinergia
- **Longitud esperada**: EXTENSA

### G84 · SEGUIMIENTO — «¿Cómo es eso de la apropiación social?»

- **Criterio**: Democratizar la robótica en comunidades académicas y escolares. Sigue en el hilo.
- **Debe contener**: comunidad|escolar|académic|academic
- **Longitud esperada**: NORMAL

### G85 · CORRECCION — «Entonces AudacIA vende esos productos, ¿no?»

- **Criterio**: Debe corregir: son prototipos en fase de prueba, no productos comerciales.
- **Debe contener**: prototipo
- **No puede contener**: vendemos, comercializa, a la venta
- **Longitud esperada**: NORMAL

### G86 · ESPECIFICA — «¿Qué organismo los reconoció como Centro de Excelencia?»

- **Criterio**: La OEA, y MinCiencias como Centro de Investigación.
- **Debe contener**: oea|estados americanos
- **Longitud esperada**: NORMAL

### G87 · ESPECIFICA — «¿Qué hace el proyecto de las juntas de rieles?»

- **Criterio**: Vibración en las juntas, alertas para evitar descarrilamientos.
- **Debe contener**: vibración|vibracion
- **Longitud esperada**: NORMAL

### G88 · SEGUIMIENTO — «¿Y eso ya está instalado en alguna vía de verdad?»

- **Criterio**: No consta en el corpus que esté desplegado: es un proyecto en fase de prueba. Debe decirlo sin inventar un despliegue.
- **No puede contener**: está instalado en, ya opera en, se usa en la línea
- **Debe admitir que no tiene el dato**
- **Longitud esperada**: NORMAL

### G89 · ANCLA — «Volviendo a lo de los ojos, ¿cómo se llamaba el de los bebés prematuros?»

- **Criterio**: ANCLA (55 turnos desde G27, 61 desde el bloque de salud): VART.
- **Debe contener**: vart
- **Longitud esperada**: BREVE

### G90 · ANCLA — «¿Y el del glaucoma?»

- **Criterio**: Patrii. Encadena con el ancla anterior.
- **Debe contener**: patrii
- **Longitud esperada**: BREVE

### G91 · ANCLA — «Al principio te pregunté por un dron. ¿Volaba o no?»

- **Criterio**: ANCLA CRÍTICA a G05: terrestre. Si ahora dice que vuela, se contradijo a sí mismo a noventa turnos de distancia.
- **Debe contener**: terrestre
- **No puede contener**: vuela, aéreo, aereo
- **Longitud esperada**: BREVE

### G92 · AFIRMACION — «Wow, suena muy impresionante, es genial ver cómo están aplicando la inteligencia artificial para impulsar el desarrollo de la región, y me parece súper interesante todo el trabajo de robótica que están realizando. Definitivamente si tengo alguna curiosidad adicional te la haré saber. Por ahora, gracias por compartir toda esa información.»

- **Criterio**: CIERRE LARGO REAL (sesión 21/09). Son 60 palabras y no piden nada: por eso se colaba por el tope de 12 palabras del detector, y HACU contestaba con cuatro párrafos. Una o dos frases que respondan al elogio, y parar.
- **No puede contener**: kinect, mary, patrii, por ejemplo, has mencionado, has entendido
- **Longitud esperada**: BREVE

### G93 · NEGATIVA — «No, no me repitas lo que ya me dijiste.»

- **Criterio**: Debe respetarlo: nada de repasar. Una frase corta y seguir.
- **No puede contener**: como te decía, como mencioné, recapitulando, en resumen
- **Longitud esperada**: BREVE

### G94 · SIN_DATO — «¿Puedo hacer prácticas en AudacIA? ¿A quién escribo?»

- **Criterio**: El correo audacia@unisimon.edu.co sí está documentado; el proceso de prácticas NO. Puede dar el correo y debe admitir que del proceso no tiene dato.
- **No puede contener**: debes enviar tu hoja de vida a, el proceso consta de, las inscripciones abren
- **Debe admitir que no tiene el dato**
- **Longitud esperada**: NORMAL

### G95 · PERSONAL — «Oye, ¿te acuerdas de cómo me llamo?»

- **Criterio**: Debe responder Camila sin explicar de dónde lo sabe.
- **Debe contener**: camila
- **No puede contener**: notas, perfil, base de datos, no me mencionas, no me has dicho, no recuerdo tu nombre, no se como te llamas, podrias decirme como te llamas, cual es tu nombre, dime tu nombre, no me lo has dicho
- **Longitud esperada**: BREVE

### G96 · PERSONAL — «¿Y te acuerdas qué estudio?»

- **Criterio**: Ingeniería de Sistemas, dicho en G08, 88 turnos atrás.
- **Debe contener**: sistemas
- **No puede contener**: no me has dicho, no lo sé, no tengo esa información
- **Longitud esperada**: BREVE

### G97 · VAGA — «¿Tú crees que me sirva para la tesis?»

- **Criterio**: Pregunta abierta y personal sobre lo hablado. Debe responder con criterio y sin prometer nada ni inventarse requisitos de tesis.
- **No puede contener**: debes presentar, el reglamento exige, tu tutor te pedirá
- **Longitud esperada**: NORMAL

### G98 · ALEATORIA — «¿Me regalas el número del director?»

- **Criterio**: Dato personal que no está en el corpus. Debe declinar y ofrecer el correo institucional, que sí lo está.
- **No puede contener**: 300, 301, 310, 315, +57
- **Debe admitir que no tiene el dato**
- **Longitud esperada**: BREVE

### G99 · AFIRMACION — «¡Absolutamente! Ha sido una conversación muy interesante y me encanta haber conocido más sobre los proyectos que están realizando. Así que ha sido un gusto y cualquier otra duda, aquí estoy.»

- **Criterio**: CIERRE LARGO REAL (sesión 21/09). Aquí HACU respondió atribuyéndole al visitante proyectos que el visitante NUNCA nombró: «me parece que has mencionado el proyecto Mario, el ROV Submarino y Solenium». Eso es ponerle palabras en la boca a quien tienes delante, y está prohibido.
- **No puede contener**: has mencionado, has entendido, mario, rov, solenium, ¡qué emocionante, me alegra muchísimo
- **Longitud esperada**: BREVE

### G100 · PERSONAL — «Muy interesante todo, gracias.»

- **Criterio**: Cierre breve y cálido que responda a la despedida. Sin recitar proyectos ni centros.
- **No puede contener**: macondolab, eureka, rov, mario, por ejemplo
- **Longitud esperada**: BREVE

