"""Borra lo que el proyecto genera solo y puede volver a generar.

Existe porque la carpeta de trabajo acumula peso invisible entre exhibiciones:
bytecode, un log que crece sin techo, informes de pruebas que miden una version
del corpus que ya no existe, y la base de memoria con datos de visitantes.

Por defecto NO borra nada: lista lo que borraria y cuanto ocupa. Con `--aplicar`
lo ejecuta.

    python -m herramientas.limpiar                 # ver que sobra
    python -m herramientas.limpiar --aplicar       # borrarlo
    python -m herramientas.limpiar --aplicar --memoria   # y la memoria de visitantes
    python -m herramientas.limpiar --aplicar --indice    # y el indice vectorial

`--memoria` y `--indice` van aparte a proposito: la memoria son datos de
personas reales y el indice cuesta un par de minutos de reconstruccion. Lo demas
es basura sin discusion.

Nunca toca `models/`, `documents/` ni codigo: lo que esta aqui, o se regenera en
el siguiente arranque, o se regenera con una orden que se indica al lado.
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import shutil
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

# Carpetas en las que nunca hace falta bajar a mirar. Un `venv/` con
# llama-cpp-python, torch (via sentence-transformers), chromadb y PySide6
# instalados tiene decenas de miles de ficheros, y un `__pycache__` ahi
# dentro no lo genero este proyecto: lo genero pip al instalar el paquete.
# `RAIZ.glob("**/__pycache__")` no tiene forma de podar el descenso, asi que
# recorria site-packages entero para no encontrar nada que valiera la pena
# borrar: medido, eso es lo que hacia lento este comando en Windows.
_CARPETAS_EXCLUIDAS = frozenset({"venv", ".venv", "env", ".git", "node_modules"})


def _recorrer_podando(patron: str) -> Iterator[Path]:
    """Como `RAIZ.glob('**/' + patron)`, sin bajar a `_CARPETAS_EXCLUIDAS`."""
    for carpeta, subcarpetas, ficheros in os.walk(RAIZ):
        subcarpetas[:] = [c for c in subcarpetas if c not in _CARPETAS_EXCLUIDAS]
        if patron in subcarpetas:
            yield Path(carpeta) / patron
            subcarpetas.remove(patron)  # se borra entera; no hace falta bajar mas
            continue
        for fichero in ficheros:
            if fnmatch.fnmatch(fichero, patron):
                yield Path(carpeta) / fichero


@dataclass(frozen=True)
class Objetivo:
    """Algo prescindible del arbol y por que se puede borrar."""

    patron: str
    motivo: str
    opcional: str = ""   # nombre de la bandera que hace falta para incluirlo

    def encontrar(self) -> Iterator[Path]:
        if self.patron.startswith("**/"):
            yield from sorted(_recorrer_podando(self.patron.removeprefix("**/")))
        else:
            yield from sorted(RAIZ.glob(self.patron))


OBJETIVOS: tuple[Objetivo, ...] = (
    Objetivo("**/__pycache__", "bytecode; Python lo rehace al importar"),
    Objetivo("**/*.pyc", "bytecode suelto"),
    Objetivo("logs/*.log", "log de ejecucion; se abre uno nuevo en cada arranque"),
    Objetivo("*.wav", "grabacion de diagnostico de `python -m hacu.voz --guardar`"),
    Objetivo("pruebas/informes/*", "informes de pruebas; miden un corpus anterior"),
    Objetivo(".mypy_cache", "cache de tooling"),
    Objetivo(".ruff_cache", "cache de tooling"),
    Objetivo("Claude outputs", "entregas de la app de escritorio, ya copiadas al arbol"),
    Objetivo("hacu_memory.db*", "memoria de visitantes; datos personales", opcional="memoria"),
    Objetivo("chroma_db", "indice vectorial; se reconstruye desde documents/", opcional="indice"),
)


def _peso(ruta: Path) -> int:
    if ruta.is_file():
        return ruta.stat().st_size
    return sum(f.stat().st_size for f in ruta.rglob("*") if f.is_file())


def _humano(octetos: int) -> str:
    for unidad in ("B", "KB", "MB", "GB"):
        if octetos < 1024 or unidad == "GB":
            return f"{octetos:.0f} {unidad}" if unidad == "B" else f"{octetos:.1f} {unidad}"
        octetos /= 1024
    return f"{octetos:.1f} GB"


def _borrar(ruta: Path) -> None:
    shutil.rmtree(ruta) if ruta.is_dir() else ruta.unlink()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--aplicar", action="store_true",
                        help="borra de verdad (sin esto solo se lista)")
    parser.add_argument("--memoria", action="store_true",
                        help="incluye hacu_memory.db (datos de visitantes)")
    parser.add_argument("--indice", action="store_true",
                        help="incluye chroma_db (se reconstruye al arrancar)")
    args = parser.parse_args(argv[1:])
    incluidos = {n for n in ("memoria", "indice") if getattr(args, n)}

    # Se recogen todos los candidatos antes de tocar nada, y se descartan los que
    # cuelgan de otro candidato: `**/*.pyc` cae dentro de `**/__pycache__`, y sin
    # esto el informe contaria dos veces los mismos bytes.
    candidatos: list[tuple[Path, str]] = []
    for objetivo in OBJETIVOS:
        if objetivo.opcional and objetivo.opcional not in incluidos:
            continue
        for ruta in objetivo.encontrar():
            if ruta.exists():
                candidatos.append((ruta, objetivo.motivo))

    carpetas = {r for r, _ in candidatos if r.is_dir()}
    sueltos = [(r, m) for r, m in candidatos
               if not any(c in r.parents for c in carpetas)]

    total = 0
    borrados = 0
    for ruta, motivo in sorted(sueltos):
        octetos = _peso(ruta)
        total += octetos
        print(f"  {_humano(octetos):>9}  {ruta.relative_to(RAIZ)}  — {motivo}")
        if args.aplicar:
            _borrar(ruta)
            borrados += 1

    if total == 0:
        print("Nada que limpiar.")
        return 0
    if args.aplicar:
        print(f"\nBorrados {borrados} elementos, {_humano(total)} liberados.")
    else:
        print(f"\n{_humano(total)} en total. Nada se ha borrado: repite con --aplicar.")
        omitidos = [o.opcional for o in OBJETIVOS if o.opcional and o.opcional not in incluidos]
        if omitidos:
            print("Fuera de la lista por defecto: " + ", ".join(f"--{n}" for n in omitidos))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
