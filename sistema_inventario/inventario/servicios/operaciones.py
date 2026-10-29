"""Servicios de operaciones y movimientos de inventario.

Autoridad única para ejecutar mutaciones transaccionales de stock:
- Entradas de stock con conversión de empaques (§8.5, §9.2).
- Salidas con motivo/destinatario obligatorio y no negatividad estricta (§9.3).
- Alertas inmediatas de nivel bajo y crítico (§8.4).
- Préstamos, devoluciones parciales y bajas por daño (§9.4).
- Traslados internos y entre almacenes (§9.5).
- Conteo físico preliminar y confirmación administrativa (§9.7).
- Reversiones compensatorias de movimientos (§9.6).
"""

import hashlib
import time
import uuid
import warnings
from typing import Any, Dict, Optional, Tuple
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from inventario.auditoria import registrar_auditoria
from inventario.db.transacciones import transaccion_con_reintento
from inventario.models import (
    AlertaStockLeida,
    Almacen,
    Categoria,
    ConteoFisico,
    DevolucionPrestamo,
    Existencia,
    Movimiento,
    Prestamo,
    Producto,
    Ubicacion,
)
from inventario.servicios import stock
from inventario.servicios.alertas import verificar_estado_alerta
from inventario.servicios.excepciones import InconsistenciaInventario, StockInsuficiente


def _generar_clave_idempotencia(prefijo: str, usuario: Any, producto_id: Any, almacen_id: Any, cantidad: int) -> str:
    """Genera una clave de idempotencia determinista basada en ventana temporal de 10 segundos.

    Protege contra doble-clic del usuario o reintentos automáticos de red sin token explícito.
    """
    ventana = int(time.time() // 10)
    uid = getattr(usuario, "id", "anon")
    semilla = f"{prefijo}:{uid}:{producto_id}:{almacen_id}:{cantidad}:{ventana}"
    token = hashlib.sha256(semilla.encode()).hexdigest()[:12]
    return f"{prefijo}-{producto_id}-{almacen_id}-{token}"


def _validar_acceso_almacen(usuario: Any, almacen: Almacen) -> None:
    """Valida que el usuario tenga permiso para operar en el almacén."""
    if not getattr(usuario, "is_authenticated", False):
        raise PermissionDenied("Se requiere autenticación para operar sobre el inventario.")

    if getattr(usuario, "es_usuario_normal", False):
        if not usuario.tiene_acceso_almacen(almacen):
            raise PermissionDenied("No tienes permisos para realizar operaciones en este almacén.")


def _validar_permiso_operativo(usuario: Any, codigo_permiso: str) -> None:
    """Valida que el usuario cuente con el permiso específico para la operación."""
    if not getattr(usuario, "is_authenticated", False):
        raise PermissionDenied("Se requiere autenticación para operar sobre el inventario.")
    if getattr(usuario, "es_administrador", False):
        return
    if not getattr(usuario, "tiene_permiso", lambda p: False)(codigo_permiso):
        raise PermissionDenied(f"No tienes permisos suficientes ({codigo_permiso}) para realizar esta operación.")


@transaccion_con_reintento
def registrar_entrada(
    *,
    producto: Producto,
    almacen: Almacen,
    cantidad: int,
    usuario: Any,
    ubicacion: Optional[Ubicacion] = None,
    es_empaque: bool = False,
    cantidad_empaques: Optional[int] = None,
    clave_idempotencia: Optional[str] = None,
    motivo: str = "",
    observacion: str = "",
    ip_origen: Optional[str] = None,
) -> Tuple[Existencia, Movimiento]:
    """Registra una entrada de stock en un almacén.
    
    Soporta conversión de empaques (§8.5): si es_empaque=True, calcula
    la cantidad base a partir de cantidad_empaques * producto.unidades_por_empaque.
    """
    _validar_acceso_almacen(usuario, almacen)
    _validar_permiso_operativo(usuario, "movimientos.entrada")

    if ubicacion is not None:
        warnings.warn(
            "El parámetro 'ubicacion' en registrar_entrada está deprecado. La ubicación física es propiedad directa de Producto.",
            DeprecationWarning,
            stacklevel=2,
        )
    else:
        ubicacion = producto.ubicacion

    if not clave_idempotencia:
        clave_idempotencia = _generar_clave_idempotencia("ENT", usuario, producto.id, almacen.id, cantidad)

    # Verificación de idempotencia
    mov_existente = Movimiento.objects.filter(clave_idempotencia=clave_idempotencia).first()
    if mov_existente:
        existencia = Existencia.objects.get(producto=producto, almacen=almacen)
        return existencia, mov_existente

    # Cálculo de unidades base
    cantidad_base = cantidad
    obs_calculada = observacion

    if es_empaque:
        if not cantidad_empaques or cantidad_empaques <= 0:
            raise ValidationError("La cantidad de empaques debe ser mayor a cero.")
        if not producto.unidades_por_empaque or producto.unidades_por_empaque <= 0:
            raise ValidationError(f"El producto '{producto.nombre}' no tiene configurado el factor de empaque.")
        
        cantidad_base = cantidad_empaques * producto.unidades_por_empaque
        nombre_emp = producto.nombre_empaque or "paquete"
        nota_empaque = f"Ingreso de {cantidad_empaques} {nombre_emp}(s) equivalentes a {cantidad_base} unidades base."
        obs_calculada = f"{nota_empaque} {obs_calculada}".strip()
    else:
        if cantidad_base <= 0:
            raise ValidationError("La cantidad ingresada debe ser mayor a cero.")

    try:
        with transaction.atomic():
            existencia, _ = Existencia.objects.select_for_update().get_or_create(
                producto=producto,
                almacen=almacen,
                defaults={"stock_disponible": 0, "stock_en_prestamo": 0},
            )

            stock_anterior = existencia.stock_disponible
            existencia = stock.sumar_disponible(existencia.id, cantidad_base)
            stock_posterior = existencia.stock_disponible

            if ubicacion:
                if ubicacion.almacen_id != almacen.id:
                    raise ValidationError("La ubicación especificada no pertenece al almacén de destino.")
                if producto.ubicacion_id != ubicacion.id:
                    producto.ubicacion = ubicacion
                    producto.save(update_fields=["ubicacion"])

            movimiento = Movimiento(
                clave_idempotencia=clave_idempotencia,
                tipo=Movimiento.Tipo.ENTRADA,
                producto=producto,
                cantidad=cantidad_base,
                almacen_destino=almacen,
                ubicacion_destino=ubicacion,
                usuario=usuario,
                stock_disponible_anterior=stock_anterior,
                stock_disponible_posterior=stock_posterior,
                motivo=motivo or "Entrada regular de mercancía",
                observacion=obs_calculada,
            )
            movimiento.full_clean()
            movimiento.save()

            registrar_auditoria(
                usuario=usuario,
                accion="ENTRADA_STOCK",
                tipo_entidad="Movimiento",
                id_entidad=str(movimiento.id),
                valores_anteriores={"stock_disponible": stock_anterior},
                valores_nuevos={"stock_disponible": stock_posterior, "cantidad_ingresada": cantidad_base},
                motivo=motivo or "Entrada regular de mercancía",
                resultado="OK",
                via_haku=False,
                ip_origen=ip_origen,
            )

        return existencia, movimiento
    except IntegrityError:
        mov_existente = Movimiento.objects.filter(clave_idempotencia=clave_idempotencia).first()
        if mov_existente:
            existencia = Existencia.objects.get(producto=producto, almacen=almacen)
            return existencia, mov_existente
        raise
        raise


@transaccion_con_reintento
def registrar_salida(
    *,
    producto: Producto,
    almacen: Almacen,
    cantidad: int,
    motivo: str,
    destinatario: str,
    usuario: Any,
    ubicacion: Optional[Ubicacion] = None,
    clave_idempotencia: Optional[str] = None,
    observacion: str = "",
    ip_origen: Optional[str] = None,
) -> Tuple[Existencia, Movimiento, Dict[str, Any]]:
    """Registra una salida de stock garantizando no negatividad y auditoría.
    
    Motivo y destinatario son obligatorios (§9.3).
    Retorna la existencia actualizada, el movimiento y el estado de alerta (§8.4).
    """
    _validar_acceso_almacen(usuario, almacen)
    _validar_permiso_operativo(usuario, "movimientos.dotacion")

    if ubicacion is not None:
        warnings.warn(
            "El parámetro 'ubicacion' en registrar_salida está deprecado. La ubicación física es propiedad directa de Producto.",
            DeprecationWarning,
            stacklevel=2,
        )
    else:
        ubicacion = producto.ubicacion

    motivo = (motivo or "").strip()
    if not motivo:
        raise ValidationError("El motivo de la salida es obligatorio.")

    destinatario = (destinatario or "").strip()
    if not destinatario:
        raise ValidationError("El destinatario de la salida es obligatorio.")

    cantidad = int(cantidad)
    if cantidad <= 0:
        raise ValidationError("La cantidad a retirar debe ser mayor a cero.")

    if not clave_idempotencia:
        clave_idempotencia = _generar_clave_idempotencia("SAL", usuario, producto.id, almacen.id, cantidad)

    mov_existente = Movimiento.objects.filter(clave_idempotencia=clave_idempotencia).first()
    if mov_existente:
        existencia = Existencia.objects.get(producto=producto, almacen=almacen)
        alerta = verificar_estado_alerta(existencia)
        return existencia, mov_existente, alerta

    try:
        with transaction.atomic():
            try:
                existencia = Existencia.objects.select_for_update().get(
                    producto=producto,
                    almacen=almacen,
                )
            except Existencia.DoesNotExist:
                raise ValidationError(f"No hay existencias registradas para el producto '{producto.nombre}' en {almacen.nombre}.")

            stock_anterior = existencia.stock_disponible
            existencia = stock.descontar_disponible(existencia.id, cantidad)
            stock_posterior = existencia.stock_disponible

            # Observación autogenerada editable si no se proveyó una personalizada
            if not observacion:
                nombre_usr = getattr(usuario, "nombre_completo", "") or getattr(usuario, "username", "Usuario")
                observacion = f"{nombre_usr} registró la salida de {cantidad} unidades de {producto.codigo_interno} para {destinatario}."

            movimiento = Movimiento(
                clave_idempotencia=clave_idempotencia,
                tipo=Movimiento.Tipo.SALIDA,
                producto=producto,
                cantidad=cantidad,
                almacen_origen=almacen,
                ubicacion_origen=ubicacion,
                usuario=usuario,
                stock_disponible_anterior=stock_anterior,
                stock_disponible_posterior=stock_posterior,
                motivo=motivo,
                destinatario=destinatario,
                observacion=observacion,
            )
            movimiento.full_clean()
            movimiento.save()

            alerta = verificar_estado_alerta(existencia)

            registrar_auditoria(
                usuario=usuario,
                accion="SALIDA_STOCK",
                tipo_entidad="Movimiento",
                id_entidad=str(movimiento.id),
                valores_anteriores={"stock_disponible": stock_anterior},
                valores_nuevos={"stock_disponible": stock_posterior, "cantidad_retirada": cantidad},
                motivo=f"Salida: {motivo} (Destinatario: {destinatario})",
                resultado="OK",
                via_haku=False,
                ip_origen=ip_origen,
            )

        return existencia, movimiento, alerta
    except IntegrityError:
        mov_existente = Movimiento.objects.filter(clave_idempotencia=clave_idempotencia).first()
        if mov_existente:
            existencia = Existencia.objects.get(producto=producto, almacen=almacen)
            alerta = verificar_estado_alerta(existencia)
            return existencia, mov_existente, alerta
        raise


@transaccion_con_reintento
def registrar_prestamo(
    *,
    producto: Producto,
    almacen: Almacen,
    cantidad: int,
    destinatario: str,
    usuario: Any,
    ubicacion: Optional[Ubicacion] = None,
    motivo: str = "",
    observacion: str = "",
    clave_idempotencia: Optional[str] = None,
    ip_origen: Optional[str] = None,
) -> Tuple[Prestamo, Movimiento, Dict[str, Any]]:
    """Registra un préstamo de productos descontando de stock_disponible y sumando a stock_en_prestamo (§9.4)."""
    _validar_acceso_almacen(usuario, almacen)
    _validar_permiso_operativo(usuario, "movimientos.prestamo")

    if ubicacion is not None:
        warnings.warn(
            "El parámetro 'ubicacion' en registrar_prestamo está deprecado. La ubicación física es propiedad directa de Producto.",
            DeprecationWarning,
            stacklevel=2,
        )
    else:
        ubicacion = producto.ubicacion

    destinatario = (destinatario or "").strip()
    if not destinatario:
        raise ValidationError("El destinatario o responsable del préstamo es obligatorio.")

    cantidad = int(cantidad)
    if cantidad <= 0:
        raise ValidationError("La cantidad a prestar debe ser mayor a cero.")

    if not clave_idempotencia:
        clave_idempotencia = _generar_clave_idempotencia("PRES", usuario, producto.id, almacen.id, cantidad)

    mov_existente = Movimiento.objects.filter(clave_idempotencia=clave_idempotencia).first()
    if mov_existente and hasattr(mov_existente, "prestamo"):
        existencia = Existencia.objects.get(producto=producto, almacen=almacen)
        alerta = verificar_estado_alerta(existencia)
        return mov_existente.prestamo, mov_existente, alerta

    try:
        with transaction.atomic():
            try:
                existencia = Existencia.objects.select_for_update().get(
                    producto=producto,
                    almacen=almacen,
                )
            except Existencia.DoesNotExist:
                raise ValidationError(f"No hay existencias registradas para el producto '{producto.nombre}' en {almacen.nombre}.")

            stock_anterior = existencia.stock_disponible
            existencia = stock.mover_a_prestamo(existencia.id, cantidad)
            stock_posterior = existencia.stock_disponible

            movimiento = Movimiento(
                clave_idempotencia=clave_idempotencia,
                tipo=Movimiento.Tipo.PRESTAMO,
                producto=producto,
                cantidad=cantidad,
                almacen_origen=almacen,
                ubicacion_origen=ubicacion,
                usuario=usuario,
                stock_disponible_anterior=stock_anterior,
                stock_disponible_posterior=stock_posterior,
                motivo=motivo or "Préstamo de artículo",
                destinatario=destinatario,
                observacion=observacion,
            )
            movimiento.full_clean()
            movimiento.save()

            prestamo = Prestamo(
                movimiento=movimiento,
                producto=producto,
                almacen=almacen,
                cantidad_original=cantidad,
                cantidad_pendiente_devolucion=cantidad,
                destinatario=destinatario,
                ubicacion_origen=ubicacion,
                usuario=usuario,
                estado=Prestamo.Estado.ABIERTO,
            )
            prestamo.full_clean()
            prestamo.save()

            alerta = verificar_estado_alerta(existencia)

            registrar_auditoria(
                usuario=usuario,
                accion="PRESTAMO_STOCK",
                tipo_entidad="Prestamo",
                id_entidad=str(prestamo.id),
                valores_anteriores={"stock_disponible": stock_anterior, "stock_en_prestamo": existencia.stock_en_prestamo - cantidad},
                valores_nuevos={"stock_disponible": stock_posterior, "stock_en_prestamo": existencia.stock_en_prestamo, "destinatario": destinatario},
                motivo=f"Préstamo a {destinatario}: {motivo}",
                resultado="OK",
                via_haku=False,
                ip_origen=ip_origen,
            )

        return prestamo, movimiento, alerta
    except IntegrityError:
        mov_existente = Movimiento.objects.filter(clave_idempotencia=clave_idempotencia).first()
        if mov_existente and hasattr(mov_existente, "prestamo"):
            existencia = Existencia.objects.get(producto=producto, almacen=almacen)
            alerta = verificar_estado_alerta(existencia)
            return mov_existente.prestamo, mov_existente, alerta
        raise


@transaccion_con_reintento
def registrar_devolucion_prestamo(
    *,
    prestamo: Prestamo,
    cantidad: int,
    usuario: Any,
    motivo: str,
    observacion: str = "",
    clave_idempotencia: Optional[str] = None,
    ip_origen: Optional[str] = None,
) -> Tuple[Prestamo, DevolucionPrestamo, Movimiento]:
    """Registra la devolución (total o parcial) de un préstamo (§9.4).
    
    Suma lo devuelto al stock disponible y descuenta de stock en préstamo.
    """
    _validar_acceso_almacen(usuario, prestamo.almacen)
    _validar_permiso_operativo(usuario, "movimientos.devolucion")

    motivo = (motivo or "").strip()
    if not motivo:
        raise ValidationError("El motivo de la devolución es obligatorio.")

    cantidad = int(cantidad)
    if cantidad <= 0:
        raise ValidationError("La cantidad a devolver debe ser mayor a cero.")

    if not clave_idempotencia:
        clave_idempotencia = _generar_clave_idempotencia("DEV", usuario, prestamo.producto_id, prestamo.almacen_id, cantidad)

    mov_existente = Movimiento.objects.filter(clave_idempotencia=clave_idempotencia).first()
    if mov_existente:
        devolucion = DevolucionPrestamo.objects.filter(movimiento=mov_existente).first()
        if devolucion:
            prestamo.refresh_from_db()
            return prestamo, devolucion, mov_existente

    with transaction.atomic():
        prestamo_db = Prestamo.objects.select_for_update().get(pk=prestamo.pk)

        if prestamo_db.estado == Prestamo.Estado.CERRADO:
            raise ValidationError("El préstamo ya se encuentra cerrado.")

        if cantidad > prestamo_db.cantidad_pendiente_devolucion:
            raise ValidationError(
                f"La cantidad a devolver ({cantidad}) supera la cantidad pendiente ({prestamo_db.cantidad_pendiente_devolucion})."
            )

        existencia = Existencia.objects.select_for_update().get(
            producto=prestamo_db.producto,
            almacen=prestamo_db.almacen,
        )

        stock_anterior = existencia.stock_disponible
        existencia = stock.retornar_de_prestamo(existencia.id, cantidad)
        stock_posterior = existencia.stock_disponible

        nuevo_pendiente = prestamo_db.cantidad_pendiente_devolucion - cantidad
        prestamo_db.cantidad_pendiente_devolucion = nuevo_pendiente

        if nuevo_pendiente == 0:
            prestamo_db.estado = Prestamo.Estado.CERRADO
            prestamo_db.fecha_cierre = timezone.now()
        else:
            prestamo_db.estado = Prestamo.Estado.DEVUELTO_PARCIAL

        prestamo_db.save()

        movimiento = Movimiento(
            clave_idempotencia=clave_idempotencia,
            tipo=Movimiento.Tipo.DEVOLUCION,
            producto=prestamo_db.producto,
            cantidad=cantidad,
            almacen_destino=prestamo_db.almacen,
            movimiento_original=prestamo_db.movimiento,
            usuario=usuario,
            stock_disponible_anterior=stock_anterior,
            stock_disponible_posterior=stock_posterior,
            destinatario=prestamo_db.destinatario,
            motivo=motivo,
            observacion=observacion,
        )
        movimiento.full_clean()
        movimiento.save()

        devolucion = DevolucionPrestamo(
            prestamo=prestamo_db,
            cantidad=cantidad,
            tipo=DevolucionPrestamo.Tipo.DEVOLUCION,
            motivo=motivo,
            usuario=usuario,
            movimiento=movimiento,
        )
        devolucion.full_clean()
        devolucion.save()

        registrar_auditoria(
            usuario=usuario,
            accion="DEVOLUCION_PRESTAMO",
            tipo_entidad="Prestamo",
            id_entidad=str(prestamo_db.id),
            valores_anteriores={"pendiente": prestamo_db.cantidad_pendiente_devolucion + cantidad, "stock_disponible": stock_anterior},
            valores_nuevos={"pendiente": nuevo_pendiente, "stock_disponible": stock_posterior, "estado": prestamo_db.estado},
            motivo=f"Devolución de préstamo: {motivo}",
            resultado="OK",
            via_haku=False,
            ip_origen=ip_origen,
        )

    return prestamo_db, devolucion, movimiento


@transaccion_con_reintento
def registrar_baja_prestamo(
    *,
    prestamo: Prestamo,
    cantidad: int,
    usuario: Any,
    motivo: str,
    observacion: str = "",
    clave_idempotencia: Optional[str] = None,
    ip_origen: Optional[str] = None,
) -> Tuple[Prestamo, DevolucionPrestamo, Movimiento]:
    """Registra la baja por daño o pérdida de un artículo prestado (§9.4).
    
    Reduce la cantidad pendiente y el stock en préstamo, pero NO suma a stock disponible.
    """
    _validar_acceso_almacen(usuario, prestamo.almacen)
    _validar_permiso_operativo(usuario, "movimientos.baja")

    motivo = (motivo or "").strip()
    if not motivo:
        raise ValidationError("El motivo de la baja por daño/pérdida es obligatorio.")

    cantidad = int(cantidad)
    if cantidad <= 0:
        raise ValidationError("La cantidad dada de baja debe ser mayor a cero.")

    if not clave_idempotencia:
        clave_idempotencia = _generar_clave_idempotencia("BAJA", usuario, prestamo.producto_id, prestamo.almacen_id, cantidad)

    mov_existente = Movimiento.objects.filter(clave_idempotencia=clave_idempotencia).first()
    if mov_existente:
        baja = DevolucionPrestamo.objects.filter(movimiento=mov_existente).first()
        if baja:
            prestamo.refresh_from_db()
            return prestamo, baja, mov_existente

    with transaction.atomic():
        prestamo_db = Prestamo.objects.select_for_update().get(pk=prestamo.pk)

        if prestamo_db.estado == Prestamo.Estado.CERRADO:
            raise ValidationError("El préstamo ya se encuentra cerrado.")

        if cantidad > prestamo_db.cantidad_pendiente_devolucion:
            raise ValidationError(
                f"La cantidad dada de baja ({cantidad}) supera la cantidad pendiente ({prestamo_db.cantidad_pendiente_devolucion})."
            )

        existencia = Existencia.objects.select_for_update().get(
            producto=prestamo_db.producto,
            almacen=prestamo_db.almacen,
        )

        # Regla innegociable §9.4: NO suma al stock disponible, descuenta atómicamente de préstamo
        stock_anterior = existencia.stock_disponible
        existencia = stock.dar_baja_prestamo(existencia.id, cantidad)

        nuevo_pendiente = prestamo_db.cantidad_pendiente_devolucion - cantidad
        prestamo_db.cantidad_pendiente_devolucion = nuevo_pendiente

        if nuevo_pendiente == 0:
            prestamo_db.estado = Prestamo.Estado.CERRADO
            prestamo_db.fecha_cierre = timezone.now()
        else:
            prestamo_db.estado = Prestamo.Estado.DEVUELTO_PARCIAL

        prestamo_db.save()

        movimiento = Movimiento(
            clave_idempotencia=clave_idempotencia,
            tipo=Movimiento.Tipo.BAJA_PRESTAMO,
            producto=prestamo_db.producto,
            cantidad=cantidad,
            almacen_origen=prestamo_db.almacen,
            movimiento_original=prestamo_db.movimiento,
            usuario=usuario,
            stock_disponible_anterior=stock_anterior,
            stock_disponible_posterior=stock_anterior,  # Permanece inalterado
            destinatario=prestamo_db.destinatario,
            motivo=motivo,
            observacion=observacion,
        )
        movimiento.full_clean()
        movimiento.save()

        baja = DevolucionPrestamo(
            prestamo=prestamo_db,
            cantidad=cantidad,
            tipo=DevolucionPrestamo.Tipo.BAJA_DANO,
            motivo=motivo,
            usuario=usuario,
            movimiento=movimiento,
        )
        baja.full_clean()
        baja.save()

        registrar_auditoria(
            usuario=usuario,
            accion="BAJA_PRESTAMO_DANO",
            tipo_entidad="Prestamo",
            id_entidad=str(prestamo_db.id),
            valores_anteriores={"pendiente": prestamo_db.cantidad_pendiente_devolucion + cantidad, "stock_en_prestamo": existencia.stock_en_prestamo + cantidad},
            valores_nuevos={"pendiente": nuevo_pendiente, "stock_en_prestamo": existencia.stock_en_prestamo, "estado": prestamo_db.estado},
            motivo=f"Baja por daño/pérdida: {motivo}",
            resultado="OK",
            via_haku=False,
            ip_origen=ip_origen,
        )

    return prestamo_db, baja, movimiento


@transaccion_con_reintento
def registrar_traslado_interno(
    *,
    producto: Producto,
    almacen: Almacen,
    ubicacion_origen: Ubicacion,
    ubicacion_destino: Ubicacion,
    usuario: Any,
    motivo: str = "",
    observacion: str = "",
    clave_idempotencia: Optional[str] = None,
    ip_origen: Optional[str] = None,
) -> Tuple[Existencia, Movimiento]:
    """Registra el traslado interno de un producto entre ubicaciones de un mismo almacén (§9.5).
    
    Actualiza las ubicaciones registradas sin alterar el stock total.
    """
    _validar_acceso_almacen(usuario, almacen)
    _validar_permiso_operativo(usuario, "movimientos.traslado_interno")

    if ubicacion_origen.almacen_id != almacen.id or ubicacion_destino.almacen_id != almacen.id:
        raise ValidationError("Ambas ubicaciones deben pertenecer al almacén indicado.")

    if ubicacion_origen.id == ubicacion_destino.id:
        raise ValidationError("La ubicación origen y destino no pueden ser la misma.")

    if not clave_idempotencia:
        clave_idempotencia = _generar_clave_idempotencia(
            "TRAS-INT", usuario, producto.id, f"{ubicacion_origen.id}-{ubicacion_destino.id}", 1
        )

    mov_existente = Movimiento.objects.filter(clave_idempotencia=clave_idempotencia).first()
    if mov_existente:
        existencia = Existencia.objects.filter(producto=producto, almacen=almacen).first()
        if existencia:
            return existencia, mov_existente

    with transaction.atomic():
        try:
            existencia = Existencia.objects.select_for_update().get(
                producto=producto,
                almacen=almacen,
            )
        except Existencia.DoesNotExist:
            raise ValidationError(f"No hay existencias de '{producto.nombre}' en {almacen.nombre}.")

        # Actualiza la asignación física directa (un producto solo está en una única ubicación)
        producto.ubicacion = ubicacion_destino
        producto.save(update_fields=["ubicacion"])

        movimiento = Movimiento(
            clave_idempotencia=clave_idempotencia,
            tipo=Movimiento.Tipo.TRASLADO_INTERNO,
            producto=producto,
            cantidad=existencia.stock_disponible if existencia.stock_disponible > 0 else 1,
            almacen_origen=almacen,
            ubicacion_origen=ubicacion_origen,
            ubicacion_destino=ubicacion_destino,
            usuario=usuario,
            stock_disponible_anterior=existencia.stock_disponible,
            stock_disponible_posterior=existencia.stock_disponible,
            motivo=motivo or f"Traslado interno de '{ubicacion_origen.nombre}' a '{ubicacion_destino.nombre}'",
            observacion=observacion,
        )
        movimiento.full_clean()
        movimiento.save()

        registrar_auditoria(
            usuario=usuario,
            accion="TRASLADO_INTERNO",
            tipo_entidad="Producto",
            id_entidad=str(producto.id),
            valores_anteriores={"ubicacion_id": ubicacion_origen.id, "ubicacion_nombre": ubicacion_origen.nombre},
            valores_nuevos={"ubicacion_id": ubicacion_destino.id, "ubicacion_nombre": ubicacion_destino.nombre},
            motivo=motivo or f"Traslado interno en {almacen.nombre}",
            resultado="OK",
            via_haku=False,
            ip_origen=ip_origen,
        )

    return existencia, movimiento


@transaccion_con_reintento
def registrar_traslado_entre_almacenes(
    *,
    producto: Producto,
    almacen_origen: Almacen,
    almacen_destino: Almacen,
    cantidad: int,
    usuario: Any,
    ubicacion_origen: Optional[Ubicacion] = None,
    ubicacion_destino: Optional[Ubicacion] = None,
    categoria_destino: Optional[Categoria] = None,
    motivo: str = "",
    observacion: str = "",
    clave_idempotencia: Optional[str] = None,
    ip_origen: Optional[str] = None,
) -> Tuple[Existencia, Existencia, Movimiento]:
    """Registra el traslado de productos entre dos almacenes (§6.2, §9.5).
    
    Descuenta de origen y añade en destino en una única transacción atómica.
    El usuario normal puede enviar desde su almacén asignado hacia cualquier destino activo.
    """
    # Valida permisos sobre el almacén origen (§6.2)
    _validar_acceso_almacen(usuario, almacen_origen)
    _validar_permiso_operativo(usuario, "movimientos.traslado_almacen")

    if almacen_origen.id == almacen_destino.id:
        raise ValidationError("El almacén origen y destino no pueden ser el mismo.")

    if not almacen_destino.activo or almacen_destino.eliminado:
        raise ValidationError("El almacén destino no se encuentra disponible o activo.")

    cantidad = int(cantidad)
    if cantidad <= 0:
        raise ValidationError("La cantidad a trasladar debe ser mayor a cero.")

    if ubicacion_origen and ubicacion_origen.almacen_id != almacen_origen.id:
        raise ValidationError("La ubicación origen no pertenece al almacén origen.")

    if ubicacion_destino and ubicacion_destino.almacen_id != almacen_destino.id:
        raise ValidationError("La ubicación destino no pertenece al almacén destino.")

    if not clave_idempotencia:
        clave_idempotencia = _generar_clave_idempotencia("TRAS-ENTRE", usuario, producto.id, f"{almacen_origen.id}-{almacen_destino.id}", cantidad)

    with transaction.atomic():
        try:
            existencia_origen = Existencia.objects.select_for_update().get(
                producto=producto,
                almacen=almacen_origen,
            )
        except Existencia.DoesNotExist:
            raise ValidationError(f"No hay existencias de '{producto.nombre}' en el almacén de origen '{almacen_origen.nombre}'.")

        if existencia_origen.stock_disponible < cantidad:
            raise ValidationError(
                f"Stock insuficiente en almacén origen. Disponible: {existencia_origen.stock_disponible}, solicitado: {cantidad}."
            )

        if cantidad < existencia_origen.stock_disponible:
            raise ValidationError(
                f"Regla de negocio: un producto solo puede estar en un único almacén. "
                f"Para trasladar '{producto.nombre}' a '{almacen_destino.nombre}', debe trasladar la totalidad del stock disponible ({existencia_origen.stock_disponible} unidades)."
            )

        cant_prest = existencia_origen.stock_en_prestamo
        stock_ant_origen = existencia_origen.stock_disponible
        stock_post_origen = 0

        # 1. Actualizar categoría y ubicación física al almacén destino antes de crear la existencia de destino
        if categoria_destino:
            if categoria_destino.almacen_principal_id != almacen_destino.id:
                raise ValidationError(f"La categoría '{categoria_destino.nombre}' no pertenece al almacén destino '{almacen_destino.nombre}'.")
            producto.categoria = categoria_destino
            producto.save(update_fields=["categoria"])
        elif producto.categoria and producto.categoria.almacen_principal_id != almacen_destino.id:
            cat_dest = Categoria.objects.filter(almacen_principal=almacen_destino, activo=True, eliminado=False).first()
            if not cat_dest:
                cat_dest = Categoria.objects.create(
                    nombre=f"{producto.categoria.nombre} ({almacen_destino.nombre})",
                    almacen_principal=almacen_destino,
                )
            producto.categoria = cat_dest
            producto.save(update_fields=["categoria"])

        if ubicacion_destino:
            producto.ubicacion = ubicacion_destino
            producto.save(update_fields=["ubicacion"])
        else:
            if producto.ubicacion and producto.ubicacion.almacen_id != almacen_destino.id:
                producto.ubicacion = None
                producto.save(update_fields=["ubicacion"])

        # 2. Eliminar la existencia en el almacén de origen para respetar la regla de 1 existencia única por producto
        existencia_origen.delete()
        existencia_origen.stock_disponible = 0

        # 3. Crear o actualizar existencia en almacén destino de forma atómica
        existencia_destino, _ = Existencia.objects.select_for_update().get_or_create(
            producto=producto,
            almacen=almacen_destino,
            defaults={"stock_disponible": 0, "stock_en_prestamo": 0},
        )

        stock_ant_destino = existencia_destino.stock_disponible
        if cantidad > 0:
            existencia_destino = stock.sumar_disponible(existencia_destino.id, cantidad)
        stock_post_destino = existencia_destino.stock_disponible

        if cant_prest > 0:
            Existencia.objects.filter(pk=existencia_destino.pk).update(
                stock_en_prestamo=models.F("stock_en_prestamo") + cant_prest
            )
            existencia_destino.refresh_from_db()

        # Reasignar préstamos abiertos al almacén destino
        Prestamo.objects.filter(producto=producto, almacen=almacen_origen).update(almacen=almacen_destino)

        # Limpiar cualquier otra existencia huérfana en 0 para este producto en otros almacenes
        Existencia.objects.filter(
            producto=producto, stock_disponible=0, stock_en_prestamo=0
        ).exclude(id=existencia_destino.id).delete()

        movimiento = Movimiento(
            clave_idempotencia=clave_idempotencia,
            tipo=Movimiento.Tipo.TRASLADO_ENTRE,
            producto=producto,
            cantidad=cantidad,
            almacen_origen=almacen_origen,
            almacen_destino=almacen_destino,
            ubicacion_origen=ubicacion_origen,
            ubicacion_destino=ubicacion_destino,
            usuario=usuario,
            stock_disponible_anterior=stock_ant_origen,
            stock_disponible_posterior=stock_post_origen,
            motivo=motivo or f"Traslado de {almacen_origen.nombre} a {almacen_destino.nombre}",
            observacion=observacion,
        )
        movimiento.full_clean()
        movimiento.save()

        registrar_auditoria(
            usuario=usuario,
            accion="TRASLADO_ENTRE_ALMACENES",
            tipo_entidad="Movimiento",
            id_entidad=str(movimiento.id),
            valores_anteriores={
                "almacen_origen_id": almacen_origen.id,
                "stock_origen_anterior": stock_ant_origen,
                "almacen_destino_id": almacen_destino.id,
                "stock_destino_anterior": stock_ant_destino,
            },
            valores_nuevos={
                "stock_origen_posterior": stock_post_origen,
                "stock_destino_posterior": stock_post_destino,
                "cantidad_trasladada": cantidad,
            },
            motivo=motivo or f"Traslado de {almacen_origen.nombre} a {almacen_destino.nombre}",
            resultado="OK",
            via_haku=False,
            ip_origen=ip_origen,
        )

    return existencia_origen, existencia_destino, movimiento


def registrar_conteo_fisico(
    *,
    producto: Producto,
    almacen: Almacen,
    cantidad_contada: int,
    usuario: Any,
    motivo: str = "",
    ip_origen: Optional[str] = None,
) -> ConteoFisico:
    """Registra un conteo físico preliminar sin mutar existencias (§9.7).
    
    Cualquier usuario con acceso al almacén puede registrar el conteo.
    Calcula la diferencia automáticamente (contado - stock_sistema).
    """
    _validar_acceso_almacen(usuario, almacen)

    cantidad_contada = int(cantidad_contada)
    if cantidad_contada < 0:
        raise ValidationError("La cantidad contada no puede ser negativa.")

    alm_esperado = producto.categoria.almacen_principal if (producto.categoria and producto.categoria.almacen_principal) else almacen
    if almacen and alm_esperado and almacen.pk != alm_esperado.pk:
        raise ValidationError(
            f"El producto '{producto.nombre}' pertenece a la categoría '{producto.categoria.nombre}', "
            f"asignada exclusivamente al almacén '{alm_esperado.nombre}'. No se puede registrar un conteo en '{almacen.nombre}'."
        )
    almacen = alm_esperado or almacen

    existencia, _ = Existencia.objects.get_or_create(
        producto=producto,
        almacen=almacen,
        defaults={"stock_disponible": 0, "stock_en_prestamo": 0},
    )
    stock_sistema = existencia.stock_disponible

    if ConteoFisico.objects.filter(producto=producto, estado=ConteoFisico.Estado.PENDIENTE).exists():
        raise ValidationError(
            f"Ya existe un conteo físico pendiente de aprobación para el producto '{producto.nombre}'."
        )

    conteo = ConteoFisico(
        producto=producto,
        almacen=almacen,
        cantidad_contada=cantidad_contada,
        stock_sistema=stock_sistema,
        diferencia=cantidad_contada - stock_sistema,
        usuario_conteo=usuario,
        motivo=motivo,
        estado=ConteoFisico.Estado.PENDIENTE,
    )
    conteo.full_clean()
    conteo.save()

    registrar_auditoria(
        usuario=usuario,
        accion="REGISTRAR_CONTEO",
        tipo_entidad="ConteoFisico",
        id_entidad=str(conteo.id),
        valores_anteriores={"stock_sistema": stock_sistema},
        valores_nuevos={"cantidad_contada": cantidad_contada, "diferencia": conteo.diferencia},
        motivo=motivo or f"Conteo físico preliminar en {almacen.nombre}",
        resultado="OK",
        via_haku=False,
        ip_origen=ip_origen,
    )

    return conteo


@transaccion_con_reintento
def confirmar_conteo_fisico(
    *,
    conteo: ConteoFisico,
    usuario: Any,
    ip_origen: Optional[str] = None,
) -> Tuple[ConteoFisico, Movimiento]:
    """Confirma un conteo físico y aplica el ajuste en las existencias (§6.2, §9.7).
    
    Permiso exclusivo de Administrador o Superusuario.
    """
    if not getattr(usuario, "es_administrador", False):
        raise PermissionDenied("Solo un Administrador o Superusuario puede confirmar y aplicar ajustes de conteo físico.")

    if conteo.estado != ConteoFisico.Estado.PENDIENTE:
        raise ValidationError(f"Este conteo físico ya fue resuelto previamente (estado actual: {conteo.get_estado_display()}).")

    with transaction.atomic():
        conteo_db = ConteoFisico.objects.select_for_update().get(pk=conteo.pk)
        if conteo_db.estado != ConteoFisico.Estado.PENDIENTE:
            raise ValidationError(f"Este conteo físico ya fue resuelto previamente (estado actual: {conteo_db.get_estado_display()}).")

        try:
            existencia = Existencia.objects.select_for_update().get(
                producto=conteo_db.producto,
                almacen=conteo_db.almacen,
            )
        except Existencia.DoesNotExist:
            alm_valido = (
                conteo_db.producto.categoria.almacen_principal
                if (conteo_db.producto.categoria and conteo_db.producto.categoria.almacen_principal)
                else conteo_db.almacen
            )
            existencia, _ = Existencia.objects.select_for_update().get_or_create(
                producto=conteo_db.producto,
                almacen=alm_valido,
                defaults={"stock_disponible": 0, "stock_en_prestamo": 0},
            )
            if conteo_db.almacen_id != alm_valido.id:
                conteo_db.almacen = alm_valido
                conteo_db.save(update_fields=["almacen"])

        stock_anterior = existencia.stock_disponible
        stock_posterior = conteo_db.cantidad_contada
        diferencia = stock_posterior - stock_anterior

        existencia = stock.ajustar_stock_conteo(existencia.id, diferencia)
        stock_posterior = existencia.stock_disponible

        clave_idempotencia = f"AJUSTE-CONTEO-{conteo_db.id}"

        movimiento = Movimiento(
            clave_idempotencia=clave_idempotencia,
            tipo=Movimiento.Tipo.AJUSTE_CONTEO,
            producto=conteo_db.producto,
            cantidad=abs(diferencia) if diferencia != 0 else 1,
            almacen_origen=conteo_db.almacen if diferencia < 0 else None,
            almacen_destino=conteo_db.almacen if diferencia >= 0 else None,
            usuario=usuario,
            stock_disponible_anterior=stock_anterior,
            stock_disponible_posterior=stock_posterior,
            motivo=f"Ajuste por conteo físico confirmado. Diferencia: {diferencia:+d}",
            observacion=conteo_db.motivo,
        )
        movimiento.full_clean()
        movimiento.save()

        conteo_db.estado = ConteoFisico.Estado.CONFIRMADO
        conteo_db.usuario_confirmacion = usuario
        conteo_db.fecha_confirmacion = timezone.now()
        conteo_db.movimiento_ajuste = movimiento
        conteo_db.diferencia = diferencia
        conteo_db.save()

        # Limpiar registros de notificación leída para este conteo ya confirmado y resuelto
        AlertaStockLeida.objects.filter(clave=f"conteo:{conteo_db.id}").delete()

        registrar_auditoria(
            usuario=usuario,
            accion="CONFIRMAR_CONTEO",
            tipo_entidad="ConteoFisico",
            id_entidad=str(conteo_db.id),
            valores_anteriores={"stock_disponible": stock_anterior},
            valores_nuevos={"stock_disponible": stock_posterior, "diferencia_aplicada": diferencia},
            motivo=f"Ajuste por conteo confirmado ({diferencia:+d} unid.)",
            resultado="OK",
            via_haku=False,
            ip_origen=ip_origen,
        )

    return conteo_db, movimiento


@transaccion_con_reintento
def rechazar_conteo_fisico(
    *,
    conteo: ConteoFisico,
    usuario: Any,
    motivo: str,
    ip_origen: Optional[str] = None,
) -> ConteoFisico:
    """Rechaza un conteo físico pendiente sin alterar existencias (§6.2, §9.7).
    
    Permiso exclusivo de Administrador o Superusuario.
    """
    if not getattr(usuario, "es_administrador", False):
        raise PermissionDenied("Solo un Administrador o Superusuario puede rechazar conteos físicos.")

    motivo = (motivo or "").strip()
    if not motivo:
        raise ValidationError("El motivo de rechazo del conteo físico es obligatorio.")

    if conteo.estado != ConteoFisico.Estado.PENDIENTE:
        raise ValidationError(f"Este conteo físico ya fue resuelto previamente (estado actual: {conteo.get_estado_display()}).")

    with transaction.atomic():
        conteo_db = ConteoFisico.objects.select_for_update().get(pk=conteo.pk)
        if conteo_db.estado != ConteoFisico.Estado.PENDIENTE:
            raise ValidationError(f"Este conteo físico ya fue resuelto previamente (estado actual: {conteo_db.get_estado_display()}).")

        conteo_db.estado = ConteoFisico.Estado.RECHAZADO
        conteo_db.motivo_rechazo = motivo
        conteo_db.usuario_confirmacion = usuario
        conteo_db.fecha_confirmacion = timezone.now()
        conteo_db.save(update_fields=["estado", "motivo_rechazo", "usuario_confirmacion", "fecha_confirmacion"])

        # Limpiar registros de notificación leída para este conteo ya resuelto
        AlertaStockLeida.objects.filter(clave=f"conteo:{conteo_db.id}").delete()

        registrar_auditoria(
            usuario=usuario,
            accion="RECHAZAR_CONTEO",
            tipo_entidad="ConteoFisico",
            id_entidad=str(conteo_db.id),
            valores_anteriores={"estado": ConteoFisico.Estado.PENDIENTE},
            valores_nuevos={"estado": ConteoFisico.Estado.RECHAZADO, "motivo_rechazo": motivo},
            motivo=f"Conteo físico rechazado: {motivo}",
            resultado="OK",
            via_haku=False,
            ip_origen=ip_origen,
        )

    return conteo_db


@transaccion_con_reintento
def revertir_movimiento(
    *,
    movimiento: Movimiento,
    usuario: Any,
    motivo: str,
    ip_origen: Optional[str] = None,
) -> Tuple[Movimiento, Movimiento]:
    """Registra una reversión compensatoria de un movimiento previo sin borrar datos (§9.6).
    
    Verifica que la reversión no cause stock negativo.
    """
    motivo = (motivo or "").strip()
    if not motivo:
        raise ValidationError("El motivo de la reversión es obligatorio.")

    if movimiento.estado == Movimiento.Estado.REVERTIDO:
        raise ValidationError("Este movimiento ya ha sido revertido.")

    if not getattr(usuario, "is_authenticated", False):
        raise PermissionDenied("Se requiere autenticación para revertir movimientos.")

    _validar_permiso_operativo(usuario, "movimientos.revertir")

    with transaction.atomic():
        mov_db = Movimiento.objects.select_for_update().get(pk=movimiento.pk)
        if mov_db.estado == Movimiento.Estado.REVERTIDO:
            raise ValidationError("Este movimiento ya ha sido revertido.")

        tipo = mov_db.tipo
        prod = mov_db.producto
        cant = mov_db.cantidad

        if not getattr(usuario, "es_administrador", False):
            if tipo in (
                Movimiento.Tipo.AJUSTE_CONTEO,
                Movimiento.Tipo.BAJA_PRESTAMO,
                Movimiento.Tipo.TRASLADO_ENTRE,
                Movimiento.Tipo.TRASLADO_INTERNO,
            ):
                raise PermissionDenied(
                    f"Solo un Administrador o Superusuario puede revertir movimientos de tipo {mov_db.get_tipo_display()}."
                )

        almacen_reversion_origen = None
        almacen_reversion_destino = None

        if tipo in (Movimiento.Tipo.ENTRADA, Movimiento.Tipo.MOVIMIENTO_INICIAL):
            alm = mov_db.almacen_destino
            _validar_acceso_almacen(usuario, alm)
            existencia = Existencia.objects.select_for_update().get(producto=prod, almacen=alm)
            if existencia.stock_disponible < cant:
                raise ValidationError("No es posible revertir: provocaría stock negativo en el almacén.")
            stock_ant = existencia.stock_disponible
            existencia = stock.descontar_disponible(existencia.id, cant)
            stock_post = existencia.stock_disponible
            almacen_reversion_origen = alm

        elif tipo == Movimiento.Tipo.SALIDA:
            alm = mov_db.almacen_origen
            _validar_acceso_almacen(usuario, alm)
            existencia = Existencia.objects.select_for_update().get(producto=prod, almacen=alm)
            stock_ant = existencia.stock_disponible
            existencia = stock.sumar_disponible(existencia.id, cant)
            stock_post = existencia.stock_disponible
            almacen_reversion_destino = alm

        elif tipo == Movimiento.Tipo.TRASLADO_ENTRE:
            _validar_acceso_almacen(usuario, mov_db.almacen_origen)
            ex_dest = Existencia.objects.select_for_update().get(producto=prod, almacen=mov_db.almacen_destino)
            if ex_dest.stock_disponible < cant:
                raise ValidationError("No es posible revertir traslado: provocaría stock negativo en el almacén destino.")
            stock_ant = ex_dest.stock_disponible
            ex_dest = stock.descontar_disponible(ex_dest.id, cant)
            stock_post = ex_dest.stock_disponible
            ex_orig = Existencia.objects.select_for_update().get(producto=prod, almacen=mov_db.almacen_origen)
            ex_orig = stock.sumar_disponible(ex_orig.id, cant)
            almacen_reversion_origen = mov_db.almacen_destino
            almacen_reversion_destino = mov_db.almacen_origen

        elif tipo == Movimiento.Tipo.AJUSTE_CONTEO:
            alm = mov_db.almacen_destino or mov_db.almacen_origen
            _validar_acceso_almacen(usuario, alm)
            existencia = Existencia.objects.select_for_update().get(producto=prod, almacen=alm)
            stock_ant = existencia.stock_disponible
            # Reversión delta-relativa (§1.C):
            delta = mov_db.stock_disponible_posterior - mov_db.stock_disponible_anterior
            if delta > 0:
                # El ajuste sumó unidades; revertir requiere restar delta al stock actual.
                if existencia.stock_disponible < delta:
                    raise ValidationError("No es posible revertir: provocaría stock negativo en el almacén.")
                existencia = stock.descontar_disponible(existencia.id, delta)
                almacen_reversion_origen = alm
            elif delta < 0:
                # El ajuste restó unidades; revertir requiere sumar abs(delta) al stock actual.
                existencia = stock.sumar_disponible(existencia.id, abs(delta))
                almacen_reversion_destino = alm
            stock_post = existencia.stock_disponible

        else:
            raise ValidationError(f"No se admite reversión directa automática para movimientos de tipo {tipo}.")

        mov_db.estado = Movimiento.Estado.REVERTIDO
        mov_db.save(update_fields=["estado"])

        clave_rev = f"REV-{mov_db.id}-{uuid.uuid4().hex[:8]}"
        movimiento_rev = Movimiento(
            clave_idempotencia=clave_rev,
            tipo=Movimiento.Tipo.REVERSION,
            producto=prod,
            cantidad=cant,
            almacen_origen=almacen_reversion_origen,
            almacen_destino=almacen_reversion_destino,
            movimiento_original=mov_db,
            usuario=usuario,
            stock_disponible_anterior=stock_ant,
            stock_disponible_posterior=stock_post,
            motivo=f"Reversión de movimiento {mov_db.get_tipo_display()}: {motivo}",
        )
        movimiento_rev.full_clean()
        movimiento_rev.save()

        registrar_auditoria(
            usuario=usuario,
            accion="REVERTIR_MOVIMIENTO",
            tipo_entidad="Movimiento",
            id_entidad=str(mov_db.id),
            valores_anteriores={"estado": Movimiento.Estado.CONFIRMADO},
            valores_nuevos={"estado": Movimiento.Estado.REVERTIDO, "movimiento_reversion_id": str(movimiento_rev.id)},
            motivo=motivo,
            resultado="OK",
            via_haku=False,
            ip_origen=ip_origen,
        )

    return mov_db, movimiento_rev



