"""Vistas de administración de respaldos y copias de seguridad de la base de datos."""

import os
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import FileResponse, Http404
from django.shortcuts import redirect, render
from django.views.generic import View

from cuentas.permisos import SuperusuarioRequeridoMixin
from inventario.auditoria import registrar_auditoria
from inventario.servicios.respaldo import (
    eliminar_respaldo_bd,
    generar_respaldo_bd,
    listar_respaldos,
    obtener_estadisticas_respaldos,
    obtener_ruta_respaldo_segura,
)
from inventario.views._comun import _obtener_ip


class RespaldoListaView(LoginRequiredMixin, SuperusuarioRequeridoMixin, View):
    """Listado y panel central de gestión de respaldos del sistema (§5)."""

    def get(self, request):
        respaldos = listar_respaldos()
        estadisticas = obtener_estadisticas_respaldos()
        return render(
            request,
            "inventario/respaldo_lista.html",
            {
                "respaldos": respaldos,
                "estadisticas": estadisticas,
            },
        )


class RespaldoCrearView(LoginRequiredMixin, SuperusuarioRequeridoMixin, View):
    """Generación manual inmediata de un nuevo respaldo con compresión y SHA-256."""

    def post(self, request):
        try:
            resultado = generar_respaldo_bd(
                usuario=request.user,
                force=True,
                motivo="Respaldo manual solicitado desde el panel web por Superadministrador",
            )
            messages.success(
                request,
                f"Respaldo generado con éxito: '{resultado['archivo']}' ({resultado['tamano_formateado']}). Integridad SHA-256 verificada.",
            )
        except Exception as exc:
            messages.error(request, f"Error al generar respaldo: {exc}")
        return redirect("inventario:respaldo-lista")


class RespaldoDescargarView(LoginRequiredMixin, SuperusuarioRequeridoMixin, View):
    """Descarga segura de archivo de respaldo o checksum SHA-256."""

    def get(self, request, nombre_archivo):
        try:
            ruta_segura = obtener_ruta_respaldo_segura(nombre_archivo)
        except (ValueError, FileNotFoundError):
            raise Http404("El archivo de respaldo solicitado no existe o no es accesible.")

        try:
            registrar_auditoria(
                usuario=request.user,
                accion="DESCARGA_BACKUP",
                tipo_entidad="Sistema",
                id_entidad="backup",
                valores_nuevos={"archivo": os.path.basename(ruta_segura)},
                motivo=f"Descarga de archivo de respaldo: {nombre_archivo}",
                ip_origen=_obtener_ip(request),
            )
        except Exception:
            pass

        content_type = "application/gzip" if ruta_segura.endswith(".gz") else "text/plain"
        return FileResponse(open(ruta_segura, "rb"), as_attachment=True, filename=nombre_archivo, content_type=content_type)


class RespaldoEliminarView(LoginRequiredMixin, SuperusuarioRequeridoMixin, View):
    """Eliminación permanente de una copia de seguridad para liberar espacio."""

    def post(self, request, nombre_archivo):
        try:
            eliminar_respaldo_bd(nombre_archivo, usuario=request.user)
            messages.success(request, f"La copia de seguridad '{nombre_archivo}' fue eliminada permanentemente del almacenamiento.")
        except Exception as exc:
            messages.error(request, f"No se pudo eliminar el archivo de respaldo: {exc}")
        return redirect("inventario:respaldo-lista")
