"""Utilidades, mixins y vistas base compartidas por las vistas de inventario."""

import ast
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.views.generic import RedirectView


def _obtener_ip(request) -> str:
    """Extrae la IP origen de la petición."""
    x_forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded:
        return x_forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "127.0.0.1")


def _procesar_error_formulario(form, exc):
    """Extrae y formatea limpiamente excepciones y ValidationError en campos del formulario sin volcar JSON crudo."""
    if isinstance(exc, ValidationError):
        if hasattr(exc, "message_dict"):
            for campo, msgs in exc.message_dict.items():
                for msg in msgs:
                    if campo in form.fields:
                        form.add_error(campo, msg)
                    else:
                        form.add_error(None, msg)
            return
        if hasattr(exc, "messages"):
            for msg in exc.messages:
                form.add_error(None, msg)
            return

    msg_str = str(exc).strip()
    if msg_str.startswith("{") and msg_str.endswith("}"):
        try:
            parsed = ast.literal_eval(msg_str)
            if isinstance(parsed, dict):
                for campo, msgs in parsed.items():
                    if isinstance(msgs, list):
                        for m in msgs:
                            if campo in form.fields:
                                form.add_error(campo, m)
                            else:
                                form.add_error(None, m)
                    else:
                        if campo in form.fields:
                            form.add_error(campo, str(msgs))
                        else:
                            form.add_error(None, str(msgs))
                return
        except Exception:
            pass

    form.add_error(None, msg_str)


def _formatear_error_excepcion(exc: Exception) -> str:
    """Extrae un mensaje de error limpio y legible para el usuario, sin listas ni corchetes."""
    if hasattr(exc, "messages") and exc.messages:
        return " ".join(str(m) for m in exc.messages)
    if hasattr(exc, "message_dict") and exc.message_dict:
        partes = []
        for campo, errs in exc.message_dict.items():
            err_text = ", ".join(str(e) for e in errs)
            partes.append(f"{campo}: {err_text}" if campo != "__all__" else err_text)
        return " | ".join(partes)
    texto = str(exc).strip()
    if (texto.startswith("['") and texto.endswith("']")) or (texto.startswith('["') and texto.endswith('"]')):
        texto = texto[2:-2].strip()
    elif texto.startswith("[") and texto.endswith("]"):
        texto = texto[1:-1].strip("'\" ")
    return texto


class AjaxModalFormMixin:
    """Mixin para vistas de formulario embebidas en modales (<dialog>).
    
    Si la solicitud proviene de un modal vía AJAX (X-Requested-With: XMLHttpRequest),
    retorna JSON 400 con errores estructurados al fallar la validación,
    evitando que la página se cierre o navegue a la vista standalone.
    """

    def form_invalid(self, form):
        if self.request.headers.get("x-requested-with") == "XMLHttpRequest":
            errores = {}
            for field, err_list in form.errors.get_json_data().items():
                errores[field] = [e["message"] for e in err_list]
            return JsonResponse({
                "success": False,
                "errors": errores,
                "message": "Por favor corrige los errores señalados en el formulario.",
            }, status=400)
        return super().form_invalid(form)

    def responder_exito(self, redirect_url=None):
        target = redirect_url or (str(self.success_url) if hasattr(self, "success_url") else "/")
        if self.request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({
                "success": True,
                "redirect_url": str(target),
            })
        return redirect(target)


class RedireccionPestañaMovimientosView(LoginRequiredMixin, RedirectView):
    """Redirección canónica a pestañas específicas del Centro Unificado de Movimientos.
    
    Reemplaza vistas redundantes redirect-only unificando la verificación de autenticación
    y permisos de acceso al módulo de movimientos, preservando querystrings.
    """
    vista_destino: str = ""
    permanent = False

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not (request.user.es_administrador or request.user.puede_acceder_modulo_movimientos):
            raise PermissionDenied("No tienes permisos para acceder a esta sección de movimientos.")
        return super().dispatch(request, *args, **kwargs)

    def get_redirect_url(self, *args, **kwargs):
        params = self.request.GET.copy()
        if self.vista_destino:
            params["vista"] = self.vista_destino
        url = reverse("inventario:movimiento-lista")
        query_string = params.urlencode()
        return f"{url}?{query_string}" if query_string else url
