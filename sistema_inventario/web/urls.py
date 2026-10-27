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

    # Centro de Exportaciones (Excel / CSV) — Exclusivo Admin / Superadmin
    path("exportaciones/", views.CentroExportacionesView.as_view(), name="centro-exportaciones"),
    path("exportaciones/excel/dashboard/", views.ExportarDashboardExcelView.as_view(), name="exportar-dashboard-excel"),
    path("exportaciones/excel/<str:tipo>/", views.ExportarDatosExcelView.as_view(), name="exportar-datos-excel"),
    path("exportaciones/csv/<str:tipo>/", views.ExportarDatosCSVView.as_view(), name="exportar-datos-csv"),
]
