"""
Modelos de cuentas de usuario — Inventario AudacIA.

Tres roles cerrados (Superusuario, Administrador, Usuario) con permisos
definidos por la especificación. El Superusuario es único e ineliminable.

Datos mínimos obligatorios: nombre completo, nombre de usuario y contraseña.
No se requieren correo, teléfono ni documento de identidad (sección 6.1).
"""

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.translation import gettext_lazy as _

from .managers import UsuarioAudacIAManager


class UsuarioAudacIA(AbstractUser):
    """Usuario del sistema de inventario.

    Extiende AbstractUser de Django para:
    - Eliminar la obligatoriedad del email.
    - Añadir rol, almacén asignado y eliminación lógica.
    - Soportar bloqueo temporal por intentos fallidos.
    """

    class Rol(models.TextChoices):
        SUPERUSUARIO = "SUPERUSUARIO", _("Superusuario")
        ADMINISTRADOR = "ADMINISTRADOR", _("Administrador")
        USUARIO = "USUARIO", _("Usuario")

    # --- Campos del perfil ---

    nombre_completo = models.CharField(
        _("nombre completo"),
        max_length=255,
        help_text=_("Nombre completo del usuario."),
    )

    rol = models.CharField(
        _("rol"),
        max_length=20,
        choices=Rol.choices,
        default=Rol.USUARIO,
        help_text=_("Define los permisos del usuario en el sistema."),
    )

    # El almacén se asigna obligatoriamente a los usuarios con rol USUARIO (1 o más almacenes).
    # Administradores y Superusuario tienen acceso a todos los almacenes.
    almacen_asignado = models.ForeignKey(
        "inventario.Almacen",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="usuarios_asignados",
        verbose_name=_("almacén principal asignado"),
        help_text=_(
            "Almacén principal al que pertenece este usuario. "
            "Obligatorio para rol Usuario; null para Administrador y Superusuario."
        ),
    )
    almacenes_asignados = models.ManyToManyField(
        "inventario.Almacen",
        blank=True,
        related_name="usuarios_con_acceso",
        verbose_name=_("almacenes asignados"),
        help_text=_(
            "Almacenes a los que tiene acceso este usuario. Puede tener uno o varios asignados."
        ),
    )

    # --- Estados de cuenta (sección 6.3) ---

    eliminado_logicamente = models.BooleanField(
        _("eliminado lógicamente"),
        default=False,
        help_text=_(
            "Eliminación lógica. Los registros históricos se conservan."
        ),
    )

    fecha_creacion = models.DateTimeField(_("fecha de creación"), auto_now_add=True)
    fecha_modificacion = models.DateTimeField(_("última modificación"), auto_now=True)

    # --- Campos que NO usamos pero heredamos de AbstractUser ---

    # email no es obligatorio en este sistema.
    email = models.EmailField(_("email"), blank=True, default="")

    # Eliminamos first_name y last_name a favor de nombre_completo.
    first_name = None  # type: ignore[assignment]
    last_name = None  # type: ignore[assignment]

    # --- Manager ---

    objects = UsuarioAudacIAManager()

    USERNAME_FIELD = "username"
    REQUIRED_FIELDS = ["nombre_completo"]

    class Meta:
        verbose_name = _("usuario")
        verbose_name_plural = _("usuarios")
        ordering = ["username"]

    def __str__(self):
        return f"{self.username} ({self.get_rol_display()})"

    @property
    def es_superusuario_inventario(self):
        """True si tiene el rol Superusuario del inventario."""
        return self.rol == self.Rol.SUPERUSUARIO

    @property
    def es_administrador(self):
        """True si es Administrador o Superusuario."""
        return self.rol in (self.Rol.SUPERUSUARIO, self.Rol.ADMINISTRADOR)

    @property
    def es_usuario_normal(self):
        return self.rol == self.Rol.USUARIO

    def get_almacenes_asignados(self):
        """Retorna un QuerySet de almacenes a los que tiene acceso el usuario."""
        from inventario.models import Almacen
        if self.es_administrador:
            return Almacen.objects.filter(activo=True, eliminado=False).order_by("nombre")

        ids = set()
        if self.almacen_asignado_id:
            ids.add(self.almacen_asignado_id)
        if self.pk:
            for alm_id in self.almacenes_asignados.filter(activo=True, eliminado=False).values_list("id", flat=True):
                ids.add(alm_id)

        if not ids:
            return Almacen.objects.none()
        return Almacen.objects.filter(id__in=ids, activo=True, eliminado=False).order_by("nombre")

    def tiene_acceso_almacen(self, almacen):
        """¿Puede este usuario operar en el almacén dado?

        - Superusuario y Administrador: todos los almacenes.
        - Usuario: almacén principal asignado o cualquiera en sus almacenes asignados.
        """
        if self.es_administrador:
            return True
        if not almacen:
            return False
        alm_id = getattr(almacen, "id", almacen)
        if self.almacen_asignado_id == alm_id:
            return True
        if self.pk:
            return self.almacenes_asignados.filter(id=alm_id, activo=True, eliminado=False).exists()
        return False


class IntentoFallido(models.Model):
    """Registro de intentos fallidos de login para bloqueo temporal.

    Después de LOGIN_MAX_ATTEMPTS intentos fallidos consecutivos, la cuenta
    se bloquea durante LOGIN_LOCKOUT_MINUTES minutos (sección 6.4).
    """

    usuario = models.ForeignKey(
        UsuarioAudacIA,
        on_delete=models.CASCADE,
        related_name="intentos_fallidos",
        verbose_name=_("usuario"),
    )
    fecha_intento = models.DateTimeField(_("fecha del intento"), auto_now_add=True)
    ip = models.GenericIPAddressField(
        _("dirección IP"),
        null=True,
        blank=True,
    )

    class Meta:
        verbose_name = _("intento fallido")
        verbose_name_plural = _("intentos fallidos")
        ordering = ["-fecha_intento"]
        indexes = [
            models.Index(fields=["usuario", "-fecha_intento"]),
        ]

    def __str__(self):
        return f"Intento fallido de {self.usuario} el {self.fecha_intento}"
