"""Paquete modular de vistas de la aplicación inventario.

Re-exporta todas las vistas públicas y utilidades para preservar 100%
de compatibilidad retrospectiva con los módulos que importen desde inventario.views.
"""

from inventario.views._comun import (
    AjaxModalFormMixin,
    RedireccionPestañaMovimientosView,
    _formatear_error_excepcion,
    _obtener_ip,
    _procesar_error_formulario,
)
from inventario.views.alertas import (
    ActualizarNombrePerfilView,
    AlertaMarcarLeidaView,
    AlertasStockJSONView,
    AlertasStockListaView,
)
from inventario.views.catalogo import (
    ProductoArchivarView,
    ProductoCrearView,
    ProductoDetalleView,
    ProductoEditarView,
    ProductoFusionarView,
    ProductoListaView,
    ProductoToggleActivoView,
    ProductoVerificarNombreView,
)
from inventario.views.conteos import (
    ConteoFisicoConfirmarView,
    ConteoFisicoCrearView,
    ConteoFisicoListaView,
    ConteoFisicoRechazarView,
)
from inventario.views.errores import (
    error_403_view,
    error_404_view,
    error_500_view,
)
from inventario.views.infraestructura import (
    AlmacenCrearView,
    AlmacenDesactivarView,
    AlmacenEditarView,
    AlmacenEliminarView,
    AlmacenListaView,
    AlmacenReactivarView,
    CategoriaCrearView,
    CategoriaDesactivarView,
    CategoriaEditarView,
    CategoriaEliminarView,
    CategoriaListaView,
    CategoriaReactivarView,
    CategoriaTrasladarView,
    TipoCategoriaCrearView,
    TipoCategoriaEditarView,
    TipoCategoriaEliminarView,
    UbicacionCrearView,
    UbicacionDesactivarView,
    UbicacionEditarView,
    UbicacionEliminarView,
    UbicacionListaView,
    UbicacionReactivarView,
    UbicacionTrasladarView,
)
from inventario.views.movimientos import (
    EntradaCrearView,
    MovimientoListaView,
    MovimientoRevertirView,
    SalidaCrearView,
    TrasladoCrearView,
)
from inventario.views.prestamos import (
    PrestamoBajaView,
    PrestamoCrearView,
    PrestamoDevolucionView,
    PrestamoListaView,
)
from inventario.views.respaldos import (
    RespaldoCrearView,
    RespaldoDescargarView,
    RespaldoEliminarView,
    RespaldoListaView,
)
from inventario.servicios.respaldo import (
    eliminar_respaldo_bd,
    generar_respaldo_bd,
    listar_respaldos,
    obtener_estadisticas_respaldos,
    obtener_ruta_respaldo_segura,
)

__all__ = [
    # Utilidades comunes
    "_obtener_ip",
    "_procesar_error_formulario",
    "_formatear_error_excepcion",
    "AjaxModalFormMixin",
    "RedireccionPestañaMovimientosView",
    # Infraestructura - Almacenes
    "AlmacenListaView",
    "AlmacenReactivarView",
    "AlmacenEliminarView",
    "AlmacenCrearView",
    "AlmacenEditarView",
    "AlmacenDesactivarView",
    # Infraestructura - Ubicaciones
    "UbicacionListaView",
    "UbicacionReactivarView",
    "UbicacionEliminarView",
    "UbicacionCrearView",
    "UbicacionEditarView",
    "UbicacionDesactivarView",
    "UbicacionTrasladarView",
    # Infraestructura - Categorías y Tipos
    "CategoriaListaView",
    "CategoriaReactivarView",
    "CategoriaEliminarView",
    "CategoriaCrearView",
    "CategoriaEditarView",
    "CategoriaTrasladarView",
    "CategoriaDesactivarView",
    "TipoCategoriaCrearView",
    "TipoCategoriaEditarView",
    "TipoCategoriaEliminarView",
    # Catálogo
    "ProductoListaView",
    "ProductoCrearView",
    "ProductoEditarView",
    "ProductoDetalleView",
    "ProductoToggleActivoView",
    "ProductoArchivarView",
    "ProductoVerificarNombreView",
    "ProductoFusionarView",
    # Movimientos
    "MovimientoListaView",
    "EntradaCrearView",
    "SalidaCrearView",
    "TrasladoCrearView",
    "MovimientoRevertirView",
    # Préstamos
    "PrestamoListaView",
    "PrestamoCrearView",
    "PrestamoDevolucionView",
    "PrestamoBajaView",
    # Conteos
    "ConteoFisicoListaView",
    "ConteoFisicoCrearView",
    "ConteoFisicoConfirmarView",
    "ConteoFisicoRechazarView",
    # Alertas y Perfil
    "AlertasStockListaView",
    "AlertaMarcarLeidaView",
    "ActualizarNombrePerfilView",
    "AlertasStockJSONView",
    # Respaldos
    "RespaldoListaView",
    "RespaldoCrearView",
    "RespaldoDescargarView",
    "RespaldoEliminarView",
    "generar_respaldo_bd",
    "listar_respaldos",
    "obtener_estadisticas_respaldos",
    "obtener_ruta_respaldo_segura",
    "eliminar_respaldo_bd",
    # Errores
    "error_403_view",
    "error_404_view",
    "error_500_view",
]

# Sincronizador dinámico para pruebas legadas que usan patch("inventario.views.<helper>")
import sys
from types import ModuleType


class _ViewsModule(ModuleType):
    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        if name in (
            "listar_respaldos",
            "obtener_estadisticas_respaldos",
            "generar_respaldo_bd",
            "obtener_ruta_respaldo_segura",
            "eliminar_respaldo_bd",
        ):
            from inventario.views import respaldos as _resp
            setattr(_resp, name, value)


sys.modules[__name__].__class__ = _ViewsModule
