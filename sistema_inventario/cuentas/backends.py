"""
Backend de autenticación personalizado para Inventario AudacIA.

Garantiza que usuarios inactivos o eliminados lógicamente no puedan autenticarse.
"""

from django.contrib.auth.backends import ModelBackend


class UsuarioAudacIABackend(ModelBackend):
    """Backend de autenticación que valida cuentas activas y no eliminadas lógicamente."""

    def user_can_authenticate(self, user):
        can_auth = super().user_can_authenticate(user)
        if not can_auth:
            return False
        return not getattr(user, "eliminado_logicamente", False)
