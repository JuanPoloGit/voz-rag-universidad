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
from django.db import models
from django.utils.translation import gettext_lazy as _


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
        unique=True,
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

    def __str__(self):
        return self.nombre


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
        unique_together = [("almacen", "nombre")]
        ordering = ["almacen", "nombre"]

    def __str__(self):
        return f"{self.nombre} ({self.almacen.nombre})"


class Categoria(models.Model):
    """Categoría simple de productos, sin jerarquía.

    Cada categoría tiene un almacén principal obligatorio. Los productos
    de esa categoría pertenecen por defecto a ese almacén (sección 7.3).
    """

    nombre = models.CharField(
        _("nombre"),
        max_length=100,
        unique=True,
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

    def __str__(self):
        return self.nombre

    @property
    def prefijo_codigo(self):
        """Primeras 3 letras en mayúsculas para generar códigos internos.

        Ejemplo: "Cables" → "CAB", "Herramientas" → "HER".
        """
        limpio = "".join(c for c in self.nombre if c.isalpha())
        return limpio[:3].upper().ljust(3, "X")


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
    nombre = models.CharField(
        _("nombre"),
        max_length=255,
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
    # Ubicaciones dentro de almacenes (M2M a través de ProductoUbicacion).
    ubicaciones = models.ManyToManyField(
        Ubicacion,
        through="ProductoUbicacion",
        blank=True,
        related_name="productos",
        verbose_name=_("ubicaciones"),
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
        """Genera el código interno automáticamente al crear.

        Formato: XXX-0001, donde XXX son las 3 primeras letras de la
        categoría. El consecutivo es por prefijo, no por categoría
        (sección 8.1).
        """
        if not self.codigo_interno:
            prefijo = self.categoria.prefijo_codigo
            ultimo = (
                Producto.objects
                .filter(codigo_interno__startswith=f"{prefijo}-")
                .order_by("-codigo_interno")
                .values_list("codigo_interno", flat=True)
                .first()
            )
            if ultimo:
                numero = int(ultimo.split("-")[1]) + 1
            else:
                numero = 1
            self.codigo_interno = f"{prefijo}-{numero:04d}"
        super().save(*args, **kwargs)


class ProductoUbicacion(models.Model):
    """Relación Producto ↔ Ubicación (M2M explícita).

    Un producto puede estar en varias ubicaciones dentro de un almacén.
    No se gestiona cantidad por ubicación (sección 7.2).
    """

    producto = models.ForeignKey(
        Producto,
        on_delete=models.CASCADE,
        related_name="producto_ubicaciones",
        verbose_name=_("producto"),
    )
    ubicacion = models.ForeignKey(
        Ubicacion,
        on_delete=models.PROTECT,
        related_name="producto_ubicaciones",
        verbose_name=_("ubicación"),
    )

    class Meta:
        verbose_name = _("producto en ubicación")
        verbose_name_plural = _("productos en ubicaciones")
        unique_together = [("producto", "ubicacion")]

    def __str__(self):
        return f"{self.producto.codigo_interno} en {self.ubicacion}"


# ═══════════════════════════════════════════════════════════════════════════
# EXISTENCIAS
# ═══════════════════════════════════════════════════════════════════════════


class Existencia(models.Model):
    """Stock de un producto en un almacén específico.

    Separa stock_disponible (unidades físicamente disponibles) de
    stock_en_prestamo (unidades prestadas pendientes de devolución).

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
        unique_together = [("producto", "almacen")]

    def __str__(self):
        return (
            f"{self.producto.codigo_interno} en {self.almacen.nombre}: "
            f"{self.stock_disponible} disponible, {self.stock_en_prestamo} en préstamo"
        )

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

    # Confirmación (solo admin/super).
    confirmado = models.BooleanField(_("confirmado"), default=False)
    usuario_confirmacion = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="conteos_confirmados",
        verbose_name=_("confirmado por"),
    )
    fecha_confirmacion = models.DateTimeField(
        _("fecha de confirmación"),
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

    def __str__(self):
        estado = "✓" if self.confirmado else "⏳"
        return (
            f"{estado} Conteo {self.producto.codigo_interno} en "
            f"{self.almacen.nombre}: contado={self.cantidad_contada}, "
            f"sistema={self.stock_sistema}, dif={self.diferencia:+d}"
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
