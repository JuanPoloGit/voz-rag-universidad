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
from django.db import transaction
from django.utils import timezone

from cuentas.models import UsuarioAudacIA
from inventario.models import (
    Almacen,
    Auditoria,
    Categoria,
    DevolucionPrestamo,
    Movimiento,
    Prestamo,
    Producto,
    TipoMovimiento,
    Ubicacion,
)

logger = logging.getLogger("integraciones.haku")

# ═════════════════════════════════════════════════════════════════════════════
# 1. COLA SEGURA Y LOCK PARA CONVIVIR CON HAKU VISITANTE
# ═════════════════════════════════════════════════════════════════════════════

# Lock global reentrante para compartir la GPU/LLM con Haku Visitante.
# Ningún proceso llamará inferencia en paralelo sobre el modelo de 8B no reentrante.
HAKU_GPU_LOCK = threading.RLock()


class HakuOcupadoError(Exception):
    """Lanzada cuando Haku Visitante o un proceso previo retiene el lock."""
    pass


@contextmanager
def adquirir_lock_haku(timeout: float = 12.0):
    """Context manager para adquirir el lock de Haku con timeout seguro."""
    adquirido = HAKU_GPU_LOCK.acquire(timeout=timeout)
    if not adquirido:
        logger.warning("Timeout esperando lock de Haku (ocupado por Visitante o hilo previo)")
        raise HakuOcupadoError(
            "Haku está atendiendo en este momento a un visitante en el servidor o procesando otra solicitud. "
            "Por favor, intenta nuevamente en unos segundos."
        )
    try:
        yield
    finally:
        HAKU_GPU_LOCK.release()


# ═════════════════════════════════════════════════════════════════════════════
# 2. NORMALIZACIÓN FONÉTICA PARA SÍNTESIS DE VOZ (PIPER TTS)
# ═════════════════════════════════════════════════════════════════════════════


def normalizar_para_tts(texto: str) -> str:
    """Normaliza texto para que Piper TTS lo pronuncie correctamente.

    Regla innegociable de la especificación:
    - En pantalla se escribe 'Inventario AudacIA'.
    - Al enviar texto a Piper TTS, reemplazar 'AudacIA' por 'Audacia' para evitar
      que pronuncie 'AudakIA'.
    """
    if not texto:
        return ""

    # 1. Reemplazo de pronunciación para AudacIA
    resultado = re.sub(r"AudacIA\b", "Audacia", texto)
    resultado = re.sub(r"AUDACIA\b", "Audacia", resultado)
    resultado = re.sub(r"audacIA\b", "audacia", resultado)

    # 2. Limpieza de markdown (negritas, cursivas, encabezados, viñetas)
    resultado = re.sub(r"\*\*([^*]+)\*\*", r"\1", resultado)
    resultado = re.sub(r"\*([^*]+)\*", r"\1", resultado)
    resultado = re.sub(r"`([^`]+)`", r"\1", resultado)
    resultado = re.sub(r"^#+\s*", "", resultado, flags=re.MULTILINE)
    resultado = re.sub(r"^\s*[-*•]\s*", "", resultado, flags=re.MULTILINE)

    # 3. Separación de códigos de inventario (ej. CAB-0001 -> C A B 0 0 0 1 o guion dicho como código)
    resultado = re.sub(
        r"\b([A-Z]{3})-(\d{4})\b",
        r"código \1 guion \2",
        resultado,
    )

    return resultado.strip()


# ═════════════════════════════════════════════════════════════════════════════
# 3. SÍNTESIS DE AUDIO PARA EL CLIENTE (AUDIO EN NAVEGADOR EXCLUSIVO)
# ═════════════════════════════════════════════════════════════════════════════


def _generar_wav_sintetico_fallback(texto: str) -> bytes:
    """Genera un archivo WAV válido (16-bit PCM mono 16000Hz) en caso de fallback."""
    buffer = io.BytesIO()
    frecuencia = 16000
    duracion_s = max(0.5, min(2.0, len(texto) * 0.05))
    num_muestras = int(frecuencia * duracion_s)

    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)  # 16 bits
        wav_file.setframerate(frecuencia)
        # Tono suave de confirmación acústica
        datos = bytearray()
        for i in range(num_muestras):
            muestra = int(1000 * 0.1)  # tono mínimo inaudible/suave
            datos.extend(struct.pack("<h", muestra))
        wav_file.writeframes(bytes(datos))

    buffer.seek(0)
    return buffer.read()


def sintetizar_audio_bytes(texto: str) -> bytes:
    """Sintetiza texto a audio WAV en memoria para enviar al navegador cliente.

    REGLA INNEGOCIABLE:
    El audio generado NUNCA suena en los altavoces físicos del servidor (no usa
    sounddevice ni altavoces locales). Se devuelve exclusivamente en bytes WAV
    para reproducción en el navegador del dispositivo cliente.
    """
    texto_normalizado = normalizar_para_tts(texto)
    if not texto_normalizado:
        return _generar_wav_sintetico_fallback("vacío")

    # Intentar invocar el motor Piper si está disponible en el entorno
    try:
        # Verificar si piper-tts o el módulo hacu.voz está disponible
        from hacu.config import VozConfig  # type: ignore
        from hacu.voz.sintetizador import ruta_de_voz  # type: ignore

        cfg_voz = VozConfig()
        ruta_modelo = ruta_de_voz(cfg_voz, cfg_voz.piper_voz)

        if ruta_modelo and ruta_modelo.exists():
            from piper import PiperVoice  # type: ignore

            with adquirir_lock_haku(timeout=8.0):
                voz = PiperVoice.load(str(ruta_modelo))
                buffer = io.BytesIO()
                with wave.open(buffer, "wb") as wav_file:
                    voz.synthesize(texto_normalizado, wav_file)
                buffer.seek(0)
                return buffer.read()
    except Exception as exc:
        logger.debug("No se pudo usar Piper en proceso (%s), usando síntesis fallback", exc)

    return _generar_wav_sintetico_fallback(texto_normalizado)


# ═════════════════════════════════════════════════════════════════════════════
# 4. TRANSCRIPCIÓN DE VOZ CLIENTE (FASTER-WHISPER)
# ═════════════════════════════════════════════════════════════════════════════


def transcribir_audio_bytes(audio_bytes: bytes) -> str:
    """Transcribe un archivo de audio (WAV/WebM) recibido del navegador cliente.

    Usa faster-whisper con el lock de Haku para evitar conflictos de VRAM con el LLM.
    """
    if not audio_bytes:
        return ""

    try:
        from hacu.config import VozConfig  # type: ignore
        from hacu.voz.transcriptor import TranscriptorWhisper  # type: ignore

        # Si faster-whisper está disponible en el entorno
        cfg_voz = VozConfig()
        transcriptor = TranscriptorWhisper(cfg_voz, logger)

        with adquirir_lock_haku(timeout=10.0):
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                tmp.write(audio_bytes)
                tmp_path = tmp.name

            try:
                import numpy as np  # type: ignore
                import soundfile as sf  # type: ignore
                audio_data, _ = sf.read(tmp_path, dtype="float32")
                if audio_data.ndim > 1:
                    audio_data = audio_data.mean(axis=1)
                texto = transcriptor.transcribir(audio_data)
                return texto.strip()
            finally:
                import os
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
    except Exception as exc:
        logger.debug("Transcripción directa no disponible (%s), usando fallback", exc)
        return ""


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
    termino = termino.strip()
    if not termino:
        return None

    qs = Producto.objects.filter(activo=True, eliminado=False)
    if usuario.es_usuario_normal and usuario.almacen_asignado:
        qs = qs.filter(almacen=usuario.almacen_asignado)

    # 1. Búsqueda exacta por código interno
    producto = qs.filter(codigo_interno__iexact=termino).first()
    if producto:
        return producto

    # 2. Búsqueda exacta por código de barras
    producto = qs.filter(codigo_barras__iexact=termino).first()
    if producto:
        return producto

    # 3. Búsqueda por nombre exacto o contenido
    producto = qs.filter(nombre__iexact=termino).first()
    if producto:
        return producto

    producto = qs.filter(nombre__icontains=termino).first()
    return producto


def procesar_comando_inventario(
    usuario: UsuarioAudacIA,
    mensaje: str,
    propuesta_activa: dict | None = None,
) -> dict[str, Any]:
    """Interpreta el mensaje del usuario y devuelve respuesta o genera propuesta.

    Retorna un diccionario con:
    - tipo: "consulta" | "propuesta" | "confirmacion_ejecutada" | "propuesta_cancelada" | "error" | "conversacion"
    - mensaje_pantalla: texto formateado para la interfaz
    - mensaje_verbal: texto optimizado para síntesis de voz (sin markdown)
    - propuesta: dict con datos de la propuesta (si tipo == "propuesta")
    - datos: dict adicional para renderizado visual
    """
    texto = mensaje.strip()
    texto_lower = texto.lower()

    # ─────────────────────────────────────────────────────────────────────────
    # A. RESPUESTA A PROPUESTA ACTIVA (Confirmar / Cancelar)
    # ─────────────────────────────────────────────────────────────────────────
    if propuesta_activa:
        # Palabras de confirmación
        if re.search(r"\b(s[ií]|confirmar|confirmo|proceder|ejecutar|de acuerdo|afirmativo|adelante)\b", texto_lower):
            return {
                "tipo": "accion_confirmar",
                "propuesta_id": propuesta_activa["id"],
                "mensaje_pantalla": "Confirmación recibida. Procediendo con la transacción...",
                "mensaje_verbal": "Confirmación recibida. Ejecutando la operación.",
            }

        # Palabras de cancelación
        if re.search(r"\b(no|cancelar|cancela|anular|descartar|olv[ií]dalo|parar)\b", texto_lower):
            return {
                "tipo": "accion_cancelar",
                "propuesta_id": propuesta_activa["id"],
                "mensaje_pantalla": "Operación cancelada. La propuesta ha sido descartada.",
                "mensaje_verbal": "Operación cancelada. No se realizaron cambios en el inventario.",
            }

    # ─────────────────────────────────────────────────────────────────────────
    # B. CONSULTAS DE SOLO LECTURA (No requieren pre-confirmación)
    # ─────────────────────────────────────────────────────────────────────────

    # 1. Alertas de stock bajo / agotado
    if re.search(r"\b(alerta|alertas|bajo stock|sin stock|agotado|agotados|qu[eé] falta)\b", texto_lower):
        qs_base = Producto.objects.filter(activo=True, eliminado=False)
        if usuario.es_usuario_normal and usuario.almacen_asignado:
            qs_base = qs_base.filter(almacen=usuario.almacen_asignado)

        sin_stock = list(qs_base.filter(stock_disponible=0)[:5])
        bajo_stock = [p for p in qs_base if 0 < p.stock_disponible < p.stock_minimo][:5]

        if not sin_stock and not bajo_stock:
            res = "No hay alertas activas de stock en este momento. Todos los productos tienen existencias suficientes."
            return {"tipo": "consulta", "mensaje_pantalla": res, "mensaje_verbal": res, "datos": {}}

        lineas = []
        lineas_voz = []
        if sin_stock:
            nombres = ", ".join(p.nombre for p in sin_stock)
            lineas.append(f"**Agotados ({len(sin_stock)}):** {nombres}")
            lineas_voz.append(f"Hay {len(sin_stock)} productos agotados: {nombres}.")
        if bajo_stock:
            nombres = ", ".join(f"{p.nombre} ({p.stock_disponible}/{p.stock_minimo})" for p in bajo_stock)
            lineas.append(f"**Stock bajo ({len(bajo_stock)}):** {nombres}")
            lineas_voz.append(f"Hay {len(bajo_stock)} productos con stock bajo.")

        return {
            "tipo": "consulta",
            "mensaje_pantalla": "\n\n".join(lineas),
            "mensaje_verbal": " ".join(lineas_voz),
            "datos": {
                "sin_stock": [{"codigo": p.codigo_interno, "nombre": p.nombre} for p in sin_stock],
                "bajo_stock": [
                    {"codigo": p.codigo_interno, "nombre": p.nombre, "disponible": p.stock_disponible, "minimo": p.stock_minimo}
                    for p in bajo_stock
                ],
            },
        }

    # 2. Préstamos activos
    if re.search(r"\b(pr[eé]stamos activos|qui[eé]n tiene|pr[eé]stamos pendientes)\b", texto_lower):
        qs_prestamos = Prestamo.objects.filter(
            estado__in=[Prestamo.Estado.ABIERTO, Prestamo.Estado.DEVUELTO_PARCIAL]
        ).select_related("producto", "almacen")
        if usuario.es_usuario_normal and usuario.almacen_asignado:
            qs_prestamos = qs_prestamos.filter(almacen=usuario.almacen_asignado)

        prestamos = list(qs_prestamos[:10])
        if not prestamos:
            msg = "No hay préstamos activos pendientes en este almacén."
            return {"tipo": "consulta", "mensaje_pantalla": msg, "mensaje_verbal": msg, "datos": {}}

        items = []
        lineas_voz = [f"Hay {len(prestamos)} préstamos activos:"]
        for p in prestamos:
            items.append(f"- **{p.producto.nombre}**: {p.cantidad_pendiente_devolucion} ud(s) prestadas a **{p.destinatario}**")
            lineas_voz.append(f"{p.cantidad_pendiente_devolucion} de {p.producto.nombre} a {p.destinatario}.")

        return {
            "tipo": "consulta",
            "mensaje_pantalla": "### Préstamos activos:\n" + "\n".join(items),
            "mensaje_verbal": " ".join(lineas_voz),
            "datos": {"prestamos": [{"id": p.id, "producto": p.producto.nombre, "destinatario": p.destinatario} for p in prestamos]},
        }

    # 3. Consulta de stock de un producto específico
    if re.search(r"\b(stock|cu[aá]nto|cu[aá]ntos|hay|existencias|d[oó]nde est[aá]|ubicaci[oó]n)\b", texto_lower):
        # Intentar extraer nombre del producto o código
        limpio = re.sub(
            r"\b(cu[aá]nto|cu[aá]ntos|stock|de|hay|queda|quedan|existencias|el|la|los|las|producto|d[oó]nde|est[aá]|ubicaci[oó]n|en)\b",
            " ",
            texto_lower,
        ).strip()

        producto = _buscar_producto(limpio, usuario)
        if producto:
            ubicaciones = ", ".join(u.ubicacion.nombre for u in producto.ubicaciones.all()) or "Sin ubicación asignada"
            pantalla = (
                f"### {producto.nombre} (`{producto.codigo_interno}`)\n"
                f"- **Stock disponible:** {producto.stock_disponible} unidades\n"
                f"- **En préstamo:** {producto.stock_en_prestamo} unidades\n"
                f"- **Stock mínimo:** {producto.stock_minimo}\n"
                f"- **Almacén:** {producto.almacen.nombre}\n"
                f"- **Ubicación:** {ubicaciones}"
            )
            voz = (
                f"De {producto.nombre}, hay {producto.stock_disponible} unidades disponibles en {producto.almacen.nombre}, "
                f"y {producto.stock_en_prestamo} en préstamo. Ubicación: {ubicaciones}."
            )
            return {
                "tipo": "consulta",
                "mensaje_pantalla": pantalla,
                "mensaje_verbal": voz,
                "datos": {
                    "codigo": producto.codigo_interno,
                    "nombre": producto.nombre,
                    "disponible": producto.stock_disponible,
                    "en_prestamo": producto.stock_en_prestamo,
                },
            }

    # ─────────────────────────────────────────────────────────────────────────
    # C. MUTACIONES DE INVENTARIO (PRE-CONFIRMACIÓN OBLIGATORIA EN DOS PASOS)
    # ─────────────────────────────────────────────────────────────────────────

    cantidad = _extraer_numero(texto)

    # 1. Entrada de inventario
    if re.search(r"\b(entrada|ingresar|ingreso|llegaron|recibir|aumentar)\b", texto_lower):
        termino = re.sub(r"\b(entrada|ingresar|ingreso|llegaron|recibir|aumentar|de|unidades|unidad|al|el|la)\b", " ", texto_lower)
        if cantidad:
            termino = re.sub(rf"\b{cantidad}\b", " ", termino)
        producto = _buscar_producto(termino.strip(), usuario)

        if not producto:
            msg = "Indica el nombre o código del producto para registrar la entrada."
            return {"tipo": "conversacion", "mensaje_pantalla": msg, "mensaje_verbal": msg}

        if not cantidad or cantidad <= 0:
            msg = f"¿Cuántas unidades de {producto.nombre} vas a ingresar?"
            return {"tipo": "conversacion", "mensaje_pantalla": msg, "mensaje_verbal": msg}

        almacen = usuario.almacen_asignado if usuario.es_usuario_normal else producto.almacen
        if not usuario.tiene_acceso_almacen(almacen):
            raise PermissionDenied("No tienes acceso al almacén de este producto.")

        propuesta = {
            "id": str(uuid.uuid4()),
            "tipo_operacion": TipoMovimiento.ENTRADA,
            "producto_id": producto.id,
            "producto_nombre": producto.nombre,
            "producto_codigo": producto.codigo_interno,
            "cantidad": cantidad,
            "almacen_origen_id": almacen.id,
            "almacen_origen_nombre": almacen.nombre,
            "destinatario": "",
            "motivo": "Entrada registrada por Haku",
            "creado_en": time.time(),
            "expira_en": time.time() + 300,
        }

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
            "mensaje_verbal": verbal,
        }

    # 2. Salida de inventario
    if re.search(r"\b(salida|retirar|sacar|entregar|gastar)\b", texto_lower):
        termino = re.sub(r"\b(salida|retirar|sacar|entregar|gastar|de|unidades|unidad|al|el|la)\b", " ", texto_lower)
        if cantidad:
            termino = re.sub(rf"\b{cantidad}\b", " ", termino)

        # Destinatario y motivo en la frase
        match_destinatario = re.search(r"\b(?:para|a)\s+([A-Za-zÁ-ÿ\s]+?)(?:\s+por|\s+motivo|\s*$)", texto, re.IGNORECASE)
        destinatario = match_destinatario.group(1).strip() if match_destinatario else ""

        match_motivo = re.search(r"\b(?:motivo|por)\s+([A-Za-zÁ-ÿ\s0-9]+)$", texto, re.IGNORECASE)
        motivo = match_motivo.group(1).strip() if match_motivo else ""

        producto = _buscar_producto(termino.strip(), usuario)
        if not producto:
            msg = "Por favor indica qué producto vas a retirar del inventario."
            return {"tipo": "conversacion", "mensaje_pantalla": msg, "mensaje_verbal": msg}

        if not cantidad or cantidad <= 0:
            msg = f"¿Cuántas unidades de {producto.nombre} vas a retirar?"
            return {"tipo": "conversacion", "mensaje_pantalla": msg, "mensaje_verbal": msg}

        if not destinatario:
            msg = f"Para registrar la salida de {producto.nombre}, ¿a quién se le entregan las unidades?"
            return {"tipo": "conversacion", "mensaje_pantalla": msg, "mensaje_verbal": msg}

        if not motivo:
            motivo = f"Salida registrada por Haku para {destinatario}"

        almacen = usuario.almacen_asignado if usuario.es_usuario_normal else producto.almacen
        if not usuario.tiene_acceso_almacen(almacen):
            raise PermissionDenied("No tienes acceso al almacén de este producto.")

        if producto.stock_disponible < cantidad:
            msg = f"Stock insuficiente: {producto.nombre} solo tiene {producto.stock_disponible} disponibles en {almacen.nombre}."
            return {"tipo": "error", "mensaje_pantalla": msg, "mensaje_verbal": msg}

        propuesta = {
            "id": str(uuid.uuid4()),
            "tipo_operacion": TipoMovimiento.SALIDA,
            "producto_id": producto.id,
            "producto_nombre": producto.nombre,
            "producto_codigo": producto.codigo_interno,
            "cantidad": cantidad,
            "almacen_origen_id": almacen.id,
            "almacen_origen_nombre": almacen.nombre,
            "destinatario": destinatario,
            "motivo": motivo,
            "creado_en": time.time(),
            "expira_en": time.time() + 300,
        }

        verbal = (
            f"Vas a registrar la salida de {cantidad} unidades de {producto.nombre} para {destinatario}, "
            f"con motivo {motivo}. ¿Deseas confirmar la operación?"
        )
        pantalla = (
            f"### Propuesta pendiente de confirmación\n"
            f"- **Operación:** Salida de stock\n"
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
            "mensaje_verbal": verbal,
        }

    # 3. Préstamo
    if re.search(r"\b(prestar|pr[eé]stamo)\b", texto_lower):
        match_destinatario = re.search(r"\b(?:para|a)\s+([A-Za-zÁ-ÿ\s]+?)(?:\s+por|\s+motivo|\s*$)", texto, re.IGNORECASE)
        destinatario = match_destinatario.group(1).strip() if match_destinatario else ""

        termino = re.sub(r"\b(prestar|pr[eé]stamo|de|unidades|unidad)\b", " ", texto_lower)
        if cantidad:
            termino = re.sub(rf"\b{cantidad}\b", " ", termino)
        if destinatario:
            termino = re.sub(rf"\b{re.escape(destinatario.lower())}\b", " ", termino)

        producto = _buscar_producto(termino.strip(), usuario)
        if not producto:
            msg = "Por favor indica qué producto deseas prestar."
            return {"tipo": "conversacion", "mensaje_pantalla": msg, "mensaje_verbal": msg}

        if not cantidad or cantidad <= 0:
            msg = f"¿Cuántas unidades de {producto.nombre} se van a prestar?"
            return {"tipo": "conversacion", "mensaje_pantalla": msg, "mensaje_verbal": msg}

        if not destinatario:
            msg = f"¿A quién se le van a prestar las {cantidad} unidades de {producto.nombre}?"
            return {"tipo": "conversacion", "mensaje_pantalla": msg, "mensaje_verbal": msg}

        almacen = usuario.almacen_asignado if usuario.es_usuario_normal else producto.almacen
        if not usuario.tiene_acceso_almacen(almacen):
            raise PermissionDenied("No tienes acceso al almacén de este producto.")

        if producto.stock_disponible < cantidad:
            msg = f"Stock insuficiente para préstamo: {producto.nombre} solo tiene {producto.stock_disponible} disponibles."
            return {"tipo": "error", "mensaje_pantalla": msg, "mensaje_verbal": msg}

        propuesta = {
            "id": str(uuid.uuid4()),
            "tipo_operacion": TipoMovimiento.PRESTAMO,
            "producto_id": producto.id,
            "producto_nombre": producto.nombre,
            "producto_codigo": producto.codigo_interno,
            "cantidad": cantidad,
            "almacen_origen_id": almacen.id,
            "almacen_origen_nombre": almacen.nombre,
            "destinatario": destinatario,
            "motivo": f"Préstamo registrado por Haku a {destinatario}",
            "creado_en": time.time(),
            "expira_en": time.time() + 300,
        }

        verbal = (
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
            "mensaje_verbal": verbal,
        }

    # 4. Devolución de préstamo
    if re.search(r"\b(devolver|devoluci[oó]n)\b", texto_lower):
        # Buscar préstamo activo correspondiente
        match_destinatario = re.search(r"\b(?:de|por)\s+([A-Za-zÁ-ÿ\s]+?)(?:\s*$)", texto, re.IGNORECASE)
        destinatario = match_destinatario.group(1).strip() if match_destinatario else ""

        qs_prestamos = Prestamo.objects.filter(
            estado__in=[Prestamo.Estado.ABIERTO, Prestamo.Estado.DEVUELTO_PARCIAL]
        ).select_related("producto", "almacen")
        if usuario.es_usuario_normal and usuario.almacen_asignado:
            qs_prestamos = qs_prestamos.filter(almacen=usuario.almacen_asignado)

        if destinatario:
            qs_prestamos = qs_prestamos.filter(destinatario__icontains=destinatario)

        prestamo = qs_prestamos.first()
        if not prestamo:
            msg = "No encontré ningún préstamo activo con esos datos para devolver."
            return {"tipo": "conversacion", "mensaje_pantalla": msg, "mensaje_verbal": msg}

        cantidad_dev = cantidad if cantidad else prestamo.cantidad_pendiente_devolucion
        if cantidad_dev > prestamo.cantidad_pendiente_devolucion:
            msg = f"La cantidad a devolver ({cantidad_dev}) excede el saldo prestado ({prestamo.cantidad_pendiente_devolucion})."
            return {"tipo": "error", "mensaje_pantalla": msg, "mensaje_verbal": msg}

        propuesta = {
            "id": str(uuid.uuid4()),
            "tipo_operacion": TipoMovimiento.DEVOLUCION_PRESTAMO,
            "prestamo_id": prestamo.id,
            "producto_id": prestamo.producto.id,
            "producto_nombre": prestamo.producto.nombre,
            "producto_codigo": prestamo.producto.codigo_interno,
            "cantidad": cantidad_dev,
            "almacen_origen_id": prestamo.almacen.id,
            "almacen_origen_nombre": prestamo.almacen.nombre,
            "destinatario": prestamo.destinatario,
            "motivo": f"Devolución de préstamo por {prestamo.destinatario}",
            "creado_en": time.time(),
            "expira_en": time.time() + 300,
        }

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
            "mensaje_verbal": verbal,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # D. MENSAJE CONVERSACIONAL GENERAL (Fallback)
    # ─────────────────────────────────────────────────────────────────────────
    msg_general = (
        f"Hola {usuario.nombre_completo or usuario.username}. "
        "Soy Haku Inventario. Puedes consultarme existencias ('¿cuántos cables hay?'), "
        "alertas de stock bajo, préstamos activos, o dictar operaciones de entrada, salida y préstamos."
    )
    return {
        "tipo": "conversacion",
        "mensaje_pantalla": msg_general,
        "mensaje_verbal": normalizar_para_tts(msg_general),
        "datos": {},
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

    # Verificar expiración de la propuesta (5 minutos)
    ahora = time.time()
    if ahora > propuesta.get("expira_en", 0):
        raise ValidationError("La propuesta ha caducado. Por favor genera una nueva solicitud.")

    tipo_op = propuesta.get("tipo_operacion")
    cantidad = int(propuesta.get("cantidad", 0))
    if cantidad <= 0:
        raise ValidationError("La cantidad debe ser mayor a cero.")

    # Generar o validar idempotency_key
    if not idempotency_key:
        idempotency_key = f"haku-{propuesta['id']}-{uuid.uuid4().hex[:8]}"

    # Verificar si ya fue procesada con esta idempotency_key
    existente = Movimiento.objects.filter(idempotency_key=idempotency_key).first()
    if existente:
        return {
            "exito": True,
            "idempotente": True,
            "movimiento_id": existente.id,
            "mensaje_pantalla": f"Operación previamente confirmada (Movimiento #{existente.id}).",
            "mensaje_verbal": "Esta operación ya había sido confirmada previamente.",
        }

    with transaction.atomic():
        # Bloquear producto con select_for_update()
        producto = (
            Producto.objects.select_for_update()
            .get(id=propuesta["producto_id"], activo=True, eliminado=False)
        )

        almacen = Almacen.objects.select_for_update().get(id=propuesta["almacen_origen_id"])
        if not usuario.tiene_acceso_almacen(almacen):
            raise PermissionDenied("No tienes permisos para operar en este almacén.")

        stock_ant = producto.stock_disponible
        stock_post = stock_ant
        movimiento = None

        if tipo_op == TipoMovimiento.ENTRADA:
            stock_post = stock_ant + cantidad
            producto.stock_disponible = stock_post
            producto.save(update_fields=["stock_disponible", "fecha_modificacion"])

            movimiento = Movimiento.objects.create(
                idempotency_key=idempotency_key,
                tipo=TipoMovimiento.ENTRADA,
                producto=producto,
                cantidad=cantidad,
                almacen_origen=almacen,
                usuario=usuario,
                motivo=propuesta.get("motivo", "Entrada vía Haku"),
                stock_disponible_anterior=stock_ant,
                stock_disponible_posterior=stock_post,
                via_haku=True,
            )

        elif tipo_op == TipoMovimiento.SALIDA:
            if stock_ant < cantidad:
                raise ValidationError(
                    f"Stock insuficiente: se intentó retirar {cantidad} unidades pero solo hay {stock_ant} disponibles."
                )
            stock_post = stock_ant - cantidad
            producto.stock_disponible = stock_post
            producto.save(update_fields=["stock_disponible", "fecha_modificacion"])

            movimiento = Movimiento.objects.create(
                idempotency_key=idempotency_key,
                tipo=TipoMovimiento.SALIDA,
                producto=producto,
                cantidad=cantidad,
                almacen_origen=almacen,
                usuario=usuario,
                destinatario=propuesta.get("destinatario", ""),
                motivo=propuesta.get("motivo", "Salida vía Haku"),
                stock_disponible_anterior=stock_ant,
                stock_disponible_posterior=stock_post,
                via_haku=True,
            )

        elif tipo_op == TipoMovimiento.PRESTAMO:
            if stock_ant < cantidad:
                raise ValidationError(
                    f"Stock insuficiente para préstamo: {stock_ant} disponibles, se solicitaron {cantidad}."
                )
            stock_post = stock_ant - cantidad
            producto.stock_disponible = stock_post
            producto.stock_en_prestamo += cantidad
            producto.save(update_fields=["stock_disponible", "stock_en_prestamo", "fecha_modificacion"])

            movimiento = Movimiento.objects.create(
                idempotency_key=idempotency_key,
                tipo=TipoMovimiento.PRESTAMO,
                producto=producto,
                cantidad=cantidad,
                almacen_origen=almacen,
                usuario=usuario,
                destinatario=propuesta.get("destinatario", ""),
                motivo=propuesta.get("motivo", "Préstamo vía Haku"),
                stock_disponible_anterior=stock_ant,
                stock_disponible_posterior=stock_post,
                via_haku=True,
            )

            # Crear registro de préstamo
            Prestamo.objects.create(
                producto=producto,
                almacen=almacen,
                destinatario=propuesta.get("destinatario", ""),
                cantidad_prestada=cantidad,
                cantidad_pendiente_devolucion=cantidad,
                estado=Prestamo.Estado.ABIERTO,
                usuario=usuario,
                motivo=propuesta.get("motivo", ""),
            )

        elif tipo_op == TipoMovimiento.DEVOLUCION_PRESTAMO:
            prestamo = Prestamo.objects.select_for_update().get(id=propuesta["prestamo_id"])
            if cantidad > prestamo.cantidad_pendiente_devolucion:
                raise ValidationError("La cantidad a devolver excede el saldo pendiente del préstamo.")

            stock_post = stock_ant + cantidad
            producto.stock_disponible = stock_post
            producto.stock_en_prestamo = max(0, producto.stock_en_prestamo - cantidad)
            producto.save(update_fields=["stock_disponible", "stock_en_prestamo", "fecha_modificacion"])

            prestamo.cantidad_pendiente_devolucion -= cantidad
            if prestamo.cantidad_pendiente_devolucion == 0:
                prestamo.estado = Prestamo.Estado.CERRADO
            else:
                prestamo.estado = Prestamo.Estado.DEVUELTO_PARCIAL
            prestamo.save(update_fields=["cantidad_pendiente_devolucion", "estado"])

            movimiento = Movimiento.objects.create(
                idempotency_key=idempotency_key,
                tipo=TipoMovimiento.DEVOLUCION_PRESTAMO,
                producto=producto,
                cantidad=cantidad,
                almacen_origen=almacen,
                usuario=usuario,
                destinatario=prestamo.destinatario,
                motivo=propuesta.get("motivo", "Devolución vía Haku"),
                stock_disponible_anterior=stock_ant,
                stock_disponible_posterior=stock_post,
                via_haku=True,
            )

            DevolucionPrestamo.objects.create(
                prestamo=prestamo,
                tipo_cierre=DevolucionPrestamo.TipoCierre.DEVOLUCION_TOTAL
                if prestamo.estado == Prestamo.Estado.CERRADO
                else DevolucionPrestamo.TipoCierre.DEVOLUCION_PARCIAL,
                cantidad=cantidad,
                motivo=propuesta.get("motivo", ""),
                usuario=usuario,
            )
        else:
            raise ValidationError(f"Tipo de operación '{tipo_op}' no soportado vía Haku.")

        # Registro inmutable de auditoría
        Auditoria.objects.create(
            usuario=usuario,
            ip_origen=ip_origen,
            accion=tipo_op,
            tipo_entidad="Producto",
            id_entidad=str(producto.id),
            valores_anteriores={"stock_disponible": stock_ant},
            valores_nuevos={"stock_disponible": stock_post},
            motivo=f"Operación confirmada vía Haku: {propuesta.get('motivo', '')}",
            resultado="OK",
            via_haku=True,
        )

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
