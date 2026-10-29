"""Utilidades transversales para la aplicación inventario."""

import unicodedata


def normalizar_texto(texto: str) -> str:
    """Normaliza un texto para búsquedas deterministas insensibles a mayúsculas, minúsculas y tildes/acentos.
    
    Elimina caracteres combinantes (diacríticos) mediante descomposición NFKD y pasa a minúsculas.
    Garantiza paridad y el mismo comportamiento de búsqueda tanto en SQLite como en PostgreSQL
    sin requerir extensiones del motor de base de datos.
    
    Ejemplos:
    - "ELECTRÓNICA" -> "electronica"
    - "Cámara Réflex" -> "camara reflex"
    - "Módulo L298N" -> "modulo l298n"
    """
    if not texto:
        return ""
    nfkd = unicodedata.normalize("NFKD", str(texto))
    sin_diacriticos = "".join(c for c in nfkd if not unicodedata.combining(c))
    return sin_diacriticos.lower().strip()
