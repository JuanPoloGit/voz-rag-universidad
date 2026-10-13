"""
Validadores de contraseña personalizados para Inventario AudacIA.

Requisito de la especificación: mínimo 7 caracteres, al menos una
letra y un número. El mínimo de longitud ya lo cubre
MinimumLengthValidator de Django; aquí se añade la regla de composición.
"""

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _


class LetraYNumeroValidator:
    """Exige al menos una letra y un número en la contraseña."""

    def validate(self, password, user=None):  # noqa: ARG002
        tiene_letra = any(c.isalpha() for c in password)
        tiene_numero = any(c.isdigit() for c in password)
        if not tiene_letra or not tiene_numero:
            raise ValidationError(
                _("La contraseña debe contener al menos una letra y un número."),
                code="password_sin_letra_o_numero",
            )

    def get_help_text(self):
        return _("La contraseña debe contener al menos una letra y un número.")
