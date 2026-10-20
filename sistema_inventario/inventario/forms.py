"""Formularios para la aplicación de inventario."""

from django import forms

from inventario.models import Almacen, Categoria, Producto, Ubicacion


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

    def clean(self):
        cleaned_data = super().clean()
        if self.usuario and getattr(self.usuario, "es_usuario_normal", False):
            cleaned_data["almacen_principal"] = self.usuario.almacen_asignado
            if "almacen_principal" in self._errors:
                del self._errors["almacen_principal"]
        return cleaned_data


class ProductoForm(forms.ModelForm):
    """Formulario para creación y edición de productos."""

    ubicaciones_seleccionadas = forms.ModelMultipleChoiceField(
        queryset=Ubicacion.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={"class": "form-control"}),
        label="Ubicaciones físicas",
        help_text="Ubicaciones físicas autorizadas para este producto.",
    )

    class Meta:
        model = Producto
        fields = [
            "nombre",
            "categoria",
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
            self.fields["categoria"].queryset = Categoria.objects.filter(
                activo=True, eliminado=False, almacen_principal=usuario.almacen_asignado
            )
            self.fields["ubicaciones_seleccionadas"].queryset = Ubicacion.objects.filter(
                activo=True, eliminado=False, almacen=usuario.almacen_asignado
            )
        else:
            self.fields["categoria"].queryset = Categoria.objects.filter(activo=True, eliminado=False)
            self.fields["ubicaciones_seleccionadas"].queryset = Ubicacion.objects.filter(activo=True, eliminado=False)

        if self.instance and self.instance.pk:
            self.fields["stock_inicial"].disabled = True
            self.fields["stock_inicial"].required = False
            self.fields["ubicaciones_seleccionadas"].initial = self.instance.ubicaciones.values_list("id", flat=True)

    def clean_codigo_barras(self):
        cb = self.cleaned_data.get("codigo_barras")
        return (cb or "").strip() or None
