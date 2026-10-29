"""Vistas operativas para préstamos, devoluciones y bajas de artículos."""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.generic import View

from cuentas.permisos import PermisoRequeridoMixin
from inventario.forms import BajaPrestamoForm, DevolucionPrestamoForm, PrestamoForm
from inventario.models import Prestamo
from inventario.servicios.operaciones import (
    registrar_baja_prestamo,
    registrar_devolucion_prestamo,
    registrar_prestamo,
)
from inventario.views._comun import (
    RedireccionPestañaMovimientosView,
    _obtener_ip,
    _procesar_error_formulario,
)


class PrestamoListaView(RedireccionPestañaMovimientosView):
    """Redirección canónica al Centro Unificado de Movimientos (pestaña Préstamos)."""
    vista_destino = "PRESTAMOS"


class PrestamoCrearView(LoginRequiredMixin, PermisoRequeridoMixin, View):
    """Registro de entrega de artículos en préstamo."""

    permiso_requerido = "movimientos.prestamo"

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
                    return JsonResponse({"success": True, "redirect_url": next_url or f"{reverse('inventario:movimiento-lista')}?vista=PRESTAMOS"})
                return redirect(next_url or f"{reverse('inventario:movimiento-lista')}?vista=PRESTAMOS")
            except Exception as exc:
                _procesar_error_formulario(form, exc)
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


class PrestamoDevolucionView(LoginRequiredMixin, PermisoRequeridoMixin, View):
    """Registro de devolución total o parcial de un préstamo."""

    permiso_requerido = "movimientos.devolucion"

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
                    return JsonResponse({"success": True, "redirect_url": next_url or f"{reverse('inventario:movimiento-lista')}?vista=PRESTAMOS"})
                return redirect(next_url or f"{reverse('inventario:movimiento-lista')}?vista=PRESTAMOS")
            except Exception as exc:
                _procesar_error_formulario(form, exc)
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


class PrestamoBajaView(LoginRequiredMixin, PermisoRequeridoMixin, View):
    """Registro de baja por daño o pérdida de un préstamo (§9.4)."""

    permiso_requerido = "movimientos.baja"

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
                    return JsonResponse({"success": True, "redirect_url": next_url or f"{reverse('inventario:movimiento-lista')}?vista=PRESTAMOS"})
                return redirect(next_url or f"{reverse('inventario:movimiento-lista')}?vista=PRESTAMOS")
            except Exception as exc:
                _procesar_error_formulario(form, exc)
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
