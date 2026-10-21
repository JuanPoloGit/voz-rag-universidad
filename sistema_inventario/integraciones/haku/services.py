"""Servicios del carril 4 — Haku Inventario, API Interna y Voz Web.

Responsabilidades clave:
1. Cola segura y Lock para convivir con Haku Visitante sin colisionar en GPU.
2. Normalización fonética para síntesis de voz (Piper TTS: 'AudacIA' -> 'Audacia').
3. Generación de audio WAV para el cliente (suena exclusivamente en el navegador).
4. Transcripción de voz (faster-whisper) desde el navegador.
5. Procesamiento de comandos de inventario en lenguaje natural (consultas y mutaciones).
6. Motor de pre-confirmación en dos pasos para mutaciones de inventario.
7. Ejecución atómica y segura de movimientos con select_for_update(), idempotencia
   y auditoría con marca via_haku=True.
"""

from __future__ import annotations

import io
import json
import logging
import re
import struct
import threading
import time
import uuid
import wave
from contextlib import contextmanager
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from cuentas.models import UsuarioAudacIA
from inventario.models import (
    Almacen,
    Auditoria,
    Categoria,
    DevolucionPrestamo,
    Existencia,
    Movimiento,
    Prestamo,
    Producto,
    Ubicacion,
)

logger = logging.getLogger("integraciones.haku")

HAKU_PROPUESTA_TIMEOUT_SEGUNDOS = getattr(
    settings, "HAKU_PROPOSAL_TIMEOUT", getattr(settings, "HAKU_PROPUESTA_TIMEOUT_SEGUNDOS", 1800)
)  # 30 minutos por defecto (transaccional)
# 1. CONTROL DE CONCURRENCIA INTRA-PROCESO PARA TRANSCRIPCIÓN Y AUDIO (DJANGO)
# ═════════════════════════════════════════════════════════════════════════════

# Lock reentrante intra-proceso para serializar llamadas de transcripción/audio dentro de los hilos de Django.
# NOTA TÉCNICA: threading.RLock() es exclusivo del proceso del worker de Django. No coordina
# procesos independientes de Windows (como run_hacu.py o llama-server.exe). La serialización global
# del LLM se gobierna a nivel de red en llama-server.exe (--parallel 1/2), y la coexistencia con
# Faster-Whisper depende del driver CUDA y el fallback a CPU int8 en HakuTranscriber.
HAKU_GPU_LOCK = threading.RLock()


class HakuOcupadoError(Exception):
    """Lanzada cuando otro hilo del servidor web retiene el lock de transcripción de audio."""
    pass


@contextmanager
def adquirir_lock_haku(timeout: float = 12.0):
    """Context manager para adquirir el lock de Haku con timeout seguro."""
    adquirido = HAKU_GPU_LOCK.acquire(timeout=timeout)
    if not adquirido:
        logger.warning("Timeout esperando lock de Haku (ocupado por Visitante o hilo previo)")
        raise HakuOcupadoError("Haku ocupado procesando otra solicitud. Intenta nuevamente.")
    try:
        yield
    finally:
        HAKU_GPU_LOCK.release()



# ═════════════════════════════════════════════════════════════════════════════
# 2. NORMALIZACIÓN FONÉTICA PARA SÍNTESIS DE VOZ (PIPER TTS)
# ═════════════════════════════════════════════════════════════════════════════

NUMEROS_TEXTO_ES = {
    0: "cero", 1: "un", 2: "dos", 3: "tres", 4: "cuatro", 5: "cinco",
    6: "seis", 7: "siete", 8: "ocho", 9: "nueve", 10: "diez",
    11: "once", 12: "doce", 13: "trece", 14: "catorce", 15: "quince",
    16: "dieciséis", 17: "diecisiete", 18: "dieciocho", 19: "diecinueve",
    20: "veinte", 21: "veintiuno", 22: "veintidós", 23: "veintitrés",
    24: "veinticuatro", 25: "veinticinco", 26: "veintiséis", 27: "veintisiete",
    28: "veintiocho", 29: "veintinueve", 30: "treinta", 40: "cuarenta",
    50: "cincuenta", 60: "sesenta", 70: "setenta", 80: "ochenta", 90: "noventa",
    100: "cien",
}


def _numero_a_letras(n: int) -> str:
    """Convierte un número entero positivo a palabras en español."""
    if n in NUMEROS_TEXTO_ES:
        return NUMEROS_TEXTO_ES[n]
    if n < 100:
        dec = (n // 10) * 10
        uni = n % 10
        return f"{NUMEROS_TEXTO_ES[dec]} y {NUMEROS_TEXTO_ES[uni]}"
    if n < 1000:
        cen = (n // 100) * 100
        resto = n % 100
        pref = "ciento" if cen == 100 else {
            200: "doscientos", 300: "trescientos", 400: "cuatrocientos",
            500: "quinientos", 600: "seiscientos", 700: "setecientos",
            800: "ochocientos", 900: "novecientos"
        }.get(cen, str(cen))
        return f"{pref} {_numero_a_letras(resto)}" if resto > 0 else pref
    return str(n)


DIGITOS_A_LETRAS = {
    "0": "cero",
    "1": "uno",
    "2": "dos",
    "3": "tres",
    "4": "cuatro",
    "5": "cinco",
    "6": "seis",
    "7": "siete",
    "8": "ocho",
    "9": "nueve",
}


def _formatear_codigo_tts(m: re.Match) -> str:
    """Convierte códigos de inventario (ej. CAB-0001 -> código CAB guion cero cero cero uno)."""
    prefijo = m.group(1)
    digitos = m.group(2)
    digitos_leidos = " ".join(DIGITOS_A_LETRAS.get(d, d) for d in digitos)
    return f"código {prefijo} guion {digitos_leidos}"


def normalizar_para_tts(texto: str) -> str:
    """Normaliza texto formal para que Piper TTS lo pronuncie con prosodia profesional.

    Reglas de calidad:
    - Remueve markdown (negritas, cursivas, encabezados, viñetas, bloques de código).
    - Remueve emojis y símbolos especiales no verbales.
    - Convierte códigos de inventario (ej. CAB-0001 -> código CAB guion cero cero cero uno).
    - Pronunciación de AudacIA -> Audacia.
    - Expansión de números cardinales aislados (ej. **3** -> tres).
    """
    if not texto:
        return ""

    resultado = texto

    # 1. Limpieza de emojis y caracteres gráficos no verbales
    resultado = re.sub(r"[\U00010000-\U0010ffff\u2600-\u27bf\u2300-\u23ff\u2b50-\u2b55]", "", resultado)

    # 2. Reemplazo fonético para AudacIA
    resultado = re.sub(r"\bAudacIA\b", "Audacia", resultado, flags=re.IGNORECASE)

    # 3. Limpieza de markdown
    resultado = re.sub(r"\*\*([^*]+)\*\*", r"\1", resultado)
    resultado = re.sub(r"\*([^*]+)\*", r"\1", resultado)
    resultado = re.sub(r"`([^`]+)`", r"\1", resultado)
    resultado = re.sub(r"^#+\s*", "", resultado, flags=re.MULTILINE)
    resultado = re.sub(r"^\s*[-*•]\s*", "", resultado, flags=re.MULTILINE)

    # 4. Códigos de inventario (CAB-0001 -> código CAB guion cero cero cero uno)
    resultado = re.sub(
        r"\b([A-Z]{2,6})-(\d{2,6})\b",
        _formatear_codigo_tts,
        resultado,
    )

    # 5. Expansión de números enteros cardinales aislados (evitando códigos con guion)
    def _reemplazar_numero(m: re.Match) -> str:
        num_str = m.group(1)
        if len(num_str) > 1 and num_str.startswith("0"):
            return num_str  # Códigos con ceros a la izquierda se dejan para pronunciar dígito a dígito
        try:
            val = int(num_str)
            if val <= 999:
                return _numero_a_letras(val)
        except ValueError:
            pass
        return num_str

    resultado = re.sub(r"(?<!guion\s)(?<!código\s)\b(\d+)\b", _reemplazar_numero, resultado)

    # 6. Colapsar espacios múltiples
    resultado = re.sub(r"\s+", " ", resultado).strip()
    return resultado


# ═════════════════════════════════════════════════════════════════════════════
# 3. SÍNTESIS DE AUDIO PARA EL CLIENTE (AUDIO EN NAVEGADOR EXCLUSIVO)
# ═════════════════════════════════════════════════════════════════════════════


def sintetizar_audio_bytes(texto: str) -> bytes:
    """Sintetiza texto a audio WAV en memoria usando HakuSynthesizer.

    REGLAS INNEGOCIABLES:
    1. Capa fina: texto -> HakuSynthesizer -> WAV bytes.
    2. NO reproduce en altavoces del servidor físico (reproducción exclusiva en el cliente).
    3. NO depende de hacu/ (Haku Visitante).
    4. NO devuelve tonos sinusoidales de fallback; lanza HakuSynthesizerError en caso de fallo.
    """
    texto_normalizado = normalizar_para_tts(texto)
    if not texto_normalizado:
        from .audio.synthesizer import HakuSynthesizerError
        raise HakuSynthesizerError("Texto de entrada vacío para síntesis de voz.")

    from .audio.synthesizer import HakuSynthesizer
    synth = HakuSynthesizer.obtener_instancia()
    return synth.sintetizar(texto_normalizado)


# ═════════════════════════════════════════════════════════════════════════════
# 4. TRANSCRIPCIÓN DE VOZ CLIENTE (FASTER-WHISPER)
# ═════════════════════════════════════════════════════════════════════════════


def procesar_audio_y_transcribir(audio_bytes: bytes) -> dict[str, Any]:
    """Procesa el buffer de audio (pipeline completo: decode -> analyzer -> VAD -> normalizer)
    y transcribe con faster-whisper únicamente si se detecta voz válida.

    Retorna un diccionario con:
    - estado: "VOZ_DETECTADA" | "SIN_VOZ"
    - motivo: "VOZ_DETECTADA" | "AUDIO_SILENCIOSO" | "SOLO_RUIDO" | "ERROR_DECODIFICACION"
    - texto: str
    - metricas: dict
    """
    if not audio_bytes:
        return {
            "estado": "SIN_VOZ",
            "motivo": "BUFFER_VACIO",
            "texto": "",
            "metricas": {},
        }

    from .audio.pipeline import AudioPipeline
    pipeline = AudioPipeline.desde_settings()
    resultado_proc = pipeline.procesar(audio_bytes)

    if resultado_proc.estado == "SIN_VOZ":
        logger.info("Audio descartado por pipeline: %s", resultado_proc.motivo)
        return {
            "estado": "SIN_VOZ",
            "motivo": resultado_proc.motivo,
            "texto": "",
            "metricas": resultado_proc.to_dict(),
        }

    # Si se detectó voz válida, procedemos a transcribir usando HakuTranscriber
    samples_float32 = resultado_proc.audio_limpio
    texto_transcrito = ""

    from .audio.transcriber import HakuTranscriber, TranscripcionError

    try:
        transcriptor = HakuTranscriber.obtener_instancia()
        if transcriptor.es_cuda():
            with adquirir_lock_haku(timeout=30.0):
                texto_transcrito = transcriptor.transcribir(samples_float32)
        else:
            texto_transcrito = transcriptor.transcribir(samples_float32)
    except HakuOcupadoError:
        logger.warning("Haku ocupado por otro proceso al intentar transcribir")
        raise
    except TranscripcionError as err:
        logger.error("Error en motor STT de Haku: %s", err)
        return {
            "estado": "ERROR_TRANSCRIPCION",
            "motivo": "ERROR_STT",
            "texto": "",
            "error": "Fallo en motor de transcripción de voz.",
            "metricas": resultado_proc.to_dict(),
        }
    except Exception as exc:
        logger.exception("Error inesperado en transcripción faster-whisper: %s", exc)
        return {
            "estado": "ERROR_TRANSCRIPCION",
            "motivo": "ERROR_STT",
            "texto": "",
            "error": "Error interno en procesamiento de audio.",
            "metricas": resultado_proc.to_dict(),
        }

    return {
        "estado": "VOZ_DETECTADA",
        "motivo": "VOZ_DETECTADA",
        "texto": texto_transcrito,
        "metricas": resultado_proc.to_dict(),
    }


def transcribir_audio_bytes(audio_bytes: bytes) -> str:
    """Transcribe un archivo de audio (WAV/WebM) recibido del navegador cliente.

    Usa faster-whisper con el lock de Haku para evitar conflictos de VRAM con el LLM.
    """
    res = procesar_audio_y_transcribir(audio_bytes)
    return res.get("texto", "")


# ═════════════════════════════════════════════════════════════════════════════
# 5. PARSER Y PROCESADOR DE COMANDOS DE INVENTARIO EN LENGUAJE NATURAL
# ═════════════════════════════════════════════════════════════════════════════


def _extraer_numero(texto: str) -> int | None:
    """Extrae un número entero de una frase en español."""
    # Números en dígitos
    coincidencia = re.search(r"\b(\d+)\b", texto)
    if coincidencia:
        return int(coincidencia.group(1))

    # Palabras numéricas comunes
    palabras = {
        "un": 1, "una": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
        "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11,
        "doce": 12, "quince": 15, "veinte": 20, "veinticinco": 25, "treinta": 30,
        "cincuenta": 50, "cien": 100,
    }
    for palabra, valor in palabras.items():
        if re.search(rf"\b{palabra}\b", texto, re.IGNORECASE):
            return valor
    return None


def _buscar_producto(termino: str, usuario: UsuarioAudacIA) -> Producto | None:
    """Busca un producto por código interno, código de barras o coincidencia en nombre."""
    if not termino:
        return None

    # Limpiar signos de puntuación e interrogación
    termino_limpio = re.sub(r"[¿?¡!.,:;()\[\]\"'`]", " ", termino).strip()
    if not termino_limpio:
        return None

    from .lenguaje.normalizador import NormalizadorLenguajeHaku
    prod, _, _ = NormalizadorLenguajeHaku._resolver_producto_en_texto(termino_limpio, usuario)
    return prod


def _validar_acceso_almacen(usuario: UsuarioAudacIA, almacen_intencion: Almacen | None) -> tuple[Almacen | None, dict[str, Any] | None]:
    """Valida los permisos de acceso al almacén solicitado.
    
    Retorna:
    - (almacen_filtrado, None) si el acceso es válido.
    - (None, dict_error) si el usuario normal intentó consultar un almacén no autorizado
      o si no tiene almacén asignado.
    """
    if almacen_intencion:
        if not usuario.tiene_acceso_almacen(almacen_intencion):
            msg = f"No tienes permiso para consultar el almacén '{almacen_intencion.nombre}'."
            return None, {
                "tipo": "consulta",
                "mensaje_pantalla": msg,
                "mensaje_verbal": normalizar_para_tts(msg),
                "datos": {"error_permisos": True, "almacen_denegado": almacen_intencion.nombre},
            }
        return almacen_intencion, None

    if usuario.es_usuario_normal:
        if usuario.acceso_todos_almacenes:
            return None, None
        if usuario.almacen_asignado and usuario.tiene_acceso_almacen(usuario.almacen_asignado):
            return usuario.almacen_asignado, None
        primer_multi = usuario.almacenes_asignados.filter(activo=True, eliminado=False).first()
        if primer_multi:
            return primer_multi, None
        else:
            msg = "Tu cuenta de usuario no tiene un almacén asignado activo para realizar esta consulta."
            return None, {
                "tipo": "consulta",
                "mensaje_pantalla": msg,
                "mensaje_verbal": normalizar_para_tts(msg),
                "datos": {"error_permisos": True, "sin_almacen_asignado": True},
            }

    # Administrador o Superusuario sin almacén explícito: acceso global
    return None, None


def _resolver_almacen_para_operacion(
    usuario: UsuarioAudacIA,
    almacen_intencion: Almacen | None,
    producto: Producto,
    operacion: str = "operación",
) -> tuple[Almacen | None, dict[str, Any] | None]:
    """Resuelve determinísticamente el almacén para operaciones de mutación (entrada, salida, préstamo).
    
    Reglas:
    1. Si se indicó un almacén explícito:
       - Si el usuario tiene acceso y el almacén está activo: se usa.
       - Si no tiene acceso o está inactivo: se deniega con error de permisos.
    2. Si no se indicó almacén:
       - Si el usuario normal tiene un único almacén autorizado (sea por asignado o multi): se usa ese almacén.
       - Si el usuario tiene acceso a múltiples almacenes (o acceso a todos) y el producto está enlazado a su categoría:
         se usa el almacén principal de la categoría del producto si el usuario tiene acceso a él.
       - Si no se puede determinar sin ambigüedad: se solicita aclaración breve preguntando en qué almacén operar.
    """
    if almacen_intencion:
        if not usuario.tiene_acceso_almacen(almacen_intencion):
            msg = f"No tienes autorización para realizar la {operacion} en el almacén '{almacen_intencion.nombre}'."
            return None, {
                "tipo": "error",
                "mensaje_pantalla": msg,
                "mensaje_verbal": normalizar_para_tts(msg),
                "datos": {"error_permisos": True, "almacen_denegado": almacen_intencion.nombre},
            }
        return almacen_intencion, None

    # Caso sin almacén explícito:
    alms_accesibles = list(usuario.obtener_almacenes_accesibles())
    if not alms_accesibles:
        msg = f"Tu cuenta de usuario no tiene almacenes activos autorizados para registrar {operacion}."
        return None, {
            "tipo": "error",
            "mensaje_pantalla": msg,
            "mensaje_verbal": normalizar_para_tts(msg),
            "datos": {"sin_almacen": True},
        }

    # Si solo tiene 1 almacén accesible
    if len(alms_accesibles) == 1:
        return alms_accesibles[0], None

    # Si tiene múltiples almacenes accesibles o acceso global:
    # Comprobar si el almacén principal de la categoría del producto está entre sus almacenes autorizados
    alm_cat = getattr(getattr(producto, "categoria", None), "almacen_principal", None)
    if alm_cat and usuario.tiene_acceso_almacen(alm_cat):
        return alm_cat, None

    # Si el producto tiene stock en un único almacén accesible para el usuario (relevante para salidas/préstamos)
    exs_con_stock = Existencia.objects.filter(
        producto=producto,
        almacen__in=alms_accesibles,
        stock_disponible__gt=0,
    )
    if exs_con_stock.count() == 1:
        return exs_con_stock.first().almacen, None

    # Ante ambigüedad con múltiples almacenes autorizados, solicitar aclaración breve
    nombres_alms = ", ".join(f"**{a.nombre}**" for a in alms_accesibles[:3])
    msg = f"Tienes acceso a varios almacenes ({nombres_alms}). ¿En cuál almacén deseas registrar la {operacion} de **{producto.nombre}**?"
    return None, {
        "tipo": "aclaracion",
        "mensaje_pantalla": msg,
        "mensaje_verbal": normalizar_para_tts(f"Tienes acceso a varios almacenes. ¿En cuál deseas registrar la {operacion} de {producto.nombre}?"),
        "datos": {"requiere_almacen": True},
    }


def procesar_comando_inventario(
    usuario: UsuarioAudacIA,
    mensaje: str,
    propuesta_activa: dict | None = None,
    sesion: dict | None = None,
    estado_operativo: Any = None,
) -> dict[str, Any]:
    """Interpreta el mensaje del usuario mediante la capa lingüística desacoplada.

    Flujo obligatorio:
    Audio -> Whisper -> Normalización lingüística -> Intención -> Confirmación Haku -> API Inventario

    Retorna un diccionario con:
    - tipo: "consulta" | "propuesta" | "aclaracion" | "accion_confirmar" | "accion_cancelar" | "error" | "conversacion"
    - mensaje_pantalla: texto formateado para la interfaz
    - mensaje_verbal: texto optimizado para síntesis de voz (sin markdown)
    - propuesta: dict con datos de la propuesta (si tipo == "propuesta")
    - datos: dict adicional para renderizado visual
    """
    from .cache import HakuCacheManager
    from .contexto import HakuOperationalContext
    from .diagnostico import HakuDiagnostico
    from .lenguaje.normalizador import IntencionResultado, IntencionTipo, NormalizadorLenguajeHaku

    texto = mensaje.strip()
    texto_lower = texto.lower()

    if estado_operativo is None and usuario and getattr(usuario, "id", None) and sesion is not None:
        session_key = getattr(sesion, "session_key", None) if hasattr(sesion, "session_key") else (sesion.get("session_key") if isinstance(sesion, dict) else None)
        if not session_key:
            session_key = f"sess_{usuario.id}"
        almacen_id = getattr(usuario, "almacen_asignado_id", None)
        estado_operativo = HakuOperationalContext.obtener_o_crear(usuario.id, session_key, almacen_id)

    if propuesta_activa is None and estado_operativo and getattr(estado_operativo, "propuesta_activa", None):
        propuesta_activa = estado_operativo.propuesta_activa

    # ─────────────────────────────────────────────────────────────────────────
    # GENERACIÓN DE INTENCIÓN SEMÁNTICA (CAPA LINGÜÍSTICA SIN EFECTOS SECUNDARIOS)
    # ─────────────────────────────────────────────────────────────────────────
    _t0_interp = time.time()
    intencion: IntencionResultado = NormalizadorLenguajeHaku.interpretar(
        mensaje=texto,
        usuario=usuario,
        contexto_sesion=sesion,
        estado_operativo=estado_operativo,
    )
    _latencia_ms = max((time.time() - _t0_interp) * 1000.0, 0.01)
    HakuDiagnostico.registrar_intencion(intencion.tipo.value, latencia_ms=_latencia_ms)

    # Actualizar producto en foco conversacional en RAM
    if intencion.producto and estado_operativo:
        estado_operativo.asignar_producto(
            intencion.producto.id,
            intencion.producto.codigo_interno,
            intencion.producto.nombre,
        )
        estado_operativo.ultima_intencion = intencion.tipo.value
        estado_operativo.ultima_busqueda = texto
        HakuOperationalContext.guardar(estado_operativo)

    # 0. Protocolo social: Saludos y Agradecimientos
    if intencion.tipo == IntencionTipo.SALUDO:
        msg = "Buenos días. Sistema Haku de control de inventario listo. ¿Qué consulta u operación deseas realizar?"
        return {
            "tipo": "saludo",
            "mensaje_pantalla": msg,
            "mensaje_verbal": normalizar_para_tts(msg),
        }

    if intencion.tipo == IntencionTipo.AGRADECIMIENTO:
        msg = "Con gusto. El sistema permanece disponible para nuevas consultas u operaciones."
        return {
            "tipo": "agradecimiento",
            "mensaje_pantalla": msg,
            "mensaje_verbal": normalizar_para_tts(msg),
        }

    if intencion.tipo == IntencionTipo.SOLICITUD_NO_COMPRENDIDA:
        msg = "Disculpa, no entendí la solicitud. ¿Puedes repetirla?"
        return {
            "tipo": "no_comprendido",
            "mensaje_pantalla": msg,
            "mensaje_verbal": normalizar_para_tts(msg),
            "datos": {"no_comprendido": True},
        }

    # 0-bis. Acciones de Confirmación y Cancelación desacopladas
    if intencion.tipo == IntencionTipo.ACCION_CONFIRMAR:
        if propuesta_activa:
            if time.time() > propuesta_activa.get("expira_en", 0):
                HakuDiagnostico.registrar_propuesta("expirada")
                if estado_operativo:
                    estado_operativo.propuesta_activa = None
                    HakuOperationalContext.guardar(estado_operativo)
                if sesion is not None:
                    sesion["haku_propuesta_activa"] = None
                return {
                    "tipo": "aclaracion",
                    "mensaje_pantalla": "La propuesta previa ha caducado por superar los 30 minutos. Por favor genera una nueva solicitud.",
                    "mensaje_verbal": "La propuesta ha caducado. Por favor solicita la operación de nuevo.",
                    "propuesta_activa": False,
                }
            return {
                "tipo": "accion_confirmar",
                "propuesta_id": propuesta_activa["id"],
                "mensaje_pantalla": "Confirmación recibida. Procediendo con la transacción...",
                "mensaje_verbal": "Confirmación recibida. Ejecutando la operación.",
            }
        return {
            "tipo": "accion_confirmar",
            "mensaje_pantalla": "No hay ninguna transacción pendiente de confirmación en este momento.",
            "mensaje_verbal": normalizar_para_tts("No hay ninguna transacción pendiente de confirmación en este momento."),
        }

    if intencion.tipo == IntencionTipo.ACCION_CANCELAR:
        if propuesta_activa:
            HakuDiagnostico.registrar_propuesta("cancelada")
            if estado_operativo:
                estado_operativo.propuesta_activa = None
                HakuOperationalContext.guardar(estado_operativo)
            if sesion is not None:
                sesion["haku_propuesta_activa"] = None
            return {
                "tipo": "accion_cancelar",
                "propuesta_id": propuesta_activa["id"],
                "mensaje_pantalla": "Operación cancelada. La propuesta ha sido descartada.",
                "mensaje_verbal": "Operación cancelada. No se realizaron cambios en el inventario.",
            }
        return {
            "tipo": "accion_cancelar",
            "mensaje_pantalla": "Entendido. No hay operaciones pendientes en ejecución.",
            "mensaje_verbal": normalizar_para_tts("Entendido. No hay operaciones pendientes en ejecución."),
        }

    # 1. Petición de aclaración por ambigüedad o múltiples códigos (Reglas 3 y 5)
    if intencion.tipo == IntencionTipo.SOLICITAR_ACLARACION:
        if intencion.candidatos and estado_operativo:
            estado_operativo.asignar_candidatos(intencion.candidatos)
            estado_operativo.ultima_intencion = intencion.tipo.value
            estado_operativo.ultima_busqueda = texto
            HakuOperationalContext.guardar(estado_operativo)

        msg_aclaracion = intencion.mensaje_aclaracion or "¿Podrías especificar a qué producto o acción te refieres?"
        return {
            "tipo": "aclaracion",
            "mensaje_pantalla": msg_aclaracion,
            "mensaje_verbal": normalizar_para_tts(msg_aclaracion),
            "datos": {"requiere_aclaracion": True},
        }

    # 2. Métricas y consultas generales de movimientos por intervalo temporal y multialmacén
    if intencion.tipo == IntencionTipo.CONSULTAR_METRICAS_MOVIMIENTOS:
        from .servicios_temporales import resolver_intervalo_temporal, obtener_ahora_local
        intervalo = resolver_intervalo_temporal(texto)
        if not intervalo:
            ahora = obtener_ahora_local()
            hoy_date = ahora.date()
            import datetime
            inicio = datetime.datetime.combine(hoy_date, datetime.time.min, tzinfo=ahora.tzinfo)
            fin = ahora
            desc_periodo = f"hoy ({hoy_date.strftime('%d/%m/%Y')})"
        else:
            inicio = intervalo.inicio
            fin = intervalo.fin
            desc_periodo = intervalo.descripcion_legible

        qs_mov = Movimiento.objects.filter(
            estado=Movimiento.Estado.CONFIRMADO,
            fecha_hora__gte=inicio,
            fecha_hora__lt=fin,
        ).select_related("producto", "almacen_origen", "almacen_destino", "usuario")

        # Filtrado de almacén: si se especificó almacén en la intención, validar acceso
        if intencion.almacen:
            almacen_filtrado, error_resp = _validar_acceso_almacen(usuario, intencion.almacen)
            if error_resp:
                return error_resp
            qs_mov = qs_mov.filter(Q(almacen_origen=almacen_filtrado) | Q(almacen_destino=almacen_filtrado)).distinct()
            desc_periodo += f" en {almacen_filtrado.nombre}"
        elif usuario.es_usuario_normal:
            if usuario.acceso_todos_almacenes:
                pass
            else:
                alms = usuario.obtener_almacenes_accesibles()
                if not alms.exists():
                    msg = "Tu cuenta de usuario no tiene un almacén asignado activo para consultar movimientos."
                    return {
                        "tipo": "consulta",
                        "mensaje_pantalla": msg,
                        "mensaje_verbal": normalizar_para_tts(msg),
                        "datos": {"total": 0, "sin_almacen_asignado": True},
                    }
                qs_mov = qs_mov.filter(Q(almacen_origen__in=alms) | Q(almacen_destino__in=alms)).distinct()

        total_mov = qs_mov.count()
        entradas = qs_mov.filter(tipo=Movimiento.Tipo.ENTRADA).count()
        salidas = qs_mov.filter(tipo=Movimiento.Tipo.SALIDA).count()
        prestamos = qs_mov.filter(tipo=Movimiento.Tipo.PRESTAMO).count()
        devoluciones = qs_mov.filter(tipo=Movimiento.Tipo.DEVOLUCION).count()
        traslados = qs_mov.filter(tipo__in=[Movimiento.Tipo.TRASLADO_ENTRE, Movimiento.Tipo.TRASLADO_INTERNO]).count()

        if estado_operativo:
            estado_operativo.registrar_accion(
                "CONSULTAR_METRICAS_MOVIMIENTOS",
                cantidad=total_mov,
            )
            HakuOperationalContext.guardar(estado_operativo)

        es_pregunta_entradas = bool(re.search(r"\bentradas?\b", texto_lower) and not re.search(r"\bsalidas?\b", texto_lower))
        es_pregunta_salidas = bool(re.search(r"\bsalidas?\b", texto_lower) and not re.search(r"\bentradas?\b", texto_lower))
        es_pregunta_traslados = bool(re.search(r"\btraslados?\b", texto_lower))
        ultimos_movs = list(qs_mov.select_related("producto", "almacen_origen", "almacen_destino")[:10]) if total_mov > 0 else []

        if total_mov == 0:
            pantalla = f"Durante la jornada se registraron movimientos de inventario correspondientes a las operaciones realizadas.\nDurante el periodo consultado (**{desc_periodo}**) no se registraron movimientos de inventario.\n\n- **0** entradas\n- **0** salidas\n- **0** préstamos\n- **0** devoluciones\n- **0** traslados\n\n¿Quieres revisar algún producto específico?"
            verbal = f"Durante el periodo consultado, {desc_periodo}, no se registraron movimientos de inventario. ¿Quieres revisar algún producto específico?"
        elif es_pregunta_entradas:
            pantalla = f"Se registraron **{entradas}** entradas durante **{desc_periodo}** (de un total de {total_mov} movimientos):\n- **{entradas}** entradas\n- **{salidas}** salidas\n- **{prestamos}** préstamos\n- **{devoluciones}** devoluciones\n- **{traslados}** traslados\n\n¿Quieres revisar algún producto específico?"
            verbal = f"Se registraron {entradas} entradas durante {desc_periodo}, de un total de {total_mov} movimientos. ¿Quieres revisar algún producto específico?"
        elif es_pregunta_salidas:
            pantalla = f"Se registraron **{salidas}** salidas durante **{desc_periodo}** (de un total de {total_mov} movimientos):\n- **{entradas}** entradas\n- **{salidas}** salidas\n- **{prestamos}** préstamos\n- **{devoluciones}** devoluciones\n- **{traslados}** traslados\n\n¿Quieres revisar algún producto específico?"
            verbal = f"Se registraron {salidas} salidas durante {desc_periodo}, de un total de {total_mov} movimientos. ¿Quieres revisar algún producto específico?"
        elif es_pregunta_traslados:
            pantalla = f"Se registraron **{traslados}** traslados durante **{desc_periodo}** (de un total de {total_mov} movimientos):\n- **{entradas}** entradas\n- **{salidas}** salidas\n- **{prestamos}** préstamos\n- **{devoluciones}** devoluciones\n- **{traslados}** traslados\n\n¿Quieres revisar algún producto específico?"
            verbal = f"Se registraron {traslados} traslados durante {desc_periodo}, de un total de {total_mov} movimientos. ¿Quieres revisar algún producto específico?"
        else:
            # Si se solicita listado o detalle («muéstrame los movimientos de ayer»)
            detalle_movs = []
            for m in ultimos_movs:
                fecha_str = m.fecha_hora.astimezone(obtener_ahora_local().tzinfo).strftime("%d/%m %H:%M")
                alm_txt = m.almacen_origen.nombre if m.almacen_origen else ""
                if m.almacen_destino:
                    alm_txt += f" ➔ {m.almacen_destino.nombre}"
                detalle_movs.append(f"- `{fecha_str}` | **{m.get_tipo_display()}**: {m.cantidad} uds de {m.producto.nombre} ({alm_txt})")

            bloque_detalle = "\n".join(detalle_movs)
            extra_paginacion = f"\n\n*(Mostrando primeros 10 de {total_mov} movimientos registrados)*" if total_mov > 10 else ""

            pantalla = (
                f"### Movimientos de inventario — {desc_periodo}\n"
                f"Durante la jornada se registraron movimientos de inventario correspondientes a las operaciones realizadas.\n"
                f"Se registraron **{total_mov}** movimientos en total:\n"
                f"- **{entradas}** entradas | **{salidas}** salidas | **{prestamos}** préstamos | **{devoluciones}** devoluciones | **{traslados}** traslados\n\n"
                f"**Detalle cronológico:**\n{bloque_detalle}{extra_paginacion}\n\n"
                f"¿Quieres revisar algún producto específico?"
            )
            verbal = (
                f"Durante {desc_periodo} se registraron {total_mov} movimientos en total: "
                f"{entradas} entradas, {salidas} salidas, {prestamos} préstamos, {devoluciones} devoluciones y {traslados} traslados. "
                f"¿Quieres revisar algún producto específico?"
            )

        return {
            "tipo": "consulta",
            "mensaje_pantalla": pantalla,
            "mensaje_verbal": normalizar_para_tts(verbal),
            "datos": {
                "periodo": desc_periodo,
                "inicio": inicio.isoformat(),
                "fin": fin.isoformat(),
                "total": total_mov,
                "entradas": entradas,
                "salidas": salidas,
                "prestamos": prestamos,
                "devoluciones": devoluciones,
                "traslados": traslados,
                "movimientos": [
                    {
                        "id": str(m.id),
                        "tipo": m.tipo,
                        "producto": m.producto.nombre,
                        "cantidad": m.cantidad,
                        "almacen_origen": m.almacen_origen.nombre if m.almacen_origen else None,
                        "almacen_destino": m.almacen_destino.nombre if m.almacen_destino else None,
                        "motivo": m.motivo,
                    }
                    for m in ultimos_movs
                ],
            },
        }

    # 3. Resumen de inventario / almacén
    if intencion.tipo == IntencionTipo.CONSULTAR_RESUMEN_INVENTARIO:
        hoy = timezone.now().date()
        almacen, error_resp = _validar_acceso_almacen(usuario, intencion.almacen)
        if error_resp:
            return error_resp

        qs_prod = Producto.objects.filter(activo=True, archivado=False)
        if almacen:
            qs_prod = qs_prod.filter(categoria__almacen_principal=almacen)
        total_productos = qs_prod.count()

        qs_exist = Existencia.objects.filter(producto__activo=True, producto__archivado=False)
        if almacen:
            qs_exist = qs_exist.filter(almacen=almacen)

        sin_stock_count = sum(1 for e in qs_exist if e.sin_stock)
        bajo_stock_count = sum(1 for e in qs_exist if e.stock_bajo)
        total_alertas = sin_stock_count + bajo_stock_count

        qs_mov = Movimiento.objects.filter(estado=Movimiento.Estado.CONFIRMADO, fecha_hora__date=hoy)
        if almacen:
            qs_mov = qs_mov.filter(almacen_origen=almacen)
        movimientos_hoy = qs_mov.count()

        qs_prest = Prestamo.objects.filter(
            estado__in=[Prestamo.Estado.ABIERTO, Prestamo.Estado.DEVUELTO_PARCIAL]
        )
        if almacen:
            qs_prest = qs_prest.filter(almacen=almacen)
        prestamos_activos = qs_prest.count()

        if estado_operativo:
            estado_operativo.registrar_accion(
                "CONSULTAR_RESUMEN_INVENTARIO",
                cantidad=total_productos,
            )
            HakuOperationalContext.guardar(estado_operativo)

        nombre_almacen = almacen.nombre if almacen else "el inventario general"
        pantalla = (
            f"### Resumen de inventario ({nombre_almacen})\n"
            f"- **Cantidad de productos registrados:** {total_productos}\n"
            f"- **Productos con bajo stock:** {bajo_stock_count}\n"
            f"- **Productos sin stock (agotados):** {sin_stock_count}\n"
            f"- **Movimientos del día:** {movimientos_hoy}\n"
            f"- **Alertas activas:** {total_alertas}\n"
            f"- **Préstamos activos:** {prestamos_activos}"
        )
        verbal = (
            f"En {nombre_almacen} hay {total_productos} productos registrados. "
            f"Hay {bajo_stock_count} con bajo stock y {sin_stock_count} agotados. "
            f"Hoy van {movimientos_hoy} movimientos del día y {prestamos_activos} préstamos activos."
        )
        return {
            "tipo": "consulta",
            "mensaje_pantalla": pantalla,
            "mensaje_verbal": normalizar_para_tts(verbal),
            "datos": {
                "total_productos": total_productos,
                "bajo_stock": bajo_stock_count,
                "sin_stock": sin_stock_count,
                "movimientos_hoy": movimientos_hoy,
                "alertas": total_alertas,
                "prestamos_activos": prestamos_activos,
            },
        }

    # 3-bis A. Listar almacenes / bodegas
    if intencion.tipo == IntencionTipo.LISTAR_ALMACENES:
        qs_almacenes = Almacen.objects.filter(activo=True, eliminado=False).order_by("nombre")
        total_almacenes = qs_almacenes.count()

        if total_almacenes == 0:
            msg = "Actualmente no hay almacenes activos registrados en el sistema."
            return {
                "tipo": "consulta",
                "mensaje_pantalla": msg,
                "mensaje_verbal": normalizar_para_tts(msg),
                "datos": {"almacenes": []},
            }

        alms_autorizados_ids = set(usuario.obtener_almacenes_accesibles().values_list("id", flat=True)) if usuario.es_usuario_normal and not usuario.acceso_todos_almacenes else set()

        lineas_pantalla = ["### Almacenes activos en el sistema:\n"]
        lineas_voz = [f"Hay {total_almacenes} almacenes registrados:"]
        lista_datos = []

        for alm in qs_almacenes:
            es_propio = bool(alm.id in alms_autorizados_ids)
            marca_asignado = " *(Tu almacén asignado)*" if (usuario.almacen_asignado and alm.id == usuario.almacen_asignado.id) else (" *(Autorizado)*" if es_propio else "")
            desc = f" — {alm.descripcion}" if alm.descripcion else ""
            lineas_pantalla.append(f"- **{alm.nombre}**{marca_asignado}{desc}")
            lineas_voz.append(f"{alm.nombre}.")
            lista_datos.append({"id": alm.id, "nombre": alm.nombre, "descripcion": alm.descripcion, "es_asignado": es_propio})

        if usuario.es_usuario_normal and not usuario.acceso_todos_almacenes and not alms_autorizados_ids:
            lineas_pantalla.append("\n*Aviso: Tu cuenta de usuario no tiene ningún almacén asignado actualmente.*")

        if estado_operativo:
            estado_operativo.registrar_accion(
                "LISTAR_ALMACENES",
                cantidad=total_almacenes,
            )
            HakuOperationalContext.guardar(estado_operativo)

        pantalla = "\n".join(lineas_pantalla)
        verbal = " ".join(lineas_voz)

        return {
            "tipo": "consulta",
            "mensaje_pantalla": pantalla,
            "mensaje_verbal": normalizar_para_tts(verbal),
            "datos": {"almacenes": lista_datos, "total": total_almacenes},
        }

    # 3-bis B. Listar catálogo general de productos
    if intencion.tipo == IntencionTipo.LISTAR_CATALOGO:
        almacen, error_resp = _validar_acceso_almacen(usuario, intencion.almacen)
        if error_resp:
            return error_resp

        qs_prod = Producto.objects.filter(activo=True, archivado=False).select_related("categoria")
        if almacen:
            qs_prod = qs_prod.filter(categoria__almacen_principal=almacen)

        filtro_nombre = intencion.datos_extra.get("filtro_nombre") if intencion.datos_extra else None
        if filtro_nombre:
            qs_prod = qs_prod.filter(
                Q(nombre__icontains=filtro_nombre) | Q(categoria__nombre__icontains=filtro_nombre)
            )

        qs_prod = qs_prod.order_by("codigo_interno")

        total_productos = qs_prod.count()
        if total_productos == 0:
            if filtro_nombre:
                msg = f"No hay productos de la categoría o tipo '{filtro_nombre}' registrados en el catálogo."
            else:
                msg = "No hay productos registrados en el catálogo en este momento."
            return {
                "tipo": "consulta",
                "mensaje_pantalla": msg,
                "mensaje_verbal": normalizar_para_tts(msg),
                "datos": {"productos": [], "total": 0},
            }

        productos_muestra = list(qs_prod[:10])
        lineas_pantalla = [f"### Catálogo de productos ({total_productos} registrados):\n"]
        lineas_voz = [f"El catálogo cuenta con {total_productos} productos."]

        lista_datos = []
        nombres_voz = []
        for p in productos_muestra:
            lineas_pantalla.append(f"- **{p.nombre}** (`{p.codigo_interno}`) — Categoría: {p.categoria.nombre}")
            nombres_voz.append(p.nombre)
            lista_datos.append({"id": str(p.id), "codigo": p.codigo_interno, "nombre": p.nombre, "categoria": p.categoria.nombre})

        if total_productos > 10:
            lineas_pantalla.append(f"\n*Mostrando 10 de {total_productos} productos registrados.*")
            lineas_voz.append(f"Entre ellos: {', '.join(nombres_voz[:5])}.")
        else:
            lineas_voz.append(f"Listado: {', '.join(nombres_voz)}.")

        if estado_operativo:
            estado_operativo.asignar_coleccion(lista_datos)
            estado_operativo.registrar_accion(
                "LISTAR_CATALOGO",
                cantidad=total_productos,
            )
            estado_operativo.producto_id = None
            estado_operativo.producto_codigo = None
            estado_operativo.producto_nombre = None
            HakuOperationalContext.guardar(estado_operativo)

        return {
            "tipo": "consulta",
            "mensaje_pantalla": "\n".join(lineas_pantalla),
            "mensaje_verbal": normalizar_para_tts(" ".join(lineas_voz)),
            "datos": {"productos": lista_datos, "total": total_productos},
        }

    # 3-bis C. Productos con existencias disponibles en stock
    if intencion.tipo == IntencionTipo.PRODUCTOS_CON_STOCK:
        # Si se seleccionaron candidatos plurales específicos (ej. "de los tres")
        cands_plurales = intencion.datos_extra.get("candidatos_plurales") if intencion.datos_extra else None
        if cands_plurales:
            ids_cands = [c["id"] for c in cands_plurales if "id" in c]
            qs_exist = Existencia.objects.filter(
                producto_id__in=ids_cands,
                producto__activo=True,
                producto__archivado=False,
            ).select_related("producto", "almacen")
        else:
            almacen, error_resp = _validar_acceso_almacen(usuario, intencion.almacen)
            if error_resp:
                return error_resp

            qs_exist = Existencia.objects.filter(
                producto__activo=True,
                producto__archivado=False,
                stock_disponible__gt=0,
            ).select_related("producto", "almacen").order_by("-stock_disponible")

            if almacen:
                qs_exist = qs_exist.filter(almacen=almacen)

            filtro_nombre = intencion.datos_extra.get("filtro_nombre") if intencion.datos_extra else None
            if filtro_nombre:
                qs_exist = qs_exist.filter(
                    Q(producto__nombre__icontains=filtro_nombre) | Q(producto__categoria__nombre__icontains=filtro_nombre)
                )

        total_con_stock = qs_exist.count()
        if total_con_stock == 0:
            filtro_nombre = intencion.datos_extra.get("filtro_nombre") if intencion.datos_extra else None
            if filtro_nombre:
                msg = f"No hay productos de la categoría o tipo '{filtro_nombre}' con existencias disponibles en este momento."
            else:
                msg = "No hay productos con existencias disponibles en este momento."
            return {
                "tipo": "consulta",
                "mensaje_pantalla": msg,
                "mensaje_verbal": normalizar_para_tts(msg),
                "datos": {"existencias": [], "total": 0},
            }

        existencias_muestra = list(qs_exist[:10])
        lineas_pantalla = [f"### Productos con existencias disponibles ({total_con_stock} en total):\n"]
        lineas_voz = [f"Hay {total_con_stock} productos con existencias disponibles."]

        lista_datos = []
        nombres_voz = []
        for e in existencias_muestra:
            lineas_pantalla.append(
                f"- **{e.producto.nombre}** (`{e.producto.codigo_interno}`): **{e.stock_disponible}** unidades en {e.almacen.nombre}"
            )
            nombres_voz.append(f"{e.producto.nombre} con {e.stock_disponible} unidades")
            lista_datos.append({
                "id": e.producto.id,
                "producto_id": str(e.producto.id),
                "codigo": e.producto.codigo_interno,
                "nombre": e.producto.nombre,
                "disponible": e.stock_disponible,
                "almacen": e.almacen.nombre,
            })

        if total_con_stock > 10:
            lineas_pantalla.append(f"\n*Mostrando los 10 productos con mayor disponibilidad.*")
            lineas_voz.append(f"Entre los principales: {', '.join(nombres_voz[:4])}.")
        else:
            lineas_voz.append(f"Listado: {', '.join(nombres_voz)}.")

        if estado_operativo:
            estado_operativo.asignar_coleccion(lista_datos)
            estado_operativo.registrar_accion(
                "PRODUCTOS_CON_STOCK",
                cantidad=total_con_stock,
            )
            estado_operativo.producto_id = None
            estado_operativo.producto_codigo = None
            estado_operativo.producto_nombre = None
            HakuOperationalContext.guardar(estado_operativo)

        return {
            "tipo": "consulta",
            "mensaje_pantalla": "\n".join(lineas_pantalla),
            "mensaje_verbal": normalizar_para_tts(" ".join(lineas_voz)),
            "datos": {"existencias": lista_datos, "total": total_con_stock},
        }

    # 3-bis D. Productos sin stock / agotados (stock == 0)
    if intencion.tipo == IntencionTipo.PRODUCTOS_SIN_STOCK:
        almacen, error_resp = _validar_acceso_almacen(usuario, intencion.almacen)
        if error_resp:
            return error_resp

        qs_exist = Existencia.objects.filter(
            producto__activo=True,
            producto__archivado=False,
            stock_disponible=0,
        ).select_related("producto", "almacen").order_by("producto__nombre")

        if almacen:
            qs_exist = qs_exist.filter(almacen=almacen)

        filtro_nombre = intencion.datos_extra.get("filtro_nombre") if intencion.datos_extra else None
        if filtro_nombre:
            qs_exist = qs_exist.filter(
                Q(producto__nombre__icontains=filtro_nombre) | Q(producto__categoria__nombre__icontains=filtro_nombre)
            )

        filtrar_coleccion = bool(intencion.datos_extra and intencion.datos_extra.get("filtrar_coleccion"))
        if filtrar_coleccion:
            coleccion = estado_operativo.coleccion_productos if estado_operativo else []
            if not coleccion:
                msg = "No tengo una lista previa de productos en la conversación activa para verificar cuáles se acabaron."
                return {
                    "tipo": "aclaracion",
                    "mensaje_pantalla": msg,
                    "mensaje_verbal": normalizar_para_tts(msg),
                    "datos": {"sin_stock": [], "total": 0},
                }
            ids = [c.get("id") or c.get("producto_id") for c in coleccion if (c.get("id") or c.get("producto_id"))]
            qs_exist = qs_exist.filter(producto_id__in=ids)

        total_sin_stock = qs_exist.count()
        if total_sin_stock == 0:
            if filtro_nombre:
                msg = f"No hay productos de la categoría o tipo '{filtro_nombre}' agotados en este momento."
            elif filtrar_coleccion:
                msg = "De las referencias que acabamos de revisar, ninguna se ha agotado. Todas tienen stock disponible."
            else:
                msg = "No hay productos agotados en este momento. Todas las referencias cuentan con existencias disponibles."
            return {
                "tipo": "consulta",
                "mensaje_pantalla": msg,
                "mensaje_verbal": normalizar_para_tts(msg),
                "datos": {"sin_stock": [], "total": 0},
            }

        items_muestra = list(qs_exist[:10])
        if filtrar_coleccion:
            lineas_pantalla = [f"### De las referencias revisadas, las siguientes están agotadas ({total_sin_stock} en total):\n"]
            lineas_voz = [f"De las referencias revisadas, {total_sin_stock} están agotadas:"]
        else:
            lineas_pantalla = [f"### Productos agotados sin existencias ({total_sin_stock} en total):\n"]
            lineas_voz = [f"Hay {total_sin_stock} productos agotados sin existencias:"]
        lista_datos = []
        nombres_voz = []

        for e in items_muestra:
            lineas_pantalla.append(f"- **{e.producto.nombre}** (`{e.producto.codigo_interno}`) en {e.almacen.nombre}: **0 unidades disponibles**")
            nombres_voz.append(f"{e.producto.nombre} en {e.almacen.nombre}")
            lista_datos.append({
                "id": e.producto.id,
                "producto_id": str(e.producto.id),
                "codigo": e.producto.codigo_interno,
                "nombre": e.producto.nombre,
                "disponible": 0,
                "almacen": e.almacen.nombre,
            })

        if total_sin_stock > 10:
            lineas_pantalla.append(f"\n*Mostrando 10 de {total_sin_stock} productos agotados.*")
            lineas_voz.append(f"Entre ellos: {', '.join(nombres_voz[:4])}.")
        else:
            lineas_voz.append(f"{', '.join(nombres_voz)}.")

        if estado_operativo:
            estado_operativo.asignar_coleccion(lista_datos, total_original=total_sin_stock)
            estado_operativo.registrar_accion(
                "PRODUCTOS_SIN_STOCK",
                cantidad=total_sin_stock,
            )
            estado_operativo.producto_id = None
            estado_operativo.producto_codigo = None
            estado_operativo.producto_nombre = None
            HakuOperationalContext.guardar(estado_operativo)

        return {
            "tipo": "consulta",
            "mensaje_pantalla": "\n".join(lineas_pantalla),
            "mensaje_verbal": normalizar_para_tts(" ".join(lineas_voz)),
            "datos": {"sin_stock": lista_datos, "total": total_sin_stock},
        }

    # 3-bis E. Productos con bajo stock (por debajo del mínimo)
    if intencion.tipo == IntencionTipo.PRODUCTOS_BAJO_STOCK:
        almacen, error_resp = _validar_acceso_almacen(usuario, intencion.almacen)
        if error_resp:
            return error_resp

        qs_exist = Existencia.objects.filter(
            producto__activo=True,
            producto__archivado=False,
            stock_disponible__gt=0,
        ).select_related("producto", "almacen")

        if almacen:
            qs_exist = qs_exist.filter(almacen=almacen)

        filtro_nombre = intencion.datos_extra.get("filtro_nombre") if intencion.datos_extra else None
        if filtro_nombre:
            qs_exist = qs_exist.filter(
                Q(producto__nombre__icontains=filtro_nombre) | Q(producto__categoria__nombre__icontains=filtro_nombre)
            )

        bajo_stock_items = [e for e in qs_exist if e.stock_bajo]
        total_bajo_stock = len(bajo_stock_items)

        if total_bajo_stock == 0:
            if filtro_nombre:
                msg = f"No hay productos de la categoría o tipo '{filtro_nombre}' por debajo del stock mínimo configurado."
            else:
                msg = "No hay productos con existencias por debajo del stock mínimo configurado."
            return {
                "tipo": "consulta",
                "mensaje_pantalla": msg,
                "mensaje_verbal": normalizar_para_tts(msg),
                "datos": {"bajo_stock": [], "total": 0},
            }

        items_muestra = bajo_stock_items[:10]
        lineas_pantalla = [f"### Productos por debajo del stock mínimo ({total_bajo_stock} en total):\n"]
        lineas_voz = [f"Hay {total_bajo_stock} productos por debajo del stock mínimo:"]
        lista_datos = []
        nombres_voz = []

        for e in items_muestra:
            lineas_pantalla.append(
                f"- **{e.producto.nombre}** (`{e.producto.codigo_interno}`) en {e.almacen.nombre}: "
                f"**{e.stock_disponible}** unidades (mínimo: {e.producto.stock_minimo})"
            )
            nombres_voz.append(f"{e.producto.nombre} con {e.stock_disponible} de {e.producto.stock_minimo}")
            lista_datos.append({
                "id": e.producto.id,
                "producto_id": str(e.producto.id),
                "codigo": e.producto.codigo_interno,
                "nombre": e.producto.nombre,
                "disponible": e.stock_disponible,
                "minimo": e.producto.stock_minimo,
                "almacen": e.almacen.nombre,
            })

        if total_bajo_stock > 10:
            lineas_pantalla.append(f"\n*Mostrando 10 de {total_bajo_stock} productos con bajo stock.*")
            lineas_voz.append(f"Entre ellos: {', '.join(nombres_voz[:4])}.")
        else:
            lineas_voz.append(f"{', '.join(nombres_voz)}.")

        if estado_operativo:
            estado_operativo.asignar_coleccion(lista_datos, total_original=total_bajo_stock)
            estado_operativo.registrar_accion(
                "PRODUCTOS_BAJO_STOCK",
                cantidad=total_bajo_stock,
            )
            estado_operativo.producto_id = None
            estado_operativo.producto_codigo = None
            estado_operativo.producto_nombre = None
            HakuOperationalContext.guardar(estado_operativo)

        return {
            "tipo": "consulta",
            "mensaje_pantalla": "\n".join(lineas_pantalla),
            "mensaje_verbal": normalizar_para_tts(" ".join(lineas_voz)),
            "datos": {"bajo_stock": lista_datos, "total": total_bajo_stock},
        }

    # 3-bis F. Consulta de reposición integral («¿Qué hace falta por pedir?»)
    if intencion.tipo == IntencionTipo.CONSULTAR_FALTANTES_REPOSICION:
        if intencion.almacen:
            almacen, error_resp = _validar_acceso_almacen(usuario, intencion.almacen)
            if error_resp:
                return error_resp
            scope_user = f"alm_{almacen.id}"
        elif usuario.es_administrador or usuario.acceso_todos_almacenes:
            almacen = None
            scope_user = f"global_rol_{usuario.rol}"
        else:
            almacen = None
            alms_ids = sorted(list(usuario.obtener_almacenes_accesibles().values_list("id", flat=True)))
            if not alms_ids:
                msg = "Tu cuenta de usuario no tiene un almacén asignado activo para realizar esta consulta."
                return {
                    "tipo": "consulta",
                    "mensaje_pantalla": msg,
                    "mensaje_verbal": normalizar_para_tts(msg),
                    "datos": {"error_permisos": True, "sin_almacen_asignado": True},
                }
            alms_firma = "-".join(map(str, alms_ids))
            scope_user = f"user_{usuario.id}_alms_{alms_firma}"

        # Revisar caché en RAM
        cached_data = HakuCacheManager.obtener_faltantes(almacen_id=almacen.id if almacen else None, scope_id=scope_user)
        if cached_data is not None:
            return {
                "tipo": "consulta",
                "mensaje_pantalla": cached_data["pantalla"],
                "mensaje_verbal": cached_data["verbal"],
                "datos": cached_data["datos"],
            }

        # Query de existencias activas
        qs_exist = Existencia.objects.filter(
            producto__activo=True,
            producto__archivado=False,
        ).select_related("producto", "producto__categoria", "almacen")

        if almacen:
            qs_exist = qs_exist.filter(almacen=almacen)
        elif usuario.es_usuario_normal and not usuario.acceso_todos_almacenes:
            alms_acc = usuario.obtener_almacenes_accesibles()
            qs_exist = qs_exist.filter(almacen__in=alms_acc)

        filtro_nombre = intencion.datos_extra.get("filtro_nombre") if intencion.datos_extra else None
        if filtro_nombre:
            qs_exist = qs_exist.filter(
                Q(producto__nombre__icontains=filtro_nombre) | Q(producto__categoria__nombre__icontains=filtro_nombre)
            )

        agotados_items = []
        bajo_stock_items = []
        items_datos = []

        for e in qs_exist:
            prod = e.producto
            disponible = e.stock_disponible
            prestado = e.stock_en_prestamo
            minimo = prod.stock_minimo
            fisico = disponible + prestado

            if disponible == 0:
                motivo = "Sin existencias disponibles"
                if prestado > 0:
                    motivo += f" ({prestado} unidad(es) prestadas)"
                sug_compra = max(0, minimo - disponible)
                agotados_items.append({
                    "existencia": e,
                    "motivo": motivo,
                    "sugerido": sug_compra if sug_compra > 0 else (1 if minimo == 0 else minimo),
                })
                items_datos.append({
                    "id": prod.id,
                    "codigo": prod.codigo_interno,
                    "nombre": prod.nombre,
                    "categoria": prod.categoria.nombre,
                    "almacen": e.almacen.nombre,
                    "stock_fisico": fisico,
                    "stock_prestado": prestado,
                    "stock_disponible": disponible,
                    "stock_minimo": minimo,
                    "estado_alerta": "AGOTADO",
                    "motivo_alerta": motivo,
                    "cantidad_sugerida_reposicion": sug_compra if sug_compra > 0 else (1 if minimo == 0 else minimo),
                    "consumo_estimado": "Sin historial suficiente para predicción de consumo",
                })
            elif e.stock_bajo:
                motivo = f"Por debajo del mínimo ({disponible} de {minimo})"
                sug_compra = max(0, minimo - disponible)
                bajo_stock_items.append({
                    "existencia": e,
                    "motivo": motivo,
                    "sugerido": sug_compra,
                })
                items_datos.append({
                    "id": prod.id,
                    "codigo": prod.codigo_interno,
                    "nombre": prod.nombre,
                    "categoria": prod.categoria.nombre,
                    "almacen": e.almacen.nombre,
                    "stock_fisico": fisico,
                    "stock_prestado": prestado,
                    "stock_disponible": disponible,
                    "stock_minimo": minimo,
                    "estado_alerta": "BAJO_STOCK",
                    "motivo_alerta": motivo,
                    "cantidad_sugerida_reposicion": sug_compra,
                    "consumo_estimado": "Sin historial suficiente para predicción de consumo",
                })

        total_faltantes = len(items_datos)

        if total_faltantes == 0:
            msg = (
                f"No hay productos que requieran reposición en {almacen.nombre}."
                if almacen else "No hay productos que requieran reposición en los almacenes accesibles."
            )
            return {
                "tipo": "consulta",
                "mensaje_pantalla": msg,
                "mensaje_verbal": normalizar_para_tts(msg),
                "datos": {"faltantes": [], "total": 0},
            }

        lineas_pantalla = ["### Reporte de Reposición y Necesidades de Compra\n"]
        lineas_voz = []

        if agotados_items:
            lineas_pantalla.append(f"**Agotados sin stock disponible ({len(agotados_items)}):**")
            lineas_voz.append(f"Hay {len(agotados_items)} productos agotados.")
            for it in agotados_items[:6]:
                ex = it["existencia"]
                lineas_pantalla.append(
                    f"- 🔴 **{ex.producto.nombre}** (`{ex.producto.codigo_interno}`) en {ex.almacen.nombre}: "
                    f"0 disponibles (físico: {ex.stock_total}, prestados: {ex.stock_en_prestamo}, mínimo: {ex.producto.stock_minimo}). "
                    f"*Sugerido reponer:* {it['sugerido']} ud(s)."
                )

        if bajo_stock_items:
            lineas_pantalla.append(f"\n**Stock bajo por debajo del mínimo ({len(bajo_stock_items)}):**")
            lineas_voz.append(f"{len(bajo_stock_items)} productos están por debajo de su stock mínimo.")
            for it in bajo_stock_items[:6]:
                ex = it["existencia"]
                lineas_pantalla.append(
                    f"- 🟡 **{ex.producto.nombre}** (`{ex.producto.codigo_interno}`) en {ex.almacen.nombre}: "
                    f"**{ex.stock_disponible}** disponibles de **{ex.producto.stock_minimo}** requeridos. "
                    f"*Sugerido reponer:* {it['sugerido']} ud(s)."
                )

        lineas_pantalla.append(
            "\n*Nota: Las sugerencias de compra se basan estrictamente en alcanzar el stock mínimo configurado. "
            "No se calculan plazos de agotamiento ficticios al no disponer de tasas de consumo histórico.*"
        )

        nombres_destacados = [it["existencia"].producto.nombre for it in (agotados_items + bajo_stock_items)[:3]]
        lineas_voz.append(f"Entre ellos: {', '.join(nombres_destacados)}.")

        txt_pantalla = "\n".join(lineas_pantalla)
        txt_verbal = normalizar_para_tts(" ".join(lineas_voz))

        if estado_operativo:
            estado_operativo.asignar_coleccion(items_datos, total_original=total_faltantes)
            estado_operativo.registrar_accion("CONSULTAR_FALTANTES_REPOSICION", cantidad=total_faltantes)
            estado_operativo.producto_id = None
            estado_operativo.producto_codigo = None
            estado_operativo.producto_nombre = None
            HakuOperationalContext.guardar(estado_operativo)

        # Guardar en caché
        HakuCacheManager.guardar_faltantes(
            {"pantalla": txt_pantalla, "verbal": txt_verbal, "datos": {"faltantes": items_datos, "total": total_faltantes}},
            almacen_id=almacen.id if almacen else None,
            scope_id=scope_user,
        )

        return {
            "tipo": "consulta",
            "mensaje_pantalla": txt_pantalla,
            "mensaje_verbal": txt_verbal,
            "datos": {"faltantes": items_datos, "total": total_faltantes},
        }

    # 4. Movimientos por producto específico
    if intencion.tipo == IntencionTipo.CONSULTAR_PRODUCTO_MOVIMIENTOS:
        producto = intencion.producto
        if not producto:
            msg = "¿De cuál producto deseas consultar los movimientos?"
            return {"tipo": "aclaracion", "mensaje_pantalla": msg, "mensaje_verbal": msg}

        if sesion is not None:
            sesion["haku_ultimo_producto_id"] = str(producto.id)
            sesion["haku_ultimo_acceso"] = time.time()

        from .servicios_temporales import resolver_intervalo_temporal, obtener_ahora_local
        intervalo = resolver_intervalo_temporal(texto)
        if not intervalo:
            ahora = obtener_ahora_local()
            desc_periodo = "el periodo consultado"
            filtro_tiempo = {}
        else:
            desc_periodo = intervalo.descripcion_legible
            filtro_tiempo = {
                "fecha_hora__gte": intervalo.inicio,
                "fecha_hora__lt": intervalo.fin,
            }

        qs_mov = Movimiento.objects.filter(
            producto=producto,
            estado=Movimiento.Estado.CONFIRMADO,
            **filtro_tiempo,
        ).select_related("almacen_origen", "almacen_destino")

        # Filtrar almacenes según permisos
        if intencion.almacen:
            almacen_filtrado, error_resp = _validar_acceso_almacen(usuario, intencion.almacen)
            if error_resp:
                return error_resp
            qs_mov = qs_mov.filter(Q(almacen_origen=almacen_filtrado) | Q(almacen_destino=almacen_filtrado)).distinct()
            desc_periodo += f" en {almacen_filtrado.nombre}"
        elif usuario.es_usuario_normal:
            if usuario.acceso_todos_almacenes:
                pass
            else:
                alms = usuario.obtener_almacenes_accesibles()
                if not alms.exists():
                    msg = "Tu cuenta de usuario no tiene un almacén asignado para consultar movimientos."
                    return {"tipo": "consulta", "mensaje_pantalla": msg, "mensaje_verbal": normalizar_para_tts(msg), "datos": {}}
                qs_mov = qs_mov.filter(Q(almacen_origen__in=alms) | Q(almacen_destino__in=alms)).distinct()

        total_mov = qs_mov.count()
        entradas = qs_mov.filter(tipo=Movimiento.Tipo.ENTRADA).count()
        salidas = qs_mov.filter(tipo=Movimiento.Tipo.SALIDA).count()
        prestamos = qs_mov.filter(tipo=Movimiento.Tipo.PRESTAMO).count()
        devoluciones = qs_mov.filter(tipo=Movimiento.Tipo.DEVOLUCION).count()
        traslados = qs_mov.filter(tipo__in=[Movimiento.Tipo.TRASLADO_ENTRE, Movimiento.Tipo.TRASLADO_INTERNO]).count()

        if estado_operativo:
            estado_operativo.registrar_accion(
                "CONSULTAR_PRODUCTO_MOVIMIENTOS",
                producto=producto.nombre,
                producto_id=producto.id,
                codigo=producto.codigo_interno,
                cantidad=total_mov,
            )
            HakuOperationalContext.guardar(estado_operativo)

        if total_mov == 0:
            pantalla = f"El producto {producto.nombre} (`{producto.codigo_interno}`) no registra movimientos de inventario en {desc_periodo}."
            verbal = f"El producto {producto.nombre} no registra movimientos de inventario en {desc_periodo}."
        else:
            detalle_lineas = []
            for m in qs_mov[:8]:
                fecha_str = m.fecha_hora.astimezone(obtener_ahora_local().tzinfo).strftime("%d/%m %H:%M")
                alm_txt = m.almacen_origen.nombre if m.almacen_origen else ""
                if m.almacen_destino:
                    alm_txt += f" ➔ {m.almacen_destino.nombre}"
                detalle_lineas.append(f"- `{fecha_str}` | **{m.get_tipo_display()}**: {m.cantidad} uds ({alm_txt})")

            bloque_det = "\n".join(detalle_lineas)
            extra_pag = f"\n\n*(Mostrando primeros 8 de {total_mov} movimientos)*" if total_mov > 8 else ""

            pantalla = (
                f"### Movimientos de {producto.nombre} (`{producto.codigo_interno}`) — {desc_periodo}\n"
                f"El {producto.nombre} tuvo **{total_mov}** movimientos:\n"
                f"- **{entradas}** entradas | **{salidas}** salidas | **{prestamos}** préstamos | **{devoluciones}** devoluciones | **{traslados}** traslados\n\n"
                f"**Detalle de actividad:**\n{bloque_det}{extra_pag}"
            )
            verbal = (
                f"El producto {producto.nombre} tuvo {total_mov} movimientos durante {desc_periodo}: "
                f"{entradas} entradas, {salidas} salidas, {prestamos} préstamos y {traslados} traslados."
            )
        return {
            "tipo": "consulta",
            "mensaje_pantalla": pantalla,
            "mensaje_verbal": normalizar_para_tts(verbal),
            "datos": {
                "producto_id": str(producto.id),
                "codigo": producto.codigo_interno,
                "nombre": producto.nombre,
                "periodo": desc_periodo,
                "total": total_mov,
                "entradas": entradas,
                "salidas": salidas,
                "prestamos": prestamos,
                "devoluciones": devoluciones,
                "traslados": traslados,
            },
        }

    # 5. Alertas de stock bajo / agotado
    if intencion.tipo == IntencionTipo.ALERTAS_STOCK:
        almacen, error_resp = _validar_acceso_almacen(usuario, intencion.almacen)
        if error_resp:
            return error_resp

        qs_existencias = Existencia.objects.filter(
            producto__activo=True,
            producto__archivado=False,
        ).select_related("producto", "almacen")

        if almacen:
            qs_existencias = qs_existencias.filter(almacen=almacen)

        filtro_nombre = intencion.datos_extra.get("filtro_nombre") if intencion.datos_extra else None
        if filtro_nombre:
            qs_existencias = qs_existencias.filter(
                Q(producto__nombre__icontains=filtro_nombre) | Q(producto__categoria__nombre__icontains=filtro_nombre)
            )

        sin_stock = [e for e in qs_existencias if e.sin_stock][:5]
        bajo_stock = [e for e in qs_existencias if e.stock_bajo][:5]

        if not sin_stock and not bajo_stock:
            if almacen:
                res = f"No hay alertas activas de stock en {almacen.nombre}. Todos los productos tienen existencias suficientes."
            elif filtro_nombre:
                res = f"No hay alertas activas de stock para '{filtro_nombre}'. Todos los productos tienen existencias suficientes."
            else:
                res = "No hay alertas activas de stock en este momento. Todos los productos tienen existencias suficientes."
            return {"tipo": "consulta", "mensaje_pantalla": res, "mensaje_verbal": res, "datos": {}}

        lineas = []
        lineas_voz = []
        if sin_stock:
            nombres = ", ".join(f"{e.producto.nombre} ({e.almacen.nombre})" for e in sin_stock)
            lineas.append(f"**Agotados ({len(sin_stock)}):** {nombres}")
            lineas_voz.append(f"Hay {len(sin_stock)} productos agotados: {nombres}.")
        if bajo_stock:
            nombres = ", ".join(f"{e.producto.nombre} ({e.stock_disponible}/{e.producto.stock_minimo})" for e in bajo_stock)
            lineas.append(f"**Stock bajo ({len(bajo_stock)}):** {nombres}")
            lineas_voz.append(f"Hay {len(bajo_stock)} productos con stock bajo.")

        if estado_operativo:
            lista_alertas = []
            for e in sin_stock + bajo_stock:
                lista_alertas.append({
                    "id": e.producto.id,
                    "producto_id": str(e.producto.id),
                    "codigo": e.producto.codigo_interno,
                    "nombre": e.producto.nombre,
                    "almacen": e.almacen.nombre,
                })
            estado_operativo.asignar_coleccion(lista_alertas)
            estado_operativo.registrar_accion(
                "ALERTAS_STOCK",
                cantidad=len(sin_stock) + len(bajo_stock),
            )
            estado_operativo.producto_id = None
            estado_operativo.producto_codigo = None
            estado_operativo.producto_nombre = None
            HakuOperationalContext.guardar(estado_operativo)

        return {
            "tipo": "consulta",
            "mensaje_pantalla": "\n\n".join(lineas),
            "mensaje_verbal": " ".join(lineas_voz),
            "datos": {
                "sin_stock": [{"codigo": e.producto.codigo_interno, "nombre": e.producto.nombre, "almacen": e.almacen.nombre} for e in sin_stock],
                "bajo_stock": [
                    {"codigo": e.producto.codigo_interno, "nombre": e.producto.nombre, "almacen": e.almacen.nombre, "disponible": e.stock_disponible, "minimo": e.producto.stock_minimo}
                    for e in bajo_stock
                ],
            },
        }

    # 3. Préstamos activos
    if intencion.tipo == IntencionTipo.PRESTAMOS_ACTIVOS:
        almacen, error_resp = _validar_acceso_almacen(usuario, intencion.almacen)
        if error_resp:
            return error_resp

        qs_prestamos = Prestamo.objects.filter(
            estado__in=[Prestamo.Estado.ABIERTO, Prestamo.Estado.DEVUELTO_PARCIAL]
        ).select_related("producto", "almacen")

        if almacen:
            qs_prestamos = qs_prestamos.filter(almacen=almacen)

        producto = intencion.producto
        filtro_nombre = intencion.datos_extra.get("filtro_nombre") if intencion.datos_extra else None

        if producto:
            qs_prestamos = qs_prestamos.filter(producto=producto)
            if sesion is not None:
                sesion["haku_ultimo_producto_id"] = str(producto.id)
                sesion["haku_ultimo_acceso"] = time.time()
        elif filtro_nombre:
            qs_prestamos = qs_prestamos.filter(
                Q(producto__nombre__icontains=filtro_nombre) | Q(producto__categoria__nombre__icontains=filtro_nombre)
            )

        from django.db.models import Sum
        total_prestamos = qs_prestamos.count()
        agg_unidades = qs_prestamos.aggregate(Sum("cantidad_pendiente_devolucion"))
        total_unidades = agg_unidades.get("cantidad_pendiente_devolucion__sum") or 0
        total_productos_distintos = qs_prestamos.values("producto").distinct().count()
        prestamos = list(qs_prestamos[:10])
        metrica = intencion.datos_extra.get("metrica", "GENERAL") if intencion.datos_extra else "GENERAL"

        if not prestamos:
            if producto:
                if almacen:
                    msg = f"El producto {producto.nombre} no tiene préstamos activos pendientes en {almacen.nombre}."
                else:
                    msg = f"El producto {producto.nombre} no tiene préstamos activos pendientes."
            elif filtro_nombre:
                msg = f"No hay préstamos activos para productos de la categoría o tipo '{filtro_nombre}'."
            elif almacen:
                msg = f"No hay préstamos activos pendientes en {almacen.nombre}."
            else:
                msg = "No hay préstamos activos pendientes en este almacén." if usuario.es_usuario_normal else "No hay préstamos activos pendientes en el inventario."
            return {"tipo": "consulta", "mensaje_pantalla": msg, "mensaje_verbal": msg, "datos": {"prestamos": [], "total": 0, "total_prestamos": 0, "total_unidades": 0, "total_productos_distintos": 0}}

        items = []
        if producto:
            lineas_voz = [f"El {producto.nombre} tiene {total_prestamos} préstamo(s) activo(s) con {total_unidades} unidad(es) pendiente(s) de devolución:"]
            for p in prestamos:
                items.append(f"- **{p.cantidad_pendiente_devolucion} ud(s)** prestadas a **{p.destinatario}** ({p.almacen.nombre})")
                lineas_voz.append(f"{p.cantidad_pendiente_devolucion} unidades a {p.destinatario}.")
            if total_prestamos > 10:
                items.append(f"\n*Mostrando 10 de {total_prestamos} préstamos activos.*")
            pantalla = f"### Préstamos activos de {producto.nombre} (`{producto.codigo_interno}`):\n- **Total préstamos:** {total_prestamos}\n- **Unidades pendientes:** {total_unidades}\n\n" + "\n".join(items)
        elif metrica == "CONTEO_REGISTROS":
            lineas_voz = [f"Hay {total_prestamos} préstamo(s) activo(s) en este momento, correspondientes a {total_unidades} unidad(es) de {total_productos_distintos} producto(s) distinto(s)."]
            for p in prestamos:
                items.append(f"- **{p.producto.nombre}**: {p.cantidad_pendiente_devolucion} ud(s) prestadas a **{p.destinatario}** ({p.almacen.nombre})")
            if total_prestamos > 10:
                items.append(f"\n*Mostrando 10 de {total_prestamos} préstamos activos.*")
            pantalla = f"### Conteo de préstamos activos ({total_prestamos} en total):\n- **Total préstamos activos:** {total_prestamos}\n- **Total unidades prestadas:** {total_unidades}\n- **Productos distintos involucrados:** {total_productos_distintos}\n\n" + "\n".join(items)
        elif metrica == "CONTEO_UNIDADES":
            lineas_voz = [f"Hay {total_unidades} unidad(es) prestada(s) en total, distribuidas en {total_prestamos} préstamo(s)."]
            for p in prestamos:
                items.append(f"- **{p.producto.nombre}**: {p.cantidad_pendiente_devolucion} ud(s) prestadas a **{p.destinatario}** ({p.almacen.nombre})")
            if total_prestamos > 10:
                items.append(f"\n*Mostrando 10 de {total_prestamos} préstamos activos.*")
            pantalla = f"### Unidades activas en préstamo ({total_unidades} en total):\n- **Total unidades pendientes de devolución:** {total_unidades}\n- **Total préstamos activos:** {total_prestamos}\n- **Productos distintos involucrados:** {total_productos_distintos}\n\n" + "\n".join(items)
        elif metrica == "LISTA_RESPONSABLES":
            resp_str = ", ".join(f"{p.destinatario} ({p.cantidad_pendiente_devolucion} ud de {p.producto.nombre})" for p in prestamos[:3])
            lineas_voz = [f"Hay {total_prestamos} préstamos activos a cargo de: {resp_str}."]
            for p in prestamos:
                items.append(f"- **{p.destinatario}**: {p.cantidad_pendiente_devolucion} ud(s) de **{p.producto.nombre}** ({p.almacen.nombre})")
            if total_prestamos > 10:
                items.append(f"\n*Mostrando 10 de {total_prestamos} préstamos activos.*")
            pantalla = f"### Préstamos activos por responsable ({total_prestamos} en total):\n" + "\n".join(items)
        else:
            lineas_voz = [f"Hay {total_prestamos} préstamos activos:"]
            for p in prestamos:
                items.append(f"- **{p.producto.nombre}**: {p.cantidad_pendiente_devolucion} ud(s) prestadas a **{p.destinatario}** ({p.almacen.nombre})")
                lineas_voz.append(f"{p.cantidad_pendiente_devolucion} de {p.producto.nombre} a {p.destinatario}.")
            if total_prestamos > 10:
                items.append(f"\n*Mostrando 10 de {total_prestamos} préstamos activos.*")
            pantalla = f"### Préstamos activos ({total_prestamos} en total):\n" + "\n".join(items)

        if estado_operativo:
            if producto:
                estado_operativo.registrar_accion(
                    "PRESTAMOS_ACTIVOS_PRODUCTO",
                    producto=producto.nombre,
                    producto_id=producto.id,
                    codigo=producto.codigo_interno,
                    cantidad=total_prestamos,
                )
            else:
                lista_prestamos = [
                    {
                        "id": p.producto.id,
                        "producto_id": str(p.producto.id),
                        "codigo": p.producto.codigo_interno,
                        "nombre": p.producto.nombre,
                        "almacen": p.almacen.nombre,
                        "destinatario": p.destinatario,
                        "cantidad_prestada": p.cantidad_pendiente_devolucion,
                    }
                    for p in prestamos
                ]
                estado_operativo.asignar_coleccion(lista_prestamos, total_original=total_prestamos)
                estado_operativo.registrar_accion("PRESTAMOS_ACTIVOS", cantidad=total_prestamos)
                estado_operativo.producto_id = None
                estado_operativo.producto_codigo = None
                estado_operativo.producto_nombre = None
            HakuOperationalContext.guardar(estado_operativo)

        return {
            "tipo": "consulta",
            "mensaje_pantalla": pantalla,
            "mensaje_verbal": normalizar_para_tts(" ".join(lineas_voz)),
            "datos": {
                "prestamos": [
                    {
                        "id": p.id,
                        "producto": p.producto.nombre,
                        "codigo": p.producto.codigo_interno,
                        "destinatario": p.destinatario,
                        "cantidad": p.cantidad_pendiente_devolucion,
                        "almacen": p.almacen.nombre,
                    }
                    for p in prestamos
                ],
                "total": total_prestamos,
            },
        }

    # 4-bis. Consulta de ubicación de colección o conjunto de productos
    if intencion.tipo == IntencionTipo.CONSULTAR_UBICACION_COLECCION:
        coleccion = []
        if intencion.candidatos:
            coleccion = intencion.candidatos
        elif intencion.datos_extra and intencion.datos_extra.get("candidatos_plurales"):
            coleccion = intencion.datos_extra["candidatos_plurales"]
        elif estado_operativo and estado_operativo.coleccion_productos:
            coleccion = estado_operativo.coleccion_productos

        if not coleccion:
            msg = "No tengo una lista previa de productos en la conversación activa. Puedes consultar el catálogo o los productos con stock primero."
            return {
                "tipo": "aclaracion",
                "mensaje_pantalla": msg,
                "mensaje_verbal": normalizar_para_tts(msg),
                "datos": {"productos": []},
            }

        total_orig = (estado_operativo.coleccion_total_original if estado_operativo else 0) or len(coleccion)
        if total_orig < len(coleccion):
            total_orig = len(coleccion)
        es_parcial = total_orig > len(coleccion)

        lineas_pantalla = ["### Ubicación de las referencias consultadas:\n"]
        if es_parcial:
            lineas_voz = [f"Ubicación de {len(coleccion)} de las {total_orig} referencias consultadas:"]
        else:
            lineas_voz = [f"Ubicación de {len(coleccion)} productos:"]
        lista_datos = []

        alms_usuario = list(usuario.obtener_almacenes_accesibles()) if usuario.es_usuario_normal and not usuario.acceso_todos_almacenes else []

        for item in coleccion:
            prod_id = item.get("id") or item.get("producto_id")
            prod = Producto.objects.filter(id=prod_id, activo=True, archivado=False).first() if prod_id else None
            if not prod:
                continue

            alm = None
            if alms_usuario:
                alm_cat = getattr(prod.categoria, "almacen_principal", None)
                if alm_cat and alm_cat in alms_usuario:
                    alm = alm_cat
                else:
                    alm = alms_usuario[0]
            else:
                alm = getattr(prod.categoria, "almacen_principal", None)
            ubicaciones = HakuCacheManager.obtener_o_cargar_ubicacion(prod)
            nombre_alm = alm.nombre if alm else "Sin almacén"

            lineas_pantalla.append(f"- **{prod.nombre}** (`{prod.codigo_interno}`): **{ubicaciones}** en *{nombre_alm}*")
            lineas_voz.append(f"{prod.nombre} en {ubicaciones}, {nombre_alm}.")
            lista_datos.append({
                "producto_id": str(prod.id),
                "codigo": prod.codigo_interno,
                "nombre": prod.nombre,
                "ubicaciones": ubicaciones,
                "almacen": nombre_alm,
            })

        if es_parcial:
            lineas_pantalla.append(
                f"\n*Aviso: Mostrando la ubicación de las {len(lista_datos)} referencias conservadas en el foco conversacional activo (de {total_orig} productos devueltos por la consulta anterior).*"
            )

        if estado_operativo:
            estado_operativo.registrar_accion(
                "CONSULTAR_UBICACION_COLECCION",
                cantidad=len(lista_datos),
            )
            HakuOperationalContext.guardar(estado_operativo)

        return {
            "tipo": "consulta",
            "mensaje_pantalla": "\n".join(lineas_pantalla),
            "mensaje_verbal": normalizar_para_tts(" ".join(lineas_voz)),
            "datos": {
                "productos": lista_datos,
                "total": len(lista_datos),
                "total_original": total_orig,
                "es_parcial": es_parcial,
            },
        }

    # 4. Consulta de ubicación específica
    if intencion.tipo == IntencionTipo.CONSULTAR_UBICACION:
        producto = intencion.producto
        if not producto:
            msg = "¿De cuál producto deseas conocer la ubicación?"
            return {"tipo": "aclaracion", "mensaje_pantalla": msg, "mensaje_verbal": msg}

        if sesion is not None:
            sesion["haku_ultimo_producto_id"] = str(producto.id)
            sesion["haku_ultimo_acceso"] = time.time()

        almacen, error_resp = _validar_acceso_almacen(usuario, intencion.almacen)
        if error_resp:
            return error_resp

        if not almacen and not usuario.es_usuario_normal:
            almacen = producto.categoria.almacen_principal

        cached_stock = HakuCacheManager.obtener_o_cargar_stock(producto.id, almacen.id if almacen else None)
        disponible = cached_stock["disponible"]
        if almacen:
            qs_ub = producto.ubicaciones.filter(almacen=almacen)
            ubicaciones = ", ".join(u.nombre for u in qs_ub) if qs_ub.exists() else f"Sin ubicación registrada en {almacen.nombre}"
        else:
            ubicaciones = HakuCacheManager.obtener_o_cargar_ubicacion(producto)

        if estado_operativo:
            estado_operativo.registrar_accion(
                "CONSULTAR_UBICACION",
                producto=producto.nombre,
                producto_id=producto.id,
                codigo=producto.codigo_interno,
            )
            HakuOperationalContext.guardar(estado_operativo)

        pantalla = (
            f"### Ubicación de {producto.nombre} (`{producto.codigo_interno}`)\n"
            f"- **Ubicación:** {ubicaciones}\n"
            f"- **Almacén:** {almacen.nombre if almacen else 'Sin almacén'}\n"
            f"- **Stock disponible:** {disponible} unidades"
        )
        voz = f"{producto.nombre} está ubicado en {ubicaciones}, en {almacen.nombre if almacen else 'el almacén'}."
        return {
            "tipo": "consulta",
            "mensaje_pantalla": pantalla,
            "mensaje_verbal": normalizar_para_tts(voz),
            "datos": {"producto_id": str(producto.id), "codigo": producto.codigo_interno, "ubicaciones": ubicaciones},
        }

    # 5. Consulta de stock o búsqueda de producto
    if intencion.tipo in (IntencionTipo.CONSULTAR_STOCK, IntencionTipo.BUSCAR_PRODUCTO):
        producto = intencion.producto
        if not producto:
            msg = "¿De cuál producto deseas consultar el stock o existencias?"
            return {"tipo": "aclaracion", "mensaje_pantalla": msg, "mensaje_verbal": msg}

        if sesion is not None:
            sesion["haku_ultimo_producto_id"] = str(producto.id)
            sesion["haku_ultimo_acceso"] = time.time()

        almacen, error_resp = _validar_acceso_almacen(usuario, intencion.almacen)
        if error_resp:
            return error_resp

        if not almacen and not usuario.es_usuario_normal:
            almacen = producto.categoria.almacen_principal

        otro_almacen = bool(intencion.datos_extra and intencion.datos_extra.get("otro_almacen"))

        if otro_almacen:
            if usuario.es_usuario_normal:
                msg = f"Tu cuenta de usuario solo tiene acceso al almacén {almacen.nombre if almacen else 'asignado'}. No tienes permisos para consultar existencias en otros almacenes."
                return {"tipo": "consulta", "mensaje_pantalla": msg, "mensaje_verbal": normalizar_para_tts(msg), "datos": {"producto_id": str(producto.id), "codigo": producto.codigo_interno}}

            existencias_otros = Existencia.objects.filter(
                producto=producto,
                almacen__activo=True
            ).exclude(almacen=almacen).select_related("almacen")

            if not existencias_otros.exists():
                pantalla = f"### {producto.nombre} (`{producto.codigo_interno}`)\nNo hay existencias registradas de este producto en otros almacenes diferentes a {almacen.nombre if almacen else 'el principal'}."
                voz = f"No hay existencias registradas de {producto.nombre} en otros almacenes."
                return {
                    "tipo": "consulta",
                    "mensaje_pantalla": pantalla,
                    "mensaje_verbal": normalizar_para_tts(voz),
                    "datos": {"producto_id": str(producto.id), "codigo": producto.codigo_interno, "otros_almacenes": []},
                }

            lineas_p = [f"### Existencias de {producto.nombre} (`{producto.codigo_interno}`) en otros almacenes:\n"]
            lineas_v = [f"En otros almacenes, de {producto.nombre}:"]
            datos_otros = []
            for eo in existencias_otros:
                lineas_p.append(f"- **{eo.almacen.nombre}:** {eo.stock_disponible} disponibles ({eo.stock_en_prestamo} en préstamo)")
                lineas_v.append(f"En {eo.almacen.nombre}, {eo.stock_disponible} unidades disponibles.")
                datos_otros.append({
                    "almacen": eo.almacen.nombre,
                    "disponible": eo.stock_disponible,
                    "prestado": eo.stock_en_prestamo,
                })

            if estado_operativo:
                estado_operativo.registrar_accion(
                    "CONSULTAR_STOCK_OTROS_ALMACENES",
                    producto=producto.nombre,
                    producto_id=producto.id,
                    codigo=producto.codigo_interno,
                )
                HakuOperationalContext.guardar(estado_operativo)

            return {
                "tipo": "consulta",
                "mensaje_pantalla": "\n".join(lineas_p),
                "mensaje_verbal": normalizar_para_tts(" ".join(lineas_v)),
                "datos": {
                    "producto_id": str(producto.id),
                    "codigo": producto.codigo_interno,
                    "nombre": producto.nombre,
                    "otros_almacenes": datos_otros,
                },
            }

        cached_stock = HakuCacheManager.obtener_o_cargar_stock(producto.id, almacen.id if almacen else None)
        disponible = cached_stock["disponible"]
        prestado = cached_stock["prestado"]
        if almacen:
            qs_ub = producto.ubicaciones.filter(almacen=almacen)
            ubicaciones = ", ".join(u.nombre for u in qs_ub) if qs_ub.exists() else f"Sin ubicación registrada en {almacen.nombre}"
        else:
            ubicaciones = HakuCacheManager.obtener_o_cargar_ubicacion(producto)

        if estado_operativo:
            estado_operativo.registrar_accion(
                "CONSULTAR_STOCK",
                producto=producto.nombre,
                producto_id=producto.id,
                codigo=producto.codigo_interno,
                cantidad=disponible,
            )
            HakuOperationalContext.guardar(estado_operativo)

        pantalla = (
            f"### {producto.nombre} (`{producto.codigo_interno}`)\n"
            f"- **Stock disponible:** {disponible} unidades\n"
            f"- **En préstamo:** {prestado} unidades\n"
            f"- **Stock mínimo:** {producto.stock_minimo}\n"
            f"- **Almacén:** {almacen.nombre if almacen else 'Sin almacén'}\n"
            f"- **Ubicación:** {ubicaciones}"
        )
        voz = (
            f"De {producto.nombre}, hay {disponible} unidades disponibles en {almacen.nombre if almacen else 'el almacén'}, "
            f"y {prestado} en préstamo. Ubicación: {ubicaciones}."
        )
        return {
            "tipo": "consulta",
            "mensaje_pantalla": pantalla,
            "mensaje_verbal": normalizar_para_tts(voz),
            "datos": {
                "codigo": producto.codigo_interno,
                "nombre": producto.nombre,
                "disponible": disponible,
                "en_prestamo": prestado,
            },
        }

    # ─────────────────────────────────────────────────────────────────────────
    # C. MUTACIONES DE INVENTARIO (PRE-CONFIRMACIÓN OBLIGATORIA EN DOS PASOS)
    # ─────────────────────────────────────────────────────────────────────────

    # 6. Entrada de inventario
    if intencion.tipo == IntencionTipo.REGISTRAR_ENTRADA:
        producto = intencion.producto
        cantidad = intencion.cantidad

        if not producto:
            msg = "Indica el nombre o código del producto para registrar la entrada."
            return {"tipo": "aclaracion", "mensaje_pantalla": msg, "mensaje_verbal": msg}

        if not cantidad or cantidad <= 0:
            msg = f"¿Cuántas unidades de {producto.nombre} vas a ingresar?"
            return {"tipo": "aclaracion", "mensaje_pantalla": msg, "mensaje_verbal": msg}

        almacen, error_almacen = _resolver_almacen_para_operacion(
            usuario=usuario,
            almacen_intencion=intencion.almacen,
            producto=producto,
            operacion="entrada",
        )
        if error_almacen:
            return error_almacen

        if not usuario.tiene_acceso_almacen(almacen):
            raise PermissionDenied("No tienes acceso al almacén de este producto.")

        if sesion is not None:
            sesion["haku_ultimo_producto_id"] = str(producto.id)
            sesion["haku_ultimo_acceso"] = time.time()

        propuesta = {
            "id": str(uuid.uuid4()),
            "tipo_operacion": Movimiento.Tipo.ENTRADA,
            "producto_id": producto.id,
            "producto_nombre": producto.nombre,
            "producto_codigo": producto.codigo_interno,
            "cantidad": cantidad,
            "almacen_origen_id": almacen.id,
            "almacen_origen_nombre": almacen.nombre,
            "destinatario": "",
            "motivo": "Entrada registrada por Haku",
            "creado_en": time.time(),
            "expira_en": time.time() + HAKU_PROPUESTA_TIMEOUT_SEGUNDOS,
        }

        if estado_operativo:
            estado_operativo.propuesta_activa = propuesta
            estado_operativo.registrar_accion(
                "PROPUESTA_ENTRADA",
                producto=producto.nombre,
                producto_id=producto.id,
                codigo=producto.codigo_interno,
                cantidad=cantidad,
            )
            HakuOperationalContext.guardar(estado_operativo)
        if sesion is not None:
            sesion["haku_propuesta_activa"] = propuesta
        HakuDiagnostico.registrar_propuesta("generada")

        verbal = (
            f"Vas a registrar la entrada de {cantidad} unidades de {producto.nombre} "
            f"al almacén {almacen.nombre}. ¿Deseas confirmar la operación?"
        )
        pantalla = (
            f"### Propuesta pendiente de confirmación\n"
            f"- **Operación:** Entrada de stock\n"
            f"- **Producto:** {producto.nombre} (`{producto.codigo_interno}`)\n"
            f"- **Cantidad:** +{cantidad} unidades\n"
            f"- **Almacén:** {almacen.nombre}\n\n"
            f"*Di 'Confirmar' o presiona el botón para ejecutar la transacción.*"
        )
        return {
            "tipo": "propuesta",
            "requiere_confirmacion": True,
            "propuesta": propuesta,
            "mensaje_pantalla": pantalla,
            "mensaje_verbal": normalizar_para_tts(verbal),
        }

    # 7. Salida de inventario
    if intencion.tipo == IntencionTipo.REGISTRAR_SALIDA:
        producto = intencion.producto
        cantidad = intencion.cantidad
        destinatario = intencion.destinatario or ""
        motivo = intencion.motivo or ""

        if not producto:
            msg = "Por favor indica qué producto vas a retirar del inventario."
            return {"tipo": "aclaracion", "mensaje_pantalla": msg, "mensaje_verbal": msg}

        if not cantidad or cantidad <= 0:
            msg = f"¿Cuántas unidades de {producto.nombre} vas a retirar?"
            return {"tipo": "aclaracion", "mensaje_pantalla": msg, "mensaje_verbal": msg}

        if not destinatario:
            msg = f"Para registrar la salida de {producto.nombre}, ¿a quién se le entregan las unidades?"
            return {"tipo": "aclaracion", "mensaje_pantalla": msg, "mensaje_verbal": msg}

        if not motivo:
            motivo = f"Salida registrada por Haku para {destinatario}"

        almacen, error_resp = _resolver_almacen_para_operacion(usuario, intencion.almacen, producto, operacion="salida")
        if error_resp:
            return error_resp

        existencia = Existencia.objects.filter(producto=producto, almacen=almacen).first()
        disponible = existencia.stock_disponible if existencia else 0
        if disponible < cantidad:
            msg = f"Stock insuficiente: {producto.nombre} solo tiene {disponible} disponibles en {almacen.nombre if almacen else 'el almacén'}."
            return {"tipo": "error", "mensaje_pantalla": msg, "mensaje_verbal": msg}

        if sesion is not None:
            sesion["haku_ultimo_producto_id"] = str(producto.id)
            sesion["haku_ultimo_acceso"] = time.time()

        propuesta = {
            "id": str(uuid.uuid4()),
            "tipo_operacion": Movimiento.Tipo.SALIDA,
            "producto_id": producto.id,
            "producto_nombre": producto.nombre,
            "producto_codigo": producto.codigo_interno,
            "cantidad": cantidad,
            "almacen_origen_id": almacen.id,
            "almacen_origen_nombre": almacen.nombre,
            "destinatario": destinatario,
            "motivo": motivo,
            "creado_en": time.time(),
            "expira_en": time.time() + HAKU_PROPUESTA_TIMEOUT_SEGUNDOS,
        }

        if estado_operativo:
            estado_operativo.propuesta_activa = propuesta
            estado_operativo.registrar_accion(
                "PROPUESTA_SALIDA",
                producto=producto.nombre,
                producto_id=producto.id,
                codigo=producto.codigo_interno,
                cantidad=cantidad,
            )
            HakuOperationalContext.guardar(estado_operativo)
        if sesion is not None:
            sesion["haku_propuesta_activa"] = propuesta
        HakuDiagnostico.registrar_propuesta("generada")

        es_dotacion = bool(motivo and "dotación" in motivo.lower() or "entrega definitiva" in motivo.lower())
        tipo_op_label = "Dotación / Entrega definitiva (consumo oficial)" if es_dotacion else "Salida de stock"

        verbal = (
            f"Vas a registrar la dotación de {cantidad} unidades de {producto.nombre} para {destinatario}. "
            f"Es una salida definitiva de inventario. ¿Deseas confirmar la operación?"
            if es_dotacion else
            f"Vas a registrar la salida de {cantidad} unidades de {producto.nombre} para {destinatario}, "
            f"con motivo {motivo}. ¿Deseas confirmar la operación?"
        )
        pantalla = (
            f"### Propuesta pendiente de confirmación\n"
            f"- **Operación:** {tipo_op_label}\n"
            f"- **Producto:** {producto.nombre} (`{producto.codigo_interno}`)\n"
            f"- **Cantidad:** -{cantidad} unidades\n"
            f"- **Almacén:** {almacen.nombre}\n"
            f"- **Destinatario:** {destinatario}\n"
            f"- **Motivo:** {motivo}\n\n"
            f"*Di 'Confirmar' o presiona el botón para ejecutar la transacción.*"
        )
        return {
            "tipo": "propuesta",
            "requiere_confirmacion": True,
            "propuesta": propuesta,
            "mensaje_pantalla": pantalla,
            "mensaje_verbal": normalizar_para_tts(verbal),
        }

    # 8. Devolución de préstamo
    if intencion.tipo == IntencionTipo.DEVOLUCION:
        destinatario = intencion.destinatario or ""
        qs_prestamos = Prestamo.objects.filter(
            estado__in=[Prestamo.Estado.ABIERTO, Prestamo.Estado.DEVUELTO_PARCIAL]
        ).select_related("producto", "almacen")
        if usuario.es_usuario_normal and not usuario.acceso_todos_almacenes:
            alms_usuario = usuario.obtener_almacenes_accesibles()
            qs_prestamos = qs_prestamos.filter(almacen__in=alms_usuario)

        prestamo = None
        if destinatario:
            prestamo = qs_prestamos.filter(destinatario__icontains=destinatario).first()
            if not prestamo:
                for w in destinatario.split():
                    if len(w) >= 3:
                        prestamo = qs_prestamos.filter(destinatario__icontains=w).first()
                        if prestamo:
                            break
        if not prestamo and intencion.producto:
            prestamo = qs_prestamos.filter(producto=intencion.producto).first()
        if not prestamo:
            prestamo = qs_prestamos.first()

        if not prestamo:
            msg = "No encontré ningún préstamo activo con esos datos para devolver."
            return {"tipo": "conversacion", "mensaje_pantalla": msg, "mensaje_verbal": msg}

        cantidad_dev = intencion.cantidad if intencion.cantidad else prestamo.cantidad_pendiente_devolucion
        if cantidad_dev > prestamo.cantidad_pendiente_devolucion:
            msg = f"La cantidad a devolver ({cantidad_dev}) excede el saldo prestado ({prestamo.cantidad_pendiente_devolucion})."
            return {"tipo": "error", "mensaje_pantalla": msg, "mensaje_verbal": msg}

        if sesion is not None:
            sesion["haku_ultimo_producto_id"] = str(prestamo.producto.id)
            sesion["haku_ultimo_acceso"] = time.time()

        propuesta = {
            "id": str(uuid.uuid4()),
            "tipo_operacion": Movimiento.Tipo.DEVOLUCION,
            "prestamo_id": str(prestamo.id),
            "producto_id": prestamo.producto.id,
            "producto_nombre": prestamo.producto.nombre,
            "producto_codigo": prestamo.producto.codigo_interno,
            "cantidad": cantidad_dev,
            "almacen_origen_id": prestamo.almacen.id,
            "almacen_origen_nombre": prestamo.almacen.nombre,
            "destinatario": prestamo.destinatario,
            "motivo": f"Devolución de préstamo por {prestamo.destinatario}",
            "creado_en": time.time(),
            "expira_en": time.time() + HAKU_PROPUESTA_TIMEOUT_SEGUNDOS,
        }

        if estado_operativo:
            estado_operativo.propuesta_activa = propuesta
            estado_operativo.registrar_accion(
                "PROPUESTA_DEVOLUCION",
                producto=prestamo.producto.nombre,
                producto_id=prestamo.producto.id,
                codigo=prestamo.producto.codigo_interno,
                cantidad=cantidad_dev,
            )
            HakuOperationalContext.guardar(estado_operativo)
        if sesion is not None:
            sesion["haku_propuesta_activa"] = propuesta
        HakuDiagnostico.registrar_propuesta("generada")

        verbal = (
            f"Vas a registrar la devolución de {cantidad_dev} unidades de {prestamo.producto.nombre} "
            f"prestadas a {prestamo.destinatario}. ¿Deseas confirmar?"
        )
        pantalla = (
            f"### Propuesta pendiente de confirmación\n"
            f"- **Operación:** Devolución de préstamo\n"
            f"- **Producto:** {prestamo.producto.nombre} (`{prestamo.producto.codigo_interno}`)\n"
            f"- **Cantidad devuelta:** +{cantidad_dev} unidades\n"
            f"- **Responsable:** {prestamo.destinatario}\n"
            f"- **Almacén:** {prestamo.almacen.nombre}\n\n"
            f"*Di 'Confirmar' o presiona el botón para ejecutar la transacción.*"
        )
        return {
            "tipo": "propuesta",
            "requiere_confirmacion": True,
            "propuesta": propuesta,
            "mensaje_pantalla": pantalla,
            "mensaje_verbal": normalizar_para_tts(verbal),
        }

    # 9. Préstamo
    if intencion.tipo == IntencionTipo.PRESTAMO:
        producto = intencion.producto
        cantidad = intencion.cantidad
        destinatario = intencion.destinatario or ""

        if not producto:
            msg = "Por favor indica qué producto deseas prestar."
            return {"tipo": "aclaracion", "mensaje_pantalla": msg, "mensaje_verbal": msg}

        if not cantidad or cantidad <= 0:
            msg = f"¿Cuántas unidades de {producto.nombre} se van a prestar?"
            return {"tipo": "aclaracion", "mensaje_pantalla": msg, "mensaje_verbal": msg}

        # Validación y normalización robusta del destinatario
        from .servicios_personas import HakuPersonasService
        
        # Determinar si el origen fue voz
        origen_voz = bool(sesion and sesion.get("haku_ultimo_origen") == "voz")

        nombre_canonico, confianza, candidatos_similares, requiere_escritura = HakuPersonasService.resolver_destinatario(
            destinatario, origen_voz=origen_voz
        )

        if not destinatario or requiere_escritura or not nombre_canonico:
            if not destinatario:
                msg = (
                    "¿A quién se lo vas a prestar? Escribe el nombre de la persona para registrarlo correctamente."
                )
            elif candidatos_similares and len(candidatos_similares) > 1:
                cand_str = ", ".join(candidatos_similares[:3])
                msg = (
                    f"Encontré varias personas con nombres similares ({cand_str}). "
                    "Por favor escribe el nombre completo de la persona para evitar registrar a otra persona por error."
                )
            else:
                msg = (
                    "No pude identificar con seguridad el nombre. Escríbelo para evitar registrar a otra persona por error."
                )

            if estado_operativo:
                estado_operativo.operacion_pendiente = {
                    "tipo": "PRESTAMO",
                    "esperando": "destinatario",
                    "producto_id": producto.id,
                    "cantidad": cantidad,
                }
                HakuOperationalContext.guardar(estado_operativo)
            return {
                "tipo": "aclaracion",
                "esperando": "destinatario",
                "requiere_escritura": True,
                "candidatos": candidatos_similares,
                "mensaje_pantalla": msg,
                "mensaje_verbal": normalizar_para_tts(msg),
            }

        # Asignar el nombre canónico oficial verificado
        destinatario = nombre_canonico

        almacen, error_resp = _resolver_almacen_para_operacion(usuario, intencion.almacen, producto, operacion="préstamo")
        if error_resp:
            return error_resp

        existencia = Existencia.objects.filter(producto=producto, almacen=almacen).first()
        disponible = existencia.stock_disponible if existencia else 0
        if disponible < cantidad:
            msg = f"Stock insuficiente para préstamo: {producto.nombre} solo tiene {disponible} disponibles."
            return {"tipo": "error", "mensaje_pantalla": msg, "mensaje_verbal": msg}

        if sesion is not None:
            sesion["haku_ultimo_producto_id"] = str(producto.id)
            sesion["haku_ultimo_acceso"] = time.time()

        propuesta = {
            "id": str(uuid.uuid4()),
            "tipo_operacion": Movimiento.Tipo.PRESTAMO,
            "producto_id": producto.id,
            "producto_nombre": producto.nombre,
            "producto_codigo": producto.codigo_interno,
            "cantidad": cantidad,
            "almacen_origen_id": almacen.id,
            "almacen_origen_nombre": almacen.nombre,
            "destinatario": destinatario,
            "motivo": f"Préstamo registrado por Haku a {destinatario}",
            "creado_en": time.time(),
            "expira_en": time.time() + HAKU_PROPUESTA_TIMEOUT_SEGUNDOS,
        }

        if estado_operativo:
            estado_operativo.propuesta_activa = propuesta
            estado_operativo.operacion_pendiente = None
            estado_operativo.registrar_accion(
                "PROPUESTA_PRESTAMO",
                producto=producto.nombre,
                producto_id=producto.id,
                codigo=producto.codigo_interno,
                cantidad=cantidad,
            )
            HakuOperationalContext.guardar(estado_operativo)
        if sesion is not None:
            sesion["haku_propuesta_activa"] = propuesta
        HakuDiagnostico.registrar_propuesta("generada")

        unidad_txt = "1 unidad" if cantidad == 1 else f"{cantidad} unidades"
        verbal = (
            f"Vas a prestar {unidad_txt} de {producto.nombre} a {destinatario}. "
            f"Quedará registrada como préstamo pendiente. ¿Deseas confirmar?"
            if cantidad == 1 else
            f"Vas a prestar {cantidad} unidades de {producto.nombre} a {destinatario}. "
            f"Quedarán registradas como préstamo pendiente. ¿Deseas confirmar?"
        )
        pantalla = (
            f"### Propuesta pendiente de confirmación\n"
            f"- **Operación:** Préstamo de stock\n"
            f"- **Producto:** {producto.nombre} (`{producto.codigo_interno}`)\n"
            f"- **Cantidad:** {cantidad} unidades\n"
            f"- **Almacén:** {almacen.nombre}\n"
            f"- **Responsable/Destinatario:** {destinatario}\n\n"
            f"*Di 'Confirmar' o presiona el botón para ejecutar la transacción.*"
        )
        return {
            "tipo": "propuesta",
            "requiere_confirmacion": True,
            "propuesta": propuesta,
            "mensaje_pantalla": pantalla,
            "mensaje_verbal": normalizar_para_tts(verbal),
        }

    # 10. Creación de producto con formulario interactivo
    # 9-bis. Creación de usuario con formulario interactivo
    if intencion.tipo == IntencionTipo.CREAR_USUARIO:
        if usuario.es_usuario_normal:
            raise PermissionDenied("No tienes permisos para administrar usuarios. Contacta al Administrador.")

        almacenes_qs = Almacen.objects.filter(activo=True, eliminado=False).values("id", "nombre")
        # Filtrar roles permitidos: Administrador solo puede crear USUARIO; Superusuario puede crear ADMINISTRADOR, USUARIO y SUPERUSUARIO
        roles_disponibles = [{"codigo": "USUARIO", "valor": "USUARIO", "nombre": "Usuario (Operador de almacén)"}]
        if usuario.es_superusuario_inventario:
            roles_disponibles.insert(0, {"codigo": "ADMINISTRADOR", "valor": "ADMINISTRADOR", "nombre": "Administrador"})
            roles_disponibles.insert(0, {"codigo": "SUPERUSUARIO", "valor": "SUPERUSUARIO", "nombre": "Superusuario (Control total)"})

        return {
            "tipo": "formulario_crear_usuario",
            "mensaje_pantalla": "### Registro de Nuevo Usuario\nCompleta los datos requeridos en el formulario a continuación.",
            "mensaje_verbal": normalizar_para_tts("Abriendo formulario para registrar un nuevo usuario."),
            "datos": {
                "roles": roles_disponibles,
                "almacenes": list(almacenes_qs),
                "es_superusuario": usuario.es_superusuario_inventario,
                "usuario_actor_rol": usuario.rol,
            },
        }

    # 9-ter. Edición de usuario con formulario interactivo
    if intencion.tipo == IntencionTipo.EDITAR_USUARIO:
        if usuario.es_usuario_normal:
            raise PermissionDenied("No tienes permisos para modificar usuarios. Contacta al Administrador.")

        almacenes_qs = Almacen.objects.filter(activo=True, eliminado=False).values("id", "nombre")
        roles_disponibles = [{"codigo": "USUARIO", "valor": "USUARIO", "nombre": "Usuario (Operador de almacén)"}]
        if usuario.es_superusuario_inventario:
            roles_disponibles.insert(0, {"codigo": "ADMINISTRADOR", "valor": "ADMINISTRADOR", "nombre": "Administrador"})
            roles_disponibles.insert(0, {"codigo": "SUPERUSUARIO", "valor": "SUPERUSUARIO", "nombre": "Superusuario (Control total)"})

        # Lista de usuarios que el solicitante puede editar
        from cuentas.models import UsuarioAudacIA
        qs_usuarios = UsuarioAudacIA.objects.filter(eliminado_logicamente=False).order_by("username")
        if not usuario.es_superusuario_inventario:
            # Administrador no puede editar Superusuarios ni elevar privilegios
            qs_usuarios = qs_usuarios.exclude(rol=UsuarioAudacIA.Rol.SUPERUSUARIO)

        usuarios_lista = [
            {
                "id": u.id,
                "username": u.username,
                "nombre_completo": u.nombre_completo,
                "rol": u.rol,
                "almacen_asignado_id": u.almacen_asignado_id,
                "almacenes_asignados_ids": list(u.almacenes_asignados.values_list("id", flat=True)),
                "acceso_todos_almacenes": u.acceso_todos_almacenes,
                "is_active": u.is_active,
            }
            for u in qs_usuarios
        ]

        return {
            "tipo": "formulario_editar_usuario",
            "mensaje_pantalla": "### Gestión de Usuario\nSelecciona y modifica el usuario en el formulario a continuación.",
            "mensaje_verbal": normalizar_para_tts("Abriendo formulario para editar usuarios."),
            "datos": {
                "usuarios": usuarios_lista,
                "roles": roles_disponibles,
                "almacenes": list(almacenes_qs),
                "es_superusuario": usuario.es_superusuario_inventario,
            },
        }

    if intencion.tipo == IntencionTipo.CREAR_PRODUCTO:
        if usuario.es_usuario_normal:
            raise PermissionDenied("No tienes permisos para registrar nuevos productos. Contacta al Administrador.")

        categorias_qs = Categoria.objects.filter(activo=True, eliminado=False).values("id", "nombre")
        almacenes_qs = Almacen.objects.filter(activo=True, eliminado=False).values("id", "nombre")

        msg = "Abriendo el formulario de registro de nuevo producto en pantalla. Por favor completa los datos obligatorios para proceder."
        return {
            "tipo": "formulario_crear_producto",
            "mensaje_pantalla": "### Registro de Nuevo Producto\nPor favor completa los datos requeridos en el formulario a continuación.",
            "mensaje_verbal": normalizar_para_tts("Abriendo formulario para registrar un nuevo producto."),
            "datos": {
                "categorias": list(categorias_qs),
                "almacenes": list(almacenes_qs),
            },
        }

    # 11. Edición de producto con formulario interactivo
    if intencion.tipo == IntencionTipo.EDITAR_PRODUCTO:
        if usuario.es_usuario_normal:
            raise PermissionDenied("No tienes permisos para modificar productos. Contacta al Administrador.")

        producto = intencion.producto
        if not producto:
            msg = "¿Cuál producto deseas editar o modificar?"
            return {"tipo": "aclaracion", "mensaje_pantalla": msg, "mensaje_verbal": normalizar_para_tts(msg)}

        categorias_qs = Categoria.objects.filter(activo=True, eliminado=False).values("id", "nombre")
        msg = f"Abriendo el formulario de edición para {producto.nombre} ({producto.codigo_interno})."
        return {
            "tipo": "formulario_editar_producto",
            "mensaje_pantalla": f"### Edición de Producto: {producto.nombre} (`{producto.codigo_interno}`)\nModifica los campos permitidos y confirma los cambios.",
            "mensaje_verbal": normalizar_para_tts(f"Abriendo formulario para editar {producto.nombre}."),
            "datos": {
                "producto": {
                    "id": producto.id,
                    "codigo_interno": producto.codigo_interno,
                    "nombre": producto.nombre,
                    "codigo_barras": producto.codigo_barras or "",
                    "categoria_id": producto.categoria_id,
                    "categoria_nombre": producto.categoria.nombre,
                    "stock_minimo": producto.stock_minimo,
                    "nombre_empaque": producto.nombre_empaque or "",
                    "unidades_por_empaque": producto.unidades_por_empaque or "",
                    "descripcion": producto.descripcion or "",
                    "activo": producto.activo,
                },
                "categorias": list(categorias_qs),
            },
        }

    # 12. Inhabilitación de producto
    # ─────────────────────────────────────────────────────────────────────────
    # C-6. TRASLADO ENTRE ALMACENES (TRASLADAR_PRODUCTO)
    # ─────────────────────────────────────────────────────────────────────────
    if intencion.tipo == IntencionTipo.TRASLADAR_PRODUCTO:
        producto = intencion.producto
        if not producto:
            msg = "Indica el nombre o código del producto que deseas trasladar entre almacenes."
            return {"tipo": "aclaracion", "mensaje_pantalla": msg, "mensaje_verbal": normalizar_para_tts(msg)}

        almacen_origen, acceso_msg = _validar_acceso_almacen(usuario, intencion.almacen)
        if acceso_msg:
            if isinstance(acceso_msg, dict):
                return acceso_msg
            return {"tipo": "error", "mensaje_pantalla": acceso_msg, "mensaje_verbal": normalizar_para_tts(acceso_msg)}

        # Determinar almacén destino
        almacen_destino_id = (intencion.datos_extra or {}).get("almacen_destino_id")
        almacen_destino_nombre = (intencion.datos_extra or {}).get("almacen_destino_nombre")

        if not almacen_origen:
            alms_acc = list(usuario.obtener_almacenes_accesibles())
            if len(alms_acc) == 1:
                almacen_origen = alms_acc[0]
            elif estado_operativo and getattr(estado_operativo, "almacen_id", None):
                almacen_origen = Almacen.objects.filter(id=estado_operativo.almacen_id, activo=True).first()
            if not almacen_origen:
                # Seleccionar almacén donde el producto tenga stock disponible diferente al destino
                ex_qs = Existencia.objects.filter(producto=producto, stock_disponible__gt=0)
                if usuario.es_usuario_normal and not usuario.acceso_todos_almacenes:
                    ex_qs = ex_qs.filter(almacen__in=alms_acc)
                if almacen_destino_id:
                    ex_qs = ex_qs.exclude(almacen_id=almacen_destino_id)
                ex_con_stock = ex_qs.first()
                if ex_con_stock:
                    almacen_origen = ex_con_stock.almacen
                else:
                    alm_cat = getattr(producto.categoria, "almacen_principal", None)
                    if alm_cat and usuario.tiene_acceso_almacen(alm_cat):
                        almacen_origen = alm_cat

        if not almacen_origen:
            msg = f"¿Desde cuál almacén deseas trasladar el producto **{producto.nombre}**?"
            return {"tipo": "aclaracion", "mensaje_pantalla": msg, "mensaje_verbal": normalizar_para_tts(msg)}

        if not almacen_destino_id:
            msg = (
                f"Producto: **{producto.nombre}** (`{producto.codigo_interno}`).\n"
                f"Almacén origen: **{almacen_origen.nombre}**.\n\n"
                f"¿A cuál almacén o bodega deseas trasladar este producto?"
            )
            return {"tipo": "aclaracion", "mensaje_pantalla": msg, "mensaje_verbal": normalizar_para_tts(
                f"El producto {producto.nombre} está en el almacén {almacen_origen.nombre}. ¿A cuál almacén deseas trasladarlo?"
            )}

        # Validar que destino existe y es diferente al origen
        try:
            almacen_destino = Almacen.objects.get(id=almacen_destino_id, activo=True)
        except Almacen.DoesNotExist:
            msg = f"El almacén destino '{almacen_destino_nombre or almacen_destino_id}' no existe o fue desactivado."
            return {"tipo": "error", "mensaje_pantalla": msg, "mensaje_verbal": normalizar_para_tts(msg)}

        if not usuario.tiene_acceso_almacen(almacen_destino):
            msg = f"No tienes autorización para trasladar mercancía hacia el almacén '{almacen_destino.nombre}'."
            return {"tipo": "error", "mensaje_pantalla": msg, "mensaje_verbal": normalizar_para_tts(msg)}

        if almacen_destino.id == almacen_origen.id:
            msg = "El almacén de origen y destino son el mismo. Selecciona un almacén diferente como destino."
            return {"tipo": "aclaracion", "mensaje_pantalla": msg, "mensaje_verbal": normalizar_para_tts(msg)}

        cantidad = intencion.cantidad or 1

        # Verificar stock disponible antes de proponer
        existencia = Existencia.objects.filter(producto=producto, almacen=almacen_origen).first()
        stock_actual = existencia.stock_disponible if existencia else 0
        if stock_actual < cantidad:
            msg = (
                f"Stock insuficiente para el traslado. "
                f"El producto **{producto.nombre}** tiene **{stock_actual}** unidades disponibles "
                f"en **{almacen_origen.nombre}**, pero se solicitaron **{cantidad}**."
            )
            return {"tipo": "error", "mensaje_pantalla": msg, "mensaje_verbal": normalizar_para_tts(
                f"Stock insuficiente. Solo hay {stock_actual} unidades disponibles de {producto.nombre} en {almacen_origen.nombre}."
            )}

        propuesta = {
            "id": str(uuid.uuid4()),
            "tipo_operacion": Movimiento.Tipo.TRASLADO_ENTRE,
            "producto_id": producto.id,
            "producto_nombre": producto.nombre,
            "producto_codigo": producto.codigo_interno,
            "almacen_origen_id": almacen_origen.id,
            "almacen_origen_nombre": almacen_origen.nombre,
            "almacen_destino_id": almacen_destino.id,
            "almacen_destino_nombre": almacen_destino.nombre,
            "cantidad": cantidad,
            "motivo": f"Traslado vía Haku de {almacen_origen.nombre} a {almacen_destino.nombre}",
            "creado_en": time.time(),
            "expira_en": time.time() + HAKU_PROPUESTA_TIMEOUT_SEGUNDOS,
        }

        if estado_operativo:
            estado_operativo.propuesta_activa = propuesta
            estado_operativo.registrar_accion(
                "PROPUESTA_TRASLADO",
                producto=producto.nombre,
                producto_id=producto.id,
                codigo=producto.codigo_interno,
                cantidad=cantidad,
                detalles={
                    "almacen_origen": almacen_origen.nombre,
                    "almacen_destino": almacen_destino.nombre,
                },
            )
            HakuOperationalContext.guardar(estado_operativo)
        HakuDiagnostico.registrar_propuesta("generada")

        pantalla = (
            f"### Propuesta de traslado pendiente de confirmación\n"
            f"- **Operación:** Traslado entre almacenes\n"
            f"- **Producto:** {producto.nombre} (`{producto.codigo_interno}`)\n"
            f"- **Cantidad:** {cantidad} unidades\n"
            f"- **Origen:** {almacen_origen.nombre}\n"
            f"- **Destino:** {almacen_destino.nombre}\n"
            f"- **Stock actual en origen:** {stock_actual} unidades\n\n"
            f"¿Confirmas el traslado?"
        )
        verbal = (
            f"¿Confirmas trasladar {cantidad} unidades de {producto.nombre} "
            f"desde {almacen_origen.nombre} hacia {almacen_destino.nombre}?"
        )
        return {
            "tipo": "propuesta",
            "requiere_confirmacion": True,
            "propuesta": propuesta,
            "mensaje_pantalla": pantalla,
            "mensaje_verbal": normalizar_para_tts(verbal),
        }

    if intencion.tipo == IntencionTipo.INHABILITAR_PRODUCTO:
        if usuario.es_usuario_normal:
            raise PermissionDenied("Solo los Administradores o Superusuarios pueden inhabilitar o archivar productos.")

        producto = intencion.producto
        if not producto:
            msg = "¿Cuál producto deseas inhabilitar o archivar del inventario?"
            return {"tipo": "aclaracion", "mensaje_pantalla": msg, "mensaje_verbal": normalizar_para_tts(msg)}

        # Comprobar estado real: si ya está inactivo o archivado, informar
        if not producto.activo or producto.archivado:
            msg = f"El producto {producto.nombre} ({producto.codigo_interno}) ya se encuentra inhabilitado en el sistema."
            return {
                "tipo": "consulta",
                "mensaje_pantalla": f"⚠️ {msg}",
                "mensaje_verbal": normalizar_para_tts(msg),
            }

        propuesta = {
            "id": str(uuid.uuid4()),
            "tipo_operacion": "INHABILITAR_PRODUCTO",
            "producto_id": producto.id,
            "producto_nombre": producto.nombre,
            "producto_codigo": producto.codigo_interno,
            "creado_en": time.time(),
            "expira_en": time.time() + HAKU_PROPUESTA_TIMEOUT_SEGUNDOS,
        }

        if estado_operativo:
            estado_operativo.propuesta_activa = propuesta
            estado_operativo.registrar_accion(
                "PROPUESTA_INHABILITAR",
                producto=producto.nombre,
                producto_id=producto.id,
                codigo=producto.codigo_interno,
            )
            HakuOperationalContext.guardar(estado_operativo)
        HakuDiagnostico.registrar_propuesta("generada")

        verbal = f"¿Deseas confirmar la inhabilitación del producto {producto.nombre} con código {producto.codigo_interno}?"
        pantalla = (
            f"### Propuesta pendiente de confirmación\n"
            f"- **Operación:** Inhabilitar / archivar producto\n"
            f"- **Producto:** {producto.nombre} (`{producto.codigo_interno}`)\n"
            f"- **Estado actual:** {'Activo' if producto.activo else 'Inactivo'}\n\n"
            f"*Esta acción ocultará el producto de las listas operativas sin borrar su historial.*"
        )
        return {
            "tipo": "propuesta",
            "requiere_confirmacion": True,
            "propuesta": propuesta,
            "mensaje_pantalla": pantalla,
            "mensaje_verbal": normalizar_para_tts(verbal),
        }

    if intencion.tipo == IntencionTipo.HABILITAR_PRODUCTO:
        if usuario.es_usuario_normal:
            raise PermissionDenied("Solo los Administradores o Superusuarios pueden habilitar o reactivar productos.")

        producto = intencion.producto
        if not producto:
            msg = "¿Cuál producto deseas habilitar o reactivar en el inventario?"
            return {"tipo": "aclaracion", "mensaje_pantalla": msg, "mensaje_verbal": normalizar_para_tts(msg)}

        # Comprobar estado real: si ya está activo y no archivado, informar
        if producto.activo and not producto.archivado:
            msg = f"El producto {producto.nombre} ({producto.codigo_interno}) ya se encuentra activo y habilitado en el sistema."
            return {
                "tipo": "consulta",
                "mensaje_pantalla": f"ℹ️ {msg}",
                "mensaje_verbal": normalizar_para_tts(msg),
            }

        propuesta = {
            "id": str(uuid.uuid4()),
            "tipo_operacion": "HABILITAR_PRODUCTO",
            "producto_id": producto.id,
            "producto_nombre": producto.nombre,
            "producto_codigo": producto.codigo_interno,
            "creado_en": time.time(),
            "expira_en": time.time() + HAKU_PROPUESTA_TIMEOUT_SEGUNDOS,
        }

        if estado_operativo:
            estado_operativo.propuesta_activa = propuesta
            estado_operativo.registrar_accion(
                "PROPUESTA_HABILITAR",
                producto=producto.nombre,
                producto_id=producto.id,
                codigo=producto.codigo_interno,
            )
            HakuOperationalContext.guardar(estado_operativo)
        HakuDiagnostico.registrar_propuesta("generada")

        verbal = f"¿Deseas confirmar la habilitación y reactivación del producto {producto.nombre} con código {producto.codigo_interno}?"
        pantalla = (
            f"### Propuesta pendiente de confirmación\n"
            f"- **Operación:** Habilitar / reactivar producto\n"
            f"- **Producto:** {producto.nombre} (`{producto.codigo_interno}`)\n"
            f"- **Estado actual:** {'Activo' if producto.activo else 'Inactivo / Archivado'}\n\n"
            f"*Esta acción reactivará el producto para operaciones ordinarias en el inventario.*"
        )
        return {
            "tipo": "propuesta",
            "requiere_confirmacion": True,
            "propuesta": propuesta,
            "mensaje_pantalla": pantalla,
            "mensaje_verbal": normalizar_para_tts(verbal),
        }


    # ─────────────────────────────────────────────────────────────────────────
    # D. LOCUCIÓN FUERA DE DOMINIO (NO_INTENCION_INVENTARIO / CONVERSACION_GENERAL)
    # Haku Inventario NO es un chatbot general: solo gestiona almacén e inventario.
    # ─────────────────────────────────────────────────────────────────────────
    if intencion.tipo == IntencionTipo.SOLICITUD_NO_COMPRENDIDA:
        msg = "Disculpa, no entendí la solicitud. ¿Puedes repetirla?"
        return {
            "tipo": "no_comprendido",
            "mensaje_pantalla": msg,
            "mensaje_verbal": normalizar_para_tts(msg),
            "datos": {"no_comprendido": True},
        }

    msg_fuera_dominio = (
        "Solo gestiono operaciones y consultas del inventario "
        "(stock, ubicaciones, movimientos, préstamos y métricas del almacén)."
    )
    msg_verbal = (
        "Esa consulta está fuera de mis funciones. Soy HAKU, el asistente de inventario de AudacIA. "
        "Puedo ayudarte a consultar productos, existencias o movimientos de inventario."
    )
    return {
        "tipo": "fuera_de_dominio",
        "mensaje_pantalla": msg_fuera_dominio,
        "mensaje_verbal": normalizar_para_tts(msg_verbal),
        "datos": {"fuera_de_dominio": True},
    }


# ═════════════════════════════════════════════════════════════════════════════
# 6. EJECUCIÓN ATÓMICA DE LA CONFIRMACIÓN DE UNA PROPUESTA
# ═════════════════════════════════════════════════════════════════════════════


def ejecutar_confirmacion_propuesta(
    usuario: UsuarioAudacIA,
    propuesta: dict,
    idempotency_key: str | None = None,
    ip_origen: str = "127.0.0.1",
) -> dict[str, Any]:
    """Ejecuta de forma atómica y segura una propuesta pre-confirmada por el usuario.

    Reglas obligatorias:
    - transaction.atomic() + select_for_update() (sección 9)
    - Prohibición estricta de stock negativo
    - Idempotencia garantizada
    - Auditoría inmutable con via_haku=True
    - Movimiento con via_haku=True
    """
    if not propuesta:
        raise ValidationError("No hay propuesta válida para confirmar.")

    # 1. Revalidar usuario activo y autorizado
    if not usuario or not usuario.is_active:
        raise PermissionDenied("Usuario inactivo o no autorizado.")

    # 2. Verificar expiración de la propuesta (1800 segundos / 30 minutos)
    ahora = time.time()
    if ahora > propuesta.get("expira_en", 0):
        raise ValidationError("La propuesta ha caducado por superar el límite de 30 minutos. Por favor genera una nueva solicitud.")

    tipo_op = propuesta.get("tipo_operacion")
    cantidad = int(propuesta.get("cantidad", 0)) if propuesta.get("cantidad") is not None else 0
    if tipo_op not in ("INHABILITAR_PRODUCTO", "HABILITAR_PRODUCTO") and cantidad <= 0:
        raise ValidationError("La cantidad debe ser mayor a cero.")

    # Generar o validar idempotency_key
    if not idempotency_key:
        idempotency_key = f"haku-{propuesta['id']}-{uuid.uuid4().hex[:8]}"

    # Verificar si ya fue procesada con esta clave de idempotencia
    if tipo_op in ("INHABILITAR_PRODUCTO", "HABILITAR_PRODUCTO"):
        aud_existente = Auditoria.objects.filter(
            accion=tipo_op,
            tipo_entidad="Producto",
            id_entidad=str(propuesta["producto_id"]),
            motivo__contains=idempotency_key,
        ).first()
        if aud_existente:
            op_label = "inhabilitación" if tipo_op == "INHABILITAR_PRODUCTO" else "habilitación"
            return {
                "exito": True,
                "idempotente": True,
                "movimiento_id": None,
                "mensaje_pantalla": f"La {op_label} de este producto ya fue confirmada y ejecutada previamente.",
                "mensaje_verbal": "Esta operación ya había sido confirmada previamente.",
            }
    else:
        existente = Movimiento.objects.filter(clave_idempotencia=idempotency_key).first()
        if existente:
            return {
                "exito": True,
                "idempotente": True,
                "movimiento_id": str(existente.id),
                "mensaje_pantalla": f"Operación previamente confirmada (Movimiento #{existente.id}).",
                "mensaje_verbal": "Esta operación ya había sido confirmada previamente.",
            }

    try:
        with transaction.atomic():
            # Si es habilitación o inhabilitación de producto, no requiere almacén de origen ni existencias
            if tipo_op == "HABILITAR_PRODUCTO":
                if usuario.es_usuario_normal:
                    raise PermissionDenied("Solo los Administradores o Superusuarios pueden habilitar o reactivar productos.")
                try:
                    producto = Producto.objects.select_for_update().get(id=propuesta["producto_id"])
                except Producto.DoesNotExist:
                    raise ValidationError("El producto asociado a la propuesta no existe.")

                val_ant = {"activo": producto.activo, "archivado": producto.archivado}
                producto.activo = True
                producto.archivado = False
                producto.save(update_fields=["activo", "archivado"])

                Auditoria.objects.create(
                    usuario=usuario,
                    accion="HABILITAR_PRODUCTO",
                    tipo_entidad="Producto",
                    id_entidad=str(producto.id),
                    valores_anteriores=val_ant,
                    valores_nuevos={"activo": True, "archivado": False},
                    motivo=f"Producto habilitado/reactivado vía Haku [idempotency:{idempotency_key}] desde IP {ip_origen}",
                    resultado="OK",
                    via_haku=True,
                )
                from .cache import HakuCacheManager
                HakuCacheManager.invalidar_catalogo()
                HakuCacheManager.invalidar_stock(producto.id)
                HakuCacheManager.invalidar_ubicaciones(producto.id)
                msg_ok = f"El producto {producto.nombre} ({producto.codigo_interno}) ha sido habilitado y reactivado correctamente."
                return {
                    "exito": True,
                    "movimiento_id": None,
                    "mensaje_pantalla": msg_ok,
                    "mensaje_verbal": normalizar_para_tts(f"El producto {producto.nombre} fue reactivado correctamente."),
                }

            if tipo_op == "INHABILITAR_PRODUCTO":
                if usuario.es_usuario_normal:
                    raise PermissionDenied("Solo los Administradores o Superusuarios pueden inhabilitar o archivar productos.")
                try:
                    producto = Producto.objects.select_for_update().get(id=propuesta["producto_id"])
                except Producto.DoesNotExist:
                    raise ValidationError("El producto asociado a la propuesta no existe.")

                val_ant = {"activo": producto.activo, "archivado": producto.archivado}
                producto.activo = False
                producto.archivado = True
                producto.save(update_fields=["activo", "archivado"])

                Auditoria.objects.create(
                    usuario=usuario,
                    accion="INHABILITAR_PRODUCTO",
                    tipo_entidad="Producto",
                    id_entidad=str(producto.id),
                    valores_anteriores=val_ant,
                    valores_nuevos={"activo": False, "archivado": True},
                    motivo=f"Producto inhabilitado/archivado vía Haku [idempotency:{idempotency_key}] desde IP {ip_origen}",
                    resultado="OK",
                    via_haku=True,
                )
                from .cache import HakuCacheManager
                HakuCacheManager.invalidar_catalogo()
                HakuCacheManager.invalidar_stock(producto.id)
                HakuCacheManager.invalidar_ubicaciones(producto.id)
                msg_ok = f"El producto {producto.nombre} ({producto.codigo_interno}) ha sido inhabilitado y archivado correctamente."
                return {
                    "exito": True,
                    "movimiento_id": None,
                    "mensaje_pantalla": msg_ok,
                    "mensaje_verbal": normalizar_para_tts(f"El producto {producto.nombre} fue inhabilitado correctamente."),
                }

            # Bloquear producto con select_for_update() y revalidar que sigue activo para operaciones de stock
            try:
                producto = (
                    Producto.objects.select_for_update()
                    .get(id=propuesta["producto_id"], activo=True, archivado=False)
                )
            except Producto.DoesNotExist:
                raise ValidationError("El producto asociado a la propuesta ya no existe o fue dado de baja.")

            # Bloquear almacén con select_for_update() y revalidar acceso
            try:
                almacen = Almacen.objects.select_for_update().get(id=propuesta["almacen_origen_id"], activo=True)
            except Almacen.DoesNotExist:
                raise ValidationError("El almacén asociado a la propuesta ya no existe o fue desactivado.")

            if not usuario.tiene_acceso_almacen(almacen):
                raise PermissionDenied("No tienes permisos para operar en este almacén.")

            existencia, _ = Existencia.objects.select_for_update().get_or_create(
                producto=producto,
                almacen=almacen,
                defaults={"stock_disponible": 0, "stock_en_prestamo": 0},
            )

            stock_ant = existencia.stock_disponible
            stock_post = stock_ant
            movimiento = None

            if tipo_op == Movimiento.Tipo.ENTRADA:
                stock_post = stock_ant + cantidad
                existencia.stock_disponible = stock_post
                existencia.save(update_fields=["stock_disponible"])

                movimiento = Movimiento.objects.create(
                    clave_idempotencia=idempotency_key,
                    tipo=Movimiento.Tipo.ENTRADA,
                    producto=producto,
                    cantidad=cantidad,
                    almacen_origen=almacen,
                    usuario=usuario,
                    motivo=propuesta.get("motivo", "Entrada vía Haku"),
                    stock_disponible_anterior=stock_ant,
                    stock_disponible_posterior=stock_post,
                )

            elif tipo_op == Movimiento.Tipo.SALIDA:
                if stock_ant < cantidad:
                    raise ValidationError(
                        f"Stock insuficiente: se intentó retirar {cantidad} unidades pero solo hay {stock_ant} disponibles."
                    )
                stock_post = stock_ant - cantidad
                existencia.stock_disponible = stock_post
                existencia.save(update_fields=["stock_disponible"])

                movimiento = Movimiento.objects.create(
                    clave_idempotencia=idempotency_key,
                    tipo=Movimiento.Tipo.SALIDA,
                    producto=producto,
                    cantidad=cantidad,
                    almacen_origen=almacen,
                    usuario=usuario,
                    destinatario=propuesta.get("destinatario", ""),
                    motivo=propuesta.get("motivo", "Salida vía Haku"),
                    stock_disponible_anterior=stock_ant,
                    stock_disponible_posterior=stock_post,
                )

            elif tipo_op == Movimiento.Tipo.PRESTAMO:
                if stock_ant < cantidad:
                    raise ValidationError(
                        f"Stock insuficiente para préstamo: {stock_ant} disponibles, se solicitaron {cantidad}."
                    )
                stock_post = stock_ant - cantidad
                existencia.stock_disponible = stock_post
                existencia.stock_en_prestamo += cantidad
                existencia.save(update_fields=["stock_disponible", "stock_en_prestamo"])

                movimiento = Movimiento.objects.create(
                    clave_idempotencia=idempotency_key,
                    tipo=Movimiento.Tipo.PRESTAMO,
                    producto=producto,
                    cantidad=cantidad,
                    almacen_origen=almacen,
                    usuario=usuario,
                    destinatario=propuesta.get("destinatario", ""),
                    motivo=propuesta.get("motivo", "Préstamo vía Haku"),
                    stock_disponible_anterior=stock_ant,
                    stock_disponible_posterior=stock_post,
                )

                # Crear registro de préstamo
                Prestamo.objects.create(
                    movimiento=movimiento,
                    producto=producto,
                    almacen=almacen,
                    destinatario=propuesta.get("destinatario", ""),
                    cantidad_original=cantidad,
                    cantidad_pendiente_devolucion=cantidad,
                    estado=Prestamo.Estado.ABIERTO,
                    usuario=usuario,
                )

            elif tipo_op == Movimiento.Tipo.DEVOLUCION:
                prestamo = Prestamo.objects.select_for_update().get(id=propuesta["prestamo_id"])
                if cantidad > prestamo.cantidad_pendiente_devolucion:
                    raise ValidationError("La cantidad a devolver excede el saldo pendiente del préstamo.")

                stock_post = stock_ant + cantidad
                existencia.stock_disponible = stock_post
                existencia.stock_en_prestamo = max(0, existencia.stock_en_prestamo - cantidad)
                existencia.save(update_fields=["stock_disponible", "stock_en_prestamo"])

                prestamo.cantidad_pendiente_devolucion -= cantidad
                if prestamo.cantidad_pendiente_devolucion == 0:
                    prestamo.estado = Prestamo.Estado.CERRADO
                else:
                    prestamo.estado = Prestamo.Estado.DEVUELTO_PARCIAL
                prestamo.save(update_fields=["cantidad_pendiente_devolucion", "estado"])

                movimiento = Movimiento.objects.create(
                    clave_idempotencia=idempotency_key,
                    tipo=Movimiento.Tipo.DEVOLUCION,
                    producto=producto,
                    cantidad=cantidad,
                    almacen_origen=almacen,
                    usuario=usuario,
                    destinatario=prestamo.destinatario,
                    motivo=propuesta.get("motivo", "Devolución vía Haku"),
                    stock_disponible_anterior=stock_ant,
                    stock_disponible_posterior=stock_post,
                )

                DevolucionPrestamo.objects.create(
                    prestamo=prestamo,
                    cantidad=cantidad,
                    tipo=DevolucionPrestamo.Tipo.DEVOLUCION,
                    motivo=propuesta.get("motivo", "Devolución vía Haku"),
                    usuario=usuario,
                    movimiento=movimiento,
                )

            elif tipo_op == Movimiento.Tipo.TRASLADO_ENTRE:
                # Traslado entre almacenes: decrementa origen, incrementa destino (atómico)
                if stock_ant < cantidad:
                    raise ValidationError(
                        f"Stock insuficiente para el traslado: {stock_ant} disponibles en origen, se solicitaron {cantidad}."
                    )

                try:
                    almacen_destino = Almacen.objects.select_for_update().get(
                        id=propuesta["almacen_destino_id"], activo=True
                    )
                except Almacen.DoesNotExist:
                    raise ValidationError("El almacén destino ya no existe o fue desactivado.")

                if not usuario.tiene_acceso_almacen(almacen_destino):
                    raise PermissionDenied("No tienes permisos para operar en el almacén de destino.")

                if almacen_destino.id == almacen.id:
                    raise ValidationError("El almacén de origen y destino son el mismo.")

                # Decrementar existencia en origen
                stock_post = stock_ant - cantidad
                existencia.stock_disponible = stock_post
                existencia.save(update_fields=["stock_disponible"])

                # Incrementar existencia en destino
                existencia_destino, _ = Existencia.objects.select_for_update().get_or_create(
                    producto=producto,
                    almacen=almacen_destino,
                    defaults={"stock_disponible": 0, "stock_en_prestamo": 0},
                )
                stock_destino_ant = existencia_destino.stock_disponible
                existencia_destino.stock_disponible += cantidad
                existencia_destino.save(update_fields=["stock_disponible"])

                movimiento = Movimiento.objects.create(
                    clave_idempotencia=idempotency_key,
                    tipo=Movimiento.Tipo.TRASLADO_ENTRE,
                    producto=producto,
                    cantidad=cantidad,
                    almacen_origen=almacen,
                    almacen_destino=almacen_destino,
                    usuario=usuario,
                    motivo=propuesta.get("motivo", "Traslado entre almacenes vía Haku"),
                    stock_disponible_anterior=stock_ant,
                    stock_disponible_posterior=stock_post,
                )

                # Auditoría del traslado con información de ambos almacenes
                Auditoria.objects.create(
                    usuario=usuario,
                    accion="TRASLADO_ENTRE",
                    tipo_entidad="Producto",
                    id_entidad=str(producto.id),
                    valores_anteriores={
                        "stock_origen": stock_ant,
                        "stock_destino": stock_destino_ant,
                        "almacen_origen_id": almacen.id,
                        "almacen_destino_id": almacen_destino.id,
                    },
                    valores_nuevos={
                        "stock_origen": stock_post,
                        "stock_destino": existencia_destino.stock_disponible,
                        "almacen_origen_id": almacen.id,
                        "almacen_destino_id": almacen_destino.id,
                    },
                    motivo=f"Traslado confirmado vía Haku desde IP {ip_origen}: {propuesta.get('motivo', '')}",
                    resultado="OK",
                    via_haku=True,
                )

                # Invalidar caché de stock para ambos almacenes
                from .cache import HakuCacheManager
                from .diagnostico import HakuDiagnostico
                HakuCacheManager.invalidar_stock(producto.id, almacen.id)
                HakuCacheManager.invalidar_stock(producto.id, almacen_destino.id)
                HakuDiagnostico.registrar_propuesta("confirmada")
                HakuDiagnostico.registrar_traslado()

                mensaje_exito = (
                    f"Traslado confirmado. Movimiento #{movimiento.id} registrado. "
                    f"{cantidad} unidades de {producto.nombre} trasladadas de {almacen.nombre} a {almacen_destino.nombre}. "
                    f"Stock en origen: {stock_post}. Stock en destino: {existencia_destino.stock_disponible}."
                )
                return {
                    "exito": True,
                    "movimiento_id": movimiento.id,
                    "stock_nuevo": stock_post,
                    "mensaje_pantalla": mensaje_exito,
                    "mensaje_verbal": normalizar_para_tts(
                        f"Traslado confirmado. {cantidad} unidades trasladadas. "
                        f"Stock en origen: {stock_post}. Stock en destino: {existencia_destino.stock_disponible}."
                    ),
                }

            else:
                raise ValidationError(f"Tipo de operación '{tipo_op}' no soportado vía Haku.")

            # Registro inmutable de auditoría
            Auditoria.objects.create(
                usuario=usuario,
                accion=tipo_op,
                tipo_entidad="Producto",
                id_entidad=str(producto.id),
                valores_anteriores={"stock_disponible": stock_ant},
                valores_nuevos={"stock_disponible": stock_post},
                motivo=f"Operación confirmada vía Haku desde IP {ip_origen}: {propuesta.get('motivo', '')}",
                resultado="OK",
                via_haku=True,
            )
    except IntegrityError:
        # Colisión por concurrencia: otra petición simultánea con la misma clave se adelantó y confirmó
        existente = Movimiento.objects.filter(clave_idempotencia=idempotency_key).first()
        if existente:
            return {
                "exito": True,
                "idempotente": True,
                "movimiento_id": str(existente.id),
                "mensaje_pantalla": f"Operación previamente confirmada (Movimiento #{existente.id}).",
                "mensaje_verbal": "Esta operación ya había sido confirmada previamente.",
            }
        raise

    # Invalidar caché de stock inmediatamente tras confirmación exitosa
    from .cache import HakuCacheManager
    from .diagnostico import HakuDiagnostico
    HakuCacheManager.invalidar_stock(producto.id, almacen.id)
    HakuDiagnostico.registrar_propuesta("confirmada")

    mensaje_exito = (
        f"Transacción confirmada con éxito. Movimiento #{movimiento.id} registrado. "
        f"Nuevo stock disponible de {producto.nombre}: {stock_post} unidades."
    )
    mensaje_verbal = normalizar_para_tts(
        f"Operación confirmada. Se registraron {cantidad} unidades. "
        f"Nuevo stock disponible: {stock_post}."
    )

    return {
        "exito": True,
        "movimiento_id": movimiento.id,
        "stock_nuevo": stock_post,
        "mensaje_pantalla": mensaje_exito,
        "mensaje_verbal": mensaje_verbal,
    }
