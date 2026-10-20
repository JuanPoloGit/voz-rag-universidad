"""
Formularios de autenticación y cuentas — Inventario AudacIA.
"""

from django import forms
from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _


class LoginForm(forms.Form):
    """Formulario de inicio de sesión con opción de recordar dispositivo."""

    username = forms.CharField(
        label=_("Nombre de usuario"),
        max_length=150,
        widget=forms.TextInput(attrs={"autofocus": True, "class": "form-control"}),
    )
    password = forms.CharField(
        label=_("Contraseña"),
        widget=forms.PasswordInput(attrs={"class": "form-control"}),
    )
    recordar = forms.BooleanField(
        label=_("Recordar este dispositivo durante 7 días"),
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )


class CambiarPasswordForm(forms.Form):
    """Formulario para que un usuario cambie su propia contraseña."""

    password_actual = forms.CharField(
        label=_("Contraseña actual"),
        widget=forms.PasswordInput(attrs={"class": "form-control"}),
    )
    password_nueva = forms.CharField(
        label=_("Nueva contraseña"),
        widget=forms.PasswordInput(attrs={"class": "form-control"}),
        help_text=_("Mínimo 7 caracteres con al menos una letra y un número."),
    )
    password_confirmacion = forms.CharField(
        label=_("Confirmar nueva contraseña"),
        widget=forms.PasswordInput(attrs={"class": "form-control"}),
    )

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_password_actual(self):
        password_actual = self.cleaned_data.get("password_actual")
        if not self.user.check_password(password_actual):
            raise ValidationError(_("La contraseña actual no es correcta."))
        return password_actual

    def clean(self):
        cleaned_data = super().clean()
        nueva = cleaned_data.get("password_nueva")
        confirmacion = cleaned_data.get("password_confirmacion")

        if nueva and confirmacion:
            if nueva != confirmacion:
                self.add_error("password_confirmacion", _("Las nuevas contraseñas no coinciden."))
            else:
                password_validation.validate_password(nueva, user=self.user)

        return cleaned_data


class UsuarioForm(forms.ModelForm):
    """Formulario para creación y edición de usuarios respetando restricciones de rol."""

    password = forms.CharField(
        label=_("Contraseña"),
        widget=forms.PasswordInput(attrs={"class": "form-control"}),
        required=False,
        help_text=_("Mínimo 7 caracteres con al menos una letra y un número. Dejar en blanco para no modificar."),
    )

    class Meta:
        from .models import UsuarioAudacIA
        model = UsuarioAudacIA
        fields = ["username", "nombre_completo", "rol", "almacen_asignado", "is_active"]
        widgets = {
            "username": forms.TextInput(attrs={"class": "form-control"}),
            "nombre_completo": forms.TextInput(attrs={"class": "form-control"}),
            "rol": forms.Select(attrs={"class": "form-select"}),
            "almacen_asignado": forms.Select(attrs={"class": "form-select"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, request_user=None, **kwargs):
        from .models import UsuarioAudacIA
        self.request_user = request_user
        super().__init__(*args, **kwargs)

        if not self.instance.pk:
            self.fields["password"].required = True

        # Restricción §6.2: Administradores solo pueden crear o gestionar usuarios normales (Rol.USUARIO)
        if self.request_user and not self.request_user.es_superusuario_inventario:
            self.fields["rol"].choices = [
                (UsuarioAudacIA.Rol.USUARIO, _("Usuario")),
            ]

    def clean_rol(self):
        from .models import UsuarioAudacIA
        rol = self.cleaned_data.get("rol")
        # Si quien ejecuta no es superusuario, no puede asignar otro rol que no sea USUARIO
        if self.request_user and not self.request_user.es_superusuario_inventario:
            if rol != UsuarioAudacIA.Rol.USUARIO:
                raise ValidationError(_("Los administradores solo pueden gestionar usuarios con rol Usuario."))
        return rol

    def clean(self):
        from .models import UsuarioAudacIA
        cleaned_data = super().clean()
        rol = cleaned_data.get("rol")
        almacen = cleaned_data.get("almacen_asignado")
        password = cleaned_data.get("password")

        if rol == UsuarioAudacIA.Rol.USUARIO and not almacen:
            self.add_error("almacen_asignado", _("El almacén asignado es obligatorio para usuarios normales."))

        if rol in (UsuarioAudacIA.Rol.ADMINISTRADOR, UsuarioAudacIA.Rol.SUPERUSUARIO):
            cleaned_data["almacen_asignado"] = None

        if password:
            password_validation.validate_password(password)

        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        password = self.cleaned_data.get("password")
        if password:
            user.set_password(password)
        if commit:
            user.save()
        return user

