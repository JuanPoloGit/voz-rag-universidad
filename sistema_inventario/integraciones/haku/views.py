"""Vistas y endpoints de la API interna autenticada para Haku Inventario.

Todas las peticiones validan:
- Autenticación activa de Django (sesión de usuario).
- Estado de cuenta: cuentas inactivas o eliminadas lógicamente son rechazadas inmediatamente.
- Permisos por rol y almacén según especificación (Superusuario, Administrador, Usuario).
- Manejo de pre-confirmación en dos pasos para mutaciones.
"""

import json
import logging
import time

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.generic import TemplateView

from inventario.models import Almacen, Existencia, Prestamo, Producto

from .serializers import (
    ValidadorComando,
    ValidadorConfirmacion,
    ValidadorConsultaStock,
    ValidadorSintesisVoz,
)
from .services import (
    HakuOcupadoError,
    ejecutar_confirmacion_propuesta,
    normalizar_para_tts,
    procesar_audio_y_transcribir,
    procesar_comando_inventario,
    sintetizar_audio_bytes,
    transcribir_audio_bytes,
)

logger = logging.getLogger("integraciones.haku")


class HakuApiBaseView(LoginRequiredMixin, View):
    """Clase base para todos los endpoints de Haku con validación de seguridad."""

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({"error": "No autenticado. Inicie sesión."}, status=401)

        if not request.user.is_active or getattr(request.user, "eliminado_logicamente", False):
            return JsonResponse({"error": "Cuenta desactivada o eliminada.", "sesion_activa": False}, status=403)

        return super().dispatch(request, *args, **kwargs)

    @staticmethod
    def _obtener_json(request) -> dict:
        """Parsea el cuerpo de la petición como JSON."""
        if not request.body:
            return {}
        try:
            return json.loads(request.body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}


class MensajeHakuView(HakuApiBaseView):
    """Endpoint principal de interacción conversacional (texto o voz transcrita).

    POST /api/haku/mensaje/
    Payload: {"mensaje": "..."}
    """

    def post(self, request):
        datos = self._obtener_json(request)
        try:
            validados = ValidadorComando.validar(datos)
        except ValidationError as err:
            return JsonResponse({"error": err.message_dict if hasattr(err, "message_dict") else str(err)}, status=400)

        mensaje = validados["mensaje"]
        session_key = request.session.session_key
        if not session_key:
            request.session.save()
            session_key = request.session.session_key

        from .contexto import HakuOperationalContext
        almacen_id = getattr(request.user, "almacen_asignado_id", None)
        estado_op = HakuOperationalContext.obtener_o_crear(request.user.id, session_key, almacen_id)
        propuesta_activa = estado_op.propuesta_activa or request.session.get("haku_propuesta_activa")

        try:
            resultado = procesar_comando_inventario(
                usuario=request.user,
                mensaje=mensaje,
                propuesta_activa=propuesta_activa,
                sesion=request.session,
                estado_operativo=estado_op,
            )

            tipo = resultado.get("tipo")

            # 1. El usuario confirmó por voz la propuesta activa
            if tipo == "accion_confirmar" and propuesta_activa:
                res_ejecucion = ejecutar_confirmacion_propuesta(
                    usuario=request.user,
                    propuesta=propuesta_activa,
                    ip_origen=request.META.get("REMOTE_ADDR", "127.0.0.1"),
                )
                estado_op.propuesta_activa = None
                HakuOperationalContext.guardar(estado_op)
                request.session["haku_propuesta_activa"] = None
                return JsonResponse({
                    "tipo": "confirmacion_ejecutada",
                    "mensaje_pantalla": res_ejecucion["mensaje_pantalla"],
                    "mensaje_verbal": res_ejecucion["mensaje_verbal"],
                    "movimiento_id": res_ejecucion["movimiento_id"],
                    "propuesta_activa": False,
                })

            # 2. El usuario canceló por voz la propuesta activa
            if tipo == "accion_cancelar" and propuesta_activa:
                estado_op.propuesta_activa = None
                HakuOperationalContext.guardar(estado_op)
                request.session["haku_propuesta_activa"] = None
                return JsonResponse({
                    "tipo": "propuesta_cancelada",
                    "mensaje_pantalla": resultado["mensaje_pantalla"],
                    "mensaje_verbal": resultado["mensaje_verbal"],
                    "propuesta_activa": False,
                })

            # 3. Se generó una nueva propuesta que requiere confirmación
            if tipo == "propuesta" and resultado.get("requiere_confirmacion"):
                estado_op.propuesta_activa = resultado["propuesta"]
                HakuOperationalContext.guardar(estado_op)
                request.session["haku_propuesta_activa"] = resultado["propuesta"]
                return JsonResponse({
                    "tipo": "propuesta",
                    "requiere_confirmacion": True,
                    "propuesta": resultado["propuesta"],
                    "mensaje_pantalla": resultado["mensaje_pantalla"],
                    "mensaje_verbal": resultado["mensaje_verbal"],
                    "propuesta_activa": True,
                })

            # 4. Consulta o diálogo general
            return JsonResponse({
                "tipo": tipo,
                "mensaje_pantalla": resultado.get("mensaje_pantalla", ""),
                "mensaje_verbal": resultado.get("mensaje_verbal", ""),
                "datos": resultado.get("datos", {}),
                "propuesta_activa": estado_op.propuesta_activa is not None,
            })

        except HakuOcupadoError:
            resp = JsonResponse({
                "error": "El servicio de asistencia se encuentra ocupado. Por favor intente nuevamente.",
                "mensaje_pantalla": "Haku ocupado procesando otra solicitud. Intenta nuevamente.",
                "mensaje_verbal": "Haku ocupado procesando otra solicitud. Intenta nuevamente.",
            }, status=503)
            resp["Retry-After"] = "5"
            return resp
        except PermissionDenied as err:
            return JsonResponse({"error": str(err)}, status=403)
        except ValidationError as err:
            return JsonResponse({"error": str(err)}, status=400)
        except Exception as exc:
            logger.exception("Error procesando mensaje en Haku: %s", exc)
            return JsonResponse({"error": "Error interno al procesar comando. Intente nuevamente más tarde."}, status=500)


class ConfirmarPropuestaView(HakuApiBaseView):
    """Confirma y ejecuta atómicamente la propuesta pendiente activa.

    POST /api/haku/confirmar/
    Payload: {"propuesta_id": "...", "idempotency_key": "..."}
    """

    def post(self, request):
        datos = self._obtener_json(request)
        try:
            validados = ValidadorConfirmacion.validar(datos)
        except ValidationError as err:
            return JsonResponse({"error": err.message_dict if hasattr(err, "message_dict") else str(err)}, status=400)

        session_key = request.session.session_key or ""
        from .contexto import HakuOperationalContext
        estado_op = HakuOperationalContext.obtener(request.user.id, session_key)
        propuesta_activa = (estado_op.propuesta_activa if estado_op else None) or request.session.get("haku_propuesta_activa")

        if not propuesta_activa:
            return JsonResponse({"error": "No hay ninguna propuesta pendiente de confirmación."}, status=404)

        if propuesta_activa.get("id") != validados["propuesta_id"]:
            return JsonResponse({"error": "El ID de la propuesta no coincide con la propuesta activa."}, status=400)

        try:
            resultado = ejecutar_confirmacion_propuesta(
                usuario=request.user,
                propuesta=propuesta_activa,
                idempotency_key=validados["idempotency_key"],
                ip_origen=request.META.get("REMOTE_ADDR", "127.0.0.1"),
            )
            if estado_op:
                estado_op.propuesta_activa = None
                HakuOperationalContext.guardar(estado_op)
            request.session["haku_propuesta_activa"] = None
            return JsonResponse({
                "exito": True,
                "mensaje_pantalla": resultado["mensaje_pantalla"],
                "mensaje_verbal": resultado["mensaje_verbal"],
                "movimiento_id": resultado.get("movimiento_id"),
                "stock_nuevo": resultado.get("stock_nuevo"),
            })
        except (PermissionDenied, ValidationError) as err:
            return JsonResponse({"error": str(err)}, status=400)
        except Exception as exc:
            logger.exception("Error al confirmar propuesta: %s", exc)
            return JsonResponse({"error": "Error al ejecutar transacción. Intente nuevamente más tarde."}, status=500)


class CancelarPropuestaView(HakuApiBaseView):
    """Descarta y cancela la propuesta pendiente activa.

    POST /api/haku/cancelar/
    Payload: {"propuesta_id": "..."}
    """

    def post(self, request):
        session_key = request.session.session_key or ""
        from .contexto import HakuOperationalContext
        estado_op = HakuOperationalContext.obtener(request.user.id, session_key)
        if estado_op:
            estado_op.propuesta_activa = None
            HakuOperationalContext.guardar(estado_op)
        request.session["haku_propuesta_activa"] = None

        return JsonResponse({
            "exito": True,
            "mensaje_pantalla": "Propuesta cancelada con éxito.",
            "mensaje_verbal": "Operación cancelada. No se realizaron cambios.",
        })




class ConsultarStockView(HakuApiBaseView):
    """Consulta directa de existencias por término de búsqueda.

    GET /api/haku/consultar-stock/?q=cable
    """

    def get(self, request):
        termino = request.GET.get("q", "").strip()
        qs = Existencia.objects.filter(
            producto__activo=True,
            producto__archivado=False,
        ).select_related("producto", "producto__categoria", "almacen")

        if request.user.es_usuario_normal and request.user.almacen_asignado:
            qs = qs.filter(almacen=request.user.almacen_asignado)

        if termino:
            qs = qs.filter(producto__nombre__icontains=termino) | qs.filter(producto__codigo_interno__icontains=termino)

        productos = []
        for e in qs[:20]:
            productos.append({
                "id": str(e.producto.id),
                "codigo": e.producto.codigo_interno,
                "codigo_barras": e.producto.codigo_barras,
                "nombre": e.producto.nombre,
                "categoria": e.producto.categoria.nombre if e.producto.categoria else "",
                "almacen": e.almacen.nombre,
                "stock_disponible": e.stock_disponible,
                "stock_en_prestamo": e.stock_en_prestamo,
                "stock_minimo": e.producto.stock_minimo,
                "estado_alerta": "SIN_STOCK" if e.sin_stock else ("BAJO_STOCK" if e.stock_bajo else "NORMAL"),
            })

        return JsonResponse({"productos": productos, "total": len(productos)})


class AlertasStockView(HakuApiBaseView):
    """Consulta de productos con alerta de stock bajo o sin stock.

    GET /api/haku/alertas/
    """

    def get(self, request):
        qs = Existencia.objects.filter(
            producto__activo=True,
            producto__archivado=False,
        ).select_related("producto", "almacen")
        if request.user.es_usuario_normal and request.user.almacen_asignado:
            qs = qs.filter(almacen=request.user.almacen_asignado)

        sin_stock = []
        bajo_stock = []

        for e in qs:
            if e.sin_stock:
                sin_stock.append({"id": str(e.producto.id), "codigo": e.producto.codigo_interno, "nombre": e.producto.nombre, "almacen": e.almacen.nombre})
            elif e.stock_bajo:
                bajo_stock.append({
                    "id": str(e.producto.id),
                    "codigo": e.producto.codigo_interno,
                    "nombre": e.producto.nombre,
                    "almacen": e.almacen.nombre,
                    "disponible": e.stock_disponible,
                    "minimo": e.producto.stock_minimo,
                })

        return JsonResponse({
            "sin_stock": sin_stock,
            "bajo_stock": bajo_stock,
            "total_alertas": len(sin_stock) + len(bajo_stock),
        })


class PrestamosActivosView(HakuApiBaseView):
    """Consulta de préstamos activos.

    GET /api/haku/prestamos-activos/
    """

    def get(self, request):
        qs = Prestamo.objects.filter(
            estado__in=[Prestamo.Estado.ABIERTO, Prestamo.Estado.DEVUELTO_PARCIAL]
        ).select_related("producto", "almacen", "usuario")

        if request.user.es_usuario_normal and request.user.almacen_asignado:
            qs = qs.filter(almacen=request.user.almacen_asignado)

        prestamos = []
        for p in qs:
            prestamos.append({
                "id": p.id,
                "producto": p.producto.nombre,
                "codigo": p.producto.codigo_interno,
                "almacen": p.almacen.nombre,
                "destinatario": p.destinatario,
                "cantidad_prestada": p.cantidad_prestada,
                "cantidad_pendiente": p.cantidad_pendiente_devolucion,
                "fecha_prestamo": p.fecha_prestamo.isoformat(),
            })

        return JsonResponse({"prestamos": prestamos, "total": len(prestamos)})


class TranscribirVozView(HakuApiBaseView):
    """Endpoint de Speech-to-Text: recibe audio desde el navegador y transcribe.

    POST /api/haku/voz/transcribir/
    Multipart form con archivo 'audio', o cuerpo binario crudo.
    """

    def post(self, request):
        audio_file = request.FILES.get("audio")
        if audio_file:
            audio_bytes = audio_file.read()
        else:
            audio_bytes = request.body

        if not audio_bytes:
            return JsonResponse({"error": "No se recibió archivo de audio."}, status=400)

        fname = getattr(audio_file, "name", "raw") if audio_file else "raw"
        logger.debug("Recibido audio STT: filename='%s', bytes=%d", fname, len(audio_bytes))

        try:
            res_audio = procesar_audio_y_transcribir(audio_bytes)
            logger.debug(
                "Resultado STT: estado=%s, motivo=%s, texto='%s'",
                res_audio.get("estado"),
                res_audio.get("motivo"),
                res_audio.get("texto"),
            )

            if res_audio.get("estado") == "SIN_VOZ":
                return JsonResponse({
                    "status": "silencio",
                    "estado": "SIN_VOZ",
                    "motivo": res_audio.get("motivo", "AUDIO_SILENCIOSO"),
                    "texto": "",
                    "mensaje_pantalla": "No se detectó voz audible en el micrófono.",
                    "mensaje_verbal": "No escuché ningún comando. Por favor intenta hablar de nuevo.",
                    "metricas": res_audio.get("metricas", {}),
                })

            if res_audio.get("estado") == "ERROR_TRANSCRIPCION":
                return JsonResponse({
                    "status": "error_transcripcion",
                    "estado": "ERROR_TRANSCRIPCION",
                    "motivo": res_audio.get("motivo", "ERROR_STT"),
                    "texto": "",
                    "mensaje_pantalla": "Hubo un problema al transcribir el audio en el servidor.",
                    "mensaje_verbal": "Hubo un error de transcripción. Por favor intenta de nuevo.",
                    "error": res_audio.get("error", "Error en motor STT"),
                    "metricas": res_audio.get("metricas", {}),
                })

            return JsonResponse({
                "status": "ok",
                "estado": "VOZ_DETECTADA",
                "texto": res_audio.get("texto", ""),
                "longitud_bytes": len(audio_bytes),
                "metricas": res_audio.get("metricas", {}),
            })
        except HakuOcupadoError:
            resp = JsonResponse({
                "estado": "HAKU_OCUPADO",
                "mensaje_pantalla": "Haku ocupado procesando otra solicitud. Intenta nuevamente.",
                "mensaje_verbal": "Haku ocupado procesando otra solicitud. Intenta nuevamente.",
                "error": "El servicio de audio se encuentra ocupado. Por favor intente nuevamente.",
            }, status=503)
            resp["Retry-After"] = "5"
            return resp
        except Exception as exc:
            logger.exception("Error al transcribir audio: %s", exc)
            return JsonResponse({"error": "Error interno en transcripción de audio."}, status=500)


class SintetizarVozView(HakuApiBaseView):
    """Endpoint de Text-to-Speech: sintetiza audio WAV para el navegador cliente.

    POST /api/haku/voz/sintetizar/
    Payload: {"texto": "..."}
    Retorna: audio/wav stream binario.

    CRÍTICO: Este audio suena exclusivamente en el dispositivo del cliente.
    """

    def post(self, request):
        datos = self._obtener_json(request)
        try:
            validados = ValidadorSintesisVoz.validar(datos)
        except ValidationError as err:
            return JsonResponse({"error": str(err)}, status=400)

        texto = validados["texto"]
        try:
            from .audio.synthesizer import HakuSynthesizerError
            wav_bytes = sintetizar_audio_bytes(texto)
            response = HttpResponse(wav_bytes, content_type="audio/wav")
            response["Content-Disposition"] = 'inline; filename="haku_voz.wav"'
            response["Cache-Control"] = "no-cache, no-store, must-revalidate"
            return response
        except HakuSynthesizerError as exc:
            logger.error("Error en síntesis TTS de Haku: %s", exc)
            resp = JsonResponse({
                "status": "error_tts",
                "mensaje": "La síntesis de voz no está disponible actualmente.",
                "error": "Servicio de síntesis de voz temporalmente no disponible.",
            }, status=503)
            resp["Retry-After"] = "5"
            return resp
        except Exception as exc:
            logger.exception("Error inesperado en síntesis de voz: %s", exc)
            return JsonResponse({
                "status": "error_tts",
                "mensaje": "La síntesis de voz no está disponible actualmente.",
            }, status=500)


class EstadoHakuView(HakuApiBaseView):
    """Consulta el estado de la sesión conversacional de Haku.

    GET /api/haku/estado/
    """

    def get(self, request):
        session_key = request.session.session_key or ""
        from .contexto import HakuOperationalContext
        estado_op = HakuOperationalContext.obtener(request.user.id, session_key)
        propuesta_activa = (estado_op.propuesta_activa if estado_op else None) or request.session.get("haku_propuesta_activa")
        contexto_expirado = estado_op is None or estado_op.producto_id is None

        almacen_nombre = (
            request.user.almacen_asignado.nombre
            if (request.user.es_usuario_normal and request.user.almacen_asignado)
            else "Todos los almacenes"
        )
        return JsonResponse({
            "usuario": request.user.username,
            "nombre_completo": request.user.nombre_completo or request.user.username,
            "rol": request.user.rol,
            "almacen": almacen_nombre,
            "tiene_propuesta_activa": propuesta_activa is not None,
            "propuesta_activa": propuesta_activa,
            "contexto_expirado": contexto_expirado,
        })


@method_decorator(ensure_csrf_cookie, name="dispatch")
class PanelHakuView(LoginRequiredMixin, TemplateView):
    """Vista web completa para la interfaz de voz y panel de Haku Inventario."""

    template_name = "haku/panel_voz.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["usuario"] = self.request.user
        context["almacen"] = (
            self.request.user.almacen_asignado.nombre
            if (self.request.user.es_usuario_normal and self.request.user.almacen_asignado)
            else "Todos los almacenes"
        )
        return context
