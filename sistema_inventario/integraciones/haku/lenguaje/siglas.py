"""Normalización de siglas técnicas, términos hablados y formatos de códigos."""

from __future__ import annotations

import re


class NormalizadorSiglasTecnicas:
    """Convierte transcripciones fonéticas de términos técnicos y códigos a su forma estándar."""

    # 1. Siglas técnicas habladas en telecomunicaciones e informática
    REGLAS_SIGLAS = [
        (r"\b(?:erre\s+jota|re\s+jota|rejota|rj)\s*(?:cuarenta\s+y\s+cinco|45)\b", "RJ45"),
        (r"\b(?:erre\s+jota|re\s+jota|rejota|rj)\s*(?:once|11)\b", "RJ11"),
        (r"\b(?:hache\s+de\s+eme\s+i|hache\s+de\s+mi)\b", "HDMI"),
        (r"\b(?:u\s+te\s+pe)\b", "UTP"),
        (r"\b(?:ef\s+te\s+pe|efe\s+te\s+pe)\b", "FTP"),
        (r"\b(?:ese\s+te\s+pe)\b", "STP"),
        (r"\b(?:ve\s+ge\s+a|uve\s+ge\s+a)\b", "VGA"),
        (r"\b(?:u\s+ese\s+be)\b", "USB"),
        (r"\b(?:cat|categor[ií]a)\s*(?:seis|6)\b", "Cat 6"),
        (r"\b(?:cat|categor[ií]a)\s*(?:cinco|5)(?:\s*(?:e|letra\s+e))?\b", "Cat 5e"),
        (r"\b(?:patch\s*cord|pach\s*cor|pascor)\b", "Patch Cord"),
        (r"\b(?:switch|suich|swich)\b", "Switch"),
        (r"\b(?:router|ruter)\b", "Router"),
        (r"\b(?:dos\s+punto\s+cero|2\s+punto\s+cero|2\s+punto\s+0)\b", "2.0"),
        (r"\b(?:uno\s+punto\s+cuatro|1\s+punto\s+cuatro|1\s+punto\s+4)\b", "1.4"),
    ]

    # 2. Deletreo de prefijos de códigos de inventario
    REGLAS_PREFIJOS = [
        (r"\b(?:ce\s+a\s+be)\b", "CAB"),
        (r"\b(?:hache\s+e\s+erre)\b", "HER"),
        (r"\b(?:erre\s+e\s+de)\b", "RED"),
    ]

    # 3. Números hablados dígito a dígito comunes en códigos (ej. "cero cero uno" -> "001")
    REGLAS_DIGITOS = [
        (r"\bcero\s+cero\s+cero\s+uno\b", "0001"),
        (r"\bcero\s+cero\s+cero\s+dos\b", "0002"),
        (r"\bcero\s+cero\s+uno\b", "001"),
        (r"\bcero\s+cero\s+dos\b", "002"),
        (r"\bcero\s+uno\b", "01"),
        (r"\bcero\s+dos\b", "02"),
    ]

    # 4. Medidas comunes en inventario
    REGLAS_MEDIDAS = [
        (r"\b(?:un|1)\s+metro\b", "1m"),
        (r"\b(?:dos|2)\s+metros\b", "2m"),
        (r"\b(?:tres|3)\s+metros\b", "3m"),
        (r"\b(?:cinco|5)\s+metros\b", "5m"),
        (r"\b(?:diez|10)\s+metros\b", "10m"),
    ]

    @classmethod
    def normalizar(cls, texto: str) -> str:
        """Aplica las transformaciones fonéticas conservando nombres y términos."""
        if not texto:
            return ""

        resultado = texto

        # Aplicar reglas de siglas
        for patron, reemplazo in cls.REGLAS_SIGLAS:
            resultado = re.sub(patron, reemplazo, resultado, flags=re.IGNORECASE)

        # Aplicar reglas de prefijos
        for patron, reemplazo in cls.REGLAS_PREFIJOS:
            resultado = re.sub(patron, reemplazo, resultado, flags=re.IGNORECASE)

        # Aplicar reglas de dígitos hablados
        for patron, reemplazo in cls.REGLAS_DIGITOS:
            resultado = re.sub(patron, reemplazo, resultado, flags=re.IGNORECASE)

        # Aplicar reglas de medidas
        for patron, reemplazo in cls.REGLAS_MEDIDAS:
            resultado = re.sub(patron, reemplazo, resultado, flags=re.IGNORECASE)

        # Normalizar guiones hablados en códigos: "CAB guion 0001" o "CAB raya 0001" -> "CAB-0001"
        resultado = re.sub(
            r"\b([A-Z]{3})\s*(?:guion|gui[oó]n|raya|menos)\s*(\d{1,4})\b",
            r"\1-\2",
            resultado,
            flags=re.IGNORECASE,
        )

        # Si quedaron prefijos pegados a números: "CAB 0001" -> "CAB-0001"
        resultado = re.sub(
            r"\b(CAB|HER|RED)\s+(\d{3,4})\b",
            r"\1-\2",
            resultado,
            flags=re.IGNORECASE,
        )

        return resultado.strip()
