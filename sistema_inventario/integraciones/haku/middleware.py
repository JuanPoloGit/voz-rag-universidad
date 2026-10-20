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

HAKU_TIMEOUT_SEGUNDOS = getattr(settings, "HAKU_SESSION_TIMEOUT_SECONDS", 7200)  # 2 horas (7200 segundos)


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

            # Regla 12.2: Contexto conversacional efímero en RAM (30 min sin actividad)
            session_key = request.session.session_key
            if not session_key:
                request.session.save()
                session_key = request.session.session_key

            # Retrocompatibilidad: Limpieza de claves residuales de sesión si expira
            ahora = time.time()
            ultimo_acceso_sesion = request.session.get("haku_ultimo_acceso")
            if ultimo_acceso_sesion and (ahora - ultimo_acceso_sesion) > HAKU_TIMEOUT_SEGUNDOS:
                request.session["haku_conversacion"] = []
                request.session["haku_propuesta_activa"] = None
                request.session["haku_ultimo_producto_id"] = None
                request.session["haku_contexto_expirado"] = True

            from .contexto import HakuOperationalContext
            contexto = HakuOperationalContext.obtener(request.user.id, session_key)

            if request.path.startswith("/api/haku/"):
                if contexto is None:
                    almacen_id = getattr(request.user, "almacen_asignado_id", None)
                    HakuOperationalContext.obtener_o_crear(request.user.id, session_key, almacen_id)

        response = self.get_response(request)
        return response
