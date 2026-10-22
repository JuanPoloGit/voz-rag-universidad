"""
Capa de permisos y autorización por rol y almacén — Inventario AudacIA.

Reglas clave de la especificación (§5.3 y §6.2):
- SUPERUSUARIO y ADMINISTRADOR: acceso global a todos los almacenes.
- USUARIO: acceso restringido única y exclusivamente a su almacén asignado.
- Cuentas inactivas o eliminadas lógicamente: denegación absoluta.
- Validación en servidor para prevenir manipulación de parámetros (URL, query o body).
"""

from functools import wraps

from django.core.exceptions import PermissionDenied
from django.utils.translation import gettext_lazy as _

from inventario.models import Almacen


def puede_acceder_almacen(usuario, almacen) -> bool:
    """Verifica si un usuario tiene autorización para operar en un almacén.

    Retorna False si:
    - El usuario es anónimo, inactivo o eliminado lógicamente.
    - El almacén no existe o es None.
    - El usuario es de rol USUARIO y el almacén no coincide con su almacen_asignado,
      o el almacén está inactivo o eliminado lógicamente.
    """
    if not usuario or not getattr(usuario, "is_authenticated", False):
        return False

    if not usuario.is_active or getattr(usuario, "eliminado_logicamente", False):
        return False

    if not almacen:
        return False

    # Superusuario y Administrador tienen acceso a todos los almacenes
    if getattr(usuario, "es_administrador", False):
        return True

    # Para rol USUARIO: almacén debe estar activo y no eliminado
    if not almacen.activo or getattr(almacen, "eliminado", False):
        return False

    return usuario.tiene_acceso_almacen(almacen)


def filtrar_por_almacen(queryset, usuario, campo="almacen"):
    """Filtra un QuerySet según los permisos de almacén del usuario.

    - Administradores y Superusuarios: ven todos los registros.
    - Usuarios normales: ven únicamente los registros de sus almacenes asignados.
    - Usuarios no autorizados: QuerySet vacío (none()).
    """
    if not usuario or not getattr(usuario, "is_authenticated", False):
        return queryset.none()

    if not usuario.is_active or getattr(usuario, "eliminado_logicamente", False):
        return queryset.none()

    if getattr(usuario, "es_administrador", False):
        return queryset

    almacenes = usuario.get_almacenes_asignados()
    if not almacenes.exists():
        return queryset.none()

    return queryset.filter(**{f"{campo}__in": almacenes}).distinct()


def obtener_almacen_o_denegar(usuario, almacen_id) -> Almacen:
    """Obtiene una instancia de Almacen validando permisos en el servidor.

    Lanza PermissionDenied si el almacén no existe o si el usuario
    no tiene autorización para acceder a él.
    """
    try:
        almacen = Almacen.objects.get(id=almacen_id)
    except (Almacen.DoesNotExist, ValueError, TypeError):
        raise PermissionDenied(_("Almacén no encontrado o acceso denegado."))

    if not puede_acceder_almacen(usuario, almacen):
        raise PermissionDenied(_("No tienes permisos para operar en este almacén."))

    return almacen


def requiere_rol(*roles):
    """Decorador para vistas basadas en funciones que restringe acceso por rol."""
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if not request.user.is_authenticated:
                raise PermissionDenied(_("Debe iniciar sesión para acceder."))

            if not request.user.is_active or getattr(request.user, "eliminado_logicamente", False):
                raise PermissionDenied(_("Cuenta inactiva o eliminada."))

            if getattr(request.user, "rol", None) not in roles:
                raise PermissionDenied(_("No tienes el rol requerido para realizar esta acción."))

            return view_func(request, *args, **kwargs)
        return _wrapped_view
    return decorator


class RolRequeridoMixin:
    """Mixin para vistas basadas en clases que valida los roles permitidos."""
    roles_permitidos = ()

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            raise PermissionDenied(_("Debe iniciar sesión para acceder."))

        if not request.user.is_active or getattr(request.user, "eliminado_logicamente", False):
            raise PermissionDenied(_("Cuenta inactiva o eliminada."))

        if getattr(request.user, "rol", None) not in self.roles_permitidos:
            raise PermissionDenied(_("No tienes el rol requerido para realizar esta acción."))

        return super().dispatch(request, *args, **kwargs)


class AccesoAlmacenMixin:
    """Mixin para CBVs que resuelve el almacén y valida permisos del usuario."""
    almacen = None

    def dispatch(self, request, *args, **kwargs):
        almacen_id = (
            kwargs.get("almacen_id")
            or request.GET.get("almacen_id")
            or request.POST.get("almacen_id")
        )

        if almacen_id:
            self.almacen = obtener_almacen_o_denegar(request.user, almacen_id)
        elif request.user.is_authenticated and getattr(request.user, "rol", None) == "USUARIO":
            self.almacen = request.user.almacen_asignado
            if not puede_acceder_almacen(request.user, self.almacen):
                raise PermissionDenied(_("Tu almacén asignado no está activo o disponible."))
        else:
            self.almacen = None

        return super().dispatch(request, *args, **kwargs)
