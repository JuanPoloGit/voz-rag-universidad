"""Vistas web tradicionales de la aplicación inventario."""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views.generic import CreateView, DetailView, ListView, UpdateView, View

from cuentas.models import UsuarioAudacIA
from cuentas.permisos import RolRequeridoMixin
from inventario.forms import AlmacenForm, CategoriaForm, ProductoForm, UbicacionForm
from inventario.models import Almacen, Categoria, Existencia, Movimiento, Producto, Ubicacion
from inventario.servicios import NecesitaTraslado
from inventario.servicios.datos_maestros import (
    actualizar_almacen,
    actualizar_categoria,
    actualizar_producto,
    actualizar_ubicacion,
    archivar_producto,
    crear_almacen,
    crear_categoria,
    crear_producto,
    crear_ubicacion,
    fusionar_productos,
    reactivar_almacen,
)
from inventario.servicios.stock import (
    buscar_por_codigo_barras,
    desactivar_almacen,
    desactivar_categoria,
    desactivar_ubicacion,
    trasladar_productos_almacen,
    trasladar_productos_categoria,
    trasladar_productos_ubicacion,
    verificar_nombre_similar,
)


def _obtener_ip(request) -> str:
    """Extrae la IP origen de la petición."""
    x_forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded:
        return x_forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "127.0.0.1")


# ─────────────────────────────────────────────────────────────────────────────
# 1. ALMACENES
# ─────────────────────────────────────────────────────────────────────────────

class AlmacenListaView(LoginRequiredMixin, ListView):
    """Lista de almacenes registrados."""

    model = Almacen
    template_name = "inventario/almacen_lista.html"
    context_object_name = "almacenes"

    def get_queryset(self):
        qs = Almacen.objects.filter(eliminado=False)
        mostrar_inactivos = self.request.GET.get("mostrar_inactivos") == "1"
        if not mostrar_inactivos:
            qs = qs.filter(activo=True)
        return qs.order_by("nombre")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["mostrar_inactivos"] = self.request.GET.get("mostrar_inactivos") == "1"
        return context


class AlmacenCrearView(LoginRequiredMixin, RolRequeridoMixin, CreateView):
    """Creación de un nuevo almacén (solo Administradores y Superusuario)."""

    model = Almacen
    form_class = AlmacenForm
    template_name = "inventario/almacen_form.html"
    roles_permitidos = (UsuarioAudacIA.Rol.ADMINISTRADOR, UsuarioAudacIA.Rol.SUPERUSUARIO)
    success_url = reverse_lazy("inventario:almacen-lista")

    def form_valid(self, form):
        try:
            almacen = crear_almacen(
                nombre=form.cleaned_data["nombre"],
                descripcion=form.cleaned_data.get("descripcion", ""),
                usuario=self.request.user,
                ip_origen=_obtener_ip(self.request),
            )
            messages.success(self.request, f"Almacén '{almacen.nombre}' creado exitosamente.")
            return redirect(self.success_url)
        except Exception as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)


class AlmacenEditarView(LoginRequiredMixin, RolRequeridoMixin, UpdateView):
    """Edición de un almacén existente (solo Administradores y Superusuario)."""

    model = Almacen
    form_class = AlmacenForm
    template_name = "inventario/almacen_form.html"
    roles_permitidos = (UsuarioAudacIA.Rol.ADMINISTRADOR, UsuarioAudacIA.Rol.SUPERUSUARIO)
    success_url = reverse_lazy("inventario:almacen-lista")

    def form_valid(self, form):
        try:
            almacen = actualizar_almacen(
                almacen=self.object,
                nombre=form.cleaned_data["nombre"],
                descripcion=form.cleaned_data.get("descripcion", ""),
                usuario=self.request.user,
                ip_origen=_obtener_ip(self.request),
            )
            messages.success(self.request, f"Almacén '{almacen.nombre}' actualizado exitosamente.")
            return redirect(self.success_url)
        except Exception as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)


# ─────────────────────────────────────────────────────────────────────────────
# 2. UBICACIONES
# ─────────────────────────────────────────────────────────────────────────────

class UbicacionListaView(LoginRequiredMixin, ListView):
    """Lista de ubicaciones físicas accesibles por el usuario."""

    model = Ubicacion
    template_name = "inventario/ubicacion_lista.html"
    context_object_name = "ubicaciones"

    def get_queryset(self):
        qs = Ubicacion.objects.filter(eliminado=False, activo=True)
        user = self.request.user
        if getattr(user, "es_usuario_normal", False):
            qs = qs.filter(almacen=user.almacen_asignado)
        else:
            almacen_id = self.request.GET.get("almacen_id")
            if almacen_id:
                qs = qs.filter(almacen_id=almacen_id)
        return qs.select_related("almacen").order_by("almacen__nombre", "nombre")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if not getattr(self.request.user, "es_usuario_normal", False):
            context["almacenes"] = Almacen.objects.filter(activo=True, eliminado=False).order_by("nombre")
            context["almacen_seleccionado"] = self.request.GET.get("almacen_id")
        return context


class UbicacionCrearView(LoginRequiredMixin, CreateView):
    """Creación de ubicación en almacén autorizado."""

    model = Ubicacion
    form_class = UbicacionForm
    template_name = "inventario/ubicacion_form.html"
    success_url = reverse_lazy("inventario:ubicacion-lista")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["usuario"] = self.request.user
        return kwargs

    def form_valid(self, form):
        user = self.request.user
        if getattr(user, "es_usuario_normal", False):
            almacen = user.almacen_asignado
        else:
            almacen = form.cleaned_data.get("almacen")

        try:
            ubicacion = crear_ubicacion(
                nombre=form.cleaned_data["nombre"],
                almacen=almacen,
                descripcion=form.cleaned_data.get("descripcion", ""),
                usuario=user,
                ip_origen=_obtener_ip(self.request),
            )
            messages.success(self.request, f"Ubicación '{ubicacion.nombre}' creada exitosamente.")
            return redirect(self.success_url)
        except Exception as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)


class UbicacionEditarView(LoginRequiredMixin, UpdateView):
    """Edición de ubicación física conservando historial del nombre previo."""

    model = Ubicacion
    form_class = UbicacionForm
    template_name = "inventario/ubicacion_form.html"
    success_url = reverse_lazy("inventario:ubicacion-lista")

    def dispatch(self, request, *args, **kwargs):
        obj = self.get_object()
        if getattr(request.user, "es_usuario_normal", False):
            if not request.user.almacen_asignado or obj.almacen_id != request.user.almacen_asignado_id:
                raise PermissionDenied("No tienes permisos para modificar ubicaciones de otro almacén.")
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["usuario"] = self.request.user
        return kwargs

    def form_valid(self, form):
        try:
            ubicacion = actualizar_ubicacion(
                ubicacion=self.object,
                nombre=form.cleaned_data["nombre"],
                descripcion=form.cleaned_data.get("descripcion", ""),
                usuario=self.request.user,
                ip_origen=_obtener_ip(self.request),
            )
            messages.success(self.request, f"Ubicación '{ubicacion.nombre}' actualizada exitosamente.")
            return redirect(self.success_url)
        except Exception as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)


# ─────────────────────────────────────────────────────────────────────────────
# 3. CATEGORÍAS
# ─────────────────────────────────────────────────────────────────────────────

class CategoriaListaView(LoginRequiredMixin, ListView):
    """Lista de categorías de productos accesibles por el usuario."""

    model = Categoria
    template_name = "inventario/categoria_lista.html"
    context_object_name = "categorias"

    def get_queryset(self):
        qs = Categoria.objects.filter(eliminado=False, activo=True)
        user = self.request.user
        if getattr(user, "es_usuario_normal", False):
            qs = qs.filter(almacen_principal=user.almacen_asignado)
        else:
            almacen_id = self.request.GET.get("almacen_id")
            if almacen_id:
                qs = qs.filter(almacen_principal_id=almacen_id)
        return qs.select_related("almacen_principal").order_by("almacen_principal__nombre", "nombre")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if not getattr(self.request.user, "es_usuario_normal", False):
            context["almacenes"] = Almacen.objects.filter(activo=True, eliminado=False).order_by("nombre")
            context["almacen_seleccionado"] = self.request.GET.get("almacen_id")
        return context


class CategoriaCrearView(LoginRequiredMixin, CreateView):
    """Creación de una categoría con almacén principal obligatorio."""

    model = Categoria
    form_class = CategoriaForm
    template_name = "inventario/categoria_form.html"
    success_url = reverse_lazy("inventario:categoria-lista")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["usuario"] = self.request.user
        return kwargs

    def form_valid(self, form):
        user = self.request.user
        if getattr(user, "es_usuario_normal", False):
            almacen_principal = user.almacen_asignado
        else:
            almacen_principal = form.cleaned_data.get("almacen_principal")

        try:
            categoria = crear_categoria(
                nombre=form.cleaned_data["nombre"],
                almacen_principal=almacen_principal,
                usuario=user,
                ip_origen=_obtener_ip(self.request),
            )
            messages.success(self.request, f"Categoría '{categoria.nombre}' creada exitosamente.")
            return redirect(self.success_url)
        except Exception as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)


class CategoriaEditarView(LoginRequiredMixin, UpdateView):
    """Edición de categoría. Detecta si requiere traslado estructural al cambiar almacén."""

    model = Categoria
    form_class = CategoriaForm
    template_name = "inventario/categoria_form.html"
    success_url = reverse_lazy("inventario:categoria-lista")

    def dispatch(self, request, *args, **kwargs):
        obj = self.get_object()
        if getattr(request.user, "es_usuario_normal", False):
            if not request.user.almacen_asignado or obj.almacen_principal_id != request.user.almacen_asignado_id:
                raise PermissionDenied("No tienes permisos para modificar categorías de otro almacén.")
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["usuario"] = self.request.user
        return kwargs

    def form_valid(self, form):
        user = self.request.user
        if getattr(user, "es_usuario_normal", False):
            almacen_principal = user.almacen_asignado
        else:
            almacen_principal = form.cleaned_data.get("almacen_principal")

        try:
            categoria = actualizar_categoria(
                categoria=self.object,
                nombre=form.cleaned_data["nombre"],
                almacen_principal=almacen_principal,
                usuario=user,
                ip_origen=_obtener_ip(self.request),
            )
            messages.success(self.request, f"Categoría '{categoria.nombre}' actualizada exitosamente.")
            return redirect(self.success_url)
        except NecesitaTraslado as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)
        except Exception as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)


# ─────────────────────────────────────────────────────────────────────────────
# 4. PRODUCTOS
# ─────────────────────────────────────────────────────────────────────────────

class ProductoListaView(LoginRequiredMixin, ListView):
    """Catálogo y lista operativa de productos."""

    model = Producto
    template_name = "inventario/producto_lista.html"
    context_object_name = "productos"
    paginate_by = 25

    def get(self, request, *args, **kwargs):
        q = request.GET.get("q", "").strip()
        if q:
            match = buscar_por_codigo_barras(q)
            if match:
                user = request.user
                if getattr(user, "es_usuario_normal", False):
                    if match.categoria.almacen_principal_id == getattr(user, "almacen_asignado_id", None):
                        return redirect("inventario:producto-detalle", pk=match.pk)
                else:
                    return redirect("inventario:producto-detalle", pk=match.pk)
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        qs = Producto.objects.filter(archivado=False)
        user = self.request.user
        if getattr(user, "es_usuario_normal", False):
            qs = qs.filter(categoria__almacen_principal=user.almacen_asignado)

        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(
                Q(nombre__icontains=q)
                | Q(codigo_interno__icontains=q)
                | Q(codigo_barras__icontains=q)
            )

        categoria_id = self.request.GET.get("categoria_id")
        if categoria_id:
            qs = qs.filter(categoria_id=categoria_id)

        return qs.select_related("categoria", "categoria__almacen_principal").prefetch_related("existencias", "ubicaciones").order_by("codigo_interno")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        if getattr(user, "es_usuario_normal", False):
            context["categorias"] = Categoria.objects.filter(
                activo=True, eliminado=False, almacen_principal=user.almacen_asignado
            ).order_by("nombre")
        else:
            context["categorias"] = Categoria.objects.filter(
                activo=True, eliminado=False
            ).order_by("nombre")
        context["q"] = self.request.GET.get("q", "")
        context["categoria_seleccionada"] = self.request.GET.get("categoria_id")
        return context


class ProductoCrearView(LoginRequiredMixin, CreateView):
    """Creación de un nuevo producto en catálogo con opción de stock inicial."""

    model = Producto
    form_class = ProductoForm
    template_name = "inventario/producto_form.html"
    success_url = reverse_lazy("inventario:producto-lista")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["usuario"] = self.request.user
        return kwargs

    def form_valid(self, form):
        ubicaciones = [u.id for u in form.cleaned_data.get("ubicaciones_seleccionadas", [])]
        try:
            producto = crear_producto(
                nombre=form.cleaned_data["nombre"],
                categoria=form.cleaned_data["categoria"],
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
            return redirect(self.success_url)
        except Exception as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)


class ProductoEditarView(LoginRequiredMixin, UpdateView):
    """Edición de datos operativos de un producto."""

    model = Producto
    form_class = ProductoForm
    template_name = "inventario/producto_form.html"
    success_url = reverse_lazy("inventario:producto-lista")

    def dispatch(self, request, *args, **kwargs):
        obj = self.get_object()
        if getattr(request.user, "es_usuario_normal", False):
            if not request.user.almacen_asignado or obj.categoria.almacen_principal_id != request.user.almacen_asignado_id:
                raise PermissionDenied("No tienes permisos para modificar productos de otro almacén.")
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["usuario"] = self.request.user
        return kwargs

    def form_valid(self, form):
        ubicaciones = [u.id for u in form.cleaned_data.get("ubicaciones_seleccionadas", [])]
        try:
            producto = actualizar_producto(
                producto=self.object,
                nombre=form.cleaned_data["nombre"],
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
            return redirect(self.success_url)
        except Exception as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)


class ProductoDetalleView(LoginRequiredMixin, DetailView):
    """Detalle completo del producto: stock por almacén, ubicaciones e historial de movimientos."""

    model = Producto
    template_name = "inventario/producto_detalle.html"
    context_object_name = "producto"

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if getattr(user, "es_usuario_normal", False):
            qs = qs.filter(categoria__almacen_principal=user.almacen_asignado)
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        prod = self.object
        user = self.request.user
        if getattr(user, "es_usuario_normal", False) and getattr(user, "almacen_asignado", None):
            almacen = user.almacen_asignado
            context["existencias"] = prod.existencias.filter(almacen=almacen).select_related("almacen")
            context["movimientos"] = prod.movimientos.filter(
                Q(almacen_origen=almacen) | Q(almacen_destino=almacen)
            ).select_related("usuario", "almacen_origen", "almacen_destino").order_by("-fecha_hora")[:25]
            context["ubicaciones"] = prod.ubicaciones.filter(almacen=almacen).select_related("almacen")
        else:
            context["existencias"] = prod.existencias.select_related("almacen").all()
            context["movimientos"] = prod.movimientos.select_related("usuario", "almacen_origen", "almacen_destino").order_by("-fecha_hora")[:25]
            context["ubicaciones"] = prod.ubicaciones.select_related("almacen").all()
        return context


class ProductoArchivarView(LoginRequiredMixin, RolRequeridoMixin, View):
    """Archivado lógico de un producto (solo Administrador o Superusuario)."""

    roles_permitidos = (UsuarioAudacIA.Rol.ADMINISTRADOR, UsuarioAudacIA.Rol.SUPERUSUARIO)

    def post(self, request, pk):
        producto = get_object_or_404(Producto, pk=pk)
        try:
            archivar_producto(
                producto=producto,
                usuario=request.user,
                ip_origen=_obtener_ip(request),
            )
            messages.success(request, f"Producto '{producto.codigo_interno} — {producto.nombre}' archivado exitosamente.")
        except Exception as exc:
            messages.error(request, str(exc))
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


# ─────────────────────────────────────────────────────────────────────────────
# 5. TRASLADOS ESTRUCTURALES Y DESACTIVACIÓN
# ─────────────────────────────────────────────────────────────────────────────

class AlmacenDesactivarView(LoginRequiredMixin, RolRequeridoMixin, View):
    """Desactivación de almacén con traslado estructural obligatorio (§7.1)."""

    roles_permitidos = (UsuarioAudacIA.Rol.ADMINISTRADOR, UsuarioAudacIA.Rol.SUPERUSUARIO)

    def get(self, request, pk):
        almacen = get_object_or_404(Almacen, pk=pk)
        existencias = almacen.existencias.filter(stock_disponible__gt=0).select_related("producto")
        almacenes_destino = Almacen.objects.filter(activo=True, eliminado=False).exclude(id=almacen.id)
        return render(
            request,
            "inventario/trasladar_almacen.html",
            {
                "almacen": almacen,
                "existencias": existencias,
                "almacenes_destino": almacenes_destino,
                "tiene_stock": existencias.exists(),
            },
        )

    def post(self, request, pk):
        almacen = get_object_or_404(Almacen, pk=pk)
        destino_id = request.POST.get("almacen_destino")
        almacen_destino = get_object_or_404(Almacen, pk=destino_id) if destino_id else None

        try:
            desactivar_almacen(
                almacen=almacen,
                almacen_destino=almacen_destino,
                usuario=request.user,
                ip_origen=_obtener_ip(request),
            )
            messages.success(request, f"Almacén '{almacen.nombre}' desactivado exitosamente.")
            return redirect("inventario:almacen-lista")
        except Exception as exc:
            messages.error(request, str(exc))
            return self.get(request, pk)


class UbicacionDesactivarView(LoginRequiredMixin, RolRequeridoMixin, View):
    """Desactivación de ubicación con reasignación obligatoria (§7.2)."""

    roles_permitidos = (UsuarioAudacIA.Rol.ADMINISTRADOR, UsuarioAudacIA.Rol.SUPERUSUARIO)

    def get(self, request, pk):
        ubicacion = get_object_or_404(Ubicacion, pk=pk)
        productos = ubicacion.producto_ubicaciones.select_related("producto")
        ubicaciones_destino = Ubicacion.objects.filter(
            almacen=ubicacion.almacen, activo=True, eliminado=False
        ).exclude(id=ubicacion.id)
        return render(
            request,
            "inventario/trasladar_ubicacion.html",
            {
                "ubicacion": ubicacion,
                "productos": productos,
                "ubicaciones_destino": ubicaciones_destino,
                "tiene_productos": productos.exists(),
            },
        )

    def post(self, request, pk):
        ubicacion = get_object_or_404(Ubicacion, pk=pk)
        destino_id = request.POST.get("ubicacion_destino")
        ubicacion_destino = get_object_or_404(Ubicacion, pk=destino_id) if destino_id else None

        try:
            desactivar_ubicacion(
                ubicacion=ubicacion,
                ubicacion_destino=ubicacion_destino,
                usuario=request.user,
                ip_origen=_obtener_ip(request),
            )
            messages.success(request, f"Ubicación '{ubicacion.nombre}' desactivada exitosamente.")
            return redirect("inventario:ubicacion-lista")
        except Exception as exc:
            messages.error(request, str(exc))
            return self.get(request, pk)


class CategoriaTrasladarView(LoginRequiredMixin, RolRequeridoMixin, View):
    """Traslado de existencias de categoría a un nuevo almacén principal (§7.3)."""

    roles_permitidos = (UsuarioAudacIA.Rol.ADMINISTRADOR, UsuarioAudacIA.Rol.SUPERUSUARIO)

    def get(self, request, pk):
        categoria = get_object_or_404(Categoria, pk=pk)
        productos = categoria.productos.filter(archivado=False)
        almacenes_destino = Almacen.objects.filter(activo=True, eliminado=False).exclude(id=categoria.almacen_principal_id)
        return render(
            request,
            "inventario/trasladar_categoria.html",
            {
                "categoria": categoria,
                "productos": productos,
                "almacenes_destino": almacenes_destino,
            },
        )

    def post(self, request, pk):
        categoria = get_object_or_404(Categoria, pk=pk)
        destino_id = request.POST.get("almacen_destino")
        almacen_destino = get_object_or_404(Almacen, pk=destino_id)

        try:
            n = trasladar_productos_categoria(
                categoria=categoria,
                almacen_destino=almacen_destino,
                usuario=request.user,
                ip_origen=_obtener_ip(request),
            )
            messages.success(request, f"Se trasladaron {n} productos de '{categoria.nombre}' a '{almacen_destino.nombre}'.")
            return redirect("inventario:categoria-lista")
        except Exception as exc:
            messages.error(request, str(exc))
            return self.get(request, pk)


class CategoriaDesactivarView(LoginRequiredMixin, RolRequeridoMixin, View):
    """Desactivación de categoría vacía (§7.3)."""

    roles_permitidos = (UsuarioAudacIA.Rol.ADMINISTRADOR, UsuarioAudacIA.Rol.SUPERUSUARIO)

    def post(self, request, pk):
        categoria = get_object_or_404(Categoria, pk=pk)
        try:
            desactivar_categoria(
                categoria=categoria,
                usuario=request.user,
                ip_origen=_obtener_ip(request),
            )
            messages.success(request, f"Categoría '{categoria.nombre}' desactivada exitosamente.")
        except Exception as exc:
            messages.error(request, str(exc))
        return redirect("inventario:categoria-lista")


# ─────────────────────────────────────────────────────────────────────────────
# 6. FUSIÓN DE PRODUCTOS DUPLICADOS
# ─────────────────────────────────────────────────────────────────────────────

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
            messages.error(request, str(exc))
            return self.get(request, pk)


