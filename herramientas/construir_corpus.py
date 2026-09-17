"""Genera el corpus de HACU en dos niveles desde los .docx ya extraidos.

Nivel 1 — indice: un documento corto con TODOS los proyectos a una linea. Es lo
que contesta "¿que proyectos tiene AudacIA?" sin arrastrar treinta fichas.
Nivel 2 — fichas: una seccion `##` autocontenida por proyecto, que es lo que
contesta "cuentame todo sobre Mary".

Nada se escribe a mano salvo los resumenes de una linea (breves.py), que son
compresion editorial de las descripciones del propio informe.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from breves import BREVES  # noqa: E402

if len(sys.argv) < 3:
    sys.exit(
        "Uso: python herramientas/construir_corpus.py fuente.json documents/ "
        "[corpus_anterior.md]\n"
        "  fuente.json        lo que produce extraer.py desde los .docx\n"
        "  documents/         carpeta de salida del corpus\n"
        "  corpus_anterior.md opcional: de ahi salen los detalles tecnicos de los\n"
        "                     proyectos didacticos, que NO estan en los .docx"
    )

FUENTE = Path(sys.argv[1])
SALIDA = Path(sys.argv[2])
# El detalle tecnico de los proyectos didacticos nunca estuvo en los informes en
# Word: se escribio a mano en el corpus anterior (`documents/audacia_unisimon.md`,
# hoy borrado del arbol pero recuperable con `git show <commit>:<ruta>`). Sin ese
# fichero el corpus se regenera igual, pero las fichas didacticas salen mas
# pobres que las que hay ahora en disco. Se avisa a gritos porque el fallo es
# silencioso: los ficheros aparecen, solo que con menos dentro.
VIEJO_AUDACIA = Path(sys.argv[3]) if len(sys.argv) > 3 else None
SALIDA.mkdir(parents=True, exist_ok=True)

datos = json.loads(FUENTE.read_text(encoding="utf-8"))
sec, prosa = datos["secciones"], datos["prosa"]
CAT = "Catálogo Exhaustivo de Proyectos en Producción (con Enlaces Web Oficiales)"
DID = "Matriz de Proyectos Didácticos y Experimentales (Fase de Prueba)"

ARCHIVO_AREA = {
    "Salud y Diagnóstico Médico": ("audacia_proyectos_salud.md", "salud y diagnóstico médico"),
    "Medio Ambiente, Agro e Industria": ("audacia_proyectos_ambiente.md", "medio ambiente, agro e industria"),
    "Cultura, Arte y Turismo": ("audacia_proyectos_cultura.md", "cultura, arte y turismo"),
    "Hardware, Mecatrónica y Robótica": ("audacia_proyectos_hardware.md", "hardware, mecatrónica y robótica"),
    "Educación y EdTech": ("audacia_proyectos_educacion.md", "educación y EdTech"),
    "Energía y Servicios Públicos": ("audacia_proyectos_energia.md", "energía y servicios públicos"),
    "Programas Estratégicos": ("audacia_proyectos_energia.md", "programas estratégicos"),
}
PATRON = re.compile(r"^(?P<nombre>[^(]+)\s*\(Ruta:\s*(?P<ruta>[^)]+)\):\s*(?P<desc>.+)$")


def _partir(linea: str) -> tuple[str, str, str, str]:
    m = PATRON.match(linea)
    if not m:
        nombre, _, desc = linea.partition(":")
        return nombre.strip(), "", "", desc.strip()
    ruta = m.group("ruta").strip().rstrip("/")
    return (m.group("nombre").strip(), ruta.rsplit("/", 1)[-1],
            f"https://{ruta}/", m.group("desc").strip())


# --- Recolectar --------------------------------------------------------------
por_area: dict[str, list[dict]] = {}
for area, lineas in sec[CAT].items():
    if area == "_":
        continue
    for linea in lineas:
        nombre, slug, url, desc = _partir(linea)
        por_area.setdefault(area, []).append(
            {"nombre": nombre, "slug": slug, "url": url, "desc": desc, "prosa": prosa.get(slug, [])})

didacticos = []
for linea in sec[DID]["_"]:
    nombre, _, desc = linea.partition(":")
    didacticos.append({"nombre": nombre.strip(), "desc": desc.strip()})

# Detalle tecnico de los didacticos, del corpus anterior escrito a mano.
detalle_didactico: dict[str, list[str]] = {}
if VIEJO_AUDACIA is None or not VIEJO_AUDACIA.exists():
    print("AVISO: sin corpus anterior. Las fichas de los proyectos didacticos "
          "saldran sin su detalle tecnico (ese detalle no esta en los .docx).",
          file=sys.stderr)
if VIEJO_AUDACIA is not None and VIEJO_AUDACIA.exists():
    actual = None
    for linea in VIEJO_AUDACIA.read_text(encoding="utf-8").splitlines():
        cabecera = re.match(r"^###\s+3\.\d+\.\s*(.+?)\s*$", linea)
        if cabecera:
            actual = cabecera.group(1).strip()
            detalle_didactico[actual] = []
        elif actual and linea.startswith("*"):
            detalle_didactico[actual].append(re.sub(r"\[cite:[^\]]*\]", "", linea).strip())

ALIAS_DIDACTICO = {
    "Proyecto Tanque - Dron Terrestre": "Proyecto Tanque - Dron Terrestre (Macroproyecto de Ecosistemas)",
    "Robots Programables Avanzados": "Robots Programables Avanzados",
    "Detección de Juntas de Rieles": "Detección de Juntas de Rieles",
    "Holosand": "Holosand",
    "Detección de Fatiga Visual": "Detección de Fatiga Visual",
    "Orion": "Orion",
}

total = sum(len(v) for v in por_area.values())


# --- Nivel 1: indice ---------------------------------------------------------
ind = [
    "# AudacIA: catálogo de todos los proyectos",
    "",
    "Lista completa para enumerarlos de un vistazo. Cada proyecto tiene además su",
    "propia ficha con el detalle: esta página sirve para decir qué hay, no para",
    "explicar cada uno a fondo.",
    "",
    "AudacIA es el Centro de Investigación, Desarrollo Tecnológico e Innovación en",
    "Inteligencia Artificial y Robótica de la Universidad Simón Bolívar (Barranquilla).",
    f"Tiene {total} proyectos en producción y {len(didacticos)} proyectos didácticos en fase de prueba,",
    f"{total + len(didacticos)} en total.",
    "",
    "## Proyectos en producción",
    "",
]
for area, proyectos in por_area.items():
    ind += [f"### {area}", ""]
    ind += [f"- **{p['nombre']}**: {BREVES[p['nombre']]}." for p in proyectos]
    ind.append("")
ind += ["## Proyectos didácticos y experimentales (fase de prueba)", "",
        "Son los prototipos que se exhiben y con los que se enseña.", ""]
ind += [f"- **{p['nombre']}**: {BREVES[p['nombre']]}." for p in didacticos]
ind.append("")
(SALIDA / "audacia_indice_proyectos.md").write_text("\n".join(ind), encoding="utf-8")


# --- Nivel 2: fichas por area ------------------------------------------------
fichas: dict[str, list[str]] = {}
for area, proyectos in por_area.items():
    archivo, titulo = ARCHIVO_AREA[area]
    fichas.setdefault(archivo, [f"# AudacIA — proyectos de {titulo}", ""])
    for p in proyectos:
        bloque = [f"## {p['nombre']}", "",
                  f"{p['nombre']} es un proyecto de AudacIA, del área de {titulo}.",
                  f"Qué es: {p['desc']}", ""]
        if p["url"]:
            bloque += [f"Ficha oficial: {p['url']}", ""]
        if p["prosa"]:
            bloque += ["Detalle:", ""] + list(p["prosa"]) + [""]
        fichas[archivo] += bloque

# Fichas de los didacticos
did = ["# AudacIA — proyectos didácticos en fase de prueba", "",
       "Los prototipos que se exhiben y con los que se enseña robótica e IA.", ""]
for p in didacticos:
    did += [f"## {p['nombre']}", "",
            f"{p['nombre']} es un proyecto didáctico de AudacIA, en fase de prueba.",
            f"Qué es: {p['desc']}", ""]
    extra = detalle_didactico.get(ALIAS_DIDACTICO.get(p["nombre"], p["nombre"]), [])
    if extra:
        did += ["Detalle:", ""] + extra + [""]
fichas["audacia_proyectos_didacticos.md"] = did

for archivo, lineas in fichas.items():
    (SALIDA / archivo).write_text("\n".join(lineas), encoding="utf-8")


# --- El centro ---------------------------------------------------------------
def bloque(titulo_seccion: str, encabezado: str) -> list[str]:
    cuerpo = sec.get(titulo_seccion, {}).get("_", [])
    return [f"## {encabezado}", ""] + [f"- {t}" for t in cuerpo] + [""] if cuerpo else []


centro = ["# AudacIA: el centro", "",
          "Centro de Investigación, Desarrollo Tecnológico e Innovación en Inteligencia",
          "Artificial y Robótica de la Universidad Simón Bolívar, en Barranquilla, Colombia.",
          ""]
# La identidad y la infraestructura se separan a proposito: juntas formaban un
# fragmento mixto donde "35.000 nucleos" quedaba sepultado bajo la direccion
# postal, y una pregunta por la capacidad de computo no lo recuperaba.
_identidad = sec["Identidad e Infraestructura Institucional"]["_"]
_marcas_infra = ("Infraestructura Física", "Capacidad de Cómputo")
centro += ["## Identidad y contacto", ""]
centro += [f"- {t}" for t in _identidad if not t.startswith(_marcas_infra)] + [""]
centro += ["## Infraestructura y capacidad de cómputo", ""]
centro += [f"- {t}" for t in _identidad if t.startswith(_marcas_infra)] + [""]
centro += bloque("Filosofía, Misión, Visión y Modelo del Sistema Nervioso", "Filosofía, misión y visión")
centro += bloque("Servicios y Metodología Operativa", "Servicios y metodología")
centro += bloque("Líderes, Investigadores Principales y Caras Visibles de AudacIA", "Personas del centro")
centro += bloque("Patentes Concedidas y en Trámite ante la SIC", "Patentes ante la SIC")
centro += bloque("Artículos y Publicaciones Científicas Indexadas", "Publicaciones científicas")
centro += bloque("Reconocimientos, Distinciones y Hitos Internacionales", "Reconocimientos y distinciones")
(SALIDA / "audacia_centro.md").write_text("\n".join(centro), encoding="utf-8")

for archivo in sorted(SALIDA.glob("*.md")):
    print(f"  {archivo.name:38} {archivo.stat().st_size:6} bytes")
