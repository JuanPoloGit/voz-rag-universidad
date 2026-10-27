"""URLs para la app inventario — Vistas maestras y operativas."""

from django.urls import path

from inventario import views

app_name = "inventario"

urlpatterns = [
    # Almacenes
    path("almacenes/", views.AlmacenListaView.as_view(), name="almacen-lista"),
    path("almacenes/crear/", views.AlmacenCrearView.as_view(), name="almacen-crear"),
    path("almacenes/<int:pk>/editar/", views.AlmacenEditarView.as_view(), name="almacen-editar"),
    path("almacenes/<int:pk>/desactivar/", views.AlmacenDesactivarView.as_view(), name="almacen-desactivar"),
    path("almacenes/<int:pk>/reactivar/", views.AlmacenReactivarView.as_view(), name="almacen-reactivar"),
    path("almacenes/<int:pk>/eliminar/", views.AlmacenEliminarView.as_view(), name="almacen-eliminar"),
    path("almacenes/<int:pk>/trasladar/", views.AlmacenDesactivarView.as_view(), name="trasladar-almacen"),

    # Ubicaciones
    path("ubicaciones/", views.UbicacionListaView.as_view(), name="ubicacion-lista"),
    path("ubicaciones/crear/", views.UbicacionCrearView.as_view(), name="ubicacion-crear"),
    path("ubicaciones/<int:pk>/editar/", views.UbicacionEditarView.as_view(), name="ubicacion-editar"),
    path("ubicaciones/<int:pk>/desactivar/", views.UbicacionDesactivarView.as_view(), name="ubicacion-desactivar"),
    path("ubicaciones/<int:pk>/reactivar/", views.UbicacionReactivarView.as_view(), name="ubicacion-reactivar"),
    path("ubicaciones/<int:pk>/eliminar/", views.UbicacionEliminarView.as_view(), name="ubicacion-eliminar"),
    path("ubicaciones/<int:pk>/trasladar/", views.UbicacionTrasladarView.as_view(), name="trasladar-ubicacion"),

    # Categorías y Tipos opcionales
    path("categorias/", views.CategoriaListaView.as_view(), name="categoria-lista"),
    path("categorias/crear/", views.CategoriaCrearView.as_view(), name="categoria-crear"),
    path("categorias/<int:pk>/editar/", views.CategoriaEditarView.as_view(), name="categoria-editar"),
    path("categorias/<int:pk>/desactivar/", views.CategoriaDesactivarView.as_view(), name="categoria-desactivar"),
    path("categorias/<int:pk>/reactivar/", views.CategoriaReactivarView.as_view(), name="categoria-reactivar"),
    path("categorias/<int:pk>/eliminar/", views.CategoriaEliminarView.as_view(), name="categoria-eliminar"),
    path("categorias/<int:pk>/trasladar/", views.CategoriaTrasladarView.as_view(), name="categoria-trasladar"),
    path("categorias/tipos/crear/", views.TipoCategoriaCrearView.as_view(), name="tipo-categoria-crear"),
    path("categorias/tipos/<int:pk>/editar/", views.TipoCategoriaEditarView.as_view(), name="tipo-categoria-editar"),
    path("categorias/tipos/<int:pk>/eliminar/", views.TipoCategoriaEliminarView.as_view(), name="tipo-categoria-eliminar"),

    # Productos
    path("productos/", views.ProductoListaView.as_view(), name="producto-lista"),
    path("productos/crear/", views.ProductoCrearView.as_view(), name="producto-crear"),
    path("productos/verificar-nombre/", views.ProductoVerificarNombreView.as_view(), name="producto-verificar-nombre"),
    path("productos/<int:pk>/", views.ProductoDetalleView.as_view(), name="producto-detalle"),
    path("productos/<int:pk>/editar/", views.ProductoEditarView.as_view(), name="producto-editar"),
    path("productos/<int:pk>/toggle-activo/", views.ProductoToggleActivoView.as_view(), name="producto-toggle-activo"),
    path("productos/<int:pk>/archivar/", views.ProductoArchivarView.as_view(), name="producto-archivar"),
    path("productos/<int:pk>/fusionar/", views.ProductoFusionarView.as_view(), name="producto-fusionar"),

    # Alertas y Notificaciones de Stock
    path("alertas/", views.AlertasStockListaView.as_view(), name="alertas-lista"),
    path("alertas/marcar-leida/", views.AlertaMarcarLeidaView.as_view(), name="alerta-marcar-leida"),
    path("alertas/json/", views.AlertasStockJSONView.as_view(), name="alertas-json"),
    path("perfil/actualizar-nombre/", views.ActualizarNombrePerfilView.as_view(), name="perfil-actualizar-nombre"),

    # Movimientos
    path("movimientos/", views.MovimientoListaView.as_view(), name="movimiento-lista"),
    path("movimientos/entrada/", views.EntradaCrearView.as_view(), name="movimiento-entrada"),
    path("movimientos/salida/", views.SalidaCrearView.as_view(), name="movimiento-salida"),
    path("movimientos/traslado/", views.TrasladoCrearView.as_view(), name="movimiento-traslado"),
    path("movimientos/<uuid:pk>/revertir/", views.MovimientoRevertirView.as_view(), name="movimiento-revertir"),

    # Préstamos
    path("prestamos/", views.PrestamoListaView.as_view(), name="prestamo-lista"),
    path("prestamos/crear/", views.PrestamoCrearView.as_view(), name="prestamo-crear"),
    path("prestamos/<uuid:pk>/devolver/", views.PrestamoDevolucionView.as_view(), name="prestamo-devolver"),
    path("prestamos/<uuid:pk>/baja/", views.PrestamoBajaView.as_view(), name="prestamo-baja"),

    # Conteos Físicos
    path("conteos/", views.ConteoFisicoListaView.as_view(), name="conteo-lista"),
    path("conteos/crear/", views.ConteoFisicoCrearView.as_view(), name="conteo-crear"),
    path("conteos/<uuid:pk>/confirmar/", views.ConteoFisicoConfirmarView.as_view(), name="conteo-confirmar"),

    # Copias de Seguridad (Exclusivo Superadministrador)
    path("respaldos/", views.RespaldoListaView.as_view(), name="respaldo-lista"),
    path("respaldos/generar/", views.RespaldoCrearView.as_view(), name="respaldo-crear"),
    path("respaldos/descargar/<str:nombre_archivo>/", views.RespaldoDescargarView.as_view(), name="respaldo-descargar"),
]
