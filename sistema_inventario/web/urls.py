"""URLs de la app web — Dashboard analítico e Informes de gestión."""

from django.urls import path

from web import views

app_name = "web"

urlpatterns = [
    # Dashboard principal
    path("", views.DashboardView.as_view(), name="dashboard"),

    # Endpoints AJAX para gráficos Chart.js
    path("graficos/categorias/", views.GraficoCategoriasJSONView.as_view(), name="grafico-categorias"),
    path("graficos/balance/", views.GraficoBalanceJSONView.as_view(), name="grafico-balance"),

    # Informes de gestión
    path("reportes/existencias/", views.ReporteExistenciasView.as_view(), name="reporte-existencias"),
    path("reportes/movimientos/", views.ReporteMovimientosView.as_view(), name="reporte-movimientos"),
    path("reportes/prestamos/", views.ReportePrestamosView.as_view(), name="reporte-prestamos"),
    path("reportes/estancados/", views.ReporteProductosEstancadosView.as_view(), name="reporte-estancados"),
]
