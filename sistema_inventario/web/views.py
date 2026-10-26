"""Vistas de la aplicación web: Dashboard analítico e Informes de gestión."""

from datetime import datetime
from django.contrib.auth.mixins import LoginRequiredMixin
from cuentas.permisos import PermisoRequeridoMixin
from django.http import JsonResponse
from django.shortcuts import render
from django.views.generic import View

from inventario.models import Almacen, Categoria, Movimiento, Producto
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


class GraficoCategoriasJSONView(LoginRequiredMixin, View):
    """Endpoint AJAX que retorna la distribución de productos por categoría."""

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


class GraficoBalanceJSONView(LoginRequiredMixin, View):
    """Endpoint AJAX que retorna el balance de entradas vs. dotación (salidas) en los últimos N días."""

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
