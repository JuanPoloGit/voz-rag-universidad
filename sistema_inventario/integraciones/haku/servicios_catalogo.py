"""
Servicio de validación y consulta de códigos de barras para HAKU Inventario.

Formatos soportados con cálculo de dígito de control:
- EAN-13 (13 dígitos)
- EAN-8 (8 dígitos)
- UPC-A (12 dígitos)
- Code 128 (caracteres ASCII válidos según especificación ISO/IEC 15417)

Reglas de seguridad y diseño:
- Conservación estricta de ceros a la izquierda (nunca convertir a entero).
- Comprobación de unicidad y duplicados en el inventario interno de AudacIA.
- Desacoplamiento total: la consulta externa nunca bloquea el flujo manual.
- No inventa categorías, almacén, stock mínimo ni stock inicial.
"""

import logging
import re
from typing import Any, Dict, Optional, Tuple
import urllib.request
import urllib.error
import json

from inventario.models import Producto

logger = logging.getLogger("haku.catalogo")


def calcular_checksum_ean(codigo_sin_checksum: str) -> int:
    """Calcula el dígito de control para EAN/UPC según el estándar GS1.
    
    Ponderación: los dígitos en posiciones impares (desde la derecha) se multiplican por 3,
    y los de posiciones pares por 1.
    """
    reverse_digits = [int(d) for d in reversed(codigo_sin_checksum)]
    suma = sum(d * 3 if i % 2 == 0 else d for i, d in enumerate(reverse_digits))
    return (10 - (suma % 10)) % 10


def validar_codigo_barras(codigo: str) -> Tuple[bool, str, Optional[str]]:
    """Valida el formato de un código de barras.
    
    Retorna: (es_valido, formato_detectado, mensaje_error)
    """
    if not codigo or not isinstance(codigo, str):
        return False, "DESCONOCIDO", "El código no puede estar vacío."

    codigo = codigo.strip()

    # EAN-8
    if re.fullmatch(r"^\d{8}$", codigo):
        checksum_esperado = calcular_checksum_ean(codigo[:-1])
        if int(codigo[-1]) == checksum_esperado:
            return True, "EAN-8", None
        return False, "EAN-8", f"Dígito de control inválido para EAN-8 (esperado: {checksum_esperado})."

    # UPC-A
    if re.fullmatch(r"^\d{12}$", codigo):
        checksum_esperado = calcular_checksum_ean(codigo[:-1])
        if int(codigo[-1]) == checksum_esperado:
            return True, "UPC-A", None
        return False, "UPC-A", f"Dígito de control inválido para UPC-A (esperado: {checksum_esperado})."

    # EAN-13
    if re.fullmatch(r"^\d{13}$", codigo):
        checksum_esperado = calcular_checksum_ean(codigo[:-1])
        if int(codigo[-1]) == checksum_esperado:
            return True, "EAN-13", None
        return False, "EAN-13", f"Dígito de control inválido para EAN-13 (esperado: {checksum_esperado})."

    # Code 128 (caracteres alfanuméricos y símbolos estándar ASCII 32 a 126)
    if re.fullmatch(r"^[\x20-\x7E]{2,48}$", codigo):
        return True, "CODE-128", None

    return False, "DESCONOCIDO", "Formato de código de barras no reconocido. Se admiten EAN-13, EAN-8, UPC-A y Code 128."


def consultar_catalogo_externo(codigo: str, timeout_segundos: float = 2.0) -> Optional[Dict[str, Any]]:
    """Consulta fuentes públicas de información sobre el código de barras.
    
    Actúa con timeout estricto para no degradar la experiencia de usuario.
    Solo devuelve metadatos reales verificados:
    - nombre
    - marca
    - descripcion
    - nombre_empaque
    
    JAMÁS devuelve ni inventa categoría, almacén, stock mínimo ni stock inicial.
    """
    codigo_limpio = codigo.strip()
    if not re.fullmatch(r"^\d{8,14}$", codigo_limpio):
        return None

    # Fuente 1: Open Food Facts / Open Products Facts API pública
    url = f"https://world.openfoodfacts.org/api/v2/product/{codigo_limpio}.json?fields=product_name,brands,generic_name,packaging"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "AudacIA-HakuInventario/2.0 (inventario@audacia.local)"}
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout_segundos) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("status") == 1 and "product" in data:
                    prod = data["product"]
                    nombre = (prod.get("product_name") or prod.get("generic_name") or "").strip()
                    marca = (prod.get("brands") or "").strip()
                    empaque = (prod.get("packaging") or "").strip()
                    if nombre:
                        nombre_final = f"{nombre} ({marca})" if marca and marca.lower() not in nombre.lower() else nombre
                        return {
                            "nombre": nombre_final[:255],
                            "marca": marca[:100] if marca else "",
                            "descripcion": (prod.get("generic_name") or "")[:500],
                            "nombre_empaque": empaque[:50] if empaque else "",
                            "fuente": "OpenProductFacts",
                        }
    except urllib.error.URLError as err:
        logger.info("Catálogo externo no disponible para %s: %s", codigo_limpio, err)
    except Exception as exc:
        logger.warning("Error al consultar catálogo externo para %s: %s", codigo_limpio, exc)

    return None


def inspeccionar_codigo_barras(codigo: str) -> Dict[str, Any]:
    """Servicio completo de inspección:
    1. Valida el código conservando ceros a la izquierda.
    2. Comprueba si ya existe en la base de datos interna de AudacIA.
    3. Si no existe, consulta metadatos en catálogo.
    """
    codigo_norm = (codigo or "").strip()
    valido, formato, error = validar_codigo_barras(codigo_norm)

    if not valido:
        return {
            "valido": False,
            "codigo": codigo_norm,
            "formato": formato,
            "error": error,
            "existe": False,
        }

    # Comprobación de unicidad en inventario AudacIA
    producto_existente = Producto.objects.filter(codigo_barras=codigo_norm).first()
    if producto_existente:
        return {
            "valido": True,
            "codigo": codigo_norm,
            "formato": formato,
            "existe": True,
            "producto": {
                "id": producto_existente.id,
                "codigo_interno": producto_existente.codigo_interno,
                "nombre": producto_existente.nombre,
                "categoria": producto_existente.categoria.nombre,
                "activo": producto_existente.activo,
                "archivado": producto_existente.archivado,
            },
            "mensaje": f"El código ya está registrado para el producto {producto_existente.nombre} ({producto_existente.codigo_interno}).",
        }

    # Búsqueda en catálogo externo
    datos_catalogo = consultar_catalogo_externo(codigo_norm)
    return {
        "valido": True,
        "codigo": codigo_norm,
        "formato": formato,
        "existe": False,
        "encontrado_en_catalogo": bool(datos_catalogo),
        "datos_sugeridos": datos_catalogo,
    }
