"""
Helper canónico de auditoría — Inventario AudacIA.

Proporciona una interfaz compartida y coherente para registrar eventos
relevantes en todo el sistema (§10 de la especificación).
"""

from typing import Any, Dict, Optional

from django.contrib.auth import get_user_model
from django.db import transaction

from .models import Auditoria

Usuario = get_user_model()


def registrar_auditoria(
    *,
    usuario: Optional[Any] = None,
    accion: str,
    tipo_entidad: str,
    id_entidad: str,
    valores_anteriores: Optional[Dict[str, Any]] = None,
    valores_nuevos: Optional[Dict[str, Any]] = None,
    motivo: str = "",
    resultado: str = "OK",
    via_haku: bool = False,
    ip_origen: Optional[str] = None,
) -> Auditoria:
    """Crea un registro de auditoría inmutable.

    Parámetros:
    - usuario: UsuarioAudacIA responsable o None para acciones del sistema.
    - accion: Tipo de acción (ej. "CREAR", "EDITAR", "LOGIN_FALLIDO", "BACKUP").
    - tipo_entidad: Nombre de la entidad (ej. "Producto", "Usuario", "Almacen").
    - id_entidad: Identificador o código de la entidad afectada.
    - valores_anteriores: Instantánea JSON previa a la mutación.
    - valores_nuevos: Instantánea JSON posterior a la mutación.
    - motivo: Justificación u observación de la operación.
    - resultado: "OK", "ERROR", etc.
    - via_haku: True si la acción provino del asistente de voz.
    - ip_origen: Dirección IP del cliente (se adjunta al motivo de forma segura).
    """
    motivo_final = motivo
    if ip_origen:
        prefix = f"[IP: {ip_origen}] "
        if not motivo_final.startswith(prefix):
            motivo_final = f"{prefix}{motivo_final}".strip()

    # Si el usuario es anónimo o no válido, se registra como None (sistema)
    usuario_valido = usuario if (usuario and getattr(usuario, "is_authenticated", False)) else None

    with transaction.atomic():
        registro = Auditoria.objects.create(
            usuario=usuario_valido,
            accion=accion,
            tipo_entidad=tipo_entidad,
            id_entidad=str(id_entidad),
            valores_anteriores=valores_anteriores,
            valores_nuevos=valores_nuevos,
            motivo=motivo_final,
            resultado=resultado,
            via_haku=via_haku,
        )

    return registro
