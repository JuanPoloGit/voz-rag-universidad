"""
Módulo de resolución y análisis de expresiones temporales en lenguaje natural
para el sistema de inventario AudacIA (HAKU Inventario).

Soporta expresiones temporales relativas y absolutas en español:
- "hoy"
- "ayer"
- "esta semana"
- "semana pasada"
- "semana pasada hasta hoy"
- "últimos N días" (ej. "últimos 7 días")
- "este mes"
- "mes pasado"
- Meses específicos (ej. "octubre", "noviembre", "enero")
- Intervalos explícitos (ej. "entre el lunes y el viernes", "desde el 1 de octubre hasta hoy")

Reglas semánticas:
- Zona horaria de referencia: America/Bogota (Colombia, UTC-5).
- Límites coherentes: inicio inclusivo (start_dt) y final exclusivo (end_dt).
"""

from __future__ import annotations

import calendar
import datetime
import re
from dataclasses import dataclass
from typing import Optional, Tuple
import zoneinfo

from django.utils import timezone

ZONA_BOGOTA = zoneinfo.ZoneInfo("America/Bogota")

DIAS_SEMANA_MAP = {
    "lunes": 0,
    "martes": 1,
    "miercoles": 2,
    "miércoles": 2,
    "jueves": 3,
    "viernes": 4,
    "sabado": 5,
    "sábado": 5,
    "domingo": 6,
}

MESES_MAP = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}


@dataclass(frozen=True)
class IntervaloTemporal:
    """Intervalo de tiempo resuelto con límite inferior inclusivo y superior exclusivo."""
    etiqueta: str
    inicio: datetime.datetime
    fin: datetime.datetime
    descripcion_legible: str


def obtener_ahora_local(ref_dt: Optional[datetime.datetime] = None) -> datetime.datetime:
    """Retorna el datetime actual en la zona horaria de Bogotá."""
    if ref_dt is None:
        ref_dt = timezone.now()
    if timezone.is_aware(ref_dt):
        return ref_dt.astimezone(ZONA_BOGOTA)
    return ref_dt.replace(tzinfo=ZONA_BOGOTA)


def resolver_intervalo_temporal(
    texto: str,
    ref_dt: Optional[datetime.datetime] = None,
) -> Optional[IntervaloTemporal]:
    """Interpreta expresiones temporales en español y devuelve un IntervaloTemporal.
    
    Si no detecta ninguna expresión temporal, retorna None.
    """
    if not texto:
        return None

    ahora = obtener_ahora_local(ref_dt)
    hoy_date = ahora.date()
    texto_norm = texto.lower()

    # 1. "semana pasada hasta hoy" / "desde la semana pasada hasta hoy"
    if re.search(r"\b(?:desde\s+)?(?:la\s+)?semana\s+pasada\s+hasta\s+(?:hoy|la\s+fecha|ahora)\b", texto_norm):
        lunes_esta_semana = hoy_date - datetime.timedelta(days=hoy_date.weekday())
        lunes_semana_pasada = lunes_esta_semana - datetime.timedelta(days=7)
        inicio = datetime.datetime.combine(lunes_semana_pasada, datetime.time.min, tzinfo=ZONA_BOGOTA)
        fin = ahora
        return IntervaloTemporal(
            etiqueta="SEMANA_PASADA_HASTA_HOY",
            inicio=inicio,
            fin=fin,
            descripcion_legible=f"desde la semana pasada ({lunes_semana_pasada.strftime('%d/%m/%Y')}) hasta hoy ({hoy_date.strftime('%d/%m/%Y')})",
        )

    # 2. "semana pasada" (semana calendario anterior completa: lunes a domingo)
    if re.search(r"\b(?:la\s+)?semana\s+pasada\b", texto_norm):
        lunes_esta_semana = hoy_date - datetime.timedelta(days=hoy_date.weekday())
        lunes_semana_pasada = lunes_esta_semana - datetime.timedelta(days=7)
        domingo_semana_pasada = lunes_semana_pasada + datetime.timedelta(days=6)
        inicio = datetime.datetime.combine(lunes_semana_pasada, datetime.time.min, tzinfo=ZONA_BOGOTA)
        fin = datetime.datetime.combine(domingo_semana_pasada + datetime.timedelta(days=1), datetime.time.min, tzinfo=ZONA_BOGOTA)
        return IntervaloTemporal(
            etiqueta="SEMANA_PASADA",
            inicio=inicio,
            fin=fin,
            descripcion_legible=f"la semana pasada ({lunes_semana_pasada.strftime('%d/%m/%Y')} al {domingo_semana_pasada.strftime('%d/%m/%Y')})",
        )

    # 3. "esta semana" (desde el lunes de la semana actual hasta ahora)
    if re.search(r"\b(?:esta\s+semana|en\s+la\s+semana|semana\s+actual)\b", texto_norm):
        lunes_esta_semana = hoy_date - datetime.timedelta(days=hoy_date.weekday())
        inicio = datetime.datetime.combine(lunes_esta_semana, datetime.time.min, tzinfo=ZONA_BOGOTA)
        fin = ahora
        return IntervaloTemporal(
            etiqueta="ESTA_SEMANA",
            inicio=inicio,
            fin=fin,
            descripcion_legible=f"esta semana (desde el lunes {lunes_esta_semana.strftime('%d/%m/%Y')} hasta hoy)",
        )

    # 4. "últimos N días" (ej. "últimos 7 días", "ultimos 15 dias", "últimos siete días")
    match_ultimos = re.search(r"\b[uú]ltimos?\s+(\d+|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|quince|treinta)\s+d[ií]as\b", texto_norm)
    if match_ultimos:
        val_str = match_ultimos.group(1)
        num_map = {
            "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6,
            "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "quince": 15, "treinta": 30,
        }
        num_dias = int(val_str) if val_str.isdigit() else num_map.get(val_str, 7)
        inicio = ahora - datetime.timedelta(days=num_dias)
        fin = ahora
        return IntervaloTemporal(
            etiqueta="ULTIMOS_DIAS",
            inicio=inicio,
            fin=fin,
            descripcion_legible=f"los últimos {num_dias} días (desde {inicio.strftime('%d/%m/%Y')} hasta hoy)",
        )

    # 5. "ayer"
    if re.search(r"\bayer\b", texto_norm):
        ayer_date = hoy_date - datetime.timedelta(days=1)
        inicio = datetime.datetime.combine(ayer_date, datetime.time.min, tzinfo=ZONA_BOGOTA)
        fin = datetime.datetime.combine(hoy_date, datetime.time.min, tzinfo=ZONA_BOGOTA)
        return IntervaloTemporal(
            etiqueta="AYER",
            inicio=inicio,
            fin=fin,
            descripcion_legible=f"ayer ({ayer_date.strftime('%d/%m/%Y')})",
        )

    # 6. "mes pasado" (mes calendario anterior completo)
    if re.search(r"\b(?:el\s+)?mes\s+pasado\b", texto_norm):
        primer_dia_este_mes = hoy_date.replace(day=1)
        ultimo_dia_mes_pasado = primer_dia_este_mes - datetime.timedelta(days=1)
        primer_dia_mes_pasado = ultimo_dia_mes_pasado.replace(day=1)
        inicio = datetime.datetime.combine(primer_dia_mes_pasado, datetime.time.min, tzinfo=ZONA_BOGOTA)
        fin = datetime.datetime.combine(primer_dia_este_mes, datetime.time.min, tzinfo=ZONA_BOGOTA)
        nombre_mes = calendar.month_name[primer_dia_mes_pasado.month].capitalize()
        return IntervaloTemporal(
            etiqueta="MES_PASADO",
            inicio=inicio,
            fin=fin,
            descripcion_legible=f"el mes pasado ({primer_dia_mes_pasado.strftime('%B %Y')})",
        )

    # 7. "este mes"
    if re.search(r"\b(?:este\s+mes|mes\s+actual|en\s+el\s+mes)\b", texto_norm):
        primer_dia_este_mes = hoy_date.replace(day=1)
        inicio = datetime.datetime.combine(primer_dia_este_mes, datetime.time.min, tzinfo=ZONA_BOGOTA)
        fin = ahora
        return IntervaloTemporal(
            etiqueta="ESTE_MES",
            inicio=inicio,
            fin=fin,
            descripcion_legible=f"este mes (desde el 01/{primer_dia_este_mes.strftime('%m/%Y')} hasta hoy)",
        )

    # 8. Mes específico por nombre (ej. "octubre", "en octubre", "de octubre", "en octubre de 2026")
    for nombre_mes, num_mes in MESES_MAP.items():
        patron_mes = rf"\b(?:en|de|del\s+mes\s+de)?\s*{nombre_mes}(?:\s+(?:de|del)?\s*(\d{{4}}))?\b"
        match_mes = re.search(patron_mes, texto_norm)
        if match_mes:
            anio_match = match_mes.group(1)
            anio = int(anio_match) if anio_match else hoy_date.year
            dias_en_mes = calendar.monthrange(anio, num_mes)[1]
            inicio = datetime.datetime(anio, num_mes, 1, 0, 0, 0, tzinfo=ZONA_BOGOTA)
            if anio == hoy_date.year and num_mes == hoy_date.month:
                fin = ahora
            else:
                mes_sig = 1 if num_mes == 12 else num_mes + 1
                anio_sig = anio + 1 if num_mes == 12 else anio
                fin = datetime.datetime(anio_sig, mes_sig, 1, 0, 0, 0, tzinfo=ZONA_BOGOTA)
            return IntervaloTemporal(
                etiqueta="MES_ESPECIFICO",
                inicio=inicio,
                fin=fin,
                descripcion_legible=f"{nombre_mes.capitalize()} de {anio}",
            )

    # 9. Intervalo explícito entre días de la semana: "entre el <dia1> y el <dia2>"
    match_dias = re.search(r"\bentre\s+(?:el\s+)?([a-záéíóú]+)\s+y\s+(?:el\s+)?([a-záéíóú]+)\b", texto_norm)
    if match_dias:
        d1_str = match_dias.group(1)
        d2_str = match_dias.group(2)
        if d1_str in DIAS_SEMANA_MAP and d2_str in DIAS_SEMANA_MAP:
            idx1 = DIAS_SEMANA_MAP[d1_str]
            idx2 = DIAS_SEMANA_MAP[d2_str]
            lunes_actual = hoy_date - datetime.timedelta(days=hoy_date.weekday())
            fecha_d1 = lunes_actual + datetime.timedelta(days=idx1)
            fecha_d2 = lunes_actual + datetime.timedelta(days=idx2)
            if fecha_d1 > fecha_d2:
                fecha_d1, fecha_d2 = fecha_d2, fecha_d1
            inicio = datetime.datetime.combine(fecha_d1, datetime.time.min, tzinfo=ZONA_BOGOTA)
            fin = datetime.datetime.combine(fecha_d2 + datetime.timedelta(days=1), datetime.time.min, tzinfo=ZONA_BOGOTA)
            return IntervaloTemporal(
                etiqueta="ENTRE_DIAS",
                inicio=inicio,
                fin=fin,
                descripcion_legible=f"entre {d1_str} ({fecha_d1.strftime('%d/%m')}) y {d2_str} ({fecha_d2.strftime('%d/%m')})",
            )

    # 10. "hoy", "del día", "de hoy", "en la jornada"
    if re.search(r"\b(?:hoy|del?\s+d[ií]a|en\s+el\s+d[ií]a|esta\s+jornada|en\s+la\s+jornada|este\s+turno)\b", texto_norm):
        inicio = datetime.datetime.combine(hoy_date, datetime.time.min, tzinfo=ZONA_BOGOTA)
        fin = ahora
        return IntervaloTemporal(
            etiqueta="HOY",
            inicio=inicio,
            fin=fin,
            descripcion_legible=f"hoy ({hoy_date.strftime('%d/%m/%Y')})",
        )

    return None
