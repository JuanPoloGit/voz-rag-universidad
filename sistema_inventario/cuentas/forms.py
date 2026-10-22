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
        fields = ["username", "nombre_completo", "rol", "almacenes_asignados", "is_active"]
        widgets = {
            "username": forms.TextInput(attrs={"class": "form-control"}),
            "nombre_completo": forms.TextInput(attrs={"class": "form-control"}),
            "rol": forms.Select(attrs={"class": "form-select"}),
            "almacenes_asignados": forms.CheckboxSelectMultiple(attrs={"class": "form-check-input"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, request_user=None, **kwargs):
        from .models import UsuarioAudacIA
        from inventario.models import Almacen
        self.request_user = request_user
        super().__init__(*args, **kwargs)

        alms_qs = Almacen.objects.filter(activo=True, eliminado=False).order_by("nombre")
        if "almacenes_asignados" in self.fields:
            self.fields["almacenes_asignados"].queryset = alms_qs
            self.fields["almacenes_asignados"].required = False
            self.fields["almacenes_asignados"].label = _("Almacenes Asignados *")
            if self.instance.pk:
                ids_iniciales = set(self.instance.almacenes_asignados.values_list("id", flat=True))
                if self.instance.almacen_asignado_id:
                    ids_iniciales.add(self.instance.almacen_asignado_id)
                self.fields["almacenes_asignados"].initial = list(ids_iniciales)

        if not self.instance.pk:
            self.fields["password"].required = True

        # Restricción: No se puede crear ni asignar el rol de SUPERUSUARIO desde la interfaz.
        # Solo existe un único superusuario en el sistema (definido por comando / consola).
        if self.instance.pk and self.instance.es_superusuario_inventario:
            self.fields["rol"].choices = [
                (UsuarioAudacIA.Rol.SUPERUSUARIO, _("Superusuario")),
            ]
            self.fields["rol"].disabled = True
        elif self.request_user and self.request_user.es_superusuario_inventario:
            self.fields["rol"].choices = [
                (UsuarioAudacIA.Rol.ADMINISTRADOR, _("Administrador")),
                (UsuarioAudacIA.Rol.USUARIO, _("Usuario")),
            ]
        else:
            # Administradores solo pueden crear o gestionar usuarios normales (Rol.USUARIO)
            self.fields["rol"].choices = [
                (UsuarioAudacIA.Rol.USUARIO, _("Usuario")),
            ]

    def clean_rol(self):
        from .models import UsuarioAudacIA
        rol = self.cleaned_data.get("rol")
        # El rol SUPERUSUARIO no puede ser creado ni asignado a usuarios nuevos o existentes
        if rol == UsuarioAudacIA.Rol.SUPERUSUARIO:
            if not self.instance.pk or not self.instance.es_superusuario_inventario:
                raise ValidationError(_("No es posible crear más superusuarios. El sistema solo admite un único superusuario."))
        # Si quien ejecuta no es superusuario, no puede asignar otro rol que no sea USUARIO
        if self.request_user and not self.request_user.es_superusuario_inventario:
            if rol != UsuarioAudacIA.Rol.USUARIO:
                raise ValidationError(_("Los administradores solo pueden gestionar usuarios con rol Usuario."))
        return rol

    def clean(self):
        from .models import UsuarioAudacIA
        cleaned_data = super().clean()
        rol = cleaned_data.get("rol")
        password = cleaned_data.get("password")
        almacenes = list(cleaned_data.get("almacenes_asignados") or [])
        # Compatibilidad con clientes o tests que envíen almacen_asignado en el payload POST
        if not almacenes and self.data.get("almacen_asignado"):
            from inventario.models import Almacen
            alm_val = self.data.get("almacen_asignado")
            alm_obj = Almacen.objects.filter(pk=alm_val).first() if str(alm_val).isdigit() else None
            if alm_obj:
                almacenes = [alm_obj]
                cleaned_data["almacenes_asignados"] = almacenes

        if rol == UsuarioAudacIA.Rol.USUARIO:
            if not almacenes or len(almacenes) == 0:
                self.add_error("almacenes_asignados", _("Debes seleccionar obligatoriamente al menos un almacén para usuarios operativos."))
            else:
                cleaned_data["almacen_asignado"] = almacenes[0]
        else:
            cleaned_data["almacen_asignado"] = None
            cleaned_data["almacenes_asignados"] = []

        if password:
            password_validation.validate_password(password)

        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        password = self.cleaned_data.get("password")
        if password:
            user.set_password(password)

        rol = self.cleaned_data.get("rol")
        almacenes = list(self.cleaned_data.get("almacenes_asignados") or [])

        if rol == user.Rol.USUARIO and almacenes:
            user.almacen_asignado = almacenes[0]
        else:
            user.almacen_asignado = None

        if commit:
            user.save()
            if rol == user.Rol.USUARIO and almacenes:
                user.almacenes_asignados.set(almacenes)
            else:
                user.almacenes_asignados.clear()
        return user

