"""
Señales para garantizar la inmutabilidad de los registros de auditoría (§10).

Los registros de auditoría no pueden modificarse con save() ni eliminarse con delete().
"""

from django.db.models.signals import pre_delete, pre_save
from django.dispatch import receiver
from django.utils.translation import gettext_lazy as _

from .models import Auditoria


@receiver(pre_save, sender=Auditoria)
def impedir_modificacion_auditoria(sender, instance, **kwargs):
    """Bloquea cualquier intento de actualizar un registro de auditoría existente."""
    if instance.pk and not instance._state.adding:
        if Auditoria.objects.filter(pk=instance.pk).exists():
            raise PermissionError(
                _("Los registros de auditoría son inmutables y no pueden ser modificados.")
            )


@receiver(pre_delete, sender=Auditoria)
def impedir_eliminacion_auditoria(sender, instance, **kwargs):
    """Bloquea cualquier intento de eliminar un registro de auditoría."""
    raise PermissionError(
        _("Los registros de auditoría son inmutables y nunca pueden ser eliminados.")
    )
