/**
 * Haku Inventario — Cliente de Voz Web y Push-to-Talk
 * Responsable: Integrante 4 (Haku Inventario, API Interna y Voz Web)
 *
 * Características principales:
 * - Push-to-Talk mediante barra espaciadora en PC (cuando no se está en un input/textarea)
 * - Botón táctil para móvil / tablet (tap o pulsar sostenido)
 * - Captura de micrófono con MediaRecorder API
 * - Transcripción con faster-whisper en el servidor
 * - Reproducción de audio TTS (Piper) EXCLUSIVAMENTE en el navegador del cliente
 * - Normalización fonética ("Audacia") y cero audio en altavoces del servidor
 * - Flujo de Pre-Confirmación obligatoria en dos pasos para mutaciones
 * - Expiración del contexto conversacional tras 2 horas de inactividad (7200 segundos)
 */

(function () {
    "use strict";

    // ─────────────────────────────────────────────────────────────────────────
    // CONFIGURACIÓN Y CONSTANTES
    // ─────────────────────────────────────────────────────────────────────────

    const CONFIG = {
        ENDPOINTS: {
            MENSAJE: "/api/haku/mensaje/",
            CONFIRMAR: "/api/haku/confirmar/",
            CANCELAR: "/api/haku/cancelar/",
            TRANSCRIBIR: "/api/haku/voz/transcribir/",
            SINTETIZAR: "/api/haku/voz/sintetizar/",
            ESTADO: "/api/haku/estado/",
            ALERTAS: "/api/haku/alertas/",
            PRODUCTO_CREAR: "/api/haku/producto/crear/",
            PRODUCTO_EDITAR: "/api/haku/producto/editar/",
            PRODUCTO_CONSULTAR_CODIGO_BARRAS: "/api/haku/producto/consultar-codigo-barras/",
            USUARIO_CREAR: "/api/haku/usuario/crear/",
            USUARIO_EDITAR: "/api/haku/usuario/editar/",
        },
        TIMEOUT_INACTIVIDAD_MS: 120 * 60 * 1000, // 2 horas (120 minutos / 7200 segundos)
        MIME_TYPES: [
            "audio/webm;codecs=opus",
            "audio/webm",
            "audio/ogg;codecs=opus",
            "audio/wav",
        ],
    };

    // ─────────────────────────────────────────────────────────────────────────
    // ESTADO DE LA APLICACIÓN
    // ─────────────────────────────────────────────────────────────────────────

    const state = {
        grabando: false,
        iniciandoGrabacion: false,
        detencionPendiente: false,
        procesando: false,
        reproduciendoAudio: false,
        audioElement: null,
        abortControllerTTS: null,
        interrupcionConsumida: false,
        mediaRecorder: null,
        audioChunks: [],
        streamMicrofono: null,
        audioContextMetro: null,
        analyserNode: null,
        maxRmsGrabacion: 0,
        animFrameMetro: null,
        tiempoInicioGrabacion: 0,
        propuestaActiva: null,
        ultimoAcceso: Date.now(),
        timerInactividad: null,
        espacioPresionado: false,
        ttsPlaybackId: 0,
    };

    // ─────────────────────────────────────────────────────────────────────────
    // UTILIDADES CSRF Y RED
    // ─────────────────────────────────────────────────────────────────────────

    function getCsrfToken() {
        // 1. Buscar en meta tag
        const metaTag = document.querySelector('meta[name="csrf-token"]');
        if (metaTag && metaTag.content) return metaTag.content;

        // 2. Buscar en input oculto
        const inputCsrf = document.querySelector('input[name="csrfmiddlewaretoken"]');
        if (inputCsrf && inputCsrf.value) return inputCsrf.value;

        // 3. Buscar en cookies
        const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]*)/);
        return match ? decodeURIComponent(match[1]) : "";
    }

    async function apiFetch(url, options = {}) {
        const headers = options.headers || {};
        headers["X-CSRFToken"] = getCsrfToken();
        headers["X-Requested-With"] = "XMLHttpRequest";

        const config = { ...options, headers };
        const response = await fetch(url, config);

        if (response.status === 401) {
            mostrarToast("Sesión expirada o no iniciada. Redirigiendo...", "error");
            setTimeout(() => { window.location.href = "/cuentas/login/"; }, 1500);
            throw new Error("No autenticado");
        }

        if (response.status === 403) {
            mostrarToast("Acceso denegado o cuenta desactivada.", "error");
            throw new Error("Acceso denegado");
        }

        return response;
    }

    // ─────────────────────────────────────────────────────────────────────────
    // GESTIÓN DEL TIMER DE 2 HORAS DE INACTIVIDAD (7200s)
    // ─────────────────────────────────────────────────────────────────────────

    function reiniciarTimerInactividad() {
        state.ultimoAcceso = Date.now();
        if (state.timerInactividad) clearTimeout(state.timerInactividad);

        state.timerInactividad = setTimeout(() => {
            manejarExpiracionInactividad();
        }, CONFIG.TIMEOUT_INACTIVIDAD_MS);
    }

    function manejarExpiracionInactividad() {
        state.propuestaActiva = null;
        ocultarTarjetaPropuesta();
        mostrarToast("El contexto de Haku ha caducado por inactividad (2 horas). Tu sesión sigue abierta.", "info");
        agregarMensajeSistema("Contexto operativo reiniciado por inactividad (2 horas / 7200 s).");
    }

    // ─────────────────────────────────────────────────────────────────────────
    // MONITOREO DE NIVEL DE ENTRADA (AUDIO METER / ANALYSER)
    // ─────────────────────────────────────────────────────────────────────────

    function iniciarMedidorNivelAudio(stream) {
        try {
            const AudioContextClass = window.AudioContext || window.webkitAudioContext;
            if (!AudioContextClass) return;

            if (!state.audioContextMetro || state.audioContextMetro.state === "closed") {
                state.audioContextMetro = new AudioContextClass();
            }
            if (state.audioContextMetro.state === "suspended") {
                state.audioContextMetro.resume();
            }

            const source = state.audioContextMetro.createMediaStreamSource(stream);
            const analyser = state.audioContextMetro.createAnalyser();
            analyser.fftSize = 512;
            source.connect(analyser);

            state.analyserNode = analyser;
            state.maxRmsGrabacion = 0;

            const buffer = new Float32Array(analyser.fftSize);
            const medir = () => {
                if (!state.grabando) return;
                analyser.getFloatTimeDomainData(buffer);
                let sumSq = 0;
                for (let i = 0; i < buffer.length; i++) {
                    sumSq += buffer[i] * buffer[i];
                }
                const rms = Math.sqrt(sumSq / buffer.length);
                if (rms > state.maxRmsGrabacion) {
                    state.maxRmsGrabacion = rms;
                }
                state.animFrameMetro = requestAnimationFrame(medir);
            };
            state.animFrameMetro = requestAnimationFrame(medir);
        } catch (e) {
            console.warn("[HakuVoz] No se pudo inicializar AnalyserNode:", e);
        }
    }

    function detenerMedidorNivelAudio() {
        if (state.animFrameMetro) {
            cancelAnimationFrame(state.animFrameMetro);
            state.animFrameMetro = null;
        }
    }

    // ─────────────────────────────────────────────────────────────────────────
    // GRABACIÓN DE VOZ (MICROFONO CLIENTE)
    // ─────────────────────────────────────────────────────────────────────────

    async function obtenerStreamMicrofonoValido() {
        // Verificar si el stream existente sigue vivo y activo
        if (state.streamMicrofono) {
            const tracks = state.streamMicrofono.getAudioTracks();
            const trackValido = tracks.length > 0 && tracks[0].readyState === "live" && tracks[0].enabled;
            if (trackValido) {
                return state.streamMicrofono;
            }
            // Si el track feneció o está inactivo, detener pistas previas
            tracks.forEach(t => { try { t.stop(); } catch (_) {} });
            state.streamMicrofono = null;
        }

        // Solicitar MediaStream con restricciones estándar (evitando forzar sampleRate: 16000 que
        // en Windows silencia o entra en conflicto con drivers WASAPI a 48000Hz).
        const constraints = {
            audio: {
                echoCancellation: true,
                noiseSuppression: true,
                autoGainControl: true,
            },
        };

        const nuevoStream = await navigator.mediaDevices.getUserMedia(constraints);
        const pistas = nuevoStream.getAudioTracks();
        if (pistas.length > 0) {
            const settings = pistas[0].getSettings ? pistas[0].getSettings() : {};
            console.log("[HakuVoz] Micrófono capturado:", pistas[0].label,
                "| readyState:", pistas[0].readyState,
                "| muted:", pistas[0].muted,
                "| sampleRate:", settings.sampleRate,
                "| channels:", settings.channelCount);
        }
        state.streamMicrofono = nuevoStream;
        return nuevoStream;
    }

    async function iniciarGrabacion() {
        if (state.grabando || state.procesando || state.iniciandoGrabacion) return;

        state.iniciandoGrabacion = true;
        state.detencionPendiente = false;

        try {
            const stream = await obtenerStreamMicrofonoValido();

            // Si el usuario soltó el control antes de que getUserMedia terminara
            if (state.detencionPendiente) {
                state.iniciandoGrabacion = false;
                state.detencionPendiente = false;
                actualizarEstadoVisual("reposo");
                return;
            }

            // Seleccionar el formato de grabación soportado
            let mimeType = "";
            for (const t of CONFIG.MIME_TYPES) {
                if (MediaRecorder.isTypeSupported(t)) {
                    mimeType = t;
                    break;
                }
            }

            state.audioChunks = [];
            state.mediaRecorder = mimeType
                ? new MediaRecorder(stream, { mimeType })
                : new MediaRecorder(stream);

            state.mediaRecorder.ondataavailable = (event) => {
                const size = (event.data && event.data.size) || 0;
                console.log("[HakuVoz] ondataavailable recibido:", size, "bytes");
                if (size > 0) {
                    state.audioChunks.push(event.data);
                }
            };

            state.mediaRecorder.onstop = () => {
                const duracionSeg = (Date.now() - state.tiempoInicioGrabacion) / 1000;
                detenerMedidorNivelAudio();
                console.log("[HakuVoz] mediaRecorder onstop. Chunks:", state.audioChunks.length,
                    "| Duración:", duracionSeg.toFixed(2), "s",
                    "| Max RMS entrada:", state.maxRmsGrabacion.toFixed(4));
                procesarAudioGrabado(duracionSeg);
            };

            state.tiempoInicioGrabacion = Date.now();
            state.grabando = true;
            state.iniciandoGrabacion = false;

            iniciarMedidorNivelAudio(stream);

            state.mediaRecorder.start(250); // trozos cada 250ms
            console.log("[HakuVoz] mediaRecorder iniciado con mimeType:", mimeType || "default");
            actualizarEstadoVisual("grabando");

            // Si se solicitó detención mientras llamábamos a start()
            if (state.detencionPendiente) {
                console.log("[HakuVoz] detención pendiente detectada tras start()");
                state.detencionPendiente = false;
                detenerGrabacion();
            }
        } catch (error) {
            state.iniciandoGrabacion = false;
            state.detencionPendiente = false;
            console.error("[HakuVoz] Error al acceder al micrófono:", error);
            mostrarToast("No se pudo acceder al micrófono. Verifica los permisos del navegador o el certificado HTTPS.", "error");
            actualizarEstadoVisual("reposo");
        }
    }

    function detenerGrabacion() {
        console.log("[HakuVoz] detenerGrabacion llamado. grabando:", state.grabando, "iniciando:", state.iniciandoGrabacion);
        if (state.iniciandoGrabacion) {
            state.detencionPendiente = true;
            return;
        }
        if (!state.grabando || !state.mediaRecorder) return;
        state.grabando = false;
        actualizarEstadoVisual("procesando");
        try {
            if (state.mediaRecorder.state !== "inactive") {
                // Al llamar a stop(), el estándar W3C MediaRecorder dispara inmediatamente
                // el último ondataavailable con los datos restantes y a continuación onstop.
                state.mediaRecorder.stop();
            }
        } catch (e) {
            console.error("[HakuVoz] Error al detener grabador:", e);
        }
    }

    // ─────────────────────────────────────────────────────────────────────────
    // RESAMPLING Y ENCODING DE AUDIO A 16 KHZ MONO (PCM 16-BIT WAV)
    // ─────────────────────────────────────────────────────────────────────────

    function resampleLinear(samples, sourceRate, targetRate) {
        if (sourceRate === targetRate) return samples;
        const ratio = sourceRate / targetRate;
        const targetLength = Math.round(samples.length / ratio);
        const result = new Float32Array(targetLength);
        for (let i = 0; i < targetLength; i++) {
            const srcPos = i * ratio;
            const index = Math.floor(srcPos);
            const fraction = srcPos - index;
            const nextIndex = Math.min(index + 1, samples.length - 1);
            result[i] = samples[index] * (1 - fraction) + samples[nextIndex] * fraction;
        }
        return result;
    }

    function encodeWavPcm16(samples, sampleRate = 16000) {
        const buffer = new ArrayBuffer(44 + samples.length * 2);
        const view = new DataView(buffer);

        function writeString(offset, string) {
            for (let i = 0; i < string.length; i++) {
                view.setUint8(offset + i, string.charCodeAt(i));
            }
        }

        // Encabezado RIFF (44 bytes estándar)
        writeString(0, "RIFF");
        view.setUint32(4, 36 + samples.length * 2, true);
        writeString(8, "WAVE");

        // Subchunk 'fmt '
        writeString(12, "fmt ");
        view.setUint32(16, 16, true);
        view.setUint16(20, 1, true);                          // Formato 1 = PCM
        view.setUint16(22, 1, true);                          // 1 Canal (Mono)
        view.setUint32(24, sampleRate, true);                 // 16000 Hz
        view.setUint32(28, sampleRate * 2, true);             // Byte rate (16000 * 1 * 2 = 32000)
        view.setUint16(32, 2, true);                          // Block align
        view.setUint16(34, 16, true);                         // 16 bits por muestra

        // Subchunk 'data'
        writeString(36, "data");
        view.setUint32(40, samples.length * 2, true);

        // Muestras PCM con saturación [-1.0, 1.0] -> int16
        let offset = 44;
        for (let i = 0; i < samples.length; i++, offset += 2) {
            const s = Math.max(-1, Math.min(1, samples[i]));
            view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
        }

        return new Blob([buffer], { type: "audio/wav" });
    }

    async function resamplearAudioA16kMono(audioBlob) {
        // En navegadores Chromium/Firefox, decodeAudioData() sobre WebM/Opus chunked de MediaRecorder
        // trunca silenciosamente el audio al primer cluster (250-300ms) por falta de cabeceras Cues/SeekHead.
        // Por tanto, los flujos WebM/OGG se envían directamente al AudioDecoder (ffmpeg) del servidor,
        // garantizando decodificación al 100% sin truncamiento a 16kHz mono.
        const mime = (audioBlob && audioBlob.type) || "";
        if (mime.includes("webm")) {
            return { blob: audioBlob, filename: "grabacion.webm" };
        }
        if (mime.includes("ogg")) {
            return { blob: audioBlob, filename: "grabacion.ogg" };
        }
        if (mime.includes("mp4")) {
            return { blob: audioBlob, filename: "grabacion.mp4" };
        }

        // Si es audio/wav o formato sin clusterizado opaco, procesar en cliente si AudioContext existe
        const AudioContextClass = window.AudioContext || window.webkitAudioContext;
        if (!AudioContextClass) {
            return { blob: audioBlob, filename: "grabacion.wav" };
        }

        let audioCtx = null;
        try {
            audioCtx = new AudioContextClass();
            const arrayBuffer = await audioBlob.arrayBuffer();
            const audioBuffer = await new Promise((resolve, reject) => {
                const result = audioCtx.decodeAudioData(arrayBuffer, resolve, reject);
                if (result && typeof result.then === "function") {
                    result.then(resolve).catch(reject);
                }
            });
            const numChannels = audioBuffer.numberOfChannels;
            const length = audioBuffer.length;
            let monoData = null;
            if (numChannels === 1) {
                monoData = audioBuffer.getChannelData(0);
            } else {
                monoData = new Float32Array(length);
                for (let c = 0; c < numChannels; c++) {
                    const channelData = audioBuffer.getChannelData(c);
                    for (let i = 0; i < length; i++) {
                        monoData[i] += channelData[i] / numChannels;
                    }
                }
            }
            const resampledData = resampleLinear(monoData, audioBuffer.sampleRate, 16000);
            const wavBlob = encodeWavPcm16(resampledData, 16000);
            return { blob: wavBlob, filename: "grabacion.wav" };
        } catch (err) {
            return { blob: audioBlob, filename: "grabacion.wav" };
        } finally {
            if (audioCtx && typeof audioCtx.close === "function") {
                try { audioCtx.close(); } catch (_) {}
            }
        }
    }

    async function procesarAudioGrabado(duracionSeg = 0) {
        if (state.procesando) {
            console.warn("[HakuVoz] procesarAudioGrabado ignorado: ya se encuentra procesando.");
            return;
        }

        console.log("[HakuVoz] procesarAudioGrabado iniciado. Chunks:", state.audioChunks.length,
            "| Duración:", duracionSeg ? duracionSeg.toFixed(2) + "s" : "N/D",
            "| Max RMS entrada:", state.maxRmsGrabacion.toFixed(4));

        if (state.audioChunks.length === 0) {
            console.warn("[HakuVoz] Descartando: state.audioChunks está vacío.");
            mostrarToast("Grabación vacía. Mantén pulsada la barra espaciadora mientras hablas.", "info");
            actualizarEstadoVisual("reposo");
            return;
        }

        const mime = (state.mediaRecorder && state.mediaRecorder.mimeType) || "audio/webm";
        const audioBlob = new Blob(state.audioChunks, { type: mime });
        console.log("[HakuVoz] audioBlob generado:", audioBlob.size, "bytes, mime:", audioBlob.type);
        state.audioChunks = [];

        // Filtro de integridad: si el buffer carece de datos útiles (< 100 bytes no contiene ni cabecera ni payload).
        // En versiones iniciales se usaba if (audioBlob.size < 1000), pero descartaba locuciones válidas breves.
        if (audioBlob.size < 100) {
            console.warn("[HakuVoz] Descartando: audioBlob sin datos utilizables (", audioBlob.size, "bytes)");
            mostrarToast("Grabación sin audio capturado. Intenta hablar nuevamente.", "info");
            actualizarEstadoVisual("reposo");
            return;
        }

        state.procesando = true;
        actualizarEstadoVisual("procesando");

        // Alerta preventiva no bloqueante si el hardware/navegador entregó señal cero (micrófono silenciado)
        if (state.maxRmsGrabacion < 0.005) {
            console.warn("[HakuVoz] ADVERTENCIA: La amplitud de entrada capturada por el navegador fue prácticamente nula (Max RMS = " + state.maxRmsGrabacion.toFixed(5) + "). Comprueba que el micrófono no esté muteado en Windows o con volumen en cero.");
        }

        try {
            reiniciarTimerInactividad();

            // Preparar audio con resampling 16kHz mono (o fallback transparente a Blob original)
            const { blob: audioParaEnviar, filename } = await resamplearAudioA16kMono(audioBlob);

            // Enviar audio al backend (AudioDecoder lo procesa a 16kHz mono)
            const formData = new FormData();
            formData.append("audio", audioParaEnviar, filename);

            const respSTT = await apiFetch(CONFIG.ENDPOINTS.TRANSCRIBIR, {
                method: "POST",
                body: formData,
            });

            const dataSTT = await respSTT.json();
            console.log("[HakuVoz] Respuesta STT recibida:", dataSTT);

            // 2. Manejo explícito de silencio VAD
            if (dataSTT.status === "silencio" || dataSTT.estado === "SIN_VOZ") {
                let mensajeAviso = dataSTT.mensaje_pantalla || "No se detectó voz audible en el micrófono.";
                if (state.maxRmsGrabacion < 0.005) {
                    mensajeAviso += " (El micrófono parece no recibir señal física o estar silenciado en Windows).";
                }
                mostrarToast(mensajeAviso, "info");
                actualizarEstadoVisual("reposo");
                state.procesando = false;
                return;
            }

            // 3. Manejo explícito de error en el motor de transcripción STT
            if (dataSTT.status === "error_transcripcion" || dataSTT.estado === "ERROR_TRANSCRIPCION") {
                const mensajeError = dataSTT.mensaje_pantalla || "Hubo un problema al transcribir el audio en el servidor.";
                mostrarToast(mensajeError, "error");
                actualizarEstadoVisual("reposo");
                state.procesando = false;
                return;
            }

            const textoTranscrito = (dataSTT.texto || "").trim();

            if (!textoTranscrito) {
                mostrarToast("No se detectó voz clara. Intenta nuevamente.", "info");
                actualizarEstadoVisual("reposo");
                state.procesando = false;
                return;
            }

            // Mostrar lo que el usuario dijo
            agregarMensajeChat("usuario", textoTranscrito);

            // 3. Procesar el comando con Haku
            await enviarComandoHaku(textoTranscrito);

        } catch (error) {
            console.error("Error procesando audio:", error);
            mostrarToast("Error de conexión al procesar audio.", "error");
            actualizarEstadoVisual("reposo");
        } finally {
            state.procesando = false;
        }
    }

    // ─────────────────────────────────────────────────────────────────────────
    // ENVÍO DE COMANDO Y RESPUESTA
    // ─────────────────────────────────────────────────────────────────────────

    async function enviarComandoHaku(mensajeTexto) {
        reiniciarTimerInactividad();
        actualizarEstadoVisual("procesando");

        try {
            const resp = await apiFetch(CONFIG.ENDPOINTS.MENSAJE, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ mensaje: mensajeTexto }),
            });

            const data = await resp.json();

            if (data.error) {
                agregarMensajeChat("haku", `⚠️ **Error:** ${data.error}`);
                if (data.mensaje_verbal) reproducirVozCliente(data.mensaje_verbal);
                actualizarEstadoVisual("reposo");
                return;
            }

            // Si es una propuesta pendiente de confirmación
            if (data.tipo === "propuesta" && data.propuesta) {
                state.propuestaActiva = data.propuesta;
                mostrarTarjetaPropuesta(data.propuesta);
            } else if (data.tipo === "aclaracion" && data.requiere_escritura && data.esperando === "destinatario") {
                state.propuestaActiva = null;
                mostrarFormularioDestinatario(data);
            } else if (data.tipo === "formulario_crear_producto" && data.datos) {
                state.propuestaActiva = null;
                mostrarFormularioCrearProducto(data.datos);
            } else if (data.tipo === "formulario_editar_producto" && data.datos) {
                state.propuestaActiva = null;
                mostrarFormularioEditarProducto(data.datos);
            } else if (data.tipo === "formulario_crear_usuario" && data.datos) {
                state.propuestaActiva = null;
                mostrarFormularioCrearUsuario(data.datos);
            } else if (data.tipo === "formulario_editar_usuario" && data.datos) {
                state.propuestaActiva = null;
                mostrarFormularioEditarUsuario(data.datos);
            } else if (data.tipo === "confirmacion_ejecutada" || data.tipo === "propuesta_cancelada") {
                state.propuestaActiva = null;
                ocultarTarjetaPropuesta();
            }

            // Mostrar respuesta en chat
            if (data.mensaje_pantalla) {
                agregarMensajeChat("haku", data.mensaje_pantalla, data.tipo);
            }

            // Reproducir síntesis de voz en el dispositivo cliente
            if (data.mensaje_verbal) {
                await reproducirVozCliente(data.mensaje_verbal);
            } else {
                actualizarEstadoVisual("reposo");
            }

        } catch (error) {
            console.error("Error al enviar comando:", error);
            agregarMensajeChat("haku", "⚠️ Ocurrió un error al procesar tu solicitud.");
            actualizarEstadoVisual("reposo");
        }
    }

    // ─────────────────────────────────────────────────────────────────────────
    // SÍNTESIS DE VOZ Y REPRODUCCIÓN EN CLIENTE (PIPER TTS)
    // ─────────────────────────────────────────────────────────────────────────

    function detenerReproduccionVoz() {
        state.ttsPlaybackId = (state.ttsPlaybackId || 0) + 1;

        if (state.abortControllerTTS) {
            try {
                state.abortControllerTTS.abort();
            } catch (err) {
                console.warn("Error al abortar síntesis TTS:", err);
            }
            state.abortControllerTTS = null;
        }

        if (state.audioElement) {
            try {
                state.audioElement.onended = null;
                state.audioElement.onerror = null;
                state.audioElement.pause();
                state.audioElement.currentTime = 0;
                if (state.audioElement.src && state.audioElement.src.startsWith("blob:")) {
                    URL.revokeObjectURL(state.audioElement.src);
                }
            } catch (err) {
                console.warn("Error al detener elemento de audio:", err);
            }
            state.audioElement = null;
        }

        state.reproduciendoAudio = false;
        actualizarEstadoVisual("reposo");
    }

    async function reproducirVozCliente(textoParaVoz) {
        if (!textoParaVoz) {
            actualizarEstadoVisual("reposo");
            return;
        }

        // Cancelar limpiamente cualquier reproducción anterior
        detenerReproduccionVoz();

        const currentPlaybackId = ++state.ttsPlaybackId;

        try {
            actualizarEstadoVisual("hablando");
            state.reproduciendoAudio = true;

            const controller = new AbortController();
            state.abortControllerTTS = controller;

            const resp = await apiFetch(CONFIG.ENDPOINTS.SINTETIZAR, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ texto: textoParaVoz }),
                signal: controller.signal,
            });

            if (currentPlaybackId !== state.ttsPlaybackId || controller.signal.aborted || !state.reproduciendoAudio) {
                return;
            }

            if (!resp.ok) {
                detenerReproduccionVoz();
                return;
            }

            const audioBlob = await resp.blob();

            if (currentPlaybackId !== state.ttsPlaybackId || controller.signal.aborted || !state.reproduciendoAudio) {
                return;
            }

            const audioUrl = URL.createObjectURL(audioBlob);
            const audio = new Audio(audioUrl);
            state.audioElement = audio;

            audio.onended = () => {
                URL.revokeObjectURL(audioUrl);
                if (state.ttsPlaybackId === currentPlaybackId) {
                    state.audioElement = null;
                    state.reproduciendoAudio = false;
                    actualizarEstadoVisual("reposo");
                }
            };

            audio.onerror = () => {
                URL.revokeObjectURL(audioUrl);
                if (state.ttsPlaybackId === currentPlaybackId) {
                    state.audioElement = null;
                    state.reproduciendoAudio = false;
                    actualizarEstadoVisual("reposo");
                }
            };

            if (currentPlaybackId !== state.ttsPlaybackId || controller.signal.aborted || !state.reproduciendoAudio) {
                URL.revokeObjectURL(audioUrl);
                return;
            }

            await audio.play();

        } catch (error) {
            if (error && error.name === "AbortError") {
                // Interrupción intencional del usuario (barge-in); silenciar
                return;
            }
            console.warn("No se pudo reproducir audio en cliente:", error);
            detenerReproduccionVoz();
        }
    }

    // ─────────────────────────────────────────────────────────────────────────
    // PRE-CONFIRMACIÓN: CONFIRMAR O CANCELAR PROPUESTA
    // ─────────────────────────────────────────────────────────────────────────

    async function confirmarPropuestaManual(propuestaId) {
        if (!propuestaId) return;
        reiniciarTimerInactividad();
        actualizarEstadoVisual("procesando");

        try {
            const resp = await apiFetch(CONFIG.ENDPOINTS.CONFIRMAR, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ propuesta_id: propuestaId }),
            });

            const data = await resp.json();

            if (data.error) {
                mostrarToast(data.error, "error");
                agregarMensajeChat("haku", `⚠️ No se pudo completar: ${data.error}`);
            } else {
                state.propuestaActiva = null;
                ocultarTarjetaPropuesta();
                agregarMensajeChat("haku", `✅ ${data.mensaje_pantalla}`, "confirmacion_ejecutada");
                if (data.mensaje_verbal) {
                    await reproducirVozCliente(data.mensaje_verbal);
                }
            }
        } catch (error) {
            console.error("Error al confirmar propuesta:", error);
            mostrarToast("Error al confirmar la transacción.", "error");
        } finally {
            actualizarEstadoVisual("reposo");
        }
    }

    async function cancelarPropuestaManual(propuestaId) {
        reiniciarTimerInactividad();

        try {
            await apiFetch(CONFIG.ENDPOINTS.CANCELAR, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ propuesta_id: propuestaId }),
            });

            state.propuestaActiva = null;
            ocultarTarjetaPropuesta();
            agregarMensajeChat("haku", "❌ Operación cancelada por el usuario.", "propuesta_cancelada");
            await reproducirVozCliente("Operación cancelada. No se realizaron cambios.");
        } catch (error) {
            console.error("Error al cancelar propuesta:", error);
        } finally {
            actualizarEstadoVisual("reposo");
        }
    }

    // ─────────────────────────────────────────────────────────────────────────
    // PUSH-TO-TALK CON BARRA ESPACIADORA (PC)
    // ─────────────────────────────────────────────────────────────────────────

    function configurarPushToTalkTeclado() {
        window.addEventListener("keydown", (e) => {
            // Verificar si es la barra espaciadora
            if (e.code !== "Space" && e.key !== " ") return;

            // Ignorar repeticiones continuas automáticas mientras la tecla se mantiene pulsada
            if (e.repeat) return;

            // No interceptar si el usuario está escribiendo en un input o editable
            const activo = document.activeElement;
            const esCampoDeTexto =
                activo &&
                (activo.tagName === "INPUT" ||
                 activo.tagName === "TEXTAREA" ||
                 activo.tagName === "SELECT" ||
                 activo.isContentEditable);

            if (esCampoDeTexto) return;

            e.preventDefault(); // Evitar scroll de la página con espacio

            // 1. REGLA BARGE-IN: Si HAKU está reproduciendo voz, la pulsación cancela inmediatamente
            // la reproducción y se consume como interrupción (NO debe iniciar grabación accidental).
            if (state.reproduciendoAudio) {
                detenerReproduccionVoz();
                state.interrupcionConsumida = true;
                return;
            }

            if (!state.espacioPresionado && !state.grabando && !state.procesando) {
                state.espacioPresionado = true;
                iniciarGrabacion();
            }
        });

        window.addEventListener("keyup", (e) => {
            if (e.code !== "Space" && e.key !== " ") return;

            // Si esta pulsación consumió la interrupción de audio, resetear bandera y no iniciar/detener grabación
            if (state.interrupcionConsumida) {
                state.interrupcionConsumida = false;
                return;
            }

            if (state.espacioPresionado) {
                state.espacioPresionado = false;
                detenerGrabacion();
            }
        });
    }

    // ─────────────────────────────────────────────────────────────────────────
    // BOTÓN MÓVIL / TÁCTIL (CLICK Y TOUCH)
    // ─────────────────────────────────────────────────────────────────────────

    function configurarBotonMicrofono() {
        const btnMic = document.getElementById("haku-btn-mic");
        if (!btnMic) return;

        let fueTouch = false;

        // Soporte táctil móvil (mantener pulsado para hablar o tap para interrumpir)
        btnMic.addEventListener("touchstart", (e) => {
            e.preventDefault();
            fueTouch = true;
            if (state.reproduciendoAudio) {
                detenerReproduccionVoz();
                return;
            }
            iniciarGrabacion();
        }, { passive: false });

        btnMic.addEventListener("touchend", (e) => {
            e.preventDefault();
            setTimeout(() => { fueTouch = false; }, 400);
            if (state.grabando) {
                detenerGrabacion();
            }
        }, { passive: false });

        // Soporte clic (toggle o interrupción)
        btnMic.addEventListener("click", (e) => {
            e.preventDefault();
            if (fueTouch) return;
            if (state.reproduciendoAudio) {
                detenerReproduccionVoz();
                return;
            }
            if (state.grabando) {
                detenerGrabacion();
            } else {
                iniciarGrabacion();
            }
        });
    }

    // ─────────────────────────────────────────────────────────────────────────
    // COMPONENTES DE INTERFAZ GRÁFICA
    // ─────────────────────────────────────────────────────────────────────────

    function actualizarEstadoVisual(nuevoEstado) {
        const btnMic = document.getElementById("haku-btn-mic");
        const statusLabel = document.getElementById("haku-status-label");
        const waveContainer = document.getElementById("haku-wave-container");

        if (btnMic) {
            btnMic.classList.remove("recording", "processing", "speaking");
            if (nuevoEstado === "grabando") btnMic.classList.add("recording");
            if (nuevoEstado === "procesando") btnMic.classList.add("processing");
            if (nuevoEstado === "hablando") btnMic.classList.add("speaking");
        }

        if (statusLabel) {
            switch (nuevoEstado) {
                case "grabando":
                    statusLabel.textContent = "Escuchando... (suelta Espacio o toca el micro)";
                    statusLabel.className = "status-recording";
                    break;
                case "procesando":
                    statusLabel.textContent = "Haku está pensando...";
                    statusLabel.className = "status-processing";
                    break;
                case "hablando":
                    statusLabel.textContent = "Haku respondiendo...";
                    statusLabel.className = "status-speaking";
                    break;
                default:
                    statusLabel.textContent = "Presiona Espacio o toca el micrófono para hablar";
                    statusLabel.className = "status-idle";
            }
        }

        if (waveContainer) {
            waveContainer.style.display = nuevoEstado === "grabando" || nuevoEstado === "hablando" ? "flex" : "none";
        }
    }

    function agregarMensajeChat(remitente, texto, tipo = "normal") {
        const chatFeed = document.getElementById("haku-chat-feed");
        if (!chatFeed) return;

        const bubble = document.createElement("div");
        bubble.className = `haku-msg haku-msg-${remitente} haku-msg-type-${tipo}`;

        const hora = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
        const encabezado = remitente === "usuario" ? "Tú" : "Haku";

        // Formato simple de markdown para negritas y listas
        let htmlTexto = escapeHtml(texto)
            .replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>")
            .replace(/`([^`]+)`/g, "<code>$1</code>")
            .replace(/\n/g, "<br>");

        bubble.innerHTML = `
            <div class="msg-header">
                <span class="msg-sender">${encabezado}</span>
                <span class="msg-time">${hora}</span>
            </div>
            <div class="msg-body">${htmlTexto}</div>
        `;

        chatFeed.appendChild(bubble);
        chatFeed.scrollTop = chatFeed.scrollHeight;
    }

    function agregarMensajeSistema(texto) {
        const chatFeed = document.getElementById("haku-chat-feed");
        if (!chatFeed) return;

        const info = document.createElement("div");
        info.className = "haku-msg-sistema";
        info.textContent = texto;
        chatFeed.appendChild(info);
        chatFeed.scrollTop = chatFeed.scrollHeight;
    }

    function mostrarTarjetaPropuesta(propuesta) {
        const contenedor = document.getElementById("haku-propuesta-card");
        if (!contenedor) return;

        const badges = {
            ENTRADA: '<span class="badge badge-success">Entrada de stock</span>',
            SALIDA: '<span class="badge badge-danger">Salida de stock</span>',
            PRESTAMO: '<span class="badge badge-warning">Préstamo de artículo</span>',
            DEVOLUCION_PRESTAMO: '<span class="badge badge-info">Devolución de préstamo</span>',
        };

        const badgeHtml = badges[propuesta.tipo_operacion] || '<span class="badge badge-secondary">Operación</span>';

        contenedor.innerHTML = `
            <div class="propuesta-inner">
                <div class="propuesta-header">
                    <h4>Confirmación de Operación</h4>
                    ${badgeHtml}
                </div>
                <div class="propuesta-detalles">
                    <p><strong>Producto:</strong> ${escapeHtml(propuesta.producto_nombre)} (<code>${escapeHtml(propuesta.producto_codigo)}</code>)</p>
                    <p><strong>Cantidad:</strong> <span class="cantidad-destacada">${propuesta.cantidad}</span> unidades</p>
                    <p><strong>Almacén:</strong> ${escapeHtml(propuesta.almacen_origen_nombre)}</p>
                    ${propuesta.destinatario ? `
                        <div style="background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.1); border-radius:6px; padding:0.5rem 0.75rem; margin:0.5rem 0;">
                            <div style="display:flex; justify-content:space-between; align-items:center;">
                                <span style="font-size:0.8rem; color:var(--text-muted);">Persona / Destinatario:</span>
                                <button type="button" id="haku-btn-corregir-dest" style="background:transparent; border:none; color:var(--primary); font-size:0.75rem; cursor:pointer; text-decoration:underline;">✏️ Corregir nombre</button>
                            </div>
                            <div style="font-size:1.05rem; font-weight:700; color:var(--accent); margin-top:0.2rem;">
                                👤 ${escapeHtml(propuesta.destinatario)}
                            </div>
                        </div>
                    ` : ""}
                    ${propuesta.motivo ? `<p><strong>Motivo:</strong> ${escapeHtml(propuesta.motivo)}</p>` : ""}
                </div>
                <div class="propuesta-acciones">
                    <button type="button" class="btn btn-confirmar" id="haku-btn-confirmar">
                        ✅ Confirmar operación
                    </button>
                    <button type="button" class="btn btn-cancelar" id="haku-btn-cancelar">
                        ❌ Cancelar
                    </button>
                </div>
                <div class="propuesta-nota">
                    También puedes decir <em>"Confirmar"</em> o <em>"Cancelar"</em> por voz.
                </div>
            </div>
        `;

        contenedor.style.display = "block";

        document.getElementById("haku-btn-confirmar")?.addEventListener("click", () => {
            confirmarPropuestaManual(propuesta.id);
        });

        document.getElementById("haku-btn-cancelar")?.addEventListener("click", () => {
            cancelarPropuestaManual(propuesta.id);
        });

        document.getElementById("haku-btn-corregir-dest")?.addEventListener("click", () => {
            mostrarFormularioDestinatario({
                mensaje_pantalla: `Corrige el nombre de la persona para el préstamo de ${propuesta.producto_nombre}:`,
            });
        });
    }

    function mostrarFormularioDestinatario(dataAclaracion) {
        const contenedor = document.getElementById("haku-propuesta-card");
        if (!contenedor) return;

        const candidatos = dataAclaracion.candidatos || [];
        const botonesCandidatos = candidatos.length > 0
            ? `<div style="margin-top:0.5rem; display:flex; flex-wrap:wrap; gap:0.4rem;">
                <span style="font-size:0.75rem; color:var(--text-muted); width:100%;">Candidatos sugeridos:</span>
                ${candidatos.map(c => `<button type="button" class="btn btn-candidato-nom" style="font-size:0.8rem; padding:0.25rem 0.6rem; border:1px solid var(--accent); background:rgba(129,140,248,0.1); border-radius:4px;">👤 ${escapeHtml(c)}</button>`).join("")}
               </div>`
            : "";

        contenedor.innerHTML = `
            <div class="propuesta-inner" style="border-color: var(--warning);">
                <div class="propuesta-header">
                    <h4>Identificación del Destinatario</h4>
                    <span class="badge badge-warning">Entrada Escrita Obligatoria</span>
                </div>
                <div class="propuesta-detalles" style="margin-top:0.5rem;">
                    <p style="color:var(--text-main); font-size:0.9rem;">
                        ${escapeHtml(dataAclaracion.mensaje_pantalla || "Por favor escribe el nombre de la persona para registrar el préstamo:")}
                    </p>
                    ${botonesCandidatos}
                </div>
                <form id="haku-form-destinatario-escrito" style="margin-top:0.75rem; display:flex; gap:0.5rem;">
                    <input type="text" id="haku-input-destinatario-manual" class="input-text" placeholder="Escribe el nombre completo..." required style="flex:1; padding:0.5rem 0.75rem; font-size:0.9rem;" autofocus />
                    <button type="submit" class="btn btn-confirmar" style="padding:0.5rem 1rem;">Guardar</button>
                    <button type="button" id="haku-btn-cancelar-dest" class="btn btn-cancelar" style="padding:0.5rem 0.75rem;">✕</button>
                </form>
            </div>
        `;

        contenedor.style.display = "block";

        const inputNom = document.getElementById("haku-input-destinatario-manual");
        inputNom?.focus();

        // Clic en sugerencias
        contenedor.querySelectorAll(".btn-candidato-nom").forEach(btn => {
            btn.addEventListener("click", () => {
                const nom = btn.textContent.replace("👤", "").trim();
                if (inputNom) inputNom.value = nom;
            });
        });

        // Enviar formulario
        document.getElementById("haku-form-destinatario-escrito")?.addEventListener("submit", (ev) => {
            ev.preventDefault();
            const val = inputNom ? inputNom.value.trim() : "";
            if (!val) return;
            contenedor.style.display = "none";
            agregarMensajeChat("usuario", val);
            enviarComandoHaku(val);
        });

        document.getElementById("haku-btn-cancelar-dest")?.addEventListener("click", () => {
            contenedor.style.display = "none";
            enviarComandoHaku("cancelar");
        });
    }

    function mostrarFormularioCrearProducto(datos) {
        const contenedor = document.getElementById("haku-propuesta-card");
        if (!contenedor) return;

        const categorias = datos.categorias || [];
        const categoriasHtml = categorias.map(c => `<option value="${c.id}">${escapeHtml(c.nombre)} (${escapeHtml(c.prefijo_codigo)})</option>`).join("");
        const almacenes = datos.almacenes || [];
        const almacenesHtml = `<option value="">-- Sin stock inicial / Ninguno --</option>` + almacenes.map(a => `<option value="${a.id}">${escapeHtml(a.nombre)}</option>`).join("");

        contenedor.innerHTML = `
            <div class="propuesta-inner" style="border-color: var(--primary);">
                <div class="propuesta-header">
                    <h4>Registro de Nuevo Producto</h4>
                    <span class="badge badge-info">Formulario</span>
                </div>

                <!-- Visor integrado del escáner de código de barras con cámara -->
                <div id="haku-scanner-container" style="display:none; background:rgba(0,0,0,0.85); border:1px solid var(--accent); border-radius:8px; padding:0.75rem; margin-top:0.75rem; text-align:center;">
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:0.5rem;">
                        <span style="font-size:0.85rem; font-weight:600; color:var(--text-main);">📸 Escáner de Códigos de Barras</span>
                        <button type="button" id="haku-scanner-btn-cerrar" class="btn" style="padding:0.2rem 0.5rem; font-size:0.75rem;">✕ Cerrar visor</button>
                    </div>
                    <video id="haku-scanner-video" style="width:100%; max-height:220px; border-radius:4px; object-fit:cover; background:#000;" playsinline autoplay muted></video>
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-top:0.5rem; gap:0.5rem;">
                        <select id="haku-scanner-select-cam" class="input-text" style="font-size:0.75rem; flex:1;">
                            <option value="">Cámara trasera / por defecto</option>
                        </select>
                        <button type="button" id="haku-scanner-btn-cambiar-cam" class="btn" style="font-size:0.75rem;">🔄 Cambiar</button>
                    </div>
                    <div id="haku-scanner-status" style="font-size:0.8rem; color:var(--text-muted); margin-top:0.4rem;">Iniciando...</div>
                </div>

                <form id="haku-form-crear-producto" class="haku-form-interactivo" style="margin-top: 1rem; display: flex; flex-direction: column; gap: 0.75rem;">
                    <!-- Código de barras y botones de escaneo -->
                    <div style="background:rgba(255,255,255,0.02); border:1px solid rgba(255,255,255,0.08); border-radius:6px; padding:0.6rem;">
                        <label style="display:block; font-size:0.8rem; font-weight:600; color:var(--text-main); margin-bottom:0.25rem;">Código de barras (EAN-13, EAN-8, UPC, Code 128)</label>
                        <div style="display:flex; gap:0.5rem;">
                            <input type="text" id="haku-crear-codigo-barras" class="input-text" style="flex:1;" placeholder="Escribe o escanea el código">
                            <button type="button" id="haku-btn-escanear-camara" class="btn btn-primary" style="white-space:nowrap; font-size:0.8rem;">
                                📷 Escanear con cámara
                            </button>
                            <button type="button" id="haku-btn-verificar-codigo" class="btn" style="white-space:nowrap; font-size:0.8rem;">
                                🔍 Buscar
                            </button>
                        </div>
                        <div id="haku-crear-barcode-msg" style="display:none; font-size:0.8rem; margin-top:0.4rem;"></div>
                    </div>

                    <div>
                        <label style="display:block; font-size:0.8rem; color:var(--text-muted); margin-bottom:0.25rem;">Nombre del producto *</label>
                        <input type="text" id="haku-crear-nombre" required class="input-text" style="width:100%;" placeholder="Ej. Cable HDMI 2.0 5m">
                    </div>
                    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem;">
                        <div>
                            <label style="display:block; font-size:0.8rem; color:var(--text-muted); margin-bottom:0.25rem;">Categoría *</label>
                            <select id="haku-crear-categoria" required class="input-text" style="width:100%; background:var(--bg-main); color:var(--text-main);">
                                ${categoriasHtml}
                            </select>
                        </div>
                        <div>
                            <label style="display:block; font-size:0.8rem; color:var(--text-muted); margin-bottom:0.25rem;">Almacén inicial (opcional)</label>
                            <select id="haku-crear-almacen" class="input-text" style="width:100%; background:var(--bg-main); color:var(--text-main);">
                                ${almacenesHtml}
                            </select>
                        </div>
                    </div>
                    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem;">
                        <div>
                            <label style="display:block; font-size:0.8rem; color:var(--text-muted); margin-bottom:0.25rem;">Stock inicial</label>
                            <input type="number" id="haku-crear-stock-inicial" value="0" min="0" class="input-text" style="width:100%;">
                        </div>
                        <div>
                            <label style="display:block; font-size:0.8rem; color:var(--text-muted); margin-bottom:0.25rem;">Stock mínimo</label>
                            <input type="number" id="haku-crear-stock-minimo" value="0" min="0" class="input-text" style="width:100%;">
                        </div>
                    </div>
                    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem;">
                        <div>
                            <label style="display:block; font-size:0.8rem; color:var(--text-muted); margin-bottom:0.25rem;">Empaque (ej. Caja, Paquete)</label>
                            <input type="text" id="haku-crear-nombre-empaque" class="input-text" style="width:100%;" placeholder="Unidad / Paquete">
                        </div>
                        <div>
                            <label style="display:block; font-size:0.8rem; color:var(--text-muted); margin-bottom:0.25rem;">Uds por empaque</label>
                            <input type="number" id="haku-crear-uds-empaque" min="1" class="input-text" style="width:100%;" placeholder="Ej. 12">
                        </div>
                    </div>
                    <div>
                        <label style="display:block; font-size:0.8rem; color:var(--text-muted); margin-bottom:0.25rem;">Descripción</label>
                        <textarea id="haku-crear-descripcion" class="input-text" style="width:100%; height:60px;" placeholder="Detalles técnicos o notas"></textarea>
                    </div>
                    <div class="propuesta-acciones" style="margin-top: 0.5rem;">
                        <button type="submit" class="btn btn-confirmar">💾 Guardar producto</button>
                        <button type="button" class="btn btn-cancelar" id="haku-btn-cancelar-crear">❌ Cancelar</button>
                    </div>
                </form>
            </div>
        `;
        contenedor.style.display = "block";

        document.getElementById("haku-btn-escanear-camara")?.addEventListener("click", () => {
            iniciarEscanerCamara("haku-crear-codigo-barras", (cod) => {
                procesarCodigoBarrasDetectado(cod, "haku-crear");
            });
        });

        document.getElementById("haku-scanner-btn-cerrar")?.addEventListener("click", () => {
            detenerEscanerCamara();
        });

        document.getElementById("haku-scanner-btn-cambiar-cam")?.addEventListener("click", () => {
            iniciarEscanerCamara("haku-crear-codigo-barras", (cod) => {
                procesarCodigoBarrasDetectado(cod, "haku-crear");
            });
        });

        document.getElementById("haku-btn-verificar-codigo")?.addEventListener("click", () => {
            const val = document.getElementById("haku-crear-codigo-barras").value.trim();
            if (val) procesarCodigoBarrasDetectado(val, "haku-crear");
        });

        document.getElementById("haku-crear-codigo-barras")?.addEventListener("change", (e) => {
            const val = e.target.value.trim();
            if (val) procesarCodigoBarrasDetectado(val, "haku-crear");
        });

        document.getElementById("haku-btn-cancelar-crear")?.addEventListener("click", () => {
            detenerEscanerCamara();
            ocultarTarjetaPropuesta();
            agregarMensajeChat("haku", "Operación de registro cancelada por el usuario.");
        });

        document.getElementById("haku-form-crear-producto")?.addEventListener("submit", async (e) => {
            e.preventDefault();
            const almVal = document.getElementById("haku-crear-almacen")?.value;
            const payload = {
                nombre: document.getElementById("haku-crear-nombre").value.trim(),
                categoria_id: parseInt(document.getElementById("haku-crear-categoria").value, 10),
                almacen_id: almVal ? parseInt(almVal, 10) : null,
                stock_inicial: parseInt(document.getElementById("haku-crear-stock-inicial")?.value, 10) || 0,
                codigo_barras: document.getElementById("haku-crear-codigo-barras").value.trim() || null,
                stock_minimo: parseInt(document.getElementById("haku-crear-stock-minimo").value, 10) || 0,
                nombre_empaque: document.getElementById("haku-crear-nombre-empaque").value.trim(),
                unidades_por_empaque: document.getElementById("haku-crear-uds-empaque").value ? parseInt(document.getElementById("haku-crear-uds-empaque").value, 10) : null,
                descripcion: document.getElementById("haku-crear-descripcion").value.trim(),
            };
            actualizarEstadoVisual("procesando");
            try {
                const resp = await apiFetch(CONFIG.ENDPOINTS.PRODUCTO_CREAR, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload),
                });
                const resData = await resp.json();
                if (resData.error) {
                    mostrarToast(resData.error, "error");
                    agregarMensajeChat("haku", `⚠️ No se pudo crear: ${resData.error}`);
                } else {
                    ocultarTarjetaPropuesta();
                    mostrarToast("Producto registrado exitosamente.", "info");
                    agregarMensajeChat("haku", `✅ ${resData.mensaje_pantalla}`);
                    if (resData.mensaje_verbal) reproducirVozCliente(resData.mensaje_verbal);
                }
            } catch (err) {
                console.error("Error creando producto:", err);
                mostrarToast("Error de conexión al registrar producto.", "error");
            } finally {
                actualizarEstadoVisual("reposo");
            }
        });
    }

    function mostrarFormularioEditarProducto(datos) {
        detenerEscanerCamara();
        const contenedor = document.getElementById("haku-propuesta-card");
        if (!contenedor) return;

        const prod = datos.producto || {};
        const categorias = datos.categorias || [];
        const categoriasHtml = categorias.map(c => `
            <option value="${c.id}" ${c.id === prod.categoria_id ? "selected" : ""}>
                ${escapeHtml(c.nombre)} (${escapeHtml(c.prefijo_codigo)})
            </option>
        `).join("");

        contenedor.innerHTML = `
            <div class="propuesta-inner" style="border-color: var(--accent);">
                <div class="propuesta-header">
                    <h4>Edición de Producto: ${escapeHtml(prod.nombre)}</h4>
                    <span class="badge badge-warning"><code>${escapeHtml(prod.codigo_interno)}</code></span>
                </div>

                <!-- Visor integrado del escáner de cámara para edición -->
                <div id="haku-scanner-container" style="display:none; background:rgba(0,0,0,0.85); border:1px solid var(--accent); border-radius:8px; padding:0.75rem; margin-top:0.75rem; text-align:center;">
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:0.5rem;">
                        <span style="font-size:0.85rem; font-weight:600; color:var(--text-main);">📸 Escáner de Códigos de Barras</span>
                        <button type="button" id="haku-scanner-btn-cerrar" class="btn" style="padding:0.2rem 0.5rem; font-size:0.75rem;">✕ Cerrar visor</button>
                    </div>
                    <video id="haku-scanner-video" style="width:100%; max-height:220px; border-radius:4px; object-fit:cover; background:#000;" playsinline autoplay muted></video>
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-top:0.5rem; gap:0.5rem;">
                        <select id="haku-scanner-select-cam" class="input-text" style="font-size:0.75rem; flex:1;">
                            <option value="">Cámara trasera / por defecto</option>
                        </select>
                        <button type="button" id="haku-scanner-btn-cambiar-cam" class="btn" style="font-size:0.75rem;">🔄 Cambiar</button>
                    </div>
                    <div id="haku-scanner-status" style="font-size:0.8rem; color:var(--text-muted); margin-top:0.4rem;">Iniciando...</div>
                </div>

                <form id="haku-form-editar-producto" class="haku-form-interactivo" style="margin-top: 1rem; display: flex; flex-direction: column; gap: 0.75rem;">
                    <input type="hidden" id="haku-editar-id" value="${prod.id}">
                    <div>
                        <label style="display:block; font-size:0.8rem; color:var(--text-muted); margin-bottom:0.25rem;">Nombre del producto *</label>
                        <input type="text" id="haku-editar-nombre" required class="input-text" style="width:100%;" value="${escapeHtml(prod.nombre || '')}">
                    </div>
                    <div>
                        <label style="display:block; font-size:0.8rem; color:var(--text-muted); margin-bottom:0.25rem;">Categoría *</label>
                        <select id="haku-editar-categoria" required class="input-text" style="width:100%; background:var(--bg-main); color:var(--text-main);">
                            ${categoriasHtml}
                        </select>
                    </div>

                    <!-- Código de barras con botón de escaneo con cámara -->
                    <div style="background:rgba(255,255,255,0.02); border:1px solid rgba(255,255,255,0.08); border-radius:6px; padding:0.6rem;">
                        <label style="display:block; font-size:0.8rem; font-weight:600; color:var(--text-main); margin-bottom:0.25rem;">Código de barras (opcional)</label>
                        <div style="display:flex; gap:0.5rem;">
                            <input type="text" id="haku-editar-codigo-barras" class="input-text" style="flex:1;" value="${escapeHtml(prod.codigo_barras || '')}" placeholder="EAN / UPC">
                            <button type="button" id="haku-btn-escanear-camara-edit" class="btn btn-primary" style="white-space:nowrap; font-size:0.8rem;">
                                📷 Escanear
                            </button>
                            <button type="button" id="haku-btn-verificar-codigo-edit" class="btn" style="white-space:nowrap; font-size:0.8rem;">
                                🔍 Verificar
                            </button>
                        </div>
                        <div id="haku-editar-barcode-msg" style="display:none; font-size:0.8rem; margin-top:0.4rem;"></div>
                    </div>

                    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem;">
                        <div>
                            <label style="display:block; font-size:0.8rem; color:var(--text-muted); margin-bottom:0.25rem;">Stock mínimo</label>
                            <input type="number" id="haku-editar-stock-minimo" min="0" class="input-text" style="width:100%;" value="${prod.stock_minimo !== undefined ? prod.stock_minimo : 0}">
                        </div>
                        <div>
                            <label style="display:block; font-size:0.8rem; color:var(--text-muted); margin-bottom:0.25rem;">Empaque (ej. Caja, Paquete)</label>
                            <input type="text" id="haku-editar-nombre-empaque" class="input-text" style="width:100%;" value="${escapeHtml(prod.nombre_empaque || '')}">
                        </div>
                    </div>
                    <div>
                        <label style="display:block; font-size:0.8rem; color:var(--text-muted); margin-bottom:0.25rem;">Uds por empaque</label>
                        <input type="number" id="haku-editar-uds-empaque" min="1" class="input-text" style="width:100%;" value="${prod.unidades_por_empaque || ''}">
                    </div>
                    <div>
                        <label style="display:block; font-size:0.8rem; color:var(--text-muted); margin-bottom:0.25rem;">Descripción</label>
                        <textarea id="haku-editar-descripcion" class="input-text" style="width:100%; height:60px;">${escapeHtml(prod.descripcion || '')}</textarea>
                    </div>
                    <div class="propuesta-acciones" style="margin-top: 0.5rem;">
                        <button type="submit" class="btn btn-confirmar">💾 Guardar cambios</button>
                        <button type="button" class="btn btn-cancelar" id="haku-btn-cancelar-editar">❌ Cancelar</button>
                    </div>
                </form>
            </div>
        `;
        contenedor.style.display = "block";

        document.getElementById("haku-btn-escanear-camara-edit")?.addEventListener("click", () => {
            iniciarEscanerCamara("haku-editar-codigo-barras", (cod) => {
                procesarCodigoBarrasDetectado(cod, "haku-editar");
            });
        });

        document.getElementById("haku-btn-verificar-codigo-edit")?.addEventListener("click", () => {
            const val = document.getElementById("haku-editar-codigo-barras").value.trim();
            if (val) procesarCodigoBarrasDetectado(val, "haku-editar");
        });

        document.getElementById("haku-btn-cancelar-editar")?.addEventListener("click", () => {
            detenerEscanerCamara();
            ocultarTarjetaPropuesta();
            agregarMensajeChat("haku", "Edición cancelada por el usuario.");
        });

        document.getElementById("haku-form-editar-producto")?.addEventListener("submit", async (e) => {
            e.preventDefault();
            detenerEscanerCamara();
            const payload = {
                producto_id: parseInt(document.getElementById("haku-editar-id").value, 10),
                nombre: document.getElementById("haku-editar-nombre").value.trim(),
                categoria_id: parseInt(document.getElementById("haku-editar-categoria").value, 10),
                codigo_barras: document.getElementById("haku-editar-codigo-barras").value.trim() || null,
                stock_minimo: parseInt(document.getElementById("haku-editar-stock-minimo").value, 10) || 0,
                nombre_empaque: document.getElementById("haku-editar-nombre-empaque").value.trim(),
                unidades_por_empaque: document.getElementById("haku-editar-uds-empaque").value ? parseInt(document.getElementById("haku-editar-uds-empaque").value, 10) : null,
                descripcion: document.getElementById("haku-editar-descripcion").value.trim(),
            };
            actualizarEstadoVisual("procesando");
            try {
                const resp = await apiFetch(CONFIG.ENDPOINTS.PRODUCTO_EDITAR, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload),
                });
                const resData = await resp.json();
                if (resData.error) {
                    mostrarToast(resData.error, "error");
                    agregarMensajeChat("haku", `⚠️ No se pudo actualizar: ${resData.error}`);
                } else {
                    ocultarTarjetaPropuesta();
                    mostrarToast("Producto actualizado con éxito.", "info");
                    agregarMensajeChat("haku", `✅ ${resData.mensaje_pantalla}`);
                    if (resData.mensaje_verbal) reproducirVozCliente(resData.mensaje_verbal);
                }
            } catch (err) {
                console.error("Error actualizando producto:", err);
                mostrarToast("Error de conexión al actualizar producto.", "error");
            } finally {
                actualizarEstadoVisual("reposo");
            }
        });
    }

    function mostrarFormularioCrearUsuario(datos) {
        const contenedor = document.getElementById("haku-propuesta-card");
        if (!contenedor) return;

        const roles = datos.roles || [];
        const rolesHtml = roles.map(r => `<option value="${escapeHtml(r.valor)}">${escapeHtml(r.nombre)}</option>`).join("");
        const almacenes = datos.almacenes || [];
        const almacenesCheckboxes = almacenes.map(a => `
            <label style="display:flex; align-items:center; gap:0.4rem; font-size:0.85rem; cursor:pointer;">
                <input type="checkbox" name="haku-crear-almacenes-multi" value="${a.id}" class="haku-chk-alm">
                ${escapeHtml(a.nombre)}
            </label>
        `).join("");

        contenedor.innerHTML = `
            <div class="propuesta-inner" style="border-color: var(--primary);">
                <div class="propuesta-header">
                    <h4>Registro de Nuevo Usuario</h4>
                    <span class="badge badge-info">Gestión de Usuarios</span>
                </div>
                <div style="font-size: 0.85rem; color: var(--text-muted); margin-bottom: 0.5rem;">
                    Rol de operador: <strong>${escapeHtml(datos.usuario_actor_rol)}</strong>
                </div>
                <form id="haku-form-crear-usuario" class="haku-form-interactivo" style="display: flex; flex-direction: column; gap: 0.75rem;">
                    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem;">
                        <div>
                            <label style="display:block; font-size:0.8rem; color:var(--text-muted); margin-bottom:0.25rem;">Nombre de usuario (login) *</label>
                            <input type="text" id="haku-user-crear-username" required class="input-text" style="width:100%;" placeholder="ej. jpolo">
                        </div>
                        <div>
                            <label style="display:block; font-size:0.8rem; color:var(--text-muted); margin-bottom:0.25rem;">Nombre completo *</label>
                            <input type="text" id="haku-user-crear-nombre" required class="input-text" style="width:100%;" placeholder="ej. Juan Polo">
                        </div>
                    </div>
                    <div>
                        <label style="display:block; font-size:0.8rem; color:var(--text-muted); margin-bottom:0.25rem;">Rol asignado *</label>
                        <select id="haku-user-crear-rol" required class="input-text" style="width:100%; background:var(--bg-main); color:var(--text-main);">
                            ${rolesHtml}
                        </select>
                    </div>
                    <div id="haku-user-crear-almacenes-bloque" style="background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.1); border-radius:6px; padding:0.75rem;">
                        <label style="display:block; font-size:0.8rem; font-weight:600; color:var(--text-main); margin-bottom:0.4rem;">
                            Asignación de Almacenes <span id="haku-user-almacen-req" style="color:var(--danger, #ff4444);">*</span>
                        </label>
                        <div style="margin-bottom:0.5rem;">
                            <label style="display:flex; align-items:center; gap:0.4rem; font-size:0.85rem; font-weight:500; cursor:pointer;">
                                <input type="checkbox" id="haku-user-crear-todos-almacenes">
                                <strong>Acceso a todos los almacenes</strong>
                            </label>
                        </div>
                        <div id="haku-user-crear-lista-almacenes" style="display:flex; flex-direction:column; gap:0.35rem; max-height:140px; overflow-y:auto; padding-left:0.25rem;">
                            ${almacenesCheckboxes}
                        </div>
                    </div>
                    <div>
                        <label style="display:block; font-size:0.8rem; color:var(--text-muted); margin-bottom:0.25rem;">Contraseña inicial *</label>
                        <input type="password" id="haku-user-crear-password" required class="input-text" style="width:100%;" placeholder="Mínimo 8 caracteres, alfanumérica">
                    </div>
                    <div class="propuesta-acciones" style="margin-top: 0.5rem;">
                        <button type="submit" class="btn btn-confirmar">✅ Crear usuario</button>
                        <button type="button" class="btn btn-cancelar" id="haku-btn-cancelar-crear-usuario">❌ Cancelar</button>
                    </div>
                </form>
            </div>
        `;
        contenedor.style.display = "block";

        const selRol = document.getElementById("haku-user-crear-rol");
        const bloqueAlm = document.getElementById("haku-user-crear-almacenes-bloque");
        const chkTodos = document.getElementById("haku-user-crear-todos-almacenes");
        const listaAlm = document.getElementById("haku-user-crear-lista-almacenes");

        if (chkTodos && listaAlm) {
            chkTodos.addEventListener("change", () => {
                const checkboxes = listaAlm.querySelectorAll(".haku-chk-alm");
                checkboxes.forEach(cb => {
                    cb.disabled = chkTodos.checked;
                    if (chkTodos.checked) cb.checked = false;
                });
            });
            listaAlm.querySelectorAll(".haku-chk-alm").forEach(cb => {
                cb.addEventListener("change", () => {
                    if (cb.checked && chkTodos.checked) {
                        chkTodos.checked = false;
                    }
                });
            });
        }

        if (selRol && bloqueAlm) {
            selRol.addEventListener("change", () => {
                if (selRol.value === "USUARIO") {
                    bloqueAlm.style.display = "block";
                } else {
                    bloqueAlm.style.display = "none";
                }
            });
            selRol.dispatchEvent(new Event("change"));
        }

        document.getElementById("haku-btn-cancelar-crear-usuario")?.addEventListener("click", () => {
            ocultarTarjetaPropuesta();
            agregarMensajeChat("haku", "Creación de usuario cancelada.");
        });

        document.getElementById("haku-form-crear-usuario")?.addEventListener("submit", async (e) => {
            e.preventDefault();
            const rolVal = document.getElementById("haku-user-crear-rol").value.trim();
            const accesoTodos = rolVal === "USUARIO" && document.getElementById("haku-user-crear-todos-almacenes").checked;
            const almsSeleccionados = [];
            document.querySelectorAll("#haku-user-crear-lista-almacenes .haku-chk-alm:checked").forEach(cb => {
                almsSeleccionados.push(parseInt(cb.value, 10));
            });

            if (rolVal === "USUARIO" && !accesoTodos && almsSeleccionados.length === 0) {
                mostrarToast("Debes seleccionar al menos un almacén o marcar acceso a todos.", "error");
                return;
            }

            const payload = {
                username: document.getElementById("haku-user-crear-username").value.trim(),
                nombre_completo: document.getElementById("haku-user-crear-nombre").value.trim(),
                rol: rolVal,
                almacen_asignado_id: almsSeleccionados.length > 0 ? almsSeleccionados[0] : null,
                almacenes_asignados_ids: almsSeleccionados,
                acceso_todos_almacenes: accesoTodos,
                password: document.getElementById("haku-user-crear-password").value,
            };

            actualizarEstadoVisual("procesando");
            try {
                const resp = await apiFetch(CONFIG.ENDPOINTS.USUARIO_CREAR, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload),
                });
                const resData = await resp.json();
                if (resData.error) {
                    mostrarToast(resData.error, "error");
                    let detalleTexto = "";
                    if (resData.detalles) {
                        detalleTexto = " " + Object.values(resData.detalles).join("; ");
                    }
                    agregarMensajeChat("haku", `⚠️ No se pudo crear el usuario: ${resData.error}${detalleTexto}`);
                } else {
                    ocultarTarjetaPropuesta();
                    mostrarToast("Usuario creado con éxito.", "info");
                    agregarMensajeChat("haku", `✅ ${resData.mensaje_pantalla}`);
                    if (resData.mensaje_verbal) reproducirVozCliente(resData.mensaje_verbal);
                }
            } catch (err) {
                console.error("Error al crear usuario:", err);
                mostrarToast("Error de conexión al crear usuario.", "error");
            } finally {
                actualizarEstadoVisual("reposo");
            }
        });
    }

    function mostrarFormularioEditarUsuario(datos) {
        const contenedor = document.getElementById("haku-propuesta-card");
        if (!contenedor) return;

        const u = datos.usuario || {};
        const roles = datos.roles || [];
        const rolesHtml = roles.map(r => `<option value="${escapeHtml(r.valor)}" ${u.rol === r.valor ? "selected" : ""}>${escapeHtml(r.nombre)}</option>`).join("");
        const almacenes = datos.almacenes || [];
        const assignedIds = new Set(u.almacenes_asignados_ids || (u.almacen_asignado_id ? [u.almacen_asignado_id] : []));
        const almacenesCheckboxes = almacenes.map(a => `
            <label style="display:flex; align-items:center; gap:0.4rem; font-size:0.85rem; cursor:pointer;">
                <input type="checkbox" name="haku-editar-almacenes-multi" value="${a.id}" class="haku-chk-alm-edit" ${assignedIds.has(a.id) ? "checked" : ""}>
                ${escapeHtml(a.nombre)}
            </label>
        `).join("");

        contenedor.innerHTML = `
            <div class="propuesta-inner" style="border-color: var(--primary);">
                <div class="propuesta-header">
                    <h4>Editar Usuario: ${escapeHtml(u.username || "")}</h4>
                    <span class="badge badge-warning">Edición de Cuenta</span>
                </div>
                <form id="haku-form-editar-usuario" class="haku-form-interactivo" style="display: flex; flex-direction: column; gap: 0.75rem; margin-top: 0.5rem;">
                    <input type="hidden" id="haku-user-editar-id" value="${u.id || ''}">
                    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem;">
                        <div>
                            <label style="display:block; font-size:0.8rem; color:var(--text-muted); margin-bottom:0.25rem;">Nombre de usuario</label>
                            <input type="text" class="input-text" style="width:100%; opacity:0.8;" value="${escapeHtml(u.username || '')}" disabled>
                        </div>
                        <div>
                            <label style="display:block; font-size:0.8rem; color:var(--text-muted); margin-bottom:0.25rem;">Nombre completo *</label>
                            <input type="text" id="haku-user-editar-nombre" required class="input-text" style="width:100%;" value="${escapeHtml(u.nombre_completo || '')}">
                        </div>
                    </div>
                    <div>
                        <label style="display:block; font-size:0.8rem; color:var(--text-muted); margin-bottom:0.25rem;">Rol asignado *</label>
                        <select id="haku-user-editar-rol" required class="input-text" style="width:100%; background:var(--bg-main); color:var(--text-main);">
                            ${rolesHtml}
                        </select>
                    </div>
                    <div id="haku-user-editar-almacenes-bloque" style="background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.1); border-radius:6px; padding:0.75rem;">
                        <label style="display:block; font-size:0.8rem; font-weight:600; color:var(--text-main); margin-bottom:0.4rem;">
                            Asignación de Almacenes
                        </label>
                        <div style="margin-bottom:0.5rem;">
                            <label style="display:flex; align-items:center; gap:0.4rem; font-size:0.85rem; font-weight:500; cursor:pointer;">
                                <input type="checkbox" id="haku-user-editar-todos-almacenes" ${u.acceso_todos_almacenes ? "checked" : ""}>
                                <strong>Acceso a todos los almacenes</strong>
                            </label>
                        </div>
                        <div id="haku-user-editar-lista-almacenes" style="display:flex; flex-direction:column; gap:0.35rem; max-height:140px; overflow-y:auto; padding-left:0.25rem;">
                            ${almacenesCheckboxes}
                        </div>
                    </div>
                    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem;">
                        <div>
                            <label style="display:block; font-size:0.8rem; color:var(--text-muted); margin-bottom:0.25rem;">Estado de la cuenta</label>
                            <select id="haku-user-editar-activo" class="input-text" style="width:100%; background:var(--bg-main); color:var(--text-main);">
                                <option value="true" ${u.is_active ? "selected" : ""}>Activa</option>
                                <option value="false" ${!u.is_active ? "selected" : ""}>Inactiva / Bloqueada</option>
                            </select>
                        </div>
                        <div>
                            <label style="display:block; font-size:0.8rem; color:var(--text-muted); margin-bottom:0.25rem;">Nueva contraseña (opcional)</label>
                            <input type="password" id="haku-user-editar-password" class="input-text" style="width:100%;" placeholder="Dejar en blanco para mantener">
                        </div>
                    </div>
                    <div class="propuesta-acciones" style="margin-top: 0.5rem;">
                        <button type="submit" class="btn btn-confirmar">💾 Guardar cambios</button>
                        <button type="button" class="btn btn-cancelar" id="haku-btn-cancelar-editar-usuario">❌ Cancelar</button>
                    </div>
                </form>
            </div>
        `;
        contenedor.style.display = "block";

        const selRolEdit = document.getElementById("haku-user-editar-rol");
        const bloqueAlmEdit = document.getElementById("haku-user-editar-almacenes-bloque");
        const chkTodosEdit = document.getElementById("haku-user-editar-todos-almacenes");
        const listaAlmEdit = document.getElementById("haku-user-editar-lista-almacenes");

        if (chkTodosEdit && listaAlmEdit) {
            const toggleLista = () => {
                const checkboxes = listaAlmEdit.querySelectorAll(".haku-chk-alm-edit");
                checkboxes.forEach(cb => {
                    cb.disabled = chkTodosEdit.checked;
                    if (chkTodosEdit.checked) cb.checked = false;
                });
            };
            chkTodosEdit.addEventListener("change", toggleLista);
            listaAlmEdit.querySelectorAll(".haku-chk-alm-edit").forEach(cb => {
                cb.addEventListener("change", () => {
                    if (cb.checked && chkTodosEdit.checked) {
                        chkTodosEdit.checked = false;
                        toggleLista();
                    }
                });
            });
            toggleLista();
        }

        if (selRolEdit && bloqueAlmEdit) {
            selRolEdit.addEventListener("change", () => {
                if (selRolEdit.value === "USUARIO") {
                    bloqueAlmEdit.style.display = "block";
                } else {
                    bloqueAlmEdit.style.display = "none";
                }
            });
            selRolEdit.dispatchEvent(new Event("change"));
        }

        document.getElementById("haku-btn-cancelar-editar-usuario")?.addEventListener("click", () => {
            ocultarTarjetaPropuesta();
            agregarMensajeChat("haku", "Modificación cancelada por el usuario.");
        });

        document.getElementById("haku-form-editar-usuario")?.addEventListener("submit", async (e) => {
            e.preventDefault();
            const rolVal = document.getElementById("haku-user-editar-rol").value.trim();
            const accesoTodos = rolVal === "USUARIO" && document.getElementById("haku-user-editar-todos-almacenes").checked;
            const almsSeleccionados = [];
            document.querySelectorAll("#haku-user-editar-lista-almacenes .haku-chk-alm-edit:checked").forEach(cb => {
                almsSeleccionados.push(parseInt(cb.value, 10));
            });

            if (rolVal === "USUARIO" && !accesoTodos && almsSeleccionados.length === 0) {
                mostrarToast("Debes seleccionar al menos un almacén o marcar acceso a todos.", "error");
                return;
            }

            const payload = {
                id: parseInt(document.getElementById("haku-user-editar-id").value, 10),
                nombre_completo: document.getElementById("haku-user-editar-nombre").value.trim(),
                rol: rolVal,
                almacen_asignado_id: almsSeleccionados.length > 0 ? almsSeleccionados[0] : null,
                almacenes_asignados_ids: almsSeleccionados,
                acceso_todos_almacenes: accesoTodos,
                is_active: document.getElementById("haku-user-editar-activo").value === "true",
            };

            const pwd = document.getElementById("haku-user-editar-password").value;
            if (pwd) {
                payload.password = pwd;
            }

            actualizarEstadoVisual("procesando");
            try {
                const resp = await apiFetch(CONFIG.ENDPOINTS.USUARIO_EDITAR, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload),
                });
                const resData = await resp.json();
                if (resData.error) {
                    mostrarToast(resData.error, "error");
                    let detalleTexto = "";
                    if (resData.detalles) {
                        detalleTexto = " " + Object.values(resData.detalles).join("; ");
                    }
                    agregarMensajeChat("haku", `⚠️ No se pudo modificar el usuario: ${resData.error}${detalleTexto}`);
                } else {
                    ocultarTarjetaPropuesta();
                    mostrarToast("Usuario actualizado con éxito.", "info");
                    agregarMensajeChat("haku", `✅ ${resData.mensaje_pantalla}`);
                    if (resData.mensaje_verbal) reproducirVozCliente(resData.mensaje_verbal);
                }
            } catch (err) {
                console.error("Error al editar usuario:", err);
                mostrarToast("Error de conexión al modificar usuario.", "error");
            } finally {
                actualizarEstadoVisual("reposo");
            }
        });
    }

    function ocultarTarjetaPropuesta() {
        const contenedor = document.getElementById("haku-propuesta-card");
        if (contenedor) {
            contenedor.style.display = "none";
            contenedor.innerHTML = "";
        }
    }

    function mostrarToast(mensaje, tipo = "info") {
        const contenedorToast = document.getElementById("haku-toasts") || document.body;
        const toast = document.createElement("div");
        toast.className = `haku-toast haku-toast-${tipo}`;
        toast.textContent = mensaje;
        contenedorToast.appendChild(toast);

        setTimeout(() => {
            toast.classList.add("haku-toast-fadeout");
            setTimeout(() => toast.remove(), 400);
        }, 4000);
    }

    function escapeHtml(texto) {
        if (!texto) return "";
        return String(texto)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }

    // ─────────────────────────────────────────────────────────────────────────
    // ENVÍO DE MENSAJE MANUAL POR TECLADO
    // ─────────────────────────────────────────────────────────────────────────

    function configurarInputManual() {
        const inputTexto = document.getElementById("haku-input-texto");
        const btnEnviar = document.getElementById("haku-btn-enviar");

        if (!inputTexto || !btnEnviar) return;

        function enviar() {
            const val = inputTexto.value.trim();
            if (!val) return;
            inputTexto.value = "";
            agregarMensajeChat("usuario", val);
            enviarComandoHaku(val);
        }

        btnEnviar.addEventListener("click", (e) => {
            e.preventDefault();
            enviar();
        });

        inputTexto.addEventListener("keydown", (e) => {
            if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                enviar();
            }
        });
    }

    // ─────────────────────────────────────────────────────────────────────────
    // INICIALIZACIÓN
    // ─────────────────────────────────────────────────────────────────────────

    function init() {
        configurarPushToTalkTeclado();
        configurarBotonMicrofono();
        configurarInputManual();
        reiniciarTimerInactividad();

        // Consultar estado inicial
        apiFetch(CONFIG.ENDPOINTS.ESTADO)
            .then(res => res.json())
            .then(data => {
                if (data.tiene_propuesta_activa && data.propuesta_activa) {
                    state.propuestaActiva = data.propuesta_activa;
                    mostrarTarjetaPropuesta(data.propuesta_activa);
                }
            })
            .catch(() => {});
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }

    // Exponer API global de conveniencia
    window.HakuVoz = {
        iniciarGrabacion,
        detenerGrabacion,
        enviarComando: enviarComandoHaku,
        confirmarPropuesta: () => state.propuestaActiva && confirmarPropuestaManual(state.propuestaActiva.id),
        cancelarPropuesta: () => state.propuestaActiva && cancelarPropuestaManual(state.propuestaActiva.id),
    };

})();
