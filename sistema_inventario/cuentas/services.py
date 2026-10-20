"""
Servicios de autenticación, control de intentos fallidos y bloqueo temporal (§5.2).

Implementa:
- Bloqueo por cuenta: 5 intentos fallidos consecutivos bloquean durante 15 minutos.
- Mensaje exacto requerido ante bloqueo: contiene 'cuenta bloqueada'.
- Límite por IP para mitigar ataques distribuidos de fuerza bruta.
- Reinicio de contadores tras autenticación exitosa.
"""

from collections import defaultdict
from datetime import timedelta
from typing import Tuple

from django.conf import settings
from django.utils import timezone

from .models import IntentoFallido, UsuarioAudacIA

LOGIN_MAX_ATTEMPTS = getattr(settings, "LOGIN_MAX_ATTEMPTS", 5)
LOGIN_LOCKOUT_MINUTES = getattr(settings, "LOGIN_LOCKOUT_MINUTES", 15)
LOGIN_IP_MAX_ATTEMPTS = getattr(settings, "LOGIN_IP_MAX_ATTEMPTS", 20)

# Registro en memoria para limitar fuerza bruta por IP con usuarios inexistentes
_registro_ip_fallos = defaultdict(list)


def registrar_intento_fallido(username: str, ip: str = None):
    """Registra un intento de inicio de sesión fallido para la cuenta y la IP."""
    ahora = timezone.now()

    # 1. Registro por IP en memoria
    if ip:
        _registro_ip_fallos[ip].append(ahora)
        # Limpieza de intentos anteriores a la ventana
        ventana = ahora - timedelta(minutes=LOGIN_LOCKOUT_MINUTES)
        _registro_ip_fallos[ip] = [t for t in _registro_ip_fallos[ip] if t >= ventana]

    # 2. Registro por cuenta si el usuario existe
    try:
        usuario = UsuarioAudacIA.objects.get(username__iexact=username)
        IntentoFallido.objects.create(
            usuario=usuario,
            ip=ip,
        )
    except UsuarioAudacIA.DoesNotExist:
        pass


def verificar_bloqueo(username: str, ip: str = None) -> Tuple[bool, str]:
    """Verifica si la cuenta o la IP se encuentran en estado de bloqueo temporal.

    Retorna una tupla (bloqueado: bool, mensaje: str).
    Si la cuenta está bloqueada, el mensaje incluye obligatoriamente 'cuenta bloqueada'.
    """
    ahora = timezone.now()
    ventana = ahora - timedelta(minutes=LOGIN_LOCKOUT_MINUTES)

    # 1. Verificar bloqueo por cuenta si el usuario existe
    try:
        usuario = UsuarioAudacIA.objects.get(username__iexact=username)
        # Contar intentos fallidos dentro de la ventana de bloqueo
        fallos_recientes = IntentoFallido.objects.filter(
            usuario=usuario,
            fecha_intento__gte=ventana,
        ).count()

        if fallos_recientes >= LOGIN_MAX_ATTEMPTS:
            return (
                True,
                "Demasiados intentos fallidos. Esta cuenta bloqueada temporalmente. "
                f"Por favor espere {LOGIN_LOCKOUT_MINUTES} minutos antes de reintentar.",
            )
    except UsuarioAudacIA.DoesNotExist:
        pass

    # 2. Verificar bloqueo por IP
    if ip:
        fallos_ip = [t for t in _registro_ip_fallos.get(ip, []) if t >= ventana]
        if len(fallos_ip) >= LOGIN_IP_MAX_ATTEMPTS:
            return (
                True,
                "Demasiados intentos fallidos desde esta dirección IP. "
                f"Acceso bloqueado temporalmente por {LOGIN_LOCKOUT_MINUTES} minutos.",
            )

    return False, ""


def limpiar_intentos_fallidos(usuario, ip: str = None):
    """Limpia los intentos fallidos al iniciar sesión exitosamente."""
    if usuario and getattr(usuario, "is_authenticated", False):
        IntentoFallido.objects.filter(usuario=usuario).delete()

    if ip and ip in _registro_ip_fallos:
        _registro_ip_fallos.pop(ip, None)
