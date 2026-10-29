from django.db import migrations


def eliminar_permiso_gestionar_usuarios(apps, schema_editor):
    Permiso = apps.get_model("cuentas", "PermisoAudacIA")
    Permiso.objects.filter(codigo="seguridad.gestionar_usuarios").delete()


def revertir_eliminacion(apps, schema_editor):
    Permiso = apps.get_model("cuentas", "PermisoAudacIA")
    Permiso.objects.get_or_create(
        codigo="seguridad.gestionar_usuarios",
        defaults={
            "modulo": "SEGURIDAD",
            "nombre": "Gestionar usuarios y permisos",
            "descripcion": "Crear y administrar usuarios subordinados y sus facultades.",
            "orden": 1,
        },
    )


class Migration(migrations.Migration):

    dependencies = [
        ("cuentas", "0007_ajuste_permisos_traslados_y_almacenes"),
    ]

    operations = [
        migrations.RunPython(eliminar_permiso_gestionar_usuarios, revertir_eliminacion),
    ]
