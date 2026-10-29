"""Vistas operativas del Centro Unificado de Movimientos: Kardex, Entradas, Salidas, Traslados y Reversiones."""

import json
from datetime import datetime
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Prefetch, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.generic import ListView, View

from cuentas.permisos import PermisoRequeridoMixin
from inventario.forms import (
    ConteoFisicoForm,
    EntradaForm,
    PrestamoForm,
    ReversionForm,
    SalidaForm,
    TrasladoForm,
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
    Ubicacion,
)
from inventario.servicios.operaciones import (
    registrar_entrada,
    registrar_salida,
    registrar_traslado_entre_almacenes,
    registrar_traslado_interno,
    revertir_movimiento,
)
from inventario.utils import normalizar_texto
from inventario.views._comun import _obtener_ip, _procesar_error_formulario


class MovimientoListaView(LoginRequiredMixin, ListView):
    """Centro unificado de Movimientos: Entradas, Salidas, Préstamos (con Antigüedad), Traslados y Conteos Físicos."""

    model = Movimiento
    template_name = "inventario/movimiento_lista.html"
    context_object_name = "movimientos"
    paginate_by = 15

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not (request.user.es_administrador or request.user.puede_acceder_modulo_movimientos):
            raise PermissionDenied("No tienes permisos para acceder al módulo de movimientos.")
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        user = self.request.user
        vista = self.request.GET.get("vista", "").upper()
        if vista == "TRASLADO":
            if not (user.es_administrador or user.tiene_permiso("movimientos.traslado_interno") or user.tiene_permiso("movimientos.traslado_almacen")):
                raise PermissionDenied("No tienes permisos para consultar la vista de traslados.")

        if not (user.es_administrador or user.tiene_permiso("movimientos.ver")):
            return Movimiento.objects.none()

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

        # Optimización Fase 4: Despacho perezoso. Si la pestaña activa no es de movimientos,
        # evitamos evaluar la consulta pesada sobre Movimiento.
        if vista in ("PRESTAMOS", "CONTEOS", "SIN_ROTACION"):
            return Movimiento.objects.none()

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

        if getattr(user, "es_usuario_normal", False):
            user_alms = user.get_almacenes_asignados()
            if user_alms.exists():
                qs = qs.filter(Q(almacen_origen__in=user_alms) | Q(almacen_destino__in=user_alms))
            else:
                return qs.none()

        if tipo:
            qs = qs.filter(tipo=tipo)
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
            q_norm = normalizar_texto(q)
            qs = qs.filter(
                Q(producto__nombre_normalizado__contains=q_norm)
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
        page_num = self.request.GET.get("page", 1)

        if not vista or vista == "TODOS":
            if tipo in ("SALIDA", "DEVOLUCION", "BAJA_PRESTAMO"):
                vista = "SALIDA"
            elif tipo == "PRESTAMO":
                vista = "PRESTAMOS"
            elif tipo in ("TRASLADO_INTERNO", "TRASLADO_ENTRE"):
                vista = "TRASLADO"
            else:
                vista = "PRESTAMOS"

        fecha_desde = self.request.GET.get("fecha_desde", "").strip()
        fecha_hasta = self.request.GET.get("fecha_hasta", "").strip()
        almacen_id = self.request.GET.get("almacen_id", "").strip()
        estado_prestamo = self.request.GET.get("estado_prestamo", "")
        estado_conteo = self.request.GET.get("estado_conteo", "").strip()
        dias_str = self.request.GET.get("dias", "30").strip()
        dias_estancados = int(dias_str) if dias_str.isdigit() else 30

        puede_ver_historial = user.es_administrador or user.tiene_permiso("movimientos.ver")
        context["vista_actual"] = vista
        context["puede_ver_historial"] = puede_ver_historial

        # Inicialización de colecciones según pestaña activa (Carga Perezosa - Fase 4)
        prestamos_lista = []
        conteos_lista = []
        estancados_lista = []

        if puede_ver_historial:
            if vista == "PRESTAMOS":
                prestamos_qs = (
                    Prestamo.objects.select_related(
                        "producto", "almacen", "usuario", "ubicacion_origen", "movimiento"
                    )
                    .prefetch_related(
                        Prefetch(
                            "devoluciones",
                            queryset=DevolucionPrestamo.objects.select_related("usuario", "movimiento").order_by("-fecha"),
                        )
                    )
                    .order_by("-fecha_creacion")
                )
                if getattr(user, "es_usuario_normal", False):
                    user_alms = user.get_almacenes_asignados()
                    if user_alms.exists():
                        prestamos_qs = prestamos_qs.filter(almacen__in=user_alms)
                    else:
                        prestamos_qs = prestamos_qs.none()

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
                if estado_prestamo:
                    prestamos_qs = prestamos_qs.filter(estado=estado_prestamo)
                if q:
                    q_norm = normalizar_texto(q)
                    prestamos_qs = prestamos_qs.filter(
                        Q(producto__nombre_normalizado__contains=q_norm)
                        | Q(producto__codigo_interno__icontains=q)
                        | Q(destinatario__icontains=q)
                        | Q(movimiento__motivo__icontains=q)
                    )

                paginador_prestamos = Paginator(prestamos_qs, self.paginate_by)
                pagina_prestamos = paginador_prestamos.get_page(page_num)
                ahora = timezone.now()
                for p in pagina_prestamos.object_list:
                    fecha_fin = p.fecha_cierre or ahora
                    p.dias_transcurridos = max(0, (fecha_fin - p.fecha_creacion).days)
                    devs = list(p.devoluciones.all())
                    p.ultima_devolucion_fecha = p.fecha_cierre or (devs[0].fecha if devs else None)
                    p.motivo_prestamo = p.movimiento.motivo if p.movimiento_id else ""

                prestamos_lista = pagina_prestamos.object_list
                context["page_obj"] = pagina_prestamos
                context["paginator"] = paginador_prestamos
                context["is_paginated"] = pagina_prestamos.has_other_pages()

            elif vista == "CONTEOS":
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
                if estado_conteo and estado_conteo in ConteoFisico.Estado.values:
                    conteos_qs = conteos_qs.filter(estado=estado_conteo)
                if q:
                    q_norm = normalizar_texto(q)
                    conteos_qs = conteos_qs.filter(
                        Q(producto__nombre_normalizado__contains=q_norm)
                        | Q(producto__codigo_interno__icontains=q)
                        | Q(motivo__icontains=q)
                    )

                paginador_conteos = Paginator(conteos_qs, self.paginate_by)
                pagina_conteos = paginador_conteos.get_page(page_num)
                conteos_lista = pagina_conteos.object_list
                context["page_obj"] = pagina_conteos
                context["paginator"] = paginador_conteos
                context["is_paginated"] = pagina_conteos.has_other_pages()

            elif vista == "SIN_ROTACION":
                from web.servicios.reportes import obtener_reporte_productos_estancados
                alm_obj_filtro = (
                    Almacen.objects.filter(id=int(almacen_id), activo=True, eliminado=False).first()
                    if (almacen_id and almacen_id.isdigit())
                    else None
                )
                todos_estancados = obtener_reporte_productos_estancados(
                    usuario=user, dias_inactividad=dias_estancados, almacen=alm_obj_filtro
                )
                if q:
                    q_norm = normalizar_texto(q)
                    todos_estancados = [
                        prod for prod in todos_estancados
                        if q_norm in normalizar_texto(prod.nombre)
                        or q_norm in normalizar_texto(prod.codigo_interno)
                        or q_norm in normalizar_texto(prod.categoria.nombre)
                    ]
                paginador_estancados = Paginator(todos_estancados, self.paginate_by)
                pagina_estancados = paginador_estancados.get_page(page_num)
                estancados_lista = pagina_estancados.object_list
                context["page_obj"] = pagina_estancados
                context["paginator"] = paginador_estancados
                context["is_paginated"] = pagina_estancados.has_other_pages()

            else:
                # Vistas de movimientos operativos (ENTRADA, SALIDA, DOTACION, TRASLADO)
                for mov in context.get("movimientos", []):
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

        context["prestamos_lista"] = prestamos_lista
        context["conteos_lista"] = conteos_lista
        context["estancados_lista"] = estancados_lista
        context["dias_estancados"] = dias_estancados
        context["tipos_movimiento"] = Movimiento.Tipo.choices
        context["tipo_seleccionado"] = tipo
        context["estado_prestamo"] = estado_prestamo
        context["estados_prestamo"] = Prestamo.Estado.choices
        context["estado_conteo"] = estado_conteo
        context["estados_conteo"] = ConteoFisico.Estado.choices
        context["fecha_desde"] = fecha_desde
        context["fecha_hasta"] = fecha_hasta
        context["almacen_id"] = almacen_id
        context["q"] = q

        # Información de roles y almacenes compartida para sincronización de UI
        es_normal = getattr(user, "es_usuario_normal", False)
        user_alms_qs = user.get_almacenes_asignados() if es_normal else Almacen.objects.filter(activo=True, eliminado=False)
        alms_compartidos = list(user_alms_qs.order_by("nombre"))
        user_alms_count = len(alms_compartidos) if es_normal else 999
        primer_alm = alms_compartidos[0] if es_normal and len(alms_compartidos) == 1 else None

        context["es_usuario_normal"] = es_normal
        context["usuario_almacenes_count"] = user_alms_count
        context["usuario_almacen_unico_id"] = str(primer_alm.id) if primer_alm else ""
        context["usuario_almacen_unico_nombre"] = primer_alm.nombre if primer_alm else ""

        # Ubicaciones y Categorías compartidas (consultadas una sola vez en bloque)
        ubs_qs = Ubicacion.objects.filter(activo=True, eliminado=False).order_by("nombre")
        if es_normal:
            ubs_qs = ubs_qs.filter(almacen__in=alms_compartidos)
        ubs_compartidas = list(ubs_qs)

        cats_qs = Categoria.objects.filter(activo=True, eliminado=False).order_by("nombre")
        if es_normal:
            cats_qs = cats_qs.filter(almacen_principal__in=alms_compartidos)
        cats_compartidas = list(cats_qs)

        # Productos compartidos para mapa y formularios
        prods_qs = (
            Producto.objects.filter(archivado=False, activo=True)
            .select_related("categoria__almacen_principal", "ubicacion__almacen")
            .prefetch_related("existencias__almacen")
        )
        if es_normal:
            prods_qs = prods_qs.filter(
                Q(categoria__almacen_principal__in=alms_compartidos)
                | Q(existencias__almacen__in=alms_compartidos)
                | Q(ubicacion__almacen__in=alms_compartidos)
            ).distinct()
        prods_compartidos = list(prods_qs)

        # Formularios para el modal unificado "+ Nuevo Movimiento" reutilizando caché
        entrada_form = EntradaForm(usuario=user)
        salida_form = SalidaForm(usuario=user)
        prestamo_form = PrestamoForm(usuario=user)
        traslado_form = TrasladoForm(usuario=user)
        conteo_form = ConteoFisicoForm(usuario=user)

        choices_prod = [("", "---------")] + [(p.id, str(p)) for p in prods_compartidos]
        choices_alm = [("", "---------")] + [(a.id, str(a)) for a in alms_compartidos]
        choices_ub = [("", "---------")] + [(u.id, str(u)) for u in ubs_compartidas]
        choices_cat = [("", "---------")] + [(c.id, str(c)) for c in cats_compartidas]

        for form in [entrada_form, salida_form, prestamo_form, traslado_form, conteo_form]:
            if "producto" in form.fields:
                form.fields["producto"].widget.choices = choices_prod
            if "almacen" in form.fields:
                form.fields["almacen"].widget.choices = choices_alm
            if "ubicacion" in form.fields:
                form.fields["ubicacion"].widget.choices = choices_ub
            if "almacen_origen" in form.fields:
                form.fields["almacen_origen"].widget.choices = choices_alm
            if "almacen_destino" in form.fields:
                form.fields["almacen_destino"].widget.choices = choices_alm
            if "ubicacion_origen" in form.fields:
                form.fields["ubicacion_origen"].widget.choices = choices_ub
            if "ubicacion_destino" in form.fields:
                form.fields["ubicacion_destino"].widget.choices = choices_ub
            if "categoria_destino" in form.fields:
                form.fields["categoria_destino"].widget.choices = choices_cat

        context["entrada_form"] = entrada_form
        context["salida_form"] = salida_form
        context["prestamo_form"] = prestamo_form
        context["traslado_form"] = traslado_form
        context["conteo_form"] = conteo_form

        # Mapa de almacén, ubicación fija y stock disponible por producto (0 consultas en bucle)
        mapa_ubic = {}
        user_alm_ids = {a.id for a in alms_compartidos} if es_normal else set()

        for prod in prods_compartidos:
            ub = prod.ubicacion
            existencias = list(prod.existencias.all())

            if es_normal:
                ex_user = next((e for e in existencias if e.almacen_id in user_alm_ids), None)
                if ex_user:
                    alm = ex_user.almacen
                elif ub and ub.almacen_id in user_alm_ids:
                    alm = ub.almacen
                else:
                    alm = prod.categoria.almacen_principal
            else:
                alm = ub.almacen if ub else prod.categoria.almacen_principal

            ex = next((e for e in existencias if alm and e.almacen_id == alm.id), None)
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

        # Ubicaciones agrupadas por almacén para el selector dinámico de traslado (0 consultas)
        alms_ubs = {}
        for u in ubs_compartidas:
            aid = str(u.almacen_id)
            if aid not in alms_ubs:
                alms_ubs[aid] = []
            alms_ubs[aid].append({"id": str(u.id), "nombre": u.nombre})
        context["almacen_ubicaciones_json"] = json.dumps(alms_ubs)
        context["almacenes_filtro"] = alms_compartidos

        # Categorías agrupadas por almacén para el selector dinámico de traslado (0 consultas)
        alms_cats = {}
        for c in cats_compartidas:
            aid = str(c.almacen_principal_id)
            if aid not in alms_cats:
                alms_cats[aid] = []
            alms_cats[aid].append({"id": str(c.id), "nombre": c.nombre})
        context["almacen_categorias_json"] = json.dumps(alms_cats)
        return context


class EntradaCrearView(LoginRequiredMixin, PermisoRequeridoMixin, View):
    """Registro operativo de entrada de mercancía (con soporte de empaques e idempotencia)."""

    permiso_requerido = "movimientos.entrada"

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
                    ubic = prod.ubicacion if (prod.ubicacion and prod.ubicacion.almacen_id == alm.id) else prod.ubicacion
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
        return render(request, "inventario/movimiento_entrada.html", {"form": form})


class SalidaCrearView(LoginRequiredMixin, PermisoRequeridoMixin, View):
    """Registro operativo de salida de stock con verificación y alerta inmediata."""

    permiso_requerido = "movimientos.dotacion"

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
        return render(request, "inventario/movimiento_salida.html", {"form": form})


class TrasladoCrearView(LoginRequiredMixin, View):
    """Registro de traslado operativo (interno o entre almacenes)."""

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            raise PermissionDenied("Debe iniciar sesión para realizar traslados.")
        if getattr(request.user, "es_usuario_normal", False):
            tiene_int = request.user.tiene_permiso("movimientos.traslado_interno")
            tiene_alm = request.user.tiene_permiso("movimientos.traslado_almacen")
            if not tiene_int and not tiene_alm:
                raise PermissionDenied("No tienes permisos para registrar traslados.")
        return super().dispatch(request, *args, **kwargs)

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
                    if getattr(request.user, "es_usuario_normal", False) and not request.user.tiene_permiso("movimientos.traslado_almacen"):
                        raise PermissionDenied("No tienes permisos para realizar traslados entre almacenes.")
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
                    if getattr(request.user, "es_usuario_normal", False) and not request.user.tiene_permiso("movimientos.traslado_interno"):
                        raise PermissionDenied("No tienes permisos para realizar traslados internos de ubicación.")
                    current_ub = prod.ubicacion if (prod.ubicacion and prod.ubicacion.almacen_id == alm_orig.id) else prod.ubicacion
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
        return render(request, "inventario/movimiento_traslado.html", {"form": form})


class MovimientoRevertirView(LoginRequiredMixin, PermisoRequeridoMixin, View):
    """Reversión de un movimiento previo con verificación estricta de no negatividad (§9.6)."""

    permiso_requerido = "movimientos.revertir"

    def dispatch(self, request, *args, **kwargs):
        movimiento = get_object_or_404(Movimiento, pk=kwargs.get("pk"))
        if not getattr(request.user, "es_administrador", False):
            if movimiento.tipo in (
                Movimiento.Tipo.AJUSTE_CONTEO,
                Movimiento.Tipo.BAJA_PRESTAMO,
                Movimiento.Tipo.TRASLADO_ENTRE,
                Movimiento.Tipo.TRASLADO_INTERNO,
            ):
                raise PermissionDenied("Solo un Administrador o Superusuario puede revertir ajustes de conteo, bajas o traslados.")
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
                _procesar_error_formulario(form, exc)
        return render(request, "inventario/movimiento_revertir.html", {"movimiento": movimiento, "form": form})
