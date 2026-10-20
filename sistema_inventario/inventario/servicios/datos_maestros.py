"""Servicios de lógica de negocio para datos maestros de inventario.

Gestiona Almacenes, Ubicaciones, Categorías y Productos con validaciones,
autorización por rol/almacén y registro inmutable de auditoría.
"""

from typing import Any, Dict, List, Optional
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from inventario.auditoria import registrar_auditoria
from inventario.models import (
    Almacen,
    Auditoria,
    Categoria,
    ConteoFisico,
    Existencia,
    Movimiento,
    Prestamo,
    Producto,
    ProductoUbicacion,
    Ubicacion,
)
from inventario.servicios import NecesitaTraslado


# ─────────────────────────────────────────────────────────────────────────────
# 1. ALMACENES
# ─────────────────────────────────────────────────────────────────────────────

def crear_almacen(
    *,
    nombre: str,
    descripcion: str = "",
    usuario: Any,
    ip_origen: Optional[str] = None,
) -> Almacen:
    """Crea un nuevo almacén validando permisos de administración y unicidad."""
    if not getattr(usuario, "es_administrador", False):
        raise PermissionDenied("Solo los Administradores o el Superusuario pueden crear almacenes.")

    nombre = (nombre or "").strip()
    if not nombre:
        raise ValidationError({"nombre": "El nombre del almacén es obligatorio."})

    with transaction.atomic():
        almacen = Almacen(
            nombre=nombre,
            descripcion=(descripcion or "").strip(),
            activo=True,
            eliminado=False,
        )
        almacen.full_clean()
        almacen.save()

        registrar_auditoria(
            usuario=usuario,
            accion="CREAR_ALMACEN",
            tipo_entidad="Almacen",
            id_entidad=str(almacen.id),
            valores_anteriores=None,
            valores_nuevos={
                "nombre": almacen.nombre,
                "descripcion": almacen.descripcion,
                "activo": True,
            },
            motivo=f"Creación de almacén '{almacen.nombre}'",
            resultado="OK",
            via_haku=False,
            ip_origen=ip_origen,
        )

    return almacen


def actualizar_almacen(
    *,
    almacen: Almacen,
    nombre: str,
    descripcion: str,
    usuario: Any,
    ip_origen: Optional[str] = None,
) -> Almacen:
    """Actualiza datos básicos de un almacén existente registrando auditoría."""
    if not getattr(usuario, "es_administrador", False):
        raise PermissionDenied("Solo los Administradores o el Superusuario pueden editar almacenes.")

    nombre = (nombre or "").strip()
    if not nombre:
        raise ValidationError({"nombre": "El nombre del almacén no puede estar vacío."})

    almacen_db = Almacen.objects.get(pk=almacen.pk)
    valores_anteriores = {
        "nombre": almacen_db.nombre,
        "descripcion": almacen_db.descripcion,
    }

    with transaction.atomic():
        almacen_db.nombre = nombre
        almacen_db.descripcion = (descripcion or "").strip()
        almacen_db.full_clean()
        almacen_db.save()

        registrar_auditoria(
            usuario=usuario,
            accion="EDITAR_ALMACEN",
            tipo_entidad="Almacen",
            id_entidad=str(almacen_db.id),
            valores_anteriores=valores_anteriores,
            valores_nuevos={
                "nombre": almacen_db.nombre,
                "descripcion": almacen_db.descripcion,
            },
            motivo=f"Actualización de almacén '{almacen_db.nombre}'",
            resultado="OK",
            via_haku=False,
            ip_origen=ip_origen,
        )

    return almacen_db


def reactivar_almacen(
    *,
    almacen: Almacen,
    usuario: Any,
    ip_origen: Optional[str] = None,
) -> Almacen:
    """Reactiva un almacén previamente desactivado."""
    if not getattr(usuario, "es_administrador", False):
        raise PermissionDenied("Solo los Administradores o el Superusuario pueden reactivar almacenes.")

    with transaction.atomic():
        almacen.activo = True
        almacen.save(update_fields=["activo"])

        registrar_auditoria(
            usuario=usuario,
            accion="REACTIVAR_ALMACEN",
            tipo_entidad="Almacen",
            id_entidad=str(almacen.id),
            valores_anteriores={"activo": False},
            valores_nuevos={"activo": True},
            motivo=f"Reactivación de almacén '{almacen.nombre}'",
            resultado="OK",
            via_haku=False,
            ip_origen=ip_origen,
        )

    return almacen


# ─────────────────────────────────────────────────────────────────────────────
# 2. UBICACIONES
# ─────────────────────────────────────────────────────────────────────────────

def crear_ubicacion(
    *,
    nombre: str,
    almacen: Almacen,
    descripcion: str = "",
    usuario: Any,
    ip_origen: Optional[str] = None,
) -> Ubicacion:
    """Crea una nueva ubicación dentro de un almacén respetando permisos."""
    if not getattr(usuario, "is_authenticated", False):
        raise PermissionDenied("Se requiere autenticación para crear ubicaciones.")

    if getattr(usuario, "es_usuario_normal", False):
        if not usuario.almacen_asignado or usuario.almacen_asignado_id != almacen.id:
            raise PermissionDenied("Solo puedes registrar ubicaciones en tu almacén asignado.")

    nombre = (nombre or "").strip()
    if not nombre:
        raise ValidationError({"nombre": "El nombre de la ubicación es obligatorio."})

    with transaction.atomic():
        ubicacion = Ubicacion(
            nombre=nombre,
            almacen=almacen,
            descripcion=(descripcion or "").strip(),
            activo=True,
            eliminado=False,
        )
        ubicacion.full_clean()
        ubicacion.save()

        registrar_auditoria(
            usuario=usuario,
            accion="CREAR_UBICACION",
            tipo_entidad="Ubicacion",
            id_entidad=str(ubicacion.id),
            valores_anteriores=None,
            valores_nuevos={
                "nombre": ubicacion.nombre,
                "almacen_id": almacen.id,
                "almacen_nombre": almacen.nombre,
                "descripcion": ubicacion.descripcion,
            },
            motivo=f"Creación de ubicación '{ubicacion.nombre}' en almacén '{almacen.nombre}'",
            resultado="OK",
            via_haku=False,
            ip_origen=ip_origen,
        )

    return ubicacion


def actualizar_ubicacion(
    *,
    ubicacion: Ubicacion,
    nombre: str,
    descripcion: str,
    usuario: Any,
    ip_origen: Optional[str] = None,
) -> Ubicacion:
    """Actualiza una ubicación conservando el historial del nombre anterior en auditoría."""
    if not getattr(usuario, "is_authenticated", False):
        raise PermissionDenied("Se requiere autenticación.")

    ubicacion_db = Ubicacion.objects.select_related("almacen").get(pk=ubicacion.pk)

    if getattr(usuario, "es_usuario_normal", False):
        if not usuario.almacen_asignado or usuario.almacen_asignado_id != ubicacion_db.almacen_id:
            raise PermissionDenied("No tienes permisos para modificar ubicaciones de otros almacenes.")

    nombre = (nombre or "").strip()
    if not nombre:
        raise ValidationError({"nombre": "El nombre de la ubicación no puede estar vacío."})

    valores_anteriores = {
        "nombre": ubicacion_db.nombre,
        "descripcion": ubicacion_db.descripcion,
    }

    with transaction.atomic():
        ubicacion_db.nombre = nombre
        ubicacion_db.descripcion = (descripcion or "").strip()
        ubicacion_db.full_clean()
        ubicacion_db.save()

        registrar_auditoria(
            usuario=usuario,
            accion="EDITAR_UBICACION",
            tipo_entidad="Ubicacion",
            id_entidad=str(ubicacion_db.id),
            valores_anteriores=valores_anteriores,
            valores_nuevos={
                "nombre": ubicacion_db.nombre,
                "descripcion": ubicacion_db.descripcion,
            },
            motivo=f"Actualización de ubicación '{ubicacion_db.nombre}' (almacén '{ubicacion_db.almacen.nombre}')",
            resultado="OK",
            via_haku=False,
            ip_origen=ip_origen,
        )

    return ubicacion_db


# ─────────────────────────────────────────────────────────────────────────────
# 3. CATEGORÍAS
# ─────────────────────────────────────────────────────────────────────────────

def crear_categoria(
    *,
    nombre: str,
    almacen_principal: Almacen,
    usuario: Any,
    ip_origen: Optional[str] = None,
) -> Categoria:
    """Crea una nueva categoría asignada a un almacén principal obligatorio.
    
    Valida permisos por rol: usuarios normales solo pueden crear en su almacén.
    """
    if not getattr(usuario, "is_authenticated", False):
        raise PermissionDenied("Se requiere autenticación para crear categorías.")

    if getattr(usuario, "es_usuario_normal", False):
        if not usuario.almacen_asignado or usuario.almacen_asignado_id != almacen_principal.id:
            raise PermissionDenied("Solo puedes registrar categorías en tu almacén asignado.")

    nombre = (nombre or "").strip()
    if not nombre:
        raise ValidationError({"nombre": "El nombre de la categoría es obligatorio."})

    with transaction.atomic():
        categoria = Categoria(
            nombre=nombre,
            almacen_principal=almacen_principal,
            activo=True,
            eliminado=False,
        )
        categoria.full_clean()
        categoria.save()

        registrar_auditoria(
            usuario=usuario,
            accion="CREAR_CATEGORIA",
            tipo_entidad="Categoria",
            id_entidad=str(categoria.id),
            valores_anteriores=None,
            valores_nuevos={
                "nombre": categoria.nombre,
                "almacen_principal_id": almacen_principal.id,
                "almacen_principal_nombre": almacen_principal.nombre,
                "prefijo_codigo": categoria.prefijo_codigo,
            },
            motivo=f"Creación de categoría '{categoria.nombre}' con almacén principal '{almacen_principal.nombre}'",
            resultado="OK",
            via_haku=False,
            ip_origen=ip_origen,
        )

    return categoria


def actualizar_categoria(
    *,
    categoria: Categoria,
    nombre: str,
    almacen_principal: Almacen,
    usuario: Any,
    ip_origen: Optional[str] = None,
) -> Categoria:
    """Actualiza una categoría existente.
    
    Si el almacén principal cambia y la categoría ya cuenta con productos,
    lanza NecesitaTraslado para exigir el flujo de traslado estructural (§7.3).
    """
    if not getattr(usuario, "is_authenticated", False):
        raise PermissionDenied("Se requiere autenticación para modificar categorías.")

    categoria_db = Categoria.objects.select_related("almacen_principal").get(pk=categoria.pk)

    if getattr(usuario, "es_usuario_normal", False):
        if not usuario.almacen_asignado or usuario.almacen_asignado_id != categoria_db.almacen_principal_id:
            raise PermissionDenied("No tienes permisos para modificar categorías de otros almacenes.")
        if usuario.almacen_asignado_id != almacen_principal.id:
            raise PermissionDenied("No puedes cambiar el almacén principal a otro distinto al asignado.")

    nombre = (nombre or "").strip()
    if not nombre:
        raise ValidationError({"nombre": "El nombre de la categoría no puede estar vacío."})

    # Verificar si intenta cambiar el almacén principal respecto al guardado en base de datos
    if categoria_db.almacen_principal_id != almacen_principal.id:
        # Si tiene productos asociados no archivados, no se puede cambiar directamente (§7.3)
        if categoria_db.productos.filter(archivado=False).exists():
            raise NecesitaTraslado(
                f"La categoría '{categoria_db.nombre}' tiene productos asociados. "
                f"Cambiar el almacén principal a '{almacen_principal.nombre}' requiere trasladar los productos.",
                entidad=categoria_db,
                destino=almacen_principal,
            )

    valores_anteriores = {
        "nombre": categoria_db.nombre,
        "almacen_principal_id": categoria_db.almacen_principal_id,
        "almacen_principal_nombre": categoria_db.almacen_principal.nombre,
    }

    with transaction.atomic():
        categoria_db.nombre = nombre
        categoria_db.almacen_principal = almacen_principal
        categoria_db.full_clean()
        categoria_db.save()

        registrar_auditoria(
            usuario=usuario,
            accion="EDITAR_CATEGORIA",
            tipo_entidad="Categoria",
            id_entidad=str(categoria_db.id),
            valores_anteriores=valores_anteriores,
            valores_nuevos={
                "nombre": categoria_db.nombre,
                "almacen_principal_id": almacen_principal.id,
                "almacen_principal_nombre": almacen_principal.nombre,
            },
            motivo=f"Actualización de categoría '{categoria_db.nombre}'",
            resultado="OK",
            via_haku=False,
            ip_origen=ip_origen,
        )

    return categoria_db


# ─────────────────────────────────────────────────────────────────────────────
# 4. PRODUCTOS
# ─────────────────────────────────────────────────────────────────────────────

def crear_producto(
    *,
    nombre: str,
    categoria: Categoria,
    stock_inicial: int = 0,
    stock_minimo: int = 0,
    codigo_barras: str = "",
    nombre_empaque: str = "",
    unidades_por_empaque: Optional[int] = None,
    descripcion: str = "",
    ubicaciones: Optional[List[int]] = None,
    usuario: Any,
    ip_origen: Optional[str] = None,
) -> Producto:
    """Crea un nuevo producto en el catálogo.
    
    Genera automáticamente el código interno inmutable (XXX-0001).
    Si stock_inicial > 0, inicializa existencias y genera el movimiento inicial.
    """
    if not getattr(usuario, "is_authenticated", False):
        raise PermissionDenied("Se requiere autenticación para crear productos.")

    if getattr(usuario, "es_usuario_normal", False):
        if not usuario.almacen_asignado or categoria.almacen_principal_id != usuario.almacen_asignado_id:
            raise PermissionDenied("Solo puedes registrar productos en categorías de tu almacén asignado.")

    nombre = (nombre or "").strip()
    if not nombre:
        raise ValidationError({"nombre": "El nombre del producto es obligatorio."})

    codigo_barras = (codigo_barras or "").strip() or None
    if codigo_barras:
        if Producto.objects.filter(codigo_barras=codigo_barras).exists():
            raise ValidationError({"codigo_barras": "Ya existe un producto registrado con este código de barras."})

    with transaction.atomic():
        producto = Producto(
            nombre=nombre,
            categoria=categoria,
            stock_inicial=max(0, int(stock_inicial or 0)),
            stock_minimo=max(0, int(stock_minimo or 0)),
            codigo_barras=codigo_barras,
            nombre_empaque=(nombre_empaque or "").strip(),
            unidades_por_empaque=unidades_por_empaque if unidades_por_empaque and unidades_por_empaque > 0 else None,
            descripcion=(descripcion or "").strip(),
            activo=True,
            archivado=False,
        )
        producto.full_clean()
        producto.save()

        if ubicaciones:
            for uid in ubicaciones:
                ProductoUbicacion.objects.get_or_create(producto=producto, ubicacion_id=uid)

        # Crear existencia en almacén principal por defecto
        Existencia.objects.get_or_create(
            producto=producto,
            almacen=categoria.almacen_principal,
            defaults={"stock_disponible": 0, "stock_en_prestamo": 0},
        )

        if producto.stock_inicial > 0:
            from inventario.servicios.stock import inicializar_stock
            inicializar_stock(
                producto=producto,
                almacen=categoria.almacen_principal,
                cantidad=producto.stock_inicial,
                usuario=usuario,
                ip_origen=ip_origen,
            )

        registrar_auditoria(
            usuario=usuario,
            accion="CREAR_PRODUCTO",
            tipo_entidad="Producto",
            id_entidad=str(producto.id),
            valores_anteriores=None,
            valores_nuevos={
                "codigo_interno": producto.codigo_interno,
                "nombre": producto.nombre,
                "categoria_id": categoria.id,
                "stock_inicial": producto.stock_inicial,
                "stock_minimo": producto.stock_minimo,
                "codigo_barras": producto.codigo_barras,
            },
            motivo=f"Creación de producto '{producto.codigo_interno} — {producto.nombre}'",
            resultado="OK",
            via_haku=False,
            ip_origen=ip_origen,
        )

    return producto


def actualizar_producto(
    *,
    producto: Producto,
    nombre: str,
    stock_minimo: int,
    codigo_barras: str = "",
    nombre_empaque: str = "",
    unidades_por_empaque: Optional[int] = None,
    descripcion: str = "",
    ubicaciones: Optional[List[int]] = None,
    usuario: Any,
    ip_origen: Optional[str] = None,
) -> Producto:
    """Actualiza datos operativos de un producto.
    
    Nota: codigo_interno y stock_inicial son inmutables por especificación (§8.1).
    """
    if not getattr(usuario, "is_authenticated", False):
        raise PermissionDenied("Se requiere autenticación para modificar productos.")

    producto_db = Producto.objects.select_related("categoria").get(pk=producto.pk)

    if getattr(usuario, "es_usuario_normal", False):
        if not usuario.almacen_asignado or producto_db.categoria.almacen_principal_id != usuario.almacen_asignado_id:
            raise PermissionDenied("No tienes permisos para modificar productos de otros almacenes.")

    nombre = (nombre or "").strip()
    if not nombre:
        raise ValidationError({"nombre": "El nombre del producto no puede estar vacío."})

    codigo_barras = (codigo_barras or "").strip() or None
    if codigo_barras:
        if Producto.objects.filter(codigo_barras=codigo_barras).exclude(id=producto_db.id).exists():
            raise ValidationError({"codigo_barras": "Ya existe otro producto con este código de barras."})

    valores_anteriores = {
        "nombre": producto_db.nombre,
        "stock_minimo": producto_db.stock_minimo,
        "codigo_barras": producto_db.codigo_barras,
        "nombre_empaque": producto_db.nombre_empaque,
        "unidades_por_empaque": producto_db.unidades_por_empaque,
        "descripcion": producto_db.descripcion,
    }

    with transaction.atomic():
        producto_db.nombre = nombre
        producto_db.stock_minimo = max(0, int(stock_minimo or 0))
        producto_db.codigo_barras = codigo_barras
        producto_db.nombre_empaque = (nombre_empaque or "").strip()
        producto_db.unidades_por_empaque = unidades_por_empaque if unidades_por_empaque and unidades_por_empaque > 0 else None
        producto_db.descripcion = (descripcion or "").strip()
        producto_db.full_clean()
        producto_db.save()

        if ubicaciones is not None:
            ProductoUbicacion.objects.filter(producto=producto_db).exclude(ubicacion_id__in=ubicaciones).delete()
            for uid in ubicaciones:
                ProductoUbicacion.objects.get_or_create(producto=producto_db, ubicacion_id=uid)

        registrar_auditoria(
            usuario=usuario,
            accion="EDITAR_PRODUCTO",
            tipo_entidad="Producto",
            id_entidad=str(producto_db.id),
            valores_anteriores=valores_anteriores,
            valores_nuevos={
                "nombre": producto_db.nombre,
                "stock_minimo": producto_db.stock_minimo,
                "codigo_barras": producto_db.codigo_barras,
                "nombre_empaque": producto_db.nombre_empaque,
                "unidades_por_empaque": producto_db.unidades_por_empaque,
                "descripcion": producto_db.descripcion,
            },
            motivo=f"Actualización de producto '{producto_db.codigo_interno} — {producto_db.nombre}'",
            resultado="OK",
            via_haku=False,
            ip_origen=ip_origen,
        )

    return producto_db


def archivar_producto(
    *,
    producto: Producto,
    usuario: Any,
    ip_origen: Optional[str] = None,
) -> None:
    """Archiva un producto ocultándolo de listas operativas pero conservando historial.
    
    Solo Administradores y Superusuarios (§8.1).
    """
    if not getattr(usuario, "es_administrador", False):
        raise PermissionDenied("Solo los Administradores o el Superusuario pueden archivar productos.")

    with transaction.atomic():
        producto.archivado = True
        producto.activo = False
        producto.save(update_fields=["archivado", "activo"])

        registrar_auditoria(
            usuario=usuario,
            accion="ARCHIVAR_PRODUCTO",
            tipo_entidad="Producto",
            id_entidad=str(producto.id),
            valores_anteriores={"archivado": False, "activo": True},
            valores_nuevos={"archivado": True, "activo": False},
            motivo=f"Archivo del producto '{producto.codigo_interno} — {producto.nombre}'",
            resultado="OK",
            via_haku=False,
            ip_origen=ip_origen,
        )


def fusionar_productos(
    *,
    producto_principal: Producto,
    producto_duplicado: Producto,
    usuario: Any,
    ip_origen: Optional[str] = None,
) -> Producto:
    """Fusiona un producto duplicado en el producto principal (§8.3).
    
    1. Suma existencias por almacén.
    2. Reasigna todos los movimientos y préstamos.
    3. Unifica ubicaciones físicas.
    4. Archiva el producto duplicado y deja su existencia en 0.
    5. Registra auditoría exhaustiva.
    """
    if not getattr(usuario, "es_administrador", False):
        raise PermissionDenied("Solo los Administradores o el Superusuario pueden fusionar productos duplicados.")

    if producto_principal.id == producto_duplicado.id:
        raise ValidationError("No se puede fusionar un producto consigo mismo.")

    with transaction.atomic():
        prod_pri = Producto.objects.select_for_update().get(pk=producto_principal.pk)
        prod_dup = Producto.objects.select_for_update().get(pk=producto_duplicado.pk)

        # 1. Sumar existencias por cada almacén donde esté el duplicado
        for ex_dup in prod_dup.existencias.select_for_update():
            cant_disp = ex_dup.stock_disponible
            cant_prest = ex_dup.stock_en_prestamo

            ex_pri, _ = Existencia.objects.select_for_update().get_or_create(
                producto=prod_pri,
                almacen=ex_dup.almacen,
                defaults={"stock_disponible": 0, "stock_en_prestamo": 0},
            )

            ex_pri.stock_disponible += cant_disp
            ex_pri.stock_en_prestamo += cant_prest
            ex_pri.save()

            ex_dup.stock_disponible = 0
            ex_dup.stock_en_prestamo = 0
            ex_dup.save()

        # 2. Reasignar movimientos
        Movimiento.objects.filter(producto=prod_dup).update(producto=prod_pri)

        # 3. Reasignar préstamos
        Prestamo.objects.filter(producto=prod_dup).update(producto=prod_pri)

        # 4. Reasignar conteos físicos si los hubiera
        ConteoFisico.objects.filter(producto=prod_dup).update(producto=prod_pri)

        # 5. Unificar ubicaciones físicas
        for pu_dup in prod_dup.producto_ubicaciones.all():
            ProductoUbicacion.objects.get_or_create(
                producto=prod_pri,
                ubicacion=pu_dup.ubicacion,
            )
        prod_dup.producto_ubicaciones.all().delete()

        # 6. Archivar producto duplicado
        prod_dup.archivado = True
        prod_dup.activo = False
        prod_dup.save(update_fields=["archivado", "activo"])

        # 7. Auditoría
        registrar_auditoria(
            usuario=usuario,
            accion="FUSIONAR_PRODUCTOS",
            tipo_entidad="Producto",
            id_entidad=str(prod_pri.id),
            valores_anteriores={
                "duplicado_id": prod_dup.id,
                "duplicado_codigo": prod_dup.codigo_interno,
                "duplicado_nombre": prod_dup.nombre,
            },
            valores_nuevos={
                "principal_id": prod_pri.id,
                "principal_codigo": prod_pri.codigo_interno,
            },
            motivo=f"Fusión de producto duplicado '{prod_dup.codigo_interno} — {prod_dup.nombre}' en '{prod_pri.codigo_interno} — {prod_pri.nombre}'",
            resultado="OK",
            via_haku=False,
            ip_origen=ip_origen,
        )

    return prod_pri
