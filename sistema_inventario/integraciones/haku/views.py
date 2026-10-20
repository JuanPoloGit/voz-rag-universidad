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
from django.views.generic import TemplateView
from django.views.decorators.csrf import ensure_csrf_cookie
from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from cuentas.models import UsuarioAudacIA
from inventario.models import Almacen, Auditoria, Existencia, Prestamo, Producto

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
        content_type = getattr(audio_file, "content_type", "unknown") if audio_file else request.content_type
        logger.warning(
            "[HakuSTT] Petición recibida: archivo='%s', MIME='%s', bytes=%d",
            fname, content_type, len(audio_bytes)
        )

        try:
            res_audio = procesar_audio_y_transcribir(audio_bytes)
            metricas = res_audio.get("metricas", {})
            orig = metricas.get("metricas_originales", {})
            logger.warning(
                "[HakuSTT] Resultado pipeline: estado=%s, motivo=%s, duracion=%.2fs, RMS=%.1f dBFS, Pico=%.1f dBFS, SNR=%.1f dB",
                res_audio.get("estado"),
                res_audio.get("motivo"),
                metricas.get("duracion_seg", 0.0),
                orig.get("rms_dbfs", -100.0),
                orig.get("pico_dbfs", -100.0),
                orig.get("snr_db", 0.0),
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
class ProductoCrearHakuView(HakuApiBaseView):
    """Crea un nuevo producto en el inventario validando campos y permisos existentes.

    POST /api/haku/producto/crear/
    """

    def post(self, request):
        if request.user.es_usuario_normal:
            raise PermissionDenied("Solo los Administradores o Superusuarios pueden registrar productos.")

        datos = self._obtener_json(request)
        nombre = str(datos.get("nombre", "")).strip()
        categoria_id = datos.get("categoria_id")
        codigo_barras = str(datos.get("codigo_barras", "")).strip() or None
        stock_minimo = datos.get("stock_minimo", 0)
        nombre_empaque = str(datos.get("nombre_empaque", "")).strip()
        unidades_por_empaque = datos.get("unidades_por_empaque")
        descripcion = str(datos.get("descripcion", "")).strip()

        if not nombre:
            return JsonResponse({"error": "El nombre del producto es obligatorio."}, status=400)
        if not categoria_id:
            return JsonResponse({"error": "La categoría es obligatoria."}, status=400)

        from inventario.models import Categoria, Producto, Auditoria
        categoria = Categoria.objects.filter(id=categoria_id, activo=True, eliminado=False).first()
        if not categoria:
            return JsonResponse({"error": "La categoría seleccionada no existe o está inactiva."}, status=400)

        if codigo_barras:
            if Producto.objects.filter(codigo_barras=codigo_barras).exists():
                return JsonResponse({"error": f"El código de barras '{codigo_barras}' ya está registrado en otro producto."}, status=400)

        try:
            stock_minimo = int(stock_minimo) if stock_minimo is not None else 0
            if stock_minimo < 0:
                return JsonResponse({"error": "El stock mínimo no puede ser negativo."}, status=400)
        except (ValueError, TypeError):
            return JsonResponse({"error": "El stock mínimo debe ser un número entero válido."}, status=400)

        if unidades_por_empaque:
            try:
                unidades_por_empaque = int(unidades_por_empaque)
                if unidades_por_empaque <= 0:
                    return JsonResponse({"error": "Las unidades por empaque deben ser mayores a cero."}, status=400)
            except (ValueError, TypeError):
                return JsonResponse({"error": "Las unidades por empaque deben ser un número válido."}, status=400)
        else:
            unidades_por_empaque = None

        from django.db import transaction
        with transaction.atomic():
            producto = Producto(
                nombre=nombre,
                categoria=categoria,
                codigo_barras=codigo_barras,
                stock_minimo=stock_minimo,
                nombre_empaque=nombre_empaque,
                unidades_por_empaque=unidades_por_empaque,
                descripcion=descripcion,
                activo=True,
                archivado=False,
            )
            producto.full_clean()
            producto.save()

            stock_inicial = int(datos.get("stock_inicial", 0) or 0)
            almacen_id = datos.get("almacen_id")
            if stock_inicial > 0 and almacen_id:
                from inventario.models import Almacen, Existencia, Movimiento
                almacen = Almacen.objects.filter(id=almacen_id, activo=True).first()
                if almacen:
                    existencia, _ = Existencia.objects.get_or_create(
                        producto=producto,
                        almacen=almacen,
                        defaults={"stock_disponible": stock_inicial, "stock_en_prestamo": 0},
                    )
                    if existencia.stock_disponible != stock_inicial:
                        existencia.stock_disponible = stock_inicial
                        existencia.save(update_fields=["stock_disponible"])
                    
                    Movimiento.objects.create(
                        clave_idempotencia=f"init-stock-{producto.id}-{almacen.id}",
                        tipo=Movimiento.Tipo.ENTRADA,
                        producto=producto,
                        cantidad=stock_inicial,
                        almacen_origen=almacen,
                        usuario=request.user,
                        motivo="Stock inicial al registrar producto vía Haku",
                        stock_disponible_anterior=0,
                        stock_disponible_posterior=stock_inicial,
                    )

            Auditoria.objects.create(
                usuario=request.user,
                accion="CREAR_PRODUCTO",
                tipo_entidad="Producto",
                id_entidad=str(producto.id),
                valores_anteriores={},
                valores_nuevos={
                    "codigo_interno": producto.codigo_interno,
                    "nombre": producto.nombre,
                    "categoria": categoria.nombre,
                    "stock_minimo": stock_minimo,
                },
                motivo="Producto creado mediante formulario interactivo de Haku",
                resultado="OK",
                via_haku=True,
            )

        from .cache import HakuCacheManager
        HakuCacheManager.invalidar_catalogo()

        msg_exito = f"Producto {producto.nombre} creado exitosamente con código interno {producto.codigo_interno}."
        return JsonResponse({
            "exito": True,
            "producto": {
                "id": producto.id,
                "codigo_interno": producto.codigo_interno,
                "nombre": producto.nombre,
                "categoria": categoria.nombre,
            },
            "mensaje_pantalla": msg_exito,
            "mensaje_verbal": normalizar_para_tts(f"Producto {producto.nombre} creado con código {producto.codigo_interno}."),
        })


class ProductoEditarHakuView(HakuApiBaseView):
    """Edita un producto existente validando campos permitidos y restricciones de unicidad.

    POST /api/haku/producto/editar/
    """

    def post(self, request):
        if request.user.es_usuario_normal:
            raise PermissionDenied("Solo los Administradores o Superusuarios pueden modificar productos.")

        datos = self._obtener_json(request)
        producto_id = datos.get("producto_id")
        if not producto_id:
            return JsonResponse({"error": "El ID del producto es obligatorio."}, status=400)

        from inventario.models import Categoria, Producto, Auditoria
        producto = Producto.objects.filter(id=producto_id, archivado=False).first()
        if not producto:
            return JsonResponse({"error": "El producto especificado no existe o fue archivado."}, status=404)

        nombre = str(datos.get("nombre", producto.nombre)).strip()
        if not nombre:
            return JsonResponse({"error": "El nombre del producto no puede estar vacío."}, status=400)

        categoria_id = datos.get("categoria_id", producto.categoria_id)
        categoria = Categoria.objects.filter(id=categoria_id, activo=True, eliminado=False).first()
        if not categoria:
            return JsonResponse({"error": "La categoría seleccionada no existe o está inactiva."}, status=400)

        codigo_barras = datos.get("codigo_barras")
        if codigo_barras is not None:
            codigo_barras = str(codigo_barras).strip() or None
            if codigo_barras and Producto.objects.filter(codigo_barras=codigo_barras).exclude(id=producto.id).exists():
                return JsonResponse({"error": f"El código de barras '{codigo_barras}' ya pertenece a otro producto."}, status=400)

        stock_minimo = datos.get("stock_minimo", producto.stock_minimo)
        try:
            stock_minimo = int(stock_minimo)
            if stock_minimo < 0:
                return JsonResponse({"error": "El stock mínimo no puede ser negativo."}, status=400)
        except (ValueError, TypeError):
            return JsonResponse({"error": "El stock mínimo debe ser un número entero válido."}, status=400)

        unidades_por_empaque = datos.get("unidades_por_empaque", producto.unidades_por_empaque)
        if unidades_por_empaque:
            try:
                unidades_por_empaque = int(unidades_por_empaque)
                if unidades_por_empaque <= 0:
                    return JsonResponse({"error": "Las unidades por empaque deben ser mayores a cero."}, status=400)
            except (ValueError, TypeError):
                return JsonResponse({"error": "Las unidades por empaque deben ser un número válido."}, status=400)
        else:
            unidades_por_empaque = None

        nombre_empaque = str(datos.get("nombre_empaque", producto.nombre_empaque)).strip()
        descripcion = str(datos.get("descripcion", producto.descripcion)).strip()

        valores_anteriores = {
            "nombre": producto.nombre,
            "categoria_id": producto.categoria_id,
            "codigo_barras": producto.codigo_barras,
            "stock_minimo": producto.stock_minimo,
        }

        from django.db import transaction
        with transaction.atomic():
            producto.nombre = nombre
            producto.categoria = categoria
            producto.codigo_barras = codigo_barras
            producto.stock_minimo = stock_minimo
            producto.nombre_empaque = nombre_empaque
            producto.unidades_por_empaque = unidades_por_empaque
            producto.descripcion = descripcion
            producto.full_clean()
            producto.save()

            Auditoria.objects.create(
                usuario=request.user,
                accion="EDITAR_PRODUCTO",
                tipo_entidad="Producto",
                id_entidad=str(producto.id),
                valores_anteriores=valores_anteriores,
                valores_nuevos={
                    "nombre": producto.nombre,
                    "categoria_id": producto.categoria_id,
                    "codigo_barras": producto.codigo_barras,
                    "stock_minimo": producto.stock_minimo,
                },
                motivo="Producto actualizado mediante formulario interactivo de Haku",
                resultado="OK",
                via_haku=True,
            )

        from .cache import HakuCacheManager
        HakuCacheManager.invalidar_catalogo()
        HakuCacheManager.invalidar_stock(producto.id)
        HakuCacheManager.invalidar_ubicaciones(producto.id)

        msg_exito = f"El producto {producto.nombre} ({producto.codigo_interno}) ha sido actualizado con éxito."
        return JsonResponse({
            "exito": True,
            "producto": {
                "id": producto.id,
                "codigo_interno": producto.codigo_interno,
                "nombre": producto.nombre,
                "categoria": categoria.nombre,
                "stock_minimo": producto.stock_minimo,
            },
            "mensaje_pantalla": msg_exito,
            "mensaje_verbal": normalizar_para_tts(f"Producto {producto.nombre} actualizado correctamente."),
        })


class UsuarioCrearHakuView(HakuApiBaseView):
    """Crea un usuario desde el formulario integrado de HAKU.

    POST /api/haku/usuario/crear/
    """

    def post(self, request):
        usuario = request.user
        if not usuario.is_authenticated or not usuario.is_active or getattr(usuario, "eliminado_logicamente", False):
            return JsonResponse({"error": "No autenticado o cuenta inactiva."}, status=401)

        # Regla 1: Usuario normal no tiene acceso a la gestión de cuentas
        if usuario.es_usuario_normal:
            return JsonResponse({
                "error": "No tienes autorización para crear usuarios.",
                "mensaje_verbal": "No tienes autorización para crear usuarios en el sistema."
            }, status=403)

        payload = self._obtener_json(request)
        if not isinstance(payload, dict):
            return JsonResponse({"error": "Payload inválido."}, status=400)

        username = (payload.get("username") or "").strip().lower()
        nombre_completo = (payload.get("nombre_completo") or "").strip()
        password = payload.get("password") or ""
        rol = (payload.get("rol") or "").strip().upper()
        almacen_id = payload.get("almacen_asignado_id")

        errores = {}
        if not username:
            errores["username"] = "El nombre de usuario es obligatorio."
        elif UsuarioAudacIA.objects.filter(username=username).exists():
            errores["username"] = "Ya existe un usuario con este nombre de usuario."

        if not nombre_completo:
            errores["nombre_completo"] = "El nombre completo es obligatorio."

        if not rol:
            errores["rol"] = "El rol es obligatorio."
        elif rol not in [UsuarioAudacIA.Rol.SUPERUSUARIO, UsuarioAudacIA.Rol.ADMINISTRADOR, UsuarioAudacIA.Rol.USUARIO]:
            errores["rol"] = "El rol especificado no es válido."

        # Regla 2: Un administrador no puede crear un superusuario ni asignar roles superiores a los suyos
        if not usuario.es_superusuario_inventario and rol == UsuarioAudacIA.Rol.SUPERUSUARIO:
            return JsonResponse({
                "error": "No tienes autorización para crear un superusuario.",
                "mensaje_verbal": "No tienes autorización para crear una cuenta con rol superusuario."
            }, status=403)

        # Regla 3: Almacén asignado obligatorio para rol USUARIO
        almacen = None
        if rol == UsuarioAudacIA.Rol.USUARIO:
            if not almacen_id:
                errores["almacen_asignado_id"] = "El almacén es obligatorio para usuarios normales."
            else:
                try:
                    almacen = Almacen.objects.get(id=almacen_id, activo=True)
                except Almacen.DoesNotExist:
                    errores["almacen_asignado_id"] = "El almacén seleccionado no existe o no está activo."
        else:
            if almacen_id:
                try:
                    almacen = Almacen.objects.get(id=almacen_id, activo=True)
                except Almacen.DoesNotExist:
                    pass

        # Regla 4: Validar contraseña según Django
        if not password:
            errores["password"] = "La contraseña es obligatoria."
        else:
            try:
                validate_password(password)
            except ValidationError as e:
                errores["password"] = " ".join(e.messages)

        if errores:
            return JsonResponse({"error": "Errores de validación.", "detalles": errores}, status=400)

        # Persistir con atomicidad y registro de auditoría
        with transaction.atomic():
            nuevo_usuario = UsuarioAudacIA(
                username=username,
                nombre_completo=nombre_completo,
                rol=rol,
                almacen_asignado=almacen,
                is_active=True,
                eliminado_logicamente=False,
            )
            nuevo_usuario.set_password(password)
            nuevo_usuario.save()

            # Auditoría segura sin contraseñas
            Auditoria.objects.create(
                usuario=usuario,
                accion="CREAR_USUARIO",
                tipo_entidad="Usuario",
                id_entidad=str(nuevo_usuario.pk),
                valores_nuevos={
                    "username": nuevo_usuario.username,
                    "nombre_completo": nuevo_usuario.nombre_completo,
                    "rol": nuevo_usuario.rol,
                    "almacen": nuevo_usuario.almacen_asignado.nombre if nuevo_usuario.almacen_asignado else None,
                    "is_active": nuevo_usuario.is_active,
                },
                motivo="Creación de usuario mediante HAKU Inventario",
                resultado="OK",
                via_haku=True,
            )

        msg_exito = f"Usuario {nuevo_usuario.username} ({nuevo_usuario.nombre_completo}) creado exitosamente con rol {nuevo_usuario.rol}."
        return JsonResponse({
            "status": "ok",
            "usuario": {
                "id": nuevo_usuario.id,
                "username": nuevo_usuario.username,
                "nombre_completo": nuevo_usuario.nombre_completo,
                "rol": nuevo_usuario.rol,
                "almacen": nuevo_usuario.almacen_asignado.nombre if nuevo_usuario.almacen_asignado else None,
            },
            "mensaje_pantalla": msg_exito,
            "mensaje_verbal": normalizar_para_tts(f"Usuario {nuevo_usuario.username} creado correctamente."),
        })


class UsuarioEditarHakuView(HakuApiBaseView):
    """Edita un usuario desde el formulario integrado de HAKU.

    POST /api/haku/usuario/editar/
    """

    def post(self, request):
        usuario = request.user
        if not usuario.is_authenticated or not usuario.is_active or getattr(usuario, "eliminado_logicamente", False):
            return JsonResponse({"error": "No autenticado o cuenta inactiva."}, status=401)

        # Regla 1: Usuario normal no tiene acceso a la gestión de cuentas
        if usuario.es_usuario_normal:
            return JsonResponse({
                "error": "No tienes autorización para editar usuarios.",
                "mensaje_verbal": "No tienes autorización para gestionar cuentas en el sistema."
            }, status=403)

        payload = self._obtener_json(request)
        if not isinstance(payload, dict):
            return JsonResponse({"error": "Payload inválido."}, status=400)

        usuario_id = payload.get("id") or payload.get("usuario_id")
        if not usuario_id:
            return JsonResponse({"error": "Se requiere el ID del usuario a editar."}, status=400)

        try:
            target_user = UsuarioAudacIA.objects.select_for_update().get(id=usuario_id)
        except UsuarioAudacIA.DoesNotExist:
            return JsonResponse({"error": "El usuario especificado no existe."}, status=404)

        # Regla 2: Un administrador no puede editar, inactivar, eliminar ni modificar una cuenta SUPERUSUARIO
        if not usuario.es_superusuario_inventario and target_user.es_superusuario_inventario:
            return JsonResponse({
                "error": "No tienes autorización para modificar una cuenta de superusuario.",
                "mensaje_verbal": "No tienes autorización para modificar cuentas con rol superusuario."
            }, status=403)

        nuevo_rol = (payload.get("rol") or target_user.rol).strip().upper()
        # Regla 3: Un administrador no puede convertir una cuenta en SUPERUSUARIO ni elevar sus propios privilegios
        if not usuario.es_superusuario_inventario and nuevo_rol == UsuarioAudacIA.Rol.SUPERUSUARIO:
            return JsonResponse({
                "error": "No tienes autorización para asignar el rol de superusuario.",
                "mensaje_verbal": "No puedes conceder privilegios de superusuario."
            }, status=403)

        nombre_completo = (payload.get("nombre_completo") or target_user.nombre_completo).strip()
        almacen_id = payload.get("almacen_asignado_id")
        is_active = payload.get("is_active") if "is_active" in payload else target_user.is_active
        eliminado_logicamente = payload.get("eliminado_logicamente") if "eliminado_logicamente" in payload else target_user.eliminado_logicamente

        # Regla 4: Protección del último superusuario
        if target_user.es_superusuario_inventario:
            democion = nuevo_rol != UsuarioAudacIA.Rol.SUPERUSUARIO
            inactivacion = not is_active or eliminado_logicamente
            if democion or inactivacion:
                otros_superusuarios = UsuarioAudacIA.objects.filter(
                    rol=UsuarioAudacIA.Rol.SUPERUSUARIO,
                    is_active=True,
                    eliminado_logicamente=False
                ).exclude(id=target_user.id).count()

                if otros_superusuarios == 0:
                    return JsonResponse({
                        "error": "No se puede despojar de privilegios, inactivar o eliminar al único superusuario activo del sistema.",
                        "mensaje_verbal": "Operación rechazada. No se puede deshabilitar al único superusuario activo."
                    }, status=400)

        errores = {}
        if not nombre_completo:
            errores["nombre_completo"] = "El nombre completo no puede estar vacío."

        if nuevo_rol not in [UsuarioAudacIA.Rol.SUPERUSUARIO, UsuarioAudacIA.Rol.ADMINISTRADOR, UsuarioAudacIA.Rol.USUARIO]:
            errores["rol"] = "Rol inválido."

        almacen = target_user.almacen_asignado
        if "almacen_asignado_id" in payload:
            if nuevo_rol == UsuarioAudacIA.Rol.USUARIO:
                if not almacen_id:
                    errores["almacen_asignado_id"] = "El almacén es obligatorio para usuarios normales."
                else:
                    try:
                        almacen = Almacen.objects.get(id=almacen_id, activo=True)
                    except Almacen.DoesNotExist:
                        errores["almacen_asignado_id"] = "El almacén seleccionado no existe o no está activo."
            else:
                if almacen_id:
                    try:
                        almacen = Almacen.objects.get(id=almacen_id, activo=True)
                    except Almacen.DoesNotExist:
                        pass
                else:
                    almacen = None

        # Contraseña opcional en edición
        nueva_password = payload.get("password")
        if nueva_password:
            try:
                validate_password(nueva_password, user=target_user)
            except ValidationError as e:
                errores["password"] = " ".join(e.messages)

        if errores:
            return JsonResponse({"error": "Errores de validación.", "detalles": errores}, status=400)

        valores_anteriores = {
            "nombre_completo": target_user.nombre_completo,
            "rol": target_user.rol,
            "almacen": target_user.almacen_asignado.nombre if target_user.almacen_asignado else None,
            "is_active": target_user.is_active,
            "eliminado_logicamente": target_user.eliminado_logicamente,
        }

        with transaction.atomic():
            target_user.nombre_completo = nombre_completo
            target_user.rol = nuevo_rol
            target_user.almacen_asignado = almacen
            target_user.is_active = is_active
            target_user.eliminado_logicamente = eliminado_logicamente
            if nueva_password:
                target_user.set_password(nueva_password)
            target_user.save()

            valores_nuevos = {
                "nombre_completo": target_user.nombre_completo,
                "rol": target_user.rol,
                "almacen": target_user.almacen_asignado.nombre if target_user.almacen_asignado else None,
                "is_active": target_user.is_active,
                "eliminado_logicamente": target_user.eliminado_logicamente,
            }

            Auditoria.objects.create(
                usuario=usuario,
                accion="EDITAR_USUARIO",
                tipo_entidad="Usuario",
                id_entidad=str(target_user.pk),
                valores_anteriores=valores_anteriores,
                valores_nuevos=valores_nuevos,
                motivo="Modificación de usuario mediante HAKU Inventario",
                resultado="OK",
                via_haku=True,
            )

        msg_exito = f"Usuario {target_user.username} actualizado correctamente."
        return JsonResponse({
            "status": "ok",
            "usuario": {
                "id": target_user.id,
                "username": target_user.username,
                "nombre_completo": target_user.nombre_completo,
                "rol": target_user.rol,
                "almacen": target_user.almacen_asignado.nombre if target_user.almacen_asignado else None,
                "is_active": target_user.is_active,
            },
            "mensaje_pantalla": msg_exito,
            "mensaje_verbal": normalizar_para_tts(f"Usuario {target_user.username} actualizado correctamente."),
        })


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
