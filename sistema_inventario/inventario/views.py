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
from inventario.forms import (
    AlmacenForm,
    BajaPrestamoForm,
    CategoriaForm,
    ConteoFisicoForm,
    DevolucionPrestamoForm,
    EntradaForm,
    PrestamoForm,
    ProductoForm,
    ReversionForm,
    SalidaForm,
    TipoCategoriaForm,
    TrasladoForm,
    UbicacionForm,
)
from inventario.models import (
    Almacen,
    Categoria,
    ConteoFisico,
    DevolucionPrestamo,
    Existencia,
    Movimiento,
    Prestamo,
    Producto,
    TipoCategoria,
    Ubicacion,
)
from inventario.servicios import (
    NecesitaTraslado,
    confirmar_conteo_fisico,
    obtener_alertas_stock,
    registrar_baja_prestamo,
    registrar_conteo_fisico,
    registrar_devolucion_prestamo,
    registrar_entrada,
    registrar_prestamo,
    registrar_salida,
    registrar_traslado_entre_almacenes,
    registrar_traslado_interno,
    revertir_movimiento,
    verificar_estado_alerta,
)
from inventario.servicios.datos_maestros import (
    actualizar_almacen,
    actualizar_categoria,
    actualizar_producto,
    actualizar_tipo_categoria,
    actualizar_ubicacion,
    archivar_producto,
    crear_almacen,
    crear_categoria,
    crear_producto,
    crear_tipo_categoria,
    crear_ubicacion,
    eliminar_tipo_categoria,
    fusionar_productos,
    reactivar_almacen,
    sincronizar_tipos_categoria,
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
# 3. CATEGORÍAS Y TIPOS OPCIONALES
# ─────────────────────────────────────────────────────────────────────────────

class CategoriaListaView(LoginRequiredMixin, ListView):
    """Lista de categorías de productos accesibles por el usuario con sus tipos opcionales."""

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
        return (
            qs.select_related("almacen_principal")
            .prefetch_related("tipos", "productos")
            .order_by("almacen_principal__nombre", "nombre")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if not getattr(self.request.user, "es_usuario_normal", False):
            context["almacenes"] = Almacen.objects.filter(activo=True, eliminado=False).order_by("nombre")
            context["almacen_seleccionado"] = self.request.GET.get("almacen_id")
        context["tipo_form"] = TipoCategoriaForm(usuario=self.request.user)
        return context


class CategoriaCrearView(LoginRequiredMixin, CreateView):
    """Creación de una categoría con almacén principal obligatorio y tipos opcionales."""

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
            tipos_opcionales = form.cleaned_data.get("tipos_opcionales") or []
            if tipos_opcionales:
                sincronizar_tipos_categoria(
                    categoria=categoria,
                    nombres_tipos=tipos_opcionales,
                    usuario=user,
                    ip_origen=_obtener_ip(self.request),
                )
            messages.success(self.request, f"Categoría '{categoria.nombre}' creada exitosamente.")
            return redirect(self.success_url)
        except Exception as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)


class CategoriaEditarView(LoginRequiredMixin, UpdateView):
    """Edición de categoría y sus tipos opcionales."""

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

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["tipos_categoria"] = self.object.tipos.filter(activo=True).order_by("nombre")
        return context

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
            tipos_opcionales = form.cleaned_data.get("tipos_opcionales") or []
            if tipos_opcionales:
                sincronizar_tipos_categoria(
                    categoria=categoria,
                    nombres_tipos=tipos_opcionales,
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


class TipoCategoriaCrearView(LoginRequiredMixin, View):
    """Agrega un tipo opcional ligado a una categoría existente."""

    def post(self, request):
        categoria_id = request.POST.get("categoria")
        nombre = (request.POST.get("nombre") or "").strip()
        next_url = request.POST.get("next") or reverse("inventario:categoria-lista")

        if not categoria_id or not str(categoria_id).isdigit():
            messages.error(request, "Debe seleccionar una categoría válida.")
            return redirect(next_url)

        categoria = get_object_or_404(Categoria, pk=int(categoria_id), eliminado=False)
        try:
            tipo = crear_tipo_categoria(
                nombre=nombre,
                categoria=categoria,
                usuario=request.user,
                ip_origen=_obtener_ip(request),
            )
            messages.success(request, f"Tipo '{tipo.nombre}' agregado a la categoría '{categoria.nombre}'.")
        except Exception as exc:
            msg = exc.messages[0] if hasattr(exc, "messages") and exc.messages else str(exc)
            messages.error(request, msg)
        return redirect(next_url)


class TipoCategoriaEditarView(LoginRequiredMixin, View):
    """Renombra un tipo de categoría conservando los productos e historial."""

    def post(self, request, pk):
        tipo = get_object_or_404(TipoCategoria, pk=pk)
        nombre = (request.POST.get("nombre") or "").strip()
        next_url = request.POST.get("next") or reverse("inventario:categoria-lista")

        try:
            actualizado = actualizar_tipo_categoria(
                tipo=tipo,
                nombre=nombre,
                usuario=request.user,
                ip_origen=_obtener_ip(request),
            )
            messages.success(request, f"Tipo actualizado a '{actualizado.nombre}'.")
        except Exception as exc:
            msg = exc.messages[0] if hasattr(exc, "messages") and exc.messages else str(exc)
            messages.error(request, msg)
        return redirect(next_url)


class TipoCategoriaEliminarView(LoginRequiredMixin, View):
    """Elimina un tipo de categoría opcional (los productos quedan sin tipo)."""

    def post(self, request, pk):
        tipo = get_object_or_404(TipoCategoria, pk=pk)
        nombre_tipo = tipo.nombre
        nombre_cat = tipo.categoria.nombre
        next_url = request.POST.get("next") or reverse("inventario:categoria-lista")

        try:
            eliminar_tipo_categoria(
                tipo=tipo,
                usuario=request.user,
                ip_origen=_obtener_ip(request),
            )
            messages.success(request, f"Tipo '{nombre_tipo}' eliminado de '{nombre_cat}'.")
        except Exception as exc:
            msg = exc.messages[0] if hasattr(exc, "messages") and exc.messages else str(exc)
            messages.error(request, msg)
        return redirect(next_url)


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
                | Q(tipo__nombre__icontains=q)
            )

        categoria_id = self.request.GET.get("categoria_id")
        if categoria_id:
            qs = qs.filter(categoria_id=categoria_id)

        tipo_id = self.request.GET.get("tipo_id")
        if tipo_id:
            qs = qs.filter(tipo_id=tipo_id)

        return (
            qs.select_related("categoria", "categoria__almacen_principal", "tipo")
            .prefetch_related("existencias", "ubicaciones")
            .order_by("codigo_interno")
        )

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
        context["tipo_seleccionado"] = self.request.GET.get("tipo_id")
        return context


class ProductoCrearView(LoginRequiredMixin, CreateView):
    """Creación de un nuevo producto en catálogo con opción de stock inicial y tipo opcional."""

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
        cats_qs = Categoria.objects.filter(activo=True, eliminado=False)
        if getattr(user, "es_usuario_normal", False):
            cats_qs = cats_qs.filter(almacen_principal=user.almacen_asignado)
        tipos = TipoCategoria.objects.filter(activo=True, categoria__in=cats_qs).order_by("nombre")
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

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        cats_qs = Categoria.objects.filter(activo=True, eliminado=False)
        if getattr(user, "es_usuario_normal", False):
            cats_qs = cats_qs.filter(almacen_principal=user.almacen_asignado)
        tipos = TipoCategoria.objects.filter(activo=True, categoria__in=cats_qs).order_by("nombre")
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


# ─────────────────────────────────────────────────────────────────────────────
# 7. ALERTAS Y NOTIFICACIONES DE STOCK
# ─────────────────────────────────────────────────────────────────────────────

class AlertasStockListaView(LoginRequiredMixin, View):
    """Centro de Notificaciones y Alertas de Stock Bajo y Crítico."""

    def get(self, request):
        alertas = obtener_alertas_stock(usuario=request.user)
        return render(
            request,
            "inventario/alertas_lista.html",
            {
                "alertas": alertas,
                "criticas": alertas["criticas"],
                "bajas": alertas["bajas"],
            },
        )


class AlertasStockJSONView(LoginRequiredMixin, View):
    """Endpoint AJAX / JSON para consultar el estado de alertas en vivo."""

    def get(self, request):
        alertas = obtener_alertas_stock(usuario=request.user)
        return JsonResponse({
            "total_alertas": alertas["total_alertas"],
            "total_criticas": alertas["total_criticas"],
            "total_bajas": alertas["total_bajas"],
            "criticas": [
                {
                    "codigo": e.producto.codigo_interno,
                    "nombre": e.producto.nombre,
                    "almacen": e.almacen.nombre,
                    "stock": e.stock_disponible,
                }
                for e in alertas["criticas"][:10]
            ],
            "bajas": [
                {
                    "codigo": e.producto.codigo_interno,
                    "nombre": e.producto.nombre,
                    "almacen": e.almacen.nombre,
                    "stock": e.stock_disponible,
                    "minimo": e.producto.stock_minimo,
                }
                for e in alertas["bajas"][:10]
            ],
        })


# ─────────────────────────────────────────────────────────────────────────────
# 8. MOVIMIENTOS: LISTA, ENTRADAS Y SALIDAS
# ─────────────────────────────────────────────────────────────────────────────

class MovimientoListaView(LoginRequiredMixin, ListView):
    """Historial y trazabilidad de movimientos de inventario."""

    model = Movimiento
    template_name = "inventario/movimiento_lista.html"
    context_object_name = "movimientos"
    paginate_by = 30

    def get_queryset(self):
        qs = Movimiento.objects.select_related(
            "producto", "almacen_origen", "almacen_destino", "ubicacion_origen", "ubicacion_destino", "usuario"
        ).order_by("-fecha_hora")

        user = self.request.user
        if getattr(user, "es_usuario_normal", False):
            if user.almacen_asignado:
                qs = qs.filter(Q(almacen_origen=user.almacen_asignado) | Q(almacen_destino=user.almacen_asignado))
            else:
                return qs.none()

        tipo = self.request.GET.get("tipo")
        if tipo:
            qs = qs.filter(tipo=tipo)

        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(
                Q(producto__nombre__icontains=q)
                | Q(producto__codigo_interno__icontains=q)
                | Q(destinatario__icontains=q)
                | Q(motivo__icontains=q)
            )
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["tipos_movimiento"] = Movimiento.Tipo.choices
        context["tipo_seleccionado"] = self.request.GET.get("tipo", "")
        context["q"] = self.request.GET.get("q", "")
        return context


class EntradaCrearView(LoginRequiredMixin, View):
    """Registro operativo de entrada de mercancía (con soporte de empaques)."""

    def get(self, request):
        form = EntradaForm(usuario=request.user)
        return render(request, "inventario/movimiento_entrada.html", {"form": form})

    def post(self, request):
        form = EntradaForm(request.POST, usuario=request.user)
        if form.is_valid():
            try:
                existencia, mov = registrar_entrada(
                    producto=form.cleaned_data["producto"],
                    almacen=form.cleaned_data["almacen"],
                    cantidad=form.cleaned_data.get("cantidad") or 0,
                    usuario=request.user,
                    ubicacion=form.cleaned_data.get("ubicacion"),
                    es_empaque=form.cleaned_data.get("es_empaque", False),
                    cantidad_empaques=form.cleaned_data.get("cantidad_empaques"),
                    motivo=form.cleaned_data.get("motivo", ""),
                    observacion=form.cleaned_data.get("observacion", ""),
                    ip_origen=_obtener_ip(request),
                )
                messages.success(
                    request,
                    f"Entrada registrada: +{mov.cantidad} unid. de '{existencia.producto.codigo_interno}'. Stock actual: {existencia.stock_disponible}.",
                )
                return redirect("inventario:movimiento-lista")
            except Exception as exc:
                form.add_error(None, str(exc))
        return render(request, "inventario/movimiento_entrada.html", {"form": form})


class SalidaCrearView(LoginRequiredMixin, View):
    """Registro operativo de salida de stock con verificación y alerta inmediata."""

    def get(self, request):
        form = SalidaForm(usuario=request.user)
        return render(request, "inventario/movimiento_salida.html", {"form": form})

    def post(self, request):
        form = SalidaForm(request.POST, usuario=request.user)
        if form.is_valid():
            try:
                existencia, mov, alerta = registrar_salida(
                    producto=form.cleaned_data["producto"],
                    almacen=form.cleaned_data["almacen"],
                    cantidad=form.cleaned_data["cantidad"],
                    motivo=form.cleaned_data["motivo"],
                    destinatario=form.cleaned_data["destinatario"],
                    usuario=request.user,
                    ubicacion=form.cleaned_data.get("ubicacion"),
                    observacion=form.cleaned_data.get("observacion", ""),
                    ip_origen=_obtener_ip(request),
                )
                messages.success(
                    request,
                    f"Salida registrada: -{mov.cantidad} unid. de '{existencia.producto.codigo_interno}' para {mov.destinatario}. Stock restante: {existencia.stock_disponible}.",
                )
                if alerta.get("nivel"):
                    messages.warning(request, alerta["mensaje"])
                return redirect("inventario:movimiento-lista")
            except Exception as exc:
                form.add_error(None, str(exc))
        return render(request, "inventario/movimiento_salida.html", {"form": form})


# ─────────────────────────────────────────────────────────────────────────────
# 9. PRÉSTAMOS, DEVOLUCIONES Y BAJAS
# ─────────────────────────────────────────────────────────────────────────────

class PrestamoListaView(LoginRequiredMixin, ListView):
    """Listado y seguimiento de préstamos."""

    model = Prestamo
    template_name = "inventario/prestamo_lista.html"
    context_object_name = "prestamos"
    paginate_by = 25

    def get_queryset(self):
        qs = Prestamo.objects.select_related("producto", "almacen", "usuario", "ubicacion_origen").order_by("-fecha_creacion")
        user = self.request.user
        if getattr(user, "es_usuario_normal", False):
            if user.almacen_asignado:
                qs = qs.filter(almacen=user.almacen_asignado)
            else:
                return qs.none()

        estado = self.request.GET.get("estado")
        if estado:
            qs = qs.filter(estado=estado)

        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(
                Q(producto__nombre__icontains=q)
                | Q(producto__codigo_interno__icontains=q)
                | Q(destinatario__icontains=q)
            )
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["estados"] = Prestamo.Estado.choices
        context["estado_seleccionado"] = self.request.GET.get("estado", "")
        context["q"] = self.request.GET.get("q", "")
        return context


class PrestamoCrearView(LoginRequiredMixin, View):
    """Registro de entrega de artículos en préstamo."""

    def get(self, request):
        form = PrestamoForm(usuario=request.user)
        return render(request, "inventario/prestamo_form.html", {"form": form})

    def post(self, request):
        form = PrestamoForm(request.POST, usuario=request.user)
        if form.is_valid():
            try:
                prestamo, mov, alerta = registrar_prestamo(
                    producto=form.cleaned_data["producto"],
                    almacen=form.cleaned_data["almacen"],
                    cantidad=form.cleaned_data["cantidad"],
                    destinatario=form.cleaned_data["destinatario"],
                    usuario=request.user,
                    ubicacion=form.cleaned_data.get("ubicacion"),
                    motivo=form.cleaned_data.get("motivo", ""),
                    observacion=form.cleaned_data.get("observacion", ""),
                    ip_origen=_obtener_ip(request),
                )
                messages.success(
                    request,
                    f"Préstamo registrado exitosamente: {prestamo.cantidad_original} unid. de '{prestamo.producto.codigo_interno}' a {prestamo.destinatario}.",
                )
                if alerta.get("nivel"):
                    messages.warning(request, alerta["mensaje"])
                return redirect("inventario:prestamo-lista")
            except Exception as exc:
                form.add_error(None, str(exc))
        return render(request, "inventario/prestamo_form.html", {"form": form})


class PrestamoDevolucionView(LoginRequiredMixin, View):
    """Registro de devolución total o parcial de un préstamo."""

    def get(self, request, pk):
        prestamo = get_object_or_404(Prestamo, pk=pk)
        if getattr(request.user, "es_usuario_normal", False):
            if prestamo.almacen_id != getattr(request.user, "almacen_asignado_id", None):
                raise PermissionDenied("No tienes permisos para acceder a préstamos de otro almacén.")
        form = DevolucionPrestamoForm(initial={"cantidad": prestamo.cantidad_pendiente_devolucion})
        return render(request, "inventario/prestamo_devolucion.html", {"prestamo": prestamo, "form": form})

    def post(self, request, pk):
        prestamo = get_object_or_404(Prestamo, pk=pk)
        form = DevolucionPrestamoForm(request.POST)
        if form.is_valid():
            try:
                p_db, dev, mov = registrar_devolucion_prestamo(
                    prestamo=prestamo,
                    cantidad=form.cleaned_data["cantidad"],
                    usuario=request.user,
                    motivo=form.cleaned_data["motivo"],
                    observacion=form.cleaned_data.get("observacion", ""),
                    ip_origen=_obtener_ip(request),
                )
                messages.success(
                    request,
                    f"Devolución registrada: {dev.cantidad} unid. de '{p_db.producto.codigo_interno}'. Saldo pendiente: {p_db.cantidad_pendiente_devolucion} ({p_db.get_estado_display()}).",
                )
                return redirect("inventario:prestamo-lista")
            except Exception as exc:
                form.add_error(None, str(exc))
        return render(request, "inventario/prestamo_devolucion.html", {"prestamo": prestamo, "form": form})


class PrestamoBajaView(LoginRequiredMixin, View):
    """Registro de baja por daño o pérdida de un préstamo (§9.4)."""

    def get(self, request, pk):
        prestamo = get_object_or_404(Prestamo, pk=pk)
        if getattr(request.user, "es_usuario_normal", False):
            if prestamo.almacen_id != getattr(request.user, "almacen_asignado_id", None):
                raise PermissionDenied("No tienes permisos para dar de baja préstamos de otro almacén.")
        form = BajaPrestamoForm(initial={"cantidad": 1})
        return render(request, "inventario/prestamo_baja.html", {"prestamo": prestamo, "form": form})

    def post(self, request, pk):
        prestamo = get_object_or_404(Prestamo, pk=pk)
        form = BajaPrestamoForm(request.POST)
        if form.is_valid():
            try:
                p_db, baja, mov = registrar_baja_prestamo(
                    prestamo=prestamo,
                    cantidad=form.cleaned_data["cantidad"],
                    usuario=request.user,
                    motivo=form.cleaned_data["motivo"],
                    observacion=form.cleaned_data.get("observacion", ""),
                    ip_origen=_obtener_ip(request),
                )
                messages.warning(
                    request,
                    f"Baja por daño/pérdida registrada: {baja.cantidad} unid. de '{p_db.producto.codigo_interno}'. Saldo pendiente: {p_db.cantidad_pendiente_devolucion}.",
                )
                return redirect("inventario:prestamo-lista")
            except Exception as exc:
                form.add_error(None, str(exc))
        return render(request, "inventario/prestamo_baja.html", {"prestamo": prestamo, "form": form})


# ─────────────────────────────────────────────────────────────────────────────
# 10. TRASLADOS OPERATIVOS
# ─────────────────────────────────────────────────────────────────────────────

class TrasladoCrearView(LoginRequiredMixin, View):
    """Registro de traslado operativo (interno o entre almacenes)."""

    def get(self, request):
        form = TrasladoForm(usuario=request.user)
        return render(request, "inventario/movimiento_traslado.html", {"form": form})

    def post(self, request):
        form = TrasladoForm(request.POST, usuario=request.user)
        if form.is_valid():
            prod = form.cleaned_data["producto"]
            alm_orig = form.cleaned_data["almacen_origen"]
            alm_dest = form.cleaned_data.get("almacen_destino")
            ub_orig = form.cleaned_data.get("ubicacion_origen")
            ub_dest = form.cleaned_data.get("ubicacion_destino")
            cant = form.cleaned_data["cantidad"]
            mot = form.cleaned_data.get("motivo", "")

            try:
                if alm_dest and alm_dest.id != alm_orig.id:
                    # Traslado entre almacenes
                    ex_o, ex_d, mov = registrar_traslado_entre_almacenes(
                        producto=prod,
                        almacen_origen=alm_orig,
                        almacen_destino=alm_dest,
                        cantidad=cant,
                        usuario=request.user,
                        ubicacion_origen=ub_orig,
                        ubicacion_destino=ub_dest,
                        motivo=mot,
                        ip_origen=_obtener_ip(request),
                    )
                    messages.success(
                        request,
                        f"Traslado completado: {cant} unid. de '{prod.codigo_interno}' enviadas de '{alm_orig.nombre}' a '{alm_dest.nombre}'.",
                    )
                else:
                    # Traslado interno
                    if not ub_orig or not ub_dest:
                        raise ValidationError("Para un traslado interno debe indicar ubicación de origen y de destino.")
                    ex, mov = registrar_traslado_interno(
                        producto=prod,
                        almacen=alm_orig,
                        ubicacion_origen=ub_orig,
                        ubicacion_destino=ub_dest,
                        usuario=request.user,
                        motivo=mot,
                        ip_origen=_obtener_ip(request),
                    )
                    messages.success(
                        request,
                        f"Traslado interno completado: '{prod.codigo_interno}' reubicado de '{ub_orig.nombre}' a '{ub_dest.nombre}'.",
                    )
                return redirect("inventario:movimiento-lista")
            except Exception as exc:
                form.add_error(None, str(exc))
        return render(request, "inventario/movimiento_traslado.html", {"form": form})


# ─────────────────────────────────────────────────────────────────────────────
# 11. CONTEO FÍSICO Y AJUSTES
# ─────────────────────────────────────────────────────────────────────────────

class ConteoFisicoListaView(LoginRequiredMixin, ListView):
    """Listado de conteos físicos y solicitudes de ajuste."""

    model = ConteoFisico
    template_name = "inventario/conteo_lista.html"
    context_object_name = "conteos"
    paginate_by = 25

    def get_queryset(self):
        qs = ConteoFisico.objects.select_related("producto", "almacen", "usuario_conteo", "usuario_confirmacion").order_by("-fecha_conteo")
        user = self.request.user
        if getattr(user, "es_usuario_normal", False):
            if user.almacen_asignado:
                qs = qs.filter(almacen=user.almacen_asignado)
            else:
                return qs.none()
        return qs


class ConteoFisicoCrearView(LoginRequiredMixin, View):
    """Registro de conteo físico preliminar."""

    def get(self, request):
        form = ConteoFisicoForm(usuario=request.user)
        return render(request, "inventario/conteo_form.html", {"form": form})

    def post(self, request):
        form = ConteoFisicoForm(request.POST, usuario=request.user)
        if form.is_valid():
            try:
                conteo = registrar_conteo_fisico(
                    producto=form.cleaned_data["producto"],
                    almacen=form.cleaned_data["almacen"],
                    cantidad_contada=form.cleaned_data["cantidad_contada"],
                    usuario=request.user,
                    motivo=form.cleaned_data.get("motivo", ""),
                    ip_origen=_obtener_ip(request),
                )
                messages.success(
                    request,
                    f"Conteo registrado: Contado {conteo.cantidad_contada} unid. vs {conteo.stock_sistema} en sistema (Diferencia: {conteo.diferencia:+d}). Pendiente de confirmación administrativa.",
                )
                return redirect("inventario:conteo-lista")
            except Exception as exc:
                form.add_error(None, str(exc))
        return render(request, "inventario/conteo_form.html", {"form": form})


class ConteoFisicoConfirmarView(LoginRequiredMixin, RolRequeridoMixin, View):
    """Confirmación y aplicación administrativa de ajuste por conteo físico (§6.2, §9.7)."""

    roles_permitidos = (UsuarioAudacIA.Rol.ADMINISTRADOR, UsuarioAudacIA.Rol.SUPERUSUARIO)

    def post(self, request, pk):
        conteo = get_object_or_404(ConteoFisico, pk=pk)
        try:
            conteo_db, mov = confirmar_conteo_fisico(
                conteo=conteo,
                usuario=request.user,
                ip_origen=_obtener_ip(request),
            )
            messages.success(
                request,
                f"Ajuste por conteo confirmado exitosamente para '{conteo_db.producto.codigo_interno}'. Stock ajustado a {conteo_db.cantidad_contada} unid.",
            )
        except Exception as exc:
            messages.error(request, str(exc))
        return redirect("inventario:conteo-lista")


# ─────────────────────────────────────────────────────────────────────────────
# 12. REVERSIÓN DE MOVIMIENTOS
# ─────────────────────────────────────────────────────────────────────────────

class MovimientoRevertirView(LoginRequiredMixin, View):
    """Reversión de un movimiento previo con verificación estricta de no negatividad (§9.6)."""

    def get(self, request, pk):
        movimiento = get_object_or_404(Movimiento, pk=pk)
        if getattr(request.user, "es_usuario_normal", False):
            alm_mov = movimiento.almacen_origen or movimiento.almacen_destino
            if alm_mov and alm_mov.id != getattr(request.user, "almacen_asignado_id", None):
                raise PermissionDenied("No tienes permisos para revertir movimientos de otro almacén.")
        form = ReversionForm()
        return render(request, "inventario/movimiento_revertir.html", {"movimiento": movimiento, "form": form})

    def post(self, request, pk):
        movimiento = get_object_or_404(Movimiento, pk=pk)
        form = ReversionForm(request.POST)
        if form.is_valid():
            try:
                mov_orig, mov_rev = revertir_movimiento(
                    movimiento=movimiento,
                    usuario=request.user,
                    motivo=form.cleaned_data["motivo"],
                    ip_origen=_obtener_ip(request),
                )
                messages.success(
                    request,
                    f"Movimiento revertido exitosamente. Se generó la reversión compensatoria con folio {mov_rev.id}.",
                )
                return redirect("inventario:movimiento-lista")
            except Exception as exc:
                form.add_error(None, str(exc))
        return render(request, "inventario/movimiento_revertir.html", {"movimiento": movimiento, "form": form})



