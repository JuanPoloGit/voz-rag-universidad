"""URLs para la app integraciones.haku — Haku Inventario, API Interna y Voz Web."""

from django.urls import path

from . import views

app_name = "haku"

urlpatterns = [
    # Interacción de comandos y diálogo
    path("mensaje/", views.MensajeHakuView.as_view(), name="mensaje"),
    path("confirmar/", views.ConfirmarPropuestaView.as_view(), name="confirmar"),
    path("cancelar/", views.CancelarPropuestaView.as_view(), name="cancelar"),

    # Consultas directas de inventario
    path("consultar-stock/", views.ConsultarStockView.as_view(), name="consultar_stock"),
    path("alertas/", views.AlertasStockView.as_view(), name="alertas"),
    path("faltantes-reposicion/", views.FaltantesReposicionView.as_view(), name="faltantes_reposicion"),
    path("prestamos-activos/", views.PrestamosActivosView.as_view(), name="prestamos_activos"),

    # Endpoints de voz en cliente
    path("voz/transcribir/", views.TranscribirVozView.as_view(), name="transcribir_voz"),
    path("voz/sintetizar/", views.SintetizarVozView.as_view(), name="sintetizar_voz"),

    path("producto/crear/", views.ProductoCrearHakuView.as_view(), name="producto_crear"),
    path("producto/editar/", views.ProductoEditarHakuView.as_view(), name="producto_editar"),
    path("producto/consultar-codigo-barras/", views.ConsultarCodigoBarrasView.as_view(), name="producto_consultar_codigo_barras"),
    path("usuario/crear/", views.UsuarioCrearHakuView.as_view(), name="usuario_crear"),
    path("usuario/editar/", views.UsuarioEditarHakuView.as_view(), name="usuario_editar"),

    # Estado de la sesión y panel web
    path("estado/", views.EstadoHakuView.as_view(), name="estado"),
    path("panel/", views.PanelHakuView.as_view(), name="panel"),
]
