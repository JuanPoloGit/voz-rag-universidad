"""Vistas de infraestructura: Almacenes, Ubicaciones, Categorías y Tipos de Categoría."""

import json
import uuid
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.safestring import mark_safe
from django.views.generic import CreateView, ListView, UpdateView, View

from cuentas.models import UsuarioAudacIA
from cuentas.permisos import PermisoRequeridoMixin, RolRequeridoMixin
from inventario.auditoria import registrar_auditoria
from inventario.forms import AlmacenForm, CategoriaForm, TipoCategoriaForm, UbicacionForm
from inventario.models import Almacen, Categoria, Existencia, Movimiento, Prestamo, Producto, TipoCategoria, Ubicacion
from inventario.servicios.datos_maestros import (
    NecesitaTraslado,
    actualizar_almacen,
    actualizar_categoria,
    actualizar_tipo_categoria,
    actualizar_ubicacion,
    crear_almacen,
    crear_categoria,
    crear_tipo_categoria,
    crear_ubicacion,
    eliminar_almacen,
    eliminar_categoria,
    eliminar_tipo_categoria,
    eliminar_ubicacion,
    reactivar_almacen,
    reactivar_categoria,
    reactivar_ubicacion,
    sincronizar_tipos_categoria,
)
from inventario.servicios.stock import (
    desactivar_almacen,
    desactivar_categoria,
    desactivar_ubicacion,
)
from inventario.utils import normalizar_texto
from inventario.views._comun import (
    AjaxModalFormMixin,
    _formatear_error_excepcion,
    _obtener_ip,
    _procesar_error_formulario,
)


# ─────────────────────────────────────────────────────────────────────────────
# 1. ALMACENES
# ─────────────────────────────────────────────────────────────────────────────

class AlmacenListaView(LoginRequiredMixin, ListView):
    """Lista de almacenes registrados."""

    model = Almacen
    template_name = "inventario/almacen_lista.html"
    context_object_name = "almacenes"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not (request.user.es_administrador or request.user.puede_acceder_modulo_infraestructura):
            raise PermissionDenied("No tienes permisos para acceder a infraestructura.")
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        user = self.request.user
        if not (user.es_administrador or user.tiene_permiso("infraestructura.ver")):
            return Almacen.objects.none()
        qs = Almacen.objects.filter(eliminado=False)
        if getattr(user, "es_usuario_normal", False):
            qs = qs.filter(id__in=user.get_almacenes_asignados())
        mostrar_inactivos = self.request.GET.get("mostrar_inactivos") == "1"
        if not mostrar_inactivos:
            qs = qs.filter(activo=True)
        q = self.request.GET.get("q", "").strip()
        if q:
            q_norm = normalizar_texto(q)
            qs = qs.filter(Q(nombre_normalizado__contains=q_norm) | Q(descripcion__icontains=q))
        return qs.order_by("nombre")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        context["puede_ver_almacenes"] = user.es_administrador or user.tiene_permiso("infraestructura.ver")
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
            messages.error(request, _formatear_error_excepcion(exc))
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
            messages.error(request, _formatear_error_excepcion(exc))
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
            _procesar_error_formulario(form, exc)
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
            _procesar_error_formulario(form, exc)
            return self.form_invalid(form)


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
            messages.error(request, _formatear_error_excepcion(exc))
            return self.get(request, pk)


# ─────────────────────────────────────────────────────────────────────────────
# 2. UBICACIONES
# ─────────────────────────────────────────────────────────────────────────────

class UbicacionListaView(LoginRequiredMixin, ListView):
    """Lista de ubicaciones físicas accesibles por el usuario."""

    model = Ubicacion
    template_name = "inventario/ubicacion_lista.html"
    context_object_name = "ubicaciones"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not (request.user.es_administrador or request.user.puede_acceder_modulo_infraestructura):
            raise PermissionDenied("No tienes permisos para acceder a infraestructura.")
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        user = self.request.user
        if not (user.es_administrador or user.tiene_permiso("infraestructura.ver")):
            return Ubicacion.objects.none()
        qs = Ubicacion.objects.filter(eliminado=False)
        mostrar_inactivos = self.request.GET.get("mostrar_inactivos") == "1"
        if not mostrar_inactivos:
            qs = qs.filter(activo=True)
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
            q_norm = normalizar_texto(q)
            qs = qs.filter(Q(nombre_normalizado__contains=q_norm) | Q(descripcion__icontains=q) | Q(almacen__nombre_normalizado__contains=q_norm))
        return qs.select_related("almacen").order_by("almacen__nombre", "nombre")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        context["puede_ver_ubicaciones"] = user.es_administrador or user.tiene_permiso("infraestructura.ver")
        alms_disponibles = user.get_almacenes_asignados() if getattr(user, "es_usuario_normal", False) else Almacen.objects.filter(activo=True, eliminado=False).order_by("nombre")
        if not getattr(user, "es_usuario_normal", False) or alms_disponibles.count() > 1:
            context["almacenes"] = alms_disponibles
            context["almacen_seleccionado"] = self.request.GET.get("almacen_id")
        context["mostrar_inactivos"] = self.request.GET.get("mostrar_inactivos") == "1"
        context["q"] = self.request.GET.get("q", "")
        context["ubicacion_form"] = UbicacionForm(usuario=self.request.user)
        return context


class UbicacionReactivarView(LoginRequiredMixin, PermisoRequeridoMixin, View):
    """Reactivación de ubicación física inhabilitada."""

    permiso_requerido = "infraestructura.gestionar_ubicaciones"

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
            messages.error(request, _formatear_error_excepcion(exc))
        return redirect(request.POST.get("next") or "inventario:ubicacion-lista")


class UbicacionEliminarView(LoginRequiredMixin, PermisoRequeridoMixin, View):
    """Eliminación lógica de ubicación física (o redirección a traslado si tiene productos)."""

    permiso_requerido = "infraestructura.gestionar_ubicaciones"

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
            messages.error(request, _formatear_error_excepcion(exc))
        return redirect(request.POST.get("next") or "inventario:ubicacion-lista")


class UbicacionCrearView(LoginRequiredMixin, PermisoRequeridoMixin, AjaxModalFormMixin, CreateView):
    """Creación de ubicación en almacén autorizado."""

    permiso_requerido = "infraestructura.gestionar_ubicaciones"
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
            _procesar_error_formulario(form, exc)
            return self.form_invalid(form)


class UbicacionEditarView(LoginRequiredMixin, PermisoRequeridoMixin, AjaxModalFormMixin, UpdateView):
    """Edición de ubicación física conservando historial del nombre previo y permitiendo cambio de almacén (§Punto 4)."""

    permiso_requerido = "infraestructura.gestionar_ubicaciones"
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
        except NecesitaTraslado:
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


class UbicacionDesactivarView(LoginRequiredMixin, RolRequeridoMixin, View):
    """Desactivación de ubicación con reasignación obligatoria (§7.2)."""

    roles_permitidos = (UsuarioAudacIA.Rol.ADMINISTRADOR, UsuarioAudacIA.Rol.SUPERUSUARIO)

    def get(self, request, pk):
        ubicacion = get_object_or_404(Ubicacion, pk=pk)
        productos = ubicacion.productos.filter(archivado=False)
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
            messages.error(request, _formatear_error_excepcion(exc))
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
            Producto.objects.filter(ubicacion=ubicacion, archivado=False)
            .select_related("categoria")
            .distinct()
        )
        for p in productos_qs:
            ex = Existencia.objects.filter(producto=p, almacen=ubicacion.almacen).first()
            p.stock_actual = ex.stock_disponible if ex else 0

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

        if Ubicacion.objects.filter(almacen=almacen_destino, nombre__iexact=ubicacion.nombre, eliminado=False).exclude(pk=ubicacion.pk).exists():
            messages.error(request, f"Ya existe una ubicación con el nombre '{ubicacion.nombre}' en '{almacen_destino.nombre}'. Renómbrela antes de trasladar.")
            return self.get(request, pk)

        productos_qs = list(
            Producto.objects.filter(ubicacion=ubicacion, archivado=False)
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

        with transaction.atomic():
            ubicacion.almacen = almacen_destino
            ubicacion.save(update_fields=["almacen", "fecha_modificacion"])

            for p in productos_qs:
                cat_dest = categorias_mapeadas[p.id]
                p.categoria = cat_dest
                p.save(update_fields=["categoria"])

                ex_ori = Existencia.objects.select_for_update().filter(producto=p, almacen=almacen_anterior).first()
                cant = ex_ori.stock_disponible if ex_ori else 0
                cant_prest = ex_ori.stock_en_prestamo if ex_ori else 0

                if ex_ori:
                    ex_ori.delete()

                ex_dest, _ = Existencia.objects.select_for_update().get_or_create(
                    producto=p,
                    almacen=almacen_destino,
                    defaults={"stock_disponible": 0, "stock_en_prestamo": 0},
                )
                stock_ant = ex_dest.stock_disponible
                stock_post = stock_ant + cant
                ex_dest.stock_disponible = stock_post
                ex_dest.stock_en_prestamo += cant_prest
                ex_dest.save()

                Prestamo.objects.filter(producto=p, almacen=almacen_anterior).update(almacen=almacen_destino)

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


# ─────────────────────────────────────────────────────────────────────────────
# 3. CATEGORÍAS Y TIPOS OPCIONALES
# ─────────────────────────────────────────────────────────────────────────────

class CategoriaListaView(LoginRequiredMixin, ListView):
    """Lista de categorías de productos accesibles por el usuario con sus tipos opcionales."""

    model = Categoria
    template_name = "inventario/categoria_lista.html"
    context_object_name = "categorias"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not (request.user.es_administrador or request.user.puede_acceder_modulo_infraestructura):
            raise PermissionDenied("No tienes permisos para acceder a infraestructura.")
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        user = self.request.user
        if not (user.es_administrador or user.tiene_permiso("infraestructura.ver")):
            return Categoria.objects.none()
        qs = Categoria.objects.filter(eliminado=False)
        mostrar_inactivos = self.request.GET.get("mostrar_inactivos") == "1"
        if not mostrar_inactivos:
            qs = qs.filter(activo=True)
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
            q_norm = normalizar_texto(q)
            qs = qs.filter(Q(nombre_normalizado__contains=q_norm) | Q(prefijo_codigo__icontains=q) | Q(tipos__nombre__icontains=q)).distinct()
        return (
            qs.select_related("almacen_principal")
            .prefetch_related("tipos", "productos")
            .order_by("almacen_principal__nombre", "nombre")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        context["puede_ver_categorias"] = user.es_administrador or user.tiene_permiso("infraestructura.ver")
        alms_disponibles = user.get_almacenes_asignados() if getattr(user, "es_usuario_normal", False) else Almacen.objects.filter(activo=True, eliminado=False).order_by("nombre")
        if not getattr(user, "es_usuario_normal", False) or alms_disponibles.count() > 1:
            context["almacenes"] = alms_disponibles
            context["almacen_seleccionado"] = self.request.GET.get("almacen_id")
        context["mostrar_inactivos"] = self.request.GET.get("mostrar_inactivos") == "1"
        context["q"] = self.request.GET.get("q", "")
        context["tipo_form"] = TipoCategoriaForm(usuario=self.request.user)
        context["categoria_form"] = CategoriaForm(usuario=self.request.user)
        return context


class CategoriaReactivarView(LoginRequiredMixin, PermisoRequeridoMixin, View):
    """Reactivación de categoría previamente inhabilitada."""

    permiso_requerido = "infraestructura.gestionar_categorias"

    def dispatch(self, request, *args, **kwargs):
        ubicacion = get_object_or_404(Categoria, pk=kwargs.get("pk"), eliminado=False)
        if getattr(request.user, "es_usuario_normal", False):
            if not request.user.tiene_acceso_almacen(ubicacion.almacen_principal):
                raise PermissionDenied("No tienes permisos para modificar categorías de otro almacén.")
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, pk):
        categoria = get_object_or_404(Categoria, pk=pk, eliminado=False)
        try:
            reactivar_categoria(categoria=categoria, usuario=request.user, ip_origen=_obtener_ip(request))
            messages.success(request, f"Categoría '{categoria.nombre}' habilitada nuevamente.")
        except Exception as exc:
            messages.error(request, _formatear_error_excepcion(exc))
        return redirect(request.POST.get("next") or "inventario:categoria-lista")


class CategoriaEliminarView(LoginRequiredMixin, PermisoRequeridoMixin, View):
    """Eliminación lógica de una categoría sin productos activos."""

    permiso_requerido = "infraestructura.gestionar_categorias"

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
            messages.error(request, _formatear_error_excepcion(exc))
        return redirect(request.POST.get("next") or "inventario:categoria-lista")


class CategoriaCrearView(LoginRequiredMixin, PermisoRequeridoMixin, AjaxModalFormMixin, CreateView):
    """Creación de una categoría con almacén principal obligatorio y tipos opcionales."""

    permiso_requerido = "infraestructura.gestionar_categorias"
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
            _procesar_error_formulario(form, exc)
            return self.form_invalid(form)


class CategoriaEditarView(LoginRequiredMixin, PermisoRequeridoMixin, AjaxModalFormMixin, UpdateView):
    """Edición de categoría y sus tipos opcionales."""

    permiso_requerido = "infraestructura.gestionar_categorias"
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
        except NecesitaTraslado:
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


class TipoCategoriaCrearView(LoginRequiredMixin, PermisoRequeridoMixin, View):
    """Agrega un tipo opcional ligado a una categoría existente."""

    permiso_requerido = "infraestructura.gestionar_categorias"

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


class TipoCategoriaEditarView(LoginRequiredMixin, PermisoRequeridoMixin, View):
    """Renombra un tipo de categoría conservando los productos e historial."""

    permiso_requerido = "infraestructura.gestionar_categorias"

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


class TipoCategoriaEliminarView(LoginRequiredMixin, PermisoRequeridoMixin, View):
    """Elimina un tipo de categoría opcional (los productos quedan sin tipo)."""

    permiso_requerido = "infraestructura.gestionar_categorias"

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
            .distinct()
        )
        for p in productos_qs:
            ex = Existencia.objects.filter(producto=p, almacen=categoria.almacen_principal).first()
            p.stock_actual = ex.stock_disponible if ex else 0

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

        with transaction.atomic():
            categoria.almacen_principal = almacen_destino
            categoria.save(update_fields=["almacen_principal", "fecha_modificacion"])

            for p in productos_qs:
                ubic_id = request.POST.get(f"ubicacion_producto_{p.id}")
                ubic_dest = None
                if ubic_id and str(ubic_id).isdigit():
                    ubic_dest = Ubicacion.objects.filter(
                        pk=int(ubic_id), almacen=almacen_destino, activo=True, eliminado=False
                    ).first()
                p.ubicacion = ubic_dest
                p.save(update_fields=["ubicacion"])

                ex_ori = Existencia.objects.select_for_update().filter(producto=p, almacen=almacen_anterior).first()
                cant = ex_ori.stock_disponible if ex_ori else 0
                cant_prest = ex_ori.stock_en_prestamo if ex_ori else 0

                if ex_ori:
                    ex_ori.delete()

                ex_dest, _ = Existencia.objects.select_for_update().get_or_create(
                    producto=p,
                    almacen=almacen_destino,
                    defaults={"stock_disponible": 0, "stock_en_prestamo": 0},
                )
                stock_ant = ex_dest.stock_disponible
                stock_post = stock_ant + cant
                ex_dest.stock_disponible = stock_post
                ex_dest.stock_en_prestamo += cant_prest
                ex_dest.save()

                Prestamo.objects.filter(producto=p, almacen=almacen_anterior).update(almacen=almacen_destino)

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
            messages.error(request, _formatear_error_excepcion(exc))
        return redirect("inventario:categoria-lista")
