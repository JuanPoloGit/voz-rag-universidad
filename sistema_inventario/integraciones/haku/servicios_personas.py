"""Servicio de resolución, normalización y validación canónica de personas y destinatarios en HAKU Inventario.

REGLAS INNEGOCIABLES:
1. Jamás registrar automáticamente nombres mal reconocidos o ambiguos por voz.
2. Si no hay certidumbre suficiente o el nombre está ausente, solicitar expresamente que el usuario lo escriba.
3. Preservar caracteres canónicos (respetar la 'ñ', tildes y mayúsculas oficiales) en el almacenamiento final.
4. Comparación tolerante (Unicode NFD, minúsculas, sin tildes para match) sin alterar el valor guardado.
5. Resolución contra personas reales: usuarios del sistema (UsuarioAudacIA) y destinatarios canónicos históricos.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

from django.core.cache import caches

logger = logging.getLogger("integraciones.haku.personas")

CACHE_ALIAS = "haku_catalogo"
KEY_CACHE_PERSONAS = "personas:canonicas:lista"
TTL_PERSONAS = 900  # 15 minutos


def normalizar_para_comparacion(texto: str) -> str:
    """Limpia y estandariza un nombre exclusivamente para fines de búsqueda y comparación.
    
    - Convierte a minúsculas.
    - Descompone caracteres Unicode (NFD) eliminando marcas de tilde diacríticas,
      PERO PRESERVA la letra 'ñ' y 'Ñ' para no confundir identidades (ej. Peña vs Pena).
    - Remueve signos de puntuación y espacios redundantes.
    """
    if not texto:
        return ""
    
    t = texto.strip().lower()
    
    # Preservar la ñ reemplazándola temporalmente si se fuera a degradar
    t = t.replace("ñ", "___enie___")
    # Descomponer y quitar marcas diacríticas (tildes)
    t = "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn")
    # Restaurar la ñ
    t = t.replace("___enie___", "ñ")
    
    # Quitar caracteres que no sean letras, espacios o números
    t = re.sub(r"[^\w\sñ]", " ", t)
    # Reducir espacios múltiples
    t = re.sub(r"\s+", " ", t).strip()
    return t


def capitalizar_nombre_propio(texto: str) -> str:
    """Formatea estéticamente un nombre limpio respetando nombres compuestos (ej: Juan de Dios, María Pérez)."""
    if not texto:
        return ""
    palabras = texto.strip().split()
    particulas_menores = {"de", "del", "la", "las", "los", "y", "e"}
    res = []
    for i, p in enumerate(palabras):
        p_lower = p.lower()
        if i > 0 and p_lower in particulas_menores:
            res.append(p_lower)
        else:
            res.append(p.capitalize())
    return " ".join(res)


class HakuPersonasService:
    """Servicio para resolución y validación de personas destinatarias."""

    @classmethod
    def obtener_personas_canonicas(cls) -> List[Dict[str, Any]]:
        """Obtiene la lista consolidada de destinatarios canónicos conocidos en el sistema.
        
        Fuentes:
        1. Usuarios del sistema (UsuarioAudacIA activos).
        2. Destinatarios registrados previamente en préstamos cerrados o activos.
        """
        try:
            cache = caches[CACHE_ALIAS]
            cached = cache.get(KEY_CACHE_PERSONAS)
            if cached is not None:
                return cached
        except Exception:
            cache = None

        personas_map: Dict[str, Dict[str, Any]] = {}

        # 1. Usuarios del sistema
        try:
            from cuentas.models import UsuarioAudacIA
            for u in UsuarioAudacIA.objects.filter(is_active=True, eliminado_logicamente=False):
                nombre_oficial = getattr(u, "nombre_completo", "") or u.get_full_name() or u.username
                norm = normalizar_para_comparacion(nombre_oficial)
                if norm and norm not in personas_map:
                    personas_map[norm] = {
                        "nombre_canonico": nombre_oficial.strip(),
                        "norm": norm,
                        "tipo": "USUARIO_SISTEMA",
                        "username": u.username,
                    }
        except Exception as exc:
            logger.warning("Error leyendo usuarios para personas canónicas: %s", exc)

        # 2. Préstamos históricos
        try:
            from inventario.models import Prestamo
            destinatarios_db = (
                Prestamo.objects.exclude(destinatario__isnull=True)
                .exclude(destinatario="")
                .values_list("destinatario", flat=True)
                .distinct()[:200]
            )
            for dest in destinatarios_db:
                norm = normalizar_para_comparacion(dest)
                if norm and norm not in personas_map:
                    personas_map[norm] = {
                        "nombre_canonico": capitalizar_nombre_propio(dest),
                        "norm": norm,
                        "tipo": "DESTINATARIO_HISTORICO",
                    }
        except Exception as exc:
            logger.warning("Error leyendo destinatarios históricos: %s", exc)

        lista = list(personas_map.values())
        if cache:
            try:
                cache.set(KEY_CACHE_PERSONAS, lista, timeout=TTL_PERSONAS)
            except Exception:
                pass
        return lista

    @classmethod
    def invalidar_cache_personas(cls) -> None:
        try:
            cache = caches[CACHE_ALIAS]
            cache.delete(KEY_CACHE_PERSONAS)
        except Exception:
            pass

    @classmethod
    def resolver_destinatario(
        cls,
        texto_destinatario: str,
        origen_voz: bool = False
    ) -> Tuple[Optional[str], float, List[str], bool]:
        """Resuelve un destinatario a partir del texto ingresado o transcrito.
        
        Retorna:
            (nombre_canonico_elegido, confianza, lista_candidatos_similares, requiere_escritura)
            
        Reglas:
        - Si texto_destinatario está vacío: requiere_escritura = True.
        - Coincidencia exacta normalizada: certidumbre 1.0 -> nombre canónico.
        - Coincidencia aproximada en origen de voz:
            - Si hay múltiples candidatos cercanos: requiere_escritura = True (evita sustitución errónea).
            - Si la similitud es baja (< 0.85): requiere_escritura = True.
            - Si similitud >= 0.88 y un único candidato claro: se sugiere el canónico.
        - Si no coincide con nadie en catálogo pero fue escrito por texto: se valida longitud y limpieza.
        """
        if not texto_destinatario or not texto_destinatario.strip():
            return (None, 0.0, [], True)

        norm_in = normalizar_para_comparacion(texto_destinatario)
        if len(norm_in) < 2:
            return (None, 0.0, [], True)

        personas = cls.obtener_personas_canonicas()

        # 1. Coincidencia exacta completa
        for p in personas:
            if p["norm"] == norm_in:
                return (p["nombre_canonico"], 1.0, [p["nombre_canonico"]], False)

        # 2. Coincidencia por tokens/palabras exactas (ej: "Carlos" con "Carlos Andres Rodriguez")
        palabras_in = set(norm_in.split())
        candidatos_sub: List[Dict[str, Any]] = []
        for p in personas:
            palabras_p = set(p["norm"].split())
            if palabras_in.issubset(palabras_p) or palabras_p.issubset(palabras_in):
                candidatos_sub.append(p)

        if len(candidatos_sub) == 1:
            return (candidatos_sub[0]["nombre_canonico"], 0.95, [candidatos_sub[0]["nombre_canonico"]], False)
        elif len(candidatos_sub) > 1:
            # Varios candidatos (ej: Carlos Gomez vs Carlos Rodriguez)
            nombres = [c["nombre_canonico"] for c in candidatos_sub[:5]]
            return (None, 0.5, nombres, True)

        # 3. Comparación difusa (similitud fonética / de cadena)
        mejores: List[Tuple[float, Dict[str, Any]]] = []
        for p in personas:
            ratio = SequenceMatcher(None, norm_in, p["norm"]).ratio()
            # También evaluar ratio con la primera palabra si es solo nombre
            primera_palabra = p["norm"].split()[0] if p["norm"] else ""
            ratio_primera = SequenceMatcher(None, norm_in, primera_palabra).ratio() if primera_palabra else 0.0
            max_ratio = max(ratio, ratio_primera)
            if max_ratio >= 0.70:
                mejores.append((max_ratio, p))

        mejores.sort(key=lambda x: x[0], reverse=True)

        if mejores:
            top_score, top_p = mejores[0]
            # Si hay ambigüedad (dos con score muy cercano)
            if len(mejores) > 1 and (mejores[0][0] - mejores[1][0] < 0.10):
                candidatos_ambiguos = [m[1]["nombre_canonico"] for m in mejores[:4]]
                return (None, top_score, candidatos_ambiguos, True)

            # Si es por voz y el score no es contundente (>= 0.88), no arriesgar
            if origen_voz:
                if top_score >= 0.88:
                    return (top_p["nombre_canonico"], top_score, [top_p["nombre_canonico"]], False)
                else:
                    return (None, top_score, [top_p["nombre_canonico"]], True)
            else:
                # Si fue escrito por texto
                if top_score >= 0.80:
                    return (top_p["nombre_canonico"], top_score, [top_p["nombre_canonico"]], False)

        # 4. No coincide con nadie registrado
        # Si vino por voz y no coincide con nadie conocido, es peligroso aceptarlo a ciegas
        if origen_voz:
            return (None, 0.0, [], True)

        # Si vino por teclado y tiene un formato razonable de nombre (al menos 3 letras)
        nombre_limpio = capitalizar_nombre_propio(texto_destinatario)
        return (nombre_limpio, 0.75, [], False)
