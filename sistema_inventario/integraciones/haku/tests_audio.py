"""Pruebas exhaustivas del pipeline de audio y microfonía para Haku Inventario.

Valida:
1. Audio normal (WAV PCM 16kHz).
2. Distintos micrófonos y tasas de muestreo (44.1 kHz, 48 kHz resampleados a 16 kHz).
3. Distancias del micrófono (voz lejana a -35 dBFS normalizada a -18 dBFS, y voz saturada limitada suavemente).
4. Detección de actividad vocal (VAD): descarte de silencio y ruido sin invocar Whisper.
5. Compatibilidad con WebM / Opus y formatos del navegador.
6. Endpoint /api/haku/voz/transcribir/ devolviendo estado controlado SIN_VOZ ante silencio.
7. Simulación de múltiples clientes enviando audio simultáneamente sin saturación ni bloqueos.
"""

from __future__ import annotations

import io
import json
import struct
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import numpy as np
from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from inventario.models import Almacen, Categoria, Existencia, Producto
from integraciones.haku.audio.analyzer import AudioAnalyzer
from integraciones.haku.audio.decoder import AudioDecoder, AudioDecodeError
from integraciones.haku.audio.normalizer import AudioNormalizer
from integraciones.haku.audio.pipeline import AudioPipeline
from integraciones.haku.audio.vad import VoiceActivityDetector
from integraciones.haku.services import procesar_audio_y_transcribir


def _crear_wav_bytes(
    muestras: np.ndarray,
    sample_rate: int = 16000,
    canales: int = 1,
) -> bytes:
    """Genera un buffer binario WAV PCM 16-bit a partir de un array float32 [-1.0, 1.0]."""
    muestras_clamped = np.clip(muestras, -1.0, 1.0)
    int16_data = (muestras_clamped * 32767.0).astype(np.int16)

    bio = io.BytesIO()
    with wave.open(bio, "wb") as wf:
        wf.setnchannels(canales)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(int16_data.tobytes())
    return bio.getvalue()


def _sintetizar_tono_voz(
    duracion_seg: float = 1.0,
    sample_rate: int = 16000,
    frecuencia: float = 220.0,
    amplitud: float = 0.5,
) -> np.ndarray:
    """Genera señal tonal armónica simulando energía de formantes vocales."""
    t = np.linspace(0, duracion_seg, int(sample_rate * duracion_seg), endpoint=False)
    # Fundamental + 2 armónicos típicos de formantes de voz humana
    señal = (
        amplitud * 0.6 * np.sin(2 * np.pi * frecuencia * t)
        + amplitud * 0.3 * np.sin(2 * np.pi * (frecuencia * 2) * t)
        + amplitud * 0.1 * np.sin(2 * np.pi * (frecuencia * 3) * t)
    )
    return señal.astype(np.float32)


class AudioPipelineTestCase(TestCase):
    """Pruebas unitarias y de integración para la capa de preprocesamiento de audio."""

    def test_audio_normal_deteccion_correcta(self):
        """Audio con voz a 16kHz mono debe ser detectado con éxito."""
        tono = _sintetizar_tono_voz(duracion_seg=1.5, sample_rate=16000, amplitud=0.4)
        wav_bytes = _crear_wav_bytes(tono, sample_rate=16000)

        pipeline = AudioPipeline(vad_activado=True, normalizacion_activada=True)
        res = pipeline.procesar(wav_bytes)

        self.assertEqual(res.estado, "VOZ_DETECTADA")
        self.assertIsNotNone(res.audio_limpio)
        self.assertAlmostEqual(res.metricas_procesadas.rms_dbfs, -18.0, delta=2.5)

    def test_distintas_tasas_muestreo_microfonos(self):
        """Audio proveniente de micrófonos a 44.1 kHz o 48 kHz debe resamplearse a 16.000 Hz."""
        for sr in [44100, 48000]:
            tono = _sintetizar_tono_voz(duracion_seg=1.0, sample_rate=sr, amplitud=0.3)
            wav_bytes = _crear_wav_bytes(tono, sample_rate=sr)

            muestras_16k, dur = AudioDecoder.decodificar_a_16k_mono(wav_bytes)
            self.assertEqual(len(muestras_16k), 16000)
            self.assertAlmostEqual(dur, 1.0, delta=0.05)

    def test_silencio_descartado_por_vad(self):
        """Audio que contenga únicamente silencio absoluto (0s) no debe enviarse a Whisper."""
        silencio = np.zeros(16000 * 2, dtype=np.float32)
        wav_bytes = _crear_wav_bytes(silencio, sample_rate=16000)

        pipeline = AudioPipeline()
        res = pipeline.procesar(wav_bytes)

        self.assertEqual(res.estado, "SIN_VOZ")
        self.assertEqual(res.motivo, "AUDIO_SILENCIOSO")
        self.assertIsNone(res.audio_limpio)

    def test_ruido_ambiental_sin_voz_descartado(self):
        """Ruido estático de fondo a muy bajo volumen (-60 dBFS) debe ser rechazado por VAD."""
        np.random.seed(42)
        # Ruido gaussiano de amplitud muy baja (RMS ~ -55 dBFS)
        ruido = np.random.normal(0, 0.002, 16000 * 2).astype(np.float32)
        wav_bytes = _crear_wav_bytes(ruido, sample_rate=16000)

        pipeline = AudioPipeline()
        res = pipeline.procesar(wav_bytes)

        self.assertEqual(res.estado, "SIN_VOZ")
        self.assertIn(res.motivo, ["AUDIO_SILENCIOSO", "SOLO_RUIDO"])

    def test_microfono_lejano_normalizado_a_menos_18_dbfs(self):
        """Voz captada a distancia (baja amplitud, ej. -35 dBFS) debe normalizarse cerca de -18 dBFS."""
        tono_debil = _sintetizar_tono_voz(duracion_seg=1.2, amplitud=0.03)  # Muy bajo volumen
        wav_bytes = _crear_wav_bytes(tono_debil)

        pipeline = AudioPipeline(normalizacion_activada=True, target_rms_dbfs=-18.0)
        res = pipeline.procesar(wav_bytes)

        self.assertEqual(res.estado, "VOZ_DETECTADA")
        self.assertAlmostEqual(res.metricas_procesadas.rms_dbfs, -18.0, delta=2.0)
        self.assertGreater(res.metricas_procesadas.rms_dbfs, res.metricas_originales.rms_dbfs)

    def test_voz_saturada_con_limitador_suave(self):
        """Audio excesivamente fuerte no debe superar el techo dinámico de peak_limit."""
        tono_saturado = _sintetizar_tono_voz(duracion_seg=1.0, amplitud=1.8)  # Por encima del rango
        normalizer = AudioNormalizer(target_rms_dbfs=-18.0, peak_limit=0.92)
        procesado = normalizer.normalizar(tono_saturado)

        peak_max = np.max(np.abs(procesado))
        peak_dbfs = 20.0 * np.log10(peak_max + 1e-9)
        self.assertLessEqual(peak_dbfs, -0.7)  # Respeta el techo suave

    def test_buffer_invalido_devuelve_error_controlado(self):
        """Bytes corruptos no deben lanzar un crash no controlado."""
        datos_corruptos = b"\x00\x11\x22\x33" * 5
        pipeline = AudioPipeline()
        res = pipeline.procesar(datos_corruptos)

        self.assertEqual(res.estado, "SIN_VOZ")


class TranscribirVozEndpointTestCase(TestCase):
    """Pruebas del endpoint HTTP /api/haku/voz/transcribir/."""

    def setUp(self):
        User = get_user_model()
        self.usuario = User.objects.create_user(
            username="test_audio_user",
            email="test_audio@audacia.local",
            password="password123",
            nombre_completo="Usuario Test Audio",
            rol="ADMINISTRADOR",
        )
        self.client = Client()
        self.client.force_login(self.usuario)

    def test_silencio_retorna_estado_sin_voz_controlado(self):
        """Al recibir silencio, la API responde HTTP 200 con estado 'SIN_VOZ', status 'silencio' y mensaje verbal instructivo."""
        silencio = np.zeros(16000, dtype=np.float32)
        wav_bytes = _crear_wav_bytes(silencio)

        resp = self.client.post(
            "/api/haku/voz/transcribir/",
            data=wav_bytes,
            content_type="audio/wav",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "silencio")
        self.assertEqual(data["estado"], "SIN_VOZ")
        self.assertEqual(data["motivo"], "AUDIO_SILENCIOSO")
        self.assertEqual(data["texto"], "")
        self.assertIn("No se detectó voz", data["mensaje_pantalla"])
        self.assertIn("No escuché ningún comando", data["mensaje_verbal"])

    def test_silencio_vad_no_llega_a_procesamiento_de_intencion(self):
        """Audio silencioso es descartado por VAD en el endpoint STT y no llega a procesar intención ni comandos."""
        from unittest.mock import patch

        silencio = np.zeros(16000, dtype=np.float32)
        wav_bytes = _crear_wav_bytes(silencio)

        with patch("integraciones.haku.services.procesar_comando_inventario") as mock_cmd:
            resp = self.client.post(
                "/api/haku/voz/transcribir/",
                data=wav_bytes,
                content_type="audio/wav",
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["status"], "silencio")
            self.assertEqual(data["estado"], "SIN_VOZ")
            # El procesador de comandos e intenciones NO debe ser invocado
            mock_cmd.assert_not_called()



    def test_audio_vacio_retorna_400(self):
        """Cuerpo de petición sin bytes retorna 400."""
        resp = self.client.post(
            "/api/haku/voz/transcribir/",
            data=b"",
            content_type="audio/wav",
        )
        self.assertEqual(resp.status_code, 400)

    def test_grabacion_breve_valida_con_datos_audio(self):
        """Una grabación breve válida (~0.5s - 1.0s) con datos de audio no se rechaza por brevedad."""
        from unittest.mock import patch
        tono = _sintetizar_tono_voz(duracion_seg=0.6, amplitud=0.35)
        wav_bytes = _crear_wav_bytes(tono)

        with patch("integraciones.haku.audio.transcriber.HakuTranscriber.transcribir", return_value="hola buenos dias"):
            resp = self.client.post(
                "/api/haku/voz/transcribir/",
                data=wav_bytes,
                content_type="audio/wav",
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data.get("estado"), "VOZ_DETECTADA")
            self.assertEqual(data.get("status"), "ok")
            self.assertEqual(data.get("texto"), "hola buenos dias")
            self.assertIn("metricas", data)
            self.assertAlmostEqual(data["metricas"]["duracion_seg"], 0.6, delta=0.1)

    def test_grabacion_invalida_sin_audio_utilizable(self):
        """Buffer casi vacío (<100 bytes corruptos) es rechazado por el pipeline."""
        resp = self.client.post(
            "/api/haku/voz/transcribir/",
            data=b"corrupt_tiny_data",
            content_type="audio/webm",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data.get("estado"), "SIN_VOZ")
        self.assertEqual(data.get("status"), "silencio")

    def test_concurrencia_multiples_clientes_audio(self):
        """Simulación de 5 clientes enviando audio simultáneamente no satura ni bloquea el servicio."""
        tono = _sintetizar_tono_voz(duracion_seg=0.5, amplitud=0.3)
        wav_bytes = _crear_wav_bytes(tono)

        def enviar_audio_cliente(idx: int) -> dict:
            return procesar_audio_y_transcribir(wav_bytes)

        from unittest.mock import patch

        with patch("integraciones.haku.audio.transcriber.HakuTranscriber.transcribir", return_value="consulta stock cable"):
            with ThreadPoolExecutor(max_workers=5) as executor:
                resultados = list(executor.map(enviar_audio_cliente, range(5)))

        self.assertEqual(len(resultados), 5)
        for r in resultados:
            self.assertEqual(r["estado"], "VOZ_DETECTADA")
            self.assertIn("metricas", r)

    def test_audio_voz_humana_produce_voz_detectada_y_texto_no_vacio(self):
        """Audio WAV con voz humana real produce VOZ_DETECTADA y transcripción no vacía."""
        from pathlib import Path
        from django.conf import settings

        ruta_audio = Path(settings.BASE_DIR).parent / "prueba.wav"
        if not ruta_audio.exists():
            self.skipTest("Archivo prueba.wav no disponible en raíz")

        wav_bytes = ruta_audio.read_bytes()
        resp = self.client.post(
            "/api/haku/voz/transcribir/",
            data=wav_bytes,
            content_type="audio/wav",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data.get("estado"), "VOZ_DETECTADA")
        self.assertEqual(data.get("status"), "ok")
        self.assertGreater(len(data.get("texto", "").strip()), 0, "El texto transcrito no debe ser vacío")

    def test_error_transcripcion_retorna_status_diferenciado(self):
        """Fallo en el motor STT retorna status 'error_transcripcion' claramente diferenciado de silencio."""
        from unittest.mock import patch
        from integraciones.haku.audio.transcriber import TranscripcionError

        tono = _sintetizar_tono_voz(duracion_seg=1.0, amplitud=0.4)
        wav_bytes = _crear_wav_bytes(tono)

        with patch("integraciones.haku.audio.transcriber.HakuTranscriber.transcribir", side_effect=TranscripcionError("Fallo motor STT")):
            resp = self.client.post(
                "/api/haku/voz/transcribir/",
                data=wav_bytes,
                content_type="audio/wav",
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data.get("status"), "error_transcripcion")
            self.assertEqual(data.get("estado"), "ERROR_TRANSCRIPCION")
            self.assertEqual(data.get("motivo"), "ERROR_STT")
            self.assertIn("problema al transcribir", data.get("mensaje_pantalla", ""))


class HakuTTSSintetizadorTestCase(TestCase):
    """Pruebas obligatorias del motor TTS neuronal desacoplado de Haku Inventario."""

    def setUp(self):
        User = get_user_model()
        self.usuario = User.objects.create_user(
            username="tts_operator",
            email="tts_operator@audacia.local",
            password="password123",
            nombre_completo="Carlos Operario",
            rol="ADMINISTRADOR",
        )
        self.client = Client()

    def test_a_generacion_correcta_tts_propio(self):
        """A) Generación correcta: 'El inventario contiene cinco unidades disponibles.'
        Genera WAV válido >0 bytes, PCM 16-bit, 16 kHz, Mono."""
        from integraciones.haku.audio.synthesizer import HakuSynthesizer

        synth = HakuSynthesizer.obtener_instancia()
        texto = "El inventario contiene cinco unidades disponibles."
        wav_bytes = synth.sintetizar(texto)

        self.assertIsInstance(wav_bytes, bytes)
        self.assertGreater(len(wav_bytes), 44, "El archivo WAV debe tener tamaño > 0 y cabecera")
        self.assertTrue(wav_bytes.startswith(b"RIFF"), "El archivo debe ser un RIFF WAV válido")

        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            self.assertEqual(wf.getnchannels(), 1, "Debe ser Mono (1 canal)")
            self.assertEqual(wf.getframerate(), 16000, "Debe ser a 16.000 Hz")
            self.assertEqual(wf.getsampwidth(), 2, "Debe ser PCM de 16 bits (2 bytes por muestra)")
            duracion = wf.getnframes() / wf.getframerate()
            self.assertGreater(duracion, 1.0, "La frase debe durar más de 1 segundo")

    def test_b_integracion_endpoint_sintetizar(self):
        """B) Integración endpoint: POST /api/haku/voz/sintetizar/ responde HTTP 200 y audio/wav."""
        self.client.force_login(self.usuario)
        resp = self.client.post(
            "/api/haku/voz/sintetizar/",
            data=json.dumps({"texto": "El inventario contiene cinco unidades disponibles."}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "audio/wav")
        self.assertTrue(resp.content.startswith(b"RIFF"))
        self.assertGreater(len(resp.content), 1000)

        with wave.open(io.BytesIO(resp.content), "rb") as wf:
            self.assertEqual(wf.getnchannels(), 1)
            self.assertEqual(wf.getframerate(), 16000)
            self.assertEqual(wf.getsampwidth(), 2)

    def test_c_error_controlado_simulado_sin_fallback_sinusoidal(self):
        """C) Error controlado: Al simular fallo del motor TTS responde status 'error_tts' sin tono sinusoidal."""
        from unittest.mock import patch
        from integraciones.haku.audio.synthesizer import HakuSynthesizerError

        self.client.force_login(self.usuario)
        with patch("integraciones.haku.audio.synthesizer.HakuSynthesizer.sintetizar", side_effect=HakuSynthesizerError("Fallo simulado de ONNX Runtime")):
            resp = self.client.post(
                "/api/haku/voz/sintetizar/",
                data=json.dumps({"texto": "Texto que debe fallar controladamente"}),
                content_type="application/json",
            )
            # Debe responder con error_tts y código 503 (Servicio no disponible)
            self.assertEqual(resp.status_code, 503)
            data = resp.json()
            self.assertEqual(data.get("status"), "error_tts")
            self.assertIn("La síntesis de voz no está disponible actualmente", data.get("mensaje", ""))
            # Verificar que NO devolvió audio WAV
            self.assertNotEqual(resp.get("Content-Type"), "audio/wav")

    def test_control_calidad_normalizar_para_tts(self):
        """Verifica que normalizar_para_tts elimine markdown, emojis y expanda números y códigos."""
        from integraciones.haku.services import normalizar_para_tts

        # 1. Emojis y Markdown
        t1 = normalizar_para_tts("✅ Se realizaron **3** movimientos.")
        self.assertEqual(t1, "Se realizaron tres movimientos.")

        # 2. Código de inventario (CAB-0001 -> código CAB guion cero cero cero uno)
        t2 = normalizar_para_tts("Verificar producto CAB-0001 en estante.")
        self.assertEqual(t2, "Verificar producto código CAB guion cero cero cero uno en estante.")

        # 3. AudacIA
        t3 = normalizar_para_tts("Inventario AudacIA central.")
        self.assertEqual(t3, "Inventario Audacia central.")

        # 4. Expansión fonética de números (3 -> tres, 12 -> doce, 45 -> cuarenta y cinco)
        self.assertEqual(normalizar_para_tts("Hay 3 en stock."), "Hay tres en stock.")
        self.assertEqual(normalizar_para_tts("Hay 12 unidades."), "Hay doce unidades.")
        self.assertEqual(normalizar_para_tts("Quedan 45 cables."), "Quedan cuarenta y cinco cables.")

    def test_regresion_stt_sin_alucinaciones_en_fragmentos_cortos_o_ruido(self):
        """Verifica que fragmentos de audio ultracortos o ruido blanco no provoquen alucinaciones."""
        from integraciones.haku.audio.transcriber import HakuTranscriber

        t = HakuTranscriber.obtener_instancia()

        # 1. Fragmento ultracorto (150 ms)
        audio_corto = np.random.uniform(-0.1, 0.1, int(16000 * 0.15)).astype(np.float32)
        res_corto = t.transcribir(audio_corto)
        self.assertEqual(res_corto, "", "Fragmentos inferiores a 250ms deben descartarse sin alucinar")

        # 2. Ruido ambiente de 500 ms filtrado por VAD
        ruido = np.random.uniform(-0.02, 0.02, int(16000 * 0.5)).astype(np.float32)
        res_ruido = t.transcribir(ruido)
        self.assertNotIn("¡Tú voy a ir a esto!", res_ruido)
        self.assertNotIn("¡Pero! ¡Tienes!", res_ruido)
        self.assertEqual(res_ruido, "", "Ruido blanco no debe producir alucinaciones")

    def test_regresion_intencion_consulta_inventario_con_y_sin_saludo(self):
        """Garantiza que 'consulta el inventario' y 'Hola, consulta el inventario' se clasifiquen como CONSULTAR_RESUMEN_INVENTARIO."""
        from integraciones.haku.lenguaje.normalizador import NormalizadorLenguajeHaku, IntencionTipo

        u = self.usuario
        for frase in ["consulta el inventario", "consultar el inventario", "Hola, consulta el inventario", "Hola haku, cómo va el inventario"]:
            res = NormalizadorLenguajeHaku.interpretar(frase, usuario=u)
            self.assertEqual(
                res.tipo,
                IntencionTipo.CONSULTAR_RESUMEN_INVENTARIO,
                f"La frase '{frase}' debe mapear a CONSULTAR_RESUMEN_INVENTARIO, se obtuvo {res.tipo}"
            )

    def test_decodificacion_y_pipeline_webm_opus_silencio_real(self):
        """Verifica que un payload WebM Opus con ceros (silencio) se decodifique a muestras exactas y sea rechazado como AUDIO_SILENCIOSO."""
        import subprocess
        from integraciones.haku.audio.decoder import AudioDecoder
        from integraciones.haku.audio.analyzer import AudioAnalyzer
        from integraciones.haku.audio.pipeline import AudioPipeline

        # 1.86s de silencio simulando captura de micrófono apagado o silenciado en navegador
        sr = 48000
        duration = 1.86
        silence = np.zeros(int(sr * duration), dtype=np.float32)

        p = subprocess.Popen(
            ["ffmpeg", "-y", "-f", "f32le", "-ar", str(sr), "-ac", "1", "-i", "pipe:0", "-c:a", "libopus", "-b:a", "32k", "-f", "webm", "pipe:1"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        webm_silence, _ = p.communicate(silence.tobytes())

        # Decodificación
        samples, dur = AudioDecoder.decodificar_a_16k_mono(webm_silence)
        self.assertAlmostEqual(dur, 1.86, delta=0.1)
        self.assertEqual(len(samples), int(16000 * 1.86))
        self.assertAlmostEqual(float(np.max(np.abs(samples))), 0.0, places=4)

        # Métrica de audio
        metricas = AudioAnalyzer.analizar(samples)
        self.assertLessEqual(metricas.rms_dbfs, -100.0)

        # Pipeline rechaza con AUDIO_SILENCIOSO
        pipeline = AudioPipeline.desde_settings()
        res = pipeline.procesar(webm_silence)
        self.assertEqual(res.estado, "SIN_VOZ")
        self.assertEqual(res.motivo, "AUDIO_SILENCIOSO")


