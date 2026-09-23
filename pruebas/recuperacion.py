"""Medicion de calidad de recuperacion, sin modelo de lenguaje.

Responde una pregunta que la bateria completa no puede aislar: cuando HACU dice
"no tengo informacion sobre X" y X SI esta documentado, el fallo es del RAG, no
del LLM. Aqui se mide exactamente eso: para cada consulta hay un marcador que
debe aparecer en los fragmentos recuperados, extraido del propio corpus.

Corre en segundos y no toca la GPU, asi que sirve para calibrar embeddings y
n_results antes de gastar una corrida de 60 turnos.

    python -m pruebas.recuperacion                 # compara ambos embeddings
    python -m pruebas.recuperacion --n 2 4 6       # barre varios n_results
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

from hacu.config import PROJECT_ROOT, RagConfig
from hacu.context import recuperar_de_audacia
from hacu.rag import LocalRAGEngine
from hacu.routing import Intencion, normalizar


@dataclass(frozen=True)
class ConsultaMedida:
    """Una consulta con la verdad documentada que debe recuperarse."""

    texto: str
    intencion: Intencion
    marcadores: tuple[str, ...]
    todos: bool = False  # True: deben aparecer todos; False: basta con uno

    def aciertos(self, contexto: str | None) -> tuple[int, int]:
        """(marcadores encontrados, marcadores exigidos)."""
        if not contexto:
            return 0, len(self.marcadores)
        plano = normalizar(contexto)
        hallados = sum(1 for m in self.marcadores if normalizar(m) in plano)
        return hallados, len(self.marcadores)

    def acierta(self, contexto: str | None) -> bool:
        hallados, total = self.aciertos(contexto)
        return hallados == total if self.todos else hallados > 0


AUD = Intencion.AUDACIA
UNI = Intencion.UNIVERSIDAD

CONSULTAS: tuple[ConsultaMedida, ...] = (
    # --- Proyectos de AudacIA: la ficha concreta ---------------------------
    ConsultaMedida("Cuéntame todo sobre Mary", AUD, ("Goldberg",)),
    ConsultaMedida("¿Cómo funciona Patrii?", AUD, ("glaucoma",)),
    ConsultaMedida("¿Qué hace VART?", AUD, ("prematuros",)),
    ConsultaMedida("¿Qué es Neupeek?", AUD, ("neumonía",)),
    ConsultaMedida("Háblame del ROV submarino", AUD, ("turbidez",)),
    ConsultaMedida("¿Qué es Guajira Travel?", AUD, ("Guajira",)),
    ConsultaMedida("¿Para qué sirve Dilce?", AUD, ("eléctric",)),
    ConsultaMedida("¿Qué es Vallenato Master?", AUD, ("rítmica",)),
    ConsultaMedida("¿Qué hace el proyecto Tanque?", AUD, ("Soil Sensor",)),
    ConsultaMedida("Cuéntame sobre el proyecto Orion", AUD, ("proximidad",)),
    ConsultaMedida("¿Qué es Holosand?", AUD, ("Kinect",)),
    ConsultaMedida("¿Tienen algo para agricultura?", AUD, ("cultivo",)),
    ConsultaMedida("¿Trabajan con trenes o vías férreas?", AUD, ("rieles",)),
    # Varias respuestas son correctas: la arena interactiva, el kit pedagógico y
    # los robots didácticos. Exigir solo una era un defecto de la prueba.
    ConsultaMedida("¿Hay algo interactivo para niños?", AUD, ("Kinect", "Calvin", "Robots Programables")),
    ConsultaMedida("¿Qué hacen para prevenir accidentes de tránsito?", AUD, ("fatiga",)),
    ConsultaMedida("¿Tienen algo de salud mental?", AUD, ("ansiedad",)),
    # --- El centro: lo institucional, que compite con 37 fichas ------------
    ConsultaMedida("¿Qué patentes tiene el centro?", AUD, ("turbidez",)),
    ConsultaMedida("¿Qué publicaciones científicas tienen?", AUD, ("spectroscopy",)),
    ConsultaMedida("¿Quién dirige AudacIA?", AUD, ("Villarreal",)),
    ConsultaMedida("¿Dónde queda AudacIA?", AUD, ("Eureka",)),
    ConsultaMedida("¿Qué reconocimientos tiene AudacIA?", AUD, ("OEA", "MinCiencias"), todos=True),
    ConsultaMedida("¿Cuántos núcleos de procesamiento tiene?", AUD, ("35.000",)),
    ConsultaMedida("¿Qué servicios ofrece el centro?", AUD, ("prototipado",)),
    ConsultaMedida("¿Cuáles son los objetivos de AudacIA?", AUD, ("apropiación social",)),
    # --- Centros hermanos: lo que NO es AudacIA ----------------------------
    # Tres piezas del corpus MENCIONAN MacondoLab de pasada y una lo EXPLICA.
    # Las cuatro empatan a una palabra distintiva, y el cupo del rescate es de
    # dos: sin desempate por titulo ganaban las menciones y estas tres lineas
    # fallaban. Miden ese desempate, no el embedding.
    ConsultaMedida("¿Qué es MacondoLab?", AUD, ("incubación", "2014"), todos=True),
    ConsultaMedida("¿MacondoLab es un proyecto de ustedes?", AUD, ("al mismo nivel",)),
    ConsultaMedida("¿Y el CICV qué es?", AUD, ("Ciencias de la Vida", "2003"), todos=True),
    # --- Universidad -------------------------------------------------------
    ConsultaMedida("¿Quién es el rector actualmente?", UNI, ("Consuegra Bolívar",)),
    ConsultaMedida("¿En qué año se fundó?", UNI, ("1972",)),
    ConsultaMedida("¿Cuál es la historia de la Universidad Simón Bolívar?", UNI, ("1972",)),
    ConsultaMedida("¿Qué facultades tiene la universidad?", UNI, ("Facultad de Ingenierías",)),
    ConsultaMedida("¿Qué carreras de pregrado puedo estudiar aquí?", UNI, ("Ing. de Sistemas",)),
    ConsultaMedida("¿Dónde queda la universidad?", UNI, ("El Prado",)),
    ConsultaMedida("¿Puedo estudiar medicina aquí?", UNI, ("Medicina",)),
    ConsultaMedida("¿Qué es MacondoLab?", UNI, ("MacondoLab",)),
    ConsultaMedida("¿La universidad está acreditada?", UNI, ("Acreditación",)),
    ConsultaMedida("¿Hay doctorado en inteligencia artificial?", UNI, ("Doctorado en Inteligencia Artificial",)),
    ConsultaMedida("¿Tienen sede en Cúcuta?", UNI, ("Cúcuta",)),
    ConsultaMedida("¿Qué es el distrito Eureka?", UNI, ("Eureka",)),
)

# El catalogo NO se mide aqui: no se recupera por similitud sino que se carga
# entero (`cargar_indice`), asi que su prueba es "¿estan los 32?" y vive en
# `comprobar_indice`.
PROYECTOS_DEL_INDICE: tuple[str, ...] = (
    "Mary", "Patrii", "VART", "Neupeek", "Fractura Schatzker", "Sahli", "SkinnIA", "Camille",
    "Bucólicos", "Detección de Explosivos", "Detección de Petróleo in situ", "Biotecnia",
    "Health-Growers", "Victa", "Huellas del Maestro", "Pipemaster", "Vallenato Master",
    "Guajira Travel", "ROV Submarino", "Adinel", "Calvin", "Mario", "Mia", "Dilce", "Solenium",
    "Fellowship fAIr LAC", "Proyecto Tanque", "Robots Programables Avanzados",
    "Detección de Juntas de Rieles", "Holosand", "Detección de Fatiga Visual", "Orion",
)


def comprobar_indice(motor: LocalRAGEngine) -> tuple[int, list[str]]:
    """El indice tiene que traer los 32 proyectos, no una muestra."""
    indice = motor.cargar_indice(Intencion.AUDACIA) or ""
    plano = normalizar(indice)
    faltan = [p for p in PROYECTOS_DEL_INDICE if normalizar(p) not in plano]
    return len(PROYECTOS_DEL_INDICE) - len(faltan), faltan


def medir(config: RagConfig, n_results: int, logger: logging.Logger) -> tuple[int, list[str]]:
    """Indexa con esa configuracion y devuelve (aciertos, lineas de detalle)."""
    motor = LocalRAGEngine(config, logger)
    motor.sincronizar_documentos()

    presentes, faltan = comprobar_indice(motor)
    aciertos = 0
    detalle: list[str] = [
        f"  {'OK  ' if not faltan else 'MISS'} [{presentes}/{len(PROYECTOS_DEL_INDICE)}] "
        f"índice-catálogo completo" + (f" — faltan {faltan}" if faltan else "")
    ]
    for consulta in CONSULTAS:
        # El mismo camino que usa HACU en escena: para AudacIA, la politica de
        # profundidad; para la universidad, la busqueda directa.
        if consulta.intencion is AUD:
            contexto = recuperar_de_audacia(motor, config, consulta.texto, consulta.texto, n_results)
        else:
            contexto = motor.buscar(consulta.intencion, consulta.texto, n_results=n_results)
        ok = consulta.acierta(contexto)
        aciertos += ok
        hallados, total = consulta.aciertos(contexto)
        marca = "OK  " if ok else "MISS"
        detalle.append(f"  {marca} [{hallados}/{total}] {consulta.texto}")
    return aciertos, detalle


def main() -> int:
    parser = argparse.ArgumentParser(description="Calidad de recuperación del RAG")
    parser.add_argument("--n", type=int, nargs="+", default=[2, 4, 6], help="valores de n_results")
    parser.add_argument("--detalle", action="store_true", help="muestra consulta por consulta")
    args = parser.parse_args()

    logging.basicConfig(level=logging.ERROR)
    logger = logging.getLogger("hacu")

    base = RagConfig(doc_folder=PROJECT_ROOT / "documents")
    escenarios = {
        "por defecto (all-MiniLM-L6-v2, inglés)": False,
        "multilingüe (paraphrase-multilingual-MiniLM-L12-v2)": True,
    }

    print(f"Corpus: {base.doc_folder}")
    print(f"Consultas con verdad documentada: {len(CONSULTAS)}\n")

    resultados: dict[str, dict[int, int]] = {}
    for nombre, multilingue in escenarios.items():
        resultados[nombre] = {}
        for n in args.n:
            tmp = Path(tempfile.mkdtemp(prefix="hacu-rag-"))
            try:
                cfg = replace(base, db_path=tmp, multilingual_embeddings=multilingue, default_results=n)
                aciertos, detalle = medir(cfg, n, logger)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
            resultados[nombre][n] = aciertos
            print(f"{nombre} · n_results={n}: {aciertos}/{len(CONSULTAS)}")
            if args.detalle:
                print("\n".join(detalle))
        print()

    print("=" * 66)
    print(f"{'configuración':<52}" + "".join(f"{f'n={n}':>7}" for n in args.n))
    print("=" * 66)
    for nombre, por_n in resultados.items():
        fila = "".join(f"{por_n[n]:>7}" for n in args.n)
        print(f"{nombre:<52}{fila}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
