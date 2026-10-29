"""Vistas de catálogo de productos: listado, creación, edición, detalle, archivado y fusión."""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views.generic import CreateView, DetailView, ListView, UpdateView, View

from cuentas.models import UsuarioAudacIA
from cuentas.permisos import PermisoRequeridoMixin, RolRequeridoMixin
from inventario.forms import ProductoForm
from inventario.models import Almacen, Categoria, Producto, TipoCategoria, Ubicacion
from inventario.servicios.datos_maestros import (
    actualizar_producto,
    archivar_producto,
    crear_producto,
    fusionar_productos,
    toggle_activo_producto,
)
from inventario.servicios.stock import buscar_por_codigo_barras, verificar_nombre_similar
from inventario.utils import normalizar_texto
from inventario.views._comun import (
    AjaxModalFormMixin,
    _formatear_error_excepcion,
    _obtener_ip,
    _procesar_error_formulario,
)


class ProductoListaView(LoginRequiredMixin, ListView):
    """Catálogo y lista operativa de productos."""

    model = Producto
    template_name = "inventario/producto_lista.html"
    context_object_name = "productos"
    paginate_by = 10

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not (request.user.es_administrador or request.user.puede_acceder_modulo_catalogo):
            raise PermissionDenied("No tienes permisos para acceder al catálogo.")
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        q = request.GET.get("q", "").strip()
        if q:
            match = buscar_por_codigo_barras(q)
            if match:
                user = request.user
                if getattr(user, "es_usuario_normal", False):
                    if user.tiene_acceso_almacen(match.categoria.almacen_principal):
                        return redirect("inventario:producto-detalle", pk=match.pk)
                else:
                    return redirect("inventario:producto-detalle", pk=match.pk)
        return super().get(request, *args, **kwargs)

    def _resolver_filtros_validos(self):
        user = self.request.user
        es_normal = getattr(user, "es_usuario_normal", False)

        almacen_id_raw = self.request.GET.get("almacen_id", "").strip()
        categoria_id_raw = self.request.GET.get("categoria_id", "").strip()
        tipo_id_raw = self.request.GET.get("tipo_id", "").strip()
        ubicacion_id_raw = self.request.GET.get("ubicacion_id", "").strip()
        estado_activo = self.request.GET.get("estado_activo", "").strip()
        q = self.request.GET.get("q", "").strip()

        # 1. Almacén
        almacen_id = None
        user_alms = user.get_almacenes_asignados() if es_normal else Almacen.objects.filter(activo=True, eliminado=False)
        if almacen_id_raw and almacen_id_raw.isdigit():
            alm_candidate = int(almacen_id_raw)
            if user_alms.filter(id=alm_candidate).exists():
                almacen_id = alm_candidate
        elif es_normal and user_alms.count() == 1:
            almacen_id = user_alms.first().id

        # 2. Categoría (en cascada: si hay almacén seleccionado, sólo categorías de ese almacén)
        categoria_id = None
        if categoria_id_raw and categoria_id_raw.isdigit():
            cat_qs = Categoria.objects.filter(id=int(categoria_id_raw), activo=True, eliminado=False)
            if almacen_id:
                cat_qs = cat_qs.filter(almacen_principal_id=almacen_id)
            elif es_normal:
                cat_qs = cat_qs.filter(almacen_principal__in=user_alms)
            cat_obj = cat_qs.first()
            if cat_obj:
                categoria_id = cat_obj.id
                if not almacen_id:
                    almacen_id = cat_obj.almacen_principal_id

        # 3. Tipo (en cascada: si hay categoría, sólo tipos de esa categoría; si hay almacén, sólo tipos de categorías de ese almacén)
        tipo_id = None
        if tipo_id_raw and tipo_id_raw.isdigit():
            tipo_qs = TipoCategoria.objects.filter(id=int(tipo_id_raw), activo=True)
            if categoria_id:
                tipo_qs = tipo_qs.filter(categoria_id=categoria_id)
            elif almacen_id:
                tipo_qs = tipo_qs.filter(categoria__almacen_principal_id=almacen_id)
            elif es_normal:
                tipo_qs = tipo_qs.filter(categoria__almacen_principal__in=user_alms)
            tipo_obj = tipo_qs.first()
            if tipo_obj:
                tipo_id = tipo_obj.id

        # 4. Ubicación (en cascada: si hay almacén, sólo ubicaciones de ese almacén; si hay categoría, del almacén principal de la categoría)
        ubicacion_id = None
        if ubicacion_id_raw and ubicacion_id_raw.isdigit():
            ub_qs = Ubicacion.objects.filter(id=int(ubicacion_id_raw), activo=True, eliminado=False)
            if almacen_id:
                ub_qs = ub_qs.filter(almacen_id=almacen_id)
            elif categoria_id:
                cat_obj = Categoria.objects.filter(id=categoria_id).first()
                if cat_obj:
                    ub_qs = ub_qs.filter(almacen_id=cat_obj.almacen_principal_id)
            elif es_normal:
                ub_qs = ub_qs.filter(almacen__in=user_alms)
            ub_obj = ub_qs.first()
            if ub_obj:
                ubicacion_id = ub_obj.id
                if not almacen_id:
                    almacen_id = ub_obj.almacen_id

        return {
            "almacen_id": almacen_id,
            "categoria_id": categoria_id,
            "tipo_id": tipo_id,
            "ubicacion_id": ubicacion_id,
            "estado_activo": estado_activo,
            "q": q,
            "es_normal": es_normal,
        }

    def get_queryset(self):
        if not (self.request.user.es_administrador or self.request.user.tiene_permiso("catalogo.ver")):
            return Producto.objects.none()
        qs = Producto.objects.filter(archivado=False)
        f = self._resolver_filtros_validos()

        if f["es_normal"]:
            user_alms = self.request.user.get_almacenes_asignados()
            if f["almacen_id"]:
                qs = qs.filter(
                    Q(categoria__almacen_principal_id=f["almacen_id"])
                    | Q(existencias__almacen_id=f["almacen_id"])
                    | Q(ubicacion__almacen_id=f["almacen_id"])
                ).distinct()
            else:
                qs = qs.filter(
                    Q(categoria__almacen_principal__in=user_alms)
                    | Q(existencias__almacen__in=user_alms)
                    | Q(ubicacion__almacen__in=user_alms)
                ).distinct()
        elif f["almacen_id"]:
            qs = qs.filter(
                Q(categoria__almacen_principal_id=f["almacen_id"])
                | Q(existencias__almacen_id=f["almacen_id"])
                | Q(ubicacion__almacen_id=f["almacen_id"])
            ).distinct()

        if f["categoria_id"]:
            qs = qs.filter(categoria_id=f["categoria_id"])

        if f["tipo_id"]:
            qs = qs.filter(tipo_id=f["tipo_id"])

        if f["ubicacion_id"]:
            qs = qs.filter(ubicacion_id=f["ubicacion_id"]).distinct()

        if f["estado_activo"] == "activos":
            qs = qs.filter(activo=True)
        elif f["estado_activo"] == "inhabilitados":
            qs = qs.filter(activo=False)

        if f["q"]:
            q_norm = normalizar_texto(f["q"])
            qs = qs.filter(
                Q(nombre_normalizado__contains=q_norm)
                | Q(codigo_interno__icontains=f["q"])
                | Q(codigo_barras__icontains=f["q"])
                | Q(tipo__nombre__icontains=f["q"])
            )

        return (
            qs.select_related("categoria", "categoria__almacen_principal", "tipo", "ubicacion", "ubicacion__almacen")
            .prefetch_related("existencias")
            .order_by("codigo_interno")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        f = self._resolver_filtros_validos()

        # 1. Almacenes para el selector
        if f["es_normal"]:
            almacenes_qs = user.get_almacenes_asignados()
        else:
            almacenes_qs = Almacen.objects.filter(activo=True, eliminado=False).order_by("nombre")

        # 2. Categorías en cascada relacional según almacén
        cats_qs = Categoria.objects.filter(activo=True, eliminado=False)
        if f["es_normal"]:
            cats_qs = cats_qs.filter(almacen_principal__in=almacenes_qs)
        if f["almacen_id"]:
            cats_qs = cats_qs.filter(almacen_principal_id=f["almacen_id"])
        cats_qs = cats_qs.select_related("almacen_principal").order_by("nombre")

        # 3. Tipos en cascada relacional según categoría o almacén
        tipos_qs = TipoCategoria.objects.filter(activo=True)
        if f["categoria_id"]:
            tipos_qs = tipos_qs.filter(categoria_id=f["categoria_id"])
        elif f["almacen_id"]:
            tipos_qs = tipos_qs.filter(categoria__almacen_principal_id=f["almacen_id"])
        elif f["es_normal"]:
            tipos_qs = tipos_qs.filter(categoria__almacen_principal__in=almacenes_qs)
        tipos_qs = tipos_qs.select_related("categoria").order_by("nombre")

        # 4. Ubicaciones en cascada relacional según almacén o categoría
        ub_qs = Ubicacion.objects.filter(activo=True, eliminado=False)
        if f["es_normal"]:
            ub_qs = ub_qs.filter(almacen__in=almacenes_qs)
        if f["almacen_id"]:
            ub_qs = ub_qs.filter(almacen_id=f["almacen_id"])
        elif f["categoria_id"]:
            cat_obj = Categoria.objects.filter(id=f["categoria_id"]).first()
            if cat_obj:
                ub_qs = ub_qs.filter(almacen_id=cat_obj.almacen_principal_id)
        ub_qs = ub_qs.select_related("almacen").order_by("almacen__nombre", "nombre")

        context["almacenes"] = almacenes_qs
        context["categorias"] = cats_qs
        context["tipos_filtro"] = tipos_qs
        context["ubicaciones"] = ub_qs

        context["q"] = f["q"]
        context["puede_ver_catalogo"] = user.es_administrador or user.tiene_permiso("catalogo.ver")
        context["almacen_seleccionado"] = str(f["almacen_id"]) if f["almacen_id"] else ""
        context["categoria_seleccionada"] = str(f["categoria_id"]) if f["categoria_id"] else ""
        context["tipo_seleccionado"] = str(f["tipo_id"]) if f["tipo_id"] else ""
        context["ubicacion_seleccionada"] = str(f["ubicacion_id"]) if f["ubicacion_id"] else ""
        context["estado_activo"] = f["estado_activo"]

        context["producto_form"] = ProductoForm(usuario=user)

        # Listas JSON para el modal interactivo de creación/edición de producto (Jerarquía Almacén -> Ubicación / Categoría -> Tipo)
        alms_para_modal = user.get_almacenes_asignados() if f["es_normal"] else Almacen.objects.filter(activo=True, eliminado=False).order_by("nombre")
        cats_para_modal = Categoria.objects.filter(activo=True, eliminado=False, almacen_principal__in=alms_para_modal).select_related("almacen_principal").order_by("nombre")
        ubs_para_modal = Ubicacion.objects.filter(activo=True, eliminado=False, almacen__in=alms_para_modal).select_related("almacen").order_by("almacen__nombre", "nombre")
        todos_los_tipos = TipoCategoria.objects.filter(activo=True, categoria__in=cats_para_modal).select_related("categoria").order_by("nombre")

        context["almacenes_json"] = [{"id": a.id, "nombre": a.nombre} for a in alms_para_modal]
        context["categorias_json"] = [{"id": c.id, "nombre": c.nombre, "almacen_id": c.almacen_principal_id, "almacen_nombre": c.almacen_principal.nombre} for c in cats_para_modal]
        context["ubicaciones_json"] = [{"id": u.id, "nombre": u.nombre, "almacen_id": u.almacen_id, "almacen_nombre": u.almacen.nombre} for u in ubs_para_modal]
        context["tipos_por_categoria"] = [
            {"id": t.id, "nombre": t.nombre, "categoria_id": t.categoria_id}
            for t in todos_los_tipos
        ]
        return context


class ProductoCrearView(LoginRequiredMixin, PermisoRequeridoMixin, AjaxModalFormMixin, CreateView):
    """Creación de un nuevo producto en catálogo con opción de stock inicial y tipo opcional."""

    permiso_requerido = "catalogo.crear"
    model = Producto
    form_class = ProductoForm
    template_name = "inventario/producto_form.html"
    success_url = reverse_lazy("inventario:producto-lista")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["usuario"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        alms = user.get_almacenes_asignados() if getattr(user, "es_usuario_normal", False) else Almacen.objects.filter(activo=True, eliminado=False).order_by("nombre")
        cats_qs = Categoria.objects.filter(activo=True, eliminado=False, almacen_principal__in=alms).select_related("almacen_principal").order_by("nombre")
        ubs_qs = Ubicacion.objects.filter(activo=True, eliminado=False, almacen__in=alms).select_related("almacen").order_by("almacen__nombre", "nombre")
        tipos = TipoCategoria.objects.filter(activo=True, categoria__in=cats_qs).order_by("nombre")

        context["almacenes_json"] = [{"id": a.id, "nombre": a.nombre} for a in alms]
        context["categorias_json"] = [{"id": c.id, "nombre": c.nombre, "almacen_id": c.almacen_principal_id, "almacen_nombre": c.almacen_principal.nombre} for c in cats_qs]
        context["ubicaciones_json"] = [{"id": u.id, "nombre": u.nombre, "almacen_id": u.almacen_id, "almacen_nombre": u.almacen.nombre} for u in ubs_qs]
        context["tipos_por_categoria"] = [
            {"id": t.id, "nombre": t.nombre, "categoria_id": t.categoria_id}
            for t in tipos
        ]
        return context

    def form_valid(self, form):
        ubicaciones = [u.id for u in form.cleaned_data.get("ubicaciones_seleccionadas", [])]
        try:
            producto = crear_producto(
                nombre=form.cleaned_data["nombre"],
                categoria=form.cleaned_data["categoria"],
                tipo=form.cleaned_data.get("tipo"),
                stock_inicial=form.cleaned_data.get("stock_inicial", 0),
                stock_minimo=form.cleaned_data.get("stock_minimo", 0),
                codigo_barras=form.cleaned_data.get("codigo_barras", ""),
                nombre_empaque=form.cleaned_data.get("nombre_empaque", ""),
                unidades_por_empaque=form.cleaned_data.get("unidades_por_empaque"),
                descripcion=form.cleaned_data.get("descripcion", ""),
                ubicaciones=ubicaciones,
                usuario=self.request.user,
                ip_origen=_obtener_ip(self.request),
            )
            messages.success(self.request, f"Producto '{producto.codigo_interno} — {producto.nombre}' creado exitosamente.")
            return self.responder_exito(self.success_url)
        except Exception as exc:
            _procesar_error_formulario(form, exc)
            return self.form_invalid(form)


class ProductoEditarView(LoginRequiredMixin, PermisoRequeridoMixin, AjaxModalFormMixin, UpdateView):
    """Edición de datos operativos de un producto."""

    permiso_requerido = "catalogo.editar"
    model = Producto
    form_class = ProductoForm
    template_name = "inventario/producto_form.html"
    success_url = reverse_lazy("inventario:producto-lista")

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if getattr(user, "es_usuario_normal", False):
            user_alms = user.get_almacenes_asignados()
            qs = qs.filter(
                Q(categoria__almacen_principal__in=user_alms)
                | Q(existencias__almacen__in=user_alms)
                | Q(ubicacion__almacen__in=user_alms)
            ).distinct()
        return qs

    def dispatch(self, request, *args, **kwargs):
        obj = self.get_object()
        if getattr(request.user, "es_usuario_normal", False):
            user_alms = request.user.get_almacenes_asignados()
            tiene_acceso = (
                user_alms.filter(id=obj.categoria.almacen_principal_id).exists()
                or obj.existencias.filter(almacen__in=user_alms).exists()
                or (obj.ubicacion and user_alms.filter(id=obj.ubicacion.almacen_id).exists())
            )
            if not tiene_acceso:
                raise PermissionDenied("No tienes permisos para modificar productos de otro almacén.")
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["usuario"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        alms = user.get_almacenes_asignados() if getattr(user, "es_usuario_normal", False) else Almacen.objects.filter(activo=True, eliminado=False).order_by("nombre")
        cats_qs = Categoria.objects.filter(activo=True, eliminado=False).filter(
            Q(almacen_principal__in=alms) | Q(pk=self.object.categoria_id)
        ).select_related("almacen_principal").order_by("nombre")
        ubs_qs = Ubicacion.objects.filter(activo=True, eliminado=False, almacen__in=alms).select_related("almacen").order_by("almacen__nombre", "nombre")
        tipos = TipoCategoria.objects.filter(activo=True, categoria__in=cats_qs).order_by("nombre")

        context["almacenes_json"] = [{"id": a.id, "nombre": a.nombre} for a in alms]
        context["categorias_json"] = [{"id": c.id, "nombre": c.nombre, "almacen_id": c.almacen_principal_id, "almacen_nombre": c.almacen_principal.nombre} for c in cats_qs]
        context["ubicaciones_json"] = [{"id": u.id, "nombre": u.nombre, "almacen_id": u.almacen_id, "almacen_nombre": u.almacen.nombre} for u in ubs_qs]
        context["tipos_por_categoria"] = [
            {"id": t.id, "nombre": t.nombre, "categoria_id": t.categoria_id}
            for t in tipos
        ]
        return context

    def form_valid(self, form):
        ubicaciones = [u.id for u in form.cleaned_data.get("ubicaciones_seleccionadas", [])]
        try:
            producto = actualizar_producto(
                producto=self.object,
                nombre=form.cleaned_data["nombre"],
                categoria=form.cleaned_data.get("categoria"),
                tipo=form.cleaned_data.get("tipo"),
                actualizar_tipo=True,
                stock_minimo=form.cleaned_data.get("stock_minimo", 0),
                codigo_barras=form.cleaned_data.get("codigo_barras", ""),
                nombre_empaque=form.cleaned_data.get("nombre_empaque", ""),
                unidades_por_empaque=form.cleaned_data.get("unidades_por_empaque"),
                descripcion=form.cleaned_data.get("descripcion", ""),
                ubicaciones=ubicaciones,
                usuario=self.request.user,
                ip_origen=_obtener_ip(self.request),
            )
            messages.success(self.request, f"Producto '{producto.codigo_interno} — {producto.nombre}' actualizado exitosamente.")
            next_url = self.request.POST.get("next")
            return self.responder_exito(next_url or self.success_url)
        except Exception as exc:
            _procesar_error_formulario(form, exc)
            return self.form_invalid(form)


class ProductoDetalleView(LoginRequiredMixin, PermisoRequeridoMixin, DetailView):
    """Detalle completo del producto: stock por almacén, ubicaciones e historial de movimientos."""

    permiso_requerido = "catalogo.ver"
    model = Producto
    template_name = "inventario/producto_detalle.html"
    context_object_name = "producto"

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if getattr(user, "es_usuario_normal", False):
            user_alms = user.get_almacenes_asignados()
            qs = qs.filter(
                Q(categoria__almacen_principal__in=user_alms)
                | Q(existencias__almacen__in=user_alms)
                | Q(ubicacion__almacen__in=user_alms)
            ).distinct()
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        prod = self.object
        user = self.request.user
        if getattr(user, "es_usuario_normal", False):
            user_alms = user.get_almacenes_asignados()
            context["existencias"] = prod.existencias.filter(almacen__in=user_alms).select_related("almacen")
            context["movimientos"] = prod.movimientos.filter(
                Q(almacen_origen__in=user_alms) | Q(almacen_destino__in=user_alms)
            ).select_related("usuario", "almacen_origen", "almacen_destino").order_by("-fecha_hora")[:25]
            context["ubicaciones"] = [prod.ubicacion] if (prod.ubicacion and user_alms.filter(id=prod.ubicacion.almacen_id).exists()) else []
            cats_qs = Categoria.objects.filter(activo=True, eliminado=False).filter(
                Q(almacen_principal__in=user_alms) | Q(pk=prod.categoria_id)
            ).distinct()
        else:
            context["existencias"] = prod.existencias.select_related("almacen").all()
            context["movimientos"] = prod.movimientos.select_related("usuario", "almacen_origen", "almacen_destino").order_by("-fecha_hora")[:25]
            context["ubicaciones"] = [prod.ubicacion] if prod.ubicacion else []
            cats_qs = Categoria.objects.filter(activo=True, eliminado=False)

        context["producto_form"] = ProductoForm(instance=prod, usuario=user)
        alms_para_modal = user.get_almacenes_asignados() if getattr(user, "es_usuario_normal", False) else Almacen.objects.filter(activo=True, eliminado=False).order_by("nombre")
        context["almacenes_json"] = [{"id": a.id, "nombre": a.nombre} for a in alms_para_modal]
        context["categorias_json"] = [{"id": c.id, "nombre": c.nombre, "almacen_id": c.almacen_principal_id, "almacen_nombre": c.almacen_principal.nombre} for c in cats_qs]
        ubs_para_modal = Ubicacion.objects.filter(activo=True, eliminado=False, almacen__in=alms_para_modal).select_related("almacen").order_by("almacen__nombre", "nombre")
        context["ubicaciones_json"] = [{"id": u.id, "nombre": u.nombre, "almacen_id": u.almacen_id, "almacen_nombre": u.almacen.nombre} for u in ubs_para_modal]
        tipos = TipoCategoria.objects.filter(activo=True, categoria__in=cats_qs).order_by("nombre")
        context["tipos_por_categoria"] = [
            {"id": t.id, "nombre": t.nombre, "categoria_id": t.categoria_id}
            for t in tipos
        ]
        return context


class ProductoToggleActivoView(LoginRequiredMixin, PermisoRequeridoMixin, View):
    """Inhabilita o reactiva un producto sin eliminarlo del catálogo."""

    permiso_requerido = "catalogo.editar"

    def post(self, request, pk):
        producto = get_object_or_404(Producto, pk=pk, archivado=False)
        if getattr(request.user, "es_usuario_normal", False):
            user_alms = request.user.get_almacenes_asignados()
            tiene_acceso = (
                user_alms.filter(id=producto.categoria.almacen_principal_id).exists()
                or producto.existencias.filter(almacen__in=user_alms).exists()
                or (producto.ubicacion and user_alms.filter(id=producto.ubicacion.almacen_id).exists())
            )
            if not tiene_acceso:
                raise PermissionDenied("No tienes permisos para modificar productos de otro almacén.")
        try:
            actualizado = toggle_activo_producto(
                producto=producto,
                usuario=request.user,
                ip_origen=_obtener_ip(request),
            )
            estado_str = "habilitado" if actualizado.activo else "inhabilitado"
            messages.success(request, f"Producto '{actualizado.codigo_interno} — {actualizado.nombre}' {estado_str} exitosamente.")
        except Exception as exc:
            messages.error(request, _formatear_error_excepcion(exc))
        return redirect(request.POST.get("next") or "inventario:producto-lista")


class ProductoArchivarView(LoginRequiredMixin, RolRequeridoMixin, PermisoRequeridoMixin, View):
    """Eliminación / Archivado lógico de un producto (solo Administrador o Superusuario)."""

    roles_permitidos = (UsuarioAudacIA.Rol.ADMINISTRADOR, UsuarioAudacIA.Rol.SUPERUSUARIO)
    permiso_requerido = "catalogo.eliminar"

    def post(self, request, pk):
        producto = get_object_or_404(Producto, pk=pk)
        try:
            archivar_producto(
                producto=producto,
                usuario=request.user,
                ip_origen=_obtener_ip(request),
            )
            messages.success(request, f"Producto '{producto.codigo_interno} — {producto.nombre}' eliminado del catálogo activo.")
        except Exception as exc:
            messages.error(request, _formatear_error_excepcion(exc))
        return redirect("inventario:producto-lista")


class ProductoVerificarNombreView(LoginRequiredMixin, View):
    """Endpoint AJAX para advertencia de nombres similares al crear producto (§8.3)."""

    def get(self, request, *args, **kwargs):
        nombre = request.GET.get("nombre", "")
        excluir_id = request.GET.get("excluir_id")
        similares = verificar_nombre_similar(
            nombre,
            int(excluir_id) if excluir_id and str(excluir_id).isdigit() else None,
        )
        return JsonResponse({"similares": similares})


class ProductoFusionarView(LoginRequiredMixin, RolRequeridoMixin, View):
    """Fusión de producto duplicado en el producto principal (§8.3)."""

    roles_permitidos = (UsuarioAudacIA.Rol.ADMINISTRADOR, UsuarioAudacIA.Rol.SUPERUSUARIO)

    def get(self, request, pk):
        producto_principal = get_object_or_404(Producto, pk=pk)
        candidatos = Producto.objects.filter(archivado=False, activo=True).exclude(id=producto_principal.id).select_related("categoria")
        return render(
            request,
            "inventario/producto_fusionar.html",
            {
                "producto_principal": producto_principal,
                "candidatos": candidatos,
            },
        )

    def post(self, request, pk):
        producto_principal = get_object_or_404(Producto, pk=pk)
        duplicado_id = request.POST.get("producto_duplicado")
        producto_duplicado = get_object_or_404(Producto, pk=duplicado_id)

        try:
            fusionar_productos(
                producto_principal=producto_principal,
                producto_duplicado=producto_duplicado,
                usuario=request.user,
                ip_origen=_obtener_ip(request),
            )
            messages.success(
                request,
                f"Producto '{producto_duplicado.codigo_interno}' fusionado exitosamente en '{producto_principal.codigo_interno}'.",
            )
            return redirect("inventario:producto-detalle", pk=producto_principal.pk)
        except Exception as exc:
            messages.error(request, _formatear_error_excepcion(exc))
            return self.get(request, pk)
