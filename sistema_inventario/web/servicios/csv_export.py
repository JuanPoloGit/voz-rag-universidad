"""
Servicio de exportación a CSV con codificación UTF-8 BOM (compatible nativo con Excel en español).
"""

import csv
from datetime import datetime
from typing import Any
from django.http import HttpResponse

from web.servicios.reportes import obtener_datos_grafico_categorias


def _crear_respuesta_csv(nombre_archivo_base: str) -> tuple[HttpResponse, Any]:
    """Crea un objeto HttpResponse para streaming/descarga de CSV con BOM."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    nombre_archivo = f"{nombre_archivo_base}_{timestamp}.csv"
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{nombre_archivo}"'
    # Escribir UTF-8 Byte Order Mark (BOM) para compatibilidad con Microsoft Excel
    response.write("\ufeff")
    writer = csv.writer(response, delimiter=";", quoting=csv.QUOTE_MINIMAL)
    return response, writer


def exportar_existencias_csv(existencias_qs) -> HttpResponse:
    """Exporta el reporte de existencias a formato CSV."""
    response, writer = _crear_respuesta_csv("existencias")
    writer.writerow([
        "Código Interno",
        "Nombre Producto",
        "Categoría",
        "Almacén",
        "Stock Disponible",
        "Stock en Préstamo",
        "Stock Mínimo",
        "Estado",
    ])

    for ex in existencias_qs:
        estado = "Óptimo" if ex.stock_disponible >= ex.producto.stock_minimo else ("Agotado" if ex.stock_disponible == 0 else "Bajo Mínimo")
        writer.writerow([
            ex.producto.codigo_interno,
            ex.producto.nombre,
            ex.producto.categoria.nombre if ex.producto.categoria else "Sin categoría",
            ex.almacen.nombre,
            ex.stock_disponible,
            ex.stock_en_prestamo,
            ex.producto.stock_minimo,
            estado,
        ])

    return response


def exportar_movimientos_csv(movimientos_qs) -> HttpResponse:
    """Exporta el historial de movimientos a formato CSV."""
    response, writer = _crear_respuesta_csv("movimientos")
    writer.writerow([
        "Folio",
        "Fecha y Hora",
        "Tipo Movimiento",
        "Código Producto",
        "Nombre Producto",
        "Almacén Origen",
        "Almacén Destino",
        "Cantidad",
        "Responsable",
        "Motivo",
    ])

    for m in movimientos_qs:
        writer.writerow([
            str(m.id)[:8].upper(),
            m.fecha_hora.strftime("%Y-%m-%d %H:%M:%S"),
            m.get_tipo_display(),
            m.producto.codigo_interno,
            m.producto.nombre,
            m.almacen_origen.nombre if m.almacen_origen else "-",
            m.almacen_destino.nombre if m.almacen_destino else "-",
            m.cantidad,
            m.usuario.nombre_completo or m.usuario.username if m.usuario else "Sistema",
            m.motivo,
        ])

    return response


def exportar_prestamos_csv(prestamos_qs) -> HttpResponse:
    """Exporta el reporte de préstamos a formato CSV."""
    response, writer = _crear_respuesta_csv("prestamos")
    writer.writerow([
        "Folio",
        "Fecha Creación",
        "Destinatario",
        "Código Producto",
        "Nombre Producto",
        "Almacén",
        "Cantidad Original",
        "Cantidad Pendiente",
        "Estado",
        "Motivo",
    ])

    for p in prestamos_qs:
        motivo_prestamo = p.movimiento.motivo if getattr(p, "movimiento", None) else ""
        writer.writerow([
            str(p.id)[:8].upper(),
            p.fecha_creacion.strftime("%Y-%m-%d %H:%M:%S"),
            p.destinatario,
            p.producto.codigo_interno,
            p.producto.nombre,
            p.almacen.nombre,
            p.cantidad_original,
            p.cantidad_pendiente_devolucion,
            p.get_estado_display(),
            motivo_prestamo,
        ])

    return response


def exportar_resumen_dashboard_csv(metricas, usuario=None, almacen=None) -> HttpResponse:
    """Exporta un resumen tabular del dashboard a formato CSV."""
    response, writer = _crear_respuesta_csv("dashboard_resumen")
    writer.writerow(["Métrica / Indicador", "Valor"])
    writer.writerow(["Total Productos en Catálogo", metricas["total_productos"]])
    writer.writerow(["Stock Disponible Total", metricas["total_stock_disponible"]])
    writer.writerow(["Artículos en Préstamo", metricas["total_stock_prestamo"]])
    writer.writerow(["Artículos en Stock Crítico (0)", metricas["total_criticos"]])
    writer.writerow(["Artículos Bajo Stock Mínimo", metricas["total_bajo_stock"]])
    writer.writerow(["Préstamos Activos", metricas["total_prestamos_activos"]])
    writer.writerow([])

    # Mini resumen de categorías
    datos_cat = obtener_datos_grafico_categorias(usuario, almacen=almacen)
    writer.writerow(["Categoría", "Total Productos"])
    for cat, total in zip(datos_cat.get("labels", []), datos_cat.get("series", [])):
        writer.writerow([cat, total])

    return response


def exportar_inventario_completo_csv(usuario, almacen=None) -> HttpResponse:
    """Exporta TODOS los datos consolidados y detallados del inventario a CSV con codificación UTF-8 BOM."""
    response, writer = _crear_respuesta_csv("inventario_completo")
    writer.writerow([
        "Código Interno",
        "Nombre Producto",
        "Categoría",
        "Tipo de Producto",
        "Almacén",
        "Ubicaciones en Almacén",
        "Stock Disponible",
        "Stock en Préstamo",
        "Stock Total",
        "Stock Mínimo",
        "Estado del Stock",
        "Código de Barras",
        "Empaque / Presentación",
        "Unidades por Empaque",
    ])

    from web.servicios.reportes import obtener_reporte_existencias
    alm_id = almacen.id if almacen else None
    qs = obtener_reporte_existencias(usuario, almacen_id=alm_id)
    qs = qs.select_related("producto", "almacen", "producto__categoria", "producto__tipo").prefetch_related(
        "producto__producto_ubicaciones__ubicacion"
    )

    for ex in qs:
        prod = ex.producto
        stock_total = ex.stock_disponible + ex.stock_en_prestamo

        # Estado descriptivo
        if ex.stock_disponible == 0:
            estado = "AGOTADO / CRÍTICO"
        elif ex.stock_disponible < prod.stock_minimo:
            estado = "BAJO MÍNIMO"
        else:
            estado = "ÓPTIMO"

        # Ubicaciones del producto en este almacén
        ubicaciones_alm = [
            pu.ubicacion.nombre
            for pu in prod.producto_ubicaciones.all()
            if pu.ubicacion.almacen_id == ex.almacen_id and not getattr(pu.ubicacion, "eliminado", False)
        ]
        ubicaciones_str = ", ".join(ubicaciones_alm) if ubicaciones_alm else "Sin ubicación específica"

        writer.writerow([
            prod.codigo_interno,
            prod.nombre,
            prod.categoria.nombre if prod.categoria else "Sin categoría",
            prod.tipo.nombre if prod.tipo else "General",
            ex.almacen.nombre,
            ubicaciones_str,
            ex.stock_disponible,
            ex.stock_en_prestamo,
            stock_total,
            prod.stock_minimo,
            estado,
            prod.codigo_barras or "N/A",
            prod.nombre_empaque or "Unidad",
            prod.unidades_por_empaque or 1,
        ])

    return response
