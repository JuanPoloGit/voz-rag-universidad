"""Formularios para la aplicación de inventario."""

from django import forms

from inventario.models import Almacen, Categoria, Producto, TipoCategoria, Ubicacion


class AlmacenForm(forms.ModelForm):
    """Formulario para creación y edición de almacenes."""

    class Meta:
        model = Almacen
        fields = ["nombre", "descripcion"]
        widgets = {
            "nombre": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "Ej. Bodega Central, Depósito 1",
                "autofocus": True,
            }),
            "descripcion": forms.Textarea(attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": "Descripción o notas del almacén (opcional)",
            }),
        }


class UbicacionForm(forms.ModelForm):
    """Formulario para creación y edición de ubicaciones."""

    class Meta:
        model = Ubicacion
        fields = ["nombre", "almacen", "descripcion"]
        widgets = {
            "nombre": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "Ej. Estante A-1, Cajón 03, Rack Sur",
                "autofocus": True,
            }),
            "almacen": forms.Select(attrs={
                "class": "form-control",
            }),
            "descripcion": forms.Textarea(attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": "Descripción o referencia física de la ubicación (opcional)",
            }),
        }

    def __init__(self, *args, usuario=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.usuario = usuario
        if usuario and getattr(usuario, "es_usuario_normal", False):
            # Para usuario normal confinado a su almacén:
            self.fields["almacen"].queryset = Almacen.objects.filter(id=usuario.almacen_asignado_id)
            self.fields["almacen"].initial = usuario.almacen_asignado
            self.fields["almacen"].widget = forms.HiddenInput()
            self.fields["almacen"].required = False
        else:
            self.fields["almacen"].queryset = Almacen.objects.filter(activo=True, eliminado=False)

    def clean(self):
        cleaned_data = super().clean()
        if self.usuario and getattr(self.usuario, "es_usuario_normal", False):
            cleaned_data["almacen"] = self.usuario.almacen_asignado
            if "almacen" in self._errors:
                del self._errors["almacen"]
        return cleaned_data


class CategoriaForm(forms.ModelForm):
    """Formulario para creación y edición de categorías."""

    tipos_opcionales = forms.CharField(
        required=False,
        label="Tipos asociados (opcional)",
        help_text="Separa múltiples tipos por comas. Ej: Destornillador, Alicate, Martillo.",
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Ej. Destornillador, Alicate, Llave (opcional)",
        }),
    )

    class Meta:
        model = Categoria
        fields = ["nombre", "almacen_principal"]
        widgets = {
            "nombre": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "Ej. Cables, Herramientas, Sensores",
                "autofocus": True,
            }),
            "almacen_principal": forms.Select(attrs={
                "class": "form-control",
            }),
        }

    def __init__(self, *args, usuario=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.usuario = usuario
        if usuario and getattr(usuario, "es_usuario_normal", False):
            self.fields["almacen_principal"].queryset = Almacen.objects.filter(
                id=usuario.almacen_asignado_id
            )
            self.fields["almacen_principal"].initial = usuario.almacen_asignado
            self.fields["almacen_principal"].widget = forms.HiddenInput()
            self.fields["almacen_principal"].required = False
        else:
            self.fields["almacen_principal"].queryset = Almacen.objects.filter(
                activo=True, eliminado=False
            )

        if self.instance and self.instance.pk:
            tipos_actuales = list(
                self.instance.tipos.filter(activo=True).order_by("nombre").values_list("nombre", flat=True)
            )
            if tipos_actuales:
                self.fields["tipos_opcionales"].initial = ", ".join(tipos_actuales)

    def clean_tipos_opcionales(self):
        raw = self.cleaned_data.get("tipos_opcionales", "") or ""
        partes = [p.strip() for p in raw.split(",") if p.strip()]
        unicos = []
        vistos = set()
        for p in partes:
            clave = p.lower()
            if clave not in vistos:
                vistos.add(clave)
                unicos.append(p)
        return unicos

    def clean(self):
        cleaned_data = super().clean()
        if self.usuario and getattr(self.usuario, "es_usuario_normal", False):
            cleaned_data["almacen_principal"] = self.usuario.almacen_asignado
            if "almacen_principal" in self._errors:
                del self._errors["almacen_principal"]
        return cleaned_data


class TipoCategoriaForm(forms.ModelForm):
    """Formulario para crear o editar un tipo ligado a una categoría."""

    class Meta:
        model = TipoCategoria
        fields = ["categoria", "nombre"]
        widgets = {
            "categoria": forms.Select(attrs={
                "class": "form-control",
            }),
            "nombre": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "Ej. Destornillador, Cable, Solvente",
            }),
        }

    def __init__(self, *args, usuario=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.usuario = usuario
        if usuario and getattr(usuario, "es_usuario_normal", False):
            self.fields["categoria"].queryset = Categoria.objects.filter(
                activo=True, eliminado=False, almacen_principal=usuario.almacen_asignado
            ).order_by("nombre")
        else:
            self.fields["categoria"].queryset = Categoria.objects.filter(
                activo=True, eliminado=False
            ).order_by("nombre")


class ProductoForm(forms.ModelForm):
    """Formulario para creación y edición de productos."""

    ubicacion = forms.ModelChoiceField(
        queryset=Ubicacion.objects.none(),
        required=False,
        widget=forms.Select(attrs={"class": "form-control"}),
        label="Ubicación física única",
        help_text="Ubicación física única asignada para este producto.",
        empty_label="Sin ubicación física (opcional)",
    )

    class Meta:
        model = Producto
        fields = [
            "nombre",
            "categoria",
            "tipo",
            "stock_inicial",
            "stock_minimo",
            "codigo_barras",
            "nombre_empaque",
            "unidades_por_empaque",
            "descripcion",
        ]
        widgets = {
            "nombre": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "Ej. Cable HDMI 2.0 4K",
                "autofocus": True,
            }),
            "categoria": forms.Select(attrs={
                "class": "form-control",
            }),
            "tipo": forms.Select(attrs={
                "class": "form-control",
            }),
            "stock_inicial": forms.NumberInput(attrs={
                "class": "form-control",
                "min": 0,
            }),
            "stock_minimo": forms.NumberInput(attrs={
                "class": "form-control",
                "min": 0,
            }),
            "codigo_barras": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "Escaneo o ingreso manual (opcional)",
            }),
            "nombre_empaque": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "Ej. Caja, Paquete, Rollo (opcional)",
            }),
            "unidades_por_empaque": forms.NumberInput(attrs={
                "class": "form-control",
                "min": 1,
                "placeholder": "Ej. 50 (opcional)",
            }),
            "descripcion": forms.Textarea(attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": "Especificaciones técnicas o notas (opcional)",
            }),
        }

    def __init__(self, *args, usuario=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.usuario = usuario

        if usuario and getattr(usuario, "es_usuario_normal", False):
            cats_qs = Categoria.objects.filter(
                activo=True, eliminado=False, almacen_principal=usuario.almacen_asignado
            )
            self.fields["categoria"].queryset = cats_qs
            self.fields["tipo"].queryset = TipoCategoria.objects.filter(
                activo=True, categoria__in=cats_qs
            ).select_related("categoria").order_by("categoria__nombre", "nombre")
            self.fields["ubicacion"].queryset = Ubicacion.objects.filter(
                activo=True, eliminado=False, almacen=usuario.almacen_asignado
            )
        else:
            cats_qs = Categoria.objects.filter(activo=True, eliminado=False)
            self.fields["categoria"].queryset = cats_qs
            self.fields["tipo"].queryset = TipoCategoria.objects.filter(
                activo=True, categoria__in=cats_qs
            ).select_related("categoria").order_by("categoria__nombre", "nombre")
            self.fields["ubicacion"].queryset = Ubicacion.objects.filter(activo=True, eliminado=False)

        self.fields["tipo"].required = False
        self.fields["tipo"].empty_label = "Sin tipo (opcional)"

        if self.instance and self.instance.pk:
            self.fields["stock_inicial"].disabled = True
            self.fields["stock_inicial"].required = False
            self.fields["ubicacion"].initial = self.instance.ubicaciones.first()

    def clean(self):
        cleaned_data = super().clean()
        categoria = cleaned_data.get("categoria") or (self.instance.categoria if self.instance and self.instance.pk else None)
        tipo = cleaned_data.get("tipo")
        if tipo and categoria and tipo.categoria_id != categoria.id:
            self.add_error("tipo", "El tipo seleccionado no pertenece a la categoría elegida.")
        ub = cleaned_data.get("ubicacion")
        cleaned_data["ubicaciones_seleccionadas"] = [ub] if ub else []
        return cleaned_data

    def clean_codigo_barras(self):
        cb = self.cleaned_data.get("codigo_barras")
        return (cb or "").strip() or None


class EntradaForm(forms.Form):
    """Formulario para registrar entradas de inventario."""

    producto = forms.ModelChoiceField(
        queryset=Producto.objects.filter(archivado=False, activo=True),
        widget=forms.Select(attrs={"class": "form-control"}),
        label="Producto",
    )
    almacen = forms.ModelChoiceField(
        queryset=Almacen.objects.filter(activo=True, eliminado=False),
        widget=forms.Select(attrs={"class": "form-control"}),
        label="Almacén",
    )
    es_empaque = forms.BooleanField(
        required=False,
        label="¿Ingresar en paquetes/empaques?",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    cantidad = forms.IntegerField(
        required=False,
        min_value=1,
        label="Cantidad en unidades base",
        widget=forms.NumberInput(attrs={"class": "form-control", "placeholder": "Ej. 10"}),
    )
    cantidad_empaques = forms.IntegerField(
        required=False,
        min_value=1,
        label="Cantidad de empaques",
        widget=forms.NumberInput(attrs={"class": "form-control", "placeholder": "Ej. 2"}),
    )
    ubicacion = forms.ModelChoiceField(
        queryset=Ubicacion.objects.filter(activo=True, eliminado=False),
        required=False,
        widget=forms.Select(attrs={"class": "form-control"}),
        label="Ubicación física (opcional)",
    )
    motivo = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Ej. Recepción de lote, compra local"}),
        label="Motivo de la entrada",
    )
    observacion = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2, "placeholder": "Detalles adicionales (opcional)"}),
        label="Observación",
    )

    def __init__(self, *args, usuario=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.usuario = usuario
        if usuario and getattr(usuario, "es_usuario_normal", False):
            self.fields["almacen"].queryset = Almacen.objects.filter(id=usuario.almacen_asignado_id)
            self.fields["almacen"].initial = usuario.almacen_asignado
            self.fields["almacen"].widget = forms.HiddenInput()
            self.fields["producto"].queryset = Producto.objects.filter(
                archivado=False, activo=True, categoria__almacen_principal=usuario.almacen_asignado
            )
            self.fields["ubicacion"].queryset = Ubicacion.objects.filter(
                activo=True, eliminado=False, almacen=usuario.almacen_asignado
            )

    def clean(self):
        cleaned_data = super().clean()
        es_empaque = cleaned_data.get("es_empaque")
        cantidad = cleaned_data.get("cantidad")
        cantidad_empaques = cleaned_data.get("cantidad_empaques")
        producto = cleaned_data.get("producto")

        if es_empaque:
            if not cantidad_empaques:
                self.add_error("cantidad_empaques", "Debe indicar el número de empaques.")
            if producto and not producto.unidades_por_empaque:
                self.add_error("es_empaque", f"El producto '{producto.nombre}' no tiene unidades por empaque definidas.")
        else:
            if not cantidad:
                self.add_error("cantidad", "Debe indicar la cantidad de unidades base.")
        return cleaned_data


class SalidaForm(forms.Form):
    """Formulario para registrar salidas operativas de inventario."""

    producto = forms.ModelChoiceField(
        queryset=Producto.objects.filter(archivado=False, activo=True),
        widget=forms.Select(attrs={"class": "form-control"}),
        label="Producto",
    )
    almacen = forms.ModelChoiceField(
        queryset=Almacen.objects.filter(activo=True, eliminado=False),
        widget=forms.Select(attrs={"class": "form-control"}),
        label="Almacén",
    )
    cantidad = forms.IntegerField(
        min_value=1,
        label="Cantidad a retirar",
        widget=forms.NumberInput(attrs={"class": "form-control", "placeholder": "Ej. 3"}),
    )
    destinatario = forms.CharField(
        required=True,
        max_length=255,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Persona que recibe el artículo"}),
        label="Destinatario / Responsable",
    )
    motivo = forms.CharField(
        required=True,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Ej. Práctica de laboratorio, consumo técnico"}),
        label="Motivo obligatorio",
    )
    ubicacion = forms.ModelChoiceField(
        queryset=Ubicacion.objects.filter(activo=True, eliminado=False),
        required=False,
        widget=forms.Select(attrs={"class": "form-control"}),
        label="Ubicación de origen (opcional)",
    )
    observacion = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2, "placeholder": "Observación autogenerada o personalizada (opcional)"}),
        label="Observación adicional",
    )

    def __init__(self, *args, usuario=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.usuario = usuario
        if usuario and getattr(usuario, "es_usuario_normal", False):
            self.fields["almacen"].queryset = Almacen.objects.filter(id=usuario.almacen_asignado_id)
            self.fields["almacen"].initial = usuario.almacen_asignado
            self.fields["almacen"].widget = forms.HiddenInput()
            self.fields["producto"].queryset = Producto.objects.filter(
                archivado=False, activo=True, categoria__almacen_principal=usuario.almacen_asignado
            )
            self.fields["ubicacion"].queryset = Ubicacion.objects.filter(
                activo=True, eliminado=False, almacen=usuario.almacen_asignado
            )


class PrestamoForm(forms.Form):
    """Formulario para entrega de artículos en préstamo."""

    producto = forms.ModelChoiceField(
        queryset=Producto.objects.filter(archivado=False, activo=True),
        widget=forms.Select(attrs={"class": "form-control"}),
        label="Producto",
    )
    almacen = forms.ModelChoiceField(
        queryset=Almacen.objects.filter(activo=True, eliminado=False),
        widget=forms.Select(attrs={"class": "form-control"}),
        label="Almacén",
    )
    cantidad = forms.IntegerField(
        min_value=1,
        label="Cantidad a prestar",
        widget=forms.NumberInput(attrs={"class": "form-control", "placeholder": "Ej. 1"}),
    )
    destinatario = forms.CharField(
        required=True,
        max_length=255,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Nombre completo del solicitante/responsable"}),
        label="Destinatario / Responsable",
    )
    motivo = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Ej. Proyecto de grado, práctica"}),
        label="Motivo del préstamo",
    )
    ubicacion = forms.ModelChoiceField(
        queryset=Ubicacion.objects.filter(activo=True, eliminado=False),
        required=False,
        widget=forms.Select(attrs={"class": "form-control"}),
        label="Ubicación de origen (opcional)",
    )
    observacion = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2}),
        label="Observación adicional",
    )

    def __init__(self, *args, usuario=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.usuario = usuario
        if usuario and getattr(usuario, "es_usuario_normal", False):
            self.fields["almacen"].queryset = Almacen.objects.filter(id=usuario.almacen_asignado_id)
            self.fields["almacen"].initial = usuario.almacen_asignado
            self.fields["almacen"].widget = forms.HiddenInput()
            self.fields["producto"].queryset = Producto.objects.filter(
                archivado=False, activo=True, categoria__almacen_principal=usuario.almacen_asignado
            )


class DevolucionPrestamoForm(forms.Form):
    """Formulario para registrar devoluciones (totales o parciales)."""

    cantidad = forms.IntegerField(
        min_value=1,
        label="Cantidad a devolver",
        widget=forms.NumberInput(attrs={"class": "form-control"}),
    )
    motivo = forms.CharField(
        required=True,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Ej. Devolución en buen estado"}),
        label="Motivo / Estado del bien recibido",
    )
    observacion = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2}),
        label="Observación adicional",
    )


class BajaPrestamoForm(forms.Form):
    """Formulario para baja de préstamo por daño o pérdida."""

    cantidad = forms.IntegerField(
        min_value=1,
        label="Cantidad a dar de baja",
        widget=forms.NumberInput(attrs={"class": "form-control"}),
    )
    motivo = forms.CharField(
        required=True,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Ej. Pantalla rota irreparablemente, extravío"}),
        label="Motivo obligatorio del incidente",
    )
    observacion = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2}),
        label="Observación adicional",
    )


class TrasladoForm(forms.Form):
    """Formulario para traslados internos y entre almacenes."""

    producto = forms.ModelChoiceField(
        queryset=Producto.objects.filter(archivado=False, activo=True),
        widget=forms.Select(attrs={"class": "form-control"}),
        label="Producto",
    )
    almacen_origen = forms.ModelChoiceField(
        queryset=Almacen.objects.filter(activo=True, eliminado=False),
        widget=forms.Select(attrs={"class": "form-control"}),
        label="Almacén de origen",
    )
    almacen_destino = forms.ModelChoiceField(
        queryset=Almacen.objects.filter(activo=True, eliminado=False),
        widget=forms.Select(attrs={"class": "form-control"}),
        label="Almacén de destino (solo para traslado entre almacenes)",
        required=False,
    )
    ubicacion_origen = forms.ModelChoiceField(
        queryset=Ubicacion.objects.filter(activo=True, eliminado=False),
        required=False,
        widget=forms.Select(attrs={"class": "form-control"}),
        label="Ubicación física de origen",
    )
    ubicacion_destino = forms.ModelChoiceField(
        queryset=Ubicacion.objects.filter(activo=True, eliminado=False),
        required=False,
        widget=forms.Select(attrs={"class": "form-control"}),
        label="Ubicación física de destino",
    )
    cantidad = forms.IntegerField(
        min_value=1,
        label="Cantidad a trasladar",
        widget=forms.NumberInput(attrs={"class": "form-control", "placeholder": "Ej. 5"}),
    )
    motivo = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Ej. Redistribución de stock"}),
        label="Motivo del traslado",
    )

    def __init__(self, *args, usuario=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.usuario = usuario
        if usuario and getattr(usuario, "es_usuario_normal", False):
            self.fields["almacen_origen"].queryset = Almacen.objects.filter(id=usuario.almacen_asignado_id)
            self.fields["almacen_origen"].initial = usuario.almacen_asignado
            self.fields["almacen_origen"].widget = forms.HiddenInput()
            self.fields["almacen_destino"].queryset = Almacen.objects.filter(activo=True, eliminado=False).exclude(id=usuario.almacen_asignado_id)
            self.fields["producto"].queryset = Producto.objects.filter(
                archivado=False, activo=True, categoria__almacen_principal=usuario.almacen_asignado
            )


class ConteoFisicoForm(forms.Form):
    """Formulario para registro preliminar de conteo físico."""

    producto = forms.ModelChoiceField(
        queryset=Producto.objects.filter(archivado=False, activo=True),
        widget=forms.Select(attrs={"class": "form-control"}),
        label="Producto",
    )
    almacen = forms.ModelChoiceField(
        queryset=Almacen.objects.filter(activo=True, eliminado=False),
        widget=forms.Select(attrs={"class": "form-control"}),
        label="Almacén",
    )
    cantidad_contada = forms.IntegerField(
        min_value=0,
        label="Cantidad física verificada",
        widget=forms.NumberInput(attrs={"class": "form-control"}),
    )
    motivo = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2, "placeholder": "Notas de la inspección física"}),
        label="Observación / Justificación",
    )

    def __init__(self, *args, usuario=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.usuario = usuario
        if usuario and getattr(usuario, "es_usuario_normal", False):
            self.fields["almacen"].queryset = Almacen.objects.filter(id=usuario.almacen_asignado_id)
            self.fields["almacen"].initial = usuario.almacen_asignado
            self.fields["almacen"].widget = forms.HiddenInput()
            self.fields["producto"].queryset = Producto.objects.filter(
                archivado=False, activo=True, categoria__almacen_principal=usuario.almacen_asignado
            )


class ReversionForm(forms.Form):
    """Formulario para revertir un movimiento previo."""

    motivo = forms.CharField(
        required=True,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Justificación obligatoria de la reversión"}),
        label="Motivo de la corrección",
    )

