"""
Modelos de inventario — Inventario AudacIA.

Contiene todos los modelos de datos maestros (almacenes, ubicaciones,
categorías, productos) y los modelos transaccionales (movimientos,
préstamos, conteos, auditoría).

Reglas clave de la especificación:
- Todo cambio de stock ocurre dentro de transaction.atomic() con
  select_for_update() (sección 9).
- Stock negativo prohibido bajo toda circunstancia.
- Movimientos confirmados son inmutables (nunca DELETE).
- Código interno de producto es inmutable una vez creado.
- Stock se controla por almacén, no por ubicación.
- Auditoría inmutable desde la aplicación web.
"""

import uuid

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.utils.translation import gettext_lazy as _

from .utils import normalizar_texto


# ═══════════════════════════════════════════════════════════════════════════
# DATOS MAESTROS
# ═══════════════════════════════════════════════════════════════════════════


class Almacen(models.Model):
    """Almacén físico de la empresa.

    La cantidad es variable. Un almacén con productos/existencias no puede
    desactivarse ni eliminarse directamente — primero hay que trasladar todo
    el stock (sección 7.1).
    """

    nombre = models.CharField(
        _("nombre"),
        max_length=100,
    )
    nombre_normalizado = models.CharField(
        _("nombre normalizado"),
        max_length=100,
        db_index=True,
        blank=True,
        default="",
        editable=False,
    )
    descripcion = models.TextField(
        _("descripción"),
        blank=True,
        default="",
    )
    activo = models.BooleanField(
        _("activo"),
        default=True,
        help_text=_("Un almacén inactivo no acepta nuevos movimientos."),
    )
    eliminado = models.BooleanField(
        _("eliminado lógicamente"),
        default=False,
    )
    fecha_creacion = models.DateTimeField(_("fecha de creación"), auto_now_add=True)
    fecha_modificacion = models.DateTimeField(_("última modificación"), auto_now=True)

    class Meta:
        verbose_name = _("almacén")
        verbose_name_plural = _("almacenes")
        ordering = ["nombre"]
        constraints = [
            models.UniqueConstraint(
                fields=["nombre"],
                condition=models.Q(eliminado=False),
                name="unique_almacen_nombre_no_eliminado",
            )
        ]

    def __str__(self):
        return self.nombre

    def save(self, *args, **kwargs):
        self.nombre_normalizado = normalizar_texto(self.nombre or "")
        if "update_fields" in kwargs and kwargs["update_fields"] is not None:
            update_fields = set(kwargs["update_fields"])
            if "nombre" in update_fields:
                update_fields.add("nombre_normalizado")
            kwargs["update_fields"] = list(update_fields)
        super().save(*args, **kwargs)

    @property
    def total_articulos(self) -> int:
        """Cantidad de productos no archivados presentes en este almacén."""
        from inventario.models import Producto
        return (
            Producto.objects.filter(archivado=False)
            .filter(
                models.Q(categoria__almacen_principal=self)
                | models.Q(existencias__almacen=self)
                | models.Q(ubicacion__almacen=self)
            )
            .distinct()
            .count()
        )


class Ubicacion(models.Model):
    """Ubicación dentro de un almacén (sección/estantería).

    Un único nivel de ubicación. Nombre libre ("A", "B-03", "Estante rojo").
    No se gestiona cantidad por ubicación — el stock es por almacén
    (sección 7.2).
    """

    nombre = models.CharField(
        _("nombre"),
        max_length=100,
    )
    nombre_normalizado = models.CharField(
        _("nombre normalizado"),
        max_length=100,
        db_index=True,
        blank=True,
        default="",
        editable=False,
    )
    descripcion = models.TextField(
        _("descripción"),
        blank=True,
        default="",
    )
    almacen = models.ForeignKey(
        Almacen,
        on_delete=models.PROTECT,
        related_name="ubicaciones",
        verbose_name=_("almacén"),
    )
    activo = models.BooleanField(_("activo"), default=True)
    eliminado = models.BooleanField(_("eliminado lógicamente"), default=False)
    fecha_creacion = models.DateTimeField(_("fecha de creación"), auto_now_add=True)
    fecha_modificacion = models.DateTimeField(_("última modificación"), auto_now=True)

    class Meta:
        verbose_name = _("ubicación")
        verbose_name_plural = _("ubicaciones")
        ordering = ["almacen", "nombre"]
        constraints = [
            models.UniqueConstraint(
                fields=["almacen", "nombre"],
                condition=models.Q(eliminado=False),
                name="unique_ubicacion_almacen_nombre_no_eliminado",
            )
        ]

    def __str__(self):
        return f"{self.nombre} ({self.almacen.nombre})"

    def save(self, *args, **kwargs):
        self.nombre_normalizado = normalizar_texto(self.nombre or "")
        if "update_fields" in kwargs and kwargs["update_fields"] is not None:
            update_fields = set(kwargs["update_fields"])
            if "nombre" in update_fields:
                update_fields.add("nombre_normalizado")
            kwargs["update_fields"] = list(update_fields)
        super().save(*args, **kwargs)

    @property
    def total_articulos(self) -> int:
        """Cantidad de productos activos no archivados asignados a esta ubicación."""
        return self.productos.filter(archivado=False).count()


class Categoria(models.Model):
    """Categoría simple de productos, sin jerarquía.

    Cada categoría tiene un almacén principal obligatorio. Los productos
    de esa categoría pertenecen por defecto a ese almacén (sección 7.3).
    """

    nombre = models.CharField(
        _("nombre"),
        max_length=100,
    )
    nombre_normalizado = models.CharField(
        _("nombre normalizado"),
        max_length=100,
        db_index=True,
        blank=True,
        default="",
        editable=False,
    )
    almacen_principal = models.ForeignKey(
        Almacen,
        on_delete=models.PROTECT,
        related_name="categorias",
        verbose_name=_("almacén principal"),
        help_text=_(
            "Los productos de esta categoría pertenecen por defecto a este almacén."
        ),
    )
    activo = models.BooleanField(_("activo"), default=True)
    eliminado = models.BooleanField(_("eliminado lógicamente"), default=False)
    fecha_creacion = models.DateTimeField(_("fecha de creación"), auto_now_add=True)
    fecha_modificacion = models.DateTimeField(_("última modificación"), auto_now=True)

    class Meta:
        verbose_name = _("categoría")
        verbose_name_plural = _("categorías")
        ordering = ["nombre"]
        constraints = [
            models.UniqueConstraint(
                fields=["nombre"],
                condition=models.Q(eliminado=False),
                name="unique_categoria_nombre_no_eliminado",
            )
        ]

    def __str__(self):
        return self.nombre

    def save(self, *args, **kwargs):
        self.nombre_normalizado = normalizar_texto(self.nombre or "")
        if "update_fields" in kwargs and kwargs["update_fields"] is not None:
            update_fields = set(kwargs["update_fields"])
            if "nombre" in update_fields:
                update_fields.add("nombre_normalizado")
            kwargs["update_fields"] = list(update_fields)
        super().save(*args, **kwargs)

    @property
    def prefijo_codigo(self):
        """Primeras 3 letras en mayúsculas para generar códigos internos.

        Ejemplo: "Cables" → "CAB", "Herramientas" → "HER".
        """
        limpio = "".join(c for c in self.nombre if c.isalpha())
        return limpio[:3].upper().ljust(3, "X")

    @property
    def total_articulos(self) -> int:
        """Cantidad de productos no archivados pertenecientes a esta categoría."""
        return self.productos.filter(archivado=False).count()


class TipoCategoria(models.Model):
    """Tipo opcional ligado a una categoría para sub-clasificar artículos.

    Cada categoría puede tener 0, 1 o más tipos asociados opcionalmente
    (ej. Categoría "Herramientas Manuales" -> Tipos "Destornillador", "Alicate").
    """

    nombre = models.CharField(
        _("nombre del tipo"),
        max_length=100,
    )
    categoria = models.ForeignKey(
        Categoria,
        on_delete=models.CASCADE,
        related_name="tipos",
        verbose_name=_("categoría"),
    )
    activo = models.BooleanField(_("activo"), default=True)
    fecha_creacion = models.DateTimeField(_("fecha de creación"), auto_now_add=True)
    fecha_modificacion = models.DateTimeField(_("última modificación"), auto_now=True)

    class Meta:
        verbose_name = _("tipo de categoría")
        verbose_name_plural = _("tipos de categoría")
        unique_together = [("categoria", "nombre")]
        ordering = ["categoria", "nombre"]

    def __str__(self):
        return f"{self.nombre} ({self.categoria.nombre})"


class SecuenciaCodigo(models.Model):
    """Control de secuencia numérica atómica para prefijos de código interno.

    Garantiza que la asignación de códigos consecutivos (XXX-0001) sea atómica
    y libre de condiciones de carrera bajo alta concurrencia (§8.1).
    """

    prefijo = models.CharField(
        _("prefijo"),
        max_length=10,
        unique=True,
        db_index=True,
    )
    ultimo_valor = models.PositiveIntegerField(
        _("último valor"),
        default=0,
    )
    fecha_modificacion = models.DateTimeField(
        _("última modificación"),
        auto_now=True,
    )

    class Meta:
        verbose_name = _("secuencia de código")
        verbose_name_plural = _("secuencias de código")

    def __str__(self):
        return f"{self.prefijo}: {self.ultimo_valor}"


class Producto(models.Model):
    """Producto del inventario.

    Código interno inmutable: 3 letras de la categoría inicial + guión +
    4 dígitos (ej. CAB-0001). Una vez creado, NUNCA cambia aunque el
    producto cambie de categoría o nombre (sección 8.1).
    """

    codigo_interno = models.CharField(
        _("código interno"),
        max_length=10,
        unique=True,
        editable=False,
        help_text=_("Generado automáticamente. Inmutable."),
    )
    numero_secuencia = models.PositiveIntegerField(
        _("número de secuencia"),
        default=0,
        db_index=True,
        help_text=_("Número correlativo asignado dentro de su prefijo."),
    )
    nombre = models.CharField(
        _("nombre"),
        max_length=255,
    )
    nombre_normalizado = models.CharField(
        _("nombre normalizado"),
        max_length=255,
        db_index=True,
        blank=True,
        default="",
        editable=False,
    )
    codigo_barras = models.CharField(
        _("código de barras"),
        max_length=100,
        blank=True,
        null=True,
        unique=True,
        help_text=_("Opcional. Único cuando no es vacío (sección 8.2)."),
    )
    categoria = models.ForeignKey(
        Categoria,
        on_delete=models.PROTECT,
        related_name="productos",
        verbose_name=_("categoría"),
    )
    tipo = models.ForeignKey(
        TipoCategoria,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="productos",
        verbose_name=_("tipo (opcional)"),
        help_text=_("Subclasificación opcional ligada a la categoría del producto."),
    )
    stock_inicial = models.PositiveIntegerField(
        _("stock inicial"),
        default=0,
        help_text=_(
            "Registrado al crear el producto. Inmutable. "
            "Genera automáticamente el movimiento inicial."
        ),
    )
    stock_minimo = models.PositiveIntegerField(
        _("stock mínimo"),
        default=0,
        help_text=_(
            "Umbral de alerta. stock_disponible < stock_minimo → alerta de stock bajo."
        ),
    )
    # Conversión opcional de paquetes (sección 8.5).
    nombre_empaque = models.CharField(
        _("nombre del empaque"),
        max_length=50,
        blank=True,
        default="",
        help_text=_('Ej: "Paquete", "Caja", "Bolsa". Vacío si no aplica.'),
    )
    unidades_por_empaque = models.PositiveIntegerField(
        _("unidades por empaque"),
        null=True,
        blank=True,
        help_text=_("Ej: 12 unidades por paquete."),
    )
    descripcion = models.TextField(
        _("descripción"),
        blank=True,
        default="",
    )
    # Ubicación física única dentro del almacén (§7.2, §8 - Fase 5)
    ubicacion = models.ForeignKey(
        Ubicacion,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="productos",
        verbose_name=_("ubicación física"),
        help_text=_("Ubicación física única asignada al producto dentro de su almacén."),
    )

    activo = models.BooleanField(_("activo"), default=True)
    archivado = models.BooleanField(
        _("archivado"),
        default=False,
        help_text=_(
            "Solo Administradores/Superusuario pueden archivar. "
            "Se oculta de listas operativas pero conserva historial."
        ),
    )
    fecha_creacion = models.DateTimeField(_("fecha de creación"), auto_now_add=True)
    fecha_modificacion = models.DateTimeField(_("última modificación"), auto_now=True)

    class Meta:
        verbose_name = _("producto")
        verbose_name_plural = _("productos")
        ordering = ["codigo_interno"]

    def __str__(self):
        return f"{self.codigo_interno} — {self.nombre}"

    def save(self, *args, **kwargs):
        """Genera el código interno automáticamente al crear mediante secuencia atómica.

        Formato: XXX-0001, donde XXX son las 3 primeras letras de la
        categoría. El consecutivo es por prefijo, no por categoría
        (sección 8.1). Utiliza bloqueo pesimista en SecuenciaCodigo para
        evitar colisiones en entornos de alta concurrencia.
        """
        self.nombre_normalizado = normalizar_texto(self.nombre or "")
        if "update_fields" in kwargs and kwargs["update_fields"] is not None:
            update_fields = set(kwargs["update_fields"])
            if "nombre" in update_fields:
                update_fields.add("nombre_normalizado")
            kwargs["update_fields"] = list(update_fields)

        if not self.codigo_interno:
            prefijo = self.categoria.prefijo_codigo
            with transaction.atomic():
                secuencia, _ = SecuenciaCodigo.objects.select_for_update().get_or_create(
                    prefijo=prefijo,
                    defaults={"ultimo_valor": 0},
                )
                if secuencia.ultimo_valor == 0:
                    ultimo_prod = (
                        Producto.objects.filter(codigo_interno__startswith=f"{prefijo}-")
                        .order_by("-codigo_interno")
                        .values_list("codigo_interno", flat=True)
                        .first()
                    )
                    if ultimo_prod:
                        try:
                            secuencia.ultimo_valor = int(ultimo_prod.split("-")[1])
                        except (IndexError, ValueError):
                            pass

                SecuenciaCodigo.objects.filter(pk=secuencia.pk).update(
                    ultimo_valor=models.F("ultimo_valor") + 1
                )
                secuencia.refresh_from_db()
                self.numero_secuencia = secuencia.ultimo_valor
                self.codigo_interno = f"{prefijo}-{self.numero_secuencia:04d}"
                super().save(*args, **kwargs)
        else:
            if not self.numero_secuencia:
                try:
                    self.numero_secuencia = int(self.codigo_interno.split("-")[1])
                except (IndexError, ValueError):
                    pass
            super().save(*args, **kwargs)


    def clean(self):
        super().clean()
        if self.ubicacion_id and self.categoria_id:
            alm_cat = self.categoria.almacen_principal_id
            if self.ubicacion.almacen_id != alm_cat:
                from django.core.exceptions import ValidationError
                raise ValidationError(
                    f"La ubicación '{self.ubicacion.nombre}' está en '{self.ubicacion.almacen.nombre}', "
                    f"pero la categoría del producto pertenece a '{self.categoria.almacen_principal.nombre}'."
                )

    @property
    def ubicaciones(self):
        """Adaptador de compatibilidad hacia atrás con la API M2M previa."""
        return _UbicacionesCompatManager(self)


class _UbicacionesCompatManager:
    """Administrador proxy para compatibilidad hacia atrás con la API M2M previa."""

    def __init__(self, producto):
        self.producto = producto

    def all(self):
        if self.producto.ubicacion_id:
            return Ubicacion.objects.filter(pk=self.producto.ubicacion_id)
        return Ubicacion.objects.none()

    def filter(self, *args, **kwargs):
        return self.all().filter(*args, **kwargs)

    def exclude(self, *args, **kwargs):
        return self.all().exclude(*args, **kwargs)

    def select_related(self, *args, **kwargs):
        return self.all().select_related(*args, **kwargs)

    def first(self):
        return self.producto.ubicacion

    def count(self):
        return 1 if self.producto.ubicacion_id else 0

    def exists(self):
        return bool(self.producto.ubicacion_id)

    def add(self, *ubicaciones, **kwargs):
        if ubicaciones:
            self.producto.ubicacion = ubicaciones[0]
            self.producto.save(update_fields=["ubicacion"])

    def remove(self, *ubicaciones, **kwargs):
        if self.producto.ubicacion in ubicaciones:
            self.producto.ubicacion = None
            self.producto.save(update_fields=["ubicacion"])

    def clear(self):
        self.producto.ubicacion = None
        self.producto.save(update_fields=["ubicacion"])

    def __iter__(self):
        return iter(self.all())


# ═══════════════════════════════════════════════════════════════════════════
# COMPATIBILIDAD RETROSPECTIVA: ProductoUbicacion
# ═══════════════════════════════════════════════════════════════════════════

class _ProductoUbicacionCompatProxy:
    def __init__(self, producto, ubicacion):
        self.producto = producto
        self.ubicacion = ubicacion
        self.producto_id = producto.id if producto else None
        self.ubicacion_id = ubicacion.id if ubicacion else None

    def __repr__(self):
        return f"<ProductoUbicacionCompat: prod={self.producto_id}, ubic={self.ubicacion_id}>"


class _ProductoUbicacionCompatQuerySet:
    def __init__(self, items=None):
        self._items = items if items is not None else []

    def exists(self):
        return len(self._items) > 0

    def count(self):
        return len(self._items)

    def first(self):
        return self._items[0] if self._items else None

    def values_list(self, field, flat=False):
        return [getattr(it, field, None) for it in self._items]

    def __iter__(self):
        return iter(self._items)

    def __len__(self):
        return len(self._items)


class _ProductoUbicacionCompatManager:
    def create(self, producto=None, ubicacion=None, **kwargs):
        if producto and ubicacion:
            producto.ubicacion = ubicacion
            producto.save(update_fields=["ubicacion"])
        return _ProductoUbicacionCompatProxy(producto, ubicacion)

    def get_or_create(self, producto=None, ubicacion=None, **kwargs):
        if producto and ubicacion:
            producto.ubicacion = ubicacion
            producto.save(update_fields=["ubicacion"])
        return _ProductoUbicacionCompatProxy(producto, ubicacion), True

    def filter(self, **kwargs):
        items = []
        prod = kwargs.get("producto")
        ubic = kwargs.get("ubicacion")
        if prod:
            prod_obj = prod if isinstance(prod, Producto) else Producto.objects.filter(pk=prod).first()
            if prod_obj and prod_obj.ubicacion:
                if ubic is None or prod_obj.ubicacion == ubic or prod_obj.ubicacion_id == getattr(ubic, "pk", ubic):
                    items.append(_ProductoUbicacionCompatProxy(prod_obj, prod_obj.ubicacion))
        elif ubic:
            ubic_id = getattr(ubic, "pk", ubic)
            for p in Producto.objects.filter(ubicacion_id=ubic_id):
                items.append(_ProductoUbicacionCompatProxy(p, p.ubicacion))
        else:
            for p in Producto.objects.filter(ubicacion__isnull=False):
                items.append(_ProductoUbicacionCompatProxy(p, p.ubicacion))
        return _ProductoUbicacionCompatQuerySet(items)

    def all(self):
        return self.filter()


class _ProductoUbicacionCompat:
    """Clase proxy de compatibilidad para evitar roturas en código o pruebas legadas."""
    objects = _ProductoUbicacionCompatManager()


ProductoUbicacion = _ProductoUbicacionCompat


# ═══════════════════════════════════════════════════════════════════════════
# EXISTENCIAS
# ═══════════════════════════════════════════════════════════════════════════


class Existencia(models.Model):
    """Stock de un producto en un almacén específico.

    Regla de negocio estricta: un producto solo puede residir en un único almacén.
    Separa stock_disponible de stock_en_prestamo.

    Toda modificación DEBE ocurrir dentro de transaction.atomic() con
    select_for_update() para evitar condiciones de carrera (sección 9).
    """

    producto = models.ForeignKey(
        Producto,
        on_delete=models.CASCADE,
        related_name="existencias",
        verbose_name=_("producto"),
    )
    almacen = models.ForeignKey(
        Almacen,
        on_delete=models.PROTECT,
        related_name="existencias",
        verbose_name=_("almacén"),
    )
    stock_disponible = models.PositiveIntegerField(
        _("stock disponible"),
        default=0,
        help_text=_("Unidades físicamente disponibles en este almacén."),
    )
    stock_en_prestamo = models.PositiveIntegerField(
        _("stock en préstamo"),
        default=0,
        help_text=_("Unidades actualmente prestadas desde este almacén."),
    )

    class Meta:
        verbose_name = _("existencia")
        verbose_name_plural = _("existencias")
        indexes = [
            models.Index(fields=["producto", "stock_disponible"], name="idx_existencia_prod_disp"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["producto"],
                name="unique_existencia_un_solo_almacen",
            ),
            models.CheckConstraint(
                condition=models.Q(stock_disponible__gte=0),
                name="chk_existencia_stock_disponible_gte_0",
            ),
            models.CheckConstraint(
                condition=models.Q(stock_en_prestamo__gte=0),
                name="chk_existencia_stock_en_prestamo_gte_0",
            ),
        ]

    def __str__(self):
        return (
            f"{self.producto.codigo_interno} en {self.almacen.nombre}: "
            f"{self.stock_disponible} disponible, {self.stock_en_prestamo} en préstamo"
        )

    def clean(self):
        super().clean()
        if self.producto_id and self.almacen_id:
            alm_cat = self.producto.categoria.almacen_principal_id
            if self.almacen_id != alm_cat:
                from django.core.exceptions import ValidationError
                raise ValidationError(
                    f"El producto '{self.producto.nombre}' pertenece a la categoría '{self.producto.categoria.nombre}' "
                    f"cuyo almacén es '{self.producto.categoria.almacen_principal.nombre}'. No puede tener existencias en '{self.almacen.nombre}'."
                )
            otra = Existencia.objects.filter(producto_id=self.producto_id).exclude(pk=self.pk)
            if otra.exists():
                from django.core.exceptions import ValidationError
                raise ValidationError(
                    f"El producto '{self.producto.nombre}' ya tiene existencias registradas en '{otra.first().almacen.nombre}'. "
                    "Un producto solo puede residir en un único almacén."
                )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    @property
    def stock_total(self):
        """Stock total = disponible + prestado."""
        return self.stock_disponible + self.stock_en_prestamo

    @property
    def stock_bajo(self):
        """True si stock_disponible < stock_minimo y > 0 (sección 8.4)."""
        return 0 < self.stock_disponible < self.producto.stock_minimo

    @property
    def sin_stock(self):
        """True si stock_disponible == 0 (sección 8.4)."""
        return self.stock_disponible == 0


# ═══════════════════════════════════════════════════════════════════════════
# MOVIMIENTOS
# ═══════════════════════════════════════════════════════════════════════════


class Movimiento(models.Model):
    """Registro de un movimiento de inventario.

    Cada cambio de existencias genera un movimiento dentro de una
    transacción atómica. Los movimientos confirmados son inmutables
    y nunca se eliminan con DELETE (sección 9.6).
    """

    class Tipo(models.TextChoices):
        ENTRADA = "ENTRADA", _("Entrada")
        SALIDA = "SALIDA", _("Dotación")
        PRESTAMO = "PRESTAMO", _("Préstamo")
        DEVOLUCION = "DEVOLUCION", _("Devolución")
        BAJA_PRESTAMO = "BAJA_PRESTAMO", _("Baja de préstamo por daño/pérdida")
        TRASLADO_INTERNO = "TRASLADO_INTERNO", _("Traslado interno (mismo almacén)")
        TRASLADO_ENTRE = "TRASLADO_ENTRE", _("Traslado entre almacenes")
        AJUSTE_CONTEO = "AJUSTE_CONTEO", _("Ajuste por conteo físico")
        MOVIMIENTO_INICIAL = "MOVIMIENTO_INICIAL", _("Movimiento inicial de creación")
        REVERSION = "REVERSION", _("Reversión/Movimiento compensatorio")

    class Estado(models.TextChoices):
        CONFIRMADO = "CONFIRMADO", _("Confirmado")
        REVERTIDO = "REVERTIDO", _("Revertido")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Clave de idempotencia: evita duplicados por doble clic o reintento
    # de red/voz (sección 9.1).
    clave_idempotencia = models.CharField(
        _("clave de idempotencia"),
        max_length=100,
        unique=True,
        help_text=_(
            "Clave única por operación para evitar duplicados "
            "por doble clic o reintento de red."
        ),
    )

    tipo = models.CharField(
        _("tipo de movimiento"),
        max_length=25,
        choices=Tipo.choices,
    )

    producto = models.ForeignKey(
        Producto,
        on_delete=models.PROTECT,
        related_name="movimientos",
        verbose_name=_("producto"),
    )

    cantidad = models.PositiveIntegerField(
        _("cantidad"),
        validators=[MinValueValidator(1)],
        help_text=_("Cantidad en unidades base."),
    )

    # Almacenes involucrados.
    almacen_origen = models.ForeignKey(
        Almacen,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="movimientos_origen",
        verbose_name=_("almacén origen"),
    )
    almacen_destino = models.ForeignKey(
        Almacen,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="movimientos_destino",
        verbose_name=_("almacén destino"),
        help_text=_("Solo para traslados entre almacenes."),
    )

    # Ubicaciones involucradas.
    ubicacion_origen = models.ForeignKey(
        Ubicacion,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="movimientos_origen",
        verbose_name=_("ubicación origen"),
    )
    ubicacion_destino = models.ForeignKey(
        Ubicacion,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="movimientos_destino",
        verbose_name=_("ubicación destino"),
    )

    # Usuario que registra el movimiento.
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="movimientos",
        verbose_name=_("usuario"),
    )

    fecha_hora = models.DateTimeField(
        _("fecha y hora"),
        auto_now_add=True,
        help_text=_("Zona horaria: America/Bogota."),
    )

    motivo = models.TextField(
        _("motivo / observación"),
        blank=True,
        default="",
        help_text=_("Obligatorio en salidas. Opcional en otros tipos."),
    )

    destinatario = models.CharField(
        _("destinatario"),
        max_length=255,
        blank=True,
        default="",
        help_text=_(
            "Persona que recibe (texto libre). "
            "Obligatorio en salidas y préstamos (sección 9.3, 9.4)."
        ),
    )

    # Snapshot del stock al momento del movimiento.
    stock_disponible_anterior = models.PositiveIntegerField(
        _("stock disponible anterior"),
        help_text=_("Stock disponible ANTES de este movimiento."),
    )
    stock_disponible_posterior = models.PositiveIntegerField(
        _("stock disponible posterior"),
        help_text=_("Stock disponible DESPUÉS de este movimiento."),
    )

    estado = models.CharField(
        _("estado"),
        max_length=15,
        choices=Estado.choices,
        default=Estado.CONFIRMADO,
    )

    # Referencia al movimiento original (en reversiones y devoluciones).
    movimiento_original = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="movimientos_derivados",
        verbose_name=_("movimiento original"),
        help_text=_("Referencia al movimiento que se revierte o devuelve."),
    )

    observacion = models.TextField(
        _("observación adicional"),
        blank=True,
        default="",
    )

    # Marca si fue ejecutado vía Haku (para auditoría).
    via_haku = models.BooleanField(
        _("ejecutado vía Haku"),
        default=False,
    )

    class Meta:
        verbose_name = _("movimiento")
        verbose_name_plural = _("movimientos")
        ordering = ["-fecha_hora"]
        indexes = [
            models.Index(fields=["producto", "-fecha_hora"]),
            models.Index(fields=["almacen_origen", "-fecha_hora"]),
            models.Index(fields=["tipo", "-fecha_hora"]),
            models.Index(fields=["usuario", "-fecha_hora"]),
            models.Index(fields=["fecha_hora", "tipo"], name="idx_mov_fecha_tipo"),
        ]

    def __str__(self):
        return (
            f"{self.get_tipo_display()} — {self.producto.codigo_interno} "
            f"x{self.cantidad} ({self.fecha_hora:%Y-%m-%d %H:%M})"
        )


# ═══════════════════════════════════════════════════════════════════════════
# PRÉSTAMOS
# ═══════════════════════════════════════════════════════════════════════════


class Prestamo(models.Model):
    """Préstamo de productos con tracking de devoluciones parciales.

    Cada préstamo mantiene un saldo de cantidad_pendiente_devolucion
    y un estado (ABIERTO, DEVUELTO_PARCIAL, CERRADO) — sección 9.4.
    """

    class Estado(models.TextChoices):
        ABIERTO = "ABIERTO", _("Abierto")
        DEVUELTO_PARCIAL = "DEVUELTO_PARCIAL", _("Devuelto parcial")
        CERRADO = "CERRADO", _("Cerrado")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    movimiento = models.OneToOneField(
        Movimiento,
        on_delete=models.PROTECT,
        related_name="prestamo",
        verbose_name=_("movimiento de préstamo"),
        help_text=_("Movimiento de tipo PRESTAMO que originó este préstamo."),
    )

    producto = models.ForeignKey(
        Producto,
        on_delete=models.PROTECT,
        related_name="prestamos",
        verbose_name=_("producto"),
    )
    almacen = models.ForeignKey(
        Almacen,
        on_delete=models.PROTECT,
        related_name="prestamos",
        verbose_name=_("almacén origen"),
    )
    cantidad_original = models.PositiveIntegerField(
        _("cantidad original"),
        validators=[MinValueValidator(1)],
    )
    cantidad_pendiente_devolucion = models.PositiveIntegerField(
        _("cantidad pendiente de devolución"),
    )
    destinatario = models.CharField(
        _("destinatario / responsable"),
        max_length=255,
        help_text=_("Persona responsable que recibe el artículo."),
    )
    ubicacion_origen = models.ForeignKey(
        Ubicacion,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="prestamos_origen",
        verbose_name=_("ubicación origen"),
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="prestamos_registrados",
        verbose_name=_("registrado por"),
    )
    estado = models.CharField(
        _("estado"),
        max_length=20,
        choices=Estado.choices,
        default=Estado.ABIERTO,
    )
    fecha_creacion = models.DateTimeField(_("fecha de creación"), auto_now_add=True)
    fecha_cierre = models.DateTimeField(
        _("fecha de cierre"),
        null=True,
        blank=True,
    )

    class Meta:
        verbose_name = _("préstamo")
        verbose_name_plural = _("préstamos")
        ordering = ["-fecha_creacion"]
        indexes = [
            models.Index(fields=["estado", "-fecha_creacion"]),
            models.Index(fields=["destinatario"]),
            models.Index(fields=["producto", "estado"], name="idx_prestamo_prod_estado"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(cantidad_pendiente_devolucion__gte=0)
                & models.Q(cantidad_pendiente_devolucion__lte=models.F("cantidad_original")),
                name="chk_prestamo_pendiente_valida",
            ),
        ]

    def __str__(self):
        return (
            f"Préstamo {self.id!s:.8} — {self.producto.codigo_interno} "
            f"x{self.cantidad_original} a {self.destinatario} ({self.get_estado_display()})"
        )


class DevolucionPrestamo(models.Model):
    """Devolución (total, parcial) o baja por daño de un préstamo.

    - DEVOLUCION: suma lo devuelto al stock_disponible.
    - BAJA_DANO: reduce cantidad_pendiente pero NO suma al stock (sección 9.4).
    """

    class Tipo(models.TextChoices):
        DEVOLUCION = "DEVOLUCION", _("Devolución")
        BAJA_DANO = "BAJA_DANO", _("Baja por daño/pérdida")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    prestamo = models.ForeignKey(
        Prestamo,
        on_delete=models.PROTECT,
        related_name="devoluciones",
        verbose_name=_("préstamo"),
    )
    cantidad = models.PositiveIntegerField(
        _("cantidad"),
        validators=[MinValueValidator(1)],
    )
    tipo = models.CharField(
        _("tipo"),
        max_length=15,
        choices=Tipo.choices,
    )
    motivo = models.TextField(
        _("motivo"),
        help_text=_("Obligatorio. Describe la razón de la devolución o baja."),
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="devoluciones_registradas",
        verbose_name=_("registrado por"),
    )
    movimiento = models.OneToOneField(
        Movimiento,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="devolucion_prestamo",
        verbose_name=_("movimiento asociado"),
        help_text=_(
            "Movimiento de inventario generado por esta devolución. "
            "Null en bajas por daño (no generan movimiento de stock)."
        ),
    )
    fecha = models.DateTimeField(_("fecha"), auto_now_add=True)

    class Meta:
        verbose_name = _("devolución de préstamo")
        verbose_name_plural = _("devoluciones de préstamo")
        ordering = ["-fecha"]

    def __str__(self):
        return (
            f"{self.get_tipo_display()} — {self.prestamo.producto.codigo_interno} "
            f"x{self.cantidad}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# CONTEO FÍSICO
# ═══════════════════════════════════════════════════════════════════════════


class ConteoFisico(models.Model):
    """Conteo físico de inventario con confirmación de administrador.

    Cualquier usuario del almacén puede registrar la cantidad contada.
    Solo un Administrador o Superusuario puede confirmar y aplicar el
    ajuste, generando el movimiento de tipo AJUSTE_CONTEO (sección 9.7).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    producto = models.ForeignKey(
        Producto,
        on_delete=models.PROTECT,
        related_name="conteos",
        verbose_name=_("producto"),
    )
    almacen = models.ForeignKey(
        Almacen,
        on_delete=models.PROTECT,
        related_name="conteos",
        verbose_name=_("almacén"),
    )
    cantidad_contada = models.PositiveIntegerField(
        _("cantidad contada"),
        help_text=_("Cantidad física verificada."),
    )
    stock_sistema = models.PositiveIntegerField(
        _("stock en sistema"),
        help_text=_("Stock registrado en el sistema al momento del conteo."),
    )
    diferencia = models.IntegerField(
        _("diferencia"),
        help_text=_("cantidad_contada - stock_sistema. Puede ser negativo."),
    )

    # Quién contó.
    usuario_conteo = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="conteos_realizados",
        verbose_name=_("contado por"),
    )
    fecha_conteo = models.DateTimeField(_("fecha del conteo"), auto_now_add=True)

    # Confirmación / Resolución (solo admin/super).
    class Estado(models.TextChoices):
        PENDIENTE = "PENDIENTE", _("Pendiente")
        CONFIRMADO = "CONFIRMADO", _("Aprobado")
        RECHAZADO = "RECHAZADO", _("Rechazado")

    estado = models.CharField(
        _("estado"),
        max_length=15,
        choices=Estado.choices,
        default=Estado.PENDIENTE,
        db_index=True,
    )
    motivo_rechazo = models.TextField(
        _("motivo de rechazo"),
        blank=True,
        default="",
        help_text=_("Explicación u observación del administrador en caso de rechazo."),
    )
    usuario_confirmacion = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="conteos_confirmados",
        verbose_name=_("resuelto por"),
    )
    fecha_confirmacion = models.DateTimeField(
        _("fecha de resolución"),
        null=True,
        blank=True,
    )
    movimiento_ajuste = models.OneToOneField(
        Movimiento,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="conteo_fisico",
        verbose_name=_("movimiento de ajuste"),
    )
    motivo = models.TextField(
        _("motivo / observación"),
        blank=True,
        default="",
    )

    class Meta:
        verbose_name = _("conteo físico")
        verbose_name_plural = _("conteos físicos")
        ordering = ["-fecha_conteo"]
        indexes = [
            models.Index(fields=["estado", "-fecha_conteo"]),
            models.Index(fields=["producto", "estado"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(estado__in=["PENDIENTE", "CONFIRMADO", "RECHAZADO"]),
                name="chk_conteo_estado_valido",
            ),
            models.UniqueConstraint(
                fields=["producto"],
                condition=models.Q(estado="PENDIENTE"),
                name="unique_conteo_pendiente_por_producto",
            ),
        ]

    def __init__(self, *args, **kwargs):
        if "confirmado" in kwargs:
            conf = kwargs.pop("confirmado")
            if "estado" not in kwargs:
                kwargs["estado"] = self.Estado.CONFIRMADO if conf else self.Estado.PENDIENTE
        super().__init__(*args, **kwargs)

    @property
    def confirmado(self) -> bool:
        """Compatibilidad hacia atrás: True si el estado es CONFIRMADO."""
        return self.estado == self.Estado.CONFIRMADO

    @confirmado.setter
    def confirmado(self, value: bool) -> None:
        self.estado = self.Estado.CONFIRMADO if value else self.Estado.PENDIENTE

    @property
    def usuario_resolucion(self):
        return self.usuario_confirmacion

    @property
    def fecha_resolucion(self):
        return self.fecha_confirmacion

    def __str__(self):
        iconos = {
            self.Estado.PENDIENTE: "⏳",
            self.Estado.CONFIRMADO: "✓",
            self.Estado.RECHAZADO: "✗",
        }
        icono = iconos.get(self.estado, "?")
        return (
            f"{icono} Conteo {self.producto.codigo_interno} en "
            f"{self.almacen.nombre}: contado={self.cantidad_contada}, "
            f"sistema={self.stock_sistema}, dif={self.diferencia:+d} ({self.get_estado_display()})"
        )

    def save(self, *args, **kwargs):
        """Calcula la diferencia automáticamente."""
        self.diferencia = self.cantidad_contada - self.stock_sistema
        super().save(*args, **kwargs)


# ═══════════════════════════════════════════════════════════════════════════
# AUDITORÍA
# ═══════════════════════════════════════════════════════════════════════════


class Auditoria(models.Model):
    """Registro de auditoría inmutable.

    Obligatorio para toda creación, edición, cambio de estado, archivado,
    fusión, traslado, movimiento, cambio de permisos, recuperación de
    superusuario, copia de seguridad y acción vía Haku que modifique datos
    (sección 10).

    INMUTABLE desde la aplicación web: sin botones ni endpoints de
    edición o borrado, incluso para el superusuario.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    fecha_hora = models.DateTimeField(
        _("fecha y hora"),
        auto_now_add=True,
        help_text=_("Zona horaria: America/Bogota."),
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="registros_auditoria",
        verbose_name=_("usuario"),
        help_text=_("Null para acciones del sistema."),
    )
    accion = models.CharField(
        _("acción"),
        max_length=100,
        help_text=_('Ej: "CREAR", "EDITAR", "ARCHIVAR", "FUSIONAR", "MOVIMIENTO".'),
    )
    tipo_entidad = models.CharField(
        _("tipo de entidad"),
        max_length=50,
        help_text=_('Ej: "Producto", "Movimiento", "Usuario".'),
    )
    id_entidad = models.CharField(
        _("ID de la entidad afectada"),
        max_length=100,
    )
    valores_anteriores = models.JSONField(
        _("valores anteriores"),
        null=True,
        blank=True,
        help_text=_("Instantánea JSON del estado anterior."),
    )
    valores_nuevos = models.JSONField(
        _("valores nuevos"),
        null=True,
        blank=True,
        help_text=_("Instantánea JSON del nuevo estado."),
    )
    motivo = models.TextField(
        _("motivo"),
        blank=True,
        default="",
    )
    resultado = models.CharField(
        _("resultado"),
        max_length=50,
        blank=True,
        default="OK",
    )
    via_haku = models.BooleanField(
        _("ejecutado vía Haku"),
        default=False,
    )

    class Meta:
        verbose_name = _("registro de auditoría")
        verbose_name_plural = _("registros de auditoría")
        ordering = ["-fecha_hora"]
        indexes = [
            models.Index(fields=["tipo_entidad", "-fecha_hora"]),
            models.Index(fields=["usuario", "-fecha_hora"]),
            models.Index(fields=["accion"]),
        ]
        # No se permite editar ni borrar desde la app.
        default_permissions = ("add", "view")

    def __str__(self):
        return (
            f"[{self.fecha_hora:%Y-%m-%d %H:%M}] {self.accion} "
            f"{self.tipo_entidad} #{self.id_entidad}"
        )


class AlertaStockLeida(models.Model):
    """Registro persistente de alertas de stock leídas por usuario."""

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="alertas_stock_leidas",
        verbose_name=_("usuario"),
    )
    clave = models.CharField(_("clave de alerta"), max_length=120, db_index=True)
    fecha_leida = models.DateTimeField(_("fecha de lectura"), auto_now_add=True)

    class Meta:
        verbose_name = _("alerta de stock leída")
        verbose_name_plural = _("alertas de stock leídas")
        unique_together = ("usuario", "clave")
        indexes = [
            models.Index(fields=["usuario", "clave"]),
        ]

    def __str__(self):
        return f"{self.usuario.username} - {self.clave}"
