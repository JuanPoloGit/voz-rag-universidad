"""Señales para invalidación del caché de alertas de stock."""

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from inventario.models import AlertaStockLeida, ConteoFisico, Existencia, Prestamo
from inventario.servicios.alertas import invalidar_cache_alertas


@receiver(post_save, sender=Existencia)
@receiver(post_delete, sender=Existencia)
@receiver(post_save, sender=ConteoFisico)
@receiver(post_delete, sender=ConteoFisico)
@receiver(post_save, sender=AlertaStockLeida)
@receiver(post_delete, sender=AlertaStockLeida)
@receiver(post_save, sender=Prestamo)
@receiver(post_delete, sender=Prestamo)
def invalidar_alertas_al_modificar_modelos(sender, **kwargs):
    invalidar_cache_alertas()
