"""Serializadores y validadores para la API interna de Haku Inventario.

Valida peticiones entrantes sin requerir Django REST Framework, usando
formularios y validadores nativos de Django para mantener cero dependencias
externas innecesarias.
"""

import json
from decimal import Decimal

from django.core.exceptions import ValidationError


class ValidadorComando:
    """Valida los comandos de texto o voz enviados a Haku."""

    @staticmethod
    def validar(datos: dict) -> dict:
        mensaje = datos.get("mensaje", "").strip()
        if not mensaje:
            raise ValidationError({"mensaje": "El mensaje no puede estar vacío."})
        if len(mensaje) > 1000:
            raise ValidationError({"mensaje": "El mensaje excede los 1000 caracteres."})
        return {
            "mensaje": mensaje,
            "origen": datos.get("origen", "texto"),  # "texto" o "voz"
        }


class ValidadorConfirmacion:
    """Valida la confirmación o cancelación de una propuesta pendiente."""

    @staticmethod
    def validar(datos: dict) -> dict:
        propuesta_id = str(datos.get("propuesta_id", "")).strip()
        if not propuesta_id:
            raise ValidationError({"propuesta_id": "Debe proporcionar el ID de la propuesta."})

        idempotency_key = datos.get("idempotency_key", "").strip()
        return {
            "propuesta_id": propuesta_id,
            "idempotency_key": idempotency_key or None,
        }


class ValidadorConsultaStock:
    """Valida parámetros para consulta directa de stock."""

    @staticmethod
    def validar(datos: dict) -> dict:
        termino = datos.get("termino", "").strip()
        almacen_id = datos.get("almacen_id")
        return {
            "termino": termino,
            "almacen_id": int(almacen_id) if almacen_id else None,
        }


class ValidadorSintesisVoz:
    """Valida la petición de síntesis de voz."""

    @staticmethod
    def validar(datos: dict) -> dict:
        texto = datos.get("texto", "").strip()
        if not texto:
            raise ValidationError({"texto": "El texto para síntesis no puede estar vacío."})
        if len(texto) > 2000:
            raise ValidationError({"texto": "El texto excede el límite de 2000 caracteres."})
        return {"texto": texto}
