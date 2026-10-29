from django.db import migrations


def agregar_permiso_revertir(apps, schema_editor):
    Permiso = apps.get_model("cuentas", "PermisoAudacIA")
    Permiso.objects.update_or_create(
        codigo="movimientos.revertir",
        defaults={
            "modulo": "MOVIMIENTOS",
            "nombre": "Revertir movimientos",
            "descripcion": "Ejecutar movimientos de reversión compensatoria de operaciones previas.",
            "orden": 9,
        },
    )


def eliminar_permiso_revertir(apps, schema_editor):
    Permiso = apps.get_model("cuentas", "PermisoAudacIA")
    Permiso.objects.filter(codigo="movimientos.revertir").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("cuentas", "0008_eliminar_permiso_seguridad_gestionar_usuarios"),
    ]

    operations = [
        migrations.RunPython(agregar_permiso_revertir, eliminar_permiso_revertir),
    ]
