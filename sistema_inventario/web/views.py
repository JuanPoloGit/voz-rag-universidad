"""Vistas de la aplicación web: Dashboard analítico e Informes de gestión."""

from datetime import datetime
from django.contrib.auth.mixins import LoginRequiredMixin
from cuentas.permisos import AdministradorRequeridoMixin, PermisoRequeridoMixin
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.generic import View

from inventario.models import Almacen, Categoria, Movimiento, Producto
from web.servicios.csv_export import (
    exportar_existencias_csv,
    exportar_movimientos_csv,
    exportar_prestamos_csv,
    exportar_resumen_dashboard_csv,
)
from web.servicios.excel_export import (
    exportar_dashboard_excel,
    exportar_existencias_excel,
    exportar_movimientos_excel,
    exportar_prestamos_excel,
)
from web.servicios.reportes import (
    obtener_datos_grafico_balance_movimientos,
    obtener_datos_grafico_categorias,
    obtener_metricas_dashboard,
    obtener_reporte_existencias,
    obtener_reporte_movimientos_periodo,
    obtener_reporte_prestamos_activos,
    obtener_reporte_productos_estancados,
)


class DashboardView(LoginRequiredMixin, View):
    """Panel de Control y visualización general operativa (§11)."""

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        if not getattr(request.user, "es_administrador", False):
            if not request.user.tiene_permiso("analitica.ver_reportes"):
                from django.shortcuts import redirect
                from django.core.exceptions import PermissionDenied
                if request.user.tiene_permiso("catalogo.ver"):
                    return redirect("inventario:producto-lista")
                if request.user.tiene_permiso("movimientos.ver"):
                    return redirect("inventario:movimiento-lista")
                if request.user.tiene_permiso("infraestructura.ver"):
                    return redirect("inventario:categoria-lista")
                raise PermissionDenied("No tienes permisos suficientes para visualizar el panel de control.")
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        if "almacen_id" in request.GET and not request.GET.get("almacen_id", "").strip():
            from django.shortcuts import redirect
            return redirect("web:dashboard")

        user = request.user
        almacen_id = request.GET.get("almacen_id", "").strip()
        almacen_obj = None

        if getattr(user, "es_usuario_normal", False):
            almacenes = user.get_almacenes_asignados()
            if almacen_id and almacen_id.isdigit():
                almacen_obj = almacenes.filter(id=int(almacen_id)).first()
            if not almacen_obj and almacenes.count() == 1:
                almacen_obj = almacenes.first()
                almacen_id = str(almacen_obj.id)
            elif not almacen_obj:
                almacen_id = ""
        else:
            almacenes = Almacen.objects.filter(activo=True, eliminado=False).order_by("nombre")
            if almacen_id and almacen_id.isdigit():
                almacen_obj = Almacen.objects.filter(id=int(almacen_id), activo=True, eliminado=False).first()
                if not almacen_obj:
                    almacen_id = ""

        metricas = obtener_metricas_dashboard(user, almacen=almacen_obj)
        return render(
            request,
            "web/dashboard.html",
            {
                "metricas": metricas,
                "ultimos_movimientos": metricas["ultimos_movimientos"],
                "prestamos_recientes": metricas["prestamos_recientes"],
                "almacenes": almacenes,
                "almacen_seleccionado": almacen_id,
                "almacen_obj": almacen_obj,
            },
        )


class GraficoCategoriasJSONView(LoginRequiredMixin, PermisoRequeridoMixin, View):
    """Endpoint AJAX que retorna la distribución de productos por categoría."""

    permiso_requerido = "analitica.ver_reportes"

    def get(self, request):
        user = request.user
        almacen_id = request.GET.get("almacen_id", "").strip()
        almacen_obj = None
        if getattr(user, "es_usuario_normal", False):
            user_alms = user.get_almacenes_asignados()
            if almacen_id and almacen_id.isdigit():
                almacen_obj = user_alms.filter(id=int(almacen_id)).first()
            elif user_alms.count() == 1:
                almacen_obj = user_alms.first()
        elif almacen_id and almacen_id.isdigit():
            almacen_obj = Almacen.objects.filter(id=int(almacen_id), activo=True, eliminado=False).first()
        datos = obtener_datos_grafico_categorias(user, almacen=almacen_obj)
        return JsonResponse(datos)


class GraficoBalanceJSONView(LoginRequiredMixin, PermisoRequeridoMixin, View):
    """Endpoint AJAX que retorna el balance de entradas vs. dotación (salidas) en los últimos N días."""

    permiso_requerido = "analitica.ver_reportes"

    def get(self, request):
        user = request.user
        dias_str = request.GET.get("dias", "7")
        dias = int(dias_str) if dias_str.isdigit() else 7
        almacen_id = request.GET.get("almacen_id", "").strip()
        almacen_obj = None
        if getattr(user, "es_usuario_normal", False):
            user_alms = user.get_almacenes_asignados()
            if almacen_id and almacen_id.isdigit():
                almacen_obj = user_alms.filter(id=int(almacen_id)).first()
            elif user_alms.count() == 1:
                almacen_obj = user_alms.first()
        elif almacen_id and almacen_id.isdigit():
            almacen_obj = Almacen.objects.filter(id=int(almacen_id), activo=True, eliminado=False).first()
        datos = obtener_datos_grafico_balance_movimientos(user, dias=dias, almacen=almacen_obj)
        return JsonResponse(datos)


class ReporteExistenciasView(LoginRequiredMixin, PermisoRequeridoMixin, View):
    permiso_requerido = "analitica.ver_reportes"
    """Informe analítico de existencias actuales filtrable por almacén, categoría y nivel de stock."""

    def get(self, request):
        user = request.user
        almacen_id = request.GET.get("almacen_id")
        categoria_id = request.GET.get("categoria_id")
        filtro_stock = request.GET.get("filtro_stock", "")

        alm_id_int = int(almacen_id) if almacen_id and almacen_id.isdigit() else None
        cat_id_int = int(categoria_id) if categoria_id and categoria_id.isdigit() else None

        existencias = obtener_reporte_existencias(
            usuario=user,
            almacen_id=alm_id_int,
            categoria_id=cat_id_int,
            filtro_stock=filtro_stock if filtro_stock else None,
        )

        if getattr(user, "es_usuario_normal", False):
            almacenes = user.get_almacenes_asignados()
            categorias = Categoria.objects.filter(activo=True, eliminado=False, almacen_principal__in=almacenes)
        else:
            almacenes = Almacen.objects.filter(activo=True, eliminado=False)
            categorias = Categoria.objects.filter(activo=True, eliminado=False)

        return render(
            request,
            "web/reporte_existencias.html",
            {
                "existencias": existencias,
                "almacenes": almacenes,
                "categorias": categorias,
                "almacen_id": alm_id_int,
                "categoria_id": cat_id_int,
                "filtro_stock": filtro_stock,
            },
        )


class ReporteMovimientosView(LoginRequiredMixin, PermisoRequeridoMixin, View):
    permiso_requerido = "analitica.ver_reportes"
    """Informe cronológico de movimientos filtrable por rango de fechas, tipo y producto."""

    def get(self, request):
        user = request.user
        fecha_desde_str = request.GET.get("fecha_desde", "").strip()
        fecha_hasta_str = request.GET.get("fecha_hasta", "").strip()
        tipo = request.GET.get("tipo", "").strip()
        producto_id = request.GET.get("producto_id")

        fecha_desde = None
        fecha_hasta = None

        if fecha_desde_str:
            try:
                fecha_desde = datetime.strptime(fecha_desde_str, "%Y-%m-%d").date()
            except ValueError:
                pass

        if fecha_hasta_str:
            try:
                fecha_hasta = datetime.strptime(fecha_hasta_str, "%Y-%m-%d").date()
            except ValueError:
                pass

        prod_id_int = int(producto_id) if producto_id and producto_id.isdigit() else None

        movimientos = obtener_reporte_movimientos_periodo(
            usuario=user,
            fecha_desde=fecha_desde,
            fecha_hasta=fecha_hasta,
            tipo=tipo if tipo else None,
            producto_id=prod_id_int,
        )

        return render(
            request,
            "web/reporte_movimientos.html",
            {
                "movimientos": movimientos,
                "tipos_movimiento": Movimiento.Tipo.choices,
                "fecha_desde": fecha_desde_str,
                "fecha_hasta": fecha_hasta_str,
                "tipo_seleccionado": tipo,
            },
        )


class ReportePrestamosView(LoginRequiredMixin, PermisoRequeridoMixin, View):
    permiso_requerido = "analitica.ver_reportes"
    """Informe de préstamos activos con cálculo de antigüedad y responsable."""

    def get(self, request):
        user = request.user
        estado = request.GET.get("estado", "").strip()
        prestamos = obtener_reporte_prestamos_activos(
            usuario=user,
            estado=estado if estado else None,
        )

        return render(
            request,
            "web/reporte_prestamos.html",
            {
                "prestamos": prestamos,
                "estado_seleccionado": estado,
            },
        )


class ReporteProductosEstancadosView(LoginRequiredMixin, PermisoRequeridoMixin, View):
    permiso_requerido = "analitica.ver_reportes"
    """Informe de artículos sin rotación ni movimientos en los últimos N días."""

    def get(self, request):
        user = request.user
        dias_str = request.GET.get("dias", "30").strip()
        dias = int(dias_str) if dias_str.isdigit() else 30

        estancados = obtener_reporte_productos_estancados(usuario=user, dias_inactividad=dias)

        return render(
            request,
            "web/reporte_estancados.html",
            {
                "estancados": estancados,
                "dias": dias,
            },
        )


# ═════════════════════════════════════════════════════════════════════════════
# CENTRO DE EXPORTACIONES (EXCEL / CSV) — EXCLUSIVO ADMINISTRADORES Y SUPERADMIN
# ═════════════════════════════════════════════════════════════════════════════

class CentroExportacionesView(LoginRequiredMixin, AdministradorRequeridoMixin, View):
    """Panel central de exportaciones en Excel y CSV (§11)."""

    def get(self, request):
        from inventario.models import Prestamo
        almacenes = Almacen.objects.filter(activo=True, eliminado=False).order_by("nombre")
        categorias = Categoria.objects.filter(activo=True, eliminado=False).order_by("nombre")
        return render(
            request,
            "web/centro_exportaciones.html",
            {
                "almacenes": almacenes,
                "categorias": categorias,
                "tipos_movimiento": Movimiento.Tipo.choices,
                "estados_prestamo": Prestamo.Estado.choices,
            },
        )


class ExportarDashboardExcelView(LoginRequiredMixin, AdministradorRequeridoMixin, View):
    """Descarga del libro profesional de Excel con todas las pestañas del Dashboard."""

    def get(self, request):
        almacen_id = request.GET.get("almacen_id", "").strip()
        almacen_obj = None
        if almacen_id.isdigit():
            almacen_obj = Almacen.objects.filter(id=int(almacen_id), activo=True, eliminado=False).first()

        buffer = exportar_dashboard_excel(request.user, almacen=almacen_obj)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        nombre_archivo = f"Dashboard_AudacIA_{timestamp}.xlsx"

        response = HttpResponse(
            buffer.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = f'attachment; filename="{nombre_archivo}"'
        return response


class ExportarDatosExcelView(LoginRequiredMixin, AdministradorRequeridoMixin, View):
    """Exportación en Excel de reportes específicos (Existencias, Movimientos, Préstamos)."""

    def get(self, request, tipo):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        if tipo == "existencias":
            almacen_id = int(request.GET.get("almacen_id")) if request.GET.get("almacen_id", "").isdigit() else None
            categoria_id = int(request.GET.get("categoria_id")) if request.GET.get("categoria_id", "").isdigit() else None
            filtro_stock = request.GET.get("filtro_stock", "") or None
            qs = obtener_reporte_existencias(request.user, almacen_id=almacen_id, categoria_id=categoria_id, filtro_stock=filtro_stock)
            buffer = exportar_existencias_excel(qs)
            filename = f"Existencias_AudacIA_{timestamp}.xlsx"

        elif tipo == "movimientos":
            f_desde = None
            f_hasta = None
            if request.GET.get("fecha_desde"):
                try:
                    f_desde = datetime.strptime(request.GET["fecha_desde"].strip(), "%Y-%m-%d").date()
                except ValueError:
                    pass
            if request.GET.get("fecha_hasta"):
                try:
                    f_hasta = datetime.strptime(request.GET["fecha_hasta"].strip(), "%Y-%m-%d").date()
                except ValueError:
                    pass
            t_mov = request.GET.get("tipo", "").strip() or None
            p_id = int(request.GET.get("producto_id")) if request.GET.get("producto_id", "").isdigit() else None
            qs = obtener_reporte_movimientos_periodo(request.user, fecha_desde=f_desde, fecha_hasta=f_hasta, tipo=t_mov, producto_id=p_id)
            buffer = exportar_movimientos_excel(qs)
            filename = f"Movimientos_AudacIA_{timestamp}.xlsx"

        elif tipo == "prestamos":
            estado = request.GET.get("estado", "").strip() or None
            qs = obtener_reporte_prestamos_activos(request.user, estado=estado)
            buffer = exportar_prestamos_excel(qs)
            filename = f"Prestamos_AudacIA_{timestamp}.xlsx"

        else:
            raise Http404("Tipo de exportación en Excel no válido.")

        response = HttpResponse(
            buffer.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class ExportarDatosCSVView(LoginRequiredMixin, AdministradorRequeridoMixin, View):
    """Exportación en CSV con UTF-8 BOM de datos de existencias, movimientos, préstamos o resumen."""

    def get(self, request, tipo):
        if tipo == "dashboard":
            almacen_id = request.GET.get("almacen_id", "").strip()
            almacen_obj = Almacen.objects.filter(id=int(almacen_id)).first() if almacen_id.isdigit() else None
            metricas = obtener_metricas_dashboard(request.user, almacen=almacen_obj)
            return exportar_resumen_dashboard_csv(metricas, usuario=request.user, almacen=almacen_obj)

        elif tipo == "existencias":
            almacen_id = int(request.GET.get("almacen_id")) if request.GET.get("almacen_id", "").isdigit() else None
            categoria_id = int(request.GET.get("categoria_id")) if request.GET.get("categoria_id", "").isdigit() else None
            filtro_stock = request.GET.get("filtro_stock", "") or None
            qs = obtener_reporte_existencias(request.user, almacen_id=almacen_id, categoria_id=categoria_id, filtro_stock=filtro_stock)
            return exportar_existencias_csv(qs)

        elif tipo == "movimientos":
            f_desde = None
            f_hasta = None
            if request.GET.get("fecha_desde"):
                try:
                    f_desde = datetime.strptime(request.GET["fecha_desde"].strip(), "%Y-%m-%d").date()
                except ValueError:
                    pass
            if request.GET.get("fecha_hasta"):
                try:
                    f_hasta = datetime.strptime(request.GET["fecha_hasta"].strip(), "%Y-%m-%d").date()
                except ValueError:
                    pass
            t_mov = request.GET.get("tipo", "").strip() or None
            p_id = int(request.GET.get("producto_id")) if request.GET.get("producto_id", "").isdigit() else None
            qs = obtener_reporte_movimientos_periodo(request.user, fecha_desde=f_desde, fecha_hasta=f_hasta, tipo=t_mov, producto_id=p_id)
            return exportar_movimientos_csv(qs)

        elif tipo == "prestamos":
            estado = request.GET.get("estado", "").strip() or None
            qs = obtener_reporte_prestamos_activos(request.user, estado=estado)
            return exportar_prestamos_csv(qs)

        else:
            raise Http404("Tipo de exportación en CSV no válido.")
