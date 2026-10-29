"""Vistas de alertas de stock, notificaciones y perfil de usuario."""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.generic import View

from cuentas.models import UsuarioAudacIA
from cuentas.permisos import PermisoRequeridoMixin
from inventario.forms import EntradaForm
from inventario.models import Almacen, ConteoFisico
from inventario.servicios.alertas import (
    desmarcar_alerta_stock_leida,
    limpiar_alertas_stock_leidas,
    marcar_alerta_stock_leida,
    marcar_todas_alertas_stock_leidas,
    obtener_alertas_stock,
    obtener_claves_alertas_leidas,
)


class AlertasStockListaView(LoginRequiredMixin, PermisoRequeridoMixin, View):
    """Centro de Notificaciones y Alertas de Stock Bajo y Crítico."""

    permiso_requerido = "analitica.ver_alertas"

    def get(self, request):
        user = request.user
        if getattr(user, "es_usuario_normal", False):
            almacenes = user.get_almacenes_asignados()
        else:
            almacenes = Almacen.objects.filter(activo=True, eliminado=False).order_by("nombre")

        almacen_id = request.GET.get("almacen_id", "").strip()
        almacen_obj = None
        if almacen_id:
            almacen_obj = almacenes.filter(pk=almacen_id).first()

        alertas = obtener_alertas_stock(usuario=user, almacen=almacen_obj)
        leidas = obtener_claves_alertas_leidas(user)
        if hasattr(request, "session"):
            request.session["alertas_leidas"] = list(leidas)

        nivel = request.GET.get("nivel", "").strip().upper()
        if nivel not in ["CRITICA", "BAJA"]:
            nivel = "TODAS"

        filtro_estado = request.GET.get("estado", "").strip().lower()
        if filtro_estado not in ["pendientes", "leidas"]:
            filtro_estado = "todas"

        q = request.GET.get("q", "").strip()
        q_lower = q.lower()

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
            if nivel == "BAJA":
                continue
            if q_lower and (q_lower not in ex.producto.nombre.lower() and 
                            q_lower not in ex.producto.codigo_interno.lower() and 
                            q_lower not in ex.almacen.nombre.lower()):
                continue
            if filtro_estado == "pendientes" and ex.es_leida:
                continue
            if filtro_estado == "leidas" and not ex.es_leida:
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
            if nivel == "CRITICA":
                continue
            if q_lower and (q_lower not in ex.producto.nombre.lower() and 
                            q_lower not in ex.producto.codigo_interno.lower() and 
                            q_lower not in ex.almacen.nombre.lower()):
                continue
            if filtro_estado == "pendientes" and ex.es_leida:
                continue
            if filtro_estado == "leidas" and not ex.es_leida:
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
                "almacenes": almacenes,
                "almacen_id": str(almacen_obj.id) if almacen_obj else "",
                "almacen_obj": almacen_obj,
                "nivel": nivel,
                "filtro_estado": filtro_estado,
                "q": q,
                "entrada_form": EntradaForm(usuario=request.user),
            },
        )


class AlertaMarcarLeidaView(LoginRequiredMixin, View):
    """Marca una o todas las notificaciones como leídas de forma persistente en BD."""

    def post(self, request):
        user = request.user
        accion = request.POST.get("accion", "una")
        key = (request.POST.get("key") or "").strip()
        redirect_to = (request.POST.get("redirect_to") or "").strip()

        if accion == "todas":
            alertas = obtener_alertas_stock(usuario=user)
            claves = [
                f"{ex.producto_id}:{ex.almacen_id}:{ex.stock_disponible}"
                for ex in list(alertas["criticas"]) + list(alertas["bajas"])
            ]
            if getattr(user, "es_administrador", False):
                conteos_pendientes_ids = ConteoFisico.objects.filter(
                    estado=ConteoFisico.Estado.PENDIENTE
                ).values_list("id", flat=True)
                claves.extend([f"conteo:{cid}" for cid in conteos_pendientes_ids])

            marcar_todas_alertas_stock_leidas(user, claves)
            messages.success(request, "Todas las notificaciones actuales fueron marcadas como leídas.")
        elif accion == "no_leida" and key:
            desmarcar_alerta_stock_leida(user, key)
        elif accion == "limpiar":
            limpiar_alertas_stock_leidas(user)
        elif key:
            marcar_alerta_stock_leida(user, key)

        leidas = obtener_claves_alertas_leidas(user)
        request.session["alertas_leidas"] = list(leidas)
        request.session.modified = True

        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            alertas = obtener_alertas_stock(usuario=user)
            pendientes = 0
            for ex in list(alertas["criticas"]) + list(alertas["bajas"]):
                if f"{ex.producto_id}:{ex.almacen_id}:{ex.stock_disponible}" not in leidas:
                    pendientes += 1
            if getattr(user, "es_administrador", False):
                for c in ConteoFisico.objects.filter(estado=ConteoFisico.Estado.PENDIENTE):
                    if f"conteo:{c.id}" not in leidas:
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


class AlertasStockJSONView(LoginRequiredMixin, PermisoRequeridoMixin, View):
    """Endpoint AJAX / JSON para consultar el estado de alertas en vivo."""

    permiso_requerido = "analitica.ver_alertas"

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
