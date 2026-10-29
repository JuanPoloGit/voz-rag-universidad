from django.db import migrations


def poblar_permisos_y_retrocompatibilidad(apps, schema_editor):
    Permiso = apps.get_model("cuentas", "PermisoAudacIA")
    Usuario = apps.get_model("cuentas", "UsuarioAudacIA")
    from cuentas.models import PERMISOS_CATALOGO

    permisos_creados = []
    for data in PERMISOS_CATALOGO:
        perm, _ = Permiso.objects.get_or_create(
            codigo=data["codigo"],
            defaults={
                "modulo": data["modulo"],
                "nombre": data["nombre"],
                "descripcion": data["descripcion"],
                "orden": data["orden"],
            },
        )
        permisos_creados.append(perm)

    # Para retrocompatibilidad: Asignar permisos operativos estándar a usuarios normales existentes
    # (excepto traslado entre almacenes, que quedó reservado a administración)
    permisos_operativos = [p for p in permisos_creados if p.codigo not in ("movimientos.traslado", "catalogo.trasladar_almacen", "infraestructura.gestionar_almacenes", "seguridad.gestionar_usuarios")]

    for usuario in Usuario.objects.filter(rol="USUARIO"):
        usuario.permisos_personalizados.add(*permisos_operativos)


def revertir_poblacion_permisos(apps, schema_editor):
    Permiso = apps.get_model("cuentas", "PermisoAudacIA")
    from cuentas.models import PERMISOS_CATALOGO
    codigos = [d["codigo"] for d in PERMISOS_CATALOGO]
    Permiso.objects.filter(codigo__in=codigos).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("cuentas", "0005_permisoaudacia_usuarioaudacia_permisos_version_and_more"),
    ]

    operations = [
        migrations.RunPython(poblar_permisos_y_retrocompatibilidad, revertir_poblacion_permisos),
    ]
