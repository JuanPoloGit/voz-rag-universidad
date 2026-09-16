"""Troceo del stream de tokens en frases pronunciables.

La sintesis no puede esperar a que termine la generacion: serian cuatro segundos
de silencio con el visitante mirando. El segmentador va acumulando los tokens que
emite el modelo y suelta cada frase en cuanto esta cerrada, de modo que HACU
pronuncia la primera mientras genera la segunda.

Es logica pura y sin dependencias: se prueba sin microfono, sin altavoz y sin GPU.
"""

from __future__ import annotations

import re

_TERMINADORES = ".!?…"
# Abreviaturas que llevan punto y NO cierran frase. "Carrera 59 No. 59-65" se
# partia en dos justo en medio de la direccion de la universidad.
_ABREVIATURAS: frozenset[str] = frozenset(
    "no núm num sr sra srta dr dra ing lic mg phd av avda etc ee uu pag pags vol"
    " ej p.ej aprox art depto depto.".split()
)
_FINAL_PALABRA = re.compile(r"([\wáéíóúüñÁÉÍÓÚÜÑ.]+)$")


def _cierra_frase(texto: str, i: int) -> bool:
    """True si el terminador en la posicion i cierra una frase de verdad."""
    caracter = texto[i]
    if caracter not in _TERMINADORES:
        return False

    # 3.14 o 59.65: el punto va entre digitos, no cierra nada.
    if caracter == "." and i > 0 and texto[i - 1].isdigit():
        siguiente = texto[i + 1] if i + 1 < len(texto) else ""
        if siguiente.isdigit():
            return False

    if caracter == ".":
        anterior = _FINAL_PALABRA.search(texto[:i])
        if anterior and anterior.group(1).rstrip(".").lower() in _ABREVIATURAS:
            return False

    # Los puntos suspensivos se tratan como un solo terminador.
    if caracter == "." and texto[i + 1: i + 2] == ".":
        return False
    return True


class SegmentadorDeFrases:
    """Acumula tokens y devuelve frases completas listas para sintetizar."""

    def __init__(self, minimo: int = 12, maximo: int = 240) -> None:
        self._buffer = ""
        self._minimo = minimo
        self._maximo = maximo

    def alimentar(self, fragmento: str) -> list[str]:
        """Anade un fragmento del stream y devuelve las frases ya cerradas."""
        if not fragmento:
            return []
        self._buffer += fragmento
        frases: list[str] = []
        while (frase := self._extraer()) is not None:
            frases.append(frase)
        return frases

    def cerrar(self) -> str | None:
        """Lo que quede al terminar la generacion, aunque no cierre en punto."""
        resto, self._buffer = self._buffer.strip(), ""
        return resto or None

    # ---------------------------------------------------------------- internos

    def _extraer(self) -> str | None:
        corte = self._punto_de_corte()
        if corte is None:
            return None
        frase = self._buffer[:corte].strip()
        self._buffer = self._buffer[corte:].lstrip()
        return frase or None

    def _punto_de_corte(self) -> int | None:
        """Indice donde termina la primera frase completa del buffer, si la hay."""
        for i, caracter in enumerate(self._buffer):
            if not _cierra_frase(self._buffer, i):
                continue
            fin = i + 1
            # Un terminador solo cuenta si ya viene mas texto detras: si es el
            # ultimo caracter del buffer, puede que el modelo siga escribiendo.
            if fin >= len(self._buffer):
                return None
            if fin < self._minimo:
                continue
            return fin

        # Frase interminable: se corta en la coma mas cercana al limite para no
        # dejar al visitante esperando una pausa que no llega.
        if len(self._buffer) >= self._maximo:
            coma = self._buffer.rfind(",", self._minimo, self._maximo)
            if coma > 0:
                return coma + 1
            espacio = self._buffer.rfind(" ", self._minimo, self._maximo)
            if espacio > 0:
                return espacio
        return None
