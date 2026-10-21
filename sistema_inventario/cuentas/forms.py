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
        fields = [
            "username",
            "nombre_completo",
            "rol",
            "almacen_asignado",
            "almacenes_asignados",
            "acceso_todos_almacenes",
            "is_active",
        ]
        widgets = {
            "username": forms.TextInput(attrs={"class": "form-control"}),
            "nombre_completo": forms.TextInput(attrs={"class": "form-control"}),
            "rol": forms.Select(attrs={"class": "form-select"}),
            "almacen_asignado": forms.Select(attrs={"class": "form-select"}),
            "almacenes_asignados": forms.SelectMultiple(attrs={"class": "form-select", "size": "4"}),
            "acceso_todos_almacenes": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, request_user=None, **kwargs):
        from .models import UsuarioAudacIA
        self.request_user = request_user
        super().__init__(*args, **kwargs)

        if not self.instance.pk:
            self.fields["password"].required = True

        self.fields["almacenes_asignados"].required = False
        self.fields["almacen_asignado"].required = False

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
        almacen_principal = cleaned_data.get("almacen_asignado")
        almacenes_multi = cleaned_data.get("almacenes_asignados")
        acceso_todos = cleaned_data.get("acceso_todos_almacenes")
        password = cleaned_data.get("password")

        if rol == UsuarioAudacIA.Rol.USUARIO:
            # Opción A vs Opción B: Exclusión mutua
            tiene_especificos = bool(almacen_principal or (almacenes_multi and len(almacenes_multi) > 0))
            if acceso_todos and tiene_especificos:
                self.add_error(
                    "acceso_todos_almacenes",
                    _("No puedes seleccionar simultáneamente 'Todos los almacenes' y almacenes específicos. Elige una sola opción."),
                )

            # Debe tener Opción A (uno o varios almacenes) u Opción B (acceso_todos_almacenes)
            if not acceso_todos and not tiene_especificos:
                self.add_error(
                    "almacen_asignado",
                    _("Debes elegir entre asignar almacenes específicos o marcar 'Todos los almacenes'."),
                )

            # Si seleccionó 'Todos los almacenes', limpiar asignaciones individuales
            if acceso_todos:
                cleaned_data["almacen_asignado"] = None
                cleaned_data["almacenes_asignados"] = []
            else:
                cleaned_data["acceso_todos_almacenes"] = False
                # Si se seleccionó multialmacén pero no principal, asignar el primero como principal para compatibilidad
                if not almacen_principal and almacenes_multi and len(almacenes_multi) > 0:
                    cleaned_data["almacen_asignado"] = almacenes_multi[0]

        if rol in (UsuarioAudacIA.Rol.ADMINISTRADOR, UsuarioAudacIA.Rol.SUPERUSUARIO):
            cleaned_data["almacen_asignado"] = None
            cleaned_data["almacenes_asignados"] = []
            cleaned_data["acceso_todos_almacenes"] = False

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
            self.save_m2m()
        return user

