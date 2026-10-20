"""
Cálculo de días laborales y festivos en Colombia (Ley 51 de 1983 - Ley Emiliani).

Proporciona funciones deterministas, sin bucles abiertos ni dependencias externas,
para la programación de respaldos y operaciones en días hábiles.
"""

import argparse
import datetime
import sys
from typing import List, Set


def calcular_domingo_pascua(year: int) -> datetime.date:
    """
    Calcula el domingo de Pascua utilizando el algoritmo gregoriano
    de Meeus/Jones/Butcher (O(1), sin bucles).
    """
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mes = (h + l - 7 * m + 114) // 31
    dia = ((h + l - 7 * m + 114) % 31) + 1
    return datetime.date(year, mes, dia)


def siguiente_lunes(fecha: datetime.date) -> datetime.date:
    """
    Aplica el traslado al siguiente lunes según la Ley Emiliani (Ley 51 de 1983).
    Si la fecha ya cae en lunes, se mantiene.
    """
    dias_hasta_lunes = (7 - fecha.weekday()) % 7
    if dias_hasta_lunes == 0:
        return fecha
    return fecha + datetime.timedelta(days=dias_hasta_lunes)


def obtener_festivos_colombia(year: int) -> Set[datetime.date]:
    """
    Retorna el conjunto de los 18 días festivos oficiales en Colombia para un año determinado.
    """
    festivos: Set[datetime.date] = set()

    # 1. Festivos con fecha fija (no se trasladan)
    festivos.add(datetime.date(year, 1, 1))    # Año Nuevo
    festivos.add(datetime.date(year, 5, 1))    # Día del Trabajo
    festivos.add(datetime.date(year, 7, 20))   # Independencia de Colombia
    festivos.add(datetime.date(year, 8, 7))    # Batalla de Boyacá
    festivos.add(datetime.date(year, 12, 8))   # Inmaculada Concepción
    festivos.add(datetime.date(year, 12, 25))  # Navidad

    # 2. Festivos sujetos a la Ley Emiliani (se trasladan al siguiente lunes)
    festivos.add(siguiente_lunes(datetime.date(year, 1, 6)))    # Reyes Magos
    festivos.add(siguiente_lunes(datetime.date(year, 3, 19)))   # San José
    festivos.add(siguiente_lunes(datetime.date(year, 6, 29)))   # San Pedro y San Pablo
    festivos.add(siguiente_lunes(datetime.date(year, 8, 15)))   # Asunción de la Virgen
    festivos.add(siguiente_lunes(datetime.date(year, 10, 12)))  # Día de la Raza
    festivos.add(siguiente_lunes(datetime.date(year, 11, 1)))   # Todos los Santos
    festivos.add(siguiente_lunes(datetime.date(year, 11, 11)))  # Independencia de Cartagena

    # 3. Festivos basados en la Pascua
    pascua = calcular_domingo_pascua(year)
    festivos.add(pascua - datetime.timedelta(days=3))           # Jueves Santo
    festivos.add(pascua - datetime.timedelta(days=2))           # Viernes Santo
    festivos.add(siguiente_lunes(pascua + datetime.timedelta(days=39)))  # Ascensión del Señor (+43 d lunes)
    festivos.add(siguiente_lunes(pascua + datetime.timedelta(days=60)))  # Corpus Christi (+64 d lunes)
    festivos.add(siguiente_lunes(pascua + datetime.timedelta(days=68)))  # Sagrado Corazón (+71 d lunes)

    return festivos


def es_festivo_colombia(fecha: datetime.date) -> bool:
    """Determina si una fecha específica es día festivo en Colombia."""
    return fecha in obtener_festivos_colombia(fecha.year)


def es_dia_laboral(fecha: datetime.date) -> bool:
    """
    Determina si una fecha es día laboral en Colombia (Lunes a Viernes no festivos).
    Sábados (weekday=5) y Domingos (weekday=6) no son laborales.
    """
    if fecha.weekday() >= 5:
        return False
    return not es_festivo_colombia(fecha)


def es_horario_laboral(dt: datetime.datetime, hora_inicio: int = 8, hora_fin: int = 18) -> bool:
    """
    Determina si un timestamp se encuentra dentro de la jornada laboral colombiana.
    """
    if not es_dia_laboral(dt.date()):
        return False
    return hora_inicio <= dt.hour < hora_fin


def obtener_ultimos_dias_laborales(fecha_referencia: datetime.date, cantidad_dias: int) -> List[datetime.date]:
    """
    Retorna una lista ordenada con los últimos N días laborales previos (o coincidentes)
    a la fecha de referencia. Garantizado de finalizar sin bucle infinito (acotado a 3*N iteraciones).
    """
    dias_laborales: List[datetime.date] = []
    actual = fecha_referencia
    limite_seguridad = cantidad_dias * 4  # Evita cualquier posibilidad de bucle abierto
    iteraciones = 0

    while len(dias_laborales) < cantidad_dias and iteraciones < limite_seguridad:
        if es_dia_laboral(actual):
            dias_laborales.append(actual)
        actual -= datetime.timedelta(days=1)
        iteraciones += 1

    return dias_laborales


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Verificador de días laborales y festivos en Colombia.")
    parser.add_argument("--es-laboral", action="store_true", help="Verifica si hoy (o la fecha especificada) es laboral.")
    parser.add_argument("--fecha", type=str, default=None, help="Fecha en formato YYYY-MM-DD (por defecto hoy).")
    parser.add_argument("--listar-festivos", type=int, default=None, help="Lista todos los festivos de un año dado.")
    args = parser.parse_args()

    if args.listar_festivos:
        festivos = sorted(obtener_festivos_colombia(args.listar_festivos))
        for f in festivos:
            print(f.isoformat())
        sys.exit(0)

    eval_fecha = datetime.date.fromisoformat(args.fecha) if args.fecha else datetime.date.today()

    if args.es_laboral:
        laboral = es_dia_laboral(eval_fecha)
        print("LABORAL" if laboral else "NO_LABORAL")
        sys.exit(0 if laboral else 1)
