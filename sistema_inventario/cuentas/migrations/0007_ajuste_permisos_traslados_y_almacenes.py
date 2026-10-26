from django.db import migrations


def ajustar_catalogo_permisos(apps, schema_editor):
    Permiso = apps.get_model("cuentas", "PermisoAudacIA")
    Usuario = apps.get_model("cuentas", "UsuarioAudacIA")
    from cuentas.models import PERMISOS_CATALOGO

    # 1. Crear o actualizar los permisos vigentes
    permisos_nuevos = {}
    for data in PERMISOS_CATALOGO:
        p, _ = Permiso.objects.update_or_create(
            codigo=data["codigo"],
            defaults={
                "modulo": data["modulo"],
                "nombre": data["nombre"],
                "descripcion": data["descripcion"],
                "orden": data["orden"],
            },
        )
        permisos_nuevos[data["codigo"]] = p

    # 2. Reasignar a usuarios que tenían permisos viejos
    p_traslado_antiguo = Permiso.objects.filter(codigo="movimientos.traslado").first()
    p_reubicar_antiguo = Permiso.objects.filter(codigo="catalogo.reubicar_interno").first()

    p_traslado_int = permisos_nuevos.get("movimientos.traslado_interno")

    for u in Usuario.objects.all():
        tuvo_reubicar = False
        if p_reubicar_antiguo and u.permisos_personalizados.filter(pk=p_reubicar_antiguo.pk).exists():
            tuvo_reubicar = True
        if p_traslado_antiguo and u.permisos_personalizados.filter(pk=p_traslado_antiguo.pk).exists():
            tuvo_reubicar = True

        if tuvo_reubicar and p_traslado_int:
            u.permisos_personalizados.add(p_traslado_int)

    # 3. Eliminar los permisos obsoletos
    codigos_obsoletos = [
        "catalogo.reubicar_interno",
        "catalogo.trasladar_almacen",
        "infraestructura.gestionar_almacenes",
        "movimientos.traslado",
    ]
    Permiso.objects.filter(codigo__in=codigos_obsoletos).delete()


def revertir_ajuste_permisos(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("cuentas", "0006_poblar_permisos_iniciales"),
    ]

    operations = [
        migrations.RunPython(ajustar_catalogo_permisos, revertir_ajuste_permisos),
    ]
