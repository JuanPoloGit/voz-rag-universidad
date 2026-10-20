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
    path("almacenes/<int:pk>/trasladar/", views.AlmacenDesactivarView.as_view(), name="trasladar-almacen"),

    # Ubicaciones
    path("ubicaciones/", views.UbicacionListaView.as_view(), name="ubicacion-lista"),
    path("ubicaciones/crear/", views.UbicacionCrearView.as_view(), name="ubicacion-crear"),
    path("ubicaciones/<int:pk>/editar/", views.UbicacionEditarView.as_view(), name="ubicacion-editar"),
    path("ubicaciones/<int:pk>/desactivar/", views.UbicacionDesactivarView.as_view(), name="ubicacion-desactivar"),
    path("ubicaciones/<int:pk>/trasladar/", views.UbicacionDesactivarView.as_view(), name="trasladar-ubicacion"),

    # Categorías
    path("categorias/", views.CategoriaListaView.as_view(), name="categoria-lista"),
    path("categorias/crear/", views.CategoriaCrearView.as_view(), name="categoria-crear"),
    path("categorias/<int:pk>/editar/", views.CategoriaEditarView.as_view(), name="categoria-editar"),
    path("categorias/<int:pk>/desactivar/", views.CategoriaDesactivarView.as_view(), name="categoria-desactivar"),
    path("categorias/<int:pk>/trasladar/", views.CategoriaTrasladarView.as_view(), name="categoria-trasladar"),

    # Productos
    path("productos/", views.ProductoListaView.as_view(), name="producto-lista"),
    path("productos/crear/", views.ProductoCrearView.as_view(), name="producto-crear"),
    path("productos/verificar-nombre/", views.ProductoVerificarNombreView.as_view(), name="producto-verificar-nombre"),
    path("productos/<int:pk>/", views.ProductoDetalleView.as_view(), name="producto-detalle"),
    path("productos/<int:pk>/editar/", views.ProductoEditarView.as_view(), name="producto-editar"),
    path("productos/<int:pk>/archivar/", views.ProductoArchivarView.as_view(), name="producto-archivar"),
    path("productos/<int:pk>/fusionar/", views.ProductoFusionarView.as_view(), name="producto-fusionar"),
]
