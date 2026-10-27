"""
Modelos de cuentas de usuario — Inventario AudacIA.

Tres roles cerrados (Superusuario, Administrador, Usuario) con permisos
definidos por la especificación. El Superusuario es único e ineliminable.

Datos mínimos obligatorios: nombre completo, nombre de usuario y contraseña.
No se requieren correo, teléfono ni documento de identidad (sección 6.1).
"""

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.translation import gettext_lazy as _

from .managers import UsuarioAudacIAManager


class PermisoAudacIA(models.Model):
    """Permiso granular del sistema de inventario (RBAC / PBAC)."""

    class Modulo(models.TextChoices):
        CATALOGO = "CATALOGO", _("Catálogo y Productos")
        MOVIMIENTOS = "MOVIMIENTOS", _("Movimientos y Operaciones")
        INFRAESTRUCTURA = "INFRAESTRUCTURA", _("Almacenes y Ubicaciones")
        ANALITICA = "ANALITICA", _("Alertas y Reportes")
        SEGURIDAD = "SEGURIDAD", _("Seguridad y Usuarios")

    codigo = models.CharField(
        _("código"),
        max_length=64,
        unique=True,
        help_text=_("Identificador único del permiso (ej. 'movimientos.entrada')."),
    )
    modulo = models.CharField(
        _("módulo"),
        max_length=32,
        choices=Modulo.choices,
        help_text=_("Área funcional a la que pertenece el permiso."),
    )
    nombre = models.CharField(
        _("nombre"),
        max_length=128,
        help_text=_("Nombre descriptivo del permiso para la interfaz."),
    )
    descripcion = models.CharField(
        _("descripción"),
        max_length=255,
        blank=True,
        default="",
        help_text=_("Explicación de la acción que autoriza este permiso."),
    )
    orden = models.PositiveSmallIntegerField(
        _("orden"),
        default=0,
        help_text=_("Orden de presentación dentro del módulo."),
    )

    class Meta:
        verbose_name = _("permiso")
        verbose_name_plural = _("permisos")
        ordering = ["modulo", "orden", "nombre"]

    def __str__(self):
        return f"{self.nombre} ({self.codigo})"

    _cached_todos_codigos = None

    @classmethod
    def get_todos_codigos(cls) -> set:
        if cls._cached_todos_codigos is None:
            cls._cached_todos_codigos = set(cls.objects.values_list("codigo", flat=True))
        return set(cls._cached_todos_codigos)

    @classmethod
    def agrupados_por_modulo(cls):
        """Devuelve una lista de tuplas (modulo_codigo, modulo_nombre, [permisos]) ordenada."""
        modulos_ordenados = [
            (cls.Modulo.CATALOGO, _("Catálogo y Fichas Técnicas")),
            (cls.Modulo.MOVIMIENTOS, _("Movimientos y Operaciones")),
            (cls.Modulo.INFRAESTRUCTURA, _("Almacenes y Ubicaciones")),
            (cls.Modulo.ANALITICA, _("Alertas y Reportes")),
            (cls.Modulo.SEGURIDAD, _("Seguridad y Administración")),
        ]
        todos = list(cls.objects.all().order_by("modulo", "orden", "nombre"))
        resultado = []
        for mod_cod, mod_label in modulos_ordenados:
            perms = [p for p in todos if p.modulo == mod_cod]
            if perms:
                resultado.append((mod_cod, mod_label, perms))
        return resultado


class UsuarioAudacIA(AbstractUser):
    """Usuario del sistema de inventario.

    Extiende AbstractUser de Django para:
    - Eliminar la obligatoriedad del email.
    - Añadir rol, almacén asignado y eliminación lógica.
    - Soportar bloqueo temporal por intentos fallidos.
    """

    class Rol(models.TextChoices):
        SUPERUSUARIO = "SUPERUSUARIO", _("Superusuario")
        ADMINISTRADOR = "ADMINISTRADOR", _("Administrador")
        USUARIO = "USUARIO", _("Usuario")

    # --- Campos del perfil ---

    nombre_completo = models.CharField(
        _("nombre completo"),
        max_length=255,
        help_text=_("Nombre completo del usuario."),
    )

    rol = models.CharField(
        _("rol"),
        max_length=20,
        choices=Rol.choices,
        default=Rol.USUARIO,
        help_text=_("Define los permisos del usuario en el sistema."),
    )

    # El almacén se asigna obligatoriamente a los usuarios con rol USUARIO (1 o más almacenes).
    # Administradores y Superusuario tienen acceso a todos los almacenes.
    almacen_asignado = models.ForeignKey(
        "inventario.Almacen",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="usuarios_asignados",
        verbose_name=_("almacén principal asignado"),
        help_text=_(
            "Almacén principal al que pertenece este usuario. "
            "Obligatorio para rol Usuario; null para Administrador y Superusuario."
        ),
    )
    almacenes_asignados = models.ManyToManyField(
        "inventario.Almacen",
        blank=True,
        related_name="usuarios_con_acceso",
        verbose_name=_("almacenes asignados"),
        help_text=_(
            "Almacenes a los que tiene acceso este usuario. Puede tener uno o varios asignados."
        ),
    )
    creado_por = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="usuarios_creados",
        verbose_name=_("creado por"),
        help_text=_("Administrador o Superusuario que registró esta cuenta."),
    )

    # --- Estados de cuenta (sección 6.3) ---

    eliminado_logicamente = models.BooleanField(
        _("eliminado lógicamente"),
        default=False,
        help_text=_(
            "Eliminación lógica. Los registros históricos se conservan."
        ),
    )

    # --- Control de permisos granulares y sincronización ---
    permisos_version = models.PositiveIntegerField(
        _("versión de permisos"),
        default=1,
        help_text=_(
            "Contador incremental para sincronización e invalidación inmediata de permisos."
        ),
    )
    permisos_personalizados = models.ManyToManyField(
        PermisoAudacIA,
        blank=True,
        related_name="usuarios",
        verbose_name=_("permisos personalizados"),
        help_text=_("Permisos específicos asignados a este usuario."),
    )

    fecha_creacion = models.DateTimeField(_("fecha de creación"), auto_now_add=True)
    fecha_modificacion = models.DateTimeField(_("última modificación"), auto_now=True)

    # --- Campos que NO usamos pero heredamos de AbstractUser ---

    # email no es obligatorio en este sistema.
    email = models.EmailField(_("email"), blank=True, default="")

    # Eliminamos first_name y last_name a favor de nombre_completo.
    first_name = None  # type: ignore[assignment]
    last_name = None  # type: ignore[assignment]

    # --- Manager ---

    objects = UsuarioAudacIAManager()

    USERNAME_FIELD = "username"
    REQUIRED_FIELDS = ["nombre_completo"]

    class Meta:
        verbose_name = _("usuario")
        verbose_name_plural = _("usuarios")
        ordering = ["username"]

    def __str__(self):
        return f"{self.username} ({self.get_rol_display()})"

    @property
    def es_superusuario_inventario(self):
        """True si tiene el rol Superusuario del inventario."""
        return self.rol == self.Rol.SUPERUSUARIO

    @property
    def es_administrador(self):
        """True si es Administrador o Superusuario."""
        return self.rol in (self.Rol.SUPERUSUARIO, self.Rol.ADMINISTRADOR)

    @property
    def es_usuario_normal(self):
        return self.rol == self.Rol.USUARIO

    def get_almacenes_asignados(self):
        """Retorna un QuerySet de almacenes a los que tiene acceso el usuario."""
        from inventario.models import Almacen
        if self.es_administrador:
            return Almacen.objects.filter(activo=True, eliminado=False).order_by("nombre")

        ids = set()
        if self.almacen_asignado_id:
            ids.add(self.almacen_asignado_id)
        if self.pk:
            for alm_id in self.almacenes_asignados.filter(activo=True, eliminado=False).values_list("id", flat=True):
                ids.add(alm_id)

        if not ids:
            return Almacen.objects.none()
        return Almacen.objects.filter(id__in=ids, activo=True, eliminado=False).order_by("nombre")

    def tiene_acceso_almacen(self, almacen):
        """¿Puede este usuario operar en el almacén dado?

        - Superusuario y Administrador: todos los almacenes.
        - Usuario: almacén principal asignado o cualquiera en sus almacenes asignados.
        """
        if self.es_administrador:
            return True
        if not almacen:
            return False
        alm_id = getattr(almacen, "id", almacen)
        if self.almacen_asignado_id == alm_id:
            return True
        if self.pk:
            return self.almacenes_asignados.filter(id=alm_id, activo=True, eliminado=False).exists()
        return False

    def get_permisos_codigos(self) -> set:
        """Retorna el conjunto de códigos de permisos asignados al usuario.

        Utiliza una caché viva exclusivamente durante la petición HTTP actual (_permisos_cache).
        """
        if not self.is_authenticated or not self.is_active or self.eliminado_logicamente:
            return set()
        if self.es_administrador:
            return PermisoAudacIA.get_todos_codigos()
        if not hasattr(self, "_permisos_cache"):
            if not self.pk:
                return set()
            self._permisos_cache = set(
                self.permisos_personalizados.values_list("codigo", flat=True)
            )
        return self._permisos_cache

    def limpiar_permisos_cache(self):
        """Limpia la caché de permisos en memoria de esta instancia."""
        if hasattr(self, "_permisos_cache"):
            delattr(self, "_permisos_cache")

    def refresh_from_db(self, using=None, fields=None):
        self.limpiar_permisos_cache()
        super().refresh_from_db(using=using, fields=fields)

    def tiene_permiso(self, codigo_permiso: str) -> bool:
        """Verifica si el usuario cuenta con un permiso específico en tiempo O(1)."""
        if not self.is_authenticated or not self.is_active or self.eliminado_logicamente:
            return False
        if self.es_administrador:
            return True
        return codigo_permiso in self.get_permisos_codigos()

    def tiene_algun_permiso(self, *codigos: str) -> bool:
        """Retorna True si el usuario tiene al menos uno de los permisos indicados (o es admin)."""
        if not self.is_authenticated or not self.is_active or self.eliminado_logicamente:
            return False
        if self.es_administrador:
            return True
        user_perms = self.get_permisos_codigos()
        return any(c in user_perms for c in codigos)

    @property
    def puede_acceder_modulo_movimientos(self) -> bool:
        """Permite ingresar a Movimientos si puede ver o tiene cualquier permiso operativo."""
        return self.tiene_algun_permiso(
            "movimientos.ver",
            "movimientos.entrada",
            "movimientos.prestamo",
            "movimientos.dotacion",
            "movimientos.devolucion",
            "movimientos.baja",
            "movimientos.traslado_interno",
            "movimientos.traslado_almacen",
        )

    @property
    def puede_acceder_modulo_catalogo(self) -> bool:
        """Permite ingresar a Catálogo si puede ver, crear, editar o eliminar productos."""
        return self.tiene_algun_permiso(
            "catalogo.ver",
            "catalogo.crear",
            "catalogo.editar",
            "catalogo.eliminar",
            "analitica.ver_alertas",
        )

    @property
    def puede_acceder_modulo_infraestructura(self) -> bool:
        """Permite ingresar a Infraestructura si puede ver o gestionar categorías/ubicaciones."""
        return self.tiene_algun_permiso(
            "infraestructura.ver",
            "infraestructura.gestionar_categorias",
            "infraestructura.gestionar_ubicaciones",
        )


class IntentoFallido(models.Model):
    """Registro de intentos fallidos de login para bloqueo temporal.

    Después de LOGIN_MAX_ATTEMPTS intentos fallidos consecutivos, la cuenta
    se bloquea durante LOGIN_LOCKOUT_MINUTES minutos (sección 6.4).
    """

    usuario = models.ForeignKey(
        UsuarioAudacIA,
        on_delete=models.CASCADE,
        related_name="intentos_fallidos",
        verbose_name=_("usuario"),
    )
    fecha_intento = models.DateTimeField(_("fecha del intento"), auto_now_add=True)
    ip = models.GenericIPAddressField(
        _("dirección IP"),
        null=True,
        blank=True,
    )

    class Meta:
        verbose_name = _("intento fallido")
        verbose_name_plural = _("intentos fallidos")
        ordering = ["-fecha_intento"]
        indexes = [
            models.Index(fields=["usuario", "-fecha_intento"]),
        ]

    def __str__(self):
        return f"Intento fallido de {self.usuario} el {self.fecha_intento}"


# --- Catálogo de permisos del sistema y Presets operativos ---

PERMISOS_CATALOGO = [
    # Módulo 1: CATALOGO
    {
        "codigo": "catalogo.ver",
        "modulo": "CATALOGO",
        "nombre": "Ver catálogo y productos",
        "descripcion": "Consultar catálogo, fichas de producto, stock y especificaciones.",
        "orden": 1,
    },
    {
        "codigo": "catalogo.crear",
        "modulo": "CATALOGO",
        "nombre": "Crear productos",
        "descripcion": "Registrar nuevos productos en el catálogo con stock inicial.",
        "orden": 2,
    },
    {
        "codigo": "catalogo.editar",
        "modulo": "CATALOGO",
        "nombre": "Editar información de productos",
        "descripcion": "Modificar nombre, descripción, stock mínimo, código de barras y empaques.",
        "orden": 3,
    },
    {
        "codigo": "catalogo.eliminar",
        "modulo": "CATALOGO",
        "nombre": "Archivar o eliminar productos",
        "descripcion": "Archivar o retirar productos del catálogo de inventario.",
        "orden": 4,
    },

    # Módulo 2: MOVIMIENTOS
    {
        "codigo": "movimientos.ver",
        "modulo": "MOVIMIENTOS",
        "nombre": "Ver historial de movimientos",
        "descripcion": "Consultar movimientos, kardex y trazabilidad de inventario.",
        "orden": 1,
    },
    {
        "codigo": "movimientos.entrada",
        "modulo": "MOVIMIENTOS",
        "nombre": "Registrar entradas de stock",
        "descripcion": "Registrar abastecimientos, compras y recepciones en almacén.",
        "orden": 2,
    },
    {
        "codigo": "movimientos.prestamo",
        "modulo": "MOVIMIENTOS",
        "nombre": "Autorizar y entregar préstamos",
        "descripcion": "Entregar herramientas, equipos y materiales en calidad de préstamo.",
        "orden": 3,
    },
    {
        "codigo": "movimientos.dotacion",
        "modulo": "MOVIMIENTOS",
        "nombre": "Realizar dotaciones",
        "descripcion": "Entregar consumibles o dotación de forma definitiva a destinatarios.",
        "orden": 4,
    },
    {
        "codigo": "movimientos.devolucion",
        "modulo": "MOVIMIENTOS",
        "nombre": "Recibir devoluciones de préstamos",
        "descripcion": "Recibir y reintegrar a stock disponible los artículos en préstamo.",
        "orden": 5,
    },
    {
        "codigo": "movimientos.baja",
        "modulo": "MOVIMIENTOS",
        "nombre": "Registrar bajas por daño o pérdida",
        "descripcion": "Dar de baja existencias o préstamos deteriorados o extraviados.",
        "orden": 6,
    },
    {
        "codigo": "movimientos.traslado_interno",
        "modulo": "MOVIMIENTOS",
        "nombre": "Ejecutar traslados internos",
        "descripcion": "Reubicar productos físicamente entre estantes o ubicaciones del mismo almacén.",
        "orden": 7,
    },
    {
        "codigo": "movimientos.traslado_almacen",
        "modulo": "MOVIMIENTOS",
        "nombre": "Ejecutar traslados entre almacenes",
        "descripcion": "Transferir productos y existencias entre distintos almacenes del sistema.",
        "orden": 8,
    },

    # Módulo 3: INFRAESTRUCTURA
    {
        "codigo": "infraestructura.ver",
        "modulo": "INFRAESTRUCTURA",
        "nombre": "Ver almacenes, categorías y ubicaciones",
        "descripcion": "Consultar la estructura física y organizativa del inventario.",
        "orden": 1,
    },
    {
        "codigo": "infraestructura.gestionar_ubicaciones",
        "modulo": "INFRAESTRUCTURA",
        "nombre": "Gestionar ubicaciones físicas",
        "descripcion": "Crear, editar o reorganizar estantes, casilleros y niveles.",
        "orden": 2,
    },
    {
        "codigo": "infraestructura.gestionar_categorias",
        "modulo": "INFRAESTRUCTURA",
        "nombre": "Gestionar categorías y tipos",
        "descripcion": "Crear, editar o clasificar categorías y tipos de productos.",
        "orden": 3,
    },

    # Módulo 4: ANALITICA
    {
        "codigo": "analitica.ver_alertas",
        "modulo": "ANALITICA",
        "nombre": "Ver centro de alertas de stock",
        "descripcion": "Monitorear alertas de existencias agotadas o bajo umbral mínimo.",
        "orden": 1,
    },
    {
        "codigo": "analitica.ver_reportes",
        "modulo": "ANALITICA",
        "nombre": "Ver dashboard y reportes analíticos",
        "descripcion": "Acceder al panel de control y reportes de existencias y rotación.",
        "orden": 2,
    },
    {
        "codigo": "analitica.exportar",
        "modulo": "ANALITICA",
        "nombre": "Exportar reportes (Excel / CSV / PDF)",
        "descripcion": "Descargar archivos de existencias, préstamos y movimientos.",
        "orden": 3,
    },

]

PRESETS_PERMISOS = {
    "OPERADOR_INTEGRAL": {
        "nombre": "Operador Integral de Almacén",
        "descripcion": "Control operativo de entradas, salidas, préstamos y catálogo en su almacén.",
        "icon": "zap",
        "icono": "zap",
        "codigos": [
            "catalogo.ver",
            "catalogo.crear",
            "catalogo.editar",
            "movimientos.ver",
            "movimientos.entrada",
            "movimientos.prestamo",
            "movimientos.dotacion",
            "movimientos.devolucion",
            "movimientos.baja",
            "infraestructura.ver",
            "infraestructura.gestionar_ubicaciones",
            "infraestructura.gestionar_categorias",
            "analitica.ver_alertas",
            "analitica.ver_reportes",
        ],
    },
    "ABASTECIMIENTO": {
        "nombre": "Operador de Abastecimiento",
        "descripcion": "Recepción de compras, entrada de stock y monitoreo de alertas.",
        "icon": "package",
        "icono": "package",
        "codigos": [
            "catalogo.ver",
            "movimientos.ver",
            "movimientos.entrada",
            "analitica.ver_alertas",
        ],
    },
    "PANOLERO": {
        "nombre": "Almacenista de Herramientas / Despacho",
        "descripcion": "Entrega de herramientas y equipos en préstamo, dotaciones y devoluciones.",
        "icon": "wrench",
        "icono": "wrench",
        "codigos": [
            "catalogo.ver",
            "movimientos.ver",
            "movimientos.prestamo",
            "movimientos.dotacion",
            "movimientos.devolucion",
            "movimientos.baja",
            "analitica.ver_alertas",
        ],
    },
    "GESTOR_CATALOGO": {
        "nombre": "Gestor de Catálogo y Bodega",
        "descripcion": "Catalogación de productos, estantes, casilleros y categorías.",
        "icon": "tag",
        "icono": "tag",
        "codigos": [
            "catalogo.ver",
            "catalogo.crear",
            "catalogo.editar",
            "infraestructura.ver",
            "infraestructura.gestionar_ubicaciones",
            "infraestructura.gestionar_categorias",
            "analitica.ver_alertas",
        ],
    },
    "AUDITOR": {
        "nombre": "Auditor / Solo Lectura",
        "descripcion": "Consulta global de catálogo, movimientos, reportes y exportación.",
        "icon": "eye",
        "icono": "eye",
        "codigos": [
            "catalogo.ver",
            "movimientos.ver",
            "infraestructura.ver",
            "analitica.ver_alertas",
            "analitica.ver_reportes",
            "analitica.exportar",
        ],
    },
}
