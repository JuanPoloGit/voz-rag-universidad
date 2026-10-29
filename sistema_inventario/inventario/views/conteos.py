"""Vistas operativas y de gestión de Conteos Físicos de inventario."""

import json
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.generic import View

from cuentas.models import UsuarioAudacIA
from cuentas.permisos import PermisoRequeridoMixin, RolRequeridoMixin
from inventario.forms import ConteoFisicoForm
from inventario.models import ConteoFisico
from inventario.servicios.operaciones import (
    confirmar_conteo_fisico,
    rechazar_conteo_fisico,
    registrar_conteo_fisico,
)
from inventario.views._comun import (
    RedireccionPestañaMovimientosView,
    _formatear_error_excepcion,
    _obtener_ip,
    _procesar_error_formulario,
)


class ConteoFisicoListaView(RedireccionPestañaMovimientosView):
    """Redirección canónica al Centro Unificado de Movimientos (pestaña Conteos Físicos)."""
    vista_destino = "CONTEOS"


class ConteoFisicoCrearView(LoginRequiredMixin, PermisoRequeridoMixin, View):
    """Registro de conteo físico preliminar."""

    permiso_requerido = "movimientos.ver"

    def _obtener_mapa_producto_almacen(self, form):
        qs = form.fields["producto"].queryset.select_related("categoria__almacen_principal")
        mapa = {
            str(p.id): {
                "almacen_id": str(p.categoria.almacen_principal_id) if (p.categoria and p.categoria.almacen_principal_id) else "",
                "almacen_nombre": p.categoria.almacen_principal.nombre if (p.categoria and p.categoria.almacen_principal) else "",
            }
            for p in qs
        }
        return json.dumps(mapa)

    def get(self, request):
        form = ConteoFisicoForm(usuario=request.user)
        return render(
            request,
            "inventario/conteo_form.html",
            {
                "form": form,
                "producto_almacenes_json": self._obtener_mapa_producto_almacen(form),
            },
        )

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
                    return JsonResponse({"success": True, "redirect_url": next_url or f"{reverse('inventario:movimiento-lista')}?vista=CONTEOS"})
                return redirect(next_url or f"{reverse('inventario:movimiento-lista')}?vista=CONTEOS")
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
        return render(
            request,
            "inventario/conteo_form.html",
            {
                "form": form,
                "producto_almacenes_json": self._obtener_mapa_producto_almacen(form),
            },
        )


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
            messages.error(request, _formatear_error_excepcion(exc))
        return redirect(next_url or f"{reverse('inventario:movimiento-lista')}?vista=CONTEOS")


class ConteoFisicoRechazarView(LoginRequiredMixin, RolRequeridoMixin, View):
    """Rechazo administrativo de un conteo físico sin alterar existencias (§6.2, §9.7)."""

    roles_permitidos = (UsuarioAudacIA.Rol.ADMINISTRADOR, UsuarioAudacIA.Rol.SUPERUSUARIO)

    def post(self, request, pk):
        conteo = get_object_or_404(ConteoFisico, pk=pk)
        next_url = request.POST.get("next")
        motivo = request.POST.get("motivo_rechazo", "").strip() or request.POST.get("motivo", "").strip()
        try:
            conteo_db = rechazar_conteo_fisico(
                conteo=conteo,
                usuario=request.user,
                motivo=motivo,
                ip_origen=_obtener_ip(request),
            )
            messages.success(
                request,
                f"El conteo físico para '{conteo_db.producto.codigo_interno}' ha sido rechazado correctamente.",
            )
        except Exception as exc:
            messages.error(request, _formatear_error_excepcion(exc))
        return redirect(next_url or f"{reverse('inventario:movimiento-lista')}?vista=CONTEOS")
