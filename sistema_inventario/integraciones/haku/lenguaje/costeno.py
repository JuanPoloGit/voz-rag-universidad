"""Interpretación de expresiones coloquiales de la región Caribe (Barranquilla/Costa) para Haku Inventario.

REGLA DE DISEÑO ARQUITECTÓNICO — SEPARACIÓN ESTRICTA ENTRADA / SALIDA:
1. Capa de Ingesta y Comprensión (Entrada):
   - El dialecto caribeño (modismos: vaina, vuelta, voleo; expresiones coloquiales;
     variaciones fonéticas de Whisper: prestao->prestado, pa->para; elisión de plural)
     pertenece EXCLUSIVAMENTE a la interpretación determinista de intenciones operacionales.
2. Capa de Comunicación y Respuesta (Salida):
   - Haku NUNCA utiliza personalidad costeña ni responde con jerga regional ('ajá', 'compae', 'la vaina', etc.).
   - La respuesta generada (texto en pantalla y voz TTS) es estrictamente profesional, neutral,
     formal y empresarial, adecuada para un sistema de inventario organizacional.
   - Nunca se infiere que comprender el habla regional autorice a responder de manera informal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ExpresionAnalizada:
    """Resultado del preprocesamiento dialectal costeño."""
    texto_original: str
    texto_estandar: str
    vocativo_detectado: Optional[str]
    es_ambiguo: bool
    demostrativo_detectado: Optional[str]
    producto_mencionado: Optional[str]
    cantidad_detectada: Optional[int]


def sanitizar_demostrativo_salida(dem: Optional[str]) -> str:
    """Sanitiza cualquier demostrativo o comodín para que nunca contenga regionalismos costeños."""
    if not dem:
        return "ese producto"
    if re.search(r"\b(vaina|vuelta|cosa|movida|voleo|caleta|pelao|pelaa|aj[aá]|compae|compadre|mi\s+llave|cuadro|volado)\b", dem, re.IGNORECASE):
        return "ese producto"
    return dem


class ValidadorSalidaEmpresarial:
    """Validador y blindaje estricto que impide que Haku genere regionalismos en sus respuestas."""

    PALABRAS_PROHIBIDAS = [
        "ajá",
        "compae",
        "compadre",
        "mi llave",
        "cuadro",
        "la vaina",
        "esa vaina",
        "vaina",
        "esa vuelta",
        "la vuelta",
        "pelao",
        "pelaa",
        "voleo",
        "caleta",
        "ombe",
    ]

    PATRONES_PROHIBIDOS = [
        r"\baj[aá]\b",
        r"\bcompae\b",
        r"\bcompadre\b",
        r"\bmi\s+llave\b",
        r"\bcuadro\b",
        r"\b(?:la|esa|esta)\s+vaina\b",
        r"\bvaina\b",
        r"\b(?:la|esa|esta)\s+vuelta\b",
        r"\b(?:est[aá]|qued[oó])\s+pela[oa]\b",
        r"\bpela[oa]\b",
        r"\bpelaa\b",
        r"\bvoleo\b",
        r"\bcaleta\b",
        r"\bombe\b",
    ]

    @classmethod
    def detectar_regionalismos_prohibidos(cls, texto: str, nombres_permitidos: Optional[list[str]] = None) -> list[str]:
        """Detecta términos regionales prohibidos en la salida de Haku."""
        if not texto:
            return []

        texto_evaluar = texto
        if nombres_permitidos:
            for nombre in nombres_permitidos:
                if nombre:
                    texto_evaluar = re.sub(re.escape(nombre), "", texto_evaluar, flags=re.IGNORECASE)

        encontrados = []
        for patron in cls.PATRONES_PROHIBIDOS:
            match = re.search(patron, texto_evaluar, re.IGNORECASE)
            if match:
                encontrados.append(match.group(0))
        return encontrados

    @classmethod
    def asegurar_salida_empresarial(cls, texto: str, nombres_permitidos: Optional[list[str]] = None) -> str:
        """Verifica que el texto no contenga regionalismos. Si los hubiera, lanza un error de integridad."""
        prohibidos = cls.detectar_regionalismos_prohibidos(texto, nombres_permitidos)
        if prohibidos:
            raise ValueError(
                f"Contaminación dialectal detectada en la salida empresarial de Haku: {prohibidos}. "
                "La capa de salida debe ser estrictamente formal y neutral."
            )
        return texto


class InterpretadorCosteno:
    """Interpreta modismos y estructuras gramaticales de la costa caribeña colombiana."""

    # Vocativos y saludos comunes de apertura que no aportan a la intención
    VOCATIVOS = [
        r"^hey\s+haku\b",
        r"^ey\s+haku\b",
        r"^aj[aá]\s+haku\b",
        r"^oye\s+haku\b",
        r"^mira\s+haku\b",
        r"^viejo\s+haku\b",
        r"^haku\s+cuadro\b",
        r"^haku\s+mi\s+llave\b",
        r"^haku\s+compa\b",
        r"^haku\s+compae\b",
        r"^haku\b",
        r"^compadre\b",
        r"^compae\b",
        r"^compa\b",
        r"^mijo\b",
        r"^mi\s+llave\b",
        r"^cuadro\b",
        r"^papi\b",
        r"^ombe\b",
        r"^oye\b",
        r"^hey\b",
        r"^ey\b",
        r"^aj[aá]\b",
        r"^ve\s+ac[aá]\b",
        r"^mira\s+ve\b",
        r"^ven\s+ac[aá]\b",
        r"^h[aá]game\s+el\s+favor\b",
        r"^por\s+fa\b",
    ]

    # Normalizaciones fonéticas para transcripciones imperfectas de Faster-Whisper en habla caribeña
    NORMALIZACIONES_FONETICAS = [
        # Participios y adjetivos con pérdida de /d/ intervocálica
        (r"\bprestao\b", "prestado"),
        (r"\bpelao\b", "pelado"),
        (r"\bagotao\b", "agotado"),
        (r"\bsal[ií]o\b", "salido"),
        (r"\bmov[ií]o\b", "movido"),
        (r"\bdespachao\b", "despachado"),
        (r"\bguardao\b", "guardado"),
        (r"\btirao\b", "tirado"),
        (r"\bacabao\b", "acabado"),
        (r"\bquedao\b", "quedado"),

        # Verbo estar con aspiración de /s/ o apócope coloquial
        (r"\beht[aá]\b", "está"),
        (r"\beht[aá]n\b", "están"),
        (r"\b(d[oó]nde|d[oó]n|en\s+d[oó]nde)\s+ta\b", r"\1 está"),
        (r"\b(d[oó]nde|d[oó]n|en\s+d[oó]nde)\s+tan\b", r"\1 están"),
        (r"\bta\s+(pelao|pelado|escaso|flojo|vac[ií]o|agotao|agotado|bueno|malo)\b", r"está \1"),
        (r"\btan\s+(pelao|pelado|escaso|flojo|vac[ií]o|agotao|agotado)\b", r"están \1"),

        # Apócope de preposiciones
        (r"\bpa\b", "para"),
        (r"\bp'a\b", "para"),

        # Pérdida de /s/ plural en sustantivos y artículos clave
        (r"\blo\s+movimientos?\b", "los movimientos"),
        (r"\b(?:eto|ehto)\s+cables?\b", "estos cables"),
        (r"\b(?:eto|ehto)\b", "estos"),
        (r"\blo\s+cables?\b", "los cables"),
        (r"\blo\s+chismes?\b", "los chismes"),
        (r"\blo\s+aparatos?\b", "los aparatos"),
        (r"\blo\s+conectores?\b", "los conectores"),
        (r"\bla\s+ponchadoras\b", "las ponchadoras"),

        # Términos técnicos y de inventario con variaciones fonéticas o transcripciones Whisper
        (r"\b(?:barro|barros|bajo)\s+(?:stop|estoco|estock|estoc|top)\b", "bajo stock"),
        (r"\b(?:bajo\s+stop)\b", "bajo stock"),
        (r"\b(?:estoco|estock|estoc)\b", "stock"),
        (r"\b(?:almas[eé]n|arma[zs][eé]n|armazen)\b", "almacén"),
        (r"\b(?:almasenes|arma[zs]enes|armazenes)\b", "almacenes"),
        (r"\b(?:volegas|bolegas|bodejas)\b", "bodegas"),
        (r"\b(?:volega|bolega|bodeja)\b", "bodega"),
    ]

    # Demostrativos y comodines ambiguos que requieren resolución o aclaración
    DEMOSTRATIVOS_AMBIGUOS = [
        r"\b(?:esa\s+vaina|esta\s+vaina|la\s+vaina)\b",
        r"\b(?:este\s+volado|ese\s+volado)\b",
        r"\b(?:ese\s+chisme|este\s+chisme|los\s+chismes)\b",
        r"\b(?:ese\s+aparato|este\s+aparato)\b",
        r"\b(?:ese\s+cable|este\s+cable)\b",
        r"\b(?:esa\s+caja|esta\s+caja)\b",
        r"\b(?:ese\s+producto|este\s+producto|aquel\s+producto)\b",
        r"\b(?:el\s+anterior|lo\s+anterior|producto\s+anterior)\b",
        r"\b(?:de\s+eso|de\s+esos|de\s+esas|de\s+aquellos)\b",
        r"\b(?:eso\s+all[aá]|eso)\b",
        r"\b(?:el\s+coroto|los\s+corotos)\b",
        r"\b(?:esa\s+cosa|esta\s+cosa)\b",
    ]

    # Mapeos de giros verbales costeños a expresiones estándar de inventario
    GIROS_VERBALES = [
        # 1. Consultas generales de actividad / estado del almacén
        # Patrón: (verbo de estado: cómo va/anda/está/estuvo/fue) + (comodín: vaina/cosa/vuelta/movida/voleo/jugada/trabajo/día/jornada) + (tiempo: hoy/el día/la jornada)?
        (
            r"\b(?:c[oó]mo|qu[eé]\s+tal)\s+(?:va|anda|est[aá]|estuvo|fue|sigue|pinta)\s+(?:la\s+vaina|la\s+cosa|la\s+vuelta|la\s+movida|el\s+voleo|la\s+jugada|el\s+trabajo|el\s+d[ií]a|la\s+jornada|eso)\s*(?:de\s+hoy|hoy|el\s+d[ií]a|esta\s+mañana|ahorita)?\b",
            "cómo van los movimientos de hoy",
        ),
        (
            r"\b(?:c[oó]mo|qu[eé]\s+tal)\s+(?:estuvo|fue)\s+(?:la\s+vaina|la\s+cosa|la\s+vuelta|la\s+movida|el\s+voleo|la\s+jugada|el\s+trabajo|el\s+d[ií]a|la\s+jornada)\b",
            "cómo van los movimientos de hoy",
        ),
        (
            r"\bc[oó]mo\s+(?:va|anda|est[aá]|sigue)\s+(?:la\s+vaina|la\s+cosa|la\s+vuelta|la\s+movida|el\s+voleo)\s+(?:de\s+)?hoy\b",
            "cómo van los movimientos de hoy",
        ),
        (
            r"\bc[oó]mo\s+va\s+la\s+vaina\b",
            "cómo van los movimientos de hoy",
        ),
        (
            r"\bc[oó]mo\s+est[aá]\s+la\s+vaina\b",
            "cómo van los movimientos de hoy",
        ),
        (
            r"\bc[oó]mo\s+va\s+eso\s+(?:de\s+)?hoy\b",
            "cómo van los movimientos de hoy",
        ),
        (
            r"\bqu[eé]\s+(?:tanto\s+)?se\s+ha\s+movido\s+(?:de\s+)?hoy\b",
            "cómo van los movimientos de hoy",
        ),
        (
            r"\bqu[eé]\s+se\s+ha\s+movido\s+hoy\b",
            "cómo van los movimientos de hoy",
        ),
        (
            r"\bqu[eé]\s+voleo\s+hay\s+(?:de\s+)?hoy\b",
            "cómo van los movimientos de hoy",
        ),
        (
            r"\bc[oó]mo\s+va\s+el\s+d[ií]a(?:\s+de\s+hoy)?\b",
            "cómo van los movimientos de hoy",
        ),
        (
            r"\bqu[eé]\s+tan\s+movid[oa]\s+(?:estuvo|ha\s+estado|anda)\s+(?:el\s+d[ií]a|la\s+cosa)\b",
            "cómo van los movimientos de hoy",
        ),
        (
            r"\bc[oó]mo\s+(?:est[aá]|estuvo|va)\s+el\s+voleo\b",
            "cómo van los movimientos de hoy",
        ),

        # 2. Consultas de inventario y almacén (resumen)
        (r"\bdime\s+c[oó]mo\s+va\s+el\s+almac[eé]n\b", "cómo va el almacén"),
        (r"\bc[oó]mo\s+va\s+la\s+cosa\s+en\s+el\s+almac[eé]n\b", "cómo va el almacén"),
        (r"\bc[oó]mo\s+pinta\s+el\s+almac[eé]n\b", "cómo va el almacén"),
        (r"\bc[oó]mo\s+anda\s+el\s+inventario\b", "cómo va el almacén"),
        (r"\bdame\s+el\s+resumen\s+de\s+la\s+bodega\b", "resumen del almacén"),

        # 3. Consultas de movimientos por producto
        (r"\bqu[eé]\s+tanto\s+ha\s+salido\s+del?\b", "qué tanto ha salido de"),
        (r"\bqu[eé]\s+tanto\s+se\s+ha\s+movido\b", "cómo se ha movido"),
        (r"\bc[oó]mo\s+se\s+ha\s+despachado\b", "cómo se ha movido"),
        (r"\bqu[eé]\s+pas[oó]\s+con\s+(?:el|la|los|las|ese|esa|aquel|aquella)\b", "cómo se ha movido"),
        (r"\bqu[eé]\s+movimiento\s+tuvo\b", "cómo se ha movido"),
        (r"\bc[oó]mo\s+ha\s+estado\s+saliendo\b", "cómo se ha movido"),

        # 4. Stock y disponibilidad en bodega
        (r"\bcu[aá]nto\s+queda\s+de\b", "cuánto stock hay de"),
        (r"\bcu[aá]ntos\s+quedan\s+de\b", "cuánto stock hay de"),
        (r"\bcu[aá]ntas\s+quedan\s+de\b", "cuánto stock hay de"),
        (r"\bquedan\s+muchos\b", "cuánto stock queda"),
        (r"\bquedan\s+pocos\b", "cuánto stock queda"),
        (r"\bquedan\s+bastantes\b", "cuánto stock queda"),
        (r"\bhay\s+bastante\b", "cuánto stock queda"),
        (r"\bhay\s+algo\b", "cuánto stock queda"),
        (r"\btodav[ií]a\s+hay\b", "cuánto stock queda"),
        (r"\bqued[oó]\s+vac[ií]o\b", "alerta bajo stock"),
        (r"\bse\s+acab[oó]\b", "alerta bajo stock"),
        (r"\bno\s+hay\s+nada\b", "alerta bajo stock"),
        (r"\bqu[eé]\s+hay\s+en\s+caleta\b", "qué hay disponible"),

        # Alertas coloquiales ("está escaso", "está flojo", "está pelado")
        (r"\best[aá]\s+escaso\b", "alerta bajo stock"),
        (r"\best[aá]\s+flojo\b", "alerta bajo stock"),
        (r"\best[aá]\s+pelado\b", "alerta bajo stock"),
        (r"\bqued[oó]\s+pelado\b", "alerta bajo stock"),

        # Ubicación
        (r"\bd[oó]nde\s+(?:dejaron|metieron|tiraron|guardaron)\s+(?:el|la|los|las)\b", "dónde está "),
        (r"\ben\s+qu[eé]\s+lado\s+(?:metieron|dejaron|tiraron|guardaron|est[aá]n?)\b", "dónde está "),

        # PRÉSTAMO: préstame, déjame uno, pásame uno prestado
        (r"\bpr[eé]stame\b", "préstamo de"),
        (r"\bd[eé]jame\s+uno\b", "préstamo de 1"),
        (r"\bd[eé]jame\s+una\b", "préstamo de 1"),
        (r"\bp[aá]same\s+un[oa]\s+prestado\b", "préstamo de 1"),

        # SALIDA: sácame, despáchame, mándame, bájame, pásame, saca, saca ahí, sácalo, despáchalo, bájalo
        (r"\bs[aá]came\b", "salida de"),
        (r"\bdesp[aá]chame\b", "salida de"),
        (r"\bm[aá]ndame\b", "salida de"),
        (r"\bb[aá]jame\b", "salida de"),
        (r"\bsaca\s+ah[ií]\b", "salida de"),
        (r"\bs[aá]calo\b", "salida de"),
        (r"\bdesp[aá]chalo\b", "salida de"),
        (r"\bb[aá]jalo\b", "salida de"),
        (r"\bp[aá]same\b", "salida de"),

        # ENTRADA: mete, mételo, emboca, embócalo, agrégale, llegó, llegaron
        (r"\b(?:mete|metan|emboca)\b", "entrada de"),
        (r"\bm[eé]telo\b", "entrada de"),
        (r"\bemb[oó]calo\b", "entrada de"),
        (r"\bagr[eé]gale\b", "entrada de"),
        (r"\blleg[oó]\b", "entrada de"),
        (r"\bllegaron\b", "entrada de"),

        # DEVOLUCIÓN: te regreso, ya volvió, devuelvo, lo traje otra vez
        (r"\bte\s+regreso\b", "devolución de"),
        (r"\bya\s+volvi[oó]\b", "devolución de"),
        (r"\bdevuelvo\b", "devolución de"),
        (r"\blo\s+traje\s+otra\s+vez\b", "devolución de"),

        # Búsqueda
        (r"\bb[uú]scame\s+el\s+([a-zA-Z0-9\s]+?)\s+ese\s+([a-zA-Z0-9\s]+)\b", r"buscar \1 \2"),
    ]

    @classmethod
    def normalizar_fonetica_whisper(cls, texto: str) -> str:
        """Corrige errores fonéticos frecuentes de Faster-Whisper en habla caribeña."""
        if not texto:
            return ""
        resultado = texto
        for pat, rep in cls.NORMALIZACIONES_FONETICAS:
            resultado = re.sub(pat, rep, resultado, flags=re.IGNORECASE)
        return resultado

    @classmethod
    def normalizar(cls, texto: str) -> ExpresionAnalizada:
        """Limpia el texto, detecta si es ambiguo y aisla la intención."""
        if not texto:
            return ExpresionAnalizada(
                texto_original="",
                texto_estandar="",
                vocativo_detectado=None,
                es_ambiguo=True,
                demostrativo_detectado=None,
                producto_mencionado=None,
                cantidad_detectada=None,
            )

        t_clean = cls.normalizar_fonetica_whisper(texto).strip()
        vocativo = None

        # 1. Quitar vocativos iniciales (ej. "Haku búscame...", "Oye Haku...")
        for pat in cls.VOCATIVOS:
            m = re.search(pat, t_clean, flags=re.IGNORECASE)
            if m:
                vocativo = m.group(0).strip()
                t_clean = re.sub(pat, "", t_clean, flags=re.IGNORECASE).strip()
                # Limpiar comas iniciales que queden: ", búscame..."
                t_clean = re.sub(r"^[,\s]+", "", t_clean)
                break

        # 2. Aplicar giros verbales costeños
        estandarizado = t_clean
        for pat, reemplazo in cls.GIROS_VERBALES:
            estandarizado = re.sub(pat, reemplazo, estandarizado, flags=re.IGNORECASE)

        # 3. Detectar demostrativos ambiguos ("esa vaina", "ese cable", "de esos", etc.)
        demostrativo = None
        for pat in cls.DEMOSTRATIVOS_AMBIGUOS:
            m = re.search(pat, estandarizado, flags=re.IGNORECASE)
            if m:
                demostrativo = m.group(0).strip()
                break

        # 4. Extraer cantidad si existe
        match_cant = re.search(r"\b(\d+)\b", estandarizado)
        cantidad = int(match_cant.group(1)) if match_cant else None
        if not cantidad:
            palabras_num = {
                "un": 1, "una": 1, "uno": 1, "dos": 2, "tres": 3,
                "cuatro": 4, "cinco": 5, "seis": 6, "diez": 10,
            }
            for p, v in palabras_num.items():
                if re.search(rf"\b{p}\b", estandarizado, flags=re.IGNORECASE):
                    cantidad = v
                    break

        # 5. Detección de ambigüedad
        # Si la frase contiene un demostrativo comodín ("esa vaina", "ese cable", "de esos")
        # y NO especifica marcas, modelos o códigos concretos (ej. no dice "HDMI", "UTP", "RJ45", "CAB-0001")
        es_ambiguo = False
        terminos_tecnicos = re.search(
            r"\b(HDMI|UTP|RJ45|Switch|Ponchadora|Patch\s*Cord|VGA|Router|Cat\s*6|CAB-\d+|HER-\d+|RED-\d+)\b",
            estandarizado,
            flags=re.IGNORECASE,
        )

        if demostrativo is not None and not terminos_tecnicos:
            # Ejemplo: "pásame ese cable", "búscame esa vaina", "sácame tres de esos"
            es_ambiguo = True

        return ExpresionAnalizada(
            texto_original=texto,
            texto_estandar=estandarizado,
            vocativo_detectado=vocativo,
            es_ambiguo=es_ambiguo,
            demostrativo_detectado=demostrativo,
            producto_mencionado=terminos_tecnicos.group(0) if terminos_tecnicos else None,
            cantidad_detectada=cantidad,
        )
