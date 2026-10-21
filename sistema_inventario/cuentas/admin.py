from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import IntentoFallido, UsuarioAudacIA


@admin.register(UsuarioAudacIA)
class UsuarioAudacIAAdmin(UserAdmin):
    """Admin personalizado: muestra los campos del modelo propio."""

    list_display = ("username", "nombre_completo", "rol", "almacen_asignado", "acceso_todos_almacenes", "is_active")
    list_filter = ("rol", "acceso_todos_almacenes", "is_active", "eliminado_logicamente")
    search_fields = ("username", "nombre_completo")
    ordering = ("username",)

    fieldsets = (
        (None, {"fields": ("username", "password")}),
        ("Información personal", {"fields": ("nombre_completo", "email")}),
        ("Rol y almacenes", {"fields": ("rol", "almacen_asignado", "almacenes_asignados", "acceso_todos_almacenes")}),
        ("Permisos", {"fields": ("is_active", "is_staff", "is_superuser",
                                  "eliminado_logicamente", "groups", "user_permissions")}),
        ("Fechas", {"fields": ("last_login",)}),
    )

    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("username", "nombre_completo", "rol",
                       "almacen_asignado", "almacenes_asignados", "acceso_todos_almacenes", "password1", "password2"),
        }),
    )


@admin.register(IntentoFallido)
class IntentoFallidoAdmin(admin.ModelAdmin):
    list_display = ("usuario", "fecha_intento", "ip")
    list_filter = ("fecha_intento",)
    readonly_fields = ("usuario", "fecha_intento", "ip")
