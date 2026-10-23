"""Vistas web tradicionales de la aplicación inventario."""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from datetime import datetime
from django.utils import timezone
from django.utils.safestring import mark_safe
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
    ProductoUbicacion,
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
    eliminar_almacen,
    eliminar_categoria,
    eliminar_tipo_categoria,
    eliminar_ubicacion,
    fusionar_productos,
    reactivar_almacen,
    reactivar_categoria,
    reactivar_ubicacion,
    sincronizar_tipos_categoria,
    toggle_activo_producto,
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



def _procesar_error_formulario(form, exc):
    """Extrae y formatea limpiamente excepciones y ValidationError en campos del formulario sin volcar JSON crudo."""
    if isinstance(exc, ValidationError):
        if hasattr(exc, "message_dict"):
            for campo, msgs in exc.message_dict.items():
                for msg in msgs:
                    if campo in form.fields:
                        form.add_error(campo, msg)
                    else:
                        form.add_error(None, msg)
            return
        if hasattr(exc, "messages"):
            for msg in exc.messages:
                form.add_error(None, msg)
            return

    msg_str = str(exc).strip()
    if msg_str.startswith("{") and msg_str.endswith("}"):
        import ast
        try:
            parsed = ast.literal_eval(msg_str)
            if isinstance(parsed, dict):
                for campo, msgs in parsed.items():
                    if isinstance(msgs, list):
                        for m in msgs:
                            if campo in form.fields:
                                form.add_error(campo, m)
                            else:
                                form.add_error(None, m)
                    else:
                        if campo in form.fields:
                            form.add_error(campo, str(msgs))
                        else:
                            form.add_error(None, str(msgs))
                return
        except Exception:
            pass

    form.add_error(None, msg_str)


class AjaxModalFormMixin:
    """Mixin para vistas de formulario embebidas en modales (<dialog>).
    
    Si la solicitud proviene de un modal vía AJAX (X-Requested-With: XMLHttpRequest),
    retorna JSON 400 con errores estructurados al fallar la validación,
    evitando que la página se cierre o navegue a la vista standalone.
    """

    def form_invalid(self, form):
        if self.request.headers.get("x-requested-with") == "XMLHttpRequest":
            errores = {}
            for field, err_list in form.errors.get_json_data().items():
                errores[field] = [e["message"] for e in err_list]
            return JsonResponse({
                "success": False,
                "errors": errores,
                "message": "Por favor corrige los errores señalados en el formulario.",
            }, status=400)
        return super().form_invalid(form)

    def responder_exito(self, redirect_url=None):
        target = redirect_url or (str(self.success_url) if hasattr(self, "success_url") else "/")
        if self.request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({
                "success": True,
                "redirect_url": str(target),
            })
        return redirect(target)


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
        user = self.request.user
        if getattr(user, "es_usuario_normal", False):
            qs = qs.filter(id__in=user.get_almacenes_asignados())
        mostrar_inactivos = self.request.GET.get("mostrar_inactivos") == "1"
        if not mostrar_inactivos:
            qs = qs.filter(activo=True)
        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(Q(nombre__icontains=q) | Q(descripcion__icontains=q))
        return qs.order_by("nombre")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["mostrar_inactivos"] = self.request.GET.get("mostrar_inactivos") == "1"
        context["q"] = self.request.GET.get("q", "")
        context["almacen_form"] = AlmacenForm()
        return context


class AlmacenReactivarView(LoginRequiredMixin, RolRequeridoMixin, View):
    """Reactivación de almacén inhabilitado (solo Administradores y Superusuario)."""

    roles_permitidos = (UsuarioAudacIA.Rol.ADMINISTRADOR, UsuarioAudacIA.Rol.SUPERUSUARIO)

    def post(self, request, pk):
        almacen = get_object_or_404(Almacen, pk=pk, eliminado=False)
        try:
            reactivar_almacen(almacen=almacen, usuario=request.user, ip_origen=_obtener_ip(request))
            messages.success(request, f"Almacén '{almacen.nombre}' habilitado nuevamente.")
        except Exception as exc:
            messages.error(request, str(exc))
        return redirect(request.POST.get("next") or "inventario:almacen-lista")


class AlmacenEliminarView(LoginRequiredMixin, RolRequeridoMixin, View):
    """Eliminación lógica de un almacén sin stock (o redirección a traslado estructural)."""

    roles_permitidos = (UsuarioAudacIA.Rol.ADMINISTRADOR, UsuarioAudacIA.Rol.SUPERUSUARIO)

    def post(self, request, pk):
        almacen = get_object_or_404(Almacen, pk=pk, eliminado=False)
        try:
            eliminar_almacen(almacen=almacen, usuario=request.user, ip_origen=_obtener_ip(request))
            messages.success(request, f"Almacén '{almacen.nombre}' eliminado exitosamente.")
        except NecesitaTraslado as exc:
            messages.warning(request, str(exc))
            return redirect("inventario:almacen-desactivar", pk=almacen.pk)
        except Exception as exc:
            messages.error(request, str(exc))
        return redirect(request.POST.get("next") or "inventario:almacen-lista")


class AlmacenCrearView(LoginRequiredMixin, RolRequeridoMixin, AjaxModalFormMixin, CreateView):
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
            return self.responder_exito(self.success_url)
        except Exception as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)


class AlmacenEditarView(LoginRequiredMixin, RolRequeridoMixin, AjaxModalFormMixin, UpdateView):
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
            return self.responder_exito(self.success_url)
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
        qs = Ubicacion.objects.filter(eliminado=False)
        mostrar_inactivos = self.request.GET.get("mostrar_inactivos") == "1"
        if not mostrar_inactivos:
            qs = qs.filter(activo=True)
        user = self.request.user
        if getattr(user, "es_usuario_normal", False):
            user_alms = user.get_almacenes_asignados()
            qs = qs.filter(almacen__in=user_alms)
            almacen_id = self.request.GET.get("almacen_id")
            if almacen_id and almacen_id.isdigit():
                qs = qs.filter(almacen_id=int(almacen_id))
        else:
            almacen_id = self.request.GET.get("almacen_id")
            if almacen_id:
                qs = qs.filter(almacen_id=almacen_id)
        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(Q(nombre__icontains=q) | Q(descripcion__icontains=q) | Q(almacen__nombre__icontains=q))
        return qs.select_related("almacen").order_by("almacen__nombre", "nombre")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        alms_disponibles = user.get_almacenes_asignados() if getattr(user, "es_usuario_normal", False) else Almacen.objects.filter(activo=True, eliminado=False).order_by("nombre")
        if not getattr(user, "es_usuario_normal", False) or alms_disponibles.count() > 1:
            context["almacenes"] = alms_disponibles
            context["almacen_seleccionado"] = self.request.GET.get("almacen_id")
        context["mostrar_inactivos"] = self.request.GET.get("mostrar_inactivos") == "1"
        context["q"] = self.request.GET.get("q", "")
        context["ubicacion_form"] = UbicacionForm(usuario=self.request.user)
        return context


class UbicacionReactivarView(LoginRequiredMixin, View):
    """Reactivación de ubicación física inhabilitada."""

    def dispatch(self, request, *args, **kwargs):
        ubicacion = get_object_or_404(Ubicacion, pk=kwargs.get("pk"), eliminado=False)
        if getattr(request.user, "es_usuario_normal", False):
            if not request.user.tiene_acceso_almacen(ubicacion.almacen):
                raise PermissionDenied("No tienes permisos para modificar ubicaciones de otro almacén.")
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, pk):
        ubicacion = get_object_or_404(Ubicacion, pk=pk, eliminado=False)
        try:
            reactivar_ubicacion(ubicacion=ubicacion, usuario=request.user, ip_origen=_obtener_ip(request))
            messages.success(request, f"Ubicación '{ubicacion.nombre}' habilitada nuevamente.")
        except Exception as exc:
            messages.error(request, str(exc))
        return redirect(request.POST.get("next") or "inventario:ubicacion-lista")


class UbicacionEliminarView(LoginRequiredMixin, View):
    """Eliminación lógica de ubicación física (o redirección a traslado si tiene productos)."""

    def dispatch(self, request, *args, **kwargs):
        ubicacion = get_object_or_404(Ubicacion, pk=kwargs.get("pk"), eliminado=False)
        if getattr(request.user, "es_usuario_normal", False):
            if not request.user.tiene_acceso_almacen(ubicacion.almacen):
                raise PermissionDenied("No tienes permisos para eliminar ubicaciones de otro almacén.")
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, pk):
        ubicacion = get_object_or_404(Ubicacion, pk=pk, eliminado=False)
        try:
            eliminar_ubicacion(ubicacion=ubicacion, usuario=request.user, ip_origen=_obtener_ip(request))
            messages.success(request, f"Ubicación '{ubicacion.nombre}' eliminada exitosamente.")
        except NecesitaTraslado as exc:
            messages.warning(request, str(exc))
            if getattr(request.user, "es_administrador", False):
                return redirect("inventario:ubicacion-desactivar", pk=ubicacion.pk)
        except Exception as exc:
            messages.error(request, str(exc))
        return redirect(request.POST.get("next") or "inventario:ubicacion-lista")


class UbicacionCrearView(LoginRequiredMixin, AjaxModalFormMixin, CreateView):
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
        almacen = form.cleaned_data.get("almacen")
        if not user.tiene_acceso_almacen(almacen):
            form.add_error("almacen", "No tienes permisos para crear ubicaciones en este almacén.")
            return self.form_invalid(form)

        try:
            ubicacion = crear_ubicacion(
                nombre=form.cleaned_data["nombre"],
                almacen=almacen,
                descripcion=form.cleaned_data.get("descripcion", ""),
                usuario=user,
                ip_origen=_obtener_ip(self.request),
            )
            messages.success(self.request, f"Ubicación '{ubicacion.nombre}' creada exitosamente.")
            return self.responder_exito(self.success_url)
        except Exception as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)


class UbicacionEditarView(LoginRequiredMixin, AjaxModalFormMixin, UpdateView):
    """Edición de ubicación física conservando historial del nombre previo y permitiendo cambio de almacén (§Punto 4)."""

    model = Ubicacion
    form_class = UbicacionForm
    template_name = "inventario/ubicacion_form.html"
    success_url = reverse_lazy("inventario:ubicacion-lista")

    def dispatch(self, request, *args, **kwargs):
        obj = self.get_object()
        if getattr(request.user, "es_usuario_normal", False):
            if not request.user.tiene_acceso_almacen(obj.almacen):
                raise PermissionDenied("No tienes permisos para modificar ubicaciones de otro almacén.")
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["usuario"] = self.request.user
        return kwargs

    def form_valid(self, form):
        almacen_nuevo = form.cleaned_data.get("almacen")
        try:
            ubicacion = actualizar_ubicacion(
                ubicacion=self.object,
                nombre=form.cleaned_data["nombre"],
                descripcion=form.cleaned_data.get("descripcion", ""),
                almacen=almacen_nuevo,
                usuario=self.request.user,
                ip_origen=_obtener_ip(self.request),
            )
            messages.success(self.request, f"Ubicación '{ubicacion.nombre}' actualizada exitosamente.")
            return self.responder_exito(self.success_url)
        except NecesitaTraslado as exc:
            url_asistido = reverse("inventario:trasladar-ubicacion", kwargs={"pk": self.object.pk})
            if almacen_nuevo:
                url_asistido += f"?almacen_destino={almacen_nuevo.id}"
            mensaje_html = (
                f"La ubicación '{self.object.nombre}' tiene productos asignados. "
                f"Cambiar el almacén requiere trasladar los productos. "
                f"Para trasladarla a '{almacen_nuevo.nombre if almacen_nuevo else 'el nuevo almacén'}' junto con sus existencias y reasignar categorías, use el "
                f"<a href='{url_asistido}' style='color: var(--accent-2); font-weight: 700; text-decoration: underline;'>Asistente de Traslado de Ubicación</a>."
            )
            form.add_error("almacen", mark_safe(mensaje_html))
            return self.form_invalid(form)
        except Exception as exc:
            _procesar_error_formulario(form, exc)
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
        qs = Categoria.objects.filter(eliminado=False)
        mostrar_inactivos = self.request.GET.get("mostrar_inactivos") == "1"
        if not mostrar_inactivos:
            qs = qs.filter(activo=True)
        user = self.request.user
        if getattr(user, "es_usuario_normal", False):
            user_alms = user.get_almacenes_asignados()
            qs = qs.filter(almacen_principal__in=user_alms)
            almacen_id = self.request.GET.get("almacen_id")
            if almacen_id and almacen_id.isdigit():
                qs = qs.filter(almacen_principal_id=int(almacen_id))
        else:
            almacen_id = self.request.GET.get("almacen_id")
            if almacen_id:
                qs = qs.filter(almacen_principal_id=almacen_id)
        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(Q(nombre__icontains=q) | Q(prefijo_codigo__icontains=q) | Q(tipos__nombre__icontains=q)).distinct()
        return (
            qs.select_related("almacen_principal")
            .prefetch_related("tipos", "productos")
            .order_by("almacen_principal__nombre", "nombre")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        alms_disponibles = user.get_almacenes_asignados() if getattr(user, "es_usuario_normal", False) else Almacen.objects.filter(activo=True, eliminado=False).order_by("nombre")
        if not getattr(user, "es_usuario_normal", False) or alms_disponibles.count() > 1:
            context["almacenes"] = alms_disponibles
            context["almacen_seleccionado"] = self.request.GET.get("almacen_id")
        context["mostrar_inactivos"] = self.request.GET.get("mostrar_inactivos") == "1"
        context["q"] = self.request.GET.get("q", "")
        context["tipo_form"] = TipoCategoriaForm(usuario=self.request.user)
        context["categoria_form"] = CategoriaForm(usuario=self.request.user)
        return context


class CategoriaReactivarView(LoginRequiredMixin, View):
    """Reactivación de categoría previamente inhabilitada."""

    def dispatch(self, request, *args, **kwargs):
        categoria = get_object_or_404(Categoria, pk=kwargs.get("pk"), eliminado=False)
        if getattr(request.user, "es_usuario_normal", False):
            if not request.user.tiene_acceso_almacen(categoria.almacen_principal):
                raise PermissionDenied("No tienes permisos para modificar categorías de otro almacén.")
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, pk):
        categoria = get_object_or_404(Categoria, pk=pk, eliminado=False)
        try:
            reactivar_categoria(categoria=categoria, usuario=request.user, ip_origen=_obtener_ip(request))
            messages.success(request, f"Categoría '{categoria.nombre}' habilitada nuevamente.")
        except Exception as exc:
            messages.error(request, str(exc))
        return redirect(request.POST.get("next") or "inventario:categoria-lista")


class CategoriaEliminarView(LoginRequiredMixin, View):
    """Eliminación lógica de una categoría sin productos activos."""

    def dispatch(self, request, *args, **kwargs):
        categoria = get_object_or_404(Categoria, pk=kwargs.get("pk"), eliminado=False)
        if getattr(request.user, "es_usuario_normal", False):
            if not request.user.tiene_acceso_almacen(categoria.almacen_principal):
                raise PermissionDenied("No tienes permisos para eliminar categorías de otro almacén.")
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, pk):
        categoria = get_object_or_404(Categoria, pk=pk, eliminado=False)
        try:
            eliminar_categoria(categoria=categoria, usuario=request.user, ip_origen=_obtener_ip(request))
            messages.success(request, f"Categoría '{categoria.nombre}' eliminada exitosamente.")
        except NecesitaTraslado as exc:
            messages.warning(request, str(exc))
            if getattr(request.user, "es_administrador", False):
                return redirect("inventario:categoria-trasladar", pk=categoria.pk)
        except Exception as exc:
            messages.error(request, str(exc))
        return redirect(request.POST.get("next") or "inventario:categoria-lista")


class CategoriaCrearView(LoginRequiredMixin, AjaxModalFormMixin, CreateView):
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
        almacen_principal = form.cleaned_data.get("almacen_principal")
        if not user.tiene_acceso_almacen(almacen_principal):
            form.add_error("almacen_principal", "No tienes permisos para crear categorías en este almacén.")
            return self.form_invalid(form)

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
            return self.responder_exito(self.success_url)
        except Exception as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)


class CategoriaEditarView(LoginRequiredMixin, AjaxModalFormMixin, UpdateView):
    """Edición de categoría y sus tipos opcionales."""

    model = Categoria
    form_class = CategoriaForm
    template_name = "inventario/categoria_form.html"
    success_url = reverse_lazy("inventario:categoria-lista")

    def dispatch(self, request, *args, **kwargs):
        obj = self.get_object()
        if getattr(request.user, "es_usuario_normal", False):
            if not request.user.tiene_acceso_almacen(obj.almacen_principal):
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
        almacen_principal = form.cleaned_data.get("almacen_principal")
        if not user.tiene_acceso_almacen(almacen_principal):
            form.add_error("almacen_principal", "No tienes permisos para asignar este almacén.")
            return self.form_invalid(form)

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
            return self.responder_exito(self.success_url)
        except NecesitaTraslado as exc:
            url_asistido = reverse("inventario:categoria-trasladar", kwargs={"pk": self.object.pk})
            if almacen_principal:
                url_asistido += f"?almacen_destino={almacen_principal.id}"
            mensaje_html = (
                f"La categoría '{self.object.nombre}' tiene productos asociados. "
                f"Cambiar el almacén principal requiere trasladar los productos. "
                f"Para realizar este traslado y definir la ubicación física de cada producto en destino, use el "
                f"<a href='{url_asistido}' style='color: var(--accent-2); font-weight: 700; text-decoration: underline;'>Asistente de Traslado de Categoría</a>."
            )
            form.add_error("almacen_principal", mark_safe(mensaje_html))
            return self.form_invalid(form)
        except Exception as exc:
            _procesar_error_formulario(form, exc)
            return self.form_invalid(form)


class TipoCategoriaCrearView(LoginRequiredMixin, View):
    """Agrega un tipo opcional ligado a una categoría existente."""

    def post(self, request):
        categoria_id = request.POST.get("categoria")
        nombre = (request.POST.get("nombre") or "").strip()
        next_url = request.POST.get("next") or reverse("inventario:categoria-lista")

        if not categoria_id or not str(categoria_id).isdigit():
            if request.headers.get("x-requested-with") == "XMLHttpRequest":
                return JsonResponse({"success": False, "errors": {"categoria": ["Debe seleccionar una categoría válida."]}}, status=400)
            messages.error(request, "Debe seleccionar una categoría válida.")
            return redirect(next_url)

        categoria = get_object_or_404(Categoria, pk=int(categoria_id), eliminado=False)
        if getattr(request.user, "es_usuario_normal", False):
            if not request.user.tiene_acceso_almacen(categoria.almacen_principal):
                raise PermissionDenied("No tienes permisos para modificar categorías de otro almacén.")
        try:
            tipo = crear_tipo_categoria(
                nombre=nombre,
                categoria=categoria,
                usuario=request.user,
                ip_origen=_obtener_ip(request),
            )
            messages.success(request, f"Tipo '{tipo.nombre}' agregado a la categoría '{categoria.nombre}'.")
            if request.headers.get("x-requested-with") == "XMLHttpRequest":
                return JsonResponse({"success": True, "redirect_url": next_url})
        except Exception as exc:
            msg = exc.messages[0] if hasattr(exc, "messages") and exc.messages else str(exc)
            if request.headers.get("x-requested-with") == "XMLHttpRequest":
                return JsonResponse({"success": False, "errors": {"nombre": [msg]}}, status=400)
            messages.error(request, msg)
        return redirect(next_url)


class TipoCategoriaEditarView(LoginRequiredMixin, View):
    """Renombra un tipo de categoría conservando los productos e historial."""

    def dispatch(self, request, *args, **kwargs):
        tipo = get_object_or_404(TipoCategoria, pk=kwargs.get("pk"))
        if getattr(request.user, "es_usuario_normal", False):
            if not request.user.tiene_acceso_almacen(tipo.categoria.almacen_principal):
                raise PermissionDenied("No tienes permisos para modificar categorías de otro almacén.")
        return super().dispatch(request, *args, **kwargs)

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
            if request.headers.get("x-requested-with") == "XMLHttpRequest":
                return JsonResponse({"success": True, "redirect_url": next_url})
        except Exception as exc:
            msg = exc.messages[0] if hasattr(exc, "messages") and exc.messages else str(exc)
            if request.headers.get("x-requested-with") == "XMLHttpRequest":
                return JsonResponse({"success": False, "errors": {"nombre": [msg]}}, status=400)
            messages.error(request, msg)
        return redirect(next_url)


class TipoCategoriaEliminarView(LoginRequiredMixin, View):
    """Elimina un tipo de categoría opcional (los productos quedan sin tipo)."""

    def dispatch(self, request, *args, **kwargs):
        tipo = get_object_or_404(TipoCategoria, pk=kwargs.get("pk"))
        if getattr(request.user, "es_usuario_normal", False):
            if not request.user.tiene_acceso_almacen(tipo.categoria.almacen_principal):
                raise PermissionDenied("No tienes permisos para eliminar categorías de otro almacén.")
        return super().dispatch(request, *args, **kwargs)

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
    paginate_by = 10

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
        qs = Producto.objects.filter(archivado=False)
        f = self._resolver_filtros_validos()

        if f["es_normal"]:
            user_alms = self.request.user.get_almacenes_asignados()
            if f["almacen_id"]:
                qs = qs.filter(
                    Q(categoria__almacen_principal_id=f["almacen_id"])
                    | Q(existencias__almacen_id=f["almacen_id"])
                    | Q(ubicaciones__almacen_id=f["almacen_id"])
                ).distinct()
            else:
                qs = qs.filter(
                    Q(categoria__almacen_principal__in=user_alms)
                    | Q(existencias__almacen__in=user_alms)
                    | Q(ubicaciones__almacen__in=user_alms)
                ).distinct()
        elif f["almacen_id"]:
            qs = qs.filter(
                Q(categoria__almacen_principal_id=f["almacen_id"])
                | Q(existencias__almacen_id=f["almacen_id"])
                | Q(ubicaciones__almacen_id=f["almacen_id"])
            ).distinct()

        if f["categoria_id"]:
            qs = qs.filter(categoria_id=f["categoria_id"])

        if f["tipo_id"]:
            qs = qs.filter(tipo_id=f["tipo_id"])

        if f["ubicacion_id"]:
            qs = qs.filter(ubicaciones__id=f["ubicacion_id"]).distinct()

        if f["estado_activo"] == "activos":
            qs = qs.filter(activo=True)
        elif f["estado_activo"] == "inhabilitados":
            qs = qs.filter(activo=False)

        if f["q"]:
            qs = qs.filter(
                Q(nombre__icontains=f["q"])
                | Q(codigo_interno__icontains=f["q"])
                | Q(codigo_barras__icontains=f["q"])
                | Q(tipo__nombre__icontains=f["q"])
            )

        return (
            qs.select_related("categoria", "categoria__almacen_principal", "tipo")
            .prefetch_related("existencias", "ubicaciones")
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


class ProductoCrearView(LoginRequiredMixin, AjaxModalFormMixin, CreateView):
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
            form.add_error(None, str(exc))
            return self.form_invalid(form)


class ProductoEditarView(LoginRequiredMixin, AjaxModalFormMixin, UpdateView):
    """Edición de datos operativos de un producto."""

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
                | Q(ubicaciones__almacen__in=user_alms)
            ).distinct()
        return qs

    def dispatch(self, request, *args, **kwargs):
        obj = self.get_object()
        if getattr(request.user, "es_usuario_normal", False):
            user_alms = request.user.get_almacenes_asignados()
            tiene_acceso = (
                user_alms.filter(id=obj.categoria.almacen_principal_id).exists()
                or obj.existencias.filter(almacen__in=user_alms).exists()
                or obj.ubicaciones.filter(almacen__in=user_alms).exists()
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
            form.add_error(None, str(exc))
            return self.form_invalid(form)
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
            user_alms = user.get_almacenes_asignados()
            qs = qs.filter(
                Q(categoria__almacen_principal__in=user_alms)
                | Q(existencias__almacen__in=user_alms)
                | Q(ubicaciones__almacen__in=user_alms)
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
            context["ubicaciones"] = prod.ubicaciones.filter(almacen__in=user_alms).select_related("almacen")
            cats_qs = Categoria.objects.filter(activo=True, eliminado=False).filter(
                Q(almacen_principal__in=user_alms) | Q(pk=prod.categoria_id)
            ).distinct()
        else:
            context["existencias"] = prod.existencias.select_related("almacen").all()
            context["movimientos"] = prod.movimientos.select_related("usuario", "almacen_origen", "almacen_destino").order_by("-fecha_hora")[:25]
            context["ubicaciones"] = prod.ubicaciones.select_related("almacen").all()
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


class ProductoToggleActivoView(LoginRequiredMixin, View):
    """Inhabilita o reactiva un producto sin eliminarlo del catálogo."""

    def post(self, request, pk):
        producto = get_object_or_404(Producto, pk=pk, archivado=False)
        if getattr(request.user, "es_usuario_normal", False):
            user_alms = request.user.get_almacenes_asignados()
            tiene_acceso = (
                user_alms.filter(id=producto.categoria.almacen_principal_id).exists()
                or producto.existencias.filter(almacen__in=user_alms).exists()
                or producto.ubicaciones.filter(almacen__in=user_alms).exists()
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
            messages.error(request, str(exc))
        return redirect(request.POST.get("next") or "inventario:producto-lista")


class ProductoArchivarView(LoginRequiredMixin, RolRequeridoMixin, View):
    """Eliminación / Archivado lógico de un producto (solo Administrador o Superusuario)."""

    roles_permitidos = (UsuarioAudacIA.Rol.ADMINISTRADOR, UsuarioAudacIA.Rol.SUPERUSUARIO)

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
            "inventario/desactivar_ubicacion.html",
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


class UbicacionTrasladarView(LoginRequiredMixin, RolRequeridoMixin, View):
    """Traslado de una ubicación física y todos sus productos a otro almacén (§Punto 6)."""

    roles_permitidos = (UsuarioAudacIA.Rol.ADMINISTRADOR, UsuarioAudacIA.Rol.SUPERUSUARIO)

    def get(self, request, pk):
        ubicacion = get_object_or_404(Ubicacion, pk=pk, eliminado=False)
        user = request.user
        alms_permitidos = user.get_almacenes_asignados() if getattr(user, "es_usuario_normal", False) else Almacen.objects.filter(activo=True, eliminado=False)
        almacenes_destino = alms_permitidos.exclude(id=ubicacion.almacen_id).order_by("nombre")

        destino_id = request.GET.get("almacen_destino")
        if destino_id and destino_id.isdigit():
            almacen_destino_sel = almacenes_destino.filter(id=int(destino_id)).first()
        else:
            almacen_destino_sel = almacenes_destino.first()

        productos_qs = list(
            Producto.objects.filter(ubicaciones=ubicacion, archivado=False)
            .select_related("categoria")
            .distinct()
        )
        for p in productos_qs:
            ex = Existencia.objects.filter(producto=p, almacen=ubicacion.almacen).first()
            p.stock_actual = ex.stock_disponible if ex else 0

        # Construir mapa de categorías por almacén para JS
        import json
        categorias_por_almacen = {}
        for alm in almacenes_destino:
            categorias_por_almacen[str(alm.id)] = list(
                Categoria.objects.filter(almacen_principal=alm, activo=True, eliminado=False)
                .order_by("nombre")
                .values("id", "nombre")
            )

        categorias_destino_actuales = []
        if almacen_destino_sel:
            categorias_destino_actuales = Categoria.objects.filter(
                almacen_principal=almacen_destino_sel, activo=True, eliminado=False
            ).order_by("nombre")

        return render(
            request,
            "inventario/trasladar_ubicacion.html",
            {
                "ubicacion": ubicacion,
                "almacenes_destino": almacenes_destino,
                "almacen_destino_seleccionado": almacen_destino_sel,
                "productos": productos_qs,
                "categorias_destino_actuales": categorias_destino_actuales,
                "categorias_por_almacen_json": json.dumps(categorias_por_almacen),
            },
        )

    def post(self, request, pk):
        ubicacion = get_object_or_404(Ubicacion, pk=pk, eliminado=False)
        user = request.user
        destino_id = request.POST.get("almacen_destino")
        if not destino_id or not destino_id.isdigit():
            messages.error(request, "Debe seleccionar un almacén de destino válido.")
            return self.get(request, pk)

        almacen_destino = get_object_or_404(Almacen, pk=int(destino_id), activo=True, eliminado=False)
        if getattr(user, "es_usuario_normal", False) and not user.tiene_acceso_almacen(almacen_destino):
            messages.error(request, "No tienes permisos para trasladar a este almacén.")
            return self.get(request, pk)

        # Validar nombre único en almacén destino
        if Ubicacion.objects.filter(almacen=almacen_destino, nombre__iexact=ubicacion.nombre, eliminado=False).exclude(pk=ubicacion.pk).exists():
            messages.error(request, f"Ya existe una ubicación con el nombre '{ubicacion.nombre}' en '{almacen_destino.nombre}'. Renómbrela antes de trasladar.")
            return self.get(request, pk)

        productos_qs = list(
            Producto.objects.filter(ubicaciones=ubicacion, archivado=False)
            .distinct()
        )

        categorias_mapeadas = {}
        for p in productos_qs:
            cat_id = request.POST.get(f"categoria_producto_{p.id}")
            if not cat_id or not str(cat_id).isdigit():
                messages.error(request, f"Debe seleccionar una categoría en destino para el producto '{p.nombre}'.")
                return self.get(request, pk)
            cat_dest = Categoria.objects.filter(
                id=int(cat_id), almacen_principal=almacen_destino, activo=True, eliminado=False
            ).first()
            if not cat_dest:
                messages.error(request, f"Categoría destino inválida para '{p.nombre}'.")
                return self.get(request, pk)
            categorias_mapeadas[p.id] = cat_dest

        almacen_anterior = ubicacion.almacen
        total_trasladados = 0
        from django.db import transaction
        import uuid
        from inventario.auditoria import registrar_auditoria

        with transaction.atomic():
            ubicacion.almacen = almacen_destino
            ubicacion.save(update_fields=["almacen", "fecha_modificacion"])

            for p in productos_qs:
                cat_dest = categorias_mapeadas[p.id]
                p.categoria = cat_dest
                p.save(update_fields=["categoria"])

                ex_ori = Existencia.objects.select_for_update().filter(producto=p, almacen=almacen_anterior).first()
                cant = ex_ori.stock_disponible if ex_ori else 0

                if ex_ori:
                    ex_ori.stock_disponible = 0
                    if ex_ori.stock_en_prestamo == 0:
                        ex_ori.delete()
                    else:
                        ex_ori.save()

                ex_dest, _ = Existencia.objects.select_for_update().get_or_create(
                    producto=p,
                    almacen=almacen_destino,
                    defaults={"stock_disponible": 0, "stock_en_prestamo": 0},
                )
                stock_ant = ex_dest.stock_disponible
                stock_post = stock_ant + cant
                ex_dest.stock_disponible = stock_post
                ex_dest.save()

                Existencia.objects.filter(
                    producto=p, stock_disponible=0, stock_en_prestamo=0
                ).exclude(id=ex_dest.id).delete()

                if cant > 0:
                    Movimiento.objects.create(
                        clave_idempotencia=f"TRAS-UBIC-{p.id}-{almacen_anterior.id}-{almacen_destino.id}-{uuid.uuid4().hex[:8]}",
                        producto=p,
                        tipo=Movimiento.Tipo.TRASLADO_ENTRE,
                        almacen_origen=almacen_anterior,
                        almacen_destino=almacen_destino,
                        ubicacion_origen=ubicacion,
                        ubicacion_destino=ubicacion,
                        cantidad=cant,
                        stock_disponible_anterior=stock_ant,
                        stock_disponible_posterior=stock_post,
                        usuario=user,
                        motivo=f"Traslado por cambio de almacén de ubicación '{ubicacion.nombre}'",
                    )
                total_trasladados += 1

            registrar_auditoria(
                usuario=user,
                accion="TRASLADO_UBICACION",
                tipo_entidad="Ubicacion",
                id_entidad=str(ubicacion.id),
                valores_anteriores={"almacen_id": almacen_anterior.id, "almacen_nombre": almacen_anterior.nombre},
                valores_nuevos={"almacen_id": almacen_destino.id, "almacen_nombre": almacen_destino.nombre, "productos_trasladados": total_trasladados},
                motivo=f"Traslado de ubicación '{ubicacion.nombre}' a '{almacen_destino.nombre}' con reasignación de categorías",
                resultado="OK",
                ip_origen=_obtener_ip(request),
            )

        messages.success(
            request,
            f"Se trasladó la ubicación '{ubicacion.nombre}' y {total_trasladados} producto(s) a '{almacen_destino.nombre}' exitosamente."
        )
        return redirect("inventario:ubicacion-lista")


class CategoriaTrasladarView(LoginRequiredMixin, RolRequeridoMixin, View):
    """Traslado de existencias de categoría a un nuevo almacén principal reasignando ubicación física (§Punto 6)."""

    roles_permitidos = (UsuarioAudacIA.Rol.ADMINISTRADOR, UsuarioAudacIA.Rol.SUPERUSUARIO)

    def get(self, request, pk):
        categoria = get_object_or_404(Categoria, pk=pk, eliminado=False)
        user = request.user
        alms_permitidos = user.get_almacenes_asignados() if getattr(user, "es_usuario_normal", False) else Almacen.objects.filter(activo=True, eliminado=False)
        almacenes_destino = alms_permitidos.exclude(id=categoria.almacen_principal_id).order_by("nombre")

        destino_id = request.GET.get("almacen_destino")
        if destino_id and destino_id.isdigit():
            almacen_destino_sel = almacenes_destino.filter(id=int(destino_id)).first()
        else:
            almacen_destino_sel = almacenes_destino.first()

        productos_qs = list(
            categoria.productos.filter(archivado=False)
            .prefetch_related("ubicaciones")
            .distinct()
        )
        for p in productos_qs:
            ex = Existencia.objects.filter(producto=p, almacen=categoria.almacen_principal).first()
            p.stock_actual = ex.stock_disponible if ex else 0

        # Construir mapa de ubicaciones por almacén para JS
        import json
        ubicaciones_por_almacen = {}
        for alm in almacenes_destino:
            ubicaciones_por_almacen[str(alm.id)] = list(
                Ubicacion.objects.filter(almacen=alm, activo=True, eliminado=False)
                .order_by("nombre")
                .values("id", "nombre")
            )

        ubicaciones_destino_actuales = []
        if almacen_destino_sel:
            ubicaciones_destino_actuales = Ubicacion.objects.filter(
                almacen=almacen_destino_sel, activo=True, eliminado=False
            ).order_by("nombre")

        return render(
            request,
            "inventario/trasladar_categoria.html",
            {
                "categoria": categoria,
                "almacenes_destino": almacenes_destino,
                "almacen_destino_seleccionado": almacen_destino_sel,
                "productos": productos_qs,
                "ubicaciones_destino_actuales": ubicaciones_destino_actuales,
                "ubicaciones_por_almacen_json": json.dumps(ubicaciones_por_almacen),
            },
        )

    def post(self, request, pk):
        categoria = get_object_or_404(Categoria, pk=pk, eliminado=False)
        user = request.user
        destino_id = request.POST.get("almacen_destino")
        if not destino_id or not destino_id.isdigit():
            messages.error(request, "Debe seleccionar un almacén de destino válido.")
            return self.get(request, pk)

        almacen_destino = get_object_or_404(Almacen, pk=int(destino_id), activo=True, eliminado=False)
        if getattr(user, "es_usuario_normal", False) and not user.tiene_acceso_almacen(almacen_destino):
            messages.error(request, "No tienes permisos para trasladar a este almacén.")
            return self.get(request, pk)

        productos_qs = list(categoria.productos.filter(archivado=False).distinct())
        almacen_anterior = categoria.almacen_principal
        total_trasladados = 0
        from django.db import transaction
        import uuid
        from inventario.auditoria import registrar_auditoria

        with transaction.atomic():
            categoria.almacen_principal = almacen_destino
            categoria.save(update_fields=["almacen_principal", "fecha_modificacion"])

            for p in productos_qs:
                ubic_id = request.POST.get(f"ubicacion_producto_{p.id}")
                ProductoUbicacion.objects.filter(producto=p).delete()
                ubic_dest = None
                if ubic_id and str(ubic_id).isdigit():
                    ubic_dest = Ubicacion.objects.filter(
                        pk=int(ubic_id), almacen=almacen_destino, activo=True, eliminado=False
                    ).first()
                    if ubic_dest:
                        ProductoUbicacion.objects.create(producto=p, ubicacion=ubic_dest)

                ex_ori = Existencia.objects.select_for_update().filter(producto=p, almacen=almacen_anterior).first()
                cant = ex_ori.stock_disponible if ex_ori else 0

                if ex_ori:
                    ex_ori.stock_disponible = 0
                    if ex_ori.stock_en_prestamo == 0:
                        ex_ori.delete()
                    else:
                        ex_ori.save()

                ex_dest, _ = Existencia.objects.select_for_update().get_or_create(
                    producto=p,
                    almacen=almacen_destino,
                    defaults={"stock_disponible": 0, "stock_en_prestamo": 0},
                )
                stock_ant = ex_dest.stock_disponible
                stock_post = stock_ant + cant
                ex_dest.stock_disponible = stock_post
                ex_dest.save()

                Existencia.objects.filter(
                    producto=p, stock_disponible=0, stock_en_prestamo=0
                ).exclude(id=ex_dest.id).delete()

                if cant > 0:
                    Movimiento.objects.create(
                        clave_idempotencia=f"TRAS-CAT-{p.id}-{almacen_anterior.id}-{almacen_destino.id}-{uuid.uuid4().hex[:8]}",
                        producto=p,
                        tipo=Movimiento.Tipo.TRASLADO_ENTRE,
                        almacen_origen=almacen_anterior,
                        almacen_destino=almacen_destino,
                        ubicacion_destino=ubic_dest,
                        cantidad=cant,
                        stock_disponible_anterior=stock_ant,
                        stock_disponible_posterior=stock_post,
                        usuario=user,
                        motivo=f"Traslado por cambio de almacén principal de la categoría '{categoria.nombre}'",
                    )
                total_trasladados += 1

            registrar_auditoria(
                usuario=user,
                accion="TRASLADO_CATEGORIA",
                tipo_entidad="Categoria",
                id_entidad=str(categoria.id),
                valores_anteriores={"almacen_principal_id": almacen_anterior.id, "almacen_nombre": almacen_anterior.nombre},
                valores_nuevos={"almacen_principal_id": almacen_destino.id, "almacen_nombre": almacen_destino.nombre, "productos_trasladados": total_trasladados},
                motivo=f"Traslado de categoría '{categoria.nombre}' a '{almacen_destino.nombre}' con reasignación de ubicaciones físicas",
                resultado="OK",
                ip_origen=_obtener_ip(request),
            )

        messages.success(
            request,
            f"Se trasladó la categoría '{categoria.nombre}' y {total_trasladados} producto(s) a '{almacen_destino.nombre}' exitosamente."
        )
        return redirect("inventario:categoria-lista")


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
        leidas = set(request.session.get("alertas_leidas", []))
        filtro_estado = request.GET.get("estado", "todas")
        q = request.GET.get("q", "").strip().lower()

        notificaciones = []
        criticas_filtradas = []
        bajas_filtradas = []
        no_leidas_count = 0

        for ex in alertas["criticas"]:
            clave = f"{ex.producto_id}:{ex.almacen_id}:{ex.stock_disponible}"
            ex.notificacion_key = clave
            ex.es_leida = clave in leidas
            ex.tipo_alerta = "CRITICA"
            if not ex.es_leida:
                no_leidas_count += 1
            if q and q not in ex.producto.nombre.lower() and q not in ex.producto.codigo_interno.lower() and q not in ex.almacen.nombre.lower():
                continue
            if filtro_estado == "pendientes" and ex.es_leida:
                continue
            criticas_filtradas.append(ex)
            notificaciones.append(ex)

        for ex in alertas["bajas"]:
            clave = f"{ex.producto_id}:{ex.almacen_id}:{ex.stock_disponible}"
            ex.notificacion_key = clave
            ex.es_leida = clave in leidas
            ex.tipo_alerta = "BAJA"
            if not ex.es_leida:
                no_leidas_count += 1
            if q and q not in ex.producto.nombre.lower() and q not in ex.producto.codigo_interno.lower() and q not in ex.almacen.nombre.lower():
                continue
            if filtro_estado == "pendientes" and ex.es_leida:
                continue
            bajas_filtradas.append(ex)
            notificaciones.append(ex)

        alertas["total_no_leidas"] = no_leidas_count

        return render(
            request,
            "inventario/alertas_lista.html",
            {
                "alertas": alertas,
                "criticas": criticas_filtradas,
                "bajas": bajas_filtradas,
                "notificaciones": notificaciones,
                "filtro_estado": filtro_estado,
                "q": request.GET.get("q", ""),
                "entrada_form": EntradaForm(usuario=request.user),
            },
        )


class AlertaMarcarLeidaView(LoginRequiredMixin, View):
    """Marca una o todas las notificaciones de stock como leídas y opcionalmente redirige al producto."""

    def post(self, request):
        leidas = set(request.session.get("alertas_leidas", []))
        accion = request.POST.get("accion", "una")
        key = request.POST.get("key", "")
        redirect_to = request.POST.get("redirect_to", "")

        if accion == "todas":
            alertas = obtener_alertas_stock(usuario=request.user)
            for ex in list(alertas["criticas"]) + list(alertas["bajas"]):
                leidas.add(f"{ex.producto_id}:{ex.almacen_id}:{ex.stock_disponible}")
            messages.success(request, "Todas las notificaciones actuales fueron marcadas como leídas.")
        elif accion == "no_leida" and key:
            leidas.discard(key)
        elif accion == "limpiar":
            leidas.clear()
        elif key:
            leidas.add(key)

        request.session["alertas_leidas"] = list(leidas)
        request.session.modified = True

        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            alertas = obtener_alertas_stock(usuario=request.user)
            pendientes = 0
            for ex in list(alertas["criticas"]) + list(alertas["bajas"]):
                if f"{ex.producto_id}:{ex.almacen_id}:{ex.stock_disponible}" not in leidas:
                    pendientes += 1
            return JsonResponse({"ok": True, "total_no_leidas": pendientes})

        if redirect_to:
            return redirect(redirect_to)
        return redirect(request.POST.get("next") or "inventario:alertas-lista")


class ActualizarNombrePerfilView(LoginRequiredMixin, View):
    """Permite al usuario autenticado actualizar su nombre completo y username (solo superusuario) desde Mi Perfil."""

    def post(self, request):
        user = request.user
        nuevo_nombre = (request.POST.get("nombre_completo") or "").strip()
        nuevo_username = (request.POST.get("username") or "").strip()
        next_url = request.POST.get("next") or reverse("cuentas:perfil")

        if not nuevo_nombre:
            if request.headers.get("x-requested-with") == "XMLHttpRequest":
                return JsonResponse({"success": False, "errors": {"nombre_completo": ["El nombre completo no puede estar vacío."]}}, status=400)
            messages.error(request, "El nombre completo no puede estar vacío.")
            return redirect(next_url)

        fields_to_update = ["nombre_completo"]
        user.nombre_completo = nuevo_nombre

        # El nombre de usuario si se puede cambiar, pero únicamente por el superusuario
        if nuevo_username and nuevo_username != user.username:
            if not user.es_superusuario_inventario:
                if request.headers.get("x-requested-with") == "XMLHttpRequest":
                    return JsonResponse({"success": False, "errors": {"username": ["Solo el superusuario puede modificar el nombre de usuario."]}}, status=400)
                messages.error(request, "Solo el superusuario puede modificar el nombre de usuario.")
                return redirect(next_url)
            from cuentas.models import UsuarioAudacIA
            if UsuarioAudacIA.objects.filter(username__iexact=nuevo_username).exclude(pk=user.pk).exists():
                if request.headers.get("x-requested-with") == "XMLHttpRequest":
                    return JsonResponse({"success": False, "errors": {"username": ["Ya existe otro usuario con este nombre de usuario."]}}, status=400)
                messages.error(request, "Ya existe otro usuario con este nombre de usuario.")
                return redirect(next_url)
            user.username = nuevo_username
            fields_to_update.append("username")

        user.save(update_fields=fields_to_update)
        messages.success(request, "Tu perfil ha sido actualizado exitosamente.")
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"success": True, "redirect_url": next_url})
        return redirect(next_url)


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
    """Centro unificado de Movimientos: Entradas, Salidas, Préstamos (con Antigüedad), Traslados y Conteos Físicos."""

    model = Movimiento
    template_name = "inventario/movimiento_lista.html"
    context_object_name = "movimientos"
    paginate_by = 15

    def get_queryset(self):
        qs = Movimiento.objects.select_related(
            "producto",
            "almacen_origen",
            "almacen_destino",
            "ubicacion_origen",
            "ubicacion_destino",
            "usuario",
            "prestamo",
            "movimiento_original",
            "movimiento_original__prestamo",
        ).order_by("-fecha_hora")

        user = self.request.user
        if getattr(user, "es_usuario_normal", False):
            user_alms = user.get_almacenes_asignados()
            if user_alms.exists():
                qs = qs.filter(Q(almacen_origen__in=user_alms) | Q(almacen_destino__in=user_alms))
            else:
                return qs.none()

        vista = self.request.GET.get("vista", "").upper()
        if vista == "TRASLADO" and getattr(user, "es_usuario_normal", False):
            raise PermissionDenied("Los usuarios estándar no tienen acceso a la vista de traslados.")
        tipo = self.request.GET.get("tipo", "")

        if not vista or vista == "TODOS":
            if tipo in ("SALIDA", "DEVOLUCION", "BAJA_PRESTAMO"):
                vista = "SALIDA"
            elif tipo == "PRESTAMO":
                vista = "PRESTAMOS"
            elif tipo in ("TRASLADO_INTERNO", "TRASLADO_ENTRE"):
                vista = "TRASLADO"
            else:
                vista = "PRESTAMOS"

        if tipo:
            qs = qs.filter(tipo=tipo)
        elif vista == "PRESTAMOS":
            qs = qs.filter(tipo__in=[Movimiento.Tipo.PRESTAMO, Movimiento.Tipo.DEVOLUCION, Movimiento.Tipo.BAJA_PRESTAMO])
        elif vista == "DOTACION":
            qs = qs.filter(tipo=Movimiento.Tipo.SALIDA).exclude(destinatario="").exclude(destinatario__isnull=True)
        elif vista == "ENTRADA":
            qs = qs.filter(tipo=Movimiento.Tipo.ENTRADA)
        elif vista == "SALIDA":
            qs = qs.filter(
                tipo__in=[
                    Movimiento.Tipo.SALIDA,
                    Movimiento.Tipo.PRESTAMO,
                    Movimiento.Tipo.DEVOLUCION,
                    Movimiento.Tipo.BAJA_PRESTAMO,
                ]
            )
        elif vista == "TRASLADO":
            qs = qs.filter(
                tipo__in=[
                    Movimiento.Tipo.TRASLADO_INTERNO,
                    Movimiento.Tipo.TRASLADO_ENTRE,
                ]
            )

        fecha_desde = self.request.GET.get("fecha_desde", "").strip()
        fecha_hasta = self.request.GET.get("fecha_hasta", "").strip()
        almacen_id = self.request.GET.get("almacen_id", "").strip()
        if fecha_desde:
            try:
                datetime.strptime(fecha_desde, "%Y-%m-%d")
                qs = qs.filter(fecha_hora__date__gte=fecha_desde)
            except ValueError:
                pass
        if fecha_hasta:
            try:
                datetime.strptime(fecha_hasta, "%Y-%m-%d")
                qs = qs.filter(fecha_hora__date__lte=fecha_hasta)
            except ValueError:
                pass
        if almacen_id and almacen_id.isdigit():
            qs = qs.filter(Q(almacen_origen_id=int(almacen_id)) | Q(almacen_destino_id=int(almacen_id)))

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
        user = self.request.user
        vista = self.request.GET.get("vista", "").upper()
        tipo = self.request.GET.get("tipo", "")
        q = self.request.GET.get("q", "").strip()

        if not vista or vista == "TODOS":
            if tipo in ("SALIDA", "DEVOLUCION", "BAJA_PRESTAMO"):
                vista = "SALIDA"
            elif tipo == "PRESTAMO":
                vista = "PRESTAMOS"
            elif tipo in ("TRASLADO_INTERNO", "TRASLADO_ENTRE"):
                vista = "TRASLADO"
            else:
                vista = "PRESTAMOS"

        # Enriquecer cada movimiento con Fecha de Entrega y Fecha de Devolución cuando aplique
        for mov in context["movimientos"]:
            mov.fecha_entrega_info = None
            mov.fecha_devolucion_info = None
            mov.prestamo_vinculado = None

            if mov.tipo == Movimiento.Tipo.PRESTAMO:
                mov.fecha_entrega_info = mov.fecha_hora
                try:
                    p = mov.prestamo
                    mov.prestamo_vinculado = p
                    mov.fecha_devolucion_info = p.fecha_cierre
                except Exception:
                    pass
            elif mov.tipo in (Movimiento.Tipo.DEVOLUCION, Movimiento.Tipo.BAJA_PRESTAMO):
                mov.fecha_devolucion_info = mov.fecha_hora
                if mov.movimiento_original_id:
                    mov.fecha_entrega_info = mov.movimiento_original.fecha_hora
                    try:
                        mov.prestamo_vinculado = mov.movimiento_original.prestamo
                    except Exception:
                        pass
            elif mov.tipo == Movimiento.Tipo.SALIDA:
                mov.fecha_entrega_info = mov.fecha_hora

        # Subsección Préstamos (dentro de Salidas / Movimientos) con Antigüedad de Préstamos
        prestamos_qs = Prestamo.objects.select_related(
            "producto", "almacen", "usuario", "ubicacion_origen", "movimiento"
        ).prefetch_related("devoluciones").order_by("-fecha_creacion")
        if getattr(user, "es_usuario_normal", False):
            user_alms = user.get_almacenes_asignados()
            if user_alms.exists():
                prestamos_qs = prestamos_qs.filter(almacen__in=user_alms)
            else:
                prestamos_qs = prestamos_qs.none()

        fecha_desde = self.request.GET.get("fecha_desde", "").strip()
        fecha_hasta = self.request.GET.get("fecha_hasta", "").strip()
        almacen_id = self.request.GET.get("almacen_id", "").strip()
        if fecha_desde:
            try:
                datetime.strptime(fecha_desde, "%Y-%m-%d")
                prestamos_qs = prestamos_qs.filter(fecha_creacion__date__gte=fecha_desde)
            except ValueError:
                pass
        if fecha_hasta:
            try:
                datetime.strptime(fecha_hasta, "%Y-%m-%d")
                prestamos_qs = prestamos_qs.filter(fecha_creacion__date__lte=fecha_hasta)
            except ValueError:
                pass
        if almacen_id and almacen_id.isdigit():
            prestamos_qs = prestamos_qs.filter(almacen_id=int(almacen_id))

        estado_prestamo = self.request.GET.get("estado_prestamo", "")
        if estado_prestamo:
            prestamos_qs = prestamos_qs.filter(estado=estado_prestamo)
        if q:
            prestamos_qs = prestamos_qs.filter(
                Q(producto__nombre__icontains=q)
                | Q(producto__codigo_interno__icontains=q)
                | Q(destinatario__icontains=q)
                | Q(movimiento__motivo__icontains=q)
            )

        ahora = timezone.now()
        prestamos_lista = list(prestamos_qs[:100])
        for p in prestamos_lista:
            fecha_fin = p.fecha_cierre or ahora
            p.dias_transcurridos = max(0, (fecha_fin - p.fecha_creacion).days)
            devs = list(p.devoluciones.all())
            p.ultima_devolucion_fecha = p.fecha_cierre or (devs[0].fecha if devs else None)
            p.motivo_prestamo = p.movimiento.motivo if p.movimiento_id else ""

        # Subsección Conteos Físicos (integrada en Movimientos)
        conteos_qs = ConteoFisico.objects.select_related(
            "producto", "almacen", "usuario_conteo", "usuario_confirmacion"
        ).order_by("-fecha_conteo")
        if getattr(user, "es_usuario_normal", False):
            user_alms = user.get_almacenes_asignados()
            if user_alms.exists():
                conteos_qs = conteos_qs.filter(almacen__in=user_alms)
            else:
                conteos_qs = conteos_qs.none()
        if fecha_desde:
            try:
                datetime.strptime(fecha_desde, "%Y-%m-%d")
                conteos_qs = conteos_qs.filter(fecha_conteo__date__gte=fecha_desde)
            except ValueError:
                pass
        if fecha_hasta:
            try:
                datetime.strptime(fecha_hasta, "%Y-%m-%d")
                conteos_qs = conteos_qs.filter(fecha_conteo__date__lte=fecha_hasta)
            except ValueError:
                pass
        if almacen_id and almacen_id.isdigit():
            conteos_qs = conteos_qs.filter(almacen_id=int(almacen_id))

        if q:
            conteos_qs = conteos_qs.filter(
                Q(producto__nombre__icontains=q)
                | Q(producto__codigo_interno__icontains=q)
                | Q(motivo__icontains=q)
            )

        # Subsección Artículos Sin Rotación (integrada en Movimientos)
        dias_str = self.request.GET.get("dias", "30").strip()
        dias_estancados = int(dias_str) if dias_str.isdigit() else 30
        from web.servicios.reportes import obtener_reporte_productos_estancados
        alm_obj_filtro = Almacen.objects.filter(id=int(almacen_id), activo=True, eliminado=False).first() if (almacen_id and almacen_id.isdigit()) else None
        estancados_lista = obtener_reporte_productos_estancados(usuario=user, dias_inactividad=dias_estancados, almacen=alm_obj_filtro)
        if q:
            q_low = q.lower()
            estancados_lista = [
                prod for prod in estancados_lista
                if q_low in prod.nombre.lower() or q_low in prod.codigo_interno.lower() or q_low in prod.categoria.nombre.lower()
            ]

        context["vista_actual"] = vista
        context["tipos_movimiento"] = Movimiento.Tipo.choices
        context["tipo_seleccionado"] = tipo
        context["estado_prestamo"] = estado_prestamo
        context["estados_prestamo"] = Prestamo.Estado.choices
        context["fecha_desde"] = fecha_desde
        context["fecha_hasta"] = fecha_hasta
        context["almacen_id"] = almacen_id
        context["q"] = q
        context["prestamos_lista"] = prestamos_lista
        context["conteos_lista"] = conteos_qs[:100]
        context["estancados_lista"] = estancados_lista
        context["dias_estancados"] = dias_estancados

        # Formularios para el modal unificado "+ Nuevo Movimiento"
        context["entrada_form"] = EntradaForm(usuario=user)
        context["salida_form"] = SalidaForm(usuario=user)
        context["prestamo_form"] = PrestamoForm(usuario=user)
        context["traslado_form"] = TrasladoForm(usuario=user)
        context["conteo_form"] = ConteoFisicoForm(usuario=user)

        # Información de roles y almacenes para sincronización de UI
        es_normal = getattr(user, "es_usuario_normal", False)
        user_alms = user.get_almacenes_asignados() if es_normal else Almacen.objects.filter(activo=True, eliminado=False)
        context["es_usuario_normal"] = es_normal
        context["usuario_almacenes_count"] = user_alms.count() if es_normal else 999
        primer_alm = user_alms.first() if es_normal and user_alms.count() == 1 else None
        context["usuario_almacen_unico_id"] = str(primer_alm.id) if primer_alm else ""
        context["usuario_almacen_unico_nombre"] = primer_alm.nombre if primer_alm else ""

        # Mapa de almacén, ubicación fija y stock disponible por producto (acotado por permisos de rol)
        import json
        mapa_ubic = {}
        prods_qs = Producto.objects.filter(archivado=False, activo=True).select_related("categoria__almacen_principal").prefetch_related("ubicaciones__almacen", "existencias__almacen")
        if es_normal:
            prods_qs = prods_qs.filter(
                Q(categoria__almacen_principal__in=user_alms)
                | Q(existencias__almacen__in=user_alms)
                | Q(ubicaciones__almacen__in=user_alms)
            ).distinct()

        for prod in prods_qs:
            if es_normal:
                ub = prod.ubicaciones.filter(almacen__in=user_alms).first() or prod.ubicaciones.first()
                ex_user = prod.existencias.filter(almacen__in=user_alms).first()
                if ex_user:
                    alm = ex_user.almacen
                elif ub and ub.almacen:
                    alm = ub.almacen
                else:
                    alm = prod.categoria.almacen_principal
            else:
                ub = prod.ubicaciones.first()
                alm = ub.almacen if ub else prod.categoria.almacen_principal
            ex = prod.existencias.filter(almacen=alm).first() if alm else None
            stock_disp = ex.stock_disponible if ex else 0
            mapa_ubic[str(prod.id)] = {
                "id": str(ub.id) if ub else "",
                "nombre": ub.nombre if ub else "Sin ubicación física",
                "almacen_id": str(alm.id) if alm else "",
                "almacen_nombre": alm.nombre if alm else "Sin almacén",
                "stock_disponible": stock_disp,
                "nombre_empaque": prod.nombre_empaque or "Paquete",
                "unidades_por_empaque": prod.unidades_por_empaque or 1,
            }
        context["mapa_ubicaciones_json"] = json.dumps(mapa_ubic)

        # Ubicaciones agrupadas por almacén para el selector dinámico de traslado (acotadas por permisos de rol)
        alms_ubs = {}
        ubs_qs = Ubicacion.objects.filter(activo=True, eliminado=False).order_by("nombre")
        if es_normal:
            ubs_qs = ubs_qs.filter(almacen__in=user_alms)
        for u in ubs_qs:
            aid = str(u.almacen_id)
            if aid not in alms_ubs:
                alms_ubs[aid] = []
            alms_ubs[aid].append({"id": str(u.id), "nombre": u.nombre})
        context["almacen_ubicaciones_json"] = json.dumps(alms_ubs)
        context["almacenes_filtro"] = user_alms.order_by("nombre")

        # Categorías agrupadas por almacén para el selector dinámico de traslado (§5.1)
        alms_cats = {}
        cats_qs = Categoria.objects.filter(activo=True, eliminado=False).order_by("nombre")
        if es_normal:
            cats_qs = cats_qs.filter(almacen_principal__in=user_alms)
        for c in cats_qs:
            aid = str(c.almacen_principal_id)
            if aid not in alms_cats:
                alms_cats[aid] = []
            alms_cats[aid].append({"id": str(c.id), "nombre": c.nombre})
        context["almacen_categorias_json"] = json.dumps(alms_cats)
        return context


class EntradaCrearView(LoginRequiredMixin, View):
    """Registro operativo de entrada de mercancía (con soporte de empaques e idempotencia)."""

    def get(self, request):
        form = EntradaForm(usuario=request.user)
        return render(request, "inventario/movimiento_entrada.html", {"form": form})

    def post(self, request):
        form = EntradaForm(request.POST, usuario=request.user)
        next_url = request.POST.get("next")
        if form.is_valid():
            try:
                prod = form.cleaned_data["producto"]
                alm = form.cleaned_data["almacen"]
                ubic = form.cleaned_data.get("ubicacion")
                if not ubic:
                    ubic = prod.ubicaciones.filter(almacen=alm).first() or prod.ubicaciones.first()
                existencia, mov = registrar_entrada(
                    producto=prod,
                    almacen=alm,
                    cantidad=form.cleaned_data.get("cantidad") or 0,
                    usuario=request.user,
                    ubicacion=ubic,
                    es_empaque=form.cleaned_data.get("es_empaque", False),
                    cantidad_empaques=form.cleaned_data.get("cantidad_empaques"),
                    clave_idempotencia=request.POST.get("clave_idempotencia") or None,
                    motivo=form.cleaned_data.get("motivo", ""),
                    observacion=form.cleaned_data.get("observacion", ""),
                    ip_origen=_obtener_ip(request),
                )
                messages.success(
                    request,
                    f"Entrada registrada: +{mov.cantidad} unid. de '{existencia.producto.codigo_interno}'. Stock actual: {existencia.stock_disponible}.",
                )
                if request.headers.get("x-requested-with") == "XMLHttpRequest":
                    return JsonResponse({"success": True, "redirect_url": next_url or reverse("inventario:movimiento-lista")})
                return redirect(next_url or "inventario:movimiento-lista")
            except Exception as exc:
                form.add_error(None, str(exc))
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            errores = {}
            for field, err_list in form.errors.get_json_data().items():
                errores[field] = [e["message"] for e in err_list]
            return JsonResponse({"success": False, "errors": errores}, status=400)
        if next_url:
            for errs in form.errors.values():
                for e in errs:
                    messages.error(request, str(e))
            return redirect(next_url)
        return render(request, "inventario/movimiento_entrada.html", {"form": form})


class SalidaCrearView(LoginRequiredMixin, View):
    """Registro operativo de salida de stock con verificación y alerta inmediata."""

    def get(self, request):
        form = SalidaForm(usuario=request.user)
        return render(request, "inventario/movimiento_salida.html", {"form": form})

    def post(self, request):
        form = SalidaForm(request.POST, usuario=request.user)
        next_url = request.POST.get("next")
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
                    clave_idempotencia=request.POST.get("clave_idempotencia") or None,
                    observacion=form.cleaned_data.get("observacion", ""),
                    ip_origen=_obtener_ip(request),
                )
                messages.success(
                    request,
                    f"Dotación registrada: -{mov.cantidad} unid. de '{existencia.producto.codigo_interno}' para {mov.destinatario}. Stock restante: {existencia.stock_disponible}.",
                )
                if alerta.get("nivel"):
                    messages.warning(request, alerta["mensaje"])
                if request.headers.get("x-requested-with") == "XMLHttpRequest":
                    return JsonResponse({"success": True, "redirect_url": next_url or reverse("inventario:movimiento-lista")})
                return redirect(next_url or "inventario:movimiento-lista")
            except Exception as exc:
                form.add_error(None, str(exc))
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            errores = {}
            for field, err_list in form.errors.get_json_data().items():
                errores[field] = [e["message"] for e in err_list]
            return JsonResponse({"success": False, "errors": errores}, status=400)
        if next_url:
            for errs in form.errors.values():
                for e in errs:
                    messages.error(request, str(e))
            return redirect(next_url)
        return render(request, "inventario/movimiento_salida.html", {"form": form})


# ─────────────────────────────────────────────────────────────────────────────
# 9. PRÉSTAMOS, DEVOLUCIONES Y BAJAS
# ─────────────────────────────────────────────────────────────────────────────

class PrestamoListaView(LoginRequiredMixin, ListView):
    """Listado y seguimiento de préstamos."""

    model = Prestamo
    template_name = "inventario/prestamo_lista.html"
    context_object_name = "prestamos"
    paginate_by = 10

    def get_queryset(self):
        qs = Prestamo.objects.select_related("producto", "almacen", "usuario", "ubicacion_origen").order_by("-fecha_creacion")
        user = self.request.user
        if getattr(user, "es_usuario_normal", False):
            user_alms = user.get_almacenes_asignados()
            if user_alms.exists():
                qs = qs.filter(almacen__in=user_alms)
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
        next_url = request.POST.get("next")
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
                    clave_idempotencia=request.POST.get("clave_idempotencia") or None,
                    ip_origen=_obtener_ip(request),
                )
                messages.success(
                    request,
                    f"Préstamo registrado exitosamente: {prestamo.cantidad_original} unid. de '{prestamo.producto.codigo_interno}' a {prestamo.destinatario}.",
                )
                if alerta.get("nivel"):
                    messages.warning(request, alerta["mensaje"])
                if request.headers.get("x-requested-with") == "XMLHttpRequest":
                    return JsonResponse({"success": True, "redirect_url": next_url or reverse("inventario:movimiento-lista")})
                return redirect(next_url or "inventario:prestamo-lista")
            except Exception as exc:
                form.add_error(None, str(exc))
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            errores = {}
            for field, err_list in form.errors.get_json_data().items():
                errores[field] = [e["message"] for e in err_list]
            return JsonResponse({"success": False, "errors": errores}, status=400)
        if next_url:
            for errs in form.errors.values():
                for e in errs:
                    messages.error(request, str(e))
            return redirect(next_url)
        return render(request, "inventario/prestamo_form.html", {"form": form})


class PrestamoDevolucionView(LoginRequiredMixin, View):
    """Registro de devolución total o parcial de un préstamo."""

    def dispatch(self, request, *args, **kwargs):
        prestamo = get_object_or_404(Prestamo, pk=kwargs.get("pk"))
        if getattr(request.user, "es_usuario_normal", False):
            if not request.user.tiene_acceso_almacen(prestamo.almacen):
                raise PermissionDenied("No tienes permisos para gestionar préstamos de otro almacén.")
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, pk):
        prestamo = get_object_or_404(Prestamo, pk=pk)
        form = DevolucionPrestamoForm(initial={"cantidad": prestamo.cantidad_pendiente_devolucion})
        return render(request, "inventario/prestamo_devolucion.html", {"prestamo": prestamo, "form": form})

    def post(self, request, pk):
        prestamo = get_object_or_404(Prestamo, pk=pk)
        form = DevolucionPrestamoForm(request.POST)
        next_url = request.POST.get("next")
        if form.is_valid():
            try:
                p_db, dev, mov = registrar_devolucion_prestamo(
                    prestamo=prestamo,
                    cantidad=form.cleaned_data["cantidad"],
                    usuario=request.user,
                    motivo=form.cleaned_data["motivo"],
                    observacion=form.cleaned_data.get("observacion", ""),
                    clave_idempotencia=request.POST.get("clave_idempotencia") or None,
                    ip_origen=_obtener_ip(request),
                )
                messages.success(
                    request,
                    f"Devolución registrada: {dev.cantidad} unid. de '{p_db.producto.codigo_interno}'. Saldo pendiente: {p_db.cantidad_pendiente_devolucion} ({p_db.get_estado_display()}).",
                )
                if request.headers.get("x-requested-with") == "XMLHttpRequest":
                    return JsonResponse({"success": True, "redirect_url": next_url or reverse("inventario:prestamo-lista")})
                return redirect(next_url or "inventario:prestamo-lista")
            except Exception as exc:
                form.add_error(None, str(exc))
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            errores = {}
            for field, err_list in form.errors.get_json_data().items():
                errores[field] = [e["message"] for e in err_list]
            return JsonResponse({"success": False, "errors": errores}, status=400)
        if next_url:
            for errs in form.errors.values():
                for e in errs:
                    messages.error(request, str(e))
            return redirect(next_url)
        return render(request, "inventario/prestamo_devolucion.html", {"prestamo": prestamo, "form": form})


class PrestamoBajaView(LoginRequiredMixin, View):
    """Registro de baja por daño o pérdida de un préstamo (§9.4)."""

    def dispatch(self, request, *args, **kwargs):
        prestamo = get_object_or_404(Prestamo, pk=kwargs.get("pk"))
        if getattr(request.user, "es_usuario_normal", False):
            if not request.user.tiene_acceso_almacen(prestamo.almacen):
                raise PermissionDenied("No tienes permisos para dar de baja préstamos de otro almacén.")
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, pk):
        prestamo = get_object_or_404(Prestamo, pk=pk)
        form = BajaPrestamoForm(initial={"cantidad": 1})
        return render(request, "inventario/prestamo_baja.html", {"prestamo": prestamo, "form": form})

    def post(self, request, pk):
        prestamo = get_object_or_404(Prestamo, pk=pk)
        form = BajaPrestamoForm(request.POST)
        next_url = request.POST.get("next")
        if form.is_valid():
            try:
                p_db, baja, mov = registrar_baja_prestamo(
                    prestamo=prestamo,
                    cantidad=form.cleaned_data["cantidad"],
                    usuario=request.user,
                    motivo=form.cleaned_data["motivo"],
                    observacion=form.cleaned_data.get("observacion", ""),
                    clave_idempotencia=request.POST.get("clave_idempotencia") or None,
                    ip_origen=_obtener_ip(request),
                )
                messages.warning(
                    request,
                    f"Baja por daño/pérdida registrada: {baja.cantidad} unid. de '{p_db.producto.codigo_interno}'. Saldo pendiente: {p_db.cantidad_pendiente_devolucion}.",
                )
                if request.headers.get("x-requested-with") == "XMLHttpRequest":
                    return JsonResponse({"success": True, "redirect_url": next_url or reverse("inventario:prestamo-lista")})
                return redirect(next_url or "inventario:prestamo-lista")
            except Exception as exc:
                form.add_error(None, str(exc))
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            errores = {}
            for field, err_list in form.errors.get_json_data().items():
                errores[field] = [e["message"] for e in err_list]
            return JsonResponse({"success": False, "errors": errores}, status=400)
        if next_url:
            for errs in form.errors.values():
                for e in errs:
                    messages.error(request, str(e))
            return redirect(next_url)
        return render(request, "inventario/prestamo_baja.html", {"prestamo": prestamo, "form": form})


# ─────────────────────────────────────────────────────────────────────────────
# 10. TRASLADOS OPERATIVOS
# ─────────────────────────────────────────────────────────────────────────────

class TrasladoCrearView(LoginRequiredMixin, RolRequeridoMixin, View):
    """Registro de traslado operativo (interno o entre almacenes). Exclusivo de Administrador y Superusuario."""

    roles_permitidos = (UsuarioAudacIA.Rol.ADMINISTRADOR, UsuarioAudacIA.Rol.SUPERUSUARIO)

    def get(self, request):
        form = TrasladoForm(usuario=request.user)
        return render(request, "inventario/movimiento_traslado.html", {"form": form})

    def post(self, request):
        form = TrasladoForm(request.POST, usuario=request.user)
        next_url = request.POST.get("next")
        if form.is_valid():
            prod = form.cleaned_data["producto"]
            alm_orig = form.cleaned_data["almacen_origen"]
            alm_dest = form.cleaned_data.get("almacen_destino")
            ub_orig = form.cleaned_data.get("ubicacion_origen")
            ub_dest = form.cleaned_data.get("ubicacion_destino")
            cant = form.cleaned_data.get("cantidad")
            if not cant:
                ex_o = Existencia.objects.filter(producto=prod, almacen=alm_orig).first()
                cant = ex_o.stock_disponible if ex_o else 0
            mot = form.cleaned_data.get("motivo", "")
            idem = request.POST.get("clave_idempotencia") or None

            try:
                if alm_dest and alm_dest.id != alm_orig.id:
                    # Traslado entre almacenes
                    cat_dest = form.cleaned_data.get("categoria_destino")
                    ex_o, ex_d, mov = registrar_traslado_entre_almacenes(
                        producto=prod,
                        almacen_origen=alm_orig,
                        almacen_destino=alm_dest,
                        cantidad=cant,
                        usuario=request.user,
                        ubicacion_origen=ub_orig,
                        ubicacion_destino=ub_dest,
                        categoria_destino=cat_dest,
                        motivo=mot,
                        clave_idempotencia=idem,
                        ip_origen=_obtener_ip(request),
                    )
                    messages.success(
                        request,
                        f"Traslado completado: {cant} unid. de '{prod.codigo_interno}' enviadas de '{alm_orig.nombre}' a '{alm_dest.nombre}'.",
                    )
                else:
                    # Traslado interno
                    current_ub = prod.ubicaciones.filter(almacen=alm_orig).first()
                    nueva_ub = ub_dest or ub_orig
                    if not nueva_ub:
                        raise ValidationError("Para un traslado interno debe indicar la nueva ubicación de destino.")
                    if current_ub and current_ub.id == nueva_ub.id:
                        raise ValidationError("La nueva ubicación debe ser distinta a la ubicación actual del producto.")
                    origen_efectivo = current_ub or ub_orig
                    if not origen_efectivo:
                        raise ValidationError("Debe indicar la ubicación de origen.")

                    ex, mov = registrar_traslado_interno(
                        producto=prod,
                        almacen=alm_orig,
                        ubicacion_origen=origen_efectivo,
                        ubicacion_destino=nueva_ub,
                        usuario=request.user,
                        motivo=mot,
                        clave_idempotencia=idem,
                        ip_origen=_obtener_ip(request),
                    )
                    messages.success(
                        request,
                        f"Traslado interno completado: '{prod.codigo_interno}' reubicado a '{nueva_ub.nombre}'.",
                    )
                if request.headers.get("x-requested-with") == "XMLHttpRequest":
                    return JsonResponse({"success": True, "redirect_url": next_url or reverse("inventario:movimiento-lista")})
                return redirect(next_url or "inventario:movimiento-lista")
            except Exception as exc:
                form.add_error(None, str(exc))
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            errores = {}
            for field, err_list in form.errors.get_json_data().items():
                errores[field] = [e["message"] for e in err_list]
            return JsonResponse({"success": False, "errors": errores}, status=400)
        if next_url:
            for errs in form.errors.values():
                for e in errs:
                    messages.error(request, str(e))
            return redirect(next_url)
        return render(request, "inventario/movimiento_traslado.html", {"form": form})


# ─────────────────────────────────────────────────────────────────────────────
# 11. CONTEO FÍSICO Y AJUSTES
# ─────────────────────────────────────────────────────────────────────────────

class ConteoFisicoListaView(LoginRequiredMixin, ListView):
    """Listado de conteos físicos y solicitudes de ajuste."""

    model = ConteoFisico
    template_name = "inventario/conteo_lista.html"
    context_object_name = "conteos"
    paginate_by = 10

    def get_queryset(self):
        qs = ConteoFisico.objects.select_related("producto", "almacen", "usuario_conteo", "usuario_confirmacion").order_by("-fecha_conteo")
        user = self.request.user
        if getattr(user, "es_usuario_normal", False):
            user_alms = user.get_almacenes_asignados()
            if user_alms.exists():
                qs = qs.filter(almacen__in=user_alms)
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
        next_url = request.POST.get("next")
        if form.is_valid():
            try:
                mot = form.cleaned_data.get("motivo", "").strip()
                obs = form.cleaned_data.get("observacion", "").strip()
                motivo_completo = f"{mot} — {obs}" if obs else mot
                conteo = registrar_conteo_fisico(
                    producto=form.cleaned_data["producto"],
                    almacen=form.cleaned_data["almacen"],
                    cantidad_contada=form.cleaned_data["cantidad_contada"],
                    usuario=request.user,
                    motivo=motivo_completo,
                    ip_origen=_obtener_ip(request),
                )
                messages.success(
                    request,
                    f"Conteo registrado: Contado {conteo.cantidad_contada} unid. vs {conteo.stock_sistema} en sistema (Diferencia: {conteo.diferencia:+d}). Pendiente de confirmación administrativa.",
                )
                if request.headers.get("x-requested-with") == "XMLHttpRequest":
                    return JsonResponse({"success": True, "redirect_url": next_url or reverse("inventario:conteo-lista")})
                return redirect(next_url or "inventario:conteo-lista")
            except Exception as exc:
                form.add_error(None, str(exc))
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            errores = {}
            for field, err_list in form.errors.get_json_data().items():
                errores[field] = [e["message"] for e in err_list]
            return JsonResponse({"success": False, "errors": errores}, status=400)
        if next_url:
            for errs in form.errors.values():
                for e in errs:
                    messages.error(request, str(e))
            return redirect(next_url)
        return render(request, "inventario/conteo_form.html", {"form": form})


class ConteoFisicoConfirmarView(LoginRequiredMixin, RolRequeridoMixin, View):
    """Confirmación y aplicación administrativa de ajuste por conteo físico (§6.2, §9.7)."""

    roles_permitidos = (UsuarioAudacIA.Rol.ADMINISTRADOR, UsuarioAudacIA.Rol.SUPERUSUARIO)

    def post(self, request, pk):
        conteo = get_object_or_404(ConteoFisico, pk=pk)
        next_url = request.POST.get("next")
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
        return redirect(next_url or "inventario:conteo-lista")


# ─────────────────────────────────────────────────────────────────────────────
# 12. REVERSIÓN DE MOVIMIENTOS
# ─────────────────────────────────────────────────────────────────────────────

class MovimientoRevertirView(LoginRequiredMixin, View):
    """Reversión de un movimiento previo con verificación estricta de no negatividad (§9.6)."""

    def dispatch(self, request, *args, **kwargs):
        movimiento = get_object_or_404(Movimiento, pk=kwargs.get("pk"))
        if getattr(request.user, "es_usuario_normal", False):
            if "TRASLADO" in movimiento.tipo:
                raise PermissionDenied("Los usuarios estándar no tienen permisos para revertir traslados.")
            alm_mov = movimiento.almacen_origen or movimiento.almacen_destino
            if alm_mov and not request.user.tiene_acceso_almacen(alm_mov):
                raise PermissionDenied("No tienes permisos para revertir movimientos de otro almacén.")
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, pk):
        movimiento = get_object_or_404(Movimiento, pk=pk)
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


# ─────────────────────────────────────────────────────────────────────────────
# 10. VISTAS PERSONALIZADAS DE ERROR (403, 404, 500)
# ─────────────────────────────────────────────────────────────────────────────

def error_403_view(request, exception=None):
    """Vista personalizada para error 403 (Permiso Denegado)."""
    return render(request, "403.html", status=403)


def error_404_view(request, exception=None):
    """Vista personalizada para error 404 (No Encontrado)."""
    return render(request, "404.html", status=404)


def error_500_view(request):
    """Vista personalizada para error 500 (Error del Servidor)."""
    return render(request, "500.html", status=500)



