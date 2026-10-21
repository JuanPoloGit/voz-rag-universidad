"""Normalizador lingüístico principal y generador de intenciones para Haku Inventario.

REGLAS INNEGOCIABLES:
1. Nunca ejecuta movimientos ni altera la base de datos: solo produce intenciones estructuradas.
2. Ante ambigüedad ("pásame ese cable", "esa vaina") solicita aclaración sin inventar productos.
3. No asume equivalencias de códigos (CAB-001 vs CAB-0001): busca coincidencias y solicita aclaración si hay más de una.
4. Contexto conversacional efímero (máximo 30 min, usuario autenticado, sin persistencia permanente).
5. Separación dialectal: el dialecto costeño pertenece únicamente a la entrada; todos los mensajes generados hacia el usuario son 100% formales, neutrales y empresariales.
"""

from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Dict, List, Optional, Set


def _quitar_tildes(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")

from django.core.exceptions import PermissionDenied
from django.db.models import Q

from cuentas.models import UsuarioAudacIA
from inventario.models import Almacen, Categoria, Existencia, Prestamo, Producto

from ..cache import HakuCacheManager
from .costeno import InterpretadorCosteno, sanitizar_demostrativo_salida
from .siglas import NormalizadorSiglasTecnicas


class AlcanceTipo(str, Enum):
    """Alcance o cardinalidad de la consulta en el inventario."""
    GLOBAL = "GLOBAL"
    ALMACEN = "ALMACEN"
    COLECCION = "COLECCION"
    INDIVIDUAL = "INDIVIDUAL"


class CondicionTipo(str, Enum):
    """Filtro o propiedad de interés sobre el stock de inventario."""
    NINGUNA = "NINGUNA"
    DISPONIBLE = "DISPONIBLE"
    AGOTADO = "AGOTADO"
    BAJO_STOCK = "BAJO_STOCK"
    EN_PRESTAMO = "EN_PRESTAMO"
    ALERTAS = "ALERTAS"


class ConceptoTipo(str, Enum):
    """Concepto u operación objetivo de la interacción."""
    STOCK = "STOCK"
    UBICACION = "UBICACION"
    PRESTAMO = "PRESTAMO"
    DEVOLUCION = "DEVOLUCION"
    MOVIMIENTO = "MOVIMIENTO"
    RESUMEN_GENERAL = "RESUMEN_GENERAL"
    CATALOGO = "CATALOGO"
    ALMACENES = "ALMACENES"
    DESCONOCIDO = "DESCONOCIDO"


class ModalidadTipo(str, Enum):
    """Modalidad de la expresión del operario."""
    CONSULTA = "CONSULTA"
    MUTACION = "MUTACION"
    CONFIRMAR = "CONFIRMAR"
    CANCELAR = "CANCELAR"
    SALUDO = "SALUDO"
    AGRADECIMIENTO = "AGRADECIMIENTO"
    DESCONOCIDO = "DESCONOCIDO"


@dataclass(frozen=True)
class SemanticFrame:
    """Marco semántico estructurado: separa modalidad, alcance, concepto y condición."""
    modalidad: ModalidadTipo = ModalidadTipo.CONSULTA
    alcance: AlcanceTipo = AlcanceTipo.INDIVIDUAL
    concepto: ConceptoTipo = ConceptoTipo.STOCK
    condicion: CondicionTipo = CondicionTipo.NINGUNA
    almacen_especifico: Optional[Almacen] = None
    filtro_coleccion: Optional[str] = None
    producto_objetivo: Optional[Producto] = None
    destinatario: Optional[str] = None
    cantidad: Optional[int] = None
    motivo: Optional[str] = None


class IntencionTipo(str, Enum):
    """Tipos de intención semántica reconocidos por Haku Inventario."""
    BUSCAR_PRODUCTO = "BUSCAR_PRODUCTO"
    CONSULTAR_STOCK = "CONSULTAR_STOCK"
    CONSULTAR_UBICACION = "CONSULTAR_UBICACION"
    CONSULTAR_UBICACION_COLECCION = "CONSULTAR_UBICACION_COLECCION"
    ALERTAS_STOCK = "ALERTAS_STOCK"
    PRODUCTOS_SIN_STOCK = "PRODUCTOS_SIN_STOCK"
    PRODUCTOS_BAJO_STOCK = "PRODUCTOS_BAJO_STOCK"
    CONSULTAR_FALTANTES_REPOSICION = "CONSULTAR_FALTANTES_REPOSICION"
    LISTAR_CATALOGO = "LISTAR_CATALOGO"
    PRODUCTOS_CON_STOCK = "PRODUCTOS_CON_STOCK"
    LISTAR_ALMACENES = "LISTAR_ALMACENES"
    PRESTAMOS_ACTIVOS = "PRESTAMOS_ACTIVOS"
    REGISTRAR_SALIDA = "REGISTRAR_SALIDA"
    REGISTRAR_ENTRADA = "REGISTRAR_ENTRADA"
    PRESTAMO = "PRESTAMO"
    DEVOLUCION = "DEVOLUCION"
    TRASLADAR_PRODUCTO = "TRASLADAR_PRODUCTO"
    CONSULTAR_METRICAS_MOVIMIENTOS = "CONSULTAR_METRICAS_MOVIMIENTOS"
    CONSULTAR_RESUMEN_INVENTARIO = "CONSULTAR_RESUMEN_INVENTARIO"
    CONSULTAR_PRODUCTO_MOVIMIENTOS = "CONSULTAR_PRODUCTO_MOVIMIENTOS"
    CREAR_PRODUCTO = "CREAR_PRODUCTO"
    EDITAR_PRODUCTO = "EDITAR_PRODUCTO"
    INHABILITAR_PRODUCTO = "INHABILITAR_PRODUCTO"
    HABILITAR_PRODUCTO = "HABILITAR_PRODUCTO"
    CREAR_USUARIO = "CREAR_USUARIO"
    EDITAR_USUARIO = "EDITAR_USUARIO"
    SOLICITAR_ACLARACION = "SOLICITAR_ACLARACION"
    SALUDO = "SALUDO"
    AGRADECIMIENTO = "AGRADECIMIENTO"
    ACCION_CONFIRMAR = "ACCION_CONFIRMAR"
    ACCION_CANCELAR = "ACCION_CANCELAR"
    SOLICITUD_NO_COMPRENDIDA = "SOLICITUD_NO_COMPRENDIDA"
    NO_INTENCION_INVENTARIO = "NO_INTENCION_INVENTARIO"
    CONVERSACION_GENERAL = "CONVERSACION_GENERAL"  # Deprecada: mantener por retrocompatibilidad


@dataclass(frozen=True)
class IntencionResultado:
    """Intención procesada lista para el motor de confirmación o consulta de Haku."""
    tipo: IntencionTipo
    producto: Optional[Producto] = None
    codigo_producto: Optional[str] = None
    cantidad: Optional[int] = None
    destinatario: Optional[str] = None
    motivo: Optional[str] = None
    mensaje_aclaracion: Optional[str] = None
    almacen: Optional[Almacen] = None
    requiere_confirmacion: bool = False
    candidatos: Optional[List[Dict[str, Any]]] = None
    datos_extra: dict[str, Any] = None
    semantic_frame: Optional[SemanticFrame] = None


@dataclass
class RanurasSemanticas:
    """Ranuras semánticas extraídas determinísticamente del texto del operario."""
    acto_de_habla: Optional[str] = None  # "SALUDO", "AGRADECIMIENTO", "CONFIRMAR", "CANCELAR"
    es_afirmacion_pura: bool = False
    es_negacion_pura: bool = False
    es_cambio_de_tema: bool = False
    conceptos_operativos: Set[str] = field(default_factory=set)
    accion: Optional[str] = None
    ventana_temporal: Optional[str] = None
    contexto_fisico: Optional[str] = None
    alcance: str = "INDIVIDUAL"  # "INDIVIDUAL", "COLECCION", "GLOBAL", "ALMACEN"
    condicion: str = "NINGUNA"  # "NINGUNA", "DISPONIBLE", "AGOTADO", "BAJO_STOCK", "EN_PRESTAMO", "ALERTAS"
    metrica_prestamo: str = "GENERAL"  # "GENERAL", "CONTEO_REGISTROS", "CONTEO_UNIDADES", "LISTA_PRODUCTOS", "LISTA_RESPONSABLES"
    destinatario: Optional[str] = None
    motivo: Optional[str] = None
    almacen_especifico: Optional[Almacen] = None
    filtro_coleccion: Optional[str] = None
    semantic_frame: Optional[SemanticFrame] = None


class NormalizadorLenguajeHaku:
    """Analizador lingüístico determinista y sin efectos secundarios en la base de datos."""

    TIMEOUT_CONTEXTO_SEGUNDOS = 1800  # 30 minutos de inactividad conversacional

    @classmethod
    def _resolver_almacen_en_texto(cls, texto: str) -> Optional[Almacen]:
        """Detecta determinísticamente si el texto menciona un almacén o bodega específico por nombre.
        
        Evita falsos positivos con frases genéricas como 'en el almacén' o 'qué almacenes tenemos'.
        """
        texto_norm = _quitar_tildes(texto.lower().strip())
        qs_almacenes = Almacen.objects.filter(activo=True, eliminado=False)
        lista_almacenes = sorted(qs_almacenes, key=lambda a: len(a.nombre), reverse=True)

        for alm in lista_almacenes:
            alm_nom = _quitar_tildes(alm.nombre.lower().strip())
            if re.search(r"\b" + re.escape(alm_nom) + r"\b", texto_norm):
                return alm
            partes = [p for p in alm_nom.split() if p not in ("almacen", "bodega", "de", "el", "la", "los", "las")]
            if partes:
                calificador = " ".join(partes)
                if len(calificador) >= 2 and re.search(r"\b(?:almac[eé]n|bodega)\s+" + re.escape(calificador) + r"\b", texto_norm):
                    return alm
                if len(calificador) >= 4 and re.search(r"\b(?:en|para|de)\s+(?:el\s+|la\s+)?(?:" + re.escape(calificador) + r")\b", texto_norm):
                    if calificador not in cls.PALABRAS_PARADA:
                        return alm
        return None

    @classmethod
    def _detectar_filtro_coleccion(cls, texto: str) -> Optional[str]:
        """Detecta si la consulta se refiere a una colección, familia o categoría de productos.
        
        Retorna la raíz/nombre de la familia (ej. 'cable', 'herramienta') o None.
        Solo aplica si la estructura lingüística es una consulta plural o explícita de colección:
        'qué cables...', 'cuáles herramientas...', 'muéstrame los cables...', 'qué hay de cables...', etc.
        """
        texto_norm = _quitar_tildes(texto.lower().strip())

        # Comandos de acción operativa nunca son consultas de colección
        if re.search(
            r"\b(sacar?|s[aá]came|s[aá]calo|saca|meter?|m[eé]tame|m[eé]telo|mete|prestar?|pr[eé]stame|pr[eé]stalo|presta|despachar?|desp[aá]chame|despacha|devolver?|devu[eé]lveme|devuelve|regresar?|regreso|regresa|traje|traer|trae)\b",
            texto_norm,
        ):
            return None

        patrones = [
            r"\b(?:qu[eé]|cu[aá]les)\s+(?:productos\s+(?:de\s+tipo\s+|de\s+la\s+categor[ií]a\s+)?|art[ií]culos\s+(?:de\s+tipo\s+|de\s+la\s+categor[ií]a\s+)?|tipos\s+de\s+)?([a-z]{3,})\b",
            r"\bmu[eé]strame\s+(?:los|las|el\s+grupo\s+de|la\s+categor[ií]a\s+de)\s+([a-z]{3,})\b",
            r"\blistar?\s+(?:los|las|categor[ií]a\s+de\s+)?([a-z]{3,})\b",
            r"\bqu[eé]\s+hay\s+de\s+([a-z]{3,})\b",
            r"\b(?:hay|tenemos)\s+(?:algunos?|algunas?)\s+([a-z]{3,})\b",
            r"\bcategor[ií]a\s+(?:de\s+)?([a-z]{3,})\b",
            r"\bfamilia\s+(?:de\s+)?([a-z]{3,})\b",
        ]

        palabras_genericas = {
            "producto", "productos", "articulo", "articulos", "item", "items",
            "referencia", "referencias", "cosa", "cosas", "vaina", "vainas",
            "almacen", "almacenes", "bodega", "bodegas", "inventario",
            "stock", "existencia", "existencias", "movimiento", "movimientos",
            "prestamo", "prestamos", "alerta", "alertas", "novedad", "novedades",
            "disponible", "disponibles", "agotado", "agotados", "vacio", "pelado",
            "hoy", "ayer", "mañana", "aqui", "alli", "alla",
            "tipo", "tipos", "clase", "clases", "grupo", "grupos",
            "musica", "noticia", "noticias", "chiste", "chistes", "partido", "partidos",
            "pon", "mete", "saca", "dame", "pasa", "trae", "lleva", "haz",
            "tanto", "saldo", "saldos", "unidad", "unidades", "menos", "mas",
        }

        for pat in patrones:
            m = re.search(pat, texto_norm)
            if m:
                cand = m.group(1).lower().strip()
                if cand in palabras_genericas or cand in cls.PALABRAS_PARADA:
                    continue

                stems = [cand]
                if cand.endswith("es") and len(cand) > 4:
                    stems.append(cand[:-2])
                elif cand.endswith("s") and len(cand) > 3:
                    stems.append(cand[:-1])

                for stem in stems:
                    if stem in palabras_genericas or stem in cls.PALABRAS_PARADA:
                        continue
                    if Categoria.objects.filter(nombre__icontains=stem, activo=True, eliminado=False).exists():
                        return stem
                    qs_prods = Producto.objects.filter(nombre__icontains=stem, activo=True, archivado=False)
                    if qs_prods.count() >= 1:
                        return stem

        return None

    @classmethod
    def _extraer_ranuras_semanticas(
        cls,
        texto_trabajo: str,
        texto_siglas: str,
        analisis_costeno: Any,
    ) -> RanurasSemanticas:
        """Extrae ranuras semánticas (slots) determinísticamente a partir del texto preprocesado."""
        ranuras = RanurasSemanticas()

        match_dest = re.search(r"\b(?:de|por|a|para|pa)\s+([A-Za-zÁ-ÿ\s]+?)(?:\s*$)", texto_siglas, re.IGNORECASE)
        if match_dest:
            d = match_dest.group(1).strip()
            # Limpiar posible sufijo de almacén (ej: "Carlos Andrés Rodríguez en el Almacén Principal")
            d = re.sub(r"\b(?:en|desde|hacia)\s+(?:el\s+|la\s+)?(?:almac[eé]n|bodega)\b.*$", "", d, flags=re.IGNORECASE).strip()
            d = re.sub(r"\b(pr[eé]stamo|pr[eé]stamos|de|el|la|los|las|inventario|almac[eé]n|bodega)\b", "", d, flags=re.IGNORECASE).strip()
            if d:
                ranuras.destinatario = d

        texto_limpio = re.sub(r"[¿?¡!.,:;()\[\]\"'`]", " ", texto_trabajo).strip()

        # 0. Detección de marcadores discursivos de cambio de tema (Topic Shift)
        if re.search(
            r"\b(cambiando\s+de\s+tema|hablando\s+de\s+otra\s+cosa|en\s+otro\s+orden|punto\s+y\s+aparte|aparte\s+de\s+eso|por\s+cierto|otro\s+tema)\b",
            texto_limpio,
        ):
            ranuras.es_cambio_de_tema = True

        # 1. Actos de habla & Afirmación / Negación Pura
        patron_confirmar_token = r"(s[ií]|correcto|adelante|confirma|confirmar|confirmo|dale|listo|h[aá]gale|hazlo|de\s+acuerdo|afirmativo|proceder|ejecutar|ok)"
        patron_cancelar_token = r"(no|cancelar|cancela|cancela\s+eso|cancelar\s+eso|anular|anula\s+eso|mejor\s+no|no\s+lo\s+hagas|olv[ií]dalo|descartar|abortar|d[eé]jalo\s+as[ií]|det[eé]n|parar)"

        # Texto enfocado en el acto sin partículas de cortesía ni nombres del asistente
        texto_acto = re.sub(r"\b(haku|por\s+favor|oye)\b", "", texto_limpio).strip()
        texto_acto = re.sub(r"\s+", " ", texto_acto)

        if re.fullmatch(rf"^(?:{patron_confirmar_token}\s*)+$", texto_acto):
            ranuras.es_afirmacion_pura = True
            ranuras.acto_de_habla = "CONFIRMAR"
        elif re.fullmatch(rf"^(?:{patron_cancelar_token}\s*)+$", texto_acto):
            ranuras.es_negacion_pura = True
            ranuras.acto_de_habla = "CANCELAR"
        else:
            if re.search(r"\b(hola(\s+haku)?|buenos\s+d[ií]as|buenas\s+tardes|buenas\s+noches|buen\s+d[ií]a|buenas(\s+buenas)?|saludos|qu[eé]\s+hubo|quihubo)\b", texto_limpio):
                ranuras.acto_de_habla = "SALUDO"
            elif re.search(r"\b(muchas\s+gracias|gracias(\s+haku)?|te\s+agradezco|agradecido|muy\s+amable)\b", texto_limpio):
                ranuras.acto_de_habla = "AGRADECIMIENTO"
            elif re.search(rf"\b{patron_confirmar_token}\b", texto_limpio):
                ranuras.acto_de_habla = "CONFIRMAR"
            elif re.search(rf"\b{patron_cancelar_token}\b", texto_limpio):
                ranuras.acto_de_habla = "CANCELAR"

        # 2. Ventana Temporal
        from ..servicios_temporales import resolver_intervalo_temporal
        intervalo_temp = resolver_intervalo_temporal(texto_limpio)
        if intervalo_temp:
            ranuras.ventana_temporal = intervalo_temp.etiqueta
        elif re.search(r"\b(hoy|d[ií]a|jornada|turno|fecha|hasta\s+ahora|esta\s+mañana|esta\s+tarde|este\s+turno)\b", texto_limpio):
            ranuras.ventana_temporal = "HOY"

        # 3. Contexto Físico de Ubicación Interna
        match_contexto = re.search(
            r"\b(estante|estantes|rack|racks|pasillo|pasillos|gaveta|gavetas|caj[oó]n|cajones|repisa|repisas|secci[oó]n|secciones|posici[oó]n|posiciones|ubicaci[oó]n|ubicaciones|localizaci[oó]n|lugar|lugares)\b",
            texto_limpio,
        )
        if match_contexto:
            ranuras.contexto_fisico = match_contexto.group(1)

        # 4. Almacén Específico y Filtro de Colección
        ranuras.almacen_especifico = cls._resolver_almacen_en_texto(texto_siglas)
        ranuras.filtro_coleccion = cls._detectar_filtro_coleccion(texto_limpio)

        # 5. Detección de Alcance Semántico (Scope)
        es_alcance_coleccion = bool(
            ranuras.filtro_coleccion is not None
            or re.search(
                r"\b(cada\s+(?:producto|referencia|art[ií]culo|uno)|cada\s+uno|todos\s+(?:ellos|los\s+productos|las\s+referencias|los\s+art[ií]culos|los\s+que\s+acabas|esos\s+productos|esos\s+art[ií]culos|los\s+de\s+la\s+lista)|esas\s+referencias|esos\s+art[ií]culos|los\s+art[ií]culos|las\s+referencias|los\s+tres|de\s+los\s+tres|todos\s+ellos|de\s+todos\s+ellos|de\s+todos|en\s+qu[eé]\s+estantes\s+se\s+encuentran|ub[ií]came\s+todos|de\s+cada\s+uno)\b",
                texto_limpio,
            )
            or re.search(
                r"\b(?:las\s+referencias|los\s+art[ií]culos|los\s+productos|lo)\s+que\s+(?:acabas\s+de\s+|dijiste\s+de\s+|listaste\s+|mencionaste\s+)?(?:listar|mencionar|decir|nombrar)\b",
                texto_limpio,
            )
            or re.search(
                r"\b(?:esos|esas)\s+(?:art[ií]culos|referencias|productos)\b",
                texto_limpio,
            )
            or re.search(
                r"\bcu[aá]les\s+de\s+(?:esos|esas|ellos|los\s+productos|las\s+referencias)\b",
                texto_limpio,
            )
        )

        # Detección de préstamos globales y métricas
        es_metrica_prestamo_registros = bool(
            re.search(r"\b(cu[aá]ntos\s+pr[eé]stamos|n[uú]mero\s+de\s+pr[eé]stamos|cantidad\s+de\s+pr[eé]stamos|total\s+de\s+pr[eé]stamos)\b", texto_limpio)
        )
        es_metrica_prestamo_unidades = bool(
            re.search(r"\b(cu[aá]ntas\s+unidades\s+(?:hay\s+|tenemos\s+)?presta(?:d[oa]|o)s?|total\s+de\s+unidades\s+presta(?:d[oa]|o)s?|cantidad\s+de\s+unidades\s+presta(?:d[oa]|o)s?|cu[aá]nto\s+material\s+hay\s+prestado)\b", texto_limpio)
        )
        es_metrica_prestamo_responsables = bool(
            re.search(r"\b(qui[eé]n\s+tiene\s+(?:cosas|productos|material|art[ií]culos|\w+)?\s*presta(?:d[oa]|o)s?|a\s+qui[eé]n\s+(?:se\s+le\s+prest[oó]|est[aá]\s+prestado)|responsables?\s+de\s+pr[eé]stamos?)\b", texto_limpio)
            and not re.search(r"\b(eso|ese|este|aquel|su|el|la|dicho)\b", texto_limpio)
        )

        es_prestamos_global = bool(
            es_metrica_prestamo_registros
            or es_metrica_prestamo_unidades
            or es_metrica_prestamo_responsables
            or re.search(r"\b(hay\s+alg[uú]n\s+producto\s+en\s+pr[eé]stamos?|hay\s+algo\s+prestado|qu[eé]\s+tenemos\s+prestado|qu[eé]\s+est[aá]\s+prestado|qu[eé]\s+(?:productos|art[ií]culos|referencias)\s+est[aá]n\s+prestados?|mu[eé]strame\s+los\s+(?:productos|art[ií]culos)\s+que\s+est[aá]n\s+prestados?|cu[aá]les\s+son\s+los\s+pr[eé]stamos\s+activos?|pr[eé]stamos\s+activos|pr[eé]stamos\s+pendientes|qu[eé]\s+pr[eé]stamos\s+hay)\b", texto_limpio)
            or (re.search(r"\bqui[eé]n\s+tiene\b", texto_limpio) and not re.search(r"\b(eso|ese|este|aquel|su|el|la|dicho)\b", texto_limpio))
            or (re.search(r"\b(pr[eé]stamo|pr[eé]stamos|prestado|prestados)\b", texto_limpio) and re.search(r"\b(hay\s+alg[uú]n|qu[eé]\s+tenemos|qu[eé]\s+hay|cu[aá]les\s+son|todos\s+los|qu[eé]\s+productos)\b", texto_limpio))
        )

        if es_metrica_prestamo_registros:
            ranuras.metrica_prestamo = "CONTEO_REGISTROS"
        elif es_metrica_prestamo_unidades:
            ranuras.metrica_prestamo = "CONTEO_UNIDADES"
        elif es_metrica_prestamo_responsables:
            ranuras.metrica_prestamo = "LISTA_RESPONSABLES"
        elif re.search(r"\b(qu[eé]\s+productos|cu[aá]les\s+productos|qu[eé]\s+art[ií]culos)\b", texto_limpio) and es_prestamos_global:
            ranuras.metrica_prestamo = "LISTA_PRODUCTOS"

        es_agotados_global = bool(
            re.search(r"\b(?:qu[eé]|cu[aá]les)\s+(?:productos|art[ií]culos|referencias)\s+(?:no\s+tienen\s+stock|est[aá]n\s+agotad[oa]s?|se\s+agotaron|tienen\s+stock\s+cero|est[aá]n\s+en\s+cero|est[aá]n\s+sin\s+existencias?|sin\s+existencias?)\b", texto_limpio)
            or re.search(r"\bqu[eé]\s+(?:productos|art[ií]culos)\s+(?:no\s+tienen|sin)\s+stock\b", texto_limpio)
            or re.search(r"\b(?:productos|art[ií]culos)\s+(?:sin\s+stock|agotados|en\s+cero)\b", texto_limpio)
            or re.search(r"\b(?:qu[eé]|cu[aá]les)\s+(?:est[aá]|est[aá]n|se)\s+agotad[oa]s?\b", texto_limpio)
            or re.search(r"\b(?:qu[eé]|cu[aá]les)\s+se\s+agotaron\b", texto_limpio)
            or re.search(r"\b(?:qu[eé]|cu[aá]les)\s+no\s+tienen\s+stock\b", texto_limpio)
            or re.search(r"\bqu[eé]\s+est[aá]\s+en\s+cero\b", texto_limpio)
        )

        es_bajo_stock_global = bool(
            re.search(r"\b(?:qu[eé]|cu[aá]les|los\s+que|mu[eé]strame\s+los\s+que)\s+(?:productos\s+|art[ií]culos\s+)?(?:est[aá]n\s+)?por\s+debajo\s+(?:del?\s+(?:stock\s+)?m[ií]nimo|de\s+lo\s+(?:que\s+)?(?:deber[ií]amos|hay\s+que)\s+tener|de\s+lo\s+necesario)\b", texto_limpio)
            or re.search(r"\b(?:qu[eé]|cu[aá]les)\s+(?:productos\s+|art[ií]culos\s+)?(?:necesitan?|requieren?)\s+(?:reposici[oó]n|reponer|reabastecimiento|reabastecer)\b", texto_limpio)
            or re.search(r"\b(?:qu[eé]|cu[aá]les)\s+(?:est[aá]n\s+)?por\s+debajo\s+del?\s+m[ií]nimo\b", texto_limpio)
            or re.search(r"\b(?:qu[eé]|cu[aá]les)\s+productos\s+tienen\s+bajo\s+st(?:ock|op)\b", texto_limpio)
            or re.search(r"\b(?:productos|art[ií]culos)\s+(?:con\s+)?bajo\s+st(?:ock|op)\b", texto_limpio)
            or re.search(r"\b(?:alerta|alertas)\s+(?:de\s+)?(?:stock|inventario)\b", texto_limpio)
            or re.search(r"\bqu[eé]\s+alertas\s+(?:hay|tenemos)\b", texto_limpio)
            or re.search(r"\b(?:en\s+)?barro\s+st(?:ock|op)\b", texto_limpio)
        )

        es_catalogo_resumen_global = bool(
            re.search(r"\b(?:qu[eé]|cu[aá]les)\s+(?:productos|art[ií]culos)\s+(?:hay|tenemos|existen|est[aá]n\s+registrados|manejamos)\b", texto_limpio)
            or re.search(r"\b(?:cat[aá]logo|listado|lista)\s+de\s+productos\b", texto_limpio)
            or re.search(r"\bqu[eé]\s+hay\s+en\s+el\s+(?:cat[aá]logo|inventario|almac[eé]n|bodega)\b", texto_limpio)
            or re.search(r"\b(?:qu[eé]|cu[aá]les)\s+(?:son\s+los\s+)?(?:almacenes|bodegas)\b", texto_limpio)
            or re.search(r"\b(?:dime|listar?|ver|mostrar|cu[aá]les\s+son)\s+(?:los\s+|las\s+)?(?:almacenes|bodegas)(?:\s+disponibles|\s+activ[oa]s)?\b", texto_limpio)
            or re.search(r"\b(?:almacenes|bodegas)\s+(?:disponibles|activ[oa]s)\b", texto_limpio)
            or re.search(r"\bqu[eé]\s+(?:almacenes|bodegas)\b", texto_limpio)
            or re.search(r"\bqu[eé]\s+productos\s+(?:hay|tenemos|est[aá]n|quedan)\s+en\s+stock\b", texto_limpio)
            or re.search(r"\bqu[eé]\s+(?:hay|tenemos|queda)\s+en\s+stock\b", texto_limpio)
            or re.search(r"\b(?:productos|art[ií]culos)\s+(?:en\s+stock|con\s+stock|disponibles)\b", texto_limpio)
            or re.search(r"\bqu[eé]\s+(?:tenemos|hay)\s+disponible\b", texto_limpio)
            or re.search(r"\b(?:resumen|balance|estado\s+del?\s+(?:almac[eé]n|inventario|bodega)|c[oó]mo\s+(?:va|anda|est[aá])\s+(?:el\s+almac[eé]n|el\s+inventario|la\s+bodega))\b", texto_limpio)
            or re.search(r"\b(?:consultar?|revisar?|ver|chequear?|dime|consulta)\s+(?:el\s+)?(?:inventario|almac[eé]n|bodega)\b", texto_limpio)
        )

        meses_y_dias_pat = r"(?:enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre|lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo)"
        es_movimientos_global = bool(
            (ranuras.ventana_temporal is not None and "MOVIMIENTO" in ranuras.conceptos_operativos)
            or re.search(r"\b(?:mu[eé]strame|dame|ver|consultar?|qu[eé])\s+(?:los\s+)?(?:movimientos?|entradas?|salidas?|traslados?|despachos?)\b", texto_limpio)
            or re.search(rf"\b(?:movimientos?|entradas?|salidas?|traslados?)\s+(?:de\s+|en\s+|durante\s+)?(?:ayer|hoy|la\s+semana|este\s+mes|el\s+mes|los\s+[uú]ltimos|{meses_y_dias_pat})\b", texto_limpio)
            or re.search(r"\b(cu[aá]nt[ao]s?|qu[eé])\s+(?:entradas?|salidas?|despachos?|movimientos?|operaciones|transacciones?|actividades?|actividad|pr[eé]stamos?|devoluciones?)\s+(?:han\s+habido|hubo|se\s+hicieron|se\s+registraron|tuvimos|tuvieron|hay|tenemos)\b", texto_limpio)
            or re.search(r"\b(?:qu[eé]|cu[aá]l|cu[aá]les)\s+(?:\w+\s+){0,3}(?:entr[oó]|entraron|ingres[oó]|ingresaron|lleg[oó]|llegaron|sali[oó]|salieron|se\s+despach[oó]|se\s+movi[oó])\b", texto_limpio)
            or re.search(r"\b(movimientos?\s+(?:de\s+)?(?:hoy|ayer)|movimientos?\s+del\s+d[ií]a|c[oó]mo\s+van\s+los\s+movimientos|c[oó]mo\s+va\s+la\s+movida|de\s+movida\s+la\s+vaina|el\s+voleo\s+(?:de\s+)?hoy|qu[eé]\s+voleo\s+hay|qu[eé]\s+se\s+ha\s+movido|c[oó]mo\s+va\s+el\s+voleo|c[oó]mo\s+estuvo\s+el\s+voleo|c[oó]mo\s+va\s+el\s+trabajo\s+hoy|qu[eé]\s+tal\s+estuvo\s+la\s+jornada|c[oó]mo\s+estuvo\s+la\s+jornada|c[oó]mo\s+estuvo\s+la\s+vuelta|c[oó]mo\s+va\s+la\s+vaina|c[oó]mo\s+anda\s+la\s+cosa|c[oó]mo\s+va\s+la\s+cosa|c[oó]mo\s+va\s+eso|qu[eé]\s+pas[oó]\s+hoy\s+en\s+(?:el\s+almac[eé]n|la\s+bodega)|c[oó]mo\s+va\s+el\s+d[ií]a|qu[eé]\s+tan\s+movido)\b", texto_limpio)
        )

        es_alertas_global = bool(
            (
                re.search(r"\b(?:alerta|alertas)\s+(?:de\s+)?(?:stock|inventario)\b", texto_limpio)
                or re.search(r"\bqu[eé]\s+alertas\s+(?:hay|tenemos)\b", texto_limpio)
                or re.search(r"\b(?:agotad[oa]s?\s+o\s+con\s+bajo\s+st(?:ock|op)|bajo\s+st(?:ock|op)\s+o\s+agotad[oa]s?)\b", texto_limpio)
                or re.search(r"\b(?:en\s+)?barro\s+st(?:ock|op)\b", texto_siglas)
                or re.search(r"\b(?:av[ií]same\s+)?(?:lo\s+que|qu[eé]|cu[aá]les)\s+est[eé]\s+en\s+bajo\s+st(?:ock|op)\b", texto_limpio)
                or re.search(r"\b(?:av[ií]same\s+)?(?:lo\s+que|qu[eé]|cu[aá]les)\s+est[eé]\s+en\s+barro\s+st(?:ock|op)\b", texto_siglas)
            )
            and not re.search(r"\b(pelado|pelao|vacio|vac[ií]o|acab[oó]|acabao|escaso|nada)\b", texto_siglas)
            and not re.search(r"\b(?:qu[eé]|cu[aá]les)\s+productos\s+(?:tienen|est[aá]n\s+en|hay\s+en)\s+bajo\s+st(?:ock|op)\b", texto_limpio)
        )

        es_faltantes_global = bool(
            re.search(
                r"\b(?:qu[eé]|cu[aá]l|cu[aá]les)\s+(?:hace\s+falta|falta|hacen\s+falta)\s+(?:por\s+|para\s+)?(?:pedir|comprar|ordenar|reponer|abastecer)\b",
                texto_limpio,
            )
            or re.search(r"\bqu[eé]\s+(?:tengo|tenemos|hay)\s+que\s+(?:pedir|comprar|ordenar|reponer|mandar\s+a\s+pedir)\b", texto_limpio)
            or re.search(r"\bqu[eé]\s+(?:est[aá]|est[aá]n)\s+por\s+(?:acabarse|terminarse|agotarse)\b", texto_limpio)
            or re.search(r"\bqu[eé]\s+debo\s+reponer\b", texto_limpio)
            or re.search(r"\brecomendaci[oó]n\s+de\s+(?:compra|compras|reposici[oó]n)\b", texto_limpio)
            or re.search(r"\bqu[eé]\s+hace\s+falta\s+(?:en\s+el\s+almac[eé]n|en\s+la\s+bodega|comprar|pedir)\b", texto_limpio)
        )

        menciona_producto_potencial = bool(
            re.search(r"\b[A-Za-z]{2,5}[-_]\d{1,4}\b", texto_siglas)
            or re.search(r"\b(?:del?|sobre|con)\s+(?:el\s+|la\s+|este\s+|ese\s+)?(?:cable|hdmi|utp|bobina|switch|router|ponchadora|conector|rj45|rj-45|patch\s*cord)\b", texto_limpio)
            or re.search(r"\b(hdmi|utp|bobina|ponchadora|patch\s*cord)\b", texto_limpio)
        )
        if ranuras.accion in ("REGISTRAR_ENTRADA", "REGISTRAR_SALIDA", "PRESTAR", "DEVOLVER"):
            ranuras.alcance = "INDIVIDUAL"
            ranuras.filtro_coleccion = None
        elif es_alcance_coleccion:
            ranuras.alcance = "COLECCION"
        elif (
            es_prestamos_global
            or es_agotados_global
            or es_bajo_stock_global
            or es_faltantes_global
            or es_alertas_global
            or es_catalogo_resumen_global
            or (es_movimientos_global and not menciona_producto_potencial)
            or re.search(r"\b(qu[eé]\s+productos|cu[aá]les\s+productos|todos\s+los\s+productos|cat[aá]logo|almacenes|resumen\s+general)\b", texto_limpio)
        ):
            ranuras.alcance = "GLOBAL"
        elif ranuras.almacen_especifico is not None:
            es_consulta_individual_en_almacen = bool(
                (analisis_costeno and getattr(analisis_costeno, "demostrativo_detectado", None))
                or re.search(r"\b(ese|esa|este|esta|aquel|aquella|de\s+es[eo]|de\s+aquel|dicho|anterior|vaina)\b", texto_limpio)
                or re.search(r"\b[A-Za-z]{3}-\d{3,4}\b", texto_siglas)
                or re.search(r"\b(cu[aá]nto\s+queda|cu[aá]nto\s+hay|cu[aá]ntas\s+unidades|d[oó]nde\s+est[aá]|d[oó]nde\s+se\s+encuentra)\b", texto_limpio)
                or re.search(r"\b(cable|hdmi|utp|bobina|switch|router|ponchadora|conector|rj45|rj-45)\b", texto_limpio)
            )
            if es_consulta_individual_en_almacen:
                ranuras.alcance = "INDIVIDUAL"
            else:
                ranuras.alcance = "ALMACEN"
        else:
            ranuras.alcance = "INDIVIDUAL"

        # 6. Detección de Condición / Filtro sobre stock
        if es_prestamos_global or re.search(r"\b(presta(?:d[oa]|o)s?|en\s+pr[eé]stamos?)\b", texto_limpio):
            ranuras.condicion = "EN_PRESTAMO"
            ranuras.conceptos_operativos.add("PRESTAMO")
        elif es_agotados_global or re.search(r"\b(sin\s+stock|no\s+tienen?\s+stock|agotad[oa]s?|en\s+cero|cero\s+stock|acabad[oa]s?|se\s+acabaron|acabaron|se\s+terminaron|terminaron|sin\s+existencias?)\b", texto_limpio):
            ranuras.condicion = "AGOTADO"
            ranuras.conceptos_operativos.add("STOCK")
            ranuras.conceptos_operativos.add("STOCK_AGOTADO")
        elif es_alertas_global:
            ranuras.condicion = "ALERTAS"
            ranuras.conceptos_operativos.add("ALERTA")
        elif es_bajo_stock_global or re.search(r"\b(bajo\s+st(?:ock|op)|por\s+debajo\s+(?:del?\s+(?:stock\s+)?m[ií]nimo|de\s+lo\s+(?:que\s+)?(?:deber[ií]amos|hay\s+que)\s+tener|de\s+lo\s+necesario)|necesitan?\s+reposici[oó]n|requieren?\s+reposici[oó]n|reponer|reabastecer|stock\s+m[ií]nimo|stock\s+insuficiente|escaso)\b", texto_limpio):
            ranuras.condicion = "BAJO_STOCK"
            ranuras.conceptos_operativos.add("STOCK")
            ranuras.conceptos_operativos.add("STOCK_BAJO")
        elif re.search(r"\b(con\s+stock|en\s+stock|disponible|disponibles|con\s+existencias?)\b", texto_limpio):
            ranuras.condicion = "DISPONIBLE"
            ranuras.conceptos_operativos.add("STOCK")
            ranuras.conceptos_operativos.add("STOCK_DISPONIBLE")
        else:
            ranuras.condicion = "NINGUNA"

        # 7. Conceptos Operativos Adicionales
        # MOVIMIENTO:
        if re.search(r"\b(movimientos?|operaci[oó]n|operaciones|transacci[oó]n|transacciones|actividad|actividades|registro|registros|despacho|despachos|flujo|flujos|movida|voleo|vuelta|trabajo|qu[eé]\s+se\s+ha\s+movido|c[oó]mo\s+va\s+la\s+vaina|qu[eé]\s+pas[oó]\s+hoy)\b", texto_limpio):
            ranuras.conceptos_operativos.add("MOVIMIENTO")

        # RESUMEN_GENERAL:
        if re.search(
            r"\b(resumen|balance|estado\s+del?\s+(almac[eé]n|inventario|bodega)|c[oó]mo\s+(va|anda|est[aá]|pinta)\s+(todo|el\s+almac[eé]n|el\s+inventario|la\s+bodega)|(?:consultar?|revisar?|ver|chequear?|dime)\s+(el\s+)?(?:inventario|almac[eé]n|bodega))\b",
            texto_limpio,
        ) or re.search(r"\bconsulta\s+(?:el\s+)?(?:inventario|almac[eé]n|bodega)\b", texto_limpio):
            ranuras.conceptos_operativos.add("RESUMEN_GENERAL")

        # STOCK:
        if re.search(r"\b(stock|existencias?|disponible|disponibilidad|saldo|saldos|unidades?|piezas?|cantidad|cantidades|quedan?|queda|tenemos|contamos|hay|bastante|bastantes|nada|escaso|pelado|vac[ií]o|agotad[oa]s?|acabad[oa]s?|acabaron|se\s+acabaron|se\s+acab[oó]|terminaron|se\s+terminaron)\b", texto_limpio):
            ranuras.conceptos_operativos.add("STOCK")

        # UBICACION:
        if match_contexto or re.search(r"\b(d[oó]nde|ubicaci[oó]n|localizaci[oó]n|posici[oó]n|secci[oó]n|encuentra|encuentran|guardan?|guardamos|guardad[oa]s?|lado|ub[ií]came)\b", texto_limpio):
            ranuras.conceptos_operativos.add("UBICACION")

        # PRESTAMO:
        # Préstamo temporal:
        if re.search(r"\b(prestar|presta|pr[eé]stale|prestale|preste|presten|pr[eé]stamo|pr[eé]stamos|pr[eé]stame|prestado|prestados|d[eé]jame\s+un[oa]|que\s+(?:lo|los|la|las)\s+devuelva)\b", texto_limpio):
            ranuras.conceptos_operativos.add("PRESTAMO")

        # DEVOLUCION:
        if re.search(r"\b(devolver|devoluci[oó]n|devoluciones|regreso|regresar|devuelvo|ya\s+volvi[oó]|lo\s+traje\s+otra\s+vez)\b", texto_limpio):
            ranuras.conceptos_operativos.add("DEVOLUCION")

        # ALERTA:
        if re.search(r"\b(alerta|alertas|bajo\s+stock|sin\s+stock|qu[eé]\s+falta)\b", texto_limpio):
            ranuras.conceptos_operativos.add("ALERTA")

        # 8. Acciones o verbos
        es_consulta_estadistica = bool(
            re.search(r"\b(cu[aá]nt[ao]s?|qu[eé])\s+(?:entradas?|salidas?|despachos?|movimientos?|operaciones|pr[eé]stamos?|devoluciones?)\b", texto_limpio)
            or re.search(r"\b(?:qu[eé]|cu[aá]l|cu[aá]les)\s+(?:\w+\s+){0,3}(?:entr[oó]|entraron|ingres[oó]|ingresaron|lleg[oó]|llegaron|sali[oó]|salieron|se\s+despach[oó]|se\s+movi[oó])\b", texto_limpio)
            or re.search(r"\b(?:han\s+habido|hubo|se\s+hicieron|se\s+registraron|tuvimos|tuvieron)\b", texto_limpio)
            or re.search(r"\b(?:movimientos?|actividad|voleo|despachos?)\s+(?:de\s+)?hoy\b", texto_limpio)
        )

        if es_consulta_estadistica:
            ranuras.accion = "CONSULTAR"
            ranuras.conceptos_operativos.add("MOVIMIENTO")
        elif re.search(r"\b(prestar|presta|pr[eé]stale|prestale|preste|presten|pr[eé]stamo|pr[eé]stame|d[eé]jame\s+un[oa]|que\s+(?:lo|los|la|las)\s+devuelva)\b", texto_limpio):
            ranuras.accion = "PRESTAR"
            ranuras.conceptos_operativos.add("PRESTAMO")
        elif re.search(r"\b(ingresar|ingreso|llegaron|lleg[oó]|recibir|aumentar|meter|mete|m[eé]telo|m[eé]tame|emboca|emb[oó]calo|agr[eé]gale|agregar|registrar\s+entrada|hacer\s+entrada)\b", texto_limpio) or (re.search(r"\bentrada\b", texto_limpio) and not re.search(r"\b(cu[aá]ntas?|qu[eé]|ver|consultar)\b", texto_limpio)):
            ranuras.accion = "REGISTRAR_ENTRADA"
        elif re.search(r"\b(dotaci[oó]n|entrega\s+definitiva|definitivamente|entregar?\s+definitivamente|entregar?\s+dotaci[oó]n|dar\s+dotaci[oó]n)\b", texto_limpio):
            ranuras.accion = "REGISTRAR_SALIDA"
            ranuras.motivo = "Dotación / entrega definitiva"
        elif re.search(r"\b(retirar|sacar|saca|s[aá]came|desp[aá]chame|despacha|desp[aá]chalo|m[aá]ndame|b[aá]jame|b[aá]jalo|entregar|p[aá]same|gastar|registrar\s+salida|hacer\s+salida)\b", texto_limpio) or (re.search(r"\bsalida\b", texto_limpio) and not re.search(r"\b(cu[aá]ntas?|qu[eé]|ver|consultar)\b", texto_limpio)):
            ranuras.accion = "REGISTRAR_SALIDA"
        elif re.search(r"\b(devolver|devoluci[oó]n|te\s+regreso|regreso|devuelvo|ya\s+volvi[oó]|lo\s+traje\s+otra\s+vez)\b", texto_limpio):
            ranuras.accion = "DEVOLVER"
        elif re.search(r"\b(trasladar|traslada|trasladom?|trasladem?|traslad[aá]me|transferir|transferencia|transfieram?|mover\s+(?:a\s+)?(?:otro\s+almac[eé]n|otra\s+bodega)|pasar\s+(?:a\s+)?(?:otro\s+almac[eé]n|otra\s+bodega)|llevar\s+a\s+(?:otro\s+almac[eé]n|otra\s+bodega)|enviar\s+a\s+(?:otro\s+almac[eé]n|otra\s+bodega)|mandar\s+(?:a\s+|para\s+)(?:otro\s+almac[eé]n|otra\s+bodega|almac[eé]n|bodega))\b", texto_limpio):
            ranuras.accion = "TRASLADAR"
            ranuras.conceptos_operativos.add("MOVIMIENTO")
        elif re.search(r"\b(prestar|presta|pr[eé]stale|prestale|preste|presten|pr[eé]stamo|pr[eé]stame|d[eé]jame\s+un[oa])\b", texto_limpio):
            ranuras.accion = "PRESTAR"
        elif re.search(r"\b(qu[eé]|c[oó]mo|cu[aá]nto|cu[aá]ntos|cu[aá]ntas|d[oó]nde|ver|dime|decir|consultar|conocer|revisar|saber|hubo|han\s+habido|tuvimos|tuvieron|se\s+hicieron|se\s+registraron|se\s+encuentra|guardan|tienen|hay)\b", texto_limpio):
            ranuras.accion = "CONSULTAR"

        # 9. Construcción del Marco Semántico (SemanticFrame)
        concepto_final = ConceptoTipo.STOCK
        if "PRESTAMO" in ranuras.conceptos_operativos or ranuras.condicion == "EN_PRESTAMO":
            concepto_final = ConceptoTipo.PRESTAMO
        elif "DEVOLUCION" in ranuras.conceptos_operativos or ranuras.accion == "DEVOLVER":
            concepto_final = ConceptoTipo.DEVOLUCION
        elif "MOVIMIENTO" in ranuras.conceptos_operativos:
            concepto_final = ConceptoTipo.MOVIMIENTO
        elif "RESUMEN_GENERAL" in ranuras.conceptos_operativos:
            concepto_final = ConceptoTipo.RESUMEN_GENERAL
        elif "UBICACION" in ranuras.conceptos_operativos:
            concepto_final = ConceptoTipo.UBICACION

        modalidad_final = ModalidadTipo.CONSULTA
        if ranuras.es_afirmacion_pura:
            modalidad_final = ModalidadTipo.CONFIRMAR
        elif ranuras.es_negacion_pura:
            modalidad_final = ModalidadTipo.CANCELAR
        elif ranuras.acto_de_habla == "SALUDO":
            modalidad_final = ModalidadTipo.SALUDO
        elif ranuras.acto_de_habla == "AGRADECIMIENTO":
            modalidad_final = ModalidadTipo.AGRADECIMIENTO
        elif ranuras.accion in ("REGISTRAR_ENTRADA", "REGISTRAR_SALIDA", "PRESTAR", "DEVOLVER", "TRASLADAR"):
            modalidad_final = ModalidadTipo.MUTACION

        ranuras.semantic_frame = SemanticFrame(
            modalidad=modalidad_final,
            alcance=AlcanceTipo(ranuras.alcance),
            concepto=concepto_final,
            condicion=CondicionTipo(ranuras.condicion),
            almacen_especifico=ranuras.almacen_especifico,
            filtro_coleccion=ranuras.filtro_coleccion,
            destinatario=ranuras.destinatario,
        )

        return ranuras

    PALABRAS_PARADA = {
        # Vocativos y cortesía
        "haku", "oye", "hola", "saludos", "favor", "gracias", "porfa", "porfavor", "buenas", "buenos", "dias", "tardes", "noches",
        # Artículos y pronombres
        "el", "la", "los", "las", "un", "uno", "una", "unos", "unas",
        "se", "lo", "le", "me", "te", "nos", "les",
        "este", "esta", "estos", "estas", "esto",
        "ese", "esa", "esos", "esas", "eso", "eto", "eta",
        "aquel", "aquella", "aquellos", "aquellas", "aquello",
        "su", "sus", "mi", "mis", "tu", "tus",
        "dicho", "dicha", "mismo", "misma", "otro", "otra", "otros", "otras",
        "anterior", "anteriores", "previo", "previa", "previos", "previas", "pasado", "pasada", "pasados", "pasadas",
        # Preposiciones y conjunciones
        "de", "del", "al", "a", "para", "por", "pa", "en", "con", "sin", "sobre", "entre",
        "y", "e", "o", "u", "ni", "que", "si", "no", "como", "cuando", "donde", "cual", "cuales",
        # Numerales y cuantificadores
        "cero", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve", "diez",
        "once", "doce", "veinte", "treinta", "cincuenta", "cien", "ciento", "mil",
        "cuanto", "cuantos", "cuanta", "cuantas", "mucho", "muchos", "mucha", "muchas",
        "poco", "pocos", "poca", "pocas", "bastante", "bastantes", "algo", "nada", "todo", "todos", "toda", "todas",
        "mas", "menos", "tanto", "demasiado", "demasiados",
        "algun", "alguno", "alguna", "algunos", "algunas", "ningun", "ninguno", "ninguna", "ningunos", "ningunas",
        # Adverbios y estados
        "hoy", "ayer", "ahora", "ya", "todavia", "aun", "bien", "mal", "asi",
        "pelado", "vacio", "vacia", "vacios", "vacias",
        "agotado", "agotados", "agotada", "agotadas", "acabado", "acabados", "terminaron", "agotaron", "acabaron",
        # Metáforas / dialectal comodín
        "vaina", "vainas", "cosa", "cosas", "tema",
        # Reposición y necesidad
        "reposicion", "reponer", "reabastecer", "reabastecimiento",
        "necesita", "necesitan", "requiere", "requieren", "falta", "faltan",
        # Sustantivos abstractos de inventario
        "producto", "productos", "articulo", "articulos", "item", "items",
        "referencia", "referencias", "bodega", "bodegas", "almacen", "almacenes",
        "stock", "existencia", "existencias", "disponible", "disponibles", "disponibilidad",
        "saldo", "saldos", "unidad", "unidades", "pieza", "piezas", "cantidad", "cantidades",
        "inventario", "inventarios",
        "ubicacion", "ubicaciones", "localizacion", "posicion", "seccion",
        "ubicado", "ubicada", "ubicados", "ubicadas", "localizado", "localizada", "localizados", "localizadas",
        "estante", "estantes", "rack", "racks", "pasillo", "pasillos", "gaveta", "gavetas",
        "cajon", "cajones", "repisa", "repisas", "lugar", "lugares",
        "movimiento", "movimientos", "operacion", "operaciones", "transaccion", "transacciones",
        "actividad", "registro", "registros", "despacho", "despachos", "flujo", "flujos",
        "salida", "salidas", "entrada", "entradas", "prestamo", "prestamos", "devolucion", "devoluciones",
        # Verbos conjugados comunes
        "hay", "hubo", "habia", "habido", "ha", "han", "hemos",
        "queda", "quedan", "quedo", "quedaron",
        "esta", "estan", "estuvo", "estado",
        "tiene", "tienen", "tuvo", "tuvimos", "tenemos", "tenido", "contamos",
        "salio", "salieron", "saliendo", "salido", "salidos", "sale", "salen",
        "entro", "entraron", "entrando", "entrado", "entrados", "entra", "entran",
        "llego", "llegaron", "llegando",
        "movio", "movieron", "moviendo", "movido", "movidos", "mueve", "mueven",
        "acabo", "acabaron", "termino",
        "paso", "pasaron", "va", "van",
        "meter", "mete", "metelo", "metele", "metame", "metieron",
        "sacar", "saca", "sacalo", "sacale", "sacame", "sacaron",
        "despachar", "despacha", "despachalo", "despachame", "despacharon",
        "bajar", "baja", "bajalo", "bajame", "bajaron",
        "mandar", "manda", "mandalo", "mandame",
        "pasar", "pasa", "pasalo", "pasame",
        "prestar", "presta", "prestalo", "prestame", "preste", "presten", "prestado", "prestao",
        "dejar", "deja", "dejalo", "dejame", "dejaron",
        "devolver", "devuelve", "devuelvelo", "devuelveme", "devuelvo",
        "regresar", "regresa", "regresalo", "regresame", "regreso",
        "traer", "trae", "traelo", "traeme", "traje", "trajo", "trajeron",
        "llevar", "lleva", "llevalo", "llevame",
        "ver", "dime", "decir", "consultar", "consulta", "consultas", "consultame",
        "buscar", "buscame", "busca", "buscan", "buscando", "saber", "sabe", "revisar", "revisa", "revisame", "mira", "mirame",
        "mencionar", "mencionaste", "mencionadas", "listar", "listadas", "dijiste", "acabas",
    }

    @classmethod
    def interpretar(
        cls,
        mensaje: str,
        usuario: UsuarioAudacIA,
        contexto_sesion: Optional[dict] = None,
        estado_operativo: Any = None,
    ) -> IntencionResultado:
        """Transforma lenguaje natural (incluyendo expresiones costeñas) en una intención segura."""
        def _responder(res: IntencionResultado) -> IntencionResultado:
            HakuCacheManager.registrar_intencion_detectada(res.tipo.value)
            return res

        if not mensaje or not mensaje.strip():
            return _responder(IntencionResultado(
                tipo=IntencionTipo.SOLICITAR_ACLARACION,
                mensaje_aclaracion="No entendí tu mensaje. ¿Puedes repetirlo?",
            ))

        producto_en_contexto = None

        # 1. Recuperar producto en contexto desde EstadoOperativoHaku (RAM) o diccionario fallback
        if estado_operativo is not None and hasattr(estado_operativo, "esta_expirado"):
            if not estado_operativo.esta_expirado(cls.TIMEOUT_CONTEXTO_SEGUNDOS) and estado_operativo.producto_id:
                qs_ctx = Producto.objects.all() if (usuario and usuario.es_administrador) else Producto.objects.filter(activo=True, archivado=False)
                producto_en_contexto = qs_ctx.filter(id=estado_operativo.producto_id).first()

        if producto_en_contexto is None and contexto_sesion:
            ahora = time.time()
            ultimo_acceso = contexto_sesion.get("haku_ultimo_acceso", ahora)
            if (ahora - ultimo_acceso) <= cls.TIMEOUT_CONTEXTO_SEGUNDOS:
                prod_id = contexto_sesion.get("haku_ultimo_producto_id")
                if prod_id:
                    qs_ctx = Producto.objects.all() if (usuario and usuario.es_administrador) else Producto.objects.filter(activo=True, archivado=False)
                    producto_en_contexto = qs_ctx.filter(id=prod_id).first()

        # 1. Paso 1: Normalización fonética Whisper (resuelve errores dialectales en transcripción)
        texto_fonetico = InterpretadorCosteno.normalizar_fonetica_whisper(mensaje)

        # 2. Paso 2: Normalización de siglas técnicas y códigos hablados
        texto_siglas = NormalizadorSiglasTecnicas.normalizar(texto_fonetico)

        # 3. Paso 3: Análisis dialectal costeño y detección de giros/ambigüedades
        analisis = InterpretadorCosteno.normalizar(texto_siglas)
        texto_trabajo = analisis.texto_estandar.lower()

        # 4. Paso 4: Extracción determinista de ranuras semánticas
        ranuras = cls._extraer_ranuras_semanticas(texto_trabajo, texto_siglas, analisis)

        # Si el usuario introduce una ruptura discursiva de tema ("cambiando de tema", "otro tema", etc.),
        # se purga de raíz el foco conversacional previo para que la nueva consulta inicie limpia.
        if ranuras.es_cambio_de_tema:
            producto_en_contexto = None
            if estado_operativo:
                estado_operativo.limpiar_foco_conversacional()

        # AISLAMIENTO TOTAL DE ALCANCE (FASE 2):
        # Si la consulta es de alcance GLOBAL o ALMACEN, el foco conversacional previo NUNCA se hereda.
        if ranuras.alcance in ("GLOBAL", "ALMACEN"):
            producto_en_contexto = None

        # AISLAMIENTO POR CAMBIO DE FLUJO (FASE 3):
        # Las intenciones CRUD (crear/editar usuario o producto) implican un flujo completamente
        # nuevo e independiente del producto anterior en foco. Purgar para evitar contaminación.
        _es_flujo_crud = bool(
            re.search(r"\b(?:quiero\s+|deseo\s+|voy\s+a\s+|vamos\s+a\s+|necesito\s+)?(?:crear|registrar|ingresar|dar\s+de\s+alta|editar|modificar|actualizar|inhabilitar|desactivar|archivar|dar\s+de\s+baja)\s+(?:un\s+)?(?:nuevo\s+)?(?:usuario|producto)\b", texto_trabajo)
        )
        if _es_flujo_crud:
            producto_en_contexto = None
            if estado_operativo:
                estado_operativo.producto_id = None
                estado_operativo.producto_codigo = None
                estado_operativo.producto_nombre = None
                estado_operativo.candidatos = []

        # ─────────────────────────────────────────────────────────────────────
        # REGLA 0: PROTOCOLO SOCIAL Y CONFIRMACIONES / CANCELACIONES PURAS
        # ─────────────────────────────────────────────────────────────────────
        if ranuras.es_afirmacion_pura:
            return _responder(IntencionResultado(tipo=IntencionTipo.ACCION_CONFIRMAR))

        if ranuras.es_negacion_pura:
            return _responder(IntencionResultado(tipo=IntencionTipo.ACCION_CANCELAR))

        # 0-bis. SEGUIMIENTO DE OPERACIÓN PENDIENTE (RECOLECCIÓN MULTITURNO DE DESTINATARIO)
        op_pendiente = getattr(estado_operativo, "operacion_pendiente", None) if estado_operativo else None
        if op_pendiente and op_pendiente.get("esperando") == "destinatario":
            # Si el usuario responde cancelando o cambiando de tema
            if ranuras.es_cambio_de_tema:
                if estado_operativo:
                    estado_operativo.operacion_pendiente = None
            else:
                # Extraer nombre del destinatario del texto
                texto_nom = texto_siglas.strip()
                # Limpiar prefijos conversacionales como "la persona se llama", "se llama", "a", "para", "ay"
                texto_nom = re.sub(r"^(?:la\s+persona\s+se\s+llama|se\s+llama|el\s+destinatario\s+es|es\s+para|para|a)\s+", "", texto_nom, flags=re.IGNORECASE).strip()
                # Si empieza por 'ay' pegado al nombre (ej. 'ayancarlos' -> 'yancarlos')
                if re.match(r"^ay[a-z]{3,}", texto_nom, flags=re.IGNORECASE):
                    texto_nom = texto_nom[1:].strip()
                elif re.match(r"^a\s+[a-z]{2,}", texto_nom, flags=re.IGNORECASE):
                    texto_nom = texto_nom[2:].strip()
                texto_nom = texto_nom.strip().title()
                
                prod_id = op_pendiente.get("producto_id")
                cant = op_pendiente.get("cantidad", 1)
                prod_obj = Producto.objects.filter(id=prod_id, activo=True, archivado=False).first()
                if prod_obj and len(texto_nom) >= 2:
                    return _responder(IntencionResultado(
                        tipo=IntencionTipo.PRESTAMO,
                        producto=prod_obj,
                        cantidad=cant,
                        destinatario=texto_nom,
                        requiere_confirmacion=True,
                    ))

        tiene_operacion = bool(
            ranuras.conceptos_operativos.intersection({"STOCK", "UBICACION", "MOVIMIENTO", "RESUMEN_GENERAL", "PRESTAMO", "DEVOLUCION", "ALERTA"})
            or (ranuras.accion in {"REGISTRAR_ENTRADA", "REGISTRAR_SALIDA", "PRESTAR", "DEVOLVER", "TRASLADAR"})
        )

        if ranuras.acto_de_habla == "SALUDO" and not tiene_operacion:
            return _responder(IntencionResultado(tipo=IntencionTipo.SALUDO))

        if ranuras.acto_de_habla == "AGRADECIMIENTO" and not tiene_operacion:
            return _responder(IntencionResultado(tipo=IntencionTipo.AGRADECIMIENTO))

        # Creación y edición de usuarios por voz / texto
        if re.search(r"\b(?:quiero\s+|deseo\s+|voy\s+a\s+|vamos\s+a\s+|necesito\s+)?(?:crear|registrar|ingresar|dar\s+de\s+alta)\s+(?:un\s+)?(?:nuevo\s+)?usuario\b", texto_trabajo):
            return _responder(IntencionResultado(
                tipo=IntencionTipo.CREAR_USUARIO,
                almacen=ranuras.almacen_especifico,
            ))

        if re.search(r"\b(?:quiero\s+|deseo\s+|voy\s+a\s+|vamos\s+a\s+|necesito\s+)?(?:editar|modificar|actualizar|cambiar\s+(?:los\s+)?datos\s+de|cambiar\s+(?:los\s+)?permisos\s+de)\s+(?:este\s+|un\s+)?usuario\b", texto_trabajo):
            return _responder(IntencionResultado(
                tipo=IntencionTipo.EDITAR_USUARIO,
                almacen=ranuras.almacen_especifico,
            ))

        # Creación de producto nuevo por voz
        if re.search(r"\b(?:quiero\s+|deseo\s+|voy\s+a\s+|vamos\s+a\s+)?(?:crear|registrar|ingresar|dar\s+de\s+alta)\s+(?:un\s+)?(?:nuevo\s+)?producto\b", texto_trabajo):
            return _responder(IntencionResultado(
                tipo=IntencionTipo.CREAR_PRODUCTO,
                almacen=ranuras.almacen_especifico,
            ))

        # Intención de habilitar o reactivar producto sin producto en texto ni contexto
        es_frase_habilitar = bool(re.search(r"\b(?:quiero\s+|deseo\s+|voy\s+a\s+|vamos\s+a\s+)?(?:ir\s+a\s+)?(?:habilitar|activar|reactivar|dar\s+de\s+alta)\b", texto_trabajo))
        es_frase_inhabilitar = bool(re.search(r"\b(?:quiero\s+|deseo\s+|voy\s+a\s+|vamos\s+a\s+)?(?:ir\s+a\s+)?(?:inhabilitar|desactivar|archivar|dar\s+de\s+baja)\b", texto_trabajo))

        tiene_pronombre_o_demostrativo = bool(
            analisis.demostrativo_detectado
            or re.search(r"\b(lo|la|los|las|ese|esa|este|esta|aquel|aquella|dicho|lo\s+quiero|quiero\s+inhabilitarlo|quiero\s+habilitarlo|inhabilitarlo|habilitarlo|desactivarlo|activarlo)\b", texto_trabajo)
            or re.search(r"\b(?:inhabilit[aá]r?lo|desact[ií]v[aá]r?lo|arch[ií]v[aá]r?lo|inhabil[ií]talo|desact[ií]valo|arch[ií]valo|habilit[aá]r?lo|activ[aá]r?lo|reactiv[aá]r?lo|hab[ií]l[ií]talo|act[ií]valo|react[ií]valo)\b", texto_trabajo)
        )

        if es_frase_habilitar and not tiene_pronombre_o_demostrativo and not producto_en_contexto and not re.search(r"\b[A-Za-z]{2,5}[-_]\d{1,4}\b", texto_siglas):
            return _responder(IntencionResultado(
                tipo=IntencionTipo.HABILITAR_PRODUCTO,
                almacen=ranuras.almacen_especifico,
            ))

        if es_frase_inhabilitar and not tiene_pronombre_o_demostrativo and not producto_en_contexto and not re.search(r"\b[A-Za-z]{2,5}[-_]\d{1,4}\b", texto_siglas):
            return _responder(IntencionResultado(
                tipo=IntencionTipo.INHABILITAR_PRODUCTO,
                almacen=ranuras.almacen_especifico,
            ))

        # 5. Paso 5: Búsqueda de producto nombrado explícitamente en el texto actual
        candidatos_previos = getattr(estado_operativo, "candidatos", None) if estado_operativo else None

        prod_mencionado = None
        aclaracion_codigo = None
        lista_candidatos = []

        # Buscar producto individual si el alcance NO es GLOBAL, ALMACEN ni COLECCION con filtro,
        # o si la acción explícita es una mutación/préstamo (ej. "Préstale 2 cinta aislante a Carlos en el Almacén Principal")
        es_accion_mutacion = ranuras.accion in ("REGISTRAR_ENTRADA", "REGISTRAR_SALIDA", "PRESTAR", "DEVOLVER", "TRASLADAR")
        if es_accion_mutacion or (ranuras.alcance not in ("GLOBAL", "ALMACEN") and not (ranuras.alcance == "COLECCION" and ranuras.filtro_coleccion)):
            prod_mencionado, aclaracion_codigo, lista_candidatos = cls._resolver_producto_en_texto(
                texto_siglas, usuario, candidatos_previos=candidatos_previos
            )

        if prod_mencionado is not None:
            ranuras.alcance = "INDIVIDUAL"
            ranuras.filtro_coleccion = None
            aclaracion_codigo = None
            # FASE 3: Un producto explícito nuevo sustituye al anterior y limpia candidatos previos.
            if estado_operativo and estado_operativo.candidatos:
                estado_operativo.candidatos = []
        elif aclaracion_codigo and ranuras.alcance == "COLECCION" and ranuras.filtro_coleccion and not ranuras.accion:
            aclaracion_codigo = None

        if aclaracion_codigo:
            es_demostrativo_puro = bool(
                analisis.demostrativo_detectado
                or re.search(r"\b(ese|esa|este|esta|aquel|aquella|eso|esos|esas|estos|estas|aquellos|aquellas|su|sus|de\s+es[eoa]s?|de\s+aquell[oas]+|dicho\s+producto|el\s+producto\s+anterior|el\s+mismo|la\s+misma|esa\s+vaina|esta\s+vaina|la\s+vaina|vaina)\b", texto_trabajo)
            )
            contexto_en_candidatos = bool(
                producto_en_contexto and any(str(c.get("id")) == str(producto_en_contexto.id) for c in lista_candidatos)
            )
            es_accion_contextual = bool(
                contexto_en_candidatos
                and (
                    ranuras.accion in ("DEVOLVER", "PRESTAR", "REGISTRAR_ENTRADA", "REGISTRAR_SALIDA")
                    and re.search(r"\b(prestao|prestado|devoluci[oó]n|regreso|devuelvo|regr[eé]same|devu[eé]lveme)\b", texto_trabajo)
                )
            )
            prestamo_candidato = None
            if not contexto_en_candidatos and ranuras.accion == "DEVOLVER" and ranuras.destinatario:
                qs_p = Prestamo.objects.filter(
                    destinatario__icontains=ranuras.destinatario,
                    estado__in=[Prestamo.Estado.ABIERTO, Prestamo.Estado.DEVUELTO_PARCIAL],
                )
                prestamos_coincidentes = [p for p in qs_p if any(str(c.get("id")) == str(p.producto_id) for c in lista_candidatos)]
                if len(prestamos_coincidentes) == 1:
                    prestamo_candidato = prestamos_coincidentes[0].producto

            if producto_en_contexto and (es_demostrativo_puro or es_accion_contextual):
                prod_mencionado = producto_en_contexto
                aclaracion_codigo = None
                lista_candidatos = []
            elif prestamo_candidato:
                prod_mencionado = prestamo_candidato
                aclaracion_codigo = None
                lista_candidatos = []
            elif analisis.demostrativo_detectado and not producto_en_contexto:
                dem = sanitizar_demostrativo_salida(analisis.demostrativo_detectado)
                return _responder(IntencionResultado(
                    tipo=IntencionTipo.SOLICITAR_ACLARACION,
                    mensaje_aclaracion=(
                        f"Te refieres a '{dem}', pero no tengo un producto asociado actualmente (no indicaste cuál producto). "
                        "Por favor especifica el nombre o código interno del producto."
                    ),
                ))
            else:
                return _responder(IntencionResultado(
                    tipo=IntencionTipo.SOLICITAR_ACLARACION,
                    mensaje_aclaracion=aclaracion_codigo,
                    candidatos=lista_candidatos,
                ))

        # 5-bis: Selección plural de candidatos ante aclaración previa ("de los tres", "de todos ellos")
        if lista_candidatos and not aclaracion_codigo and candidatos_previos:
            if "UBICACION" in ranuras.conceptos_operativos or ranuras.contexto_fisico:
                return _responder(IntencionResultado(
                    tipo=IntencionTipo.CONSULTAR_UBICACION_COLECCION,
                    candidatos=lista_candidatos,
                    almacen=ranuras.almacen_especifico,
                    datos_extra={"candidatos_plurales": lista_candidatos},
                ))
            return _responder(IntencionResultado(
                tipo=IntencionTipo.CONSULTAR_UBICACION_COLECCION if (estado_operativo and estado_operativo.ultima_intencion == IntencionTipo.CONSULTAR_UBICACION.value) else IntencionTipo.PRODUCTOS_CON_STOCK,
                candidatos=lista_candidatos,
                almacen=ranuras.almacen_especifico,
                datos_extra={"candidatos_plurales": lista_candidatos},
            ))

        # ─────────────────────────────────────────────────────────────────────
        # CAPA DE INTENCIONES GLOBALES Y DE COLECCIÓN (INMUNE AL PRODUCTO ANTERIOR)
        # ─────────────────────────────────────────────────────────────────────
        # Se activa cuando el alcance es explícitamente GLOBAL, ALMACEN,
        # o cuando es una consulta sobre una COLECCION específica con filtro.
        # NUNCA se activa si el alcance es INDIVIDUAL para no secuestrar consultas
        # dependientes, demostrativos ni solicitudes de aclaración individual.
        if ranuras.alcance in ("GLOBAL", "ALMACEN", "COLECCION"):
            # A. Préstamos activos (Global - sin referencia a producto individual específico)
            es_prestamos_global_cond = bool(
                ranuras.condicion == "EN_PRESTAMO"
                or ranuras.metrica_prestamo != "GENERAL"
                or re.search(r"\b(hay\s+alg[uú]n\s+producto\s+en\s+pr[eé]stamos?|hay\s+algo\s+prestado|qu[eé]\s+tenemos\s+prestado|qu[eé]\s+est[aá]\s+prestado|qu[eé]\s+(?:productos|art[ií]culos|referencias)\s+est[aá]n\s+prestados?|mu[eé]strame\s+los\s+(?:productos|art[ií]culos)\s+que\s+est[aá]n\s+prestados?|cu[aá]les\s+son\s+los\s+pr[eé]stamos\s+activos?|pr[eé]stamos\s+activos|pr[eé]stamos\s+pendientes|qui[eé]n\s+tiene\s+(?:pr[eé]stamos|cosas\s+prestadas)|qu[eé]\s+pr[eé]stamos\s+hay)\b", texto_trabajo)
                or (re.search(r"\bqui[eé]n\s+tiene\b", texto_trabajo) and not re.search(r"\b(eso|ese|este|aquel|su|el|la|dicho)\b", texto_trabajo))
                or (re.search(r"\b(pr[eé]stamo|pr[eé]stamos|prestado|prestados)\b", texto_trabajo) and re.search(r"\b(hay\s+alg[uú]n|qu[eé]\s+tenemos|qu[eé]\s+hay|cu[aá]les\s+son|todos\s+los|qu[eé]\s+productos)\b", texto_trabajo))
            )
            if es_prestamos_global_cond and ranuras.accion not in ("REGISTRAR_ENTRADA", "REGISTRAR_SALIDA", "DEVOLVER", "PRESTAR"):
                datos = {}
                if ranuras.filtro_coleccion:
                    datos["filtro_nombre"] = ranuras.filtro_coleccion
                if hasattr(ranuras, "metrica_prestamo") and ranuras.metrica_prestamo:
                    datos["metrica"] = ranuras.metrica_prestamo
                return _responder(IntencionResultado(
                    tipo=IntencionTipo.PRESTAMOS_ACTIVOS,
                    almacen=ranuras.almacen_especifico,
                    datos_extra=datos or None,
                ))

            # B. Productos sin stock / agotados (Global determinista - PRODUCTOS_SIN_STOCK)
            es_consulta_sin_stock = bool(
                ranuras.condicion == "AGOTADO"
                or "STOCK_AGOTADO" in ranuras.conceptos_operativos
                or re.search(r"\b(?:qu[eé]|cu[aá]les)\s+(?:productos|art[ií]culos|referencias)\s+(?:no\s+tienen\s+stock|est[aá]n\s+agotad[oa]s?|se\s+agotaron|tienen\s+stock\s+cero|est[aá]n\s+en\s+cero|est[aá]n\s+sin\s+existencias?|sin\s+existencias?)\b", texto_trabajo)
                or re.search(r"\bqu[eé]\s+(?:productos|art[ií]culos)\s+(?:no\s+tienen|sin)\s+stock\b", texto_trabajo)
                or re.search(r"\b(?:productos|art[ií]culos)\s+(?:sin\s+stock|agotados|en\s+cero)\b", texto_trabajo)
                or re.search(r"\b(?:qu[eé]|cu[aá]les)\s+(?:est[aá]|est[aá]n|se)\s+agotad[oa]s?\b", texto_trabajo)
                or re.search(r"\b(?:qu[eé]|cu[aá]les)\s+se\s+agotaron\b", texto_trabajo)
                or re.search(r"\b(?:qu[eé]|cu[aá]les)\s+no\s+tienen\s+stock\b", texto_trabajo)
                or re.search(r"\bqu[eé]\s+est[aá]\s+en\s+cero\b", texto_trabajo)
                or (ranuras.alcance == "COLECCION" and re.search(r"\b(se\s+acabaron|acabaron|se\s+terminaron|agotad[oa]s?)\b", texto_trabajo))
            )
            if es_consulta_sin_stock:
                datos = {}
                if ranuras.filtro_coleccion:
                    datos["filtro_nombre"] = ranuras.filtro_coleccion
                elif ranuras.alcance == "COLECCION":
                    datos["filtrar_coleccion"] = True
                return _responder(IntencionResultado(
                    tipo=IntencionTipo.PRODUCTOS_SIN_STOCK,
                    almacen=ranuras.almacen_especifico,
                    datos_extra=datos or None,
                ))

            # C. Alertas de inventario / stock generales (Global - ALERTAS_STOCK)
            es_consulta_alertas_global = bool(
                (
                    ranuras.condicion == "ALERTAS"
                    or re.search(r"\b(?:alerta|alertas)\s+(?:de\s+)?(?:stock|inventario)\b", texto_siglas, re.IGNORECASE)
                    or re.search(r"\bqu[eé]\s+alertas\s+(?:hay|tenemos)\b", texto_siglas, re.IGNORECASE)
                    or re.search(r"\b(?:agotad[oa]s?\s+o\s+con\s+bajo\s+st(?:ock|op)|bajo\s+st(?:ock|op)\s+o\s+agotad[oa]s?)\b", texto_siglas, re.IGNORECASE)
                    or re.search(r"\b(?:en\s+)?barro\s+st(?:ock|op)\b", texto_siglas, re.IGNORECASE)
                    or re.search(r"\b(?:qu[eé]|cu[aá]les)\s+productos\s+(?:tienen|est[aá]n\s+en|hay\s+en)\s+bajo\s+st(?:ock|op)\b", texto_siglas, re.IGNORECASE)
                )
                and not re.search(r"\b(pelado|pelao|vacio|vac[ií]o|acab[oó]|escaso|nada)\b", texto_siglas, re.IGNORECASE)
            ) and not re.search(r"\b(por\s+debajo\s+del?\s+m[ií]nimo|no\s+tienen\s+stock|sin\s+stock|est[aá]n\s+agotados|se\s+agotaron|necesitan\s+reposici[oó]n)\b", texto_trabajo)
            if es_consulta_alertas_global:
                return _responder(IntencionResultado(
                    tipo=IntencionTipo.ALERTAS_STOCK,
                    almacen=ranuras.almacen_especifico,
                ))

            # D-0. Consulta de faltantes / compras / reposición integral («¿Qué hace falta por pedir?»)
            es_consulta_faltantes = bool(
                re.search(
                    r"\b(?:qu[eé]|cu[aá]l|cu[aá]les)\s+(?:hace\s+falta|falta|hacen\s+falta)\s+(?:por\s+|para\s+)?(?:pedir|comprar|ordenar|reponer|abastecer)\b",
                    texto_trabajo,
                )
                or re.search(r"\bqu[eé]\s+(?:tengo|tenemos|hay)\s+que\s+(?:pedir|comprar|ordenar|reponer|mandar\s+a\s+pedir)\b", texto_trabajo)
                or re.search(r"\bqu[eé]\s+(?:est[aá]|est[aá]n)\s+por\s+(?:acabarse|terminarse|agotarse)\b", texto_trabajo)
                or re.search(r"\bqu[eé]\s+debo\s+reponer\b", texto_trabajo)
                or re.search(r"\brecomendaci[oó]n\s+de\s+(?:compra|compras|reposici[oó]n)\b", texto_trabajo)
                or re.search(r"\bqu[eé]\s+hace\s+falta\s+(?:en\s+el\s+almac[eé]n|en\s+la\s+bodega|comprar|pedir)\b", texto_trabajo)
            )
            if es_consulta_faltantes:
                datos = {}
                if ranuras.filtro_coleccion:
                    datos["filtro_nombre"] = ranuras.filtro_coleccion
                return _responder(IntencionResultado(
                    tipo=IntencionTipo.CONSULTAR_FALTANTES_REPOSICION,
                    almacen=ranuras.almacen_especifico,
                    datos_extra=datos or None,
                ))

            # D. Productos por debajo del mínimo / reposición (Global determinista - PRODUCTOS_BAJO_STOCK)
            es_consulta_bajo_stock = bool(
                ranuras.condicion == "BAJO_STOCK"
                or "STOCK_BAJO" in ranuras.conceptos_operativos
                or re.search(r"\b(?:qu[eé]|cu[aá]les|los\s+que|mu[eé]strame\s+los\s+que)\s+(?:productos\s+|art[ií]culos\s+)?(?:est[aá]n\s+)?por\s+debajo\s+(?:del?\s+(?:stock\s+)?m[ií]nimo|de\s+lo\s+(?:que\s+)?(?:deber[ií]amos|hay\s+que)\s+tener|de\s+lo\s+necesario)\b", texto_trabajo)
                or re.search(r"\b(?:qu[eé]|cu[aá]les)\s+(?:productos\s+|art[ií]culos\s+)?(?:necesitan?|requieren?)\s+(?:reposici[oó]n|reponer|reabastecimiento|reabastecer)\b", texto_trabajo)
                or re.search(r"\b(?:qu[eé]|cu[aá]les)\s+(?:est[aá]n\s+)?por\s+debajo\s+del?\s+m[ií]nimo\b", texto_trabajo)
                or re.search(r"\b(?:productos|art[ií]culos)\s+(?:con\s+)?bajo\s+st(?:ock|op)\b", texto_trabajo)
                or re.search(r"\bpor\s+debajo\s+(?:del?\s+(?:stock\s+)?m[ií]nimo|de\s+lo\s+(?:que\s+)?(?:deber[ií]amos|hay\s+que)\s+tener)\b", texto_trabajo)
                or re.search(r"\bcu[aá]l\s+es\s+el\s+producto\s+con\s+stock\s+bajo\b", texto_trabajo)
            ) and not re.search(r"\b(agotad[oa]s?|sin\s+stock|no\s+tienen?\s+stock)\b", texto_trabajo)
            if es_consulta_bajo_stock:
                datos = {}
                if ranuras.filtro_coleccion:
                    datos["filtro_nombre"] = ranuras.filtro_coleccion
                return _responder(IntencionResultado(
                    tipo=IntencionTipo.PRODUCTOS_BAJO_STOCK,
                    almacen=ranuras.almacen_especifico,
                    datos_extra=datos or None,
                ))

            # E. Listar almacenes / bodegas
            es_consulta_almacenes = bool(
                re.search(
                    r"\b(?:qu[eé]|cu[aá]les)\s+(?:son\s+los\s+|tenemos\s+de\s+|hay\s+de\s+|existen\s+de\s+)?(?:almacenes|bodegas)\b",
                    texto_trabajo,
                )
                or re.search(r"\bqu[eé]\s+(?:almac[eé]n|bodega)\b", texto_trabajo)
                or re.search(r"\b(?:listar?|ver|mostrar|dime|cu[aá]les\s+son)\s+(?:los\s+|las\s+)?(?:almacenes|bodegas)(?:\s+disponibles|\s+activ[oa]s)?\b", texto_trabajo)
                or re.search(r"\bqu[eé]\s+(?:almacenes|bodegas)\s+(?:tenemos|hay|manejamos)\b", texto_trabajo)
                or re.search(r"\b(?:almacenes|bodegas)\s+(?:disponibles|activ[oa]s)\b", texto_trabajo)
            )
            if es_consulta_almacenes and not re.search(r"\b(d[oó]nde|est[aá]|queda|guardan?|guardad[oa]s?|metieron)\b", texto_trabajo) and ranuras.alcance != "COLECCION":
                return _responder(IntencionResultado(tipo=IntencionTipo.LISTAR_ALMACENES))

            # F. Ubicación de colección / referencias plurales
            es_consulta_ubicacion_coleccion = bool(
                (
                    "UBICACION" in ranuras.conceptos_operativos
                    or re.search(r"\b(d[oó]nde|ubicaci[oó]n|en\s+qu[eé]\s+(?:almac[eé]n|bodega|estante|rack|repisa|pasillo|gaveta|caj[oó]n|secci[oó]n)|d[oó]nde\s+(?:est[aá]n|se\s+guardan|se\s+encuentran|los\s+metieron)|d[oó]nde\s+guardamos|ub[ií]came)\b", texto_trabajo)
                )
                and (
                    ranuras.alcance == "COLECCION"
                    or re.search(r"\b(cada\s+(?:producto|uno|referencia|art[ií]culo)|cada\s+uno|todos\s+(?:ellos|los\s+productos|las\s+referencias|los\s+art[ií]culos|los\s+que\s+acabas|esos)|esas\s+referencias|esos\s+art[ií]culos|en\s+qu[eé]\s+estantes\s+se\s+encuentran|los\s+tres|de\s+los\s+tres|todos\s+ellos|de\s+todos)\b", texto_trabajo)
                )
                and ranuras.condicion not in ("DISPONIBLE", "AGOTADO", "BAJO_STOCK")
                and ranuras.accion not in ("REGISTRAR_ENTRADA", "REGISTRAR_SALIDA", "PRESTAR", "DEVOLVER")
            )
            if es_consulta_ubicacion_coleccion:
                return _responder(IntencionResultado(
                    tipo=IntencionTipo.CONSULTAR_UBICACION_COLECCION,
                    almacen=ranuras.almacen_especifico,
                ))

            # G. Listar catálogo general de productos
            es_consulta_catalogo = bool(
                re.search(r"\b(?:cu[aá]l\s+es\s+el\s+)?listado\s+de\s+productos\b", texto_trabajo)
                or re.search(r"\b(?:lista|listado|cat[aá]logo)\s+de\s+productos\b", texto_trabajo)
                or re.search(r"\bqu[eé]\s+productos\s+(?:hay|tenemos|existen|est[aá]n\s+registrados|manejamos)(?!\s+en\s+stock)\b", texto_trabajo)
                or (ranuras.alcance == "COLECCION" and ranuras.filtro_coleccion and re.search(r"\bqu[eé]\s+(?:\w+\s+)?(?:tenemos|hay|manejamos|existen)\b", texto_trabajo) and not re.search(r"\b(disponibles?|en\s+stock|con\s+stock)\b", texto_trabajo))
                or re.search(r"\bver\s+(?:el\s+)?(?:cat[aá]logo|listado|lista)\s+de\s+productos\b", texto_trabajo)
                or re.search(r"\bqu[eé]\s+hay\s+en\s+el\s+cat[aá]logo\b", texto_trabajo)
            ) and not re.search(r"\b(bajo\s+stock|en\s+stock|con\s+stock|agotad[oa]s?|sin\s+stock|prestado|prestados)\b", texto_trabajo)
            if es_consulta_catalogo:
                datos = {}
                if ranuras.filtro_coleccion:
                    datos["filtro_nombre"] = ranuras.filtro_coleccion
                return _responder(IntencionResultado(
                    tipo=IntencionTipo.LISTAR_CATALOGO,
                    almacen=ranuras.almacen_especifico,
                    datos_extra=datos or None,
                ))

            # H. Productos con existencias disponibles en stock (Global o por familia/colección)
            es_consulta_disponibles = bool(
                ranuras.condicion == "DISPONIBLE"
                or (ranuras.alcance == "COLECCION" and ranuras.filtro_coleccion and not es_consulta_sin_stock and not es_consulta_bajo_stock)
                or re.search(r"\bqu[eé]\s+productos\s+(?:hay|tenemos|est[aá]n|quedan)\s+en\s+stock\b", texto_trabajo)
                or re.search(r"\bqu[eé]\s+(?:hay|tenemos|queda)\s+en\s+stock\b", texto_trabajo)
                or re.search(r"\b(?:productos|art[ií]culos)\s+(?:en\s+stock|con\s+stock|disponibles|con\s+existencias?)\b", texto_trabajo)
                or re.search(r"\bqu[eé]\s+productos\s+(?:tienen|cuentan\s+con)\s+(?:stock|existencias?|disponibilidad)\b", texto_trabajo)
                or re.search(r"\bqu[eé]\s+(?:tenemos|hay)\s+disponible\b", texto_trabajo)
                or ("STOCK_DISPONIBLE" in ranuras.conceptos_operativos and ranuras.alcance in ("GLOBAL", "COLECCION", None))
            ) and not re.search(r"\b(bajo\s+stock|agotad[oa]s?|sin\s+stock|no\s+tienen?\s+stock|qu[eé]\s+falta|necesitan\s+reposici[oó]n|prestado|prestados)\b", texto_trabajo)
            if es_consulta_disponibles:
                datos = {}
                if ranuras.filtro_coleccion:
                    datos["filtro_nombre"] = ranuras.filtro_coleccion
                return _responder(IntencionResultado(
                    tipo=IntencionTipo.PRODUCTOS_CON_STOCK,
                    almacen=ranuras.almacen_especifico,
                    datos_extra=datos or None,
                ))

            # I. Resumen general de almacén / inventario («¿Qué hay en el almacén?»)
            es_consulta_resumen = bool(
                "RESUMEN_GENERAL" in ranuras.conceptos_operativos
                or re.search(
                    r"\b(?:dime\s+)?c[oó]mo\s+(?:va|anda|est[aá]|pinta)\s+(?:todo\s+en\s+)?(?:el\s+almac[eé]n|el\s+inventario|la\s+bodega)|resumen\s+del?\s+(?:almac[eé]n|inventario|bodega|d[ií]a|turno|jornada)|estado\s+del?\s+(?:almac[eé]n|inventario|bodega)|(?:consultar?|revisar?|ver|chequear?|dime)\s+(?:el\s+)?(?:inventario|almac[eé]n|bodega)|consulta\s+(?:el\s+)?(?:inventario|almac[eé]n|bodega)\b",
                    texto_trabajo,
                )
                or re.search(r"\bqu[eé]\s+hay\s+en\s+el\s+(?:almac[eé]n|bodega|inventario)\b", texto_trabajo)
                or (ranuras.almacen_especifico and re.search(r"\bqu[eé]\s+hay\s+en\b", texto_trabajo))
            ) and not re.search(r"\b(movimientos?|entradas?|salidas?|prestado|agotados?)\b", texto_trabajo)
            if es_consulta_resumen:
                return _responder(IntencionResultado(
                    tipo=IntencionTipo.CONSULTAR_RESUMEN_INVENTARIO,
                    almacen=ranuras.almacen_especifico,
                ))

            # J. Métricas generales de movimientos del almacén (del día / hoy / temporales)
            es_consulta_movimientos_general = bool(
                ("MOVIMIENTO" in ranuras.conceptos_operativos and ranuras.ventana_temporal is not None)
                or re.search(
                    r"\b(?:mu[eé]strame|dame|ver|consultar?|qu[eé])\s+(?:los\s+)?movimientos?\s+(?:de\s+|en\s+|durante\s+)?(?:ayer|hoy|la\s+semana|este\s+mes|el\s+mes|los\s+[uú]ltimos|[a-z]+)\b",
                    texto_trabajo,
                )
                or re.search(
                    r"\b(cu[aá]nt[ao]s?|qu[eé])\s+(?:entradas?|salidas?|despachos?|movimientos?|operaciones|transacciones?|actividades?|actividad|pr[eé]stamos?|devoluciones?)\s+(?:han\s+habido|hubo|se\s+hicieron|se\s+registraron|tuvimos|tuvieron|hay|tenemos)\b",
                    texto_trabajo,
                )
                or re.search(
                    r"\b(cu[aá]nt[ao]s?)\s+(?:entradas?|salidas?|despachos?|movimientos?|operaciones|transacciones?)\b",
                    texto_trabajo,
                )
                or re.search(
                    r"\b(?:qu[eé]|cu[aá]l|cu[aá]les)\s+(?:\w+\s+){0,3}(?:entr[oó]|entraron|ingres[oó]|ingresaron|lleg[oó]|llegaron|sali[oó]|salieron|se\s+despach[oó]|se\s+movi[oó])\b",
                    texto_trabajo,
                )
                or re.search(
                    r"\b(movimientos?\s+(?:de\s+)?(?:hoy|ayer)|movimientos?\s+del\s+d[ií]a|c[oó]mo\s+van\s+los\s+movimientos|c[oó]mo\s+va\s+la\s+movida|de\s+movida\s+la\s+vaina|el\s+voleo\s+(?:de\s+)?hoy|qu[eé]\s+voleo\s+hay|qu[eé]\s+se\s+ha\s+movido\s+hoy|c[oó]mo\s+va\s+el\s+voleo|c[oó]mo\s+estuvo\s+el\s+voleo|c[oó]mo\s+va\s+el\s+trabajo\s+hoy|qu[eé]\s+tal\s+estuvo\s+la\s+jornada|c[oó]mo\s+estuvo\s+la\s+jornada|c[oó]mo\s+estuvo\s+la\s+vuelta|c[oó]mo\s+va\s+la\s+vaina\s+hoy|c[oó]mo\s+anda\s+la\s+cosa\s+hoy|c[oó]mo\s+va\s+la\s+cosa\s+hoy|c[oó]mo\s+va\s+eso\s+hoy|c[oó]mo\s+va\s+la\s+vaina|c[oó]mo\s+est[aá]\s+la\s+vaina|qu[eé]\s+pas[oó]\s+hoy\s+en\s+(?:el\s+almac[eé]n|la\s+bodega))\b",
                    texto_trabajo,
                )
            )
            if es_consulta_movimientos_general:
                return _responder(IntencionResultado(
                    tipo=IntencionTipo.CONSULTAR_METRICAS_MOVIMIENTOS,
                    almacen=ranuras.almacen_especifico,
                ))

            # K. Creación de producto nuevo por voz
            if re.search(r"\b(?:quiero\s+|deseo\s+|voy\s+a\s+|vamos\s+a\s+)?(?:crear|registrar|ingresar|dar\s+de\s+alta)\s+(?:un\s+)?(?:nuevo\s+)?producto\b", texto_trabajo):
                return _responder(IntencionResultado(
                    tipo=IntencionTipo.CREAR_PRODUCTO,
                    almacen=ranuras.almacen_especifico,
                ))

            # L. Inhabilitar o archivar producto por voz (cuando no se nombró producto específico en texto global)
            if re.search(r"\b(?:quiero\s+|deseo\s+|voy\s+a\s+)?(?:inhabilitar|desactivar|archivar|dar\s+de\s+baja)\s+(?:un\s+)?producto\b", texto_trabajo) and not prod_mencionado:
                return _responder(IntencionResultado(
                    tipo=IntencionTipo.INHABILITAR_PRODUCTO,
                    almacen=ranuras.almacen_especifico,
                ))

        # ─────────────────────────────────────────────────────────────────────
        # CAPA DE ENTIDAD Y JUSTIFICACIÓN CONTEXTUAL
        # ─────────────────────────────────────────────────────────────────────
        # Solo se hereda el producto anterior si existe justificación conversacional legítima:
        # 1. Referencia demostrativa explícita ("ese producto", "su stock", "cuántos quedan de ese")
        # 2. Respuesta a aclaración pendiente o selección de candidato
        # 3. Continuación elíptica inequívoca de consulta sobre producto ("¿y en el almacén B?", "¿dónde está?")
        producto_objetivo = None
        if prod_mencionado:
            producto_objetivo = prod_mencionado
        else:
            tiene_justificacion = False

            # 1. Referencia demostrativa o pronominal explícita al producto en foco
            es_ref_demostrativa = bool(
                analisis.demostrativo_detectado
                or re.search(r"\b(ese|esa|este|esta|aquel|aquella|eso|esos|esas|estos|estas|aquellos|aquellas|su|sus|de\s+es[eoa]s?|de\s+aquell[oas]+|dicho\s+producto|el\s+producto\s+anterior|el\s+mismo|la\s+misma|esa\s+vaina|esta\s+vaina|la\s+vaina|vaina)\b", texto_trabajo)
                or re.search(r"\b(lo\s+quiero|la\s+quiero|quiero\s+(?:inhabilitarlo|habilitarlo|desactivarlo|activarlo|archivarlo)|inhabilitarlo|habilitarlo|desactivarlo|activarlo|archivarlo|darlo\s+de\s+(?:alta|baja))\b", texto_trabajo)
                or re.search(r"\b(?:inhabilit[aá]r?lo|desact[ií]v[aá]r?lo|arch[ií]v[aá]r?lo|inhabil[ií]talo|desact[ií]valo|arch[ií]valo|habilit[aá]r?lo|activ[aá]r?lo|reactiv[aá]r?lo|hab[ií]l[ií]talo|act[ií]valo|react[ií]valo)\b", texto_trabajo)
                or (re.search(r"\b(?:lo|la)\b", texto_trabajo) and re.search(r"\b(?:inhabilitar|desactivar|habilitar|activar|archivar)\b", texto_trabajo))
            )

            # 2. Respuesta a aclaración o propuesta pendiente
            responde_aclaracion = bool(
                candidatos_previos
                or (estado_operativo and estado_operativo.ultima_intencion == IntencionTipo.SOLICITAR_ACLARACION.value)
                or (estado_operativo and estado_operativo.propuesta_activa)
            )

            # 3. Continuación elíptica inequívoca cuando ya hay producto en contexto (sea RAM o sesión)
            es_continuacion_eliptica = False
            if producto_en_contexto is not None:
                dest_tokens = set(_quitar_tildes(ranuras.destinatario.lower()).split()) if ranuras.destinatario else set()
                alm_tokens = set(_quitar_tildes(ranuras.almacen_especifico.nombre.lower()).split()) if ranuras.almacen_especifico else set()
                tokens_consulta = [
                    w for w in _quitar_tildes(re.sub(r"[¿?¡!.,:;()\[\]\"'`]", " ", texto_siglas).lower()).split()
                    if w not in cls.PALABRAS_PARADA and not w.isdigit() and w not in dest_tokens and w not in alm_tokens and w not in {"bodega", "almacen", "bodegas", "almacenes"}
                ]
                intento_producto_especifico = bool(tokens_consulta) and not es_ref_demostrativa

                # Solo es elipsis legítima si NO hubo un intento fallido de nombrar otro producto
                if not intento_producto_especifico:
                    es_pregunta_dependiente = bool(
                        re.search(r"\b(d[oó]nde|ubicaci[oó]n|cu[aá]nto|cu[aá]ntos|quedan?|queda|stock|saldo|qui[eé]n\s+lo\s+tiene|qui[eé]n\s+tiene|prestado|prestao)\b", texto_trabajo)
                        or re.search(r"\b(?:y\s+(?:del?|en)\s+(?:el\s+)?(?:otro\s+almac[eé]n|otra\s+bodega|almac[eé]n\s+[A-Za-z0-9]+|bodega\s+[A-Za-z0-9]+))\b", texto_trabajo)
                        or (ranuras.almacen_especifico is not None and re.search(r"\b(?:y\s+en|en)\b", texto_trabajo))
                        or re.search(r"\b(qued[oó]\s+(?:pelado|vac[ií]o)|se\s+acab[oó]|no\s+hay\s+nada|pelado|vac[ií]o)\b", texto_trabajo)
                        or re.search(r"\b(c[oó]mo\s+se\s+ha\s+movido|c[oó]mo\s+ha\s+estado\s+saliendo|qu[eé]\s+movimiento\s+tuvo|qu[eé]\s+tanto\s+ha\s+salido|c[oó]mo\s+se\s+ha\s+despachado|cu[aá]nto\s+ha\s+salido|c[oó]mo\s+va|qu[eé]\s+pas[oó])\b", texto_trabajo)
                        or (ranuras.accion in ("REGISTRAR_SALIDA", "REGISTRAR_ENTRADA", "PRESTAR", "DEVOLVER") and (analisis.cantidad_detectada is not None or re.search(r"\b(inventario|devoluci[oó]n|regreso|prestao|prestado)\b", texto_trabajo)))
                    )
                    if es_pregunta_dependiente:
                        es_continuacion_eliptica = True
                else:
                    if estado_operativo:
                        estado_operativo.limpiar_foco_conversacional()

            if es_ref_demostrativa or responde_aclaracion or es_continuacion_eliptica:
                tiene_justificacion = True

            if tiene_justificacion:
                if producto_en_contexto:
                    producto_objetivo = producto_en_contexto
                elif candidatos_previos and len(candidatos_previos) > 0:
                    cand_id = candidatos_previos[0].get("id")
                    if cand_id:
                        cand_prod = Producto.objects.filter(id=cand_id, activo=True, archivado=False).first()
                        if cand_prod:
                            producto_objetivo = cand_prod

        # ─────────────────────────────────────────────────────────────────────
        # RESOLUCIÓN DE ACCIONES Y CONSULTAS SOBRE EL PRODUCTO
        # ─────────────────────────────────────────────────────────────────────
        if producto_objetivo:
            # 2.A Movimientos de producto específico (incluye "cómo va esa vaina", "qué pasó con esa vaina", etc.)
            if ("MOVIMIENTO" in ranuras.conceptos_operativos and ranuras.accion not in ("REGISTRAR_ENTRADA", "REGISTRAR_SALIDA", "PRESTAR", "DEVOLVER", "TRASLADAR")) or re.search(
                r"\b(c[oó]mo\s+se\s+ha\s+movido|qu[eé]\s+tanto\s+(?:ha\s+salido|se\s+ha\s+movido)|c[oó]mo\s+se\s+ha\s+despachado|movimientos?\s+del?\s+|qu[eé]\s+movimientos\s+(?:tiene|ha\s+tenido)|cu[aá]nto\s+ha\s+salido|c[oó]mo\s+va\s+(?:esa\s+vaina|este\s+cable|el\s+cable|este\s+producto|eso|aquello)|qu[eé]\s+pas[oó]\s+con\s+(?:esa\s+vaina|este\s+cable|el\s+cable|este\s+producto|eso|aquello)|qu[eé]\s+movimiento\s+tuvo|c[oó]mo\s+ha\s+estado\s+saliendo)\b",
                texto_trabajo,
            ):
                return _responder(IntencionResultado(
                    tipo=IntencionTipo.CONSULTAR_PRODUCTO_MOVIMIENTOS,
                    producto=producto_objetivo,
                    almacen=ranuras.almacen_especifico,
                ))

            # 2.B Consulta de ubicación del producto
            if ("UBICACION" in ranuras.conceptos_operativos or ranuras.contexto_fisico or re.search(
                r"\b(d[oó]nde|ubicaci[oó]n|localizaci[oó]n|estante|caj[oó]n|rack|pasillo|gaveta|repisa|secci[oó]n|posici[oó]n|en\s+qu[eé]\s+lado|d[oó]nde\s+guardan|d[oó]nde\s+se\s+encuentra)\b",
                texto_trabajo,
            )) and ranuras.accion not in ("REGISTRAR_ENTRADA", "REGISTRAR_SALIDA", "PRESTAR", "DEVOLVER", "TRASLADAR"):
                return _responder(IntencionResultado(
                    tipo=IntencionTipo.CONSULTAR_UBICACION,
                    producto=producto_objetivo,
                    almacen=ranuras.almacen_especifico,
                ))

            # 2.B-bis Consulta de préstamos activos / quién tiene el producto en préstamo
            if (
                ranuras.accion not in ("PRESTAR", "DEVOLVER", "REGISTRAR_ENTRADA", "REGISTRAR_SALIDA", "TRASLADAR")
                and not re.search(r"\b(te\s+regreso|regreso|devuelvo|devolver|devoluci[oó]n|d[eé]jame\s+un[oa]|p[aá]same\s+un[oa]\s+prestado)\b", texto_trabajo)
                and (
                    re.search(r"\b(pr[eé]stamos activos|qui[eé]n tiene|pr[eé]stamos pendientes|est[aá]\s+prestado|tiene\s+prestado|hay\s+presta(?:d[oa]|o)s?|queda\s+presta(?:d[oa]|o)s?|cu[aá]nto\s+(?:hay\s+|queda\s+)?presta(?:d[oa]|o)s?|cu[aá]ntas\s+unidades\s+(?:hay\s+)?presta(?:d[oa]|o)s?)\b", texto_trabajo)
                    or (ranuras.condicion == "EN_PRESTAMO" and re.search(r"\b(cu[aá]nto|qu[eé]|qui[eé]n|hay)\b", texto_trabajo))
                )
            ):
                return _responder(IntencionResultado(
                    tipo=IntencionTipo.PRESTAMOS_ACTIVOS,
                    producto=producto_objetivo,
                    almacen=ranuras.almacen_especifico,
                ))

            # 2.C Consulta de stock / existencias del producto
            if ("STOCK" in ranuras.conceptos_operativos or re.search(
                r"\b(stock|cu[aá]nto|cu[aá]ntos|cu[aá]ntas|hay|existencias?|quedan?|queda|disponible|disponibilidad|saldo|saldos|unidades?|piezas?|cantidad|cantidades|tenemos|contamos|quedan\s+muchos|quedan\s+pocos|quedan\s+bastantes|hay\s+bastante|hay\s+algo|todav[ií]a\s+hay|qued[oó]\s+pelado|qued[oó]\s+vac[ií]o|se\s+acab[oó]|se\s+acabado|acabado|acabados|acab[oó]|agotado|agotados|no\s+hay\s+nada|est[aá]\s+pelado|est[aá]\s+escaso|est[aá]\s+flojo|est[aá]\s+agotado)\b",
                texto_trabajo,
            )) and ranuras.accion not in ("REGISTRAR_ENTRADA", "REGISTRAR_SALIDA", "PRESTAR", "DEVOLVER", "TRASLADAR"):
                es_otro_alm = bool(re.search(r"\b(?:otro\s+almac[eé]n|otra\s+bodega)\b", texto_trabajo))
                return _responder(IntencionResultado(
                    tipo=IntencionTipo.CONSULTAR_STOCK,
                    producto=producto_objetivo,
                    almacen=ranuras.almacen_especifico,
                    datos_extra={"otro_almacen": True} if es_otro_alm else None,
                ))

            # 2.D Devolución de préstamo (evaluar ANTES de préstamo)
            if ranuras.accion == "DEVOLVER" or re.search(r"\b(devolver|devoluci[oó]n|te\s+regreso|regreso|devuelvo|ya\s+volvi[oó]|lo\s+traje\s+otra\s+vez)\b", texto_trabajo):
                match_dest = re.search(r"\b(?:de|por|a|para)\s+([A-Za-zÁ-ÿ\s]+?)(?:\s*$)", texto_siglas, re.IGNORECASE)
                dest = match_dest.group(1).strip() if match_dest else None
                if dest:
                    dest = re.sub(r"\b(pr[eé]stamo|pr[eé]stamos|de|el|la)\b", "", dest, flags=re.IGNORECASE).strip()

                return _responder(IntencionResultado(
                    tipo=IntencionTipo.DEVOLUCION,
                    producto=producto_objetivo,
                    almacen=ranuras.almacen_especifico,
                    destinatario=dest,
                    cantidad=analisis.cantidad_detectada,
                    requiere_confirmacion=True,
                ))

            # 2.E Préstamo
            if ranuras.accion == "PRESTAR" or (re.search(r"\b(prestar|presta|pr[eé]stale|prestale|preste|presten|pr[eé]stamo|pr[eé]stame|d[eé]jame\s+uno|d[eé]jame\s+una|p[aá]same\s+un[oa]\s+prestado)\b", texto_trabajo) and not re.search(r"\b(devolver|devoluci[oó]n|regreso|devuelvo)\b", texto_trabajo)):
                match_dest = re.search(r"\b(?:para|a)\s+([A-Za-zÁ-ÿ\s]+?)(?:\s+(?:por|motivo)|\s*$)", texto_siglas, re.IGNORECASE)
                dest = match_dest.group(1).strip() if match_dest else None
                if dest:
                    dest = re.sub(r"\b(?:en|desde|hacia)\s+(?:el\s+|la\s+)?(?:almac[eé]n|bodega)\b.*$", "", dest, flags=re.IGNORECASE).strip()
                    # Si el destinatario capturó el nombre del producto (ej. "a Yankar del segundo producto" o "del producto a Yankar")
                    # limpiar el nombre del producto o referencias ordinales
                    dest = re.sub(r"\b(?:del?|de\s+la|el|la)\s+(?:segundo|primer|tercer|segunda|primera|tercera)\s+producto\b", "", dest, flags=re.IGNORECASE).strip()
                    if producto_objetivo and producto_objetivo.nombre.lower() in dest.lower():
                        dest = re.sub(re.escape(producto_objetivo.nombre), "", dest, flags=re.IGNORECASE).strip()

                return _responder(IntencionResultado(
                    tipo=IntencionTipo.PRESTAMO,
                    producto=producto_objetivo,
                    almacen=ranuras.almacen_especifico,
                    cantidad=analisis.cantidad_detectada,
                    destinatario=dest or None,
                    requiere_confirmacion=True,
                ))

            # 2.E-bis Edición de producto identificado
            if re.search(r"\b(?:quiero\s+|deseo\s+|voy\s+a\s+)?(?:editar|modificar|actualizar)\s+(?:el\s+|este\s+|ese\s+)?producto\b", texto_trabajo) or re.search(r"\b(?:editar|modificar|actualizar)\b", texto_trabajo):
                return _responder(IntencionResultado(
                    tipo=IntencionTipo.EDITAR_PRODUCTO,
                    producto=producto_objetivo,
                    almacen=ranuras.almacen_especifico,
                ))

            # 2.E-ter Inhabilitar o archivar producto identificado
            if re.search(r"\b(?:quiero\s+|deseo\s+|voy\s+a\s+)?(?:ir\s+a\s+)?(?:inhabilitar|desactivar|archivar|dar\s+de\s+baja)\b", texto_trabajo) or re.search(r"\b(?:inhabilit[aá]r?lo|desact[ií]v[aá]r?lo|arch[ií]v[aá]r?lo|inhabil[ií]talo|desact[ií]valo|arch[ií]valo)\b", texto_trabajo):
                return _responder(IntencionResultado(
                    tipo=IntencionTipo.INHABILITAR_PRODUCTO,
                    producto=producto_objetivo,
                    almacen=ranuras.almacen_especifico,
                    requiere_confirmacion=True,
                ))

            # 2.E-ter-bis Habilitar o reactivar producto identificado
            if re.search(r"\b(?:quiero\s+|deseo\s+|voy\s+a\s+)?(?:ir\s+a\s+)?(?:habilitar|activar|reactivar|dar\s+de\s+alta)\b", texto_trabajo) or re.search(r"\b(?:habilit[aá]r?lo|activ[aá]r?lo|reactiv[aá]r?lo|hab[ií]l[ií]talo|act[ií]valo|react[ií]valo)\b", texto_trabajo):
                return _responder(IntencionResultado(
                    tipo=IntencionTipo.HABILITAR_PRODUCTO,
                    producto=producto_objetivo,
                    almacen=ranuras.almacen_especifico,
                    requiere_confirmacion=True,
                ))

            # 2.E-quater Traslado entre almacenes con producto identificado
            if ranuras.accion == "TRASLADAR" or re.search(r"\b(trasladar|transferir|transferencia|mover\s+a\s+(?:otro\s+almac[eé]n|otra\s+bodega)|pasar\s+a\s+(?:otro\s+almac[eé]n|otra\s+bodega))\b", texto_trabajo):
                almacen_destino = None
                almacen_origen = None
                texto_norm_completo = _quitar_tildes(texto_trabajo.lower())
                qs_alm = list(Almacen.objects.filter(activo=True, eliminado=False))

                # 1. Coincidencia de almacén destino (precedido por a, al, hacia, para)
                for alm in qs_alm:
                    alm_norm = _quitar_tildes(alm.nombre.lower().strip())
                    patron = rf"\b(?:a|al|hacia|para)\s+(?:el\s+|la\s+)?(?:almac[eé]n\s+|bodega\s+)?{re.escape(alm_norm)}\b"
                    if re.search(patron, texto_norm_completo):
                        almacen_destino = alm
                        break

                if not almacen_destino:
                    match_destino = re.search(
                        r"\b(?:a(?:l)?|hacia|para)\s+(?:el\s+|la\s+)?(?:almac[eé]n|bodega)\s+([A-Za-zÁ-ÿ0-9\s]+?)(?:\s*$|\s+\d|\s+del?\s+)",
                        texto_trabajo,
                    )
                    if match_destino:
                        nombre_dest = match_destino.group(1).strip()
                        texto_norm_dest = _quitar_tildes(nombre_dest.lower())
                        for alm in qs_alm:
                            if _quitar_tildes(alm.nombre.lower()) in texto_norm_dest or texto_norm_dest in _quitar_tildes(alm.nombre.lower()):
                                almacen_destino = alm
                                break

                # 2. Coincidencia de almacén origen (precedido por desde, del, de)
                for alm in qs_alm:
                    alm_norm = _quitar_tildes(alm.nombre.lower().strip())
                    patron_orig = rf"\b(?:desde|del?)\s+(?:el\s+|la\s+)?(?:almac[eé]n\s+|bodega\s+)?{re.escape(alm_norm)}\b"
                    if re.search(patron_orig, texto_norm_completo):
                        almacen_origen = alm
                        break

                # 3. Si no hay origen explícito pero ranuras detectó un almacén y no es el destino:
                if not almacen_origen:
                    if ranuras.almacen_especifico and (not almacen_destino or ranuras.almacen_especifico.id != almacen_destino.id):
                        almacen_origen = ranuras.almacen_especifico

                datos = {}
                if almacen_destino:
                    datos["almacen_destino_id"] = almacen_destino.id
                    datos["almacen_destino_nombre"] = almacen_destino.nombre

                return _responder(IntencionResultado(
                    tipo=IntencionTipo.TRASLADAR_PRODUCTO,
                    producto=producto_objetivo,
                    almacen=almacen_origen,
                    cantidad=analisis.cantidad_detectada,
                    requiere_confirmacion=True,
                    datos_extra=datos or None,
                ))

            # 2.F Entrada de stock
            if ranuras.accion == "REGISTRAR_ENTRADA" or re.search(r"\b(entrada|ingresar|ingreso|llegaron|lleg[oó]|recibir|aumentar|meter|mete|m[eé]telo|m[eé]tame|emboca|emb[oó]calo|agr[eé]gale|agregar)\b", texto_trabajo):
                return _responder(IntencionResultado(
                    tipo=IntencionTipo.REGISTRAR_ENTRADA,
                    producto=producto_objetivo,
                    almacen=ranuras.almacen_especifico,
                    cantidad=analisis.cantidad_detectada,
                    requiere_confirmacion=True,
                ))

            # 2.G Salida de stock
            if ranuras.accion == "REGISTRAR_SALIDA" or re.search(r"\b(salida|retirar|sacar|saca|s[aá]came|desp[aá]chame|despacha|desp[aá]chalo|m[aá]ndame|b[aá]jame|b[aá]jalo|entregar|p[aá]same|gastar)\b", texto_trabajo):
                match_dest = re.search(r"\b(?:para|a)\s+([A-Za-zÁ-ÿ\s]+?)(?:\s+(?:por|motivo)|\s*$)", texto_siglas, re.IGNORECASE)
                dest = match_dest.group(1).strip() if match_dest else None
                if dest:
                    dest = re.sub(r"\b(?:en|desde|hacia)\s+(?:el\s+|la\s+)?(?:almac[eé]n|bodega)\b.*$", "", dest, flags=re.IGNORECASE).strip()

                match_mot = re.search(r"\b(?:motivo|por)\s+([A-Za-zÁ-ÿ\s0-9]+)$", texto_siglas, re.IGNORECASE)
                mot = match_mot.group(1).strip() if match_mot else ranuras.motivo

                return _responder(IntencionResultado(
                    tipo=IntencionTipo.REGISTRAR_SALIDA,
                    producto=producto_objetivo,
                    almacen=ranuras.almacen_especifico,
                    cantidad=analisis.cantidad_detectada,
                    destinatario=dest,
                    motivo=mot,
                    requiere_confirmacion=True,
                ))

            # 2.H Búsqueda o consulta del producto si fue explícitamente nombrado
            # o si el usuario usó un demostrativo explícito hacia él
            if prod_mencionado or (analisis.demostrativo_detectado or re.search(r"\b(ese|este|aquel|eso)\b", texto_trabajo)):
                return _responder(IntencionResultado(
                    tipo=IntencionTipo.BUSCAR_PRODUCTO,
                    producto=producto_objetivo,
                    almacen=ranuras.almacen_especifico,
                ))

        # ─────────────────────────────────────────────────────────────────────
        # PRIORIDAD 3: Acciones transaccionales intentadas SIN producto
        # ─────────────────────────────────────────────────────────────────────
        if ranuras.accion == "REGISTRAR_SALIDA" or re.search(r"\b(salida|retirar|sacar|saca|s[aá]came|desp[aá]chame|despacha|desp[aá]chalo|m[aá]ndame|b[aá]jame|b[aá]jalo|entregar|p[aá]same|gastar)\b", texto_trabajo):
            dem = sanitizar_demostrativo_salida(analisis.demostrativo_detectado) if analisis.demostrativo_detectado else None
            if dem:
                msg = f"No tengo un producto asociado actualmente ({dem}, no indicaste cuál producto). Indícame qué producto deseas retirar."
            else:
                msg = "No tengo un producto asociado actualmente. Indícame qué producto deseas retirar."
            return _responder(IntencionResultado(
                tipo=IntencionTipo.SOLICITAR_ACLARACION,
                mensaje_aclaracion=msg,
            ))

        if ranuras.accion == "REGISTRAR_ENTRADA" or re.search(r"\b(entrada|ingresar|ingreso|llegaron|lleg[oó]|recibir|aumentar|meter|mete|m[eé]telo|m[eé]tame|emboca|emb[oó]calo|agr[eé]gale|agregar)\b", texto_trabajo):
            return _responder(IntencionResultado(
                tipo=IntencionTipo.SOLICITAR_ACLARACION,
                mensaje_aclaracion="Indica el nombre o código del producto para registrar la entrada.",
            ))

        if ranuras.accion == "DEVOLVER" or re.search(r"\b(devolver|devoluci[oó]n|te\s+regreso|regreso|devuelvo|ya\s+volvi[oó]|lo\s+traje\s+otra\s+vez)\b", texto_trabajo):
            match_dest = re.search(r"\b(?:de|por|a|para)\s+([A-Za-zÁ-ÿ\s]+?)(?:\s*$)", texto_siglas, re.IGNORECASE)
            dest = match_dest.group(1).strip() if match_dest else None
            if dest:
                dest = re.sub(r"\b(pr[eé]stamo|pr[eé]stamos|de|el|la)\b", "", dest, flags=re.IGNORECASE).strip()

            if dest:
                return _responder(IntencionResultado(
                    tipo=IntencionTipo.DEVOLUCION,
                    producto=None,
                    destinatario=dest,
                    cantidad=analisis.cantidad_detectada,
                    requiere_confirmacion=True,
                ))

            return _responder(IntencionResultado(
                tipo=IntencionTipo.SOLICITAR_ACLARACION,
                mensaje_aclaracion="Indica el producto o destinatario del préstamo que se está devolviendo.",
            ))

        if ranuras.accion == "PRESTAR" or (re.search(r"\b(prestar|pr[eé]stamo|pr[eé]stame|d[eé]jame\s+uno|d[eé]jame\s+una|p[aá]same\s+un[oa]\s+prestado)\b", texto_trabajo) and not re.search(r"\b(devolver|devoluci[oó]n|regreso|devuelvo)\b", texto_trabajo)):
            return _responder(IntencionResultado(
                tipo=IntencionTipo.SOLICITAR_ACLARACION,
                mensaje_aclaracion="Por favor indica qué producto deseas prestar.",
            ))

        if ranuras.accion == "TRASLADAR" or re.search(r"\b(trasladar|transferir|transferencia|mover\s+a\s+(?:otro\s+almac[eé]n|otra\s+bodega)|pasar\s+a\s+(?:otro\s+almac[eé]n|otra\s+bodega))\b", texto_trabajo):
            return _responder(IntencionResultado(
                tipo=IntencionTipo.SOLICITAR_ACLARACION,
                mensaje_aclaracion="Indica el nombre o código del producto que deseas trasladar entre almacenes.",
            ))

        if re.search(r"\b(c[oó]mo\s+se\s+ha\s+movido|qu[eé]\s+tanto\s+(?:ha\s+salido|se\s+ha\s+movido)|c[oó]mo\s+se\s+ha\s+despachado|movimientos?\s+del?\s+|qu[eé]\s+movimientos\s+(?:tiene|ha\s+tenido)|cu[aá]nto\s+ha\s+salido)\b", texto_trabajo):
            dem = sanitizar_demostrativo_salida(analisis.demostrativo_detectado) if analisis.demostrativo_detectado else None
            if dem:
                msg = f"Te refieres a '{dem}', pero no tengo un producto asociado actualmente. ¿De cuál producto deseas consultar los movimientos?"
            else:
                msg = "¿De cuál producto deseas consultar los movimientos?"
            return _responder(IntencionResultado(
                tipo=IntencionTipo.SOLICITAR_ACLARACION,
                mensaje_aclaracion=msg,
            ))

        # PRIORIDAD 4: Referencias demostrativas huérfanas / comodines ambiguos sin producto
        # Solo si no se trata de una consulta específica de ubicación o stock sin producto
        es_consulta_especifica = bool(
            "UBICACION" in ranuras.conceptos_operativos
            or ranuras.contexto_fisico
            or "STOCK" in ranuras.conceptos_operativos
            or re.search(r"\b(d[oó]nde|ubicaci[oó]n|localizaci[oó]n|estante|rack|pasillo|gaveta|repisa|caj[oó]n|stock|cu[aá]nto|cu[aá]ntos|cu[aá]ntas|quedan?|queda|hay|disponible|disponibilidad)\b", texto_trabajo)
        )

        es_referencia_demostrativa = (
            not es_consulta_especifica
            and (
                analisis.es_ambiguo
                or bool(re.search(r"\b(ese|este|aquel|eso|esos|esas|anterior|vaina|volado|de\s+esos)\b", texto_trabajo))
            )
        )
        if es_referencia_demostrativa:
            dem = sanitizar_demostrativo_salida(analisis.demostrativo_detectado)
            return _responder(IntencionResultado(
                tipo=IntencionTipo.SOLICITAR_ACLARACION,
                mensaje_aclaracion=(
                    f"Te refieres a '{dem}', pero no tengo un producto asociado actualmente (no indicaste cuál producto). "
                    "Por favor especifica el nombre o código interno del producto."
                ),
            ))

        # PRIORIDAD 5: Consultas específicas sin producto asociado (solicitar aclaración)

        # C. Consulta de ubicación sin producto
        if (
            "UBICACION" in ranuras.conceptos_operativos
            or ranuras.contexto_fisico
            or re.search(r"\b(d[oó]nde|ubicaci[oó]n|localizaci[oó]n|estante|caj[oó]n|rack|pasillo|gaveta|repisa|secci[oó]n|posici[oó]n|en qu[eé] lado|d[oó]nde\s+guardan|d[oó]nde\s+se\s+encuentra)\b", texto_trabajo)
        ) and not cls._es_fuera_de_dominio(texto_trabajo):
            return _responder(IntencionResultado(
                tipo=IntencionTipo.SOLICITAR_ACLARACION,
                mensaje_aclaracion="¿De cuál producto deseas conocer la ubicación?",
            ))

        # D. Consulta de stock sin producto
        if (
            "STOCK" in ranuras.conceptos_operativos
            or re.search(r"\b(stock|cu[aá]nto|cu[aá]ntos|cu[aá]ntas|hay|existencias?|quedan?|queda|disponible|disponibilidad|saldo|saldos|unidades?|piezas?|cantidad|cantidades|tenemos|contamos)\b", texto_trabajo)
        ) and not cls._es_fuera_de_dominio(texto_trabajo):
            return _responder(IntencionResultado(
                tipo=IntencionTipo.SOLICITAR_ACLARACION,
                mensaje_aclaracion="¿De cuál producto deseas consultar el stock?",
            ))

        # E. Consulta de préstamo sin producto
        if (
            "PRESTAMO" in ranuras.conceptos_operativos
            or re.search(r"\b(qui[eé]n\s+lo\s+tiene|qui[eé]n\s+tiene\s+presta[doa]|a\s+qui[eé]n\s+se\s+le\s+prest[oó]|est[aá]\s+presta[doa])\b", texto_trabajo)
        ) and not cls._es_fuera_de_dominio(texto_trabajo):
            return _responder(IntencionResultado(
                tipo=IntencionTipo.SOLICITAR_ACLARACION,
                mensaje_aclaracion="¿De cuál producto deseas consultar los préstamos?",
            ))

        # PRIORIDAD 6: Fallback lingüístico con separación semántica
        tipo_fallback = cls._clasificar_fallback(texto_trabajo)
        return _responder(IntencionResultado(
            tipo=tipo_fallback,
            datos_extra={"texto": texto_siglas},
        ))

    # ─────────────────────────────────────────────────────────────────────────
    # SEPARACIÓN SEMÁNTICA DE FALLBACK LINGÜÍSTICO (FASE 1)
    # ─────────────────────────────────────────────────────────────────────────

    PATRON_FUERA_DOMINIO = (
        r"\b("
        r"clima|lluvia|temperatura|tiempo\s+meteorol[oó]gico|hierve\s+el\s+agua|"
        r"m[uú]sica|canci[oó]n|canciones|reproduc(?:e|ir)|"
        r"pel[ií]cula|pel[ií]culas|cine|serie|series|"
        r"chiste|chistes|broma|bromas|cu[eé]ntame\s+un\s+chiste|"
        r"historia|historias|cuento|cuentos|"
        r"f[uú]tbol|partido|partidos|gol|goles|liga|campeonato|copa\s+del\s+mundo|"
        r"noticias?|peri[oó]dico|actualidad|qu[eé]\s+est[aá]\s+pasando\s+en\s+las\s+noticias|"
        r"pol[ií]tica|elecciones|presidente|gobierno|"
        r"d[oó]lar|d[oó]lares|euro|euros|tasa\s+de\s+cambio|divisa|divisas|cripto|bitcoin|"
        r"hora|qu[eé]\s+hora\s+es|cu[aá]l\s+es\s+la\s+hora|"
        r"planetas?\s+del\s+sistema\s+solar|astronom[ií]a|"
        r"qu[eé]\s+es\s+(?:el|la|los|las)\s+(?:sol|luna|tierra|universo|vida|amor|filosof[ií]a|muerte)|"
        r"qui[eé]n\s+(?:es|fue|descubri[oó]|invent[oó]|fund[oó]|conquist[oó]|gan[oó])\s+[a-zñáéíóú\s]+|"
        r"c[oó]mo\s+funciona\s+(?:el\s+mundo|el\s+universo|la\s+gravedad)|"
        r"cu[aá]nto\s+cuesta\s+(?:viajar|un\s+vuelo|un\s+hotel|un\s+viaje|ir\s+a)\s+[a-zñáéíóú\s]+|"
        r"viajar\s+a|turismo\s+en|vuelos?\s+a|"
        r"resuelve\s+(?:esta\s+)?(?:ecuaci[oó]n|f[oó]rmula|problema\s+matem[aá]tico)|"
        r"receta|recetas|cocinar|ingredientes\s+para"
        r")\b"
    )

    @classmethod
    def _es_fuera_de_dominio(cls, texto: str) -> bool:
        """Determina de forma determinista si la locución corresponde a un tema explícitamente fuera de dominio.
        Garantiza protección estricta si la locución incluye intenciones o términos de inventario.
        """
        # Salvaguarda: si menciona términos operativos o transaccionales del inventario, NO es fuera de dominio
        terminos_inventario = r"\b(stock|existencias?|inventario|almac[eé]n|bodega|pr[eé]stamo|pr[eé]stamos|devoluci[oó]n|entrada|salida|cables?|hdmi|utp|rj45|switch|ponchadora)\b"
        if re.search(terminos_inventario, texto, flags=re.IGNORECASE) and not re.search(r"\b(chiste|broma|canci[oó]n|pel[ií]cula|noticias)\b", texto, flags=re.IGNORECASE):
            return False
        return bool(re.search(cls.PATRON_FUERA_DOMINIO, texto, flags=re.IGNORECASE))

    @classmethod
    def _clasificar_fallback(cls, texto_trabajo: str) -> IntencionTipo:
        """Clasifica locuciones que no coincidieron con ninguna intención operativa.

        Orden estricto determinista:
        1. Baja comprensión / ruido / transcripción incompleta (SOLICITUD_NO_COMPRENDIDA)
        2. Fuera de dominio temático real (NO_INTENCION_INVENTARIO)
        3. Caso ambiguo o residual no reconocido (SOLICITUD_NO_COMPRENDIDA)
        """
        texto_limpio = re.sub(r"[¿?¡!.,:;()\[\]\"'`]", " ", texto_trabajo).strip().lower()
        texto_limpio = re.sub(r"\s+", " ", texto_limpio)
        tokens = texto_limpio.split()

        # PRIMERO: Entradas de baja comprensión o señales aisladas de Whisper
        tokens_baja_comprension = {
            "menos", "eh", "ah", "mmm", "mm", "um", "uh", "aja", "ajá",
            "que", "qué", "buen"
        }
        if not tokens or len(texto_limpio) <= 2:
            return IntencionTipo.SOLICITUD_NO_COMPRENDIDA

        # Si el texto completo es una sola palabra de baja comprensión o palabra muy corta sin sentido
        if len(tokens) == 1 and (tokens[0] in tokens_baja_comprension or len(tokens[0]) <= 3):
            return IntencionTipo.SOLICITUD_NO_COMPRENDIDA

        # Si todos los tokens pertenecen al vocabulario de baja comprensión / titubeos
        if set(tokens).issubset(tokens_baja_comprension | {"oye", "haku", "este", "bueno"}):
            return IntencionTipo.SOLICITUD_NO_COMPRENDIDA

        # SEGUNDO: Fuera de dominio real (temas no relacionados con inventario)
        if cls._es_fuera_de_dominio(texto_limpio):
            return IntencionTipo.NO_INTENCION_INVENTARIO

        # TERCERO: Detección parcial de intención incompleta (el usuario mencionó algo de
        # inventario pero sin la información mínima para clasificar). Distinguir de producto
        # ambiguo: si el texto menciona términos operativos genéricos, es intención incompleta.
        _mencion_inventario_generica = bool(
            re.search(r"\b(trasladar|mover|transferir|cambiar|pasar)\b", texto_limpio)
            or re.search(r"\b(producto|articulo|referencia|material|equipo|herramienta)\b", texto_limpio)
        )
        if _mencion_inventario_generica:
            return IntencionTipo.SOLICITUD_NO_COMPRENDIDA

        # CUARTO: Cualquier caso ambiguo devuelve SOLICITUD_NO_COMPRENDIDA
        return IntencionTipo.SOLICITUD_NO_COMPRENDIDA

    # ─────────────────────────────────────────────────────────────────────────
    # RESOLUCIÓN NO PRESUNTIVA DE CÓDIGOS Y NOMBRES (REGLA 5)
    # ─────────────────────────────────────────────────────────────────────────

    @classmethod
    def _resolver_producto_en_texto(
        cls,
        texto: str,
        usuario: UsuarioAudacIA,
        candidatos_previos: Optional[List[Dict[str, Any]]] = None,
    ) -> tuple[Optional[Producto], Optional[str], List[Dict[str, Any]]]:
        # Para operaciones de reactivación, inhabilitación o consulta directa de código, los administradores
        # deben poder resolver el producto por código aunque esté inactivo.
        qs = Producto.objects.all() if usuario.es_administrador else Producto.objects.filter(activo=True, archivado=False)
        if usuario.es_usuario_normal:
            if usuario.acceso_todos_almacenes:
                pass
            else:
                alms_accesibles = usuario.obtener_almacenes_accesibles()
                if alms_accesibles.exists():
                    qs = qs.filter(categoria__almacen_principal__in=alms_accesibles).distinct()
                else:
                    qs = qs.none()

        texto_clean = _quitar_tildes(re.sub(r"[¿?¡!.,:;()\[\]\"'`]", " ", texto).strip().lower())

        # Si el texto es explícitamente fuera de dominio, no resolver coincidencias de productos
        if cls._es_fuera_de_dominio(texto_clean):
            return None, None, []

        # 0. Resolución de candidatos previos en contexto (ej. "el primero", "el segundo", "opción 1")
        if candidatos_previos:
            if re.search(r"\b(primer[oa]|opci[oó]n\s+1|el\s+1)\b", texto_clean) and len(candidatos_previos) >= 1:
                cand = qs.filter(id=candidatos_previos[0]["id"]).first()
                if cand:
                    return cand, None, []
            elif re.search(r"\b(segund[oa]|opci[oó]n\s+2|el\s+2)\b", texto_clean) and len(candidatos_previos) >= 2:
                cand = qs.filter(id=candidatos_previos[1]["id"]).first()
                if cand:
                    return cand, None, []
            elif re.search(r"\b(tercer[oa]|opci[oó]n\s+3|el\s+3)\b", texto_clean) and len(candidatos_previos) >= 3:
                cand = qs.filter(id=candidatos_previos[2]["id"]).first()
                if cand:
                    return cand, None, []
            elif re.search(r"\b(de\s+los\s+tres|de\s+todos\s+ellos|de\s+todos|los\s+tres|todos\s+ellos|mu[eé]strame\s+los\s+tres|dime\s+de\s+los\s+tres)\b", texto_clean):
                return None, None, list(candidatos_previos)

        # 1. Búsqueda exacta de código por token (prioridad máxima de código exacto)
        palabras_texto = [w.strip() for w in re.split(r"[\s,;¿?¡!]+", texto) if w.strip()]
        for w in palabras_texto:
            w_code = w.upper().replace("_", "-")
            if "-" in w_code or any(c.isdigit() for c in w_code):
                p_exacto_code = qs.filter(codigo_interno__iexact=w_code).first()
                if p_exacto_code:
                    partes = w_code.split("-")
                    if len(partes) >= 2 and partes[-1].isdigit():
                        num_val = int(partes[-1])
                        candidatos_num = [
                            p for p in qs.filter(codigo_interno__istartswith=f"{partes[0]}-")
                            if p.codigo_interno.split("-")[-1].isdigit() and int(p.codigo_interno.split("-")[-1]) == num_val
                        ]
                        if len(candidatos_num) > 1 and len(partes[-1]) < 4:
                            pass
                        else:
                            return p_exacto_code, None, []
                    else:
                        return p_exacto_code, None, []

        # 1-bis. Búsqueda de patrón de código en el texto (ej. "CAB-0001", "CAB-001", "HER-1")
        match_code = re.search(r"\b([A-Za-z]{2,5})[-_\s]?(\d{1,4})\b", texto)
        if match_code:
            prefijo = match_code.group(1).upper()
            num_str = match_code.group(2)
            num_int = int(num_str)
            codigo_formateado = f"{prefijo}-{num_str}"
            
            # Buscar todos los productos del mismo prefijo cuyo sufijo termine o coincida numéricamente
            coincidencias = []
            for p in qs.filter(codigo_interno__istartswith=f"{prefijo}-"):
                try:
                    partes = p.codigo_interno.split("-")
                    if len(partes) >= 2 and int(partes[1]) == num_int:
                        coincidencias.append(p)
                except (ValueError, IndexError):
                    continue

            # Regla 5: Si hay múltiples coincidencias numéricas (ej. CAB-001 y CAB-0001), desambiguar
            if len(coincidencias) > 1:
                coincidencias_str = [p for p in coincidencias if p.codigo_interno.upper() == codigo_formateado.upper()]
                if len(coincidencias_str) == 1 and len(num_str) >= 4:
                    return coincidencias_str[0], None, []
                
                opciones = ", ".join(f"{p.codigo_interno} ({p.nombre})" for p in coincidencias)
                candidatos_lista = [
                    {"id": p.id, "nombre": p.nombre, "codigo": p.codigo_interno}
                    for p in coincidencias[:3]
                ]
                return (
                    None,
                    f"Encontré varias coincidencias para el código '{match_code.group(0)}': {opciones}. "
                    "¿Cuál de ellos deseas consultar?",
                    candidatos_lista,
                )
            if len(coincidencias) == 1:
                return coincidencias[0], None, []

        # 2. Búsqueda por coincidencia de nombre exacto delimitado por palabras
        coincidencias_nombre_exacto = []
        for p in qs:
            p_nom = _quitar_tildes(p.nombre.lower().strip())
            if re.search(r"\b" + re.escape(p_nom) + r"\b", texto_clean):
                coincidencias_nombre_exacto.append(p)

        if coincidencias_nombre_exacto:
            coincidencias_nombre_exacto.sort(key=lambda p: len(p.nombre), reverse=True)
            mejor_len = len(coincidencias_nombre_exacto[0].nombre)
            mejores = [p for p in coincidencias_nombre_exacto if len(p.nombre) == mejor_len]
            if len(mejores) == 1:
                return mejores[0], None, []
            elif len(mejores) > 1:
                opciones = ", ".join(f"{p.nombre} ({p.codigo_interno})" for p in mejores[:3])
                candidatos_lista = [
                    {"id": p.id, "nombre": p.nombre, "codigo": p.codigo_interno}
                    for p in mejores[:3]
                ]
                return (
                    None,
                    f"Encontré varios productos con nombre similar: {opciones}. ¿Cuál de ellos deseas consultar?",
                    candidatos_lista,
                )

        # 3. N-gramas y puntuación composicional con desambiguación y morfología
        tokens = [w for w in texto_clean.split() if w not in cls.PALABRAS_PARADA and not w.isdigit()]
        if not tokens:
            return None, None, []

        def _variantes(w: str) -> set[str]:
            v = {w}
            if len(w) >= 5 and w.endswith("es"):
                v.add(w[:-2])
            if len(w) >= 4 and w.endswith("s"):
                v.add(w[:-1])
            return v

        prod_data = []
        for p in qs:
            p_nom = _quitar_tildes(p.nombre.lower())
            p_toks = set(re.findall(r"\b[A-Za-z0-9ñ]{2,}\b", p_nom))
            prod_data.append((p, p_nom, p_toks))

        for n in range(len(tokens), 0, -1):
            candidatos_n_grama = []
            for i in range(len(tokens) - n + 1):
                gram_tokens = tokens[i : i + n]
                if not any(len(t) >= 3 for t in gram_tokens):
                    continue

                for p, p_nom, p_toks in prod_data:
                    coincide_gram = True
                    for gt in gram_tokens:
                        if not (_variantes(gt) & p_toks):
                            coincide_gram = False
                            break
                    if coincide_gram:
                        tokens_adicionales = sum(1 for t in tokens if (_variantes(t) & p_toks))
                        candidatos_n_grama.append((n, tokens_adicionales, len(p_toks), p))

            if candidatos_n_grama:
                mejores_por_prod = {}
                for n_gram_len, tok_add, p_toks_len, p in candidatos_n_grama:
                    score = (n_gram_len * 10) + (tok_add * 2)
                    if p.id not in mejores_por_prod or score > mejores_por_prod[p.id][0]:
                        mejores_por_prod[p.id] = (score, p)

                lista_ordenada = sorted(mejores_por_prod.values(), key=lambda x: x[0], reverse=True)
                top_score, top_p = lista_ordenada[0]

                candidatos_cercanos = [p for s, p in lista_ordenada if (top_score - s) <= 2]

                if len(candidatos_cercanos) == 1:
                    return top_p, None, []

                cands = [{"id": p.id, "nombre": p.nombre, "codigo": p.codigo_interno} for p in candidatos_cercanos[:3]]
                opciones = ", ".join(f"{p.nombre} ({p.codigo_interno})" for p in candidatos_cercanos[:3])
                return None, f"Encontré varias coincidencias para tu consulta: {opciones}. ¿Cuál de ellos deseas consultar?", cands

        return None, None, []
