"""Servicios de lógica de negocio para existencias y movimientos de stock.

Autoridad única para mutaciones de Existencia y Movimiento en el inventario.
Garantiza no negatividad de stock, aislamiento de transacciones con bloqueo
y auditoría inmutable.
"""

import uuid
from typing import Any, List, Optional, Tuple
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models, transaction

from inventario.auditoria import registrar_auditoria
from inventario.db.transacciones import transaccion_con_reintento
from inventario.models import (
    Almacen,
    Categoria,
    Existencia,
    Movimiento,
    Prestamo,
    Producto,
    Ubicacion,
)
from inventario.servicios.excepciones import InconsistenciaInventario, StockInsuficiente


# ─────────────────────────────────────────────────────────────────────────────
# PRIMITIVAS ATÓMICAS DE MUTACIÓN DE STOCK (Autoridad única sobre Existencia)
# ─────────────────────────────────────────────────────────────────────────────

def bloquear_existencias(*existencia_ids: int) -> dict[int, Existencia]:
    """Bloquea existencias en orden determinista por ID para evitar interbloqueos.

    En PostgreSQL, select_for_update() impone un bloqueo exclusivo sobre las filas.
    En SQLite, asegura la lectura consistente dentro de la transacción.
    """
    if not existencia_ids:
        return {}
    ids_ordenados = sorted(set(existencia_ids))
    qs = Existencia.objects.select_for_update().filter(id__in=ids_ordenados).order_by("id")
    return {ex.id: ex for ex in qs}


def sumar_disponible(existencia_id: int, cantidad: int) -> Existencia:
    """Incrementa stock_disponible de forma atómica en base de datos."""
    cantidad = int(cantidad)
    if cantidad <= 0:
        raise ValidationError("La cantidad a sumar debe ser mayor a cero.")

    filas = Existencia.objects.filter(pk=existencia_id).update(
        stock_disponible=models.F("stock_disponible") + cantidad
    )
    if filas == 0:
        raise InconsistenciaInventario(f"No existe el registro de existencia con ID {existencia_id}.")

    return Existencia.objects.get(pk=existencia_id)


def descontar_disponible(existencia_id: int, cantidad: int) -> Existencia:
    """Deduce stock_disponible de forma atómica con verificación estricta de no negatividad."""
    cantidad = int(cantidad)
    if cantidad <= 0:
        raise ValidationError("La cantidad a descontar debe ser mayor a cero.")

    filas = Existencia.objects.filter(
        pk=existencia_id,
        stock_disponible__gte=cantidad,
    ).update(
        stock_disponible=models.F("stock_disponible") - cantidad
    )
    if filas == 0:
        ex_actual = Existencia.objects.filter(pk=existencia_id).first()
        disp = ex_actual.stock_disponible if ex_actual else 0
        raise StockInsuficiente(
            f"Stock insuficiente. Disponible: {disp}, solicitado: {cantidad}.",
            disponible=disp,
            solicitado=cantidad,
            almacen_id=ex_actual.almacen_id if ex_actual else None,
            producto_id=ex_actual.producto_id if ex_actual else None,
        )

    return Existencia.objects.get(pk=existencia_id)


def mover_a_prestamo(existencia_id: int, cantidad: int) -> Existencia:
    """Traslada unidades de stock_disponible a stock_en_prestamo de forma atómica."""
    cantidad = int(cantidad)
    if cantidad <= 0:
        raise ValidationError("La cantidad a prestar debe ser mayor a cero.")

    filas = Existencia.objects.filter(
        pk=existencia_id,
        stock_disponible__gte=cantidad,
    ).update(
        stock_disponible=models.F("stock_disponible") - cantidad,
        stock_en_prestamo=models.F("stock_en_prestamo") + cantidad,
    )
    if filas == 0:
        ex_actual = Existencia.objects.filter(pk=existencia_id).first()
        disp = ex_actual.stock_disponible if ex_actual else 0
        raise StockInsuficiente(
            f"Stock insuficiente para préstamo. Disponible: {disp}, solicitado: {cantidad}.",
            disponible=disp,
            solicitado=cantidad,
            almacen_id=ex_actual.almacen_id if ex_actual else None,
            producto_id=ex_actual.producto_id if ex_actual else None,
        )

    return Existencia.objects.get(pk=existencia_id)


def retornar_de_prestamo(existencia_id: int, cantidad: int) -> Existencia:
    """Retorna unidades de stock_en_prestamo a stock_disponible de forma atómica."""
    cantidad = int(cantidad)
    if cantidad <= 0:
        raise ValidationError("La cantidad a retornar de préstamo debe ser mayor a cero.")

    filas = Existencia.objects.filter(
        pk=existencia_id,
        stock_en_prestamo__gte=cantidad,
    ).update(
        stock_en_prestamo=models.F("stock_en_prestamo") - cantidad,
        stock_disponible=models.F("stock_disponible") + cantidad,
    )
    if filas == 0:
        ex_actual = Existencia.objects.filter(pk=existencia_id).first()
        en_prestamo = ex_actual.stock_en_prestamo if ex_actual else 0
        raise InconsistenciaInventario(
            f"La cantidad a devolver ({cantidad}) supera las unidades registradas en préstamo ({en_prestamo}).",
            detalles={"solicitado": cantidad, "en_prestamo": en_prestamo},
        )

    return Existencia.objects.get(pk=existencia_id)


def dar_baja_prestamo(existencia_id: int, cantidad: int) -> Existencia:
    """Reduce stock_en_prestamo sin retornarlo al stock_disponible (artículo dañado/perdido)."""
    cantidad = int(cantidad)
    if cantidad <= 0:
        raise ValidationError("La cantidad a dar de baja debe ser mayor a cero.")

    filas = Existencia.objects.filter(
        pk=existencia_id,
        stock_en_prestamo__gte=cantidad,
    ).update(
        stock_en_prestamo=models.F("stock_en_prestamo") - cantidad,
    )
    if filas == 0:
        ex_actual = Existencia.objects.filter(pk=existencia_id).first()
        en_prestamo = ex_actual.stock_en_prestamo if ex_actual else 0
        raise InconsistenciaInventario(
            f"La cantidad a dar de baja ({cantidad}) supera las unidades registradas en préstamo ({en_prestamo}).",
            detalles={"solicitado": cantidad, "en_prestamo": en_prestamo},
        )

    return Existencia.objects.get(pk=existencia_id)


def ajustar_stock_conteo(existencia_id: int, diferencia: int) -> Existencia:
    """Aplica la diferencia (positiva o negativa) de un ajuste de conteo físico."""
    diferencia = int(diferencia)
    if diferencia > 0:
        return sumar_disponible(existencia_id, diferencia)
    elif diferencia < 0:
        return descontar_disponible(existencia_id, abs(diferencia))
    return Existencia.objects.get(pk=existencia_id)


@transaccion_con_reintento
def inicializar_stock(
    *,
    producto: Producto,
    almacen: Almacen,
    cantidad: int,
    usuario: Any,
    ip_origen: Optional[str] = None,
) -> Tuple[Existencia, Movimiento]:
    """Inicializa el stock de un producto generando el movimiento inicial obligatorio.
    
    Sección 8.1: Movimiento.Tipo.MOVIMIENTO_INICIAL inmutable.
    """
    if not getattr(usuario, "is_authenticated", False):
        raise PermissionDenied("Se requiere autenticación para inicializar stock.")

    cantidad = int(cantidad)
    if cantidad < 0:
        raise ValidationError("La cantidad de stock inicial no puede ser negativa.")

    with transaction.atomic():
        existencia, _ = Existencia.objects.select_for_update().get_or_create(
            producto=producto,
            almacen=almacen,
            defaults={"stock_disponible": 0, "stock_en_prestamo": 0},
        )

        stock_ant = existencia.stock_disponible
        if cantidad > 0:
            existencia = sumar_disponible(existencia.id, cantidad)
        stock_post = existencia.stock_disponible

        clave_idempotencia = f"INI-{producto.id}-{almacen.id}-{uuid.uuid4().hex[:8]}"

        movimiento = Movimiento(
            clave_idempotencia=clave_idempotencia,
            producto=producto,
            tipo=Movimiento.Tipo.MOVIMIENTO_INICIAL,
            almacen_destino=almacen,
            cantidad=cantidad,
            stock_disponible_anterior=stock_ant,
            stock_disponible_posterior=stock_post,
            usuario=usuario,
            motivo="Inicialización de inventario",
        )
        movimiento.full_clean()
        movimiento.save()

        registrar_auditoria(
            usuario=usuario,
            accion="INICIALIZAR_STOCK",
            tipo_entidad="Producto",
            id_entidad=str(producto.id),
            valores_anteriores={"stock_disponible": stock_ant},
            valores_nuevos={"stock_disponible": stock_post, "almacen_id": almacen.id},
            motivo=f"Stock inicial ({cantidad} unid.) en {almacen.nombre}",
            resultado="OK",
            via_haku=False,
            ip_origen=ip_origen,
        )

    return existencia, movimiento


def buscar_por_codigo_barras(codigo: str) -> Optional[Producto]:
    """Busca un producto activo y no archivado por código de barras exacto."""
    codigo = (codigo or "").strip()
    if not codigo:
        return None
    return Producto.objects.filter(codigo_barras=codigo, archivado=False).first()


def verificar_nombre_similar(nombre: str, excluir_id: Optional[int] = None) -> List[dict]:
    """Busca productos existentes con nombres similares para prevenir duplicados (§8.3)."""
    nombre = (nombre or "").strip()
    if len(nombre) < 3:
        return []

    qs = Producto.objects.filter(archivado=False)
    if excluir_id:
        qs = qs.exclude(id=excluir_id)

    palabras = [p for p in nombre.split() if len(p) >= 3]
    filtro = models.Q(nombre__icontains=nombre)
    for p in palabras:
        filtro |= models.Q(nombre__icontains=p)

    candidatos = qs.filter(filtro).distinct()[:10]
    return [
        {
            "id": p.id,
            "codigo_interno": p.codigo_interno,
            "nombre": p.nombre,
        }
        for p in candidatos
    ]


def crear_existencia_excepcion(
    *,
    producto: Producto,
    almacen: Almacen,
    usuario: Any,
    confirmacion: bool,
    motivo: str,
    ip_origen: Optional[str] = None,
) -> Existencia:
    """Prohibido por regla de negocio estricta: un producto solo puede estar en un único almacén."""
    raise ValidationError(
        "Regla de negocio estricta: un producto solo puede pertenecer y residir en un único almacén. "
        "No se permite crear existencias en múltiples almacenes."
    )


# ─────────────────────────────────────────────────────────────────────────────
# TRASLADOS ESTRUCTURALES Y DESACTIVACIÓN
# ─────────────────────────────────────────────────────────────────────────────

def trasladar_productos_almacen(
    *,
    almacen_origen: Almacen,
    almacen_destino: Almacen,
    usuario: Any,
    ip_origen: Optional[str] = None,
) -> int:
    """Traslada en bloque todas las existencias de un almacén a otro (§7.1)."""
    if almacen_origen.id == almacen_destino.id:
        raise ValidationError("El almacén destino no puede ser el mismo almacén de origen.")

    total_trasladados = 0

    with transaction.atomic():
        # 1. Actualizar categorías cuyo almacén principal sea el de origen para que apunten al destino
        Categoria.objects.filter(almacen_principal=almacen_origen).update(almacen_principal=almacen_destino)

        # 2. Limpiar asignaciones de ubicaciones físicas pertenecientes al almacén de origen
        Producto.objects.filter(ubicacion__almacen=almacen_origen).update(ubicacion=None)

        existencias_origen = list(Existencia.objects.select_for_update().filter(
            almacen=almacen_origen
        ))

        for ex_ori in existencias_origen:
            cant = ex_ori.stock_disponible
            cant_prest = ex_ori.stock_en_prestamo
            prod = ex_ori.producto

            # Asegurar que la categoría del producto apunte al almacén destino
            if prod.categoria and prod.categoria.almacen_principal_id != almacen_destino.id:
                cat_dest = Categoria.objects.filter(almacen_principal=almacen_destino, activo=True, eliminado=False).first()
                if not cat_dest:
                    cat_dest = Categoria.objects.create(
                        nombre=f"{prod.categoria.nombre} ({almacen_destino.nombre})",
                        almacen_principal=almacen_destino,
                    )
                prod.categoria = cat_dest
                prod.save(update_fields=["categoria"])

            # Eliminar primero origen para respetar la restricción única de 1 existencia por producto
            ex_ori.delete()

            ex_dest, _ = Existencia.objects.select_for_update().get_or_create(
                producto=prod,
                almacen=almacen_destino,
                defaults={"stock_disponible": 0, "stock_en_prestamo": 0},
            )

            stock_ant = ex_dest.stock_disponible
            stock_post = stock_ant + cant
            ex_dest.stock_disponible = stock_post
            ex_dest.stock_en_prestamo += cant_prest
            ex_dest.save()

            # Reasignar préstamos abiertos al almacén destino
            Prestamo.objects.filter(producto=prod, almacen=almacen_origen).update(almacen=almacen_destino)

            Movimiento.objects.create(
                clave_idempotencia=f"TRAS-ALM-{prod.id}-{almacen_origen.id}-{almacen_destino.id}-{uuid.uuid4().hex[:8]}",
                producto=prod,
                tipo=Movimiento.Tipo.TRASLADO_ENTRE,
                almacen_origen=almacen_origen,
                almacen_destino=almacen_destino,
                cantidad=cant,
                stock_disponible_anterior=stock_ant,
                stock_disponible_posterior=stock_post,
                usuario=usuario,
                motivo=f"Traslado estructural de almacén '{almacen_origen.nombre}' a '{almacen_destino.nombre}'",
            )
            total_trasladados += 1

        # Limpiar asignaciones de ubicaciones físicas pertenecientes al almacén de origen
        Producto.objects.filter(ubicacion__almacen=almacen_origen).update(ubicacion=None)

        registrar_auditoria(
            usuario=usuario,
            accion="TRASLADO_ESTRUCTURAL_ALMACEN",
            tipo_entidad="Almacen",
            id_entidad=str(almacen_origen.id),
            valores_anteriores={"almacen_origen_id": almacen_origen.id},
            valores_nuevos={"almacen_destino_id": almacen_destino.id, "productos_movidos": total_trasladados},
            motivo=f"Traslado estructural de {total_trasladados} productos a {almacen_destino.nombre}",
            resultado="OK",
            via_haku=False,
            ip_origen=ip_origen,
        )

    return total_trasladados


def trasladar_productos_ubicacion(
    *,
    ubicacion_origen: Ubicacion,
    ubicacion_destino: Ubicacion,
    usuario: Any,
    ip_origen: Optional[str] = None,
) -> int:
    """Reasigna productos de una ubicación a otra dentro del mismo almacén (§7.2)."""
    if ubicacion_origen.id == ubicacion_destino.id:
        raise ValidationError("La ubicación destino no puede ser la misma de origen.")

    if ubicacion_origen.almacen_id != ubicacion_destino.almacen_id:
        raise ValidationError("El traslado de ubicación solo puede realizarse entre ubicaciones del mismo almacén.")

    total_movidos = 0

    with transaction.atomic():
        total_movidos = Producto.objects.filter(ubicacion=ubicacion_origen).update(ubicacion=ubicacion_destino)

        registrar_auditoria(
            usuario=usuario,
            accion="TRASLADO_UBICACION",
            tipo_entidad="Ubicacion",
            id_entidad=str(ubicacion_origen.id),
            valores_anteriores={"ubicacion_origen_id": ubicacion_origen.id},
            valores_nuevos={"ubicacion_destino_id": ubicacion_destino.id, "productos_movidos": total_movidos},
            motivo=f"Reasignación física de {total_movidos} productos a {ubicacion_destino.nombre}",
            resultado="OK",
            via_haku=False,
            ip_origen=ip_origen,
        )

    return total_movidos


def trasladar_productos_categoria(
    *,
    categoria: Categoria,
    almacen_destino: Almacen,
    usuario: Any,
    ip_origen: Optional[str] = None,
) -> int:
    """Traslada todos los productos de una categoría a un nuevo almacén principal (§7.3)."""
    if categoria.almacen_principal_id == almacen_destino.id:
        raise ValidationError("El almacén destino ya es el almacén principal de la categoría.")

    total_trasladados = 0
    almacen_origen = categoria.almacen_principal

    with transaction.atomic():
        # Actualizar primero el almacén principal de la categoría antes de crear las nuevas existencias
        categoria.almacen_principal = almacen_destino
        categoria.save(update_fields=["almacen_principal"])

        # Limpiar ubicaciones de productos que pertenecían al almacén anterior
        Producto.objects.filter(categoria=categoria).exclude(ubicacion__almacen=almacen_destino).update(ubicacion=None)

        for prod in categoria.productos.filter(archivado=False):
            ex_ori = Existencia.objects.select_for_update().filter(producto=prod, almacen=almacen_origen).first()
            if ex_ori:
                cant = ex_ori.stock_disponible
                cant_prest = ex_ori.stock_en_prestamo

                # Eliminar primero para respetar la restricción única de 1 existencia por producto
                ex_ori.delete()

                ex_dest, _ = Existencia.objects.select_for_update().get_or_create(
                    producto=prod,
                    almacen=almacen_destino,
                    defaults={"stock_disponible": 0, "stock_en_prestamo": 0},
                )
                stock_ant = ex_dest.stock_disponible
                stock_post = stock_ant + cant
                ex_dest.stock_disponible = stock_post
                ex_dest.stock_en_prestamo += cant_prest
                ex_dest.save()

                Prestamo.objects.filter(producto=prod, almacen=almacen_origen).update(almacen=almacen_destino)

                Movimiento.objects.create(
                    clave_idempotencia=f"TRAS-CAT-{prod.id}-{almacen_origen.id}-{almacen_destino.id}-{uuid.uuid4().hex[:8]}",
                    producto=prod,
                    tipo=Movimiento.Tipo.TRASLADO_ENTRE,
                    almacen_origen=almacen_origen,
                    almacen_destino=almacen_destino,
                    cantidad=cant,
                    stock_disponible_anterior=stock_ant,
                    stock_disponible_posterior=stock_post,
                    usuario=usuario,
                    motivo=f"Traslado por cambio de almacén principal de la categoría '{categoria.nombre}'",
                )
            total_trasladados += 1

        registrar_auditoria(
            usuario=usuario,
            accion="TRASLADO_CATEGORIA",
            tipo_entidad="Categoria",
            id_entidad=str(categoria.id),
            valores_anteriores={"almacen_principal_id": almacen_origen.id},
            valores_nuevos={"almacen_principal_id": almacen_destino.id, "productos_movidos": total_trasladados},
            motivo=f"Traslado de categoría '{categoria.nombre}' al almacén '{almacen_destino.nombre}'",
            resultado="OK",
            via_haku=False,
            ip_origen=ip_origen,
        )

    return total_trasladados


def desactivar_almacen(
    *,
    almacen: Almacen,
    almacen_destino: Optional[Almacen] = None,
    usuario: Any,
    ip_origen: Optional[str] = None,
) -> None:
    """Desactiva un almacén exigiendo traslado previo si contiene existencias o préstamos activos (§7.1)."""
    if not getattr(usuario, "es_administrador", False):
        raise PermissionDenied("Solo los Administradores o el Superusuario pueden desactivar almacenes.")

    tiene_stock = almacen.existencias.filter(
        models.Q(stock_disponible__gt=0) | models.Q(stock_en_prestamo__gt=0)
    ).exists()
    tiene_prestamos = almacen.prestamos.filter(estado__in=["ABIERTO", "DEVUELTO_PARCIAL"]).exists()

    if tiene_stock or tiene_prestamos:
        if not almacen_destino:
            raise ValidationError("El almacén tiene existencias o préstamos activos; debe especificar un almacén destino para el traslado estructural.")
        if almacen_destino.id == almacen.id:
            raise ValidationError("El almacén destino no puede ser el mismo que se va a desactivar.")

    with transaction.atomic():
        if (tiene_stock or tiene_prestamos) and almacen_destino:
            trasladar_productos_almacen(
                almacen_origen=almacen,
                almacen_destino=almacen_destino,
                usuario=usuario,
                ip_origen=ip_origen,
            )
            # Reasignar préstamos abiertos al almacén destino
            almacen.prestamos.filter(estado__in=["ABIERTO", "DEVUELTO_PARCIAL"]).update(almacen=almacen_destino)

        if almacen_destino:
            Categoria.objects.filter(almacen_principal=almacen).update(almacen_principal=almacen_destino)

        almacen.activo = False
        almacen.save(update_fields=["activo"])
        almacen.ubicaciones.update(activo=False)

        registrar_auditoria(
            usuario=usuario,
            accion="DESACTIVAR_ALMACEN",
            tipo_entidad="Almacen",
            id_entidad=str(almacen.id),
            valores_anteriores={"activo": True},
            valores_nuevos={"activo": False, "almacen_destino_id": almacen_destino.id if almacen_destino else None},
            motivo=f"Desactivación del almacén '{almacen.nombre}'",
            resultado="OK",
            via_haku=False,
            ip_origen=ip_origen,
        )


def desactivar_ubicacion(
    *,
    ubicacion: Ubicacion,
    ubicacion_destino: Optional[Ubicacion] = None,
    usuario: Any,
    ip_origen: Optional[str] = None,
) -> None:
    """Desactiva una ubicación exigiendo reasignación si contiene productos (§7.2)."""
    if not getattr(usuario, "es_administrador", False):
        raise PermissionDenied("Solo los Administradores o el Superusuario pueden desactivar ubicaciones.")

    tiene_productos = ubicacion.productos.filter(archivado=False).exists()

    if tiene_productos:
        if not ubicacion_destino:
            raise ValidationError("La ubicación contiene productos; debe especificar una ubicación destino en el mismo almacén.")
        if ubicacion_destino.id == ubicacion.id:
            raise ValidationError("La ubicación destino no puede ser la misma de origen.")

    with transaction.atomic():
        if tiene_productos and ubicacion_destino:
            trasladar_productos_ubicacion(
                ubicacion_origen=ubicacion,
                ubicacion_destino=ubicacion_destino,
                usuario=usuario,
                ip_origen=ip_origen,
            )

        ubicacion.activo = False
        ubicacion.save(update_fields=["activo"])

        registrar_auditoria(
            usuario=usuario,
            accion="DESACTIVAR_UBICACION",
            tipo_entidad="Ubicacion",
            id_entidad=str(ubicacion.id),
            valores_anteriores={"activo": True},
            valores_nuevos={"activo": False, "ubicacion_destino_id": ubicacion_destino.id if ubicacion_destino else None},
            motivo=f"Desactivación de ubicación '{ubicacion.nombre}'",
            resultado="OK",
            via_haku=False,
            ip_origen=ip_origen,
        )


def desactivar_categoria(
    *,
    categoria: Categoria,
    usuario: Any,
    ip_origen: Optional[str] = None,
) -> None:
    """Desactiva una categoría si no cuenta con productos activos (§7.3)."""
    if not getattr(usuario, "es_administrador", False):
        raise PermissionDenied("Solo los Administradores o el Superusuario pueden desactivar categorías.")

    if categoria.productos.filter(archivado=False).exists():
        raise ValidationError("No se puede desactivar una categoría con productos activos asociados.")

    with transaction.atomic():
        categoria.activo = False
        categoria.save(update_fields=["activo"])

        registrar_auditoria(
            usuario=usuario,
            accion="DESACTIVAR_CATEGORIA",
            tipo_entidad="Categoria",
            id_entidad=str(categoria.id),
            valores_anteriores={"activo": True},
            valores_nuevos={"activo": False},
            motivo=f"Desactivación de la categoría '{categoria.nombre}'",
            resultado="OK",
            via_haku=False,
            ip_origen=ip_origen,
        )
