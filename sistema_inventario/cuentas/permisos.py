"""                                                                                                           
Capa de permisos y autorización por rol y almacén — Inventario AudacIA.                                       

Reglas clave de la especificación (§5.3 y §6.2):                                                              
- SUPERUSUARIO y ADMINISTRADOR: acceso global a todos los almacenes.                                          
- USUARIO: acceso restringido única y exclusivamente a su almacén asignado (a menos que tenga                 
acceso_todos_almacenes).                                                                                      
- Cuentas inactivas o eliminadas lógicamente: denegación absoluta.                                            
- Validación en servidor para prevenir manipulación de parámetros (URL, query o body).                        
"""                                                                                                           

from functools import wraps                                                                                   

from django.core.exceptions import PermissionDenied                                                           
from django.utils.translation import gettext_lazy as _                                                        

from django.db.models import Q                                                                                
from inventario.models import Almacen                                                                         


def puede_acceder_almacen(usuario, almacen) -> bool:                                                          
    """Verifica si un usuario tiene autorización para operar en un almacén.                                   

    Retorna False si:                                                                                         
    - El usuario es anónimo, inactivo o eliminado lógicamente.                                                
    - El almacén no existe o es None.                                                                         
    - El almacén está inactivo o eliminado lógicamente.                                                       
    - El usuario es de rol USUARIO y no tiene asignado este almacén (ni como principal ni en                  
    almacenes_asignados),                                                                                     
      ni cuenta con autorización de acceso_todos_almacenes.                                                   
    """                                                                                                       
    if not usuario or not getattr(usuario, "is_authenticated", False):                                        
        return False                                                                                          

    if not usuario.is_active or getattr(usuario, "eliminado_logicamente", False):                             
        return False                                                                                          

    if not almacen:                                                                                           
        return False                                                                                          

    if hasattr(usuario, "tiene_acceso_almacen"):                                                              
        return usuario.tiene_acceso_almacen(almacen)                                                          

    # Fallback seguro                                                                                         
    if not getattr(almacen, "activo", True) or getattr(almacen, "eliminado", False):                          
        return False                                                                                          

    if getattr(usuario, "es_administrador", False) or getattr(usuario, "acceso_todos_almacenes", False):      
        return True                                                                                           

    alm_id = getattr(almacen, "id", almacen)                                                                  
    if getattr(usuario, "almacen_asignado_id", None) == alm_id:                                               
        return True                                                                                           

    if getattr(usuario, "pk", None) and hasattr(usuario, "almacenes_asignados"):                              
        return usuario.almacenes_asignados.filter(id=alm_id, activo=True, eliminado=False).exists()           

    return False                                                                                              


def filtrar_por_almacen(queryset, usuario, campo="almacen"):                                                  
    """Filtra un QuerySet según los permisos de almacén del usuario.                                          

    - Administradores, Superusuarios y usuarios con acceso_todos_almacenes: ven todos los registros.          
    - Usuarios normales: ven únicamente los registros de sus almacenes asignados (principal + multialmacén    
    activos).                                                                                                 
    - Usuarios no autorizados / inactivos: QuerySet vacío (none()).                                           
    """                                                                                                       
    if not usuario or not getattr(usuario, "is_authenticated", False):                                        
        return queryset.none()                                                                                

    if not usuario.is_active or getattr(usuario, "eliminado_logicamente", False):                             
        return queryset.none()                                                                                

    if getattr(usuario, "es_administrador", False) or getattr(usuario, "acceso_todos_almacenes", False):      
        return queryset                                                                                       

    if hasattr(usuario, "get_almacenes_asignados"):                                                           
        almacenes = usuario.get_almacenes_asignados()                                                         
        if not almacenes.exists():                                                                            
            return queryset.none()                                                                            
        almacenes_ids = list(almacenes.values_list("id", flat=True))                                          
    else:                                                                                                     
        almacenes_ids = set()                                                                                 
        if getattr(usuario, "almacen_asignado_id", None):                                                     
            if usuario.almacen_asignado and usuario.almacen_asignado.activo and not                           
            usuario.almacen_asignado.eliminado:                                                               
                almacenes_ids.add(usuario.almacen_asignado_id)                                                

        if getattr(usuario, "pk", None) and hasattr(usuario, "almacenes_asignados"):                          
            almacenes_ids.update(                                                                             
                usuario.almacenes_asignados.filter(activo=True, eliminado=False).values_list("id", flat=True) 
            )                                                                                                 

        if not almacenes_ids:                                                                                 
            return queryset.none()                                                                            

    lookup = f"{campo}_id__in" if not campo.endswith("_id") else f"{campo}__in"                               
    return queryset.filter(**{lookup: almacenes_ids}).distinct()                                              


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


class SuperusuarioRequeridoMixin:                                                                             
    """Mixin para vistas exclusivas de Superadministrador (ej. Respaldos del Sistema)."""                     

    def dispatch(self, request, *args, **kwargs):                                                             
        if not request.user.is_authenticated:                                                                 
            raise PermissionDenied(_("Debe iniciar sesión para acceder."))                                    

        if not request.user.is_active or getattr(request.user, "eliminado_logicamente", False):               
            raise PermissionDenied(_("Cuenta inactiva o eliminada."))                                         

        es_super = (                                                                                          
            getattr(request.user, "is_superuser", False)                                                      
            or getattr(request.user, "rol", None) == "SUPERUSUARIO"                                           
            or getattr(request.user, "es_superusuario_inventario", False)                                     
        )                                                                                                     
        if not es_super:                                                                                      
            raise PermissionDenied(_("Esta acción es exclusiva para Superadministradores del sistema."))      

        return super().dispatch(request, *args, **kwargs)                                                     


class AdministradorRequeridoMixin:                                                                            
    """Mixin para vistas exclusivas de Administrador o Superadministrador (ej. Centro de Exportaciones)."""   

    def dispatch(self, request, *args, **kwargs):                                                             
        if not request.user.is_authenticated:                                                                 
            raise PermissionDenied(_("Debe iniciar sesión para acceder."))                                    

        if not request.user.is_active or getattr(request.user, "eliminado_logicamente", False):               
            raise PermissionDenied(_("Cuenta inactiva o eliminada."))                                         

        if not (getattr(request.user, "es_administrador", False) or getattr(request.user, "is_superuser",     
        False)):                                                                                              
            raise PermissionDenied(_("Esta acción es exclusiva para Administradores o Superadministradores."))

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


class PermisoRequeridoMixin:                                                                                  
    """Mixin para CBVs que valida que el usuario cuente con los permisos requeridos."""                       
    permiso_requerido = None  # str o iterable de str                                                         
    requiere_todos = False                                                                                    

    def dispatch(self, request, *args, **kwargs):                                                             
        if not request.user.is_authenticated:                                                                 
            raise PermissionDenied(_("Debe iniciar sesión para acceder."))                                    

        if not request.user.is_active or getattr(request.user, "eliminado_logicamente", False):               
            raise PermissionDenied(_("Cuenta inactiva o eliminada."))                                         

        if getattr(request.user, "es_administrador", False):                                                  
            return super().dispatch(request, *args, **kwargs)                                                 

        if self.permiso_requerido:                                                                            
            perms = [self.permiso_requerido] if isinstance(self.permiso_requerido, str) else                  
            list(self.permiso_requerido)                                                                      
            if self.requiere_todos:                                                                           
                autorizado = all(request.user.tiene_permiso(p) for p in perms)                                
            else:                                                                                             
                autorizado = any(request.user.tiene_permiso(p) for p in perms)                                
            if not autorizado:                                                                                
                raise PermissionDenied(_("No tienes permisos suficientes para realizar esta acción."))        

        return super().dispatch(request, *args, **kwargs)                                                     


def requiere_permiso(*codigos_permiso, requiere_todos=False):                                                 
    """Decorador para vistas basadas en funciones que valida permisos granulares."""                          
    def decorator(view_func):                                                                                 
        @wraps(view_func)                                                                                     
        def _wrapped_view(request, *args, **kwargs):                                                          
            if not request.user.is_authenticated:                                                             
                raise PermissionDenied(_("Debe iniciar sesión para acceder."))                                

            if not request.user.is_active or getattr(request.user, "eliminado_logicamente", False):           
                raise PermissionDenied(_("Cuenta inactiva o eliminada."))                                     

            if getattr(request.user, "es_administrador", False):                                              
                return view_func(request, *args, **kwargs)                                                    

            if codigos_permiso:                                                                               
                if requiere_todos:                                                                            
                    autorizado = all(request.user.tiene_permiso(p) for p in codigos_permiso)                  
                else:                                                                                         
                    autorizado = any(request.user.tiene_permiso(p) for p in codigos_permiso)                  
                if not autorizado:                                                                            
                    raise PermissionDenied(_("No tienes permisos suficientes para realizar esta acción."))    

            return view_func(request, *args, **kwargs)                                                        
        return _wrapped_view                                                                                  
    return decorator 