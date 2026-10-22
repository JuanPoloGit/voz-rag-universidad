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

    def validate_unique(self):
        nombre = self.cleaned_data.get("nombre")
        if nombre:
            qs = Almacen.objects.filter(nombre__iexact=nombre.strip(), eliminado=False)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                self.add_error("nombre", "Ya existe un almacén activo con este Nombre.")

        exclude = self._get_validation_exclusions()
        exclude.add("nombre")
        try:
            self.instance.validate_unique(exclude=exclude)
        except forms.ValidationError as e:
            self._update_errors(e)


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
            user_alms = usuario.get_almacenes_asignados()
            self.fields["almacen"].queryset = user_alms.order_by("nombre")
            self.fields["almacen"].initial = user_alms.first()
            if user_alms.count() == 1:
                self.fields["almacen"].widget = forms.HiddenInput()
                self.fields["almacen"].required = False
        else:
            self.fields["almacen"].queryset = Almacen.objects.filter(activo=True, eliminado=False).order_by("nombre")

    def clean(self):
        cleaned_data = super().clean()
        if self.usuario and getattr(self.usuario, "es_usuario_normal", False):
            user_alms = self.usuario.get_almacenes_asignados()
            alm = cleaned_data.get("almacen")
            if not alm and user_alms.count() == 1:
                cleaned_data["almacen"] = user_alms.first()
            elif alm and not self.usuario.tiene_acceso_almacen(alm):
                self.add_error("almacen", "No tienes acceso al almacén seleccionado.")
            if "almacen" in self._errors and cleaned_data.get("almacen"):
                del self._errors["almacen"]
        return cleaned_data

    def validate_unique(self):
        nombre = self.cleaned_data.get("nombre")
        almacen = self.cleaned_data.get("almacen")
        if nombre and almacen:
            qs = Ubicacion.objects.filter(
                almacen=almacen,
                nombre__iexact=nombre.strip(),
                eliminado=False,
            )
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                self.add_error("nombre", "Ya existe una ubicación con este/a Nombre en este almacén.")

        exclude = self._get_validation_exclusions()
        exclude.add("nombre")
        exclude.add("almacen")
        try:
            self.instance.validate_unique(exclude=exclude)
        except forms.ValidationError as e:
            self._update_errors(e)


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
            user_alms = usuario.get_almacenes_asignados()
            self.fields["almacen_principal"].queryset = user_alms.order_by("nombre")
            self.fields["almacen_principal"].initial = user_alms.first()
            if user_alms.count() == 1:
                self.fields["almacen_principal"].widget = forms.HiddenInput()
                self.fields["almacen_principal"].required = False
        else:
            self.fields["almacen_principal"].queryset = Almacen.objects.filter(
                activo=True, eliminado=False
            ).order_by("nombre")

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
            user_alms = self.usuario.get_almacenes_asignados()
            alm = cleaned_data.get("almacen_principal")
            if not alm and user_alms.count() == 1:
                cleaned_data["almacen_principal"] = user_alms.first()
            elif alm and not self.usuario.tiene_acceso_almacen(alm):
                self.add_error("almacen_principal", "No tienes acceso al almacén seleccionado.")
            if "almacen_principal" in self._errors and cleaned_data.get("almacen_principal"):
                del self._errors["almacen_principal"]
        return cleaned_data

    def validate_unique(self):
        nombre = self.cleaned_data.get("nombre")
        if nombre:
            qs = Categoria.objects.filter(nombre__iexact=nombre.strip(), eliminado=False)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                self.add_error("nombre", "Ya existe un/a categoría con este/a Nombre.")

        exclude = self._get_validation_exclusions()
        exclude.add("nombre")
        try:
            self.instance.validate_unique(exclude=exclude)
        except forms.ValidationError as e:
            self._update_errors(e)


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
            user_alms = usuario.get_almacenes_asignados()
            self.fields["categoria"].queryset = Categoria.objects.filter(
                activo=True, eliminado=False, almacen_principal__in=user_alms
            ).order_by("nombre")
        else:
            self.fields["categoria"].queryset = Categoria.objects.filter(
                activo=True, eliminado=False
            ).order_by("nombre")

    def validate_unique(self):
        nombre = self.cleaned_data.get("nombre")
        categoria = self.cleaned_data.get("categoria")
        if nombre and categoria:
            qs = TipoCategoria.objects.filter(
                categoria=categoria,
                nombre__iexact=nombre.strip(),
                eliminado=False,
            )
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                self.add_error("nombre", "Ya existe un tipo con este/a Nombre en esta categoría.")

        exclude = self._get_validation_exclusions()
        exclude.add("nombre")
        exclude.add("categoria")
        try:
            self.instance.validate_unique(exclude=exclude)
        except forms.ValidationError as e:
            self._update_errors(e)


class ProductoForm(forms.ModelForm):
    """Formulario para creación y edición de productos."""

    almacen = forms.ModelChoiceField(
        queryset=Almacen.objects.none(),
        required=False,
        widget=forms.Select(attrs={"class": "form-control"}),
        label="Almacén",
        help_text="Almacén al que pertenece este producto (asociado a su categoría y ubicación).",
        empty_label="Selecciona un almacén...",
    )
    ubicacion = forms.ModelChoiceField(
        queryset=Ubicacion.objects.none(),
        required=True,
        widget=forms.Select(attrs={"class": "form-control"}),
        label="Ubicación Física Única *",
        help_text="Ubicación física obligatoria asignada en bodega para este producto.",
        empty_label="Selecciona una ubicación física obligatoria...",
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
                "placeholder": "Ej. 60 (total unidades base)",
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
                "placeholder": "Ej. 50 (unidades por empaque)",
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
            user_alms = usuario.get_almacenes_asignados()
            cats_qs = Categoria.objects.filter(
                activo=True, eliminado=False, almacen_principal__in=user_alms
            )
            ub_qs = Ubicacion.objects.filter(
                activo=True, eliminado=False, almacen__in=user_alms
            )
            self.fields["almacen"].queryset = user_alms.order_by("nombre")
            if user_alms.count() == 1:
                self.fields["almacen"].initial = user_alms.first()
        else:
            self.fields["almacen"].queryset = Almacen.objects.filter(activo=True, eliminado=False).order_by("nombre")
            cats_qs = Categoria.objects.filter(activo=True, eliminado=False)
            ub_qs = Ubicacion.objects.filter(activo=True, eliminado=False)

        self.fields["categoria"].queryset = cats_qs.order_by("nombre")
        self.fields["ubicacion"].queryset = ub_qs.order_by("almacen__nombre", "nombre")

        # Acotar las opciones de tipo a la categoría correspondiente
        cat_id_efectiva = None
        if self.is_bound:
            cat_id_raw = self.data.get("categoria")
            if cat_id_raw and str(cat_id_raw).isdigit():
                cat_id_efectiva = int(cat_id_raw)
        elif self.instance and self.instance.pk and self.instance.categoria_id:
            cat_id_efectiva = self.instance.categoria_id

        if cat_id_efectiva:
            self.fields["tipo"].queryset = TipoCategoria.objects.filter(
                activo=True, categoria_id=cat_id_efectiva
            ).select_related("categoria").order_by("nombre")
        else:
            self.fields["tipo"].queryset = TipoCategoria.objects.filter(
                activo=True, categoria__in=cats_qs
            ).select_related("categoria").order_by("categoria__nombre", "nombre")

        self.fields["tipo"].required = False
        self.fields["tipo"].empty_label = "Sin tipo (opcional)"

        if self.instance and self.instance.pk:
            self.fields["stock_inicial"].disabled = True
            self.fields["stock_inicial"].required = False
            self.fields["almacen"].initial = self.instance.categoria.almacen_principal_id
            self.fields["ubicacion"].initial = self.instance.ubicaciones.first()

    def clean(self):
        cleaned_data = super().clean()
        categoria = cleaned_data.get("categoria") or (self.instance.categoria if self.instance and self.instance.pk else None)
        almacen = cleaned_data.get("almacen")
        tipo = cleaned_data.get("tipo")
        ub = cleaned_data.get("ubicacion")

        if not ub:
            self.add_error("ubicacion", "La ubicación física es obligatoria para registrar o editar el producto.")

        if categoria:
            if almacen and categoria.almacen_principal_id != almacen.id:
                self.add_error("categoria", f"La categoría '{categoria.nombre}' pertenece a '{categoria.almacen_principal.nombre}', no al almacén '{almacen.nombre}'.")

            if ub and ub.almacen_id != categoria.almacen_principal_id:
                self.add_error("ubicacion", f"La ubicación '{ub.nombre}' pertenece a '{ub.almacen.nombre}', pero la categoría del producto pertenece a '{categoria.almacen_principal.nombre}'.")

            if tipo and tipo.categoria_id != categoria.id:
                self.add_error("tipo", "El tipo seleccionado no pertenece a la categoría elegida.")

        if self.usuario and getattr(self.usuario, "es_usuario_normal", False):
            user_alms = self.usuario.get_almacenes_asignados()
            if almacen and not user_alms.filter(id=almacen.id).exists():
                self.add_error("almacen", "No tienes acceso al almacén seleccionado.")
            if categoria and not user_alms.filter(id=categoria.almacen_principal_id).exists():
                self.add_error("categoria", "No tienes acceso a la categoría seleccionada.")
            if ub and not user_alms.filter(id=ub.almacen_id).exists():
                self.add_error("ubicacion", "No tienes acceso a la ubicación física seleccionada.")

        cleaned_data["ubicaciones_seleccionadas"] = [ub] if ub else []
        return cleaned_data

    def clean_codigo_barras(self):
        cb = self.cleaned_data.get("codigo_barras")
        return (cb or "").strip() or None

    def validate_unique(self):
        cb = self.cleaned_data.get("codigo_barras")
        if cb:
            qs = Producto.objects.filter(codigo_barras=cb, archivado=False)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                self.add_error("codigo_barras", "Ya existe un producto activo con este Código de barras.")

        exclude = self._get_validation_exclusions()
        exclude.add("codigo_barras")
        try:
            self.instance.validate_unique(exclude=exclude)
        except forms.ValidationError as e:
            self._update_errors(e)


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
        required=True,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Ej. Recepción de lote, compra local"}),
        label="Motivo de la entrada *",
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
            user_alms = usuario.get_almacenes_asignados()
            self.fields["almacen"].queryset = user_alms.order_by("nombre")
            self.fields["almacen"].initial = user_alms.first()
            if user_alms.count() == 1:
                self.fields["almacen"].widget = forms.HiddenInput()
                self.fields["almacen"].required = False
            self.fields["producto"].queryset = Producto.objects.filter(
                archivado=False, activo=True, categoria__almacen_principal__in=user_alms
            )
            self.fields["ubicacion"].queryset = Ubicacion.objects.filter(
                activo=True, eliminado=False, almacen__in=user_alms
            )

    def clean(self):
        cleaned_data = super().clean()
        es_empaque = cleaned_data.get("es_empaque")
        cantidad = cleaned_data.get("cantidad")
        cantidad_empaques = cleaned_data.get("cantidad_empaques")
        producto = cleaned_data.get("producto")
        almacen = cleaned_data.get("almacen")

        if self.usuario and getattr(self.usuario, "es_usuario_normal", False):
            user_alms = self.usuario.get_almacenes_asignados()
            if not almacen and user_alms.count() == 1:
                almacen = user_alms.first()
                cleaned_data["almacen"] = almacen
            if almacen and not user_alms.filter(id=almacen.id).exists():
                self.add_error("almacen", "No tienes acceso al almacén seleccionado.")
            if producto and not user_alms.filter(id=producto.categoria.almacen_principal_id).exists():
                self.add_error("producto", "El producto seleccionado no pertenece a tus almacenes asignados.")

        if producto and almacen and producto.categoria.almacen_principal_id != almacen.id:
            self.add_error("almacen", f"El producto '{producto.nombre}' pertenece al almacén '{producto.categoria.almacen_principal.nombre}'.")

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
        label="Cantidad a entregar (Dotación)",
        widget=forms.NumberInput(attrs={"class": "form-control", "placeholder": "Ej. 3"}),
    )
    destinatario = forms.CharField(
        required=True,
        max_length=255,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Persona que recibe la dotación"}),
        label="Destinatario / Receptor de la dotación",
    )
    motivo = forms.CharField(
        required=True,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Ej. Asignación de equipo, dotación semestral, consumo"}),
        label="Motivo de la dotación *",
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
            user_alms = usuario.get_almacenes_asignados()
            self.fields["almacen"].queryset = user_alms.order_by("nombre")
            self.fields["almacen"].initial = user_alms.first()
            if user_alms.count() == 1:
                self.fields["almacen"].widget = forms.HiddenInput()
                self.fields["almacen"].required = False
            self.fields["producto"].queryset = Producto.objects.filter(
                archivado=False, activo=True, categoria__almacen_principal__in=user_alms
            )

    def clean(self):
        cleaned_data = super().clean()
        producto = cleaned_data.get("producto")
        almacen = cleaned_data.get("almacen")
        cantidad = cleaned_data.get("cantidad")

        if self.usuario and getattr(self.usuario, "es_usuario_normal", False):
            user_alms = self.usuario.get_almacenes_asignados()
            if not almacen and user_alms.count() == 1:
                almacen = user_alms.first()
                cleaned_data["almacen"] = almacen
            if almacen and not user_alms.filter(id=almacen.id).exists():
                self.add_error("almacen", "No tienes acceso al almacén seleccionado.")
            if producto and not user_alms.filter(id=producto.categoria.almacen_principal_id).exists():
                self.add_error("producto", "El producto seleccionado no pertenece a tus almacenes asignados.")

        if producto and almacen:
            if producto.categoria.almacen_principal_id != almacen.id:
                self.add_error("almacen", f"El producto '{producto.nombre}' pertenece al almacén '{producto.categoria.almacen_principal.nombre}'.")
            if cantidad:
                from inventario.models import Existencia
                existencia = Existencia.objects.filter(producto=producto, almacen=almacen).first()
                stock_disp = existencia.stock_disponible if existencia else 0
                if cantidad > stock_disp:
                    self.add_error("cantidad", f"No puedes entregar {cantidad} unidades. Stock disponible actual: {stock_disp} unidades.")

        return cleaned_data


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
        required=True,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Ej. Proyecto de grado, práctica de laboratorio"}),
        label="Motivo del préstamo *",
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
            user_alms = usuario.get_almacenes_asignados()
            self.fields["almacen"].queryset = user_alms.order_by("nombre")
            self.fields["almacen"].initial = user_alms.first()
            if user_alms.count() == 1:
                self.fields["almacen"].widget = forms.HiddenInput()
                self.fields["almacen"].required = False
            self.fields["producto"].queryset = Producto.objects.filter(
                archivado=False, activo=True, categoria__almacen_principal__in=user_alms
            )

    def clean(self):
        cleaned_data = super().clean()
        producto = cleaned_data.get("producto")
        almacen = cleaned_data.get("almacen")
        cantidad = cleaned_data.get("cantidad")

        if self.usuario and getattr(self.usuario, "es_usuario_normal", False):
            user_alms = self.usuario.get_almacenes_asignados()
            if not almacen and user_alms.count() == 1:
                almacen = user_alms.first()
                cleaned_data["almacen"] = almacen
            if almacen and not user_alms.filter(id=almacen.id).exists():
                self.add_error("almacen", "No tienes acceso al almacén seleccionado.")
            if producto and not user_alms.filter(id=producto.categoria.almacen_principal_id).exists():
                self.add_error("producto", "El producto seleccionado no pertenece a tus almacenes asignados.")

        if producto and almacen:
            if producto.categoria.almacen_principal_id != almacen.id:
                self.add_error("almacen", f"El producto '{producto.nombre}' pertenece al almacén '{producto.categoria.almacen_principal.nombre}'.")
            if cantidad:
                from inventario.models import Existencia
                existencia = Existencia.objects.filter(producto=producto, almacen=almacen).first()
                stock_disp = existencia.stock_disponible if existencia else 0
                if cantidad > stock_disp:
                    self.add_error("cantidad", f"No puedes prestar {cantidad} unidades. Stock disponible actual: {stock_disp} unidades.")

        return cleaned_data


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
        required=False,
        min_value=1,
        label="Cantidad a trasladar",
        widget=forms.NumberInput(attrs={"class": "form-control", "placeholder": "Cantidad total disponible"}),
    )
    motivo = forms.CharField(
        required=True,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Ej. Traslado interno o entre almacenes..."}),
        label="Motivo del traslado *",
    )

    def __init__(self, *args, usuario=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.usuario = usuario
        if usuario and getattr(usuario, "es_usuario_normal", False):
            user_alms = usuario.get_almacenes_asignados()
            self.fields["almacen_origen"].queryset = user_alms.order_by("nombre")
            self.fields["almacen_origen"].initial = user_alms.first()
            if user_alms.count() == 1:
                self.fields["almacen_origen"].widget = forms.HiddenInput()
                self.fields["almacen_origen"].required = False
                self.fields["almacen_destino"].queryset = Almacen.objects.none()
                self.fields["almacen_destino"].widget = forms.HiddenInput()
                self.fields["almacen_destino"].required = False
                self.fields["ubicacion_destino"].queryset = Ubicacion.objects.none()
                self.fields["ubicacion_destino"].widget = forms.HiddenInput()
                self.fields["ubicacion_destino"].required = False
            else:
                self.fields["almacen_destino"].queryset = user_alms.order_by("nombre")
                self.fields["ubicacion_destino"].queryset = Ubicacion.objects.filter(
                    activo=True, eliminado=False, almacen__in=user_alms
                )
            self.fields["producto"].queryset = Producto.objects.filter(
                archivado=False, activo=True, categoria__almacen_principal__in=user_alms
            )
            self.fields["ubicacion_origen"].queryset = Ubicacion.objects.filter(
                activo=True, eliminado=False, almacen__in=user_alms
            )

    def clean(self):
        cleaned_data = super().clean()
        prod = cleaned_data.get("producto")
        alm_orig = cleaned_data.get("almacen_origen")
        alm_dest = cleaned_data.get("almacen_destino")
        ub_orig = cleaned_data.get("ubicacion_origen")
        ub_dest = cleaned_data.get("ubicacion_destino")
        cant = cleaned_data.get("cantidad")
        motivo = cleaned_data.get("motivo")

        is_normal = self.usuario and getattr(self.usuario, "es_usuario_normal", False)
        user_alms = self.usuario.get_almacenes_asignados() if is_normal else None

        if prod:
            alm_orig = prod.categoria.almacen_principal
            cleaned_data["almacen_origen"] = alm_orig
            if is_normal and not user_alms.filter(id=alm_orig.id).exists():
                self.add_error("producto", "No tienes permisos sobre el almacén de este producto.")

            from inventario.models import Existencia
            ex = Existencia.objects.filter(producto=prod, almacen=alm_orig).first()
            stock_disp = ex.stock_disponible if ex else 0
            if not cant:
                cleaned_data["cantidad"] = stock_disp
                cant = stock_disp
            if stock_disp <= 0:
                self.add_error("producto", f"El producto '{prod.codigo_interno}' no tiene existencias disponibles para trasladar.")
            elif cant > stock_disp:
                self.add_error("cantidad", f"No puedes trasladar {cant} unidades. Stock disponible en origen: {stock_disp} unidades.")

            ub_actual = prod.ubicaciones.first()
            es_cambio_ubicacion_interna = ub_orig and ((ub_actual and ub_orig.id != ub_actual.id) or not ub_actual)

            if is_normal and user_alms.count() <= 1:
                if alm_dest:
                    self.add_error("almacen_destino", "Solo tienes un almacén asignado; no puedes realizar traslados hacia otros almacenes.")
                if not ub_orig:
                    self.add_error("ubicacion_origen", "Selecciona una nueva ubicación física dentro de tu almacén.")
                elif ub_actual and ub_orig.id == ub_actual.id:
                    self.add_error("ubicacion_origen", "Debes seleccionar una ubicación diferente a la actual para realizar un traslado interno.")
                cleaned_data["almacen_destino"] = None
                cleaned_data["ubicacion_destino"] = None
            elif es_cambio_ubicacion_interna and not alm_dest:
                cleaned_data["almacen_destino"] = None
                cleaned_data["ubicacion_destino"] = None
            else:
                if not alm_dest:
                    self.add_error("almacen_destino", "Debe seleccionar el almacén de destino.")
                elif alm_dest.id == alm_orig.id:
                    self.add_error("almacen_destino", "El almacén de destino debe ser diferente al de origen.")
                elif is_normal and not user_alms.filter(id=alm_dest.id).exists():
                    self.add_error("almacen_destino", "Solo puedes trasladar hacia almacenes que tengas asignados.")

                if alm_dest:
                    if not ub_dest:
                        self.add_error("ubicacion_destino", "Debe seleccionar la ubicación física en el almacén de destino.")
                    elif ub_dest.almacen_id != getattr(alm_dest, "id", None):
                        self.add_error("ubicacion_destino", "La ubicación física seleccionada no pertenece al almacén destino.")

        if not motivo:
            self.add_error("motivo", "El motivo del traslado es obligatorio.")

        return cleaned_data


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
        label="Cantidad física verificada *",
        widget=forms.NumberInput(attrs={"class": "form-control", "placeholder": "Ej. 15"}),
    )
    motivo = forms.CharField(
        required=True,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Ej. Auditoría física programada"}),
        label="Motivo del conteo *",
    )
    observacion = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2, "placeholder": "Notas de la inspección física (opcional)"}),
        label="Observación (opcional)",
    )

    def __init__(self, *args, usuario=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.usuario = usuario
        if usuario and getattr(usuario, "es_usuario_normal", False):
            user_alms = usuario.get_almacenes_asignados()
            self.fields["almacen"].queryset = user_alms.order_by("nombre")
            self.fields["almacen"].initial = user_alms.first()
            if user_alms.count() == 1:
                self.fields["almacen"].widget = forms.HiddenInput()
                self.fields["almacen"].required = False
            self.fields["producto"].queryset = Producto.objects.filter(
                archivado=False, activo=True, categoria__almacen_principal__in=user_alms
            )

    def clean(self):
        cleaned_data = super().clean()
        prod = cleaned_data.get("producto")
        almacen = cleaned_data.get("almacen")

        if self.usuario and getattr(self.usuario, "es_usuario_normal", False):
            user_alms = self.usuario.get_almacenes_asignados()
            if not almacen and user_alms.count() == 1:
                almacen = user_alms.first()
                cleaned_data["almacen"] = almacen
            if almacen and not user_alms.filter(id=almacen.id).exists():
                self.add_error("almacen", "No tienes acceso al almacén seleccionado.")
            if prod and not user_alms.filter(id=prod.categoria.almacen_principal_id).exists():
                self.add_error("producto", "El producto no pertenece a tus almacenes asignados.")

        if prod and almacen and prod.categoria.almacen_principal_id != almacen.id:
            self.add_error("almacen", f"El producto '{prod.nombre}' pertenece al almacén '{prod.categoria.almacen_principal.nombre}'.")

        return cleaned_data


class ReversionForm(forms.Form):
    """Formulario para revertir un movimiento previo."""

    motivo = forms.CharField(
        required=True,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Justificación obligatoria de la reversión"}),
        label="Motivo de la corrección",
    )

