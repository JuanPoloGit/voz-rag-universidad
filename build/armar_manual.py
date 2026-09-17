"""Ensambla la documentacion de docs/ en un solo Markdown apto para pandoc.

Resuelve lo que un PDF de una sola pieza necesita y un arbol de archivos no:
identificadores estables para cada encabezado, enlaces entre capitulos que
apuntan a esos identificadores en vez de a rutas de archivo, y sustitucion de los
simbolos que ninguna fuente tipografica del sistema dibuja.
"""

from __future__ import annotations

import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

# Orden del manual. El README abre como presentacion; el resto son los capitulos.
CAPITULOS: tuple[tuple[str, str], ...] = (
    ("README.md", "Presentación"),
    ("docs/instalacion.md", None),
    ("docs/github.md", None),
    ("docs/docker.md", None),
    ("docs/arquitectura.md", None),
    ("docs/configuracion.md", None),
    ("docs/corpus.md", None),
    ("docs/voz.md", None),
    ("docs/interfaz.md", None),
    ("docs/pruebas.md", None),
    ("docs/operacion.md", None),
    ("docs/datos.md", None),
)

# Ninguna fuente instalada dibuja estos glifos; en el PDF saldrian como cajas
# negras. Se sustituyen solo aqui: los .md del repositorio no se tocan.
SIMBOLOS: tuple[tuple[str, str], ...] = (
    ("### ⚠️ Si tu GPU", "### Atención si tu GPU"),
    ("️", ""),          # selector de variacion, invisible pero rompe la fuente
    ("✅", "[OK]"),
    ("❌", "[X]"),
    ("⚠", "[!]"),
    ("🎤", "[mic]"),
    ("🔧", "[*]"),
)

# El indice de documentacion del README lo sustituye el propio indice del PDF.
SECCIONES_FUERA = {"README.md": ("Documentación",)}


def anclar(texto: str) -> str:
    """Identificador al estilo de GitHub, que es como estan escritos los enlaces."""
    plano = texto.lower()
    plano = re.sub(r"[`*_\[\]()]", "", plano)
    plano = re.sub(r"[^\w\s-]", "", plano, flags=re.UNICODE)
    return plano.strip().replace(" ", "-")


def sin_tildes(texto: str) -> str:
    descompuesto = unicodedata.normalize("NFD", texto)
    return "".join(c for c in descompuesto if unicodedata.category(c) != "Mn")


@dataclass
class Capitulo:
    ruta: Path
    clave: str            # "docs/voz.md", tal como aparece en los enlaces
    stem: str
    titulo: str
    lineas: list[str] = field(default_factory=list)
    anclas: dict[str, str] = field(default_factory=dict)   # ancla GitHub -> id del manual
    rotulos: dict[str, str] = field(default_factory=dict)  # id del manual -> texto del encabezado


def _fuera_de_codigo(lineas: list[str]):
    """Itera (indice, linea, dentro_de_bloque_de_codigo)."""
    dentro = False
    for i, linea in enumerate(lineas):
        if linea.lstrip().startswith("```"):
            dentro = not dentro
            yield i, linea, True
            continue
        yield i, linea, dentro


def preparar(ruta: Path, clave: str, titulo_forzado: str | None) -> Capitulo:
    bruto = ruta.read_text(encoding="utf-8")
    for viejo, nuevo in SIMBOLOS:
        bruto = bruto.replace(viejo, nuevo)
    lineas = bruto.splitlines()

    stem = ruta.stem if ruta.stem != "README" else "presentacion"
    cap = Capitulo(ruta=ruta, clave=clave, stem=stem, titulo="")

    fuera = SECCIONES_FUERA.get(ruta.name, ())
    saltando = False
    contador = 0
    salida: list[str] = []

    for _, linea, en_codigo in _fuera_de_codigo(lineas):
        cabecera = None if en_codigo else re.match(r"^(#{1,6})\s+(.*?)\s*$", linea)
        if cabecera is None:
            if not saltando:
                salida.append(linea)
            continue

        nivel, texto = len(cabecera.group(1)), cabecera.group(2)

        if nivel == 1:
            cap.titulo = titulo_forzado or texto
            cap.anclas[""] = f"cap-{stem}"
            cap.anclas[anclar(texto)] = f"cap-{stem}"
            cap.rotulos[f"cap-{stem}"] = cap.titulo
            salida.append(f"# {cap.titulo} {{#cap-{stem}}}")
            saltando = False
            continue

        # Una seccion excluida se salta entera, hasta el siguiente encabezado
        # de su mismo nivel o superior.
        if saltando and nivel > nivel_excluido:
            continue
        saltando = False
        if texto in fuera:
            saltando, nivel_excluido = True, nivel
            continue

        contador += 1
        ident = f"s-{stem}-{contador}"
        cap.anclas[anclar(texto)] = ident
        cap.rotulos[ident] = texto
        salida.append(f"{'#' * nivel} {texto} {{#{ident}}}")

    cap.lineas = _pulir_para_pdf(salida)
    return cap


_REGLA = re.compile(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$")
_MAXIMO_RESERVA = 38     # lineas; mas que eso no cabe en una pagina de todos modos


def _pulir_para_pdf(lineas: list[str]) -> list[str]:
    """Quita reglas horizontales y protege los bloques de codigo largos."""
    salida: list[str] = []
    i = 0
    while i < len(lineas):
        linea = lineas[i]
        if linea.lstrip().startswith("```"):
            cierre = i + 1
            while cierre < len(lineas) and not lineas[cierre].lstrip().startswith("```"):
                cierre += 1
            alto = cierre - i - 1
            if alto >= 12:
                reserva = min(alto + 3, _MAXIMO_RESERVA)
                # Tres entradas y no una sola cadena con saltos: el resto del
                # ensamblador cuenta vallas ``` linea a linea, y un bloque entero
                # metido en una sola entrada le descuadra el conteo.
                salida.append("```{=latex}")
                salida.append(f"\\needspace{{{reserva}\\baselineskip}}")
                salida.append("```")
                salida.append("")
            salida.extend(lineas[i:cierre + 1])
            i = cierre + 1
            continue
        if _REGLA.match(linea):
            i += 1
            continue
        salida.append(linea)
        i += 1
    return salida


_ES_RUTA = re.compile(r"^[.\w/-]+\.md(#[\w%-]*)?$")


def reescribir_enlaces(cap: Capitulo, indice: dict[str, Capitulo], problemas: list[str]) -> None:
    """Enlaces a otros .md -> anclas internas del manual."""

    def destino(m: re.Match[str]) -> str:
        etiqueta, objetivo = m.group(1), m.group(2)
        if objetivo.startswith(("http://", "https://", "mailto:", "#")):
            return m.group(0)
        ruta, _, ancla = objetivo.partition("#")
        if not ruta:
            return m.group(0)
        clave = (cap.ruta.parent / ruta).resolve().relative_to(RAIZ).as_posix()
        otro = indice.get(clave)
        if otro is None:
            problemas.append(f"{cap.clave}: enlace a un archivo que no es capitulo -> {objetivo}")
            return etiqueta                     # se queda el texto, sin enlace
        ident = otro.anclas.get(ancla)
        if ident is None:
            problemas.append(f"{cap.clave}: ancla desconocida -> {objetivo}")
            ident = otro.anclas[""]
        # «Ver docs/instalacion.md» no significa nada dentro de un PDF de una
        # sola pieza: la etiqueta pasa a ser el titulo de lo que se referencia.
        if _ES_RUTA.match(etiqueta):
            etiqueta = f"*{otro.rotulos.get(ident, otro.titulo)}*"
        return f"[{etiqueta}](#{ident})"

    dentro = False
    for i, linea in enumerate(cap.lineas):
        if linea.lstrip().startswith("```"):
            dentro = not dentro
            continue
        if not dentro:
            cap.lineas[i] = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", destino, linea)


def main() -> int:
    capitulos = []
    for clave, titulo in CAPITULOS:
        ruta = RAIZ / clave
        if not ruta.exists():
            print(f"falta {clave}", file=sys.stderr)
            return 1
        capitulos.append(preparar(ruta, clave, titulo))

    indice = {c.clave: c for c in capitulos}
    problemas: list[str] = []
    for cap in capitulos:
        reescribir_enlaces(cap, indice, problemas)

    partes = ["\n".join(c.lineas).strip() for c in capitulos]
    salida = RAIZ / "build" / "manual.md"
    salida.write_text("\n\n\\newpage\n\n".join(partes) + "\n", encoding="utf-8")

    print(f"{len(capitulos)} capitulos -> {salida.relative_to(RAIZ)}")
    for c in capitulos:
        print(f"  {c.titulo}  ({len(c.anclas) - 1} secciones)")
    if problemas:
        print("\nAVISOS:")
        for p in problemas:
            print("  " + p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
