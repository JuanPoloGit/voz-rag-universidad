"""Servicios de agregación analítica y generación de reportes de inventario."""

from datetime import timedelta
from typing import Any, Dict, List, Optional
from django.db.models import Count, F, Max, Q, Sum
from django.utils import timezone

from inventario.models import Almacen, Categoria, Existencia, Movimiento, Prestamo, Producto


def obtener_metricas_dashboard(usuario: Any, almacen: Optional[Almacen] = None) -> Dict[str, Any]:
    """Calcula las métricas principales para el Dashboard según el ámbito del usuario y almacén opcional."""
    es_normal = getattr(usuario, "es_usuario_normal", False)
    almacen_usuario = getattr(usuario, "almacen_asignado", None) if es_normal else None
    almacen_efectivo = almacen_usuario if es_normal else almacen

    if es_normal and not almacen_efectivo:
        return {
            "total_productos": 0,
            "total_stock_disponible": 0,
            "total_stock_prestamo": 0,
            "total_criticos": 0,
            "total_bajo_stock": 0,
            "total_prestamos_activos": 0,
            "ultimos_movimientos": [],
            "prestamos_recientes": [],
        }

    if almacen_efectivo:
        prod_qs = Producto.objects.filter(
            archivado=False,
            activo=True,
        ).filter(
            Q(categoria__almacen_principal=almacen_efectivo) | Q(existencias__almacen=almacen_efectivo)
        ).distinct()
        ex_qs = Existencia.objects.filter(
            producto__archivado=False,
            producto__activo=True,
            almacen=almacen_efectivo,
        )
        mov_qs = Movimiento.objects.filter(
            Q(almacen_origen=almacen_efectivo) | Q(almacen_destino=almacen_efectivo)
        )
        pres_qs = Prestamo.objects.filter(
            almacen=almacen_efectivo,
            estado__in=[Prestamo.Estado.ABIERTO, Prestamo.Estado.DEVUELTO_PARCIAL],
        )
    else:
        prod_qs = Producto.objects.filter(archivado=False, activo=True)
        ex_qs = Existencia.objects.filter(
            producto__archivado=False,
            producto__activo=True,
            almacen__activo=True,
            almacen__eliminado=False,
        )
        mov_qs = Movimiento.objects.all()
        pres_qs = Prestamo.objects.filter(
            estado__in=[Prestamo.Estado.ABIERTO, Prestamo.Estado.DEVUELTO_PARCIAL]
        )

    totales_stock = ex_qs.aggregate(
        disp=Sum("stock_disponible"),
        prest=Sum("stock_en_prestamo"),
    )

    return {
        "total_productos": prod_qs.count(),
        "total_stock_disponible": totales_stock["disp"] or 0,
        "total_stock_prestamo": totales_stock["prest"] or 0,
        "total_criticos": ex_qs.filter(stock_disponible=0).count(),
        "total_bajo_stock": ex_qs.filter(
            stock_disponible__gt=0,
            stock_disponible__lt=F("producto__stock_minimo"),
        ).count(),
        "total_prestamos_activos": pres_qs.count(),
        "ultimos_movimientos": mov_qs.select_related(
            "producto", "usuario", "almacen_origen", "almacen_destino"
        ).order_by("-fecha_hora")[:10],
        "prestamos_recientes": pres_qs.select_related("producto", "almacen").order_by("-fecha_creacion")[:5],
    }


def obtener_datos_grafico_categorias(usuario: Any, almacen: Optional[Almacen] = None) -> Dict[str, Any]:
    """Genera datos para el gráfico de distribución de productos por categoría."""
    es_normal = getattr(usuario, "es_usuario_normal", False)
    almacen_usuario = getattr(usuario, "almacen_asignado", None) if es_normal else None
    almacen_efectivo = almacen_usuario if es_normal else almacen

    if es_normal and not almacen_efectivo:
        return {"labels": [], "data": []}

    cats_qs = Categoria.objects.filter(activo=True, eliminado=False)
    if almacen_efectivo:
        cats_qs = cats_qs.filter(almacen_principal=almacen_efectivo)

    cats = cats_qs.annotate(
        num_prods=Count("productos", filter=Q(productos__archivado=False, productos__activo=True))
    ).order_by("-num_prods")

    labels = []
    data = []
    for c in cats:
        if c.num_prods > 0:
            labels.append(c.nombre)
            data.append(c.num_prods)

    return {"labels": labels, "data": data}


def obtener_datos_grafico_balance_movimientos(
    usuario: Any, dias: int = 7, almacen: Optional[Almacen] = None
) -> Dict[str, Any]:
    """Genera serie de tiempo con balance de Entradas vs. Dotación (Salidas) en los últimos N días."""
    hoy = timezone.now().date()
    labels = []
    entradas = []
    salidas = []

    es_normal = getattr(usuario, "es_usuario_normal", False)
    almacen_usuario = getattr(usuario, "almacen_asignado", None) if es_normal else None
    almacen_efectivo = almacen_usuario if es_normal else almacen

    if es_normal and not almacen_efectivo:
        return {"labels": [], "entradas": [], "salidas": []}

    base_qs = Movimiento.objects.all()
    if almacen_efectivo:
        base_qs = base_qs.filter(Q(almacen_origen=almacen_efectivo) | Q(almacen_destino=almacen_efectivo))

    for i in range(dias - 1, -1, -1):
        dia = hoy - timedelta(days=i)
        dia_str = dia.strftime("%d/%m")
        labels.append(dia_str)

        total_ent = base_qs.filter(
            tipo=Movimiento.Tipo.ENTRADA,
            fecha_hora__date=dia,
        ).aggregate(total=Sum("cantidad"))["total"] or 0

        total_sal = base_qs.filter(
            tipo=Movimiento.Tipo.SALIDA,
            fecha_hora__date=dia,
        ).aggregate(total=Sum("cantidad"))["total"] or 0

        entradas.append(total_ent)
        salidas.append(total_sal)

    return {
        "labels": labels,
        "entradas": entradas,
        "salidas": salidas,
    }


def obtener_reporte_existencias(
    usuario: Any,
    almacen_id: Optional[int] = None,
    categoria_id: Optional[int] = None,
    filtro_stock: Optional[str] = None,
):
    """Consulta de existencias filtrable con cálculo de alertas."""
    es_normal = getattr(usuario, "es_usuario_normal", False)
    almacen_usuario = getattr(usuario, "almacen_asignado", None) if es_normal else None

    qs = Existencia.objects.filter(
        producto__archivado=False,
        producto__activo=True,
        almacen__activo=True,
        almacen__eliminado=False,
    ).select_related("producto", "almacen", "producto__categoria").order_by("producto__codigo_interno")

    if es_normal:
        if not almacen_usuario:
            return qs.none()
        qs = qs.filter(almacen=almacen_usuario)
    elif almacen_id:
        qs = qs.filter(almacen_id=almacen_id)

    if categoria_id:
        qs = qs.filter(producto__categoria_id=categoria_id)

    if filtro_stock == "CRITICO":
        qs = qs.filter(stock_disponible=0)
    elif filtro_stock == "BAJO":
        qs = qs.filter(stock_disponible__gt=0, stock_disponible__lt=F("producto__stock_minimo"))
    elif filtro_stock == "NORMAL":
        qs = qs.filter(stock_disponible__gte=F("producto__stock_minimo"))

    return qs


def obtener_reporte_movimientos_periodo(
    usuario: Any,
    fecha_desde: Optional[Any] = None,
    fecha_hasta: Optional[Any] = None,
    tipo: Optional[str] = None,
    producto_id: Optional[int] = None,
):
    """Consulta de movimientos en un período filtrables por tipo y producto."""
    es_normal = getattr(usuario, "es_usuario_normal", False)
    almacen_usuario = getattr(usuario, "almacen_asignado", None) if es_normal else None

    qs = Movimiento.objects.select_related(
        "producto", "almacen_origen", "almacen_destino", "usuario"
    ).order_by("-fecha_hora")

    if es_normal:
        if not almacen_usuario:
            return qs.none()
        qs = qs.filter(Q(almacen_origen=almacen_usuario) | Q(almacen_destino=almacen_usuario))

    if fecha_desde:
        qs = qs.filter(fecha_hora__date__gte=fecha_desde)
    if fecha_hasta:
        qs = qs.filter(fecha_hora__date__lte=fecha_hasta)
    if tipo:
        qs = qs.filter(tipo=tipo)
    if producto_id:
        qs = qs.filter(producto_id=producto_id)

    return qs


def obtener_reporte_prestamos_activos(usuario: Any, estado: Optional[str] = None) -> List[Any]:
    """Lista de préstamos abiertos con cálculo de días transcurridos."""
    es_normal = getattr(usuario, "es_usuario_normal", False)
    almacen_usuario = getattr(usuario, "almacen_asignado", None) if es_normal else None

    qs = Prestamo.objects.select_related("producto", "almacen", "usuario").order_by("-fecha_creacion")

    if es_normal:
        if not almacen_usuario:
            return []
        qs = qs.filter(almacen=almacen_usuario)

    if estado:
        qs = qs.filter(estado=estado)
    else:
        qs = qs.filter(estado__in=[Prestamo.Estado.ABIERTO, Prestamo.Estado.DEVUELTO_PARCIAL])

    ahora = timezone.now()
    prestamos = list(qs)
    for p in prestamos:
        p.dias_transcurridos = (ahora - p.fecha_creacion).days

    return prestamos


def obtener_reporte_productos_estancados(usuario: Any, dias_inactividad: int = 30) -> List[Any]:
    """Identifica productos que no han tenido ningún movimiento en los últimos N días."""
    es_normal = getattr(usuario, "es_usuario_normal", False)
    almacen_usuario = getattr(usuario, "almacen_asignado", None) if es_normal else None

    prods = Producto.objects.filter(archivado=False, activo=True).select_related("categoria")
    if es_normal:
        if not almacen_usuario:
            return []
        prods = prods.filter(categoria__almacen_principal=almacen_usuario)

    limite = timezone.now() - timedelta(days=dias_inactividad)
    
    # Anota la última fecha de movimiento
    prods_con_max = prods.annotate(ultima_actividad=Max("movimientos__fecha_hora"))
    
    estancados = []
    for p in prods_con_max:
        if not p.ultima_actividad or p.ultima_actividad < limite:
            p.dias_sin_movimiento = (
                (timezone.now() - p.ultima_actividad).days if p.ultima_actividad else dias_inactividad
            )
            estancados.append(p)

    return estancados
