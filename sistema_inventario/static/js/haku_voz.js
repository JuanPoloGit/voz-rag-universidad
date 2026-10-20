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
    // GRABACIÓN DE VOZ (MICROFONO CLIENTE)
    // ─────────────────────────────────────────────────────────────────────────

    async function iniciarGrabacion() {
        if (state.grabando || state.procesando || state.iniciandoGrabacion) return;

        state.iniciandoGrabacion = true;
        state.detencionPendiente = false;

        try {
            if (!state.streamMicrofono) {
                state.streamMicrofono = await navigator.mediaDevices.getUserMedia({
                    audio: {
                        echoCancellation: true,
                        noiseSuppression: true,
                        sampleRate: 16000,
                    },
                });
            }

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
                ? new MediaRecorder(state.streamMicrofono, { mimeType })
                : new MediaRecorder(state.streamMicrofono);

            state.mediaRecorder.ondataavailable = (event) => {
                if (event.data && event.data.size > 0) {
                    state.audioChunks.push(event.data);
                }
            };

            state.mediaRecorder.onstop = procesarAudioGrabado;

            state.mediaRecorder.start(250); // trozos cada 250ms
            state.grabando = true;
            state.iniciandoGrabacion = false;
            actualizarEstadoVisual("grabando");

            // Si se solicitó detención mientras llamábamos a start()
            if (state.detencionPendiente) {
                state.detencionPendiente = false;
                detenerGrabacion();
            }
        } catch (error) {
            state.iniciandoGrabacion = false;
            state.detencionPendiente = false;
            console.error("Error al acceder al micrófono:", error);
            mostrarToast("No se pudo acceder al micrófono. Verifica los permisos del navegador o el certificado HTTPS.", "error");
            actualizarEstadoVisual("reposo");
        }
    }

    function detenerGrabacion() {
        if (state.iniciandoGrabacion) {
            state.detencionPendiente = true;
            return;
        }
        if (!state.grabando || !state.mediaRecorder) return;
        state.grabando = false;
        actualizarEstadoVisual("procesando");
        try {
            if (state.mediaRecorder.state !== "inactive") {
                state.mediaRecorder.stop();
            }
        } catch (e) {
            console.error("Error al detener grabador:", e);
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

    async function procesarAudioGrabado() {
        if (state.audioChunks.length === 0) {
            actualizarEstadoVisual("reposo");
            return;
        }

        const mime = (state.mediaRecorder && state.mediaRecorder.mimeType) || "audio/webm";
        const audioBlob = new Blob(state.audioChunks, { type: mime });
        state.audioChunks = [];

        // Filtro: si el audio dura menos de 300ms o es insignificante (< 1000 bytes), descartar (clic accidental)
        if (audioBlob.size < 1000) {
            actualizarEstadoVisual("reposo");
            return;
        }

        state.procesando = true;
        actualizarEstadoVisual("procesando");

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

            // 2. Manejo explícito de silencio VAD
            if (dataSTT.status === "silencio" || dataSTT.estado === "SIN_VOZ") {
                const mensajeAviso = dataSTT.mensaje_pantalla || "No se detectó voz audible en el micrófono.";
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
                    ${propuesta.destinatario ? `<p><strong>Destinatario:</strong> ${escapeHtml(propuesta.destinatario)}</p>` : ""}
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
