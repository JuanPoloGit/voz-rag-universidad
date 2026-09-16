"""Inventario y diagnostico del audio.

Antes de una exhibicion hay que saber por que microfono entra la voz y por que
altavoz sale, con numeros, no con suposiciones: en una portatil con webcam, base
de conexiones y auriculares llega a haber cinco entradas y el sistema no siempre
elige la que uno cree.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import VozConfig
from .microfono import AudioNoDisponible, _sounddevice


@dataclass(frozen=True)
class Dispositivo:
    """Una entrada o salida de audio del sistema."""

    indice: int
    nombre: str
    entradas: int
    salidas: int
    frecuencia: int
    por_defecto_entrada: bool = False
    por_defecto_salida: bool = False

    @property
    def es_entrada(self) -> bool:
        return self.entradas > 0

    @property
    def es_salida(self) -> bool:
        return self.salidas > 0

    def etiqueta(self) -> str:
        marcas = "".join(("🎤" if self.por_defecto_entrada else "",
                          "🔊" if self.por_defecto_salida else ""))
        tipo = "/".join(t for t, hay in (("in", self.es_entrada), ("out", self.es_salida)) if hay)
        return f"[{self.indice:>2}] {self.nombre[:52]:<52} {tipo:<7} {self.frecuencia:>6} Hz {marcas}"


def listar_dispositivos() -> list[Dispositivo]:
    """Todos los dispositivos de audio que ve el sistema."""
    sd = _sounddevice()
    try:
        entrada_def, salida_def = sd.default.device
    except Exception:
        entrada_def = salida_def = None

    dispositivos: list[Dispositivo] = []
    for indice, bruto in enumerate(sd.query_devices()):
        dispositivos.append(Dispositivo(
            indice=indice,
            nombre=str(bruto.get("name", "?")).strip(),
            entradas=int(bruto.get("max_input_channels", 0)),
            salidas=int(bruto.get("max_output_channels", 0)),
            frecuencia=int(bruto.get("default_samplerate", 0) or 0),
            por_defecto_entrada=indice == entrada_def,
            por_defecto_salida=indice == salida_def,
        ))
    return dispositivos


def comprobar(config: VozConfig) -> list[str]:
    """Verifica que la configuracion de audio es utilizable. Devuelve los problemas."""
    problemas: list[str] = []
    try:
        dispositivos = listar_dispositivos()
    except AudioNoDisponible as error:
        return [str(error)]

    if not any(d.es_entrada for d in dispositivos):
        problemas.append("No hay ningun dispositivo de entrada: no se puede oir al visitante.")
    if not any(d.es_salida for d in dispositivos):
        problemas.append("No hay ningun dispositivo de salida: HACU no podra hablar.")

    for etiqueta, indice, condicion in (
        ("entrada", config.dispositivo_entrada, lambda d: d.es_entrada),
        ("salida", config.dispositivo_salida, lambda d: d.es_salida),
    ):
        if indice is None:
            continue
        elegido = next((d for d in dispositivos if d.indice == indice), None)
        if elegido is None:
            problemas.append(f"El dispositivo de {etiqueta} {indice} no existe.")
        elif not condicion(elegido):
            problemas.append(f"El dispositivo {indice} ({elegido.nombre}) no sirve como {etiqueta}.")
    return problemas
