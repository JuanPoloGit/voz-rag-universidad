"""
Señales para el modelo UsuarioAudacIA.

Implementa las invariantes de negocio de la especificación (§6.2):
1. Unicidad del Superusuario: solo puede existir uno en el sistema.
2. Ineliminabilidad del Superusuario: no puede borrarse con delete().
3. Obligatoriedad de almacén: obligatorio para Rol.USUARIO; nulo para Administrador y Superusuario.
4. Coherencia de flags: is_superuser e is_staff sincronizados según el rol.
"""

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.signals import post_save, pre_delete, pre_save
from django.dispatch import receiver
from django.utils.translation import gettext_lazy as _

from .models import PermisoAudacIA, PRESETS_PERMISOS, UsuarioAudacIA


@receiver(pre_save, sender=UsuarioAudacIA)
def validar_invariantes_usuario(sender, instance, **kwargs):
    """Valida reglas de negocio y sincroniza flags antes de guardar."""
    # 1. Sincronización de flags y almacén según rol
    if instance.rol == UsuarioAudacIA.Rol.SUPERUSUARIO:
        instance.is_superuser = True
        instance.is_staff = True
        instance.almacen_asignado = None
    elif instance.rol == UsuarioAudacIA.Rol.ADMINISTRADOR:
        instance.is_superuser = False
        instance.is_staff = True
        instance.almacen_asignado = None
    elif instance.rol == UsuarioAudacIA.Rol.USUARIO:
        instance.is_superuser = False
        instance.is_staff = False
        if not instance.almacen_asignado_id:
            if instance.pk and instance.almacenes_asignados.exists():
                instance.almacen_asignado = instance.almacenes_asignados.first()
            else:
                raise ValidationError(
                    _("Los usuarios con rol Usuario deben tener obligatoriamente al menos un almacén asignado.")
                )

    # 2. Verificar regla de superusuario único
    if instance.rol == UsuarioAudacIA.Rol.SUPERUSUARIO or instance.is_superuser:
        existentes = UsuarioAudacIA.objects.filter(
            models.Q(rol=UsuarioAudacIA.Rol.SUPERUSUARIO) | models.Q(is_superuser=True)
        )
        if instance.pk:
            existentes = existentes.exclude(pk=instance.pk)

        if existentes.exists():
            raise ValidationError(
                _("Ya existe un superusuario en el sistema. Solo se permite un único superusuario.")
            )


@receiver(pre_delete, sender=UsuarioAudacIA)
def impedir_borrado_superusuario(sender, instance, **kwargs):
    """Impide la eliminación física del superusuario (§6.2)."""
    if instance.rol == UsuarioAudacIA.Rol.SUPERUSUARIO or instance.is_superuser:
        raise ValidationError(
            _("El superusuario es ineliminable.")
        )


@receiver(post_save, sender=UsuarioAudacIA)
def asignar_permisos_por_defecto_usuario(sender, instance, created, **kwargs):
    """
    Garantiza compatibilidad retroactiva: usuarios operativos recién creados programáticamente
    reciben los permisos operativos base (preset 'OPERADOR_INTEGRAL') a menos que ya
    hayan sido asignados explícitamente.
    """
    if created and instance.rol == UsuarioAudacIA.Rol.USUARIO:
        # Solo asignar si no tiene permisos configurados previamente
        if not instance.permisos_personalizados.exists():
            preset_cods = PRESETS_PERMISOS.get("OPERADOR_INTEGRAL", {}).get("codigos", [])
            perms = list(PermisoAudacIA.objects.filter(codigo__in=preset_cods))
            if perms:
                instance.permisos_personalizados.set(perms)

