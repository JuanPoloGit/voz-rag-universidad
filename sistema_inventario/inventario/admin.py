"""Admin de la app inventario — Inventario AudacIA."""

from django.contrib import admin

from .models import (
    Almacen,
    Auditoria,
    Categoria,
    ConteoFisico,
    DevolucionPrestamo,
    Existencia,
    Movimiento,
    Prestamo,
    Producto,
    ProductoUbicacion,
    Ubicacion,
)


@admin.register(Almacen)
class AlmacenAdmin(admin.ModelAdmin):
    list_display = ("nombre", "activo", "eliminado", "fecha_creacion")
    list_filter = ("activo", "eliminado")
    search_fields = ("nombre", "descripcion")


@admin.register(Ubicacion)
class UbicacionAdmin(admin.ModelAdmin):
    list_display = ("nombre", "almacen", "activo", "eliminado")
    list_filter = ("almacen", "activo", "eliminado")
    search_fields = ("nombre", "descripcion")


@admin.register(Categoria)
class CategoriaAdmin(admin.ModelAdmin):
    list_display = ("nombre", "prefijo_codigo", "almacen_principal", "activo", "eliminado")
    list_filter = ("almacen_principal", "activo", "eliminado")
    search_fields = ("nombre", "descripcion")


@admin.register(Producto)
class ProductoAdmin(admin.ModelAdmin):
    list_display = (
        "codigo_interno",
        "nombre",
        "categoria",
        "stock_inicial",
        "stock_minimo",
        "activo",
        "archivado",
    )
    list_filter = ("categoria", "activo", "archivado")
    search_fields = ("codigo_interno", "codigo_barras", "nombre")
    readonly_fields = ("codigo_interno", "stock_inicial", "fecha_creacion", "fecha_modificacion")


@admin.register(Existencia)
class ExistenciaAdmin(admin.ModelAdmin):
    list_display = ("producto", "almacen", "stock_disponible", "stock_en_prestamo")
    list_filter = ("almacen",)
    search_fields = ("producto__nombre", "producto__codigo_interno", "almacen__nombre")


@admin.register(ProductoUbicacion)
class ProductoUbicacionAdmin(admin.ModelAdmin):
    list_display = ("producto", "ubicacion")
    list_filter = ("ubicacion__almacen",)
    search_fields = ("producto__nombre", "producto__codigo_interno", "ubicacion__nombre")


@admin.register(Movimiento)
class MovimientoAdmin(admin.ModelAdmin):
    list_display = (
        "tipo",
        "producto",
        "cantidad",
        "almacen_origen",
        "almacen_destino",
        "usuario",
        "fecha_hora",
        "estado",
    )
    list_filter = ("tipo", "estado", "almacen_origen")
    search_fields = ("producto__nombre", "producto__codigo_interno", "destinatario", "motivo")
    readonly_fields = (
        "clave_idempotencia",
        "stock_disponible_anterior",
        "stock_disponible_posterior",
        "fecha_hora",
    )


@admin.register(Prestamo)
class PrestamoAdmin(admin.ModelAdmin):
    list_display = (
        "producto",
        "almacen",
        "destinatario",
        "cantidad_original",
        "cantidad_pendiente_devolucion",
        "estado",
        "fecha_creacion",
    )
    list_filter = ("estado", "almacen")
    search_fields = ("producto__nombre", "destinatario", "motivo")


@admin.register(DevolucionPrestamo)
class DevolucionPrestamoAdmin(admin.ModelAdmin):
    list_display = ("prestamo", "tipo", "cantidad", "usuario", "fecha")
    list_filter = ("tipo",)
    search_fields = ("prestamo__destinatario", "motivo")


@admin.register(ConteoFisico)
class ConteoFisicoAdmin(admin.ModelAdmin):
    list_display = (
        "producto",
        "almacen",
        "cantidad_contada",
        "stock_sistema",
        "diferencia",
        "confirmado",
        "usuario_conteo",
        "usuario_confirmacion",
    )
    list_filter = ("confirmado", "almacen")
    search_fields = ("producto__nombre", "motivo")


@admin.register(Auditoria)
class AuditoriaAdmin(admin.ModelAdmin):
    list_display = (
        "fecha_hora",
        "usuario",
        "accion",
        "tipo_entidad",
        "id_entidad",
        "resultado",
        "via_haku",
    )
    list_filter = ("accion", "tipo_entidad", "resultado", "via_haku")
    search_fields = ("id_entidad", "motivo", "ip_origen")
    readonly_fields = [f.name for f in Auditoria._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
