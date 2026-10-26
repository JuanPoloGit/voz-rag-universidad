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
    permisos_personalizados = forms.ModelMultipleChoiceField(
        queryset=None,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "form-check-input"}),
        required=False,
        label=_("Permisos Granulares"),
    )

    class Meta:
        from .models import UsuarioAudacIA
        model = UsuarioAudacIA
        fields = ["username", "nombre_completo", "rol", "almacenes_asignados", "permisos_personalizados", "is_active"]
        widgets = {
            "username": forms.TextInput(attrs={"class": "form-control"}),
            "nombre_completo": forms.TextInput(attrs={"class": "form-control"}),
            "rol": forms.Select(attrs={"class": "form-select"}),
            "almacenes_asignados": forms.CheckboxSelectMultiple(attrs={"class": "form-check-input"}),
            "permisos_personalizados": forms.CheckboxSelectMultiple(attrs={"class": "form-check-input"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, request_user=None, **kwargs):
        from .models import UsuarioAudacIA, PermisoAudacIA, PRESETS_PERMISOS
        from inventario.models import Almacen
        self.request_user = request_user
        super().__init__(*args, **kwargs)

        # Configuración del campo de permisos granulares
        perms_qs = PermisoAudacIA.objects.all().order_by("modulo", "orden")
        if "permisos_personalizados" in self.fields:
            self.fields["permisos_personalizados"].queryset = perms_qs
            self.fields["permisos_personalizados"].required = False
            if self.instance.pk:
                pks_actuales = list(self.instance.permisos_personalizados.values_list("pk", flat=True))
                self.fields["permisos_personalizados"].initial = pks_actuales
                self.initial["permisos_personalizados"] = pks_actuales
            elif "permisos_personalizados" not in self.initial:
                # Al crear nuevo usuario, preseleccionar el preset 'OPERADOR_INTEGRAL'
                preset_cods = PRESETS_PERMISOS.get("OPERADOR_INTEGRAL", {}).get("codigos", [])
                pks_default = list(perms_qs.filter(codigo__in=preset_cods).values_list("pk", flat=True))
                self.fields["permisos_personalizados"].initial = pks_default
                self.initial["permisos_personalizados"] = pks_default

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
                self.initial["almacenes_asignados"] = list(ids_iniciales)

        if not self.instance.pk:
            self.fields["password"].required = True
        else:
            self.fields["password"].label = _("Restablecer Contraseña (opcional)")
            self.fields["password"].help_text = _("Mínimo 7 caracteres con al menos una letra y un número. Dejar en blanco para conservar la contraseña actual.")
            # Restricción: El nombre de usuario solo puede ser modificado por el superusuario
            if self.request_user and not self.request_user.es_superusuario_inventario:
                self.fields["username"].disabled = True
                self.fields["username"].help_text = _("El nombre de usuario solo puede ser modificado por el superusuario.")

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

    def clean_username(self):
        username = self.cleaned_data.get("username")
        if self.instance.pk and self.request_user and not self.request_user.es_superusuario_inventario:
            return self.instance.username
        return username

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

                # Validar que no se desasignen almacenes huérfanos con existencias o productos
                if self.instance.pk:
                    from inventario.models import Producto, Existencia
                    alms_previos = set(self.instance.get_almacenes_asignados())
                    alms_nuevos = set(almacenes)
                    alms_removidos = alms_previos - alms_nuevos

                    for alm in alms_removidos:
                        from django.db.models import Q
                        tiene_prods = (
                            Producto.objects.filter(categoria__almacen_principal=alm, archivado=False).exists()
                            or Existencia.objects.filter(almacen=alm).filter(Q(stock_disponible__gt=0) | Q(stock_en_prestamo__gt=0)).exists()
                        )
                        if tiene_prods:
                            otros_con_acceso = (
                                UsuarioAudacIA.objects.filter(
                                    is_active=True,
                                    rol=UsuarioAudacIA.Rol.USUARIO,
                                    almacenes_asignados=alm,
                                )
                                .exclude(pk=self.instance.pk)
                                .exists()
                            )
                            if not otros_con_acceso:
                                self.add_error(
                                    "almacenes_asignados",
                                    _(
                                        f"No puedes retirar el almacén '{alm.nombre}' porque no hay ningún otro usuario "
                                        f"asignado a él y tiene productos registrados."
                                    ),
                                )
        if rol != UsuarioAudacIA.Rol.USUARIO:
            cleaned_data["almacen_asignado"] = None
            cleaned_data["almacenes_asignados"] = []
            cleaned_data["permisos_personalizados"] = []

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

        permisos = list(self.cleaned_data.get("permisos_personalizados") or []) if rol == user.Rol.USUARIO else []

        # Detección de cambios de seguridad para versión de permisos y purga de sesiones
        es_edicion = bool(user.pk)
        rol_cambio = False
        desactivado = False
        permisos_cambiaron = False

        if es_edicion:
            from .models import UsuarioAudacIA
            anterior = UsuarioAudacIA.objects.filter(pk=user.pk).first()
            if anterior:
                if anterior.rol != rol:
                    rol_cambio = True
                if anterior.is_active and not user.is_active:
                    desactivado = True
                if rol == user.Rol.USUARIO:
                    pks_anteriores = set(anterior.permisos_personalizados.values_list("pk", flat=True))
                    pks_nuevos = set(p.pk if hasattr(p, "pk") else p for p in permisos)
                    if pks_anteriores != pks_nuevos:
                        permisos_cambiaron = True

        if permisos_cambiaron or rol_cambio:
            user.permisos_version = (user.permisos_version or 1) + 1

        if commit:
            user.save()
            if rol == user.Rol.USUARIO and almacenes:
                user.almacenes_asignados.set(almacenes)
            else:
                user.almacenes_asignados.clear()

            if rol == user.Rol.USUARIO:
                user.permisos_personalizados.set(permisos)
            else:
                user.permisos_personalizados.clear()

            # Purgar sesiones activas en la BD si el usuario fue desactivado o cambió de rol
            if es_edicion and (desactivado or rol_cambio):
                from .services import purgar_sesiones_activas
                purgar_sesiones_activas(user)

        return user


class PerfilUsuarioForm(forms.ModelForm):
    """Formulario para que Administradores y Superusuarios gestionen su propio perfil."""

    password_actual = forms.CharField(
        label=_("Contraseña actual"),
        widget=forms.PasswordInput(attrs={"class": "form-control"}),
        required=False,
        help_text=_("Requerida únicamente si deseas cambiar tu contraseña."),
    )
    password_nueva = forms.CharField(
        label=_("Nueva contraseña"),
        widget=forms.PasswordInput(attrs={"class": "form-control"}),
        required=False,
    )
    password_confirmacion = forms.CharField(
        label=_("Confirmar nueva contraseña"),
        widget=forms.PasswordInput(attrs={"class": "form-control"}),
        required=False,
    )

    class Meta:
        from .models import UsuarioAudacIA
        model = UsuarioAudacIA
        fields = ["nombre_completo"]
        widgets = {
            "nombre_completo": forms.TextInput(attrs={"class": "form-control"}),
        }

    def clean(self):
        cleaned_data = super().clean()
        actual = cleaned_data.get("password_actual")
        nueva = cleaned_data.get("password_nueva")
        confirmacion = cleaned_data.get("password_confirmacion")

        if nueva or confirmacion or actual:
            if not actual:
                self.add_error("password_actual", _("Debes ingresar tu contraseña actual para establecer una nueva."))
            elif not self.instance.check_password(actual):
                self.add_error("password_actual", _("La contraseña actual no es correcta."))

            if not nueva:
                self.add_error("password_nueva", _("Ingresa la nueva contraseña."))
            elif nueva != confirmacion:
                self.add_error("password_confirmacion", _("Las nuevas contraseñas no coinciden."))
            else:
                password_validation.validate_password(nueva, user=self.instance)

        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        nueva = self.cleaned_data.get("password_nueva")
        if nueva:
            user.set_password(nueva)
        if commit:
            user.save()
        return user


