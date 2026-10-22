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
    TipoCategoria,
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

    alm_existente = Almacen.objects.filter(nombre__iexact=nombre).first()
    if alm_existente:
        if not alm_existente.eliminado:
            raise ValidationError({"nombre": f"Ya existe un almacén activo con el nombre '{nombre}'."})
        else:
            with transaction.atomic():
                alm_existente.eliminado = False
                alm_existente.activo = True
                alm_existente.descripcion = (descripcion or "").strip()
                alm_existente.save()
                registrar_auditoria(
                    usuario=usuario,
                    accion="CREAR_ALMACEN",
                    tipo_entidad="Almacen",
                    id_entidad=str(alm_existente.id),
                    valores_anteriores={"eliminado": True, "activo": False},
                    valores_nuevos={"eliminado": False, "activo": True, "nombre": alm_existente.nombre},
                    motivo=f"Reactivación de almacén '{alm_existente.nombre}' previamente eliminado",
                    resultado="OK",
                    via_haku=False,
                    ip_origen=ip_origen,
                )
            return alm_existente

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


def eliminar_almacen(
    *,
    almacen: Almacen,
    usuario: Any,
    ip_origen: Optional[str] = None,
) -> None:
    """Elimina lógicamente un almacén si no tiene stock disponible ni ubicaciones con artículos."""
    if not getattr(usuario, "es_administrador", False):
        raise PermissionDenied("Solo los Administradores o el Superusuario pueden eliminar almacenes.")

    if almacen.existencias.filter(stock_disponible__gt=0).exists():
        raise NecesitaTraslado(
            f"El almacén '{almacen.nombre}' tiene stock disponible. Debe trasladar su inventario antes de eliminarlo.",
            entidad=almacen,
        )

    with transaction.atomic():
        almacen.activo = False
        almacen.eliminado = True
        almacen.save(update_fields=["activo", "eliminado"])

        registrar_auditoria(
            usuario=usuario,
            accion="ELIMINAR_ALMACEN",
            tipo_entidad="Almacen",
            id_entidad=str(almacen.id),
            valores_anteriores={"activo": True, "eliminado": False},
            valores_nuevos={"activo": False, "eliminado": True},
            motivo=f"Eliminación lógica de almacén '{almacen.nombre}'",
            resultado="OK",
            via_haku=False,
            ip_origen=ip_origen,
        )


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
        if not usuario.tiene_acceso_almacen(almacen):
            raise PermissionDenied("Solo puedes registrar ubicaciones en tus almacenes asignados.")

    nombre = (nombre or "").strip()
    if not nombre:
        raise ValidationError({"nombre": "El nombre de la ubicación es obligatorio."})

    if Ubicacion.objects.filter(almacen=almacen, nombre__iexact=nombre, eliminado=False).exists():
        raise ValidationError({"nombre": f"Ya existe una ubicación activa con el nombre '{nombre}' en este almacén."})

    ubicacion_del = Ubicacion.objects.filter(almacen=almacen, nombre__iexact=nombre, eliminado=True).first()
    if ubicacion_del:
        with transaction.atomic():
            ubicacion_del.eliminado = False
            ubicacion_del.activo = True
            ubicacion_del.descripcion = (descripcion or "").strip()
            ubicacion_del.save(update_fields=["eliminado", "activo", "descripcion"])

            registrar_auditoria(
                usuario=usuario,
                accion="CREAR_UBICACION",
                tipo_entidad="Ubicacion",
                id_entidad=str(ubicacion_del.id),
                valores_anteriores={"eliminado": True, "activo": False},
                valores_nuevos={
                    "nombre": ubicacion_del.nombre,
                    "almacen_id": almacen.id,
                    "almacen_nombre": almacen.nombre,
                    "descripcion": ubicacion_del.descripcion,
                },
                motivo=f"Recreación/reactivación de ubicación '{ubicacion_del.nombre}' en almacén '{almacen.nombre}'",
                resultado="OK",
                via_haku=False,
                ip_origen=ip_origen,
            )
        return ubicacion_del

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
    almacen: Optional[Almacen] = None,
    usuario: Any,
    ip_origen: Optional[str] = None,
) -> Ubicacion:
    """Actualiza una ubicación conservando el historial del nombre anterior en auditoría.
    
    Permite cambiar de almacén si la ubicación no tiene productos asignados.
    """
    if not getattr(usuario, "is_authenticated", False):
        raise PermissionDenied("Se requiere autenticación.")

    ubicacion_db = Ubicacion.objects.select_related("almacen").get(pk=ubicacion.pk)

    if getattr(usuario, "es_usuario_normal", False):
        if not usuario.tiene_acceso_almacen(ubicacion_db.almacen):
            raise PermissionDenied("No tienes permisos para modificar ubicaciones de otros almacenes.")
        if almacen and not usuario.tiene_acceso_almacen(almacen):
            raise PermissionDenied("No tienes permisos para asignar la ubicación al almacén seleccionado.")

    nombre = (nombre or "").strip()
    if not nombre:
        raise ValidationError({"nombre": "El nombre de la ubicación no puede estar vacío."})

    almacen_efectivo = almacen or ubicacion_db.almacen
    if almacen and almacen.id != ubicacion_db.almacen_id:
        if ProductoUbicacion.objects.filter(ubicacion=ubicacion_db, producto__archivado=False).exists():
            raise ValidationError({
                "almacen": f"No se puede cambiar el almacén de la ubicación '{ubicacion_db.nombre}' porque tiene productos asignados. Reasigne o traslade los productos primero."
            })

    # Verificar unicidad en el almacén destino/efectivo
    qs_unique = Ubicacion.objects.filter(almacen=almacen_efectivo, nombre__iexact=nombre, eliminado=False).exclude(pk=ubicacion_db.pk)
    if qs_unique.exists():
        raise ValidationError({"nombre": f"Ya existe una ubicación con el nombre '{nombre}' en el almacén '{almacen_efectivo.nombre}'."})

    valores_anteriores = {
        "nombre": ubicacion_db.nombre,
        "descripcion": ubicacion_db.descripcion,
        "almacen_id": ubicacion_db.almacen_id,
        "almacen_nombre": ubicacion_db.almacen.nombre,
    }

    with transaction.atomic():
        ubicacion_db.nombre = nombre
        ubicacion_db.descripcion = (descripcion or "").strip()
        if almacen and almacen.id != ubicacion_db.almacen_id:
            ubicacion_db.almacen = almacen
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
                "almacen_id": ubicacion_db.almacen_id,
                "almacen_nombre": ubicacion_db.almacen.nombre,
            },
            motivo=f"Actualización de ubicación '{ubicacion_db.nombre}' (almacén '{ubicacion_db.almacen.nombre}')",
            resultado="OK",
            via_haku=False,
            ip_origen=ip_origen,
        )

    return ubicacion_db


def reactivar_ubicacion(
    *,
    ubicacion: Ubicacion,
    usuario: Any,
    ip_origen: Optional[str] = None,
) -> Ubicacion:
    """Reactiva una ubicación física previamente inhabilitada."""
    if not getattr(usuario, "is_authenticated", False):
        raise PermissionDenied("Se requiere autenticación.")

    if getattr(usuario, "es_usuario_normal", False):
        if not usuario.tiene_acceso_almacen(ubicacion.almacen):
            raise PermissionDenied("No tienes permisos para reactivar ubicaciones de otros almacenes.")

    with transaction.atomic():
        ubicacion.activo = True
        ubicacion.save(update_fields=["activo"])

        registrar_auditoria(
            usuario=usuario,
            accion="REACTIVAR_UBICACION",
            tipo_entidad="Ubicacion",
            id_entidad=str(ubicacion.id),
            valores_anteriores={"activo": False},
            valores_nuevos={"activo": True},
            motivo=f"Reactivación de ubicación '{ubicacion.nombre}'",
            resultado="OK",
            via_haku=False,
            ip_origen=ip_origen,
        )
    return ubicacion


def eliminar_ubicacion(
    *,
    ubicacion: Ubicacion,
    usuario: Any,
    ip_origen: Optional[str] = None,
) -> None:
    """Elimina lógicamente una ubicación física si no tiene productos asignados."""
    if not getattr(usuario, "is_authenticated", False):
        raise PermissionDenied("Se requiere autenticación.")

    if getattr(usuario, "es_usuario_normal", False):
        if not usuario.tiene_acceso_almacen(ubicacion.almacen):
            raise PermissionDenied("No tienes permisos para eliminar ubicaciones de otros almacenes.")

    if ProductoUbicacion.objects.filter(ubicacion=ubicacion, producto__archivado=False).exists():
        raise NecesitaTraslado(
            f"La ubicación '{ubicacion.nombre}' tiene productos asignados. Reasígnelos antes de eliminarla.",
            entidad=ubicacion,
        )

    with transaction.atomic():
        ubicacion.activo = False
        ubicacion.eliminado = True
        ubicacion.save(update_fields=["activo", "eliminado"])

        registrar_auditoria(
            usuario=usuario,
            accion="ELIMINAR_UBICACION",
            tipo_entidad="Ubicacion",
            id_entidad=str(ubicacion.id),
            valores_anteriores={"activo": True, "eliminado": False},
            valores_nuevos={"activo": False, "eliminado": True},
            motivo=f"Eliminación lógica de ubicación '{ubicacion.nombre}'",
            resultado="OK",
            via_haku=False,
            ip_origen=ip_origen,
        )


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
        if not usuario.tiene_acceso_almacen(almacen_principal):
            raise PermissionDenied("Solo puedes registrar categorías en tus almacenes asignados.")

    nombre = (nombre or "").strip()
    if not nombre:
        raise ValidationError({"nombre": "El nombre de la categoría es obligatorio."})

    if Categoria.objects.filter(nombre__iexact=nombre, eliminado=False).exists():
        raise ValidationError({"nombre": f"Ya existe una categoría activa con el nombre '{nombre}'."})

    categoria_del = Categoria.objects.filter(nombre__iexact=nombre, eliminado=True).first()
    if categoria_del:
        with transaction.atomic():
            categoria_del.eliminado = False
            categoria_del.activo = True
            categoria_del.almacen_principal = almacen_principal
            categoria_del.save(update_fields=["eliminado", "activo", "almacen_principal"])

            registrar_auditoria(
                usuario=usuario,
                accion="CREAR_CATEGORIA",
                tipo_entidad="Categoria",
                id_entidad=str(categoria_del.id),
                valores_anteriores={"eliminado": True, "activo": False},
                valores_nuevos={
                    "nombre": categoria_del.nombre,
                    "almacen_principal_id": almacen_principal.id,
                    "almacen_principal_nombre": almacen_principal.nombre,
                    "prefijo_codigo": categoria_del.prefijo_codigo,
                },
                motivo=f"Recreación/reactivación de categoría '{categoria_del.nombre}' con almacén '{almacen_principal.nombre}'",
                resultado="OK",
                via_haku=False,
                ip_origen=ip_origen,
            )
        return categoria_del

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
        if not usuario.tiene_acceso_almacen(categoria_db.almacen_principal):
            raise PermissionDenied("No tienes permisos para modificar categorías de otros almacenes.")
        if not usuario.tiene_acceso_almacen(almacen_principal):
            raise PermissionDenied("Solo puedes asignar almacenes a los que tienes acceso.")

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


def crear_tipo_categoria(
    *,
    nombre: str,
    categoria: Categoria,
    usuario: Any,
    ip_origen: Optional[str] = None,
) -> TipoCategoria:
    """Crea un tipo opcional ligado a una categoría respetando permisos de almacén."""
    if not getattr(usuario, "is_authenticated", False):
        raise PermissionDenied("Se requiere autenticación para crear tipos de categoría.")

    if getattr(usuario, "es_usuario_normal", False):
        if not usuario.tiene_acceso_almacen(categoria.almacen_principal):
            raise PermissionDenied("Solo puedes registrar tipos en categorías de tus almacenes asignados.")

    nombre = (nombre or "").strip()
    if not nombre:
        raise ValidationError({"nombre": "El nombre del tipo es obligatorio."})

    tipo_existente = TipoCategoria.objects.filter(categoria=categoria, nombre__iexact=nombre).first()
    if tipo_existente:
        if tipo_existente.activo:
            raise ValidationError({"nombre": f"Ya existe el tipo activo '{nombre}' en la categoría '{categoria.nombre}'."})
        else:
            with transaction.atomic():
                tipo_existente.activo = True
                tipo_existente.save(update_fields=["activo"])
                registrar_auditoria(
                    usuario=usuario,
                    accion="CREAR_TIPO_CATEGORIA",
                    tipo_entidad="TipoCategoria",
                    id_entidad=str(tipo_existente.id),
                    valores_anteriores={"activo": False},
                    valores_nuevos={"activo": True, "nombre": tipo_existente.nombre},
                    motivo=f"Reactivación de tipo '{tipo_existente.nombre}' en categoría '{categoria.nombre}'",
                    resultado="OK",
                    via_haku=False,
                    ip_origen=ip_origen,
                )
            return tipo_existente

    with transaction.atomic():
        tipo = TipoCategoria(
            nombre=nombre,
            categoria=categoria,
            activo=True,
        )
        tipo.full_clean()
        tipo.save()

        registrar_auditoria(
            usuario=usuario,
            accion="CREAR_TIPO_CATEGORIA",
            tipo_entidad="TipoCategoria",
            id_entidad=str(tipo.id),
            valores_anteriores=None,
            valores_nuevos={
                "nombre": tipo.nombre,
                "categoria_id": categoria.id,
                "categoria_nombre": categoria.nombre,
            },
            motivo=f"Creación de tipo '{tipo.nombre}' en categoría '{categoria.nombre}'",
            resultado="OK",
            via_haku=False,
            ip_origen=ip_origen,
        )

    return tipo


def actualizar_tipo_categoria(
    *,
    tipo: TipoCategoria,
    nombre: str,
    usuario: Any,
    ip_origen: Optional[str] = None,
) -> TipoCategoria:
    """Actualiza el nombre de un tipo de categoría conservando los productos ligados."""
    if not getattr(usuario, "is_authenticated", False):
        raise PermissionDenied("Se requiere autenticación para editar tipos de categoría.")

    tipo_db = TipoCategoria.objects.select_related("categoria", "categoria__almacen_principal").get(pk=tipo.pk)

    if getattr(usuario, "es_usuario_normal", False):
        if not usuario.tiene_acceso_almacen(tipo_db.categoria.almacen_principal):
            raise PermissionDenied("No tienes permisos para modificar tipos de otros almacenes.")

    nombre = (nombre or "").strip()
    if not nombre:
        raise ValidationError({"nombre": "El nombre del tipo no puede estar vacío."})

    if TipoCategoria.objects.filter(categoria=tipo_db.categoria, nombre__iexact=nombre).exclude(pk=tipo_db.pk).exists():
        raise ValidationError({"nombre": f"Ya existe otro tipo con el nombre '{nombre}' en esta categoría."})

    valores_anteriores = {
        "nombre": tipo_db.nombre,
        "categoria_id": tipo_db.categoria_id,
    }

    with transaction.atomic():
        tipo_db.nombre = nombre
        tipo_db.full_clean()
        tipo_db.save()

        registrar_auditoria(
            usuario=usuario,
            accion="EDITAR_TIPO_CATEGORIA",
            tipo_entidad="TipoCategoria",
            id_entidad=str(tipo_db.id),
            valores_anteriores=valores_anteriores,
            valores_nuevos={
                "nombre": tipo_db.nombre,
                "categoria_id": tipo_db.categoria_id,
            },
            motivo=f"Actualización de tipo '{tipo_db.nombre}' (categoría '{tipo_db.categoria.nombre}')",
            resultado="OK",
            via_haku=False,
            ip_origen=ip_origen,
        )

    return tipo_db


def eliminar_tipo_categoria(
    *,
    tipo: TipoCategoria,
    usuario: Any,
    ip_origen: Optional[str] = None,
) -> None:
    """Elimina un tipo de categoría (los productos ligados quedan con tipo=None)."""
    if not getattr(usuario, "is_authenticated", False):
        raise PermissionDenied("Se requiere autenticación para eliminar tipos de categoría.")

    tipo_db = TipoCategoria.objects.select_related("categoria").get(pk=tipo.pk)

    if getattr(usuario, "es_usuario_normal", False):
        if not usuario.tiene_acceso_almacen(tipo_db.categoria.almacen_principal):
            raise PermissionDenied("No tienes permisos para eliminar tipos de otros almacenes.")

    valores_anteriores = {
        "nombre": tipo_db.nombre,
        "categoria_id": tipo_db.categoria_id,
        "categoria_nombre": tipo_db.categoria.nombre,
    }
    tipo_id = str(tipo_db.id)
    motivo = f"Eliminación de tipo '{tipo_db.nombre}' en categoría '{tipo_db.categoria.nombre}'"

    with transaction.atomic():
        tipo_db.delete()

        registrar_auditoria(
            usuario=usuario,
            accion="ELIMINAR_TIPO_CATEGORIA",
            tipo_entidad="TipoCategoria",
            id_entidad=tipo_id,
            valores_anteriores=valores_anteriores,
            valores_nuevos=None,
            motivo=motivo,
            resultado="OK",
            via_haku=False,
            ip_origen=ip_origen,
        )


def sincronizar_tipos_categoria(
    *,
    categoria: Categoria,
    nombres_tipos: List[str],
    usuario: Any,
    ip_origen: Optional[str] = None,
) -> List[TipoCategoria]:
    """Crea o mantiene los tipos indicados para una categoría (sin borrar existentes si no se pide)."""
    creados = []
    for raw in nombres_tipos:
        limpio = (raw or "").strip()
        if not limpio:
            continue
        existente = TipoCategoria.objects.filter(categoria=categoria, nombre__iexact=limpio).first()
        if existente:
            if not existente.activo:
                existente.activo = True
                existente.save(update_fields=["activo"])
            creados.append(existente)
        else:
            creados.append(
                crear_tipo_categoria(
                    nombre=limpio,
                    categoria=categoria,
                    usuario=usuario,
                    ip_origen=ip_origen,
                )
            )
    return creados


def reactivar_categoria(
    *,
    categoria: Categoria,
    usuario: Any,
    ip_origen: Optional[str] = None,
) -> Categoria:
    """Reactiva una categoría previamente inhabilitada."""
    if not getattr(usuario, "is_authenticated", False):
        raise PermissionDenied("Se requiere autenticación.")

    if getattr(usuario, "es_usuario_normal", False):
        if not usuario.tiene_acceso_almacen(categoria.almacen_principal):
            raise PermissionDenied("No tienes permisos para reactivar categorías de otros almacenes.")

    with transaction.atomic():
        categoria.activo = True
        categoria.save(update_fields=["activo"])

        registrar_auditoria(
            usuario=usuario,
            accion="REACTIVAR_CATEGORIA",
            tipo_entidad="Categoria",
            id_entidad=str(categoria.id),
            valores_anteriores={"activo": False},
            valores_nuevos={"activo": True},
            motivo=f"Reactivación de categoría '{categoria.nombre}'",
            resultado="OK",
            via_haku=False,
            ip_origen=ip_origen,
        )
    return categoria


def eliminar_categoria(
    *,
    categoria: Categoria,
    usuario: Any,
    ip_origen: Optional[str] = None,
) -> None:
    """Elimina lógicamente una categoría si no tiene productos activos."""
    if not getattr(usuario, "is_authenticated", False):
        raise PermissionDenied("Se requiere autenticación.")

    if getattr(usuario, "es_usuario_normal", False):
        if not usuario.tiene_acceso_almacen(categoria.almacen_principal):
            raise PermissionDenied("No tienes permisos para eliminar categorías de otros almacenes.")

    if categoria.productos.filter(archivado=False).exists():
        raise NecesitaTraslado(
            f"La categoría '{categoria.nombre}' tiene productos asociados. Debe trasladarlos a otra categoría antes de eliminarla.",
            entidad=categoria,
        )

    with transaction.atomic():
        categoria.activo = False
        categoria.eliminado = True
        categoria.save(update_fields=["activo", "eliminado"])

        registrar_auditoria(
            usuario=usuario,
            accion="ELIMINAR_CATEGORIA",
            tipo_entidad="Categoria",
            id_entidad=str(categoria.id),
            valores_anteriores={"activo": True, "eliminado": False},
            valores_nuevos={"activo": False, "eliminado": True},
            motivo=f"Eliminación lógica de categoría '{categoria.nombre}'",
            resultado="OK",
            via_haku=False,
            ip_origen=ip_origen,
        )


# ─────────────────────────────────────────────────────────────────────────────
# 4. PRODUCTOS
# ─────────────────────────────────────────────────────────────────────────────

def crear_producto(
    *,
    nombre: str,
    categoria: Categoria,
    tipo: Optional[TipoCategoria] = None,
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
        if not usuario.tiene_acceso_almacen(categoria.almacen_principal):
            raise PermissionDenied("Solo puedes registrar productos en categorías de tus almacenes asignados.")

    nombre = (nombre or "").strip()
    if not nombre:
        raise ValidationError({"nombre": "El nombre del producto es obligatorio."})

    if tipo is not None and tipo.categoria_id != categoria.id:
        raise ValidationError({"tipo": "El tipo seleccionado no pertenece a la categoría del producto."})

    codigo_barras = (codigo_barras or "").strip() or None
    if codigo_barras:
        prod_cb = Producto.objects.filter(codigo_barras=codigo_barras).first()
        if prod_cb:
            if not prod_cb.archivado:
                raise ValidationError({"codigo_barras": "Ya existe un producto activo registrado con este código de barras."})
            else:
                prod_cb.codigo_barras = None
                prod_cb.save(update_fields=["codigo_barras"])

    with transaction.atomic():
        producto = Producto(
            nombre=nombre,
            categoria=categoria,
            tipo=tipo,
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
                "tipo_id": tipo.id if tipo else None,
                "tipo_nombre": tipo.nombre if tipo else None,
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
    categoria: Optional[Categoria] = None,
    tipo: Optional[TipoCategoria] = None,
    actualizar_tipo: bool = False,
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

    producto_db = Producto.objects.select_related("categoria", "tipo").get(pk=producto.pk)

    if getattr(usuario, "es_usuario_normal", False):
        if not usuario.tiene_acceso_almacen(producto_db.categoria.almacen_principal):
            raise PermissionDenied("No tienes permisos para modificar productos de otros almacenes.")
        if categoria and not usuario.tiene_acceso_almacen(categoria.almacen_principal):
            raise PermissionDenied("Solo puedes asignar categorías pertenecientes a tus almacenes asignados.")

    categoria_efectiva = categoria or producto_db.categoria

    nombre = (nombre or "").strip()
    if not nombre:
        raise ValidationError({"nombre": "El nombre del producto no puede estar vacío."})

    if actualizar_tipo and tipo is not None and tipo.categoria_id != categoria_efectiva.id:
        raise ValidationError({"tipo": "El tipo seleccionado no pertenece a la categoría del producto."})

    codigo_barras = (codigo_barras or "").strip() or None
    if codigo_barras:
        prod_existente = Producto.objects.filter(codigo_barras=codigo_barras).exclude(id=producto_db.id).first()
        if prod_existente:
            if not prod_existente.archivado:
                raise ValidationError({"codigo_barras": "Ya existe otro producto activo con este código de barras."})
            else:
                prod_existente.codigo_barras = None
                prod_existente.save(update_fields=["codigo_barras"])

    valores_anteriores = {
        "nombre": producto_db.nombre,
        "categoria_id": producto_db.categoria_id,
        "tipo_id": producto_db.tipo_id,
        "stock_minimo": producto_db.stock_minimo,
        "codigo_barras": producto_db.codigo_barras,
        "nombre_empaque": producto_db.nombre_empaque,
        "unidades_por_empaque": producto_db.unidades_por_empaque,
        "descripcion": producto_db.descripcion,
    }

    ubicacion_ant = producto_db.ubicaciones.first()
    almacen_ant = producto_db.categoria.almacen_principal
    almacen_nuevo = categoria.almacen_principal if categoria is not None else almacen_ant

    with transaction.atomic():
        producto_db.nombre = nombre
        if categoria is not None:
            producto_db.categoria = categoria
            Existencia.objects.get_or_create(
                producto=producto_db,
                almacen=categoria.almacen_principal,
                defaults={"stock_disponible": 0, "stock_en_prestamo": 0},
            )
        if actualizar_tipo:
            producto_db.tipo = tipo
        producto_db.stock_minimo = max(0, int(stock_minimo or 0))
        producto_db.codigo_barras = codigo_barras
        producto_db.nombre_empaque = (nombre_empaque or "").strip()
        producto_db.unidades_por_empaque = unidades_por_empaque if unidades_por_empaque and unidades_por_empaque > 0 else None
        producto_db.descripcion = (descripcion or "").strip()
        producto_db.full_clean()
        producto_db.save()

        ubicacion_nueva = None
        if ubicaciones is not None:
            ProductoUbicacion.objects.filter(producto=producto_db).exclude(ubicacion_id__in=ubicaciones).delete()
            for uid in ubicaciones:
                ProductoUbicacion.objects.get_or_create(producto=producto_db, ubicacion_id=uid)
            ubicacion_nueva = Ubicacion.objects.filter(id__in=ubicaciones).first()

        # Registro automático de traslado en caso de cambio de almacén o ubicación (§Punto 12)
        if almacen_nuevo.id != almacen_ant.id:
            from inventario.servicios.operaciones import registrar_traslado_entre_almacenes
            ex_origen = Existencia.objects.filter(producto=producto_db, almacen=almacen_ant).first()
            cant_traslado = ex_origen.stock_disponible if ex_origen and ex_origen.stock_disponible > 0 else 0
            if cant_traslado > 0:
                registrar_traslado_entre_almacenes(
                    producto=producto_db,
                    almacen_origen=almacen_ant,
                    almacen_destino=almacen_nuevo,
                    cantidad=cant_traslado,
                    usuario=usuario,
                    ubicacion_origen=ubicacion_ant,
                    ubicacion_destino=ubicacion_nueva,
                    motivo="Traslado por cambio de categoría/almacén en edición de producto",
                    ip_origen=ip_origen,
                )
            else:
                Existencia.objects.get_or_create(
                    producto=producto_db,
                    almacen=almacen_nuevo,
                    defaults={"stock_disponible": 0, "stock_en_prestamo": 0},
                )
        elif ubicaciones is not None and ubicacion_nueva and ubicacion_ant and ubicacion_nueva.id != ubicacion_ant.id:
            from inventario.servicios.operaciones import registrar_traslado_interno
            registrar_traslado_interno(
                producto=producto_db,
                almacen=almacen_ant,
                ubicacion_origen=ubicacion_ant,
                ubicacion_destino=ubicacion_nueva,
                usuario=usuario,
                motivo="Traslado interno por reubicación física en edición de producto",
                ip_origen=ip_origen,
            )

        registrar_auditoria(
            usuario=usuario,
            accion="EDITAR_PRODUCTO",
            tipo_entidad="Producto",
            id_entidad=str(producto_db.id),
            valores_anteriores=valores_anteriores,
            valores_nuevos={
                "nombre": producto_db.nombre,
                "categoria_id": producto_db.categoria_id,
                "tipo_id": producto_db.tipo_id,
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


def toggle_activo_producto(
    *,
    producto: Producto,
    usuario: Any,
    ip_origen: Optional[str] = None,
) -> Producto:
    """Inhabilita o reactiva un producto sin archivarlo."""
    if not getattr(usuario, "is_authenticated", False):
        raise PermissionDenied("Se requiere autenticación.")

    if getattr(usuario, "es_usuario_normal", False):
        if not usuario.tiene_acceso_almacen(producto.categoria.almacen_principal):
            raise PermissionDenied("No tienes permisos para cambiar el estado de productos de otros almacenes.")

    anterior = producto.activo
    nuevo = not anterior

    with transaction.atomic():
        producto.activo = nuevo
        producto.save(update_fields=["activo"])

        registrar_auditoria(
            usuario=usuario,
            accion="TOGGLE_ACTIVO_PRODUCTO",
            tipo_entidad="Producto",
            id_entidad=str(producto.id),
            valores_anteriores={"activo": anterior},
            valores_nuevos={"activo": nuevo},
            motivo=f"{'Reactivación' if nuevo else 'Inhabilitación'} del producto '{producto.codigo_interno} — {producto.nombre}'",
            resultado="OK",
            via_haku=False,
            ip_origen=ip_origen,
        )

    return producto


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
