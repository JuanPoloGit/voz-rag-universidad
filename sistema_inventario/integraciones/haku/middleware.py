"""Middleware para Haku Inventario — Control de sesiones y contexto.

Reglas clave:
1. Inactivación / eliminación lógica inmediata:
   Si el usuario fue desactivado o eliminado lógicamente, su sesión se revoca
   de inmediato en la siguiente petición (sección 6.3).
2. Timeout conversacional de 30 minutos:
   El contexto conversacional de Haku (historial de diálogo y propuestas pendientes)
   caduca tras 30 minutos de inactividad, sin cerrar la sesión web de 7 días
   (sección 12.2).
"""

import time
from django.conf import settings
from django.contrib.auth import logout
from django.http import JsonResponse
from django.shortcuts import redirect

HAKU_TIMEOUT_SEGUNDOS = getattr(settings, "HAKU_SESSION_TIMEOUT_SECONDS", 1800)  # 30 minutos


class HakuSessionMiddleware:
    """Gestiona el ciclo de vida de la sesión conversacional de Haku y revocación."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            # Regla 6.3: Desactivar o eliminar cuenta revoca la sesión de inmediato
            if not request.user.is_active or getattr(request.user, "eliminado_logicamente", False):
                logout(request)
                if request.path.startswith("/api/"):
                    return JsonResponse(
                        {"error": "Cuenta desactivada o eliminada.", "sesion_activa": False},
                        status=401,
                    )
                return redirect("cuentas:login" if "cuentas" in settings.INSTALLED_APPS else "/")

            # Regla 12.2: Timeout de 30 minutos para el contexto de Haku
            ahora = time.time()
            ultimo_acceso = request.session.get("haku_ultimo_acceso")

            if ultimo_acceso is not None:
                diferencia = ahora - ultimo_acceso
                if diferencia > HAKU_TIMEOUT_SEGUNDOS:
                    # El contexto de Haku ha caducado
                    request.session["haku_conversacion"] = []
                    request.session["haku_propuesta_activa"] = None
                    request.session["haku_contexto_expirado"] = True
                else:
                    request.session["haku_contexto_expirado"] = False
            else:
                request.session["haku_contexto_expirado"] = False

            # Si la petición actual es hacia la API de Haku, refrescar la actividad
            if request.path.startswith("/api/haku/"):
                request.session["haku_ultimo_acceso"] = ahora

        response = self.get_response(request)
        return response
