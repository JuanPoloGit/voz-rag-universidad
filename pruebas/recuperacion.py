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
    # --- AudacIA -----------------------------------------------------------
    ConsultaMedida("¿Qué es AudacIA exactamente?", AUD, ("Centro de Investigación",)),
    ConsultaMedida("¿Qué hace el tanque?", AUD, ("Soil Sensor",)),
    ConsultaMedida("¿Cuánto costó construir el tanque?", AUD, ("Soil Sensor",)),
    ConsultaMedida("Cuéntame sobre el proyecto Orion", AUD, ("Cinturón inteligente",)),
    ConsultaMedida("¿Qué es Holosand?", AUD, ("Kinect",)),
    ConsultaMedida("¿Tienen algo para agricultura?", AUD, ("Soil Sensor",)),
    ConsultaMedida("¿Trabajan con trenes o vías férreas?", AUD, ("Rieles",)),
    ConsultaMedida("¿Hay algo interactivo para niños?", AUD, ("Kinect",)),
    ConsultaMedida("¿Qué hacen para prevenir accidentes de tránsito?", AUD, ("Fatiga",)),
    ConsultaMedida(
        "¿Cuáles son todos los proyectos de AudacIA?", AUD,
        ("Tanque", "Programables", "Rieles", "Holosand", "Fatiga", "Orion"), todos=True,
    ),
    # --- Universidad -------------------------------------------------------
    ConsultaMedida("¿Quién es el rector actualmente?", UNI, ("Consuegra Bolívar",)),
    ConsultaMedida("¿En qué año se fundó?", UNI, ("1972",)),
    ConsultaMedida("¿Cuál es la historia de la Universidad Simón Bolívar?", UNI, ("1972",)),
    ConsultaMedida("¿Qué facultades tiene la universidad?", UNI, ("Facultad de Ingenierías",)),
    ConsultaMedida("¿Qué carreras de pregrado puedo estudiar aquí?", UNI, ("Ingeniería de Sistemas",)),
    ConsultaMedida("¿Dónde queda la universidad?", UNI, ("Carrera 59",)),
    ConsultaMedida("¿Cuántos doctorados ofrecen?", UNI, ("doctorados",)),
    ConsultaMedida("¿Puedo estudiar medicina aquí?", UNI, ("Medicina",)),
    ConsultaMedida("¿Qué es MacondoLab?", UNI, ("MacondoLab",)),
    ConsultaMedida("¿La universidad está acreditada?", UNI, ("Acreditación Institucional",)),
)


def medir(config: RagConfig, n_results: int, logger: logging.Logger) -> tuple[int, list[str]]:
    """Indexa con esa configuracion y devuelve (aciertos, lineas de detalle)."""
    motor = LocalRAGEngine(config, logger)
    motor.sincronizar_documentos()

    aciertos = 0
    detalle: list[str] = []
    for consulta in CONSULTAS:
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
