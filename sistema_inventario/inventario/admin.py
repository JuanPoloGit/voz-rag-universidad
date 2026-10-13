"""Admin de la app inventario — Inventario AudacIA."""

from django.contrib import admin

from .models import (
    Almacen,
    Auditoria,
    Categoria,
    ConteoFisico,
    DevolucionPrestamo,
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
    list_display = ("nombre", "almacen", "activa", "eliminada")
    list_filter = ("almacen", "activa", "eliminada")
    search_fields = ("nombre", "descripcion")


@admin.register(Categoria)
class CategoriaAdmin(admin.ModelAdmin):
    list_display = ("nombre", "almacen_principal", "activa", "eliminada")
    list_filter = ("almacen_principal", "activa", "eliminada")
    search_fields = ("nombre", "descripcion")


@admin.register(Producto)
class ProductoAdmin(admin.ModelAdmin):
    list_display = (
        "codigo_interno",
        "nombre",
        "almacen",
        "categoria",
        "stock_disponible",
        "stock_en_prestamo",
        "stock_minimo",
        "activo",
    )
    list_filter = ("almacen", "categoria", "activo", "eliminado")
    search_fields = ("codigo_interno", "codigo_barras", "nombre")
    readonly_fields = ("codigo_interno", "stock_inicial", "fecha_creacion", "fecha_modificacion")


@admin.register(ProductoUbicacion)
class ProductoUbicacionAdmin(admin.ModelAdmin):
    list_display = ("producto", "ubicacion", "es_principal")
    list_filter = ("ubicacion__almacen", "es_principal")
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
        "via_haku",
    )
    list_filter = ("tipo", "estado", "almacen_origen", "via_haku")
    search_fields = ("producto__nombre", "producto__codigo_interno", "destinatario", "motivo")
    readonly_fields = (
        "idempotency_key",
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
        "cantidad_prestada",
        "cantidad_pendiente_devolucion",
        "estado",
        "fecha_prestamo",
    )
    list_filter = ("estado", "almacen")
    search_fields = ("producto__nombre", "destinatario", "motivo")


@admin.register(DevolucionPrestamo)
class DevolucionPrestamoAdmin(admin.ModelAdmin):
    list_display = ("prestamo", "tipo_cierre", "cantidad", "usuario", "fecha_hora")
    list_filter = ("tipo_cierre",)
    search_fields = ("prestamo__destinatario", "motivo")


@admin.register(ConteoFisico)
class ConteoFisicoAdmin(admin.ModelAdmin):
    list_display = (
        "producto",
        "almacen",
        "cantidad_contada",
        "stock_sistema_en_conteo",
        "diferencia",
        "estado",
        "usuario_conteo",
        "usuario_confirmacion",
    )
    list_filter = ("estado", "almacen")
    search_fields = ("producto__nombre", "observaciones")


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
